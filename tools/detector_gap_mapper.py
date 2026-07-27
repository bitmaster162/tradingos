#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BITEVO = ROOT / "configs" / "BitEvo_composite_config.json"
DEFAULT_SMARTMONEY = ROOT / "smartmoney" / "SmartMoney_Alerts_Config.json"


RUNTIME_FILES = [
    "tools/pipeline_runner.py",
    "tools/max_backtest.py",
    "tools/max_v15_state_filters.py",
    "tools/max_v16_event_first_miner.py",
    "tools/max_v17_short_continuation_hardening.py",
    "tools/bitevo_contract_checker.py",
    "tools/bitevo_registry_validator.py",
    "tools/overlay_signal_evaluator.py",
]


DETECTOR_MAP = {
    "rsi_ao_div": {
        "smartmoney_alert": "diver_confirmed",
        "required_data": ["ohlcv.close", "ohlcv.high", "ohlcv.low", "ohlcv.volume", "oi.funding", "oi.open_interest"],
        "runtime_status": "partial",
        "implemented": ["RSI14 value", "AO(5,34) value", "basic trend/regime filters", "OI/funding context"],
        "missing": ["pivot-based divergence classification", "regular/hidden bull/bear labels", "mBOS/BOS/CHOCH detector", "volume surge sigma detector", "alert payload emitter"],
        "existing_consumers": ["tools/pipeline_runner.py"],
        "first_smoke_test": "Feed a crafted OHLCV CSV with known pivot divergence and assert a detector emits diver_confirmed=false/true deterministically.",
        "priority": 2,
        "reason": "Useful, but divergence needs careful anti-lookahead pivots before it can be trusted.",
    },
    "liquidity_sweep_eq": {
        "smartmoney_alert": "sweep_return",
        "required_data": ["ohlcv.high", "ohlcv.low", "ohlcv.close", "ohlcv.volume", "optional liquidation clusters", "optional delta"],
        "runtime_status": "partial",
        "implemented": ["20-bar bullish/bearish sweep flags", "sweep reversal context in MAX Core Lite", "alert-only market state support"],
        "missing": ["equal high/low clustering", "sweep displacement ticks", "return-inside max bars", "FVG validation", "delta imbalance", "liquidation cluster same-as-sweep"],
        "existing_consumers": ["tools/pipeline_runner.py", "tools/max_v15_state_filters.py", "tools/max_v16_event_first_miner.py"],
        "first_smoke_test": "Craft OHLCV with equal highs, wick sweep and close back inside; assert sweep_return detector fires without future bars.",
        "priority": 1,
        "reason": "Best first detector: clear data contract, easy deterministic fixture, directly tied to current MAX sweep logic.",
    },
    "smt_btc_eth": {
        "smartmoney_alert": "smt_ethbtc_anomaly",
        "required_data": ["BTCUSDT OHLCV", "ETHUSDT OHLCV", "aligned timestamps"],
        "runtime_status": "partial",
        "implemented": ["basic primary vs pairB return divergence"],
        "missing": ["HH/LL structure disagreement", "correlation window", "sigma-normalized divergence", "max lag bars", "pair trade optional output"],
        "existing_consumers": ["tools/pipeline_runner.py"],
        "first_smoke_test": "Feed aligned BTC/ETH fixture where BTC makes HH and ETH fails; assert SMT anomaly context is detected.",
        "priority": 3,
        "reason": "Valuable overlay, but needs aligned multi-asset data and structure logic.",
    },
    "funding_hot": {
        "smartmoney_alert": "funding_hot",
        "required_data": ["oi.funding or derivatives funding feed"],
        "runtime_status": "mostly_covered_as_filter",
        "implemented": ["positive/negative funding crowding warnings", "risk reduction thresholds in configs"],
        "missing": ["standalone SmartMoney alert event", "annualized funding normalization", "cooldown/coalescing", "alert payload emitter"],
        "existing_consumers": ["tools/pipeline_runner.py", "configs/BitEvo_composite_config.json"],
        "first_smoke_test": "Feed OI CSV with funding above threshold and assert funding_hot context is emitted as non-trading alert.",
        "priority": 1,
        "reason": "Smallest useful standalone detector; easy to prove and useful as risk gate.",
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def file_has(path: str, needle: str) -> bool:
    target = ROOT / path
    if not target.exists():
        return False
    return needle.lower() in target.read_text(encoding="utf-8", errors="replace").lower()


def runtime_evidence() -> dict[str, Any]:
    probes = {
        "rsi": ("tools/pipeline_runner.py", "_rsi"),
        "ao": ("tools/pipeline_runner.py", "_ao"),
        "sweep_flags": ("tools/pipeline_runner.py", "_sweep_flags"),
        "oi_delta": ("tools/pipeline_runner.py", "oi_delta_pct"),
        "funding": ("tools/pipeline_runner.py", "funding"),
        "smt_basic": ("tools/pipeline_runner.py", "divergence_pct"),
        "alert_only_observability": ("tools/max_v19_alert_observability.py", "forward_tracker"),
        "forward_evidence": ("tools/max_v20_forward_evidence.py", "classification"),
        "bitevo_contract": ("tools/bitevo_contract_checker.py", "validate_alert_payload"),
        "registry_validation": ("tools/bitevo_registry_validator.py", "validate_bit_evo_registry"),
    }
    return {
        key: {
            "file": path,
            "exists": (ROOT / path).exists(),
            "needle_found": file_has(path, needle),
        }
        for key, (path, needle) in probes.items()
    }


def build_report(bit_evo_path: Path, smartmoney_path: Path) -> dict[str, Any]:
    bit_evo = read_json(bit_evo_path)
    smartmoney = read_json(smartmoney_path)
    setup_ids = [item.get("setup_id") for item in bit_evo.get("setups", []) if isinstance(item, dict)]
    alert_ids = list((smartmoney.get("alerts") or {}).keys()) if isinstance(smartmoney, dict) else []

    items: list[dict[str, Any]] = []
    for setup_id in setup_ids:
        if not isinstance(setup_id, str):
            continue
        item = dict(DETECTOR_MAP.get(setup_id, {}))
        if not item:
            item = {
                "smartmoney_alert": None,
                "required_data": [],
                "runtime_status": "unknown",
                "implemented": [],
                "missing": ["detector mapping not defined"],
                "existing_consumers": [],
                "first_smoke_test": "Define detector contract first.",
                "priority": 99,
                "reason": "No mapping exists yet.",
            }
        item["setup_id"] = setup_id
        item["source"] = "BitEvo setup"
        items.append(item)

    mapped_alerts = {item.get("smartmoney_alert") for item in items}
    for alert_id in alert_ids:
        if alert_id in mapped_alerts:
            continue
        item = dict(DETECTOR_MAP.get(alert_id, {}))
        if not item:
            item = {
                "setup_id": None,
                "smartmoney_alert": alert_id,
                "required_data": [],
                "runtime_status": "manual_or_global_filter",
                "implemented": [],
                "missing": ["not linked to a BitEvo setup"],
                "existing_consumers": [],
                "first_smoke_test": "Define alert-specific detector or keep as manual risk note.",
                "priority": 50,
                "reason": "Standalone SmartMoney alert.",
            }
        item["source"] = "SmartMoney alert"
        items.append(item)

    status_counts: dict[str, int] = {}
    for item in items:
        status = str(item["runtime_status"])
        status_counts[status] = status_counts.get(status, 0) + 1

    priority_order = sorted(items, key=lambda item: int(item.get("priority", 99)))
    return {
        "generated_at": now_iso(),
        "inputs": {"bit_evo": rel(bit_evo_path), "smartmoney": rel(smartmoney_path)},
        "runtime_evidence": runtime_evidence(),
        "counts": {
            "bit_evo_setups": len(setup_ids),
            "smartmoney_alerts": len(alert_ids),
            "mapped_items": len(items),
            "status_counts": status_counts,
        },
        "items": items,
        "recommended_build_order": [
            {
                "rank": index + 1,
                "id": item.get("setup_id") or item.get("smartmoney_alert"),
                "runtime_status": item.get("runtime_status"),
                "first_smoke_test": item.get("first_smoke_test"),
                "reason": item.get("reason"),
            }
            for index, item in enumerate(priority_order[:5])
        ],
        "policy": {
            "gap_map_only": True,
            "no_detector_claim_without_smoke": True,
            "no_trade_permission": True,
            "no_alert_emission": True,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Detector Gap Map",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## What This Means",
        "",
        "This map separates three different things: configured setup, detected signal and tradable signal.",
        "",
        "- A configured setup means the idea exists in JSON.",
        "- A detector means code can identify it from data.",
        "- A tradable signal needs detector proof, backtest/paper evidence, risk gate and execution boundary.",
        "",
        "Current result: BitEvo and SmartMoney registries are structurally valid, but most setup detectors are partial, not complete live logic.",
        "",
        "## Counts",
        "",
        f"- BitEvo setups: `{report['counts']['bit_evo_setups']}`",
        f"- SmartMoney alerts: `{report['counts']['smartmoney_alerts']}`",
        f"- Mapped items: `{report['counts']['mapped_items']}`",
    ]
    for status, count in sorted(report["counts"]["status_counts"].items()):
        lines.append(f"- `{status}`: `{count}`")

    lines.extend(["", "## Recommended Build Order", ""])
    for item in report["recommended_build_order"]:
        lines.append(f"{item['rank']}. `{item['id']}` - `{item['runtime_status']}`")
        lines.append(f"   Smoke: {item['first_smoke_test']}")
        lines.append(f"   Why: {item['reason']}")

    lines.extend(["", "## Runtime Evidence", ""])
    for key, item in report["runtime_evidence"].items():
        lines.append(f"- `{key}`: file=`{item['file']}` exists=`{item['exists']}` needle_found=`{item['needle_found']}`")

    lines.extend(["", "## Detector Gaps", ""])
    for item in report["items"]:
        item_id = item.get("setup_id") or item.get("smartmoney_alert")
        lines.extend(
            [
                f"### `{item_id}`",
                "",
                f"- source: `{item['source']}`",
                f"- runtime_status: `{item['runtime_status']}`",
                f"- linked SmartMoney alert: `{item.get('smartmoney_alert') or '-'}`",
                f"- existing consumers: `{', '.join(item.get('existing_consumers', [])) or '-'}`",
                f"- required data: `{', '.join(item.get('required_data', [])) or '-'}`",
                f"- first smoke test: {item.get('first_smoke_test')}",
                "",
                "Implemented:",
            ]
        )
        for value in item.get("implemented", []):
            lines.append(f"- {value}")
        lines.append("")
        lines.append("Missing:")
        for value in item.get("missing", []):
            lines.append(f"- {value}")
        lines.append("")

    lines.extend(
        [
            "## Boundary",
            "",
            "- This document does not promote any setup to live trading.",
            "- The next code step should be one small detector with a crafted fixture and a passing smoke test.",
            "- Best first detector: `liquidity_sweep_eq` or standalone `funding_hot`, because they are easiest to prove without lookahead.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Map BitEvo/SmartMoney setup detectors to current runtime gaps.")
    parser.add_argument("--bitevo", default=str(DEFAULT_BITEVO))
    parser.add_argument("--smartmoney", default=str(DEFAULT_SMARTMONEY))
    parser.add_argument("--out-prefix", default="docs/DETECTOR_GAP_MAP_2026-06-02")
    args = parser.parse_args()

    bit_evo_path = Path(args.bitevo)
    smartmoney_path = Path(args.smartmoney)
    if not bit_evo_path.is_absolute():
        bit_evo_path = ROOT / bit_evo_path
    if not smartmoney_path.is_absolute():
        smartmoney_path = ROOT / smartmoney_path

    report = build_report(bit_evo_path, smartmoney_path)
    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "mapped_items": report["counts"]["mapped_items"],
                "status_counts": report["counts"]["status_counts"],
                "top_next": report["recommended_build_order"][0] if report["recommended_build_order"] else None,
                "out_json": rel(out_prefix.with_suffix(".json")),
                "out_md": rel(out_prefix.with_suffix(".md")),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

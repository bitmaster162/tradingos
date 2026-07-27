#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.derivatives_event_edge_miner import (  # noqa: E402
    EventConfig,
    join_rows,
    read_csv,
    regime_matches,
    safe_float,
)
from tools.derivatives_event_forward_observer import (  # noqa: E402
    data_paths,
    forward_feature,
    selected_config,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def round_float(value: Any, digits: int = 6) -> float | None:
    parsed = safe_float(value)
    return round(float(parsed), digits) if parsed is not None else None


def progress_for(actual: float | None, operator: str, required: float | None) -> float | None:
    if actual is None or required is None:
        return None
    if operator == ">=":
        if required == 0:
            return 1.0 if actual >= required else 0.0
        return round(max(0.0, min(1.5, actual / required)), 6) if required > 0 else (1.0 if actual >= required else 0.0)
    if operator == "<=":
        if actual <= required:
            return 1.0
        if actual == 0:
            return 0.0
        if required >= 0 and actual > 0:
            return round(max(0.0, min(1.0, required / actual)), 6)
        return 0.0
    return None


def condition(name: str, actual: Any, operator: str, required: Any) -> dict[str, Any]:
    actual_f = safe_float(actual)
    required_f = safe_float(required)
    if actual_f is None or required_f is None:
        passed = False
    elif operator == ">=":
        passed = actual_f >= required_f
    elif operator == "<=":
        passed = actual_f <= required_f
    else:
        raise ValueError(f"unsupported operator: {operator}")
    return {
        "name": name,
        "passed": passed,
        "actual": round_float(actual_f),
        "operator": operator,
        "required": round_float(required_f),
        "progress": progress_for(actual_f, operator, required_f),
    }


def family_conditions(config: EventConfig, feature: dict[str, float]) -> list[dict[str, Any]]:
    price = feature.get("price_move_atr")
    oi = feature.get("oi_delta_pct")
    funding = feature.get("funding")
    volume_z = feature.get("volume_z")
    close_loc = feature.get("close_location")
    if config.family == "oi_build_fade":
        if config.side == "SHORT":
            return [
                condition("price_move_atr", price, ">=", config.price_atr),
                condition("oi_delta_pct", oi, ">=", config.oi_pct),
                condition("funding", funding, ">=", config.funding_abs),
            ]
        return [
            condition("price_move_atr", price, "<=", -config.price_atr),
            condition("oi_delta_pct", oi, ">=", config.oi_pct),
            condition("funding", funding, "<=", -config.funding_abs),
        ]
    if config.family == "oi_build_continuation":
        if config.side == "LONG":
            return [
                condition("price_move_atr", price, ">=", config.price_atr),
                condition("oi_delta_pct", oi, ">=", config.oi_pct),
                condition("funding", funding, "<=", config.funding_abs),
                condition("close_location", close_loc, ">=", config.close_location),
            ]
        return [
            condition("price_move_atr", price, "<=", -config.price_atr),
            condition("oi_delta_pct", oi, ">=", config.oi_pct),
            condition("funding", funding, ">=", -config.funding_abs),
            condition("close_location", close_loc, "<=", 1.0 - config.close_location),
        ]
    if config.family == "deleveraging_reversal":
        base = [
            condition("volume_z", volume_z, ">=", config.volume_z),
            condition("oi_delta_pct", oi, "<=", -config.oi_pct),
        ]
        if config.side == "LONG":
            return base + [
                condition("price_move_atr", price, "<=", -config.price_atr),
                condition("close_location", close_loc, ">=", config.close_location),
            ]
        return base + [
            condition("price_move_atr", price, ">=", config.price_atr),
            condition("close_location", close_loc, "<=", 1.0 - config.close_location),
        ]
    if config.family == "squeeze_exhaustion_fade":
        base = [
            condition("volume_z", volume_z, ">=", config.volume_z),
            condition("oi_delta_pct", oi, "<=", -config.oi_pct),
        ]
        if config.side == "SHORT":
            return base + [
                condition("price_move_atr", price, ">=", config.price_atr),
                condition("close_location", close_loc, "<=", 1.0 - config.close_location),
            ]
        return base + [
            condition("price_move_atr", price, "<=", -config.price_atr),
            condition("close_location", close_loc, ">=", config.close_location),
        ]
    if config.family == "funding_extreme_fade":
        if config.side == "SHORT":
            return [
                condition("funding", funding, ">=", config.funding_abs),
                condition("price_move_atr", price, ">=", max(0.25, config.price_atr * 0.5)),
            ]
        return [
            condition("funding", funding, "<=", -config.funding_abs),
            condition("price_move_atr", price, "<=", -max(0.25, config.price_atr * 0.5)),
        ]
    raise ValueError(f"unsupported family: {config.family}")


def summarize_conditions(conditions: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(conditions)
    passed = sum(1 for row in conditions if row.get("passed") is True)
    blockers = [str(row.get("name")) for row in conditions if row.get("passed") is not True]
    min_progress = min((float(row["progress"]) for row in conditions if isinstance(row.get("progress"), (int, float))), default=None)
    return {
        "passed": passed,
        "total": total,
        "blockers": blockers,
        "all_passed": passed == total,
        "near": total > 0 and passed >= max(1, total - 1),
        "min_progress": round(min_progress, 6) if min_progress is not None else None,
    }


def render_markdown(report: dict[str, Any]) -> str:
    latest = report.get("latest") if isinstance(report.get("latest"), dict) else {}
    summary = latest.get("summary") if isinstance(latest.get("summary"), dict) else {}
    selected = report.get("selected_config") if isinstance(report.get("selected_config"), dict) else {}
    lines = [
        "# Derivatives Event Pending Watch",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Explains latest-bar readiness for the derivatives-event candidate.",
        "- Does not write observer signals.",
        "- Does not send Telegram.",
        "- Does not create paper-entry intents or orders.",
        "",
        "## Candidate",
        "",
        f"- Strategy: `{selected.get('strategy_id')}`.",
        f"- Family / side / TF: `{selected.get('family')}` / `{selected.get('side')}` / `{selected.get('interval')}`.",
        f"- Regime: `{selected.get('regime_filter')}`.",
        "",
        "## Latest",
        "",
        f"- Status: `{latest.get('status')}`.",
        f"- Bar: `{latest.get('bar_ts')}` close `{latest.get('close')}`.",
        f"- Passed: `{summary.get('passed')}` / `{summary.get('total')}`.",
        f"- Blockers: `{summary.get('blockers')}`.",
        f"- Regime passed: `{latest.get('regime_passed')}`.",
        "",
        "## Conditions",
        "",
        "| condition | pass | actual | op | required | progress |",
        "|---|---:|---:|---|---:|---:|",
    ]
    for row in latest.get("conditions", []):
        lines.append(
            f"| {row.get('name')} | `{row.get('passed')}` | `{row.get('actual')}` | `{row.get('operator')}` | `{row.get('required')}` | `{row.get('progress')}` |"
        )
    lines.extend(["", "## Decision", "", f"- `{report.get('decision')}`.", f"- Next: `{report.get('next_action')}`.", ""])
    return "\n".join(lines)


def blocked_report(reason: str, args: argparse.Namespace, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    report = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "watch_id": "derivatives_event_pending_watch",
        "decision": reason,
        "latest": {"status": reason},
        "runtime_boundary": {
            "pending_watch_only": True,
            "paper_allowed": False,
            "live_allowed": False,
            "orders_allowed": False,
            "telegram_allowed": False,
            "can_trade": False,
        },
        "next_action": "fix pending-watch input before using this diagnostic",
        "can_trade": False,
    }
    if extra:
        report.update(extra)
    return report


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    miner_path = resolve_path(args.miner_report)
    miner = read_json(miner_path)
    if not miner:
        return blocked_report("blocked_missing_miner_report", args, {"miner_report": rel_path(miner_path)})
    config = selected_config(miner)
    if config is None:
        return blocked_report("blocked_no_selected_derivatives_candidate", args, {"miner_report": rel_path(miner_path)})
    paths = data_paths(miner, config)
    if paths is None:
        return blocked_report("blocked_missing_candidate_data_paths", args, {"miner_report": rel_path(miner_path), "selected_config": config.__dict__})
    klines_path, derivatives_path = paths
    rows = join_rows(read_csv(klines_path), read_csv(derivatives_path))
    latest_index, feature = forward_feature(rows, config)
    if latest_index is None or feature is None:
        return blocked_report(
            "blocked_no_latest_forward_feature",
            args,
            {"rows": len(rows), "klines_path": rel_path(klines_path), "derivatives_path": rel_path(derivatives_path), "selected_config": config.__dict__},
        )
    latest_row = rows[latest_index]
    regime_passed = bool(regime_matches(config, feature))
    conditions = [{"name": "regime_filter", "passed": regime_passed, "actual": config.regime_filter, "operator": "==", "required": "pass", "progress": 1.0 if regime_passed else 0.0}]
    conditions.extend(family_conditions(config, feature))
    summary = summarize_conditions(conditions)
    if summary["all_passed"]:
        status = "pending_watch_signal_conditions_met"
    elif summary["near"]:
        status = "pending_watch_near_signal"
    else:
        status = "pending_watch_blocked"
    latest = {
        "status": status,
        "bar_ts": str(latest_row.get("time") or ""),
        "bar_index": latest_index,
        "close": round_float(latest_row.get("close"), 8),
        "regime_passed": regime_passed,
        "feature_snapshot": {
            "price_move_atr": round_float(feature.get("price_move_atr")),
            "oi_delta_pct": round_float(feature.get("oi_delta_pct")),
            "funding": round_float(feature.get("funding"), 8),
            "volume_z": round_float(feature.get("volume_z")),
            "close_location": round_float(feature.get("close_location")),
            "atr": round_float(feature.get("atr"), 8),
            "ema50": round_float(feature.get("ema50"), 8),
            "ema200": round_float(feature.get("ema200"), 8),
            "ema50_slope_20": round_float(feature.get("ema50_slope_20"), 8),
            "ema200_slope_20": round_float(feature.get("ema200_slope_20"), 8),
        },
        "conditions": conditions,
        "summary": summary,
        "can_trade": False,
    }
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "watch_id": "derivatives_event_pending_watch",
        "miner_report": rel_path(miner_path),
        "klines_path": rel_path(klines_path),
        "derivatives_path": rel_path(derivatives_path),
        "selected_config": config.__dict__,
        "latest": latest,
        "decision": status,
        "next_action": "use blockers to explain no-signal state; observer remains the only signal writer",
        "runtime_boundary": {
            "pending_watch_only": True,
            "paper_allowed": False,
            "live_allowed": False,
            "orders_allowed": False,
            "telegram_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Pending-watch diagnostics for derivatives-event candidate")
    parser.add_argument("--miner-report", default="docs/DERIVATIVES_EVENT_EDGE_MINER_REGIME_2026-06-26.json")
    parser.add_argument("--out-prefix", default="docs/DERIVATIVES_EVENT_PENDING_WATCH_2026-06-27")
    args = parser.parse_args()
    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    latest = report.get("latest") if isinstance(report.get("latest"), dict) else {}
    summary = latest.get("summary") if isinstance(latest.get("summary"), dict) else {}
    print(
        json.dumps(
            {
                "decision": report.get("decision"),
                "passed": summary.get("passed"),
                "total": summary.get("total"),
                "blockers": summary.get("blockers"),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    decision = str(report.get("decision", ""))
    if decision == "blocked_no_selected_derivatives_candidate":
        return 0
    return 0 if not decision.startswith("blocked_") else 2


if __name__ == "__main__":
    raise SystemExit(main())

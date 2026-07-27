#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.range_family_validator import generate_signals, load_interval_payload  # noqa: E402
from tools.range_refined_filter_shadow_ablation import apply_variant, cost_expectancy, make_variants  # noqa: E402
from tools.range_refined_forward_observer import build_config, selected_candidate  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def read_json(path: Path, default: Any = None) -> Any:
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def variant_evidence(ablation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = ablation.get("results") if isinstance(ablation.get("results"), list) else []
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        variant_id = str(row.get("variant_id") or "")
        if not variant_id:
            continue
        out[variant_id] = {
            "verdict": row.get("verdict"),
            "signals": row.get("signals"),
            "full_trades": row.get("full", {}).get("summary", {}).get("trades") if isinstance(row.get("full"), dict) else None,
            "full_expectancy_r": row.get("full", {}).get("summary", {}).get("expectancy_r") if isinstance(row.get("full"), dict) else None,
            "holdout_trades": row.get("holdout", {}).get("summary", {}).get("trades") if isinstance(row.get("holdout"), dict) else None,
            "holdout_expectancy_r": row.get("holdout", {}).get("summary", {}).get("expectancy_r") if isinstance(row.get("holdout"), dict) else None,
            "segment_positive_ratio": row.get("segment_positive_ratio"),
            "worst_segment_expectancy_r": row.get("worst_segment_expectancy_r"),
            "cost10_expectancy_r": cost_expectancy(row, 10.0),
        }
    return out


def signal_payload(config: Any, signal: dict[str, Any], latest_bar: Any, latest_index: int) -> dict[str, Any]:
    return {
        "bar_index": latest_index,
        "bar_ts": str(latest_bar.ts),
        "close": round(float(latest_bar.close), 8),
        "atr": signal.get("atr"),
        "feature_snapshot": signal.get("feature_snapshot") if isinstance(signal.get("feature_snapshot"), dict) else {},
        "reason": signal.get("reason") or config.trigger,
    }


def event(event_type: str, **payload: Any) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "ts_emitted": now_iso(),
        "can_trade": False,
        "sends_orders": False,
        "uses_private_credentials": False,
        "creates_paper_entry_intents": False,
        **payload,
    }


def render_markdown(report: dict[str, Any]) -> str:
    latest = report.get("latest_result") if isinstance(report.get("latest_result"), dict) else {}
    selected = report.get("selected_candidate") if isinstance(report.get("selected_candidate"), dict) else {}
    lines = [
        "# Range Refined Filter Shadow Forward Observer",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Observer-only comparison for RANGE ablation variants.",
        "- Does not change the active selected RANGE observer.",
        "- Does not create paper-entry intents.",
        "- Does not send orders or grant live permission.",
        "",
        "## Selected Candidate",
        "",
        f"- Base: `{selected.get('base_strategy_id')}`.",
        f"- Active filter: `{selected.get('filter_mode')}` (`{'+'.join(selected.get('filters') or [])}`).",
        f"- TF / side / RR: `{selected.get('interval')}` / `{selected.get('side')}` / `{selected.get('rr')}`.",
        "",
        "## Latest Observation",
        "",
        f"- Latest bar: `{latest.get('latest_closed_bar_ts')}` close `{latest.get('latest_closed_close')}`.",
        f"- Raw base signals: `{latest.get('raw_base_signals_on_latest_bar')}`.",
        f"- Variant signals: `{latest.get('variant_signals_on_latest_bar')}`.",
        f"- Signalling variants: `{latest.get('signalling_variants')}`.",
        f"- Journal: `{report.get('journal_path')}`.",
        "",
        "## Variant States",
        "",
        "| Variant | Status | Historical Verdict | Full Exp | Cost +10 Exp |",
        "|---|---|---|---:|---:|",
    ]
    for row in latest.get("variants", []):
        evidence = row.get("historical_evidence") if isinstance(row.get("historical_evidence"), dict) else {}
        lines.append(
            f"| `{row.get('variant_id')}` | `{row.get('status')}` | `{evidence.get('verdict')}` | "
            f"`{evidence.get('full_expectancy_r')}` | `{evidence.get('cost10_expectancy_r')}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- A shadow signal is not a trade signal.",
            "- Shadow variants must earn promotion through their own observer scoreboard and gate before any paper-design review.",
            "- Variants with weak cost-stress remain blocked even if they emit more forward signals.",
            "",
        ]
    )
    return "\n".join(lines)


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    refiner_path = resolve_path(args.refiner_report)
    source_path = resolve_path(args.source_range_report)
    ablation_path = resolve_path(args.ablation_report)
    cache_dir = resolve_path(args.cache_dir)
    journal_path = resolve_path(args.journal_path)
    state_path = resolve_path(args.state_path)

    refiner_report = read_json(refiner_path)
    source_report = read_json(source_path)
    ablation_report = read_json(ablation_path)
    selected = selected_candidate(refiner_report)
    base_config = build_config(selected, source_report)
    bars, features, rsi14 = load_interval_payload(cache_dir, base_config.interval, args.oi_lag, args.spot_perp_lookback)
    if not bars:
        raise ValueError(f"no_bars_loaded:{rel_path(cache_dir)}:{base_config.interval}")

    latest_index = len(bars) - 1
    latest_bar = bars[latest_index]
    raw_signals = generate_signals(base_config, bars, features, rsi14, latest_index, latest_index + 1)
    raw_signal = raw_signals[0] if raw_signals else None
    evidence_by_variant = variant_evidence(ablation_report)
    variant_rows: list[dict[str, Any]] = []
    journal_rows: list[dict[str, Any]] = []
    signalling_variants: list[str] = []

    for variant in make_variants():
        variant_config = replace(base_config, strategy_id=f"{base_config.strategy_id}__shadow_{variant['variant_id']}")
        filtered = apply_variant(raw_signals, variant_config, variant["funcs"])
        if not raw_signals:
            status = "shadow_no_base_signal"
            latest_signal = None
        elif filtered:
            status = "shadow_variant_signal_observed"
            latest_signal = signal_payload(variant_config, filtered[0], latest_bar, latest_index)
            signalling_variants.append(str(variant["variant_id"]))
        else:
            status = "shadow_variant_filtered_out"
            latest_signal = signal_payload(variant_config, raw_signal, latest_bar, latest_index) if isinstance(raw_signal, dict) else None
        row = {
            "variant_id": variant["variant_id"],
            "description": variant["description"],
            "filters": variant["filters"],
            "status": status,
            "strategy_id": variant_config.strategy_id,
            "latest_signal": latest_signal,
            "historical_evidence": evidence_by_variant.get(str(variant["variant_id"]), {}),
        }
        variant_rows.append(row)
        journal_rows.append(
            event(
                "range_filter_shadow_variant_state",
                observer_id="range_refined_filter_shadow_forward_observer",
                variant_id=variant["variant_id"],
                status=status,
                strategy_id=variant_config.strategy_id,
                base_strategy_id=selected.get("base_strategy_id"),
                active_strategy_id=selected.get("strategy_id"),
                symbol=args.symbol.upper(),
                interval=base_config.interval,
                side=base_config.side,
                bar_ts=str(latest_bar.ts),
                bar_index=latest_index,
                close=round(float(latest_bar.close), 8),
                raw_base_signals_on_latest_bar=len(raw_signals),
                latest_signal=latest_signal,
                historical_evidence=evidence_by_variant.get(str(variant["variant_id"]), {}),
            )
        )

    append_jsonl(journal_path, journal_rows)
    state = read_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    state.update(
        {
            "last_run_at": now_iso(),
            "last_closed_bar_ts": str(latest_bar.ts),
            "raw_base_signals_on_latest_bar": len(raw_signals),
            "variant_signals_on_latest_bar": len(signalling_variants),
            "signalling_variants": signalling_variants,
        }
    )
    write_json(state_path, state)
    return {
        "latest_closed_bar_ts": str(latest_bar.ts),
        "latest_closed_close": round(float(latest_bar.close), 8),
        "raw_base_signals_on_latest_bar": len(raw_signals),
        "variant_signals_on_latest_bar": len(signalling_variants),
        "signalling_variants": signalling_variants,
        "variants": variant_rows,
        "journal_events_written": len(journal_rows),
        "journal_path": rel_path(journal_path),
        "state_path": rel_path(state_path),
        "can_trade": False,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Observer-only forward comparison for RANGE filter ablation variants")
    parser.add_argument("--refiner-report", default="docs/RANGE_WATCHLIST_REFINER_2026-06-16.json")
    parser.add_argument("--source-range-report", default="docs/RANGE_FAMILY_VALIDATOR_2026-06-16.json")
    parser.add_argument("--ablation-report", default="docs/RANGE_REFINED_FILTER_SHADOW_ABLATION_2026-06-17.json")
    parser.add_argument("--cache-dir", default="_dl/forward_paper_feed/cache")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--oi-lag", type=int, default=12)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--journal-path", default="logs/forward_paper_feed/range_refined_filter_shadow_forward_observer.jsonl")
    parser.add_argument("--state-path", default="logs/forward_paper_feed/range_refined_filter_shadow_forward_observer_state.json")
    parser.add_argument("--out-prefix", default="docs/RANGE_REFINED_FILTER_SHADOW_FORWARD_OBSERVER_2026-06-17")
    args = parser.parse_args()

    refiner_report = read_json(resolve_path(args.refiner_report))
    selected = selected_candidate(refiner_report)
    latest = run_once(args)
    out_prefix = resolve_path(args.out_prefix)
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "range_refined_filter_shadow_forward_observer_public_cache_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
            "changes_active_strategy": False,
        },
        "selected_candidate": selected,
        "latest_result": latest,
        "journal_path": latest["journal_path"],
        "state_path": latest["state_path"],
        "decision": "shadow_forward_observer_no_trade_permission",
        "next_action": "accumulate shadow variant observations; do not promote without independent scoreboard and gate",
        "can_trade": False,
    }
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "raw_base_signals_on_latest_bar": latest["raw_base_signals_on_latest_bar"],
                "variant_signals_on_latest_bar": latest["variant_signals_on_latest_bar"],
                "signalling_variants": latest["signalling_variants"],
                "json": rel_path(out_prefix.with_suffix(".json")),
                "md": rel_path(out_prefix.with_suffix(".md")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

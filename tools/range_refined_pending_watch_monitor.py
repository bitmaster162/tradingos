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

from tools.range_family_validator import load_interval_payload, previous_high_low, rsi_ok, trend_atr  # noqa: E402
from tools.range_refined_forward_observer import build_config, selected_candidate  # noqa: E402
from tools.range_watchlist_refiner import FILTER_FUNCS  # noqa: E402


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


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def trigger_state(config: Any, bar: Any, high: float | None, low: float | None, atr: float | None) -> dict[str, Any]:
    if high is None or low is None or high <= low:
        return {"trigger_ok": False, "reason": "range_not_ready"}
    width = high - low
    lower_edge = low + width * config.edge_pct
    upper_edge = high - width * config.edge_pct
    close = float(bar.close)
    high_price = float(bar.high)
    low_price = float(bar.low)

    if config.trigger == "near_high":
        level = upper_edge
        distance = max(0.0, level - close)
        progress_den = max(level - low, 1e-12)
        progress_pct = ((close - low) / progress_den) * 100.0
        trigger_ok = close >= level
        direction = "price_below_upper_edge" if not trigger_ok else "price_at_or_above_upper_edge"
    elif config.trigger == "near_low":
        level = lower_edge
        distance = max(0.0, close - level)
        progress_den = max(high - level, 1e-12)
        progress_pct = ((high - close) / progress_den) * 100.0
        trigger_ok = close <= level
        direction = "price_above_lower_edge" if not trigger_ok else "price_at_or_below_lower_edge"
    elif config.trigger == "sweep_up_reclaim":
        level = high
        distance = max(0.0, level - high_price)
        progress_pct = 100.0 if high_price > high and close < high else (high_price - low) / max(width, 1e-12) * 100.0
        trigger_ok = high_price > high and close < high
        direction = "needs_sweep_and_reclaim" if not trigger_ok else "sweep_reclaimed"
    elif config.trigger == "sweep_down_reclaim":
        level = low
        distance = max(0.0, low_price - level)
        progress_pct = 100.0 if low_price < low and close > low else (high - low_price) / max(width, 1e-12) * 100.0
        trigger_ok = low_price < low and close > low
        direction = "needs_sweep_and_reclaim" if not trigger_ok else "sweep_reclaimed"
    else:
        return {"trigger_ok": False, "reason": f"unsupported_trigger:{config.trigger}"}

    distance_atr = distance / atr if atr and atr > 0 else None
    distance_pct = (distance / close) * 100.0 if close else None
    return {
        "trigger": config.trigger,
        "trigger_ok": bool(trigger_ok),
        "direction": direction,
        "range_high": round(float(high), 8),
        "range_low": round(float(low), 8),
        "lower_edge": round(float(lower_edge), 8),
        "upper_edge": round(float(upper_edge), 8),
        "trigger_level": round(float(level), 8),
        "distance_to_trigger": round(float(distance), 8),
        "distance_to_trigger_atr": None if distance_atr is None else round(float(distance_atr), 6),
        "distance_to_trigger_pct": None if distance_pct is None else round(float(distance_pct), 6),
        "trigger_progress_pct": round(max(0.0, min(150.0, float(progress_pct))), 3),
    }


def filter_checks(config: Any, snapshot: dict[str, Any], filter_names: list[str]) -> dict[str, bool | None]:
    signal = {
        "bar_index": snapshot.get("bar_index"),
        "atr": snapshot.get("atr"),
        "reason": config.trigger,
        "feature_snapshot": snapshot,
    }
    checks: dict[str, bool | None] = {}
    for name in filter_names:
        func = FILTER_FUNCS.get(name)
        checks[name] = None if func is None else bool(func(config, signal))
    return checks


def evaluate_bar(config: Any, bars: list[Any], features: list[dict[str, Any]], rsi14: list[float | None], filter_names: list[str], index: int) -> dict[str, Any]:
    bar = bars[index]
    high, low = previous_high_low(bars, index, config.lookback)
    feature = features[index]
    atr = finite_float(feature.get("atr"))
    width_atr = (high - low) / atr if high is not None and low is not None and high > low and atr and atr > 0 else None
    trend = trend_atr(bars, features, index, config.lookback)
    atr_ratio = finite_float(feature.get("atr_ratio"))
    rsi_value = rsi14[index] if index < len(rsi14) else None
    t_state = trigger_state(config, bar, high, low, atr)
    side_trigger_valid = not (
        (config.side == "LONG" and config.trigger in {"near_high", "sweep_up_reclaim"})
        or (config.side == "SHORT" and config.trigger in {"near_low", "sweep_down_reclaim"})
    )
    context_checks = {
        "lookback_ready": high is not None and low is not None and high > low,
        "atr_available": atr is not None and atr > 0,
        "width_atr_in_bounds": width_atr is not None and config.min_width_atr <= width_atr <= config.max_width_atr,
        "trend_atr_in_bounds": trend is not None and abs(trend) <= config.max_abs_trend_atr,
        "atr_ratio_ok": atr_ratio is not None and atr_ratio <= config.max_atr_ratio,
        "rsi_ok": rsi_ok(config, rsi_value),
        "side_trigger_valid": side_trigger_valid,
    }
    context_ok = all(bool(value) for value in context_checks.values())
    snapshot = {
        "bar_index": index,
        "bar_ts": str(bar.ts),
        "open": round(float(bar.open), 8),
        "high": round(float(bar.high), 8),
        "low": round(float(bar.low), 8),
        "close": round(float(bar.close), 8),
        "atr": None if atr is None else round(float(atr), 8),
        "width_atr": None if width_atr is None else round(float(width_atr), 6),
        "trend_atr": None if trend is None else round(float(trend), 6),
        "atr_ratio": None if atr_ratio is None else round(float(atr_ratio), 6),
        "rsi14": None if rsi_value is None else round(float(rsi_value), 6),
        "volume_z": feature.get("volume_z"),
        "funding": feature.get("funding"),
        "oi_delta_pct": feature.get("oi_delta_pct"),
        "spot_perp_divergence_pct": feature.get("spot_perp_divergence_pct"),
    }
    f_checks = filter_checks(config, snapshot, filter_names)
    refined_ready = context_ok and bool(t_state.get("trigger_ok")) and all(value is True for value in f_checks.values())
    blockers = [name for name, passed in context_checks.items() if not passed]
    filter_blockers = [name for name, passed in f_checks.items() if passed is False]
    return {
        "bar": snapshot,
        "context_checks": context_checks,
        "context_ok": context_ok,
        "context_blockers": blockers,
        "trigger": t_state,
        "trigger_ok": bool(t_state.get("trigger_ok")),
        "filter_checks": f_checks,
        "filter_blockers": filter_blockers,
        "refined_ready": refined_ready,
    }


def classify(latest: dict[str, Any], args: argparse.Namespace) -> tuple[str, str]:
    distance_atr = finite_float(latest.get("trigger", {}).get("distance_to_trigger_atr"))
    distance_pct = finite_float(latest.get("trigger", {}).get("distance_to_trigger_pct"))
    if latest.get("refined_ready"):
        return "range_pending_refined_trigger_active", "observer_should_emit_signal; keep no-trade boundary"
    if latest.get("trigger_ok"):
        return "range_pending_trigger_active_filters_blocking", "watch filters; do not relax without gates"
    if not latest.get("context_ok"):
        primary = ",".join(latest.get("context_blockers") or []) or "unknown_context"
        return "range_pending_context_blocked", f"wait_for_context:{primary}"
    near_by_atr = distance_atr is not None and distance_atr <= args.near_atr
    near_by_pct = distance_pct is not None and distance_pct <= args.near_pct
    if near_by_atr or near_by_pct:
        return "range_pending_near_trigger", "watch_next_closed_bar_for_trigger"
    return "range_pending_context_ready_not_near", "wait_for_price_to_approach_range_edge"


def render_markdown(report: dict[str, Any]) -> str:
    selected = report.get("selected_candidate") if isinstance(report.get("selected_candidate"), dict) else {}
    latest = report.get("latest") if isinstance(report.get("latest"), dict) else {}
    trigger = latest.get("trigger") if isinstance(latest.get("trigger"), dict) else {}
    lines = [
        "# Range Refined Pending Watch Monitor",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Observer-only proximity monitor.",
        "- Does not create signals, paper-entry intents or orders.",
        "- Does not relax filters or change active strategy.",
        "",
        "## Selected Candidate",
        "",
        f"- Strategy: `{selected.get('strategy_id')}`.",
        f"- Base: `{selected.get('base_strategy_id')}`.",
        f"- Filter: `{selected.get('filter_mode')}` (`{'+'.join(selected.get('filters') or [])}`).",
        f"- TF / side / trigger / RR: `{selected.get('interval')}` / `{selected.get('side')}` / `{selected.get('trigger')}` / `{selected.get('rr')}`.",
        "",
        "## Latest Watch",
        "",
        f"- Classification: `{report.get('classification')}`.",
        f"- Next action: `{report.get('next_action')}`.",
        f"- Bar: `{latest.get('bar', {}).get('bar_ts')}` close `{latest.get('bar', {}).get('close')}`.",
        f"- Context ok: `{latest.get('context_ok')}` blockers `{latest.get('context_blockers')}`.",
        f"- Trigger ok: `{latest.get('trigger_ok')}`.",
        f"- Trigger level: `{trigger.get('trigger_level')}`.",
        f"- Distance: `{trigger.get('distance_to_trigger')}` / `{trigger.get('distance_to_trigger_atr')}` ATR / `{trigger.get('distance_to_trigger_pct')}`%.",
        f"- Progress: `{trigger.get('trigger_progress_pct')}`%.",
        f"- Filter blockers: `{latest.get('filter_blockers')}`.",
        "",
        "## Recent Bars",
        "",
        "| Bar TS | Close | Context | Trigger | Dist ATR | Dist % | Progress | Refined Ready |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report.get("recent", []):
        if not isinstance(row, dict):
            continue
        row_trigger = row.get("trigger") if isinstance(row.get("trigger"), dict) else {}
        row_bar = row.get("bar") if isinstance(row.get("bar"), dict) else {}
        lines.append(
            f"| `{row_bar.get('bar_ts')}` | `{row_bar.get('close')}` | `{row.get('context_ok')}` | `{row.get('trigger_ok')}` | "
            f"`{row_trigger.get('distance_to_trigger_atr')}` | `{row_trigger.get('distance_to_trigger_pct')}` | `{row_trigger.get('trigger_progress_pct')}` | `{row.get('refined_ready')}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `near_trigger` means price is close enough to watch the next closed bar, not to enter.",
            "- `trigger_active_filters_blocking` means base trigger fired, but selected filters did not pass.",
            "- Only the forward observer/scoreboard/gates decide whether a future signal earns review.",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    refiner_path = resolve_path(args.refiner_report)
    source_path = resolve_path(args.source_range_report)
    cache_dir = resolve_path(args.cache_dir)
    journal_path = resolve_path(args.journal_path)
    refiner_report = read_json(refiner_path)
    source_report = read_json(source_path)
    selected = selected_candidate(refiner_report)
    config = build_config(selected, source_report)
    filter_names = list(selected.get("filters") or [])
    bars, features, rsi14 = load_interval_payload(cache_dir, config.interval, args.oi_lag, args.spot_perp_lookback)
    if not bars:
        raise ValueError(f"no_bars_loaded:{rel_path(cache_dir)}:{config.interval}")

    start = max(0, len(bars) - args.recent_bars)
    rows = [evaluate_bar(config, bars, features, rsi14, filter_names, index) for index in range(start, len(bars))]
    latest = rows[-1]
    classification, next_action = classify(latest, args)
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "range_refined_pending_watch_public_cache_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
            "changes_active_strategy": False,
        },
        "inputs": {
            "refiner_report": rel_path(refiner_path),
            "source_range_report": rel_path(source_path),
            "cache_dir": rel_path(cache_dir),
            "journal_path": rel_path(journal_path),
            "near_atr": args.near_atr,
            "near_pct": args.near_pct,
        },
        "selected_candidate": selected,
        "latest": latest,
        "recent": rows,
        "classification": classification,
        "next_action": next_action,
        "decision": "range_pending_watch_no_trade_permission",
        "can_trade": False,
    }
    append_jsonl(
        journal_path,
        {
            "event_type": "range_refined_pending_watch",
            "ts_emitted": report["generated_at"],
            "classification": classification,
            "next_action": next_action,
            "strategy_id": selected.get("strategy_id"),
            "base_strategy_id": selected.get("base_strategy_id"),
            "bar_ts": latest.get("bar", {}).get("bar_ts"),
            "close": latest.get("bar", {}).get("close"),
            "context_ok": latest.get("context_ok"),
            "trigger_ok": latest.get("trigger_ok"),
            "distance_to_trigger_atr": latest.get("trigger", {}).get("distance_to_trigger_atr"),
            "distance_to_trigger_pct": latest.get("trigger", {}).get("distance_to_trigger_pct"),
            "trigger_progress_pct": latest.get("trigger", {}).get("trigger_progress_pct"),
            "refined_ready": latest.get("refined_ready"),
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
        },
    )
    return report


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Observer-only proximity monitor for selected refined RANGE trigger")
    parser.add_argument("--refiner-report", default="docs/RANGE_WATCHLIST_REFINER_2026-06-16.json")
    parser.add_argument("--source-range-report", default="docs/RANGE_FAMILY_VALIDATOR_2026-06-16.json")
    parser.add_argument("--cache-dir", default="_dl/forward_paper_feed/cache")
    parser.add_argument("--journal-path", default="logs/forward_paper_feed/range_refined_pending_watch_monitor.jsonl")
    parser.add_argument("--oi-lag", type=int, default=12)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--recent-bars", type=int, default=24)
    parser.add_argument("--near-atr", type=float, default=0.5)
    parser.add_argument("--near-pct", type=float, default=0.5)
    parser.add_argument("--out-prefix", default="docs/RANGE_REFINED_PENDING_WATCH_2026-06-17")
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    latest = report.get("latest", {})
    trigger = latest.get("trigger", {}) if isinstance(latest, dict) else {}
    print(
        json.dumps(
            {
                "classification": report.get("classification"),
                "distance_to_trigger_atr": trigger.get("distance_to_trigger_atr"),
                "distance_to_trigger_pct": trigger.get("distance_to_trigger_pct"),
                "trigger_progress_pct": trigger.get("trigger_progress_pct"),
                "context_ok": latest.get("context_ok") if isinstance(latest, dict) else None,
                "trigger_ok": latest.get("trigger_ok") if isinstance(latest, dict) else None,
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

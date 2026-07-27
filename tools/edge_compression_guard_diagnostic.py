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

from tools.edge_same_shape_shadow_observer import config_from_candidate  # noqa: E402
from tools.liquidity_sweep_hardening import fold_summaries, summarize_trades  # noqa: E402
from tools.range_family_validator import (  # noqa: E402
    RangeConfig,
    bar_segments,
    generate_signals,
    load_interval_payload,
    parse_list,
    replay_signals,
    safe_float,
    safe_int,
)
from tools.range_watchlist_refiner import apply_filter_mode, make_filters  # noqa: E402


DEFAULT_DIAGNOSTIC = ROOT / "docs" / "EDGE_CANDIDATE_HARDENING_DIAGNOSTIC_2026-06-19.json"
DEFAULT_RANGE_REPORT = ROOT / "docs" / "RANGE_SWEEP_RECLAIM_REFINER_2026-06-18.json"
DEFAULT_CACHE_DIR = ROOT / "data" / "cache" / "binance_spot_perp_extended"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "EDGE_COMPRESSION_GUARD_DIAGNOSTIC_2026-06-19"


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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def signal_snapshot(signal: dict[str, Any]) -> dict[str, Any]:
    snapshot = signal.get("feature_snapshot")
    return snapshot if isinstance(snapshot, dict) else {}


def range_position(signal: dict[str, Any]) -> float | None:
    snapshot = signal_snapshot(signal)
    high = safe_float(snapshot.get("range_high"), math.nan)
    low = safe_float(snapshot.get("range_low"), math.nan)
    close = safe_float(snapshot.get("close"), math.nan)
    if not math.isfinite(close):
        close = safe_float(signal.get("close"), math.nan)
    if not all(math.isfinite(value) for value in (high, low, close)) or high <= low:
        return None
    return (close - low) / (high - low)


def atr_ratio(signal: dict[str, Any]) -> float | None:
    value = safe_float(signal_snapshot(signal).get("atr_ratio"), math.nan)
    return value if math.isfinite(value) else None


def enrich_signals_with_close(signals: list[dict[str, Any]], bars: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for signal in signals:
        row = dict(signal)
        index = safe_int(signal.get("bar_index"), -1)
        if 0 <= index < len(bars):
            row["bar_ts"] = str(bars[index].ts)
            row["close"] = float(bars[index].close)
        out.append(row)
    return out


def is_no_mans_land(signal: dict[str, Any], *, low: float, high: float) -> bool:
    pos = range_position(signal)
    return pos is not None and low <= pos <= high


def is_compressed(signal: dict[str, Any], *, max_atr_ratio: float) -> bool:
    ratio = atr_ratio(signal)
    return ratio is not None and ratio <= max_atr_ratio


def guard_keep(signal: dict[str, Any], guard: dict[str, Any]) -> bool:
    mode = str(guard["mode"])
    low = float(guard.get("position_low", 0.30))
    high = float(guard.get("position_high", 0.70))
    max_atr_ratio = float(guard.get("max_atr_ratio", 0.95))
    mid = is_no_mans_land(signal, low=low, high=high)
    compressed = is_compressed(signal, max_atr_ratio=max_atr_ratio)
    if mode == "none":
        return True
    if mode == "midrange_only":
        return not mid
    if mode == "compression_anywhere":
        return not compressed
    if mode == "compression_midrange":
        return not (mid and compressed)
    raise ValueError(f"unknown_guard_mode:{mode}")


def guard_id(guard: dict[str, Any]) -> str:
    mode = str(guard["mode"])
    if mode == "none":
        return "baseline_no_extra_guard"
    if mode == "compression_anywhere":
        return f"veto_compression_any_ar{float(guard['max_atr_ratio']):g}"
    if mode == "midrange_only":
        return f"veto_midrange_{float(guard['position_low']):g}_{float(guard['position_high']):g}"
    return (
        f"veto_compression_mid_ar{float(guard['max_atr_ratio']):g}"
        f"_pos{float(guard['position_low']):g}_{float(guard['position_high']):g}"
    )


def default_guards() -> list[dict[str, Any]]:
    guards: list[dict[str, Any]] = [{"mode": "none"}]
    for threshold in (0.85, 0.95, 1.05):
        guards.append({"mode": "compression_midrange", "max_atr_ratio": threshold, "position_low": 0.30, "position_high": 0.70})
    for threshold in (0.85, 0.95):
        guards.append({"mode": "compression_anywhere", "max_atr_ratio": threshold})
    guards.append({"mode": "midrange_only", "position_low": 0.30, "position_high": 0.70})
    guards.append({"mode": "midrange_only", "position_low": 0.20, "position_high": 0.80})
    return guards


def cost10_expectancy(row: dict[str, Any]) -> float | None:
    for item in row.get("cost_stress", []):
        if not isinstance(item, dict):
            continue
        if safe_float(item.get("extra_bps_per_side"), -1.0) == 10.0:
            summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
            value = safe_float(summary.get("expectancy_r"), math.nan)
            return value if math.isfinite(value) else None
    return None


def evaluate_signals(
    *,
    config: RangeConfig,
    bars: list[Any],
    signals: list[dict[str, Any]],
    settings: dict[str, Any],
    guard: dict[str, Any],
    segments: int,
) -> dict[str, Any]:
    kept = [signal for signal in signals if guard_keep(signal, guard)]
    cost = float(settings.get("cost_bps_per_side", 7.0))
    holdout_fraction = float(settings.get("holdout_fraction", 0.25))
    folds = int(settings.get("folds", 8))
    cost_stress_extra = str(settings.get("cost_stress_extra_bps", "0,5,10,20"))
    end_index = len(bars) - config.max_hold_bars - 1
    holdout_start = round(end_index * (1.0 - holdout_fraction))
    full_trades = replay_signals(config, bars, kept, cost, True)
    holdout_signals = [signal for signal in kept if int(signal["bar_index"]) >= holdout_start]
    holdout_trades = replay_signals(config, bars, holdout_signals, cost, True)
    fold_rows = fold_summaries(full_trades, folds)
    segment_rows: list[dict[str, Any]] = []
    for idx, (start, end) in enumerate(bar_segments(len(bars), config.max_hold_bars, segments), start=1):
        segment_signals = [signal for signal in kept if start <= int(signal["bar_index"]) < end]
        trades = replay_signals(config, bars, segment_signals, cost, True)
        segment_rows.append({"segment": idx, "signals": len(segment_signals), "summary": summarize_trades(trades)})
    stress_rows: list[dict[str, Any]] = []
    for extra in parse_list(cost_stress_extra, float):
        trades = replay_signals(config, bars, kept, cost + extra, True)
        stress_rows.append({"extra_bps_per_side": extra, "summary": summarize_trades(trades)})
    full_summary = summarize_trades(full_trades)
    holdout_summary = summarize_trades(holdout_trades)
    segment_values = [safe_float(item.get("summary", {}).get("expectancy_r"), -999.0) for item in segment_rows]
    segments_positive = sum(1 for value in segment_values if value > 0)
    vetoed = len(signals) - len(kept)
    return {
        "guard_id": guard_id(guard),
        "guard": guard,
        "signals_before_guard": len(signals),
        "signals_after_guard": len(kept),
        "signals_vetoed": vetoed,
        "veto_rate_pct": round(100.0 * vetoed / len(signals), 3) if signals else 0.0,
        "full": {"summary": full_summary, "stable_folds": sum(1 for fold in fold_rows if fold.get("stable")), "folds": fold_rows},
        "holdout": {"start_index": holdout_start, "signals": len(holdout_signals), "summary": holdout_summary},
        "segments": segment_rows,
        "segment_positive_ratio": round(segments_positive / len(segment_rows), 6) if segment_rows else 0.0,
        "worst_segment_expectancy_r": round(min(segment_values), 6) if segment_values else None,
        "cost_stress": stress_rows,
        "cost10_expectancy_r": cost10_expectancy({"cost_stress": stress_rows}),
        "sample_vetoed": [
            {
                "bar_index": signal.get("bar_index"),
                "bar_ts": signal.get("bar_ts"),
                "range_position": None if range_position(signal) is None else round(float(range_position(signal)), 6),
                "atr_ratio": None if atr_ratio(signal) is None else round(float(atr_ratio(signal)), 6),
                "reason": signal.get("reason"),
            }
            for signal in signals
            if not guard_keep(signal, guard)
        ][:5],
        "sample_kept": [
            {
                "bar_index": signal.get("bar_index"),
                "bar_ts": signal.get("bar_ts"),
                "range_position": None if range_position(signal) is None else round(float(range_position(signal)), 6),
                "atr_ratio": None if atr_ratio(signal) is None else round(float(atr_ratio(signal)), 6),
                "reason": signal.get("reason"),
            }
            for signal in kept[:5]
        ],
    }


def metric(row: dict[str, Any], scope: str, key: str) -> float | None:
    block = row.get(scope) if isinstance(row.get(scope), dict) else {}
    summary = block.get("summary") if isinstance(block.get("summary"), dict) else {}
    value = safe_float(summary.get(key), math.nan)
    return value if math.isfinite(value) else None


def rank_guard(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        safe_int(row.get("signals_after_guard")),
        metric(row, "holdout", "expectancy_r") if metric(row, "holdout", "expectancy_r") is not None else -999.0,
        row.get("cost10_expectancy_r") if row.get("cost10_expectancy_r") is not None else -999.0,
        safe_float(row.get("segment_positive_ratio"), -999.0),
        metric(row, "full", "expectancy_r") if metric(row, "full", "expectancy_r") is not None else -999.0,
    )


def compare_to_baseline(row: dict[str, Any], baseline: dict[str, Any], min_holdout_trades: int) -> dict[str, Any]:
    holdout_exp = metric(row, "holdout", "expectancy_r")
    base_holdout_exp = metric(baseline, "holdout", "expectancy_r")
    full_exp = metric(row, "full", "expectancy_r")
    base_full_exp = metric(baseline, "full", "expectancy_r")
    cost10 = row.get("cost10_expectancy_r")
    base_cost10 = baseline.get("cost10_expectancy_r")
    holdout_trades = safe_int((row.get("holdout") or {}).get("summary", {}).get("trades"))
    signals_after = safe_int(row.get("signals_after_guard"))
    signals_before = safe_int(row.get("signals_before_guard"))
    signal_retention = signals_after / signals_before if signals_before else 0.0
    improved_holdout = holdout_exp is not None and base_holdout_exp is not None and holdout_exp > base_holdout_exp
    improved_full = full_exp is not None and base_full_exp is not None and full_exp >= base_full_exp
    improved_cost = cost10 is not None and base_cost10 is not None and cost10 >= base_cost10
    enough_holdout = holdout_trades >= min_holdout_trades
    enough_frequency = signal_retention >= 0.70
    if row["guard_id"] == baseline["guard_id"]:
        decision = "baseline"
    elif improved_holdout and improved_full and improved_cost and enough_holdout and enough_frequency:
        decision = "guard_candidate_for_forward_shadow"
    elif improved_holdout and enough_holdout:
        decision = "guard_watchlist_only"
    else:
        decision = "guard_reject_or_no_value"
    return {
        "delta_full_expectancy_r": None if full_exp is None or base_full_exp is None else round(full_exp - base_full_exp, 6),
        "delta_holdout_expectancy_r": None if holdout_exp is None or base_holdout_exp is None else round(holdout_exp - base_holdout_exp, 6),
        "delta_cost10_expectancy_r": None if cost10 is None or base_cost10 is None else round(float(cost10) - float(base_cost10), 6),
        "signal_retention_pct": round(100.0 * signal_retention, 3),
        "enough_holdout": enough_holdout,
        "enough_frequency": enough_frequency,
        "decision": decision,
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    diagnostic_path = resolve_path(args.diagnostic)
    range_report_path = resolve_path(args.range_report)
    cache_dir = resolve_path(args.cache_dir)
    diagnostic = read_json(diagnostic_path)
    range_report = read_json(range_report_path)
    selected = diagnostic.get("selected_candidate") if isinstance(diagnostic.get("selected_candidate"), dict) else {}
    if not selected:
        raise ValueError("selected_candidate_missing")
    settings = range_report.get("settings") if isinstance(range_report.get("settings"), dict) else {}
    config = config_from_candidate(selected, settings)
    bars, features, rsi14 = load_interval_payload(cache_dir, config.interval, args.oi_lag, args.spot_perp_lookback)
    if not bars:
        raise ValueError(f"no_bars_for_interval:{config.interval}")
    end_index = len(bars) - config.max_hold_bars - 1
    raw_signals = generate_signals(config, bars, features, rsi14, 0, end_index)
    filter_names = tuple(make_filters().get(str(selected.get("filter_mode") or ""), tuple(selected.get("filters") or ())))
    refined_signals = apply_filter_mode(config, raw_signals, filter_names)
    refined_signals = enrich_signals_with_close(refined_signals, bars)
    results = [
        evaluate_signals(config=config, bars=bars, signals=refined_signals, settings=settings, guard=guard, segments=args.segments)
        for guard in default_guards()
    ]
    baseline = next(row for row in results if row["guard_id"] == "baseline_no_extra_guard")
    for row in results:
        row["comparison_to_baseline"] = compare_to_baseline(row, baseline, args.min_holdout_trades)
    ranked = sorted(results, key=rank_guard, reverse=True)
    candidates = [row for row in results if row["comparison_to_baseline"]["decision"] == "guard_candidate_for_forward_shadow"]
    watchlist = [row for row in results if row["comparison_to_baseline"]["decision"] == "guard_watchlist_only"]
    if candidates:
        decision = "compression_guard_candidate_for_forward_shadow"
        next_action = "add best compression guard to observer-only shadow path before any promotion"
    elif watchlist:
        decision = "compression_guard_watchlist_only"
        next_action = "keep as research watchlist; do not modify active candidate"
    else:
        decision = "compression_guard_rejected_for_current_edge"
        next_action = "do not add this guard to current near-high edge; route compression idea to breakout/grid tests"
    latest_index = len(bars) - 1
    latest_raw = generate_signals(config, bars, features, rsi14, latest_index, latest_index + 1)
    latest_refined = enrich_signals_with_close(apply_filter_mode(config, latest_raw, filter_names), bars)
    return {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "mode": "research_diagnostic_only",
            "can_trade": False,
            "creates_paper_entry_intents": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "changes_active_strategy": False,
        },
        "inputs": {
            "diagnostic": rel_path(diagnostic_path),
            "range_report": rel_path(range_report_path),
            "cache_dir": rel_path(cache_dir),
        },
        "selected_candidate": selected,
        "settings": {
            "cost_bps_per_side": settings.get("cost_bps_per_side", 7.0),
            "cost_stress_extra_bps": settings.get("cost_stress_extra_bps", "0,5,10,20"),
            "holdout_fraction": settings.get("holdout_fraction", 0.25),
            "folds": settings.get("folds", 8),
            "segments": args.segments,
            "min_holdout_trades_for_guard": args.min_holdout_trades,
        },
        "data": {
            "interval": config.interval,
            "bars": len(bars),
            "latest_closed_bar_ts": str(bars[-1].ts),
            "latest_close": round(float(bars[-1].close), 8),
            "raw_signals": len(raw_signals),
            "refined_signals": len(refined_signals),
            "filters": list(filter_names),
        },
        "latest_bar_guard_state": {
            "raw_signals": len(latest_raw),
            "refined_signals": len(latest_refined),
            "refined_signal_guard_flags": [
                {
                    "range_position": None if range_position(signal) is None else round(float(range_position(signal)), 6),
                    "atr_ratio": None if atr_ratio(signal) is None else round(float(atr_ratio(signal)), 6),
                    "guard_vetoes": [
                        guard_id(guard)
                        for guard in default_guards()
                        if guard["mode"] != "none" and not guard_keep(signal, guard)
                    ],
                }
                for signal in latest_refined
            ],
        },
        "baseline": baseline,
        "results": ranked,
        "candidate_count": len(candidates),
        "watchlist_count": len(watchlist),
        "decision": decision,
        "next_action": next_action,
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    selected = report["selected_candidate"]
    data = report["data"]
    lines = [
        "# Edge Compression Guard Diagnostic",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Research diagnostic only.",
        "- Tests `Compression / No-Man's-Land` as an additional veto over the current edge candidate.",
        "- Does not change the active observer, create paper intents or send orders.",
        "",
        "## Decision",
        "",
        f"- Decision: `{report['decision']}`.",
        f"- Next action: `{report['next_action']}`.",
        f"- Candidate guards: `{report['candidate_count']}`.",
        f"- Watchlist guards: `{report['watchlist_count']}`.",
        f"- Can trade: `{report['can_trade']}`.",
        "",
        "## Selected Edge",
        "",
        f"- Strategy: `{selected.get('strategy_id')}`.",
        f"- Filters: `{', '.join(selected.get('filters') or [])}`.",
        f"- Interval / side / RR / hold: `{selected.get('interval')}` / `{selected.get('side')}` / `{selected.get('rr')}` / `{selected.get('max_hold_bars')}`.",
        f"- Data: `{data.get('bars')}` bars, latest `{data.get('latest_closed_bar_ts')}` close `{data.get('latest_close')}`.",
        f"- Raw/refined historical signals: `{data.get('raw_signals')}` / `{data.get('refined_signals')}`.",
        "",
        "## Guard Results",
        "",
        "| Guard | Veto % | Full Exp | Holdout Trades | Holdout Exp | Cost+10 Exp | Delta Holdout | Decision |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in report["results"]:
        full = row.get("full", {}).get("summary", {})
        holdout = row.get("holdout", {}).get("summary", {})
        comp = row.get("comparison_to_baseline", {})
        lines.append(
            f"| `{row.get('guard_id')}` | `{row.get('veto_rate_pct')}` | `{full.get('expectancy_r')}` | "
            f"`{holdout.get('trades')}` | `{holdout.get('expectancy_r')}` | `{row.get('cost10_expectancy_r')}` | "
            f"`{comp.get('delta_holdout_expectancy_r')}` | `{comp.get('decision')}` |"
        )
    latest = report.get("latest_bar_guard_state", {})
    lines.extend(
        [
            "",
            "## Latest Bar State",
            "",
            f"- Raw/refined signals on latest bar: `{latest.get('raw_signals')}` / `{latest.get('refined_signals')}`.",
            f"- Refined signal guard flags: `{latest.get('refined_signal_guard_flags')}`.",
            "",
            "## Interpretation",
            "",
            "- If veto rate is zero, the guard is irrelevant for this edge shape.",
            "- If holdout improves but frequency collapses, keep it as research only.",
            "- A guard can enter forward shadow only if it improves holdout/full/cost stress and keeps enough frequency.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Research-only compression/no-man's-land guard diagnostic for current edge candidate.")
    parser.add_argument("--diagnostic", default=str(DEFAULT_DIAGNOSTIC))
    parser.add_argument("--range-report", default=str(DEFAULT_RANGE_REPORT))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--oi-lag", type=int, default=12)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--segments", type=int, default=6)
    parser.add_argument("--min-holdout-trades", type=int, default=15)
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    write_text(out_prefix.with_suffix(".md"), render_markdown(report))
    print(
        json.dumps(
            {
                "status": "ok",
                "decision": report["decision"],
                "candidate_count": report["candidate_count"],
                "watchlist_count": report["watchlist_count"],
                "baseline_holdout_expectancy_r": report["baseline"]["holdout"]["summary"].get("expectancy_r"),
                "json": rel_path(out_prefix.with_suffix(".json")),
                "md": rel_path(out_prefix.with_suffix(".md")),
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

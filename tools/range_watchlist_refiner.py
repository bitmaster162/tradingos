#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_hardening import fold_summaries, summarize_trades  # noqa: E402
from tools.range_family_validator import (  # noqa: E402
    RangeConfig,
    bar_segments,
    generate_signals,
    load_interval_payload,
    parse_list,
    parse_rr_list,
    replay_signals,
    safe_float,
    safe_int,
)


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


def stable_fold_count(folds: list[dict[str, Any]]) -> int:
    return sum(1 for item in folds if item.get("stable"))


def rr_to_pair(value: str) -> tuple[float, float]:
    parsed = parse_rr_list(value)
    if not parsed:
        raise ValueError(f"invalid_rr:{value}")
    return parsed[0]


def config_from_row(row: dict[str, Any], settings: dict[str, Any]) -> RangeConfig:
    stop, take = rr_to_pair(str(row["rr"]))
    side = str(row["side"]).upper()
    trigger = str(row["trigger"])
    if side == "LONG":
        rsi_filter = "lte"
        rsi_threshold = 45.0 if trigger == "near_low" else 50.0
    else:
        rsi_filter = "gte"
        rsi_threshold = 55.0 if trigger == "near_high" else 50.0
    return RangeConfig(
        strategy_id=str(row["strategy_id"]),
        interval=str(row["interval"]),
        side=side,
        trigger=trigger,
        lookback=int(row["lookback"]),
        edge_pct=float(row["edge_pct"]),
        min_width_atr=float(settings.get("min_width_atr", 2.0)),
        max_width_atr=float(settings.get("max_width_atr", 12.0)),
        max_abs_trend_atr=float(settings.get("max_abs_trend_atr", 2.2)),
        max_atr_ratio=float(settings.get("max_atr_ratio", 1.15)),
        rsi_filter=rsi_filter,
        rsi_threshold=rsi_threshold,
        stop_atr=stop,
        take_atr=take,
        max_hold_bars=int(row["max_hold_bars"]),
    )


def signal_value(signal: dict[str, Any], name: str) -> float | None:
    snapshot = signal.get("feature_snapshot") if isinstance(signal.get("feature_snapshot"), dict) else {}
    value = snapshot.get(name)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def side_is_short(config: RangeConfig) -> bool:
    return config.side.upper() == "SHORT"


def funding_aligned(config: RangeConfig, signal: dict[str, Any]) -> bool:
    funding = signal_value(signal, "funding")
    if funding is None:
        return False
    return funding >= 0 if side_is_short(config) else funding <= 0


def funding_compressed(_: RangeConfig, signal: dict[str, Any]) -> bool:
    funding = signal_value(signal, "funding")
    return funding is not None and abs(funding) <= 0.0002


def spot_confirms(config: RangeConfig, signal: dict[str, Any]) -> bool:
    div = signal_value(signal, "spot_perp_divergence_pct")
    if div is None:
        return False
    return div <= 0 if side_is_short(config) else div >= 0


def spot_strong_confirms(config: RangeConfig, signal: dict[str, Any]) -> bool:
    div = signal_value(signal, "spot_perp_divergence_pct")
    if div is None:
        return False
    return div <= -0.005 if side_is_short(config) else div >= 0.005


def oi_expansion(_: RangeConfig, signal: dict[str, Any]) -> bool:
    oi_delta = signal_value(signal, "oi_delta_pct")
    return oi_delta is not None and oi_delta >= 0


def oi_contraction(_: RangeConfig, signal: dict[str, Any]) -> bool:
    oi_delta = signal_value(signal, "oi_delta_pct")
    return oi_delta is not None and oi_delta <= 0


def volume_calm(_: RangeConfig, signal: dict[str, Any]) -> bool:
    volume_z = signal_value(signal, "volume_z")
    return volume_z is not None and volume_z <= 1.0


def volume_quiet(_: RangeConfig, signal: dict[str, Any]) -> bool:
    volume_z = signal_value(signal, "volume_z")
    return volume_z is not None and volume_z <= 0.0


def make_filters() -> dict[str, tuple[str, ...]]:
    return {
        "baseline": tuple(),
        "funding_aligned": ("funding_aligned",),
        "funding_compressed": ("funding_compressed",),
        "spot_confirms": ("spot_confirms",),
        "spot_strong_confirms": ("spot_strong_confirms",),
        "oi_expansion": ("oi_expansion",),
        "oi_contraction": ("oi_contraction",),
        "volume_calm": ("volume_calm",),
        "volume_quiet": ("volume_quiet",),
        "funding_spot": ("funding_aligned", "spot_confirms"),
        "funding_spot_oi_expansion": ("funding_aligned", "spot_confirms", "oi_expansion"),
        "funding_spot_oi_contraction": ("funding_aligned", "spot_confirms", "oi_contraction"),
        "spot_oi_expansion": ("spot_confirms", "oi_expansion"),
        "spot_oi_contraction": ("spot_confirms", "oi_contraction"),
        "funding_compressed_spot": ("funding_compressed", "spot_confirms"),
        "perp_exhaustion": ("funding_aligned", "spot_confirms", "oi_expansion", "volume_calm"),
        "quiet_spot_reversion": ("spot_confirms", "volume_quiet"),
    }


FILTER_FUNCS: dict[str, Callable[[RangeConfig, dict[str, Any]], bool]] = {
    "funding_aligned": funding_aligned,
    "funding_compressed": funding_compressed,
    "spot_confirms": spot_confirms,
    "spot_strong_confirms": spot_strong_confirms,
    "oi_expansion": oi_expansion,
    "oi_contraction": oi_contraction,
    "volume_calm": volume_calm,
    "volume_quiet": volume_quiet,
}


def apply_filter_mode(config: RangeConfig, signals: list[dict[str, Any]], filter_names: tuple[str, ...]) -> list[dict[str, Any]]:
    if not filter_names:
        return list(signals)
    output = []
    for signal in signals:
        if all(FILTER_FUNCS[name](config, signal) for name in filter_names):
            output.append(signal)
    return output


def evaluate_filtered(
    *,
    base_config: RangeConfig,
    filter_mode: str,
    filter_names: tuple[str, ...],
    bars: list[Any],
    full_signals: list[dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    config = replace(base_config, strategy_id=f"{base_config.strategy_id}__refine_{filter_mode}")
    end_index = len(bars) - config.max_hold_bars - 1
    holdout_start = round(end_index * (1.0 - args.holdout_fraction))
    filtered_signals = apply_filter_mode(config, full_signals, filter_names)
    full_trades = replay_signals(config, bars, filtered_signals, args.cost_bps_per_side, not args.allow_overlap)
    holdout_signals = [item for item in filtered_signals if int(item["bar_index"]) >= holdout_start]
    holdout_trades = replay_signals(config, bars, holdout_signals, args.cost_bps_per_side, not args.allow_overlap)
    fold_rows = fold_summaries(full_trades, args.folds)
    segment_rows = []
    for idx, (start, end) in enumerate(bar_segments(len(bars), config.max_hold_bars, args.segments), start=1):
        signals = [item for item in filtered_signals if start <= int(item["bar_index"]) < end]
        trades = replay_signals(config, bars, signals, args.cost_bps_per_side, not args.allow_overlap)
        segment_rows.append({"segment": idx, "signals": len(signals), "summary": summarize_trades(trades)})
    stress_rows = []
    for extra in parse_list(args.cost_stress_extra_bps, float):
        trades = replay_signals(config, bars, filtered_signals, args.cost_bps_per_side + extra, not args.allow_overlap)
        stress_rows.append({"extra_bps_per_side": extra, "summary": summarize_trades(trades)})

    full_summary = summarize_trades(full_trades)
    holdout_summary = summarize_trades(holdout_trades)
    segments_positive = sum(1 for item in segment_rows if safe_float(item["summary"].get("expectancy_r")) > 0)
    segment_ratio = segments_positive / len(segment_rows) if segment_rows else 0.0
    worst_segment = min((safe_float(item["summary"].get("expectancy_r")) for item in segment_rows), default=-999.0)
    cost10 = next((item for item in stress_rows if safe_float(item["extra_bps_per_side"], 0.0) == 10.0), None)
    checks = {
        "min_full_trades": safe_int(full_summary.get("trades")) >= args.min_full_trades,
        "min_full_expectancy": safe_float(full_summary.get("expectancy_r")) >= args.min_expectancy_r,
        "min_holdout_trades": safe_int(holdout_summary.get("trades")) >= args.min_holdout_trades,
        "min_holdout_expectancy": safe_float(holdout_summary.get("expectancy_r")) >= args.min_expectancy_r,
        "segment_positive_ratio": segment_ratio >= args.min_segment_positive_ratio,
        "worst_segment_floor": worst_segment >= -abs(args.max_worst_segment_expectancy_r),
        "cost_stress_10bps_positive": safe_float(cost10.get("summary", {}).get("expectancy_r")) > 0 if isinstance(cost10, dict) else False,
    }
    if all(checks.values()) and filter_mode != "baseline":
        verdict = "range_refined_candidate_for_forward_observation"
    elif checks["min_holdout_trades"] and checks["min_holdout_expectancy"]:
        verdict = "range_refined_watchlist_only"
    else:
        verdict = "reject_or_research_only"
    return {
        "base_strategy_id": base_config.strategy_id,
        "strategy_id": config.strategy_id,
        "filter_mode": filter_mode,
        "filters": list(filter_names),
        "interval": config.interval,
        "side": config.side,
        "trigger": config.trigger,
        "rr": f"{config.stop_atr:g}:{config.take_atr:g}",
        "max_hold_bars": config.max_hold_bars,
        "signals": len(filtered_signals),
        "full": {"summary": full_summary, "stable_folds": stable_fold_count(fold_rows), "folds": fold_rows},
        "holdout": {"start_index": holdout_start, "signals": len(holdout_signals), "summary": holdout_summary},
        "segments": segment_rows,
        "segment_positive_ratio": round(segment_ratio, 6),
        "worst_segment_expectancy_r": round(worst_segment, 6),
        "cost_stress": stress_rows,
        "checks": checks,
        "verdict": verdict,
        "sample_trades": [trade.__dict__ for trade in full_trades[:3]],
    }


def rank_key(item: dict[str, Any]) -> tuple[Any, ...]:
    ranks = {
        "range_refined_candidate_for_forward_observation": 3,
        "range_refined_watchlist_only": 2,
        "reject_or_research_only": 1,
    }
    return (
        ranks.get(str(item.get("verdict")), 0),
        safe_float(item.get("holdout", {}).get("summary", {}).get("expectancy_r")),
        safe_int(item.get("holdout", {}).get("summary", {}).get("trades")),
        safe_float(item.get("full", {}).get("summary", {}).get("expectancy_r")),
    )


def cost_stress_expectancy(item: dict[str, Any], extra_bps: float) -> float:
    cost_row = next(
        (row for row in item.get("cost_stress", []) if safe_float(row.get("extra_bps_per_side"), 0.0) == extra_bps),
        None,
    )
    if not isinstance(cost_row, dict):
        return -999.0
    return safe_float(cost_row.get("summary", {}).get("expectancy_r"), -999.0)


def robust_candidate_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        safe_float(item.get("full", {}).get("summary", {}).get("expectancy_r")),
        cost_stress_expectancy(item, 10.0),
        safe_float(item.get("segment_positive_ratio")),
        safe_float(item.get("worst_segment_expectancy_r")),
        safe_float(item.get("holdout", {}).get("summary", {}).get("expectancy_r")),
        safe_int(item.get("full", {}).get("summary", {}).get("trades")),
    )


def select_base_rows(source: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    rows = [item for item in source.get("results", []) if item.get("verdict") == "range_watchlist_only"]
    rows.sort(
        key=lambda item: (
            safe_float(item.get("holdout", {}).get("summary", {}).get("expectancy_r")),
            safe_int(item.get("holdout", {}).get("summary", {}).get("trades")),
            safe_float(item.get("full", {}).get("summary", {}).get("expectancy_r")),
        ),
        reverse=True,
    )
    return rows[:limit]


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Range Watchlist Refiner",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Research-only refinement of RANGE watchlist strategies.",
        "- Tests OI/funding/spot/volume filters on top watchlist rows.",
        "- Does not change the locked breakout strategy or the range family validator.",
        "- Does not grant paper or live trading permission.",
        "",
        "## Decision",
        "",
        f"- Decision: `{report['decision']}`.",
        f"- Next action: `{report['next_action']}`.",
        f"- Can trade: `{report['can_trade']}`.",
        "",
        "## Selected Candidate",
        "",
    ]
    selected = report.get("selected_candidate")
    if isinstance(selected, dict):
        selected_full = selected["full"]["summary"]
        selected_holdout = selected["holdout"]["summary"]
        selected_cost10 = next((row for row in selected["cost_stress"] if safe_float(row["extra_bps_per_side"], 0.0) == 10.0), None)
        lines.extend(
            [
                f"- Base: `{selected['base_strategy_id']}`.",
                f"- Filter: `{selected['filter_mode']}` (`{'+'.join(selected['filters'])}`).",
                f"- TF/side/RR: `{selected['interval']}` `{selected['side']}` `{selected['rr']}`.",
                f"- Full: `{selected_full.get('trades')}` trades, expectancy `{selected_full.get('expectancy_r')}`R.",
                f"- Holdout: `{selected_holdout.get('trades')}` trades, expectancy `{selected_holdout.get('expectancy_r')}`R.",
                f"- Segment ratio / worst segment: `{selected['segment_positive_ratio']}` / `{selected['worst_segment_expectancy_r']}`.",
                f"- Cost +10bps expectancy: `{selected_cost10['summary'].get('expectancy_r') if selected_cost10 else None}`R.",
                "- Status: observer-only candidate; this is not paper/live trade permission.",
                "",
            ]
        )
    else:
        lines.extend(["- None.", ""])
    lines.extend(
        [
        "## Results",
        "",
        "| Verdict | Base | Filter | TF | Side | RR | Signals | Full Trades | Full Exp | Holdout Trades | Holdout Exp | Seg+ | Worst Seg | Cost +10 Exp |",
        "|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in report["top_results"]:
        full = item["full"]["summary"]
        holdout = item["holdout"]["summary"]
        cost10 = next((row for row in item["cost_stress"] if safe_float(row["extra_bps_per_side"], 0.0) == 10.0), None)
        lines.append(
            f"| `{item['verdict']}` | `{item['base_strategy_id']}` | `{item['filter_mode']}` | `{item['interval']}` | `{item['side']}` | `{item['rr']}` | "
            f"`{item['signals']}` | `{full.get('trades')}` | `{full.get('expectancy_r')}` | "
            f"`{holdout.get('trades')}` | `{holdout.get('expectancy_r')}` | `{item['segment_positive_ratio']}` | "
            f"`{item['worst_segment_expectancy_r']}` | `{cost10['summary'].get('expectancy_r') if cost10 else None}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- A refined candidate must improve robustness, not only the recent holdout slice.",
            "- If all refined variants fail, the correct action is more feature work, not live trading.",
            "- `range_refined_candidate_for_forward_observation` still means observer-only forward comparison.",
            "",
        ]
    )
    return "\n".join(lines)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "verdict",
        "base_strategy_id",
        "filter_mode",
        "filters",
        "interval",
        "side",
        "signals",
        "full_trades",
        "full_expectancy_r",
        "holdout_trades",
        "holdout_expectancy_r",
        "segment_positive_ratio",
        "worst_segment_expectancy_r",
        "cost10_expectancy_r",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in rows:
            cost10 = next((row for row in item["cost_stress"] if safe_float(row["extra_bps_per_side"], 0.0) == 10.0), None)
            writer.writerow(
                {
                    "verdict": item["verdict"],
                    "base_strategy_id": item["base_strategy_id"],
                    "filter_mode": item["filter_mode"],
                    "filters": "+".join(item["filters"]),
                    "interval": item["interval"],
                    "side": item["side"],
                    "signals": item["signals"],
                    "full_trades": item["full"]["summary"].get("trades"),
                    "full_expectancy_r": item["full"]["summary"].get("expectancy_r"),
                    "holdout_trades": item["holdout"]["summary"].get("trades"),
                    "holdout_expectancy_r": item["holdout"]["summary"].get("expectancy_r"),
                    "segment_positive_ratio": item["segment_positive_ratio"],
                    "worst_segment_expectancy_r": item["worst_segment_expectancy_r"],
                    "cost10_expectancy_r": cost10["summary"].get("expectancy_r") if cost10 else None,
                }
            )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    source_path = resolve_path(args.source_report)
    cache_dir = resolve_path(args.cache_dir)
    source = read_json(source_path)
    settings = source.get("settings") if isinstance(source.get("settings"), dict) else {}
    base_rows = select_base_rows(source, args.base_limit)
    payloads: dict[str, tuple[list[Any], list[dict[str, Any]], list[float | None]]] = {}
    results = []
    filters = make_filters()
    for row in base_rows:
        config = config_from_row(row, settings)
        if config.interval not in payloads:
            payloads[config.interval] = load_interval_payload(cache_dir, config.interval, args.oi_lag, args.spot_perp_lookback)
        bars, features, rsi14 = payloads[config.interval]
        end_index = len(bars) - config.max_hold_bars - 1
        full_signals = generate_signals(config, bars, features, rsi14, 0, end_index)
        for mode, filter_names in filters.items():
            results.append(
                evaluate_filtered(
                    base_config=config,
                    filter_mode=mode,
                    filter_names=filter_names,
                    bars=bars,
                    full_signals=full_signals,
                    args=args,
                )
            )
    results.sort(key=rank_key, reverse=True)
    candidates = [item for item in results if item["verdict"] == "range_refined_candidate_for_forward_observation"]
    watchlist = [item for item in results if item["verdict"] == "range_refined_watchlist_only"]
    selected_candidate = max(candidates, key=robust_candidate_key) if candidates else None
    if candidates:
        decision = "range_refined_candidates_need_forward_observation"
        next_action = "add_top_refined_range_candidate_to_observer_only_comparison"
    elif watchlist:
        decision = "range_refined_watchlist_only_no_promotion"
        next_action = "inspect_top_refined_filters_and_design_next_feature"
    else:
        decision = "no_range_refinement_candidate"
        next_action = "redesign_range_features_or_keep_observing_breakout_only"
    return {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "mode": "research_only",
            "public_or_cached_data_only": True,
            "sends_orders": False,
            "can_trade": False,
        },
        "inputs": {
            "source_report": rel_path(source_path),
            "cache_dir": rel_path(cache_dir),
            "base_limit": args.base_limit,
        },
        "settings": {
            "cost_bps_per_side": args.cost_bps_per_side,
            "cost_stress_extra_bps": args.cost_stress_extra_bps,
            "holdout_fraction": args.holdout_fraction,
            "folds": args.folds,
            "segments": args.segments,
            "min_full_trades": args.min_full_trades,
            "min_holdout_trades": args.min_holdout_trades,
            "min_expectancy_r": args.min_expectancy_r,
            "min_segment_positive_ratio": args.min_segment_positive_ratio,
            "max_worst_segment_expectancy_r": args.max_worst_segment_expectancy_r,
        },
        "base_rows_tested": len(base_rows),
        "tested": len(results),
        "candidate_count": len(candidates),
        "watchlist_count": len(watchlist),
        "selected_candidate": selected_candidate,
        "top_results": results[: args.top_results],
        "results": results,
        "decision": decision,
        "next_action": next_action,
        "can_trade": False,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Research-only refiner for top RANGE watchlist strategies")
    parser.add_argument("--source-report", default="docs/RANGE_FAMILY_VALIDATOR_2026-06-16.json")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--base-limit", type=int, default=12)
    parser.add_argument("--oi-lag", type=int, default=12)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--cost-bps-per-side", type=float, default=7.0)
    parser.add_argument("--cost-stress-extra-bps", default="0,5,10,20")
    parser.add_argument("--holdout-fraction", type=float, default=0.25)
    parser.add_argument("--folds", type=int, default=8)
    parser.add_argument("--segments", type=int, default=6)
    parser.add_argument("--allow-overlap", action="store_true")
    parser.add_argument("--min-full-trades", type=int, default=60)
    parser.add_argument("--min-holdout-trades", type=int, default=15)
    parser.add_argument("--min-expectancy-r", type=float, default=0.05)
    parser.add_argument("--min-segment-positive-ratio", type=float, default=0.66)
    parser.add_argument("--max-worst-segment-expectancy-r", type=float, default=0.25)
    parser.add_argument("--top-results", type=int, default=30)
    parser.add_argument("--out-prefix", default="docs/RANGE_WATCHLIST_REFINER_2026-06-16")
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    write_csv(out_prefix.with_name(out_prefix.name + "_summary.csv"), report["results"])
    print(
        json.dumps(
            {
                "status": "ok",
                "decision": report["decision"],
                "tested": report["tested"],
                "candidate_count": report["candidate_count"],
                "watchlist_count": report["watchlist_count"],
                "out": rel_path(out_prefix.with_suffix(".json")),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

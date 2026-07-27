#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
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
    bar_segments,
    generate_signals,
    load_interval_payload,
    replay_signals,
)
from tools.range_refined_forward_observer import build_config, selected_candidate  # noqa: E402
from tools.range_watchlist_refiner import FILTER_FUNCS, safe_float, safe_int  # noqa: E402


FilterFunc = Callable[[Any, dict[str, Any]], bool]


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


def signal_value(signal: dict[str, Any], name: str) -> float | None:
    snapshot = signal.get("feature_snapshot") if isinstance(signal.get("feature_snapshot"), dict) else {}
    value = snapshot.get(name)
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def filter_named(name: str) -> FilterFunc:
    func = FILTER_FUNCS[name]
    return lambda config, signal: bool(func(config, signal))


def filter_oi_ge(threshold: float) -> FilterFunc:
    return lambda _config, signal: (signal_value(signal, "oi_delta_pct") is not None and signal_value(signal, "oi_delta_pct") >= threshold)


def filter_oi_present() -> FilterFunc:
    return lambda _config, signal: signal_value(signal, "oi_delta_pct") is not None


def make_variants() -> list[dict[str, Any]]:
    return [
        {
            "variant_id": "locked_funding_spot_oi_expansion",
            "description": "Current selected filter stack.",
            "filters": ["funding_aligned", "spot_confirms", "oi_expansion"],
            "funcs": [filter_named("funding_aligned"), filter_named("spot_confirms"), filter_named("oi_expansion")],
        },
        {
            "variant_id": "drop_oi_expansion_funding_spot",
            "description": "Drops the OI expansion requirement; keeps funding and spot confirmation.",
            "filters": ["funding_aligned", "spot_confirms"],
            "funcs": [filter_named("funding_aligned"), filter_named("spot_confirms")],
        },
        {
            "variant_id": "funding_spot_soft_oi_ge_minus_0_10",
            "description": "Keeps funding+spot and allows small OI contraction down to -0.10%.",
            "filters": ["funding_aligned", "spot_confirms", "oi_delta_pct>=-0.10"],
            "funcs": [filter_named("funding_aligned"), filter_named("spot_confirms"), filter_oi_ge(-0.10)],
        },
        {
            "variant_id": "funding_spot_soft_oi_ge_minus_0_25",
            "description": "Keeps funding+spot and allows OI contraction down to -0.25%.",
            "filters": ["funding_aligned", "spot_confirms", "oi_delta_pct>=-0.25"],
            "funcs": [filter_named("funding_aligned"), filter_named("spot_confirms"), filter_oi_ge(-0.25)],
        },
        {
            "variant_id": "funding_spot_oi_present",
            "description": "Keeps funding+spot and requires OI data to be present, but not positive.",
            "filters": ["funding_aligned", "spot_confirms", "oi_present"],
            "funcs": [filter_named("funding_aligned"), filter_named("spot_confirms"), filter_oi_present()],
        },
        {
            "variant_id": "spot_oi_expansion",
            "description": "Drops funding alignment; keeps spot confirmation and OI expansion.",
            "filters": ["spot_confirms", "oi_expansion"],
            "funcs": [filter_named("spot_confirms"), filter_named("oi_expansion")],
        },
        {
            "variant_id": "spot_only",
            "description": "Spot confirmation only.",
            "filters": ["spot_confirms"],
            "funcs": [filter_named("spot_confirms")],
        },
        {
            "variant_id": "funding_only",
            "description": "Funding alignment only.",
            "filters": ["funding_aligned"],
            "funcs": [filter_named("funding_aligned")],
        },
        {
            "variant_id": "oi_expansion_only",
            "description": "OI expansion only.",
            "filters": ["oi_expansion"],
            "funcs": [filter_named("oi_expansion")],
        },
        {
            "variant_id": "baseline_no_refined_filters",
            "description": "Base RANGE setup without refined filters.",
            "filters": [],
            "funcs": [],
        },
    ]


def apply_variant(signals: list[dict[str, Any]], config: Any, funcs: list[FilterFunc]) -> list[dict[str, Any]]:
    if not funcs:
        return list(signals)
    return [signal for signal in signals if all(func(config, signal) for func in funcs)]


def cost_expectancy(row: dict[str, Any], extra_bps: float) -> float:
    for item in row.get("cost_stress", []):
        if safe_float(item.get("extra_bps_per_side"), -999.0) == extra_bps:
            return safe_float(item.get("summary", {}).get("expectancy_r"), -999.0)
    return -999.0


def rank_key(row: dict[str, Any]) -> tuple[Any, ...]:
    verdict_rank = {
        "shadow_research_shape_passed": 3,
        "shadow_watchlist_only": 2,
        "shadow_reject_or_research_only": 1,
    }
    return (
        verdict_rank.get(str(row.get("verdict")), 0),
        safe_float(row.get("full", {}).get("summary", {}).get("expectancy_r"), -999.0),
        cost_expectancy(row, 10.0),
        safe_float(row.get("holdout", {}).get("summary", {}).get("expectancy_r"), -999.0),
        safe_float(row.get("segment_positive_ratio"), -999.0),
        safe_int(row.get("full", {}).get("summary", {}).get("trades"), 0),
    )


def evaluate_variant(
    *,
    base_config: Any,
    variant: dict[str, Any],
    bars: list[Any],
    base_signals: list[dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    config = replace(base_config, strategy_id=f"{base_config.strategy_id}__shadow_{variant['variant_id']}")
    filtered_signals = apply_variant(base_signals, config, variant["funcs"])
    end_index = len(bars) - config.max_hold_bars - 1
    holdout_start = round(end_index * (1.0 - args.holdout_fraction))
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
    for extra in [float(item.strip()) for item in args.cost_stress_extra_bps.split(",") if item.strip()]:
        trades = replay_signals(config, bars, filtered_signals, args.cost_bps_per_side + extra, not args.allow_overlap)
        stress_rows.append({"extra_bps_per_side": extra, "summary": summarize_trades(trades)})

    full_summary = summarize_trades(full_trades)
    holdout_summary = summarize_trades(holdout_trades)
    segments_positive = sum(1 for item in segment_rows if safe_float(item.get("summary", {}).get("expectancy_r"), -999.0) > 0)
    segment_ratio = segments_positive / len(segment_rows) if segment_rows else 0.0
    worst_segment = min((safe_float(item.get("summary", {}).get("expectancy_r"), -999.0) for item in segment_rows), default=-999.0)
    cost10 = next((item for item in stress_rows if safe_float(item.get("extra_bps_per_side"), -999.0) == 10.0), None)
    checks = {
        "min_full_trades": safe_int(full_summary.get("trades")) >= args.min_full_trades,
        "min_full_expectancy": safe_float(full_summary.get("expectancy_r"), -999.0) >= args.min_expectancy_r,
        "min_holdout_trades": safe_int(holdout_summary.get("trades")) >= args.min_holdout_trades,
        "min_holdout_expectancy": safe_float(holdout_summary.get("expectancy_r"), -999.0) >= args.min_expectancy_r,
        "segment_positive_ratio": segment_ratio >= args.min_segment_positive_ratio,
        "worst_segment_floor": worst_segment >= -abs(args.max_worst_segment_expectancy_r),
        "cost_stress_10bps_positive": safe_float(cost10.get("summary", {}).get("expectancy_r"), -999.0) > 0 if isinstance(cost10, dict) else False,
    }
    if all(checks.values()):
        verdict = "shadow_research_shape_passed"
    elif checks["min_holdout_trades"] and checks["min_holdout_expectancy"]:
        verdict = "shadow_watchlist_only"
    else:
        verdict = "shadow_reject_or_research_only"
    return {
        "variant_id": variant["variant_id"],
        "description": variant["description"],
        "base_strategy_id": base_config.strategy_id,
        "strategy_id": config.strategy_id,
        "filters": variant["filters"],
        "interval": config.interval,
        "side": config.side,
        "trigger": config.trigger,
        "rr": f"{config.stop_atr:g}:{config.take_atr:g}",
        "max_hold_bars": config.max_hold_bars,
        "signals": len(filtered_signals),
        "full": {"summary": full_summary, "stable_folds": sum(1 for item in fold_rows if item.get("stable")), "folds": fold_rows},
        "holdout": {"start_index": holdout_start, "signals": len(holdout_signals), "summary": holdout_summary},
        "segments": segment_rows,
        "segment_positive_ratio": round(segment_ratio, 6),
        "worst_segment_expectancy_r": round(worst_segment, 6),
        "cost_stress": stress_rows,
        "checks": checks,
        "verdict": verdict,
        "can_trade": False,
    }


def forward_counts(
    *,
    base_config: Any,
    variants: list[dict[str, Any]],
    cache_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    try:
        bars, features, rsi14 = load_interval_payload(cache_dir, base_config.interval, args.oi_lag, args.spot_perp_lookback)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc)}
    if not bars:
        return {"available": False, "error": "no_forward_bars"}
    start = max(0, len(bars) - args.forward_analyze_bars)
    end = len(bars)
    base_signals = generate_signals(base_config, bars, features, rsi14, start, end)
    rows = []
    for variant in variants:
        filtered = apply_variant(base_signals, base_config, variant["funcs"])
        latest_signal = filtered[-1] if filtered else None
        rows.append(
            {
                "variant_id": variant["variant_id"],
                "signals": len(filtered),
                "latest_signal_bar_index": latest_signal.get("bar_index") if isinstance(latest_signal, dict) else None,
                "latest_signal_bar_ts": str(bars[int(latest_signal["bar_index"])].ts) if isinstance(latest_signal, dict) else None,
            }
        )
    return {
        "available": True,
        "cache_dir": rel_path(cache_dir),
        "bars_loaded": len(bars),
        "bars_analyzed": end - start,
        "base_signals": len(base_signals),
        "variant_counts": rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    selected = report.get("selected_candidate") if isinstance(report.get("selected_candidate"), dict) else {}
    forward = report.get("forward_counts") if isinstance(report.get("forward_counts"), dict) else {}
    lines = [
        "# Range Refined Filter Shadow Ablation",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Research-only shadow ablation.",
        "- Does not change the selected observer candidate.",
        "- Does not update promotion gates.",
        "- Does not create paper-entry intents or send orders.",
        "",
        "## Decision",
        "",
        f"- Decision: `{report.get('decision')}`.",
        f"- Next action: `{report.get('next_action')}`.",
        f"- Can trade: `{report.get('can_trade')}`.",
        "",
        "## Selected Candidate",
        "",
        f"- Base: `{selected.get('base_strategy_id')}`.",
        f"- Current filter: `{selected.get('filter_mode')}` (`{'+'.join(selected.get('filters') or [])}`).",
        f"- TF / side / RR: `{selected.get('interval')}` / `{selected.get('side')}` / `{selected.get('rr')}`.",
        "",
        "## Historical Shadow Results",
        "",
        "| Verdict | Variant | Filters | Signals | Full Trades | Full Exp | Holdout Trades | Holdout Exp | Seg+ | Worst Seg | Cost +10 Exp |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report.get("results", []):
        full = item.get("full", {}).get("summary", {})
        holdout = item.get("holdout", {}).get("summary", {})
        cost10 = cost_expectancy(item, 10.0)
        lines.append(
            f"| `{item.get('verdict')}` | `{item.get('variant_id')}` | `{'+'.join(item.get('filters') or [])}` | "
            f"`{item.get('signals')}` | `{full.get('trades')}` | `{full.get('expectancy_r')}` | "
            f"`{holdout.get('trades')}` | `{holdout.get('expectancy_r')}` | "
            f"`{item.get('segment_positive_ratio')}` | `{item.get('worst_segment_expectancy_r')}` | `{cost10}` |"
        )
    lines.extend(["", "## Current Forward Cache Counts", ""])
    if forward.get("available"):
        lines.extend(
            [
                f"- Bars analyzed: `{forward.get('bars_analyzed')}`.",
                f"- Base signals: `{forward.get('base_signals')}`.",
                "",
                "| Variant | Signals | Latest Signal Bar |",
                "|---|---:|---|",
            ]
        )
        for row in forward.get("variant_counts", []):
            lines.append(f"| `{row.get('variant_id')}` | `{row.get('signals')}` | `{row.get('latest_signal_bar_ts')}` |")
    else:
        lines.append(f"- Forward count unavailable: `{forward.get('error')}`.")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- A shadow pass is not promotion. It only identifies variants worth retesting in the normal refiner/gate chain.",
            "- If a relaxed variant improves signal count but fails robustness/cost/segment gates, keep it blocked.",
            "- If a relaxed variant passes historical shape, the next step is a separate observer-only candidate, not paper/live execution.",
            "",
        ]
    )
    return "\n".join(lines)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "verdict",
        "variant_id",
        "filters",
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
            writer.writerow(
                {
                    "verdict": item.get("verdict"),
                    "variant_id": item.get("variant_id"),
                    "filters": "+".join(item.get("filters") or []),
                    "signals": item.get("signals"),
                    "full_trades": item.get("full", {}).get("summary", {}).get("trades"),
                    "full_expectancy_r": item.get("full", {}).get("summary", {}).get("expectancy_r"),
                    "holdout_trades": item.get("holdout", {}).get("summary", {}).get("trades"),
                    "holdout_expectancy_r": item.get("holdout", {}).get("summary", {}).get("expectancy_r"),
                    "segment_positive_ratio": item.get("segment_positive_ratio"),
                    "worst_segment_expectancy_r": item.get("worst_segment_expectancy_r"),
                    "cost10_expectancy_r": cost_expectancy(item, 10.0),
                }
            )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    refiner_path = resolve_path(args.refiner_report)
    source_path = resolve_path(args.source_range_report)
    cache_dir = resolve_path(args.cache_dir)
    forward_cache_dir = resolve_path(args.forward_cache_dir)
    refiner_report = read_json(refiner_path)
    source_report = read_json(source_path)
    selected = selected_candidate(refiner_report)
    base_config = build_config(selected, source_report)
    bars, features, rsi14 = load_interval_payload(cache_dir, base_config.interval, args.oi_lag, args.spot_perp_lookback)
    if not bars:
        raise ValueError(f"no_bars_loaded:{rel_path(cache_dir)}:{base_config.interval}")
    end_index = len(bars) - base_config.max_hold_bars - 1
    base_signals = generate_signals(base_config, bars, features, rsi14, 0, end_index)
    variants = make_variants()
    results = [evaluate_variant(base_config=base_config, variant=variant, bars=bars, base_signals=base_signals, args=args) for variant in variants]
    results.sort(key=rank_key, reverse=True)
    shape_passes = [item for item in results if item.get("verdict") == "shadow_research_shape_passed"]
    if shape_passes:
        decision = "shadow_ablation_candidates_found_research_only"
        next_action = "promote none; run the best shadow variant through an independent observer-only validation path"
    else:
        decision = "no_shadow_ablation_variant_passed_research_shape"
        next_action = "keep selected RANGE observer unchanged; continue forward observation or design new filters"
    return {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "range_refined_filter_shadow_ablation_research_only",
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
            "forward_cache_dir": rel_path(forward_cache_dir),
            "base_signals": len(base_signals),
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
        "selected_candidate": selected,
        "tested": len(results),
        "shadow_shape_pass_count": len(shape_passes),
        "best_variant": results[0] if results else None,
        "results": results,
        "forward_counts": forward_counts(base_config=base_config, variants=variants, cache_dir=forward_cache_dir, args=args),
        "decision": decision,
        "next_action": next_action,
        "can_trade": False,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Research-only ablation for selected refined RANGE filters")
    parser.add_argument("--refiner-report", default="docs/RANGE_WATCHLIST_REFINER_2026-06-16.json")
    parser.add_argument("--source-range-report", default="docs/RANGE_FAMILY_VALIDATOR_2026-06-16.json")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--forward-cache-dir", default="_dl/forward_paper_feed/cache")
    parser.add_argument("--oi-lag", type=int, default=12)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--forward-analyze-bars", type=int, default=320)
    parser.add_argument("--cost-bps-per-side", type=float, default=7.0)
    parser.add_argument("--cost-stress-extra-bps", default="0,5,10,20")
    parser.add_argument("--holdout-fraction", type=float, default=0.25)
    parser.add_argument("--folds", type=int, default=8)
    parser.add_argument("--segments", type=int, default=6)
    parser.add_argument("--min-full-trades", type=int, default=60)
    parser.add_argument("--min-holdout-trades", type=int, default=15)
    parser.add_argument("--min-expectancy-r", type=float, default=0.05)
    parser.add_argument("--min-segment-positive-ratio", type=float, default=0.66)
    parser.add_argument("--max-worst-segment-expectancy-r", type=float, default=0.25)
    parser.add_argument("--allow-overlap", action="store_true")
    parser.add_argument("--out-prefix", default="docs/RANGE_REFINED_FILTER_SHADOW_ABLATION_2026-06-17")
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    write_csv(out_prefix.with_suffix(".csv"), report["results"])
    best = report.get("best_variant") if isinstance(report.get("best_variant"), dict) else {}
    print(
        json.dumps(
            {
                "decision": report.get("decision"),
                "tested": report.get("tested"),
                "shadow_shape_pass_count": report.get("shadow_shape_pass_count"),
                "best_variant": best.get("variant_id"),
                "best_verdict": best.get("verdict"),
                "best_full_expectancy_r": best.get("full", {}).get("summary", {}).get("expectancy_r") if isinstance(best, dict) else None,
                "best_cost10_expectancy_r": cost_expectancy(best, 10.0) if isinstance(best, dict) else None,
                "json": rel_path(out_prefix.with_suffix(".json")),
                "md": rel_path(out_prefix.with_suffix(".md")),
                "csv": rel_path(out_prefix.with_suffix(".csv")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

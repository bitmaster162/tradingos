#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import itertools
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.strategy_mix_deep_validator import bar_segments, replay_trades, summarize_segment  # noqa: E402
from tools.strategy_mix_combo_tester import load_interval_data  # noqa: E402
from tools.strategy_mix_holdout_validator import ReplayConfig, result_to_config  # noqa: E402


LONG_GUARDS = (
    "breakout_up_40",
    "trend_up_20",
    "body_accept",
    "volume_hot",
    "spot_confirms_long",
    "oi_up",
    "funding_compressed",
    "funding_negative",
)
SHORT_GUARDS = (
    "breakout_down_40",
    "trend_down_20",
    "body_accept",
    "volume_hot",
    "spot_confirms_short",
    "oi_up",
    "funding_compressed",
    "funding_positive",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_float(value: Any, default: float = -999.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def source_items(source: dict[str, Any], verdicts: set[str], top: int) -> list[dict[str, Any]]:
    rows = []
    for item in source.get("results", []):
        verdict = item.get("deep_gate", {}).get("verdict") or item.get("verdict")
        if verdict in verdicts:
            rows.append(item)
    if not rows:
        rows = source.get("results", [])
    rows.sort(
        key=lambda item: (
            safe_float(item.get("holdout", {}).get("summary", {}).get("expectancy_r")),
            safe_float(item.get("full", {}).get("summary", {}).get("expectancy_r")),
        ),
        reverse=True,
    )
    return rows[: max(1, top)]


def guard_sets(config: ReplayConfig, max_guard_size: int, include_base: bool) -> list[tuple[str, ...]]:
    base = set(config.conditions)
    pool = LONG_GUARDS if config.side.upper() == "LONG" else SHORT_GUARDS
    pool = tuple(item for item in pool if item not in base)
    output: list[tuple[str, ...]] = [tuple()] if include_base else []
    for size in range(1, max_guard_size + 1):
        output.extend(tuple(combo) for combo in itertools.combinations(pool, size))
    return output


def guarded_config(base: ReplayConfig, guards: tuple[str, ...]) -> ReplayConfig:
    conditions = tuple(dict.fromkeys((*base.conditions, *guards)))
    suffix = "base" if not guards else "guard_" + "_".join(guards)
    return replace(base, strategy_id=f"{base.strategy_id}__{suffix}", conditions=conditions)


def summarize_no_trades(
    config: ReplayConfig,
    *,
    bars: list[Any],
    features: list[dict[str, Any]],
    matrix: dict[str, list[bool]],
    start_index: int,
    end_index: int,
    cost_bps_per_side: float,
    no_overlap: bool,
    folds: int,
) -> dict[str, Any]:
    segment = summarize_segment(
        config,
        bars=bars,
        features=features,
        matrix=matrix,
        start_index=start_index,
        end_index=end_index,
        cost_bps_per_side=cost_bps_per_side,
        no_overlap=no_overlap,
        folds=folds,
    )
    segment.pop("trades", None)
    return segment


def evaluate_guard(
    config: ReplayConfig,
    *,
    base_strategy_id: str,
    guards: tuple[str, ...],
    bars: list[Any],
    features: list[dict[str, Any]],
    matrix: dict[str, list[bool]],
    holdout_fraction: float,
    segments_count: int,
    base_cost: float,
    stress_extra_bps: float,
    no_overlap: bool,
    folds: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    end_index = len(bars) - config.max_hold_bars - 1
    split = max(1, min(len(bars) - 2, int(len(bars) * (1.0 - holdout_fraction))))
    full = summarize_no_trades(
        config,
        bars=bars,
        features=features,
        matrix=matrix,
        start_index=0,
        end_index=end_index,
        cost_bps_per_side=base_cost,
        no_overlap=no_overlap,
        folds=folds,
    )
    holdout = summarize_no_trades(
        config,
        bars=bars,
        features=features,
        matrix=matrix,
        start_index=split,
        end_index=end_index,
        cost_bps_per_side=base_cost,
        no_overlap=no_overlap,
        folds=max(2, min(4, folds)),
    )
    full_cost_stress = summarize_no_trades(
        config,
        bars=bars,
        features=features,
        matrix=matrix,
        start_index=0,
        end_index=end_index,
        cost_bps_per_side=base_cost + stress_extra_bps,
        no_overlap=no_overlap,
        folds=folds,
    )
    holdout_cost_stress = summarize_no_trades(
        config,
        bars=bars,
        features=features,
        matrix=matrix,
        start_index=split,
        end_index=end_index,
        cost_bps_per_side=base_cost + stress_extra_bps,
        no_overlap=no_overlap,
        folds=max(2, min(4, folds)),
    )
    segment_rows = []
    for start, end in bar_segments(len(bars), config.max_hold_bars, segments_count):
        segment_rows.append(
            summarize_no_trades(
                config,
                bars=bars,
                features=features,
                matrix=matrix,
                start_index=start,
                end_index=end,
                cost_bps_per_side=base_cost,
                no_overlap=no_overlap,
                folds=2,
            )
        )
    segment_summaries = [item["summary"] for item in segment_rows]
    segment_positive = sum(1 for item in segment_summaries if safe_float(item.get("expectancy_r")) > 0)
    segment_ratio = segment_positive / len(segment_summaries) if segment_summaries else 0.0
    worst_segment_exp = min((safe_float(item.get("expectancy_r")) for item in segment_summaries), default=-999.0)
    full_summary = full["summary"]
    holdout_summary = holdout["summary"]
    full_stress_summary = full_cost_stress["summary"]
    holdout_stress_summary = holdout_cost_stress["summary"]
    checks = {
        "min_full_trades": safe_int(full_summary.get("trades")) >= args.min_full_trades,
        "min_holdout_trades": safe_int(holdout_summary.get("trades")) >= args.min_holdout_trades,
        "full_expectancy": safe_float(full_summary.get("expectancy_r")) >= args.min_expectancy,
        "holdout_expectancy": safe_float(holdout_summary.get("expectancy_r")) >= args.min_expectancy,
        "full_cost_stress_positive": safe_float(full_stress_summary.get("expectancy_r")) > 0,
        "holdout_cost_stress_positive": safe_float(holdout_stress_summary.get("expectancy_r")) > 0,
        "segment_positive_ratio": segment_ratio >= args.min_segment_positive_ratio,
        "worst_segment_floor": worst_segment_exp >= -abs(args.max_worst_segment_expectancy),
    }
    if all(checks.values()):
        verdict = "guard_candidate_needs_deep"
    elif checks["min_holdout_trades"] and checks["holdout_expectancy"] and checks["holdout_cost_stress_positive"]:
        verdict = "guard_watchlist_positive"
    else:
        verdict = "reject_guard"
    score = (
        safe_float(holdout_summary.get("expectancy_r")) * 3.0
        + safe_float(full_summary.get("expectancy_r")) * 1.5
        + safe_float(holdout_stress_summary.get("expectancy_r")) * 2.0
        + segment_ratio
        - max(0, abs(worst_segment_exp) - abs(args.max_worst_segment_expectancy))
    )
    return {
        "strategy_id": config.strategy_id,
        "base_strategy_id": base_strategy_id,
        "interval": config.interval,
        "side": config.side,
        "guards": list(guards),
        "conditions": list(config.conditions),
        "rr": f"{config.stop_atr:g}:{config.take_atr:g}",
        "max_hold_bars": config.max_hold_bars,
        "verdict": verdict,
        "score": round(score, 6),
        "checks": checks,
        "full": full,
        "holdout": holdout,
        "full_cost_stress": full_cost_stress,
        "holdout_cost_stress": holdout_cost_stress,
        "segments_positive": segment_positive,
        "segments_total": len(segment_summaries),
        "segment_positive_ratio": round(segment_ratio, 6),
        "worst_segment_expectancy_r": round(worst_segment_exp, 6),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Strategy Mix Guard Optimizer",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Research-only guard-layer optimizer for 4H breakout watchlist strategies.",
        "- No orders, no private credentials, no paper/live permission.",
        "- A guard must improve robustness, not just reduce trades until the curve looks better.",
        "",
        "## Summary",
        "",
        f"- Source: `{report['source_report']}`.",
        f"- Tested variants: `{report['tested']}`.",
        f"- Guard candidates needing deep validation: `{report['candidate_count']}`.",
        f"- Guard watchlist positives: `{report['watchlist_count']}`.",
        f"- Decision: `{report['decision']}`.",
        f"- Trade export: `{report['trade_export']}`.",
        "",
        "## Top Results",
        "",
        "| Verdict | Base | Guards | TF | Side | RR | Full Trades | Full Exp | Holdout Trades | Holdout Exp | Holdout Stress Exp | Seg+ | Worst Seg Exp | Score |",
        "|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report["top_results"]:
        full = item["full"]["summary"]
        holdout = item["holdout"]["summary"]
        stress = item["holdout_cost_stress"]["summary"]
        guards = "+".join(item["guards"]) if item["guards"] else "base"
        lines.append(
            f"| `{item['verdict']}` | `{item['base_strategy_id']}` | `{guards}` | `{item['interval']}` | `{item['side']}` | `{item['rr']}` | "
            f"`{full['trades']}` | `{full['expectancy_r']}` | `{holdout['trades']}` | `{holdout['expectancy_r']}` | "
            f"`{stress['expectancy_r']}` | `{item['segments_positive']}/{item['segments_total']}` | `{item['worst_segment_expectancy_r']}` | `{item['score']}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- If the top guard candidate still has too few holdout trades, it is not deployable; it is only a narrower hypothesis.",
            "- If it passes cost stress but loses segment stability, the next step is regime gating rather than entry tuning.",
            "- If it passes this guard optimizer, rerun `strategy_mix_deep_validator.py` on the guarded candidate before any paper replay.",
            "",
            "## Next Action",
            "",
            f"- `{report['next_action']}`.",
            "",
        ]
    )
    return "\n".join(lines)


def write_trade_export(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "strategy_id",
        "base_strategy_id",
        "guards",
        "interval",
        "side",
        "rr",
        "max_hold_bars",
        "entry_ts",
        "exit_ts",
        "entry",
        "exit",
        "stop",
        "take",
        "atr",
        "r_net",
        "exit_reason",
        "bars_held",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Research-only guard optimizer for strategy mix candidates")
    parser.add_argument("--source-report", default="docs/STRATEGY_MIX_DEEP_VALIDATION_2026-06-08.json")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--candidate-verdicts", default="deep_watchlist_positive,paper_replay_candidate_locked")
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--max-guard-size", type=int, default=2)
    parser.add_argument("--holdout-fraction", type=float, default=0.25)
    parser.add_argument("--segments", type=int, default=6)
    parser.add_argument("--folds", type=int, default=6)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--stress-extra-bps", type=float, default=10.0)
    parser.add_argument("--min-full-trades", type=int, default=60)
    parser.add_argument("--min-holdout-trades", type=int, default=15)
    parser.add_argument("--min-expectancy", type=float, default=0.05)
    parser.add_argument("--min-segment-positive-ratio", type=float, default=0.50)
    parser.add_argument("--max-worst-segment-expectancy", type=float, default=0.35)
    parser.add_argument("--include-base", action="store_true")
    parser.add_argument("--allow-overlap", action="store_true")
    parser.add_argument("--out-prefix", default="docs/STRATEGY_MIX_GUARD_OPTIMIZER_2026-06-08")
    args = parser.parse_args()

    source_path = Path(args.source_report)
    source = json.loads(source_path.read_text(encoding="utf-8-sig"))
    verdicts = {item.strip() for item in args.candidate_verdicts.split(",") if item.strip()}
    base_items = source_items(source, verdicts, args.top)
    interval_cache: dict[str, tuple[list[Any], list[dict[str, Any]], dict[str, list[bool]]]] = {}
    base_cost = args.fee_bps + args.slippage_bps
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for item in base_items:
        base = result_to_config(item)
        if base.interval not in interval_cache:
            interval_cache[base.interval] = load_interval_data(Path(args.cache_dir), base.interval, oi_lag=12, spot_perp_lookback=12)
        bars, features, matrix = interval_cache[base.interval]
        for guards in guard_sets(base, args.max_guard_size, args.include_base):
            config = guarded_config(base, guards)
            try:
                results.append(
                    evaluate_guard(
                        config,
                        base_strategy_id=base.strategy_id,
                        guards=guards,
                        bars=bars,
                        features=features,
                        matrix=matrix,
                        holdout_fraction=args.holdout_fraction,
                        segments_count=args.segments,
                        base_cost=base_cost,
                        stress_extra_bps=args.stress_extra_bps,
                        no_overlap=not args.allow_overlap,
                        folds=args.folds,
                        args=args,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                errors.append({"strategy_id": config.strategy_id, "error": str(exc)})

    results.sort(
        key=lambda item: (
            item["verdict"] == "guard_candidate_needs_deep",
            item["verdict"] == "guard_watchlist_positive",
            item["score"],
            safe_int(item["holdout"]["summary"].get("trades")),
        ),
        reverse=True,
    )
    candidate_count = sum(1 for item in results if item["verdict"] == "guard_candidate_needs_deep")
    watchlist_count = sum(1 for item in results if item["verdict"] == "guard_watchlist_positive")
    trade_export = Path(args.out_prefix).with_name(Path(args.out_prefix).name + "_top_trades.csv")
    export_rows: list[dict[str, Any]] = []
    for item in results[:10]:
        base = ReplayConfig(
            strategy_id=item["strategy_id"],
            interval=item["interval"],
            side=item["side"],
            conditions=tuple(item["conditions"]),
            stop_atr=float(item["rr"].split(":", 1)[0]),
            take_atr=float(item["rr"].split(":", 1)[1]),
            max_hold_bars=int(item["max_hold_bars"]),
        )
        bars, features, matrix = interval_cache[base.interval]
        _, trades = replay_trades(
            base,
            bars=bars,
            features=features,
            matrix=matrix,
            start_index=0,
            end_index=len(bars) - base.max_hold_bars - 1,
            cost_bps_per_side=base_cost,
            no_overlap=not args.allow_overlap,
        )
        for trade in trades:
            row = trade.__dict__.copy()
            row.update(
                {
                    "strategy_id": item["strategy_id"],
                    "base_strategy_id": item["base_strategy_id"],
                    "guards": "+".join(item["guards"]) if item["guards"] else "base",
                    "interval": item["interval"],
                    "side": item["side"],
                    "rr": item["rr"],
                    "max_hold_bars": item["max_hold_bars"],
                }
            )
            export_rows.append(row)
    write_trade_export(trade_export, export_rows)

    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "research_guard_optimizer_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "settings": {
            "source_report": str(source_path),
            "cache_dir": args.cache_dir,
            "top": args.top,
            "max_guard_size": args.max_guard_size,
            "holdout_fraction": args.holdout_fraction,
            "segments": args.segments,
            "base_cost_bps_per_side": base_cost,
            "stress_extra_bps_per_side": args.stress_extra_bps,
            "min_full_trades": args.min_full_trades,
            "min_holdout_trades": args.min_holdout_trades,
            "no_overlap": not args.allow_overlap,
        },
        "source_report": str(source_path),
        "tested": len(results),
        "errors": errors[:50],
        "candidate_count": candidate_count,
        "watchlist_count": watchlist_count,
        "decision": "do_not_trade",
        "next_action": "deep_validate_guard_candidates" if candidate_count else "add_regime_guard_or_collect_more_data",
        "trade_export": str(trade_export),
        "top_results": results[:40],
        "all_results": results,
        "can_trade": False,
    }
    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(out_prefix.with_suffix(".json")),
                "md": str(out_prefix.with_suffix(".md")),
                "trades": str(trade_export),
                "tested": len(results),
                "candidate_count": candidate_count,
                "watchlist_count": watchlist_count,
                "best": results[0] if results else None,
                "decision": "do_not_trade",
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

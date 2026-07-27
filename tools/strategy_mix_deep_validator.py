#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_hardening import fold_summaries, simulate_trade, summarize_trades  # noqa: E402
from tools.strategy_mix_combo_tester import generate_signals, load_interval_data, stable_fold_count  # noqa: E402
from tools.strategy_mix_holdout_validator import ReplayConfig, result_to_config  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_float_list(value: str) -> list[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def parse_int_list(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


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


def signal_config(config: ReplayConfig) -> Any:
    return type(
        "SignalConfig",
        (),
        {
            "conditions": config.conditions,
            "side": config.side,
            "strategy_id": config.strategy_id,
            "interval": config.interval,
            "stop_atr": config.stop_atr,
            "take_atr": config.take_atr,
            "max_hold_bars": config.max_hold_bars,
        },
    )()


def replay_trades(
    config: ReplayConfig,
    *,
    bars: list[Any],
    features: list[dict[str, Any]],
    matrix: dict[str, list[bool]],
    start_index: int,
    end_index: int,
    cost_bps_per_side: float,
    no_overlap: bool,
) -> tuple[list[dict[str, Any]], list[Any]]:
    all_signals = generate_signals(signal_config(config), bars, features, matrix)
    signals = [item for item in all_signals if start_index <= int(item["bar_index"]) < end_index]
    trades = []
    last_exit_bar = -1
    for signal in sorted(signals, key=lambda item: int(item["bar_index"])):
        signal_index = int(signal["bar_index"])
        if no_overlap and signal_index <= last_exit_bar:
            continue
        trade = simulate_trade(
            dataset_id=f"mix_deep_BTCUSDT_{config.interval}",
            strategy_id=config.strategy_id,
            bars=bars,
            signal=signal,
            stop_atr=config.stop_atr,
            take_atr=config.take_atr,
            max_hold_bars=config.max_hold_bars,
            cost_bps_per_side=cost_bps_per_side,
        )
        if trade is None:
            continue
        trades.append(trade)
        if no_overlap:
            for offset in range(signal_index + 1, min(len(bars), signal_index + config.max_hold_bars + 2)):
                if bars[offset].ts == trade.exit_ts:
                    last_exit_bar = offset
                    break
    return signals, trades


def summarize_segment(
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
    signals, trades = replay_trades(
        config,
        bars=bars,
        features=features,
        matrix=matrix,
        start_index=start_index,
        end_index=end_index,
        cost_bps_per_side=cost_bps_per_side,
        no_overlap=no_overlap,
    )
    fold_rows = fold_summaries(trades, folds)
    return {
        "start_index": start_index,
        "end_index": end_index,
        "signals": len(signals),
        "summary": summarize_trades(trades),
        "stable_folds": stable_fold_count(fold_rows),
        "folds": fold_rows,
        "trades": trades,
    }


def bootstrap_positive_probability(r_values: list[float], *, iterations: int, seed: int) -> float | None:
    if not r_values:
        return None
    rng = random.Random(seed)
    positive = 0
    size = len(r_values)
    for _ in range(iterations):
        total = 0.0
        for _ in range(size):
            total += rng.choice(r_values)
        if total / size > 0:
            positive += 1
    return round(positive / iterations, 6)


def bar_segments(total_bars: int, max_hold_bars: int, segment_count: int) -> list[tuple[int, int]]:
    usable_end = max(1, total_bars - max_hold_bars - 1)
    segments: list[tuple[int, int]] = []
    for segment in range(segment_count):
        start = round(usable_end * segment / segment_count)
        end = round(usable_end * (segment + 1) / segment_count)
        if end > start:
            segments.append((start, end))
    return segments


def cost_stress(
    config: ReplayConfig,
    *,
    bars: list[Any],
    features: list[dict[str, Any]],
    matrix: dict[str, list[bool]],
    base_cost_bps_per_side: float,
    stress_bps: list[float],
    no_overlap: bool,
) -> list[dict[str, Any]]:
    output = []
    end_index = len(bars) - config.max_hold_bars - 1
    for extra_bps in stress_bps:
        _, trades = replay_trades(
            config,
            bars=bars,
            features=features,
            matrix=matrix,
            start_index=0,
            end_index=end_index,
            cost_bps_per_side=base_cost_bps_per_side + extra_bps,
            no_overlap=no_overlap,
        )
        output.append(
            {
                "extra_bps_per_side": extra_bps,
                "total_bps_per_side": base_cost_bps_per_side + extra_bps,
                "summary": summarize_trades(trades),
            }
        )
    return output


def perturbation_grid(config: ReplayConfig, hold_offsets: list[int]) -> list[ReplayConfig]:
    stop_values = sorted({round(max(0.5, config.stop_atr * factor), 4) for factor in (0.85, 1.0, 1.15)})
    take_values = sorted({round(max(0.5, config.take_atr * factor), 4) for factor in (0.85, 1.0, 1.15)})
    hold_values = sorted({max(4, config.max_hold_bars + offset) for offset in hold_offsets})
    variants: list[ReplayConfig] = []
    for stop in stop_values:
        for take in take_values:
            for hold in hold_values:
                strategy_id = f"{config.strategy_id}__perturb_s{stop:g}_t{take:g}_h{hold}"
                variants.append(
                    replace(
                        config,
                        strategy_id=strategy_id,
                        stop_atr=stop,
                        take_atr=take,
                        max_hold_bars=hold,
                    )
                )
    return variants


def perturbation_report(
    config: ReplayConfig,
    *,
    bars: list[Any],
    features: list[dict[str, Any]],
    matrix: dict[str, list[bool]],
    cost_bps_per_side: float,
    no_overlap: bool,
    hold_offsets: list[int],
) -> dict[str, Any]:
    rows = []
    for variant in perturbation_grid(config, hold_offsets):
        end_index = len(bars) - variant.max_hold_bars - 1
        _, trades = replay_trades(
            variant,
            bars=bars,
            features=features,
            matrix=matrix,
            start_index=0,
            end_index=end_index,
            cost_bps_per_side=cost_bps_per_side,
            no_overlap=no_overlap,
        )
        summary = summarize_trades(trades)
        rows.append(
            {
                "stop_atr": variant.stop_atr,
                "take_atr": variant.take_atr,
                "max_hold_bars": variant.max_hold_bars,
                "summary": summary,
            }
        )
    exp_values = [safe_float(item["summary"].get("expectancy_r")) for item in rows if item["summary"].get("expectancy_r") is not None]
    positive_rows = [item for item in rows if safe_float(item["summary"].get("expectancy_r")) > 0]
    exp_values_sorted = sorted(exp_values)
    median_exp = exp_values_sorted[len(exp_values_sorted) // 2] if exp_values_sorted else None
    return {
        "tested": len(rows),
        "positive": len(positive_rows),
        "positive_ratio": round(len(positive_rows) / len(rows), 6) if rows else None,
        "median_expectancy_r": round(median_exp, 6) if median_exp is not None else None,
        "best": max(rows, key=lambda item: safe_float(item["summary"].get("expectancy_r")), default=None),
        "worst": min(rows, key=lambda item: safe_float(item["summary"].get("expectancy_r")), default=None),
        "rows": rows,
    }


def deep_verdict(item: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    full_summary = item["full"]["summary"]
    holdout_summary = item["holdout"]["summary"]
    segment_summaries = [segment["summary"] for segment in item["segments"]]
    cost_rows = item["cost_stress"]
    perturb = item["perturbation"]
    full_exp = safe_float(full_summary.get("expectancy_r"))
    holdout_exp = safe_float(holdout_summary.get("expectancy_r"))
    segments_positive = sum(1 for summary in segment_summaries if safe_float(summary.get("expectancy_r")) > 0)
    segment_ratio = segments_positive / len(segment_summaries) if segment_summaries else 0.0
    worst_segment_exp = min((safe_float(summary.get("expectancy_r")) for summary in segment_summaries), default=-999.0)
    stress_at_or_above_10 = [row for row in cost_rows if safe_float(row["extra_bps_per_side"], 0.0) >= 10.0]
    stress_ok = all(safe_float(row["summary"].get("expectancy_r")) > 0 for row in stress_at_or_above_10) if stress_at_or_above_10 else False
    checks = {
        "min_full_trades": safe_int(full_summary.get("trades")) >= args.min_full_trades,
        "min_full_expectancy": full_exp >= args.min_expectancy,
        "min_holdout_trades": safe_int(holdout_summary.get("trades")) >= args.min_holdout_trades,
        "min_holdout_expectancy": holdout_exp >= args.min_expectancy,
        "bootstrap_positive": safe_float(item.get("bootstrap_p_positive"), 0.0) >= args.min_bootstrap_p,
        "segment_positive_ratio": segment_ratio >= args.min_segment_positive_ratio,
        "worst_segment_floor": worst_segment_exp >= -abs(args.max_worst_segment_expectancy),
        "cost_stress_10bps_positive": stress_ok,
        "perturbation_positive_ratio": safe_float(perturb.get("positive_ratio"), 0.0) >= args.min_perturbation_positive_ratio,
        "perturbation_median_positive": safe_float(perturb.get("median_expectancy_r")) > 0,
    }
    if all(checks.values()):
        verdict = "paper_replay_candidate_locked"
    elif checks["min_holdout_trades"] and checks["min_holdout_expectancy"] and checks["bootstrap_positive"]:
        verdict = "deep_watchlist_positive"
    else:
        verdict = "reject_or_redesign"
    return {
        "verdict": verdict,
        "checks": checks,
        "diagnostics": {
            "segments_positive": segments_positive,
            "segment_count": len(segment_summaries),
            "segment_positive_ratio": round(segment_ratio, 6),
            "worst_segment_expectancy_r": round(worst_segment_exp, 6),
        },
    }


def select_source_items(source: dict[str, Any], verdicts: set[str], top: int) -> list[dict[str, Any]]:
    source_rows = source.get("results") or source.get("all_results") or source.get("top_results") or []
    rows = [item for item in source_rows if item.get("verdict") in verdicts]
    if not rows:
        rows = [item for item in source_rows if "holdout" in item]
    rows.sort(
        key=lambda item: (
            item.get("verdict") == "holdout_watchlist_positive",
            safe_float(item.get("holdout", {}).get("summary", {}).get("expectancy_r")),
            safe_int(item.get("holdout", {}).get("summary", {}).get("trades")),
        ),
        reverse=True,
    )
    return rows[: max(1, top)]


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Strategy Mix Deep Validation",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Research-only robustness validation for mix-combo watchlist leads.",
        "- No private credentials, no order sending, no paper/live permission.",
        "- `paper_replay_candidate_locked` means the next step is a paper-only replay harness, not live trading.",
        "",
        "## Why 1:3 Can Work Without 70% Winrate",
        "",
        "- RR `1:3` has a theoretical breakeven near `25%` before costs.",
        "- Real tests are stricter because time exits, fees and slippage reduce realized average win.",
        "- Therefore the gate checks expectancy, sample, segments, holdout and cost stress instead of winrate alone.",
        "",
        "## Run Summary",
        "",
        f"- Source: `{report['source_report']}`.",
        f"- Tested: `{report['tested']}`.",
        f"- Deep watchlist positive: `{report['deep_watchlist_positive']}`.",
        f"- Paper replay locked candidates: `{report['paper_replay_candidate_locked']}`.",
        f"- Decision: `{report['decision']}`.",
        f"- Trade export: `{report['trade_export']}`.",
        "",
        "## Results",
        "",
        "| Verdict | Strategy | TF | Side | RR | Full Trades | Full Exp | Holdout Trades | Holdout Exp | Seg+ | Worst Seg Exp | Cost +10 Exp | Perturb + Ratio | Bootstrap P>0 |",
        "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report["results"]:
        fs = item["full"]["summary"]
        hs = item["holdout"]["summary"]
        diag = item["deep_gate"]["diagnostics"]
        stress_10 = next((row for row in item["cost_stress"] if row["extra_bps_per_side"] >= 10.0), None)
        stress_10_exp = stress_10["summary"]["expectancy_r"] if stress_10 else None
        lines.append(
            f"| `{item['deep_gate']['verdict']}` | `{item['strategy_id']}` | `{item['interval']}` | `{item['side']}` | `{item['rr']}` | "
            f"`{fs['trades']}` | `{fs['expectancy_r']}` | `{hs['trades']}` | `{hs['expectancy_r']}` | "
            f"`{diag['segments_positive']}/{diag['segment_count']}` | `{diag['worst_segment_expectancy_r']}` | "
            f"`{stress_10_exp}` | `{item['perturbation']['positive_ratio']}` | `{item['bootstrap_p_positive']}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- If a candidate fails cost stress or parameter perturbation, the edge is probably too fragile for execution.",
            "- If it fails segment stability, the edge is probably regime-specific and must be guarded or rejected.",
            "- If it survives all checks, the next bounded step is a paper-only replay module with explicit kill switches.",
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
        "interval",
        "side",
        "conditions",
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
    parser = argparse.ArgumentParser(description="Deep robustness validator for holdout-positive strategy mix candidates")
    parser.add_argument("--source-report", default="docs/STRATEGY_MIX_HOLDOUT_VALIDATION_2026-06-08.json")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--candidate-verdicts", default="holdout_watchlist_positive")
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--segments", type=int, default=6)
    parser.add_argument("--folds", type=int, default=6)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--stress-bps", default="0,5,10,15")
    parser.add_argument("--hold-offsets", default="-4,0,4")
    parser.add_argument("--bootstrap-iterations", type=int, default=1000)
    parser.add_argument("--bootstrap-seed", type=int, default=1337)
    parser.add_argument("--min-full-trades", type=int, default=80)
    parser.add_argument("--min-holdout-trades", type=int, default=20)
    parser.add_argument("--min-expectancy", type=float, default=0.05)
    parser.add_argument("--min-bootstrap-p", type=float, default=0.80)
    parser.add_argument("--min-segment-positive-ratio", type=float, default=0.50)
    parser.add_argument("--max-worst-segment-expectancy", type=float, default=0.35)
    parser.add_argument("--min-perturbation-positive-ratio", type=float, default=0.45)
    parser.add_argument("--allow-overlap", action="store_true")
    parser.add_argument("--out-prefix", default="docs/STRATEGY_MIX_DEEP_VALIDATION_2026-06-08")
    args = parser.parse_args()

    source_path = Path(args.source_report)
    source = json.loads(source_path.read_text(encoding="utf-8-sig"))
    verdicts = {item.strip() for item in args.candidate_verdicts.split(",") if item.strip()}
    source_items = select_source_items(source, verdicts, args.top)
    stress_bps = parse_float_list(args.stress_bps)
    hold_offsets = parse_int_list(args.hold_offsets)
    base_cost = args.fee_bps + args.slippage_bps
    interval_cache: dict[str, tuple[list[Any], list[dict[str, Any]], dict[str, list[bool]]]] = {}
    results: list[dict[str, Any]] = []
    export_rows: list[dict[str, Any]] = []

    for source_item in source_items:
        config = result_to_config(source_item)
        if config.interval not in interval_cache:
            interval_cache[config.interval] = load_interval_data(Path(args.cache_dir), config.interval, oi_lag=12, spot_perp_lookback=12)
        bars, features, matrix = interval_cache[config.interval]
        full = summarize_segment(
            config,
            bars=bars,
            features=features,
            matrix=matrix,
            start_index=0,
            end_index=len(bars) - config.max_hold_bars - 1,
            cost_bps_per_side=base_cost,
            no_overlap=not args.allow_overlap,
            folds=args.folds,
        )
        holdout = source_item.get("holdout", {})
        segments = []
        for start, end in bar_segments(len(bars), config.max_hold_bars, args.segments):
            segment = summarize_segment(
                config,
                bars=bars,
                features=features,
                matrix=matrix,
                start_index=start,
                end_index=end,
                cost_bps_per_side=base_cost,
                no_overlap=not args.allow_overlap,
                folds=2,
            )
            segment.pop("trades", None)
            segments.append(segment)
        stress = cost_stress(
            config,
            bars=bars,
            features=features,
            matrix=matrix,
            base_cost_bps_per_side=base_cost,
            stress_bps=stress_bps,
            no_overlap=not args.allow_overlap,
        )
        perturb = perturbation_report(
            config,
            bars=bars,
            features=features,
            matrix=matrix,
            cost_bps_per_side=base_cost,
            no_overlap=not args.allow_overlap,
            hold_offsets=hold_offsets,
        )
        r_values = [trade.r_net for trade in full["trades"]]
        result = {
            "strategy_id": config.strategy_id,
            "interval": config.interval,
            "side": config.side,
            "conditions": list(config.conditions),
            "rr": f"{config.stop_atr:g}:{config.take_atr:g}",
            "max_hold_bars": config.max_hold_bars,
            "source_verdict": source_item.get("verdict"),
            "full": {key: value for key, value in full.items() if key != "trades"},
            "holdout": holdout,
            "segments": segments,
            "cost_stress": stress,
            "perturbation": perturb,
            "bootstrap_p_positive": bootstrap_positive_probability(
                r_values,
                iterations=max(100, args.bootstrap_iterations),
                seed=args.bootstrap_seed,
            ),
        }
        result["deep_gate"] = deep_verdict(result, args)
        results.append(result)
        for trade in full["trades"]:
            trade_row = trade.__dict__.copy()
            trade_row.update(
                {
                    "strategy_id": config.strategy_id,
                    "interval": config.interval,
                    "side": config.side,
                    "conditions": "+".join(config.conditions),
                    "rr": f"{config.stop_atr:g}:{config.take_atr:g}",
                    "max_hold_bars": config.max_hold_bars,
                }
            )
            export_rows.append(trade_row)

    results.sort(
        key=lambda item: (
            item["deep_gate"]["verdict"] == "paper_replay_candidate_locked",
            item["deep_gate"]["verdict"] == "deep_watchlist_positive",
            safe_float(item["holdout"].get("summary", {}).get("expectancy_r")),
            safe_float(item["full"]["summary"].get("expectancy_r")),
        ),
        reverse=True,
    )
    paper_locked = sum(1 for item in results if item["deep_gate"]["verdict"] == "paper_replay_candidate_locked")
    deep_positive = sum(1 for item in results if item["deep_gate"]["verdict"] == "deep_watchlist_positive")
    out_prefix = Path(args.out_prefix)
    trade_export = out_prefix.with_name(out_prefix.name + "_trades.csv")
    write_trade_export(trade_export, export_rows)
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "research_deep_validation_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "settings": {
            "source_report": str(source_path),
            "cache_dir": args.cache_dir,
            "top": args.top,
            "segments": args.segments,
            "folds": args.folds,
            "base_cost_bps_per_side": base_cost,
            "stress_bps_per_side": stress_bps,
            "hold_offsets": hold_offsets,
            "no_overlap": not args.allow_overlap,
        },
        "source_report": str(source_path),
        "tested": len(results),
        "deep_watchlist_positive": deep_positive,
        "paper_replay_candidate_locked": paper_locked,
        "decision": "do_not_trade",
        "next_action": "build_paper_replay_for_locked_candidates" if paper_locked else "collect_more_data_or_add_regime_guard_before_paper",
        "trade_export": str(trade_export),
        "results": results,
        "can_trade": False,
    }
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
                "deep_watchlist_positive": deep_positive,
                "paper_replay_candidate_locked": paper_locked,
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

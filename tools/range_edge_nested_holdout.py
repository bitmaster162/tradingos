#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_hardening import fold_summaries, summarize_trades  # noqa: E402
from tools.range_family_validator import (  # noqa: E402
    RangeConfig,
    build_configs,
    generate_signals,
    load_interval_payload,
    replay_signals,
    safe_float,
    safe_int,
)
from tools.range_watchlist_refiner import apply_filter_mode, make_filters  # noqa: E402


LANES = {
    "RANGE_REFINED_4H": {
        "description": "slower range mean-reversion, 12-16 bar holding window",
        "holds": {12, 16},
    },
    "EDGE_FORWARD_4H": {
        "description": "fast range edge, 8 bar holding window",
        "holds": {8},
    },
}


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


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def find_split_index(bars: list[Any], split_ts: str) -> int:
    boundary = parse_ts(split_ts)
    for index, bar in enumerate(bars):
        if parse_ts(str(bar.ts)) >= boundary:
            return index
    raise ValueError(f"split timestamp is after available data: {split_ts}")


def window_signals(
    config: RangeConfig,
    bars: list[Any],
    features: list[dict[str, Any]],
    rsi14: list[float | None],
    *,
    start_index: int,
    end_index: int,
) -> list[dict[str, Any]]:
    safe_end = min(end_index, len(bars) - config.max_hold_bars - 1)
    return generate_signals(config, bars, features, rsi14, start_index, safe_end)


def summarize_window(
    config: RangeConfig,
    bars: list[Any],
    signals: list[dict[str, Any]],
    *,
    cost_bps_per_side: float,
    cost_stress_extra_bps: float,
    folds: int,
) -> dict[str, Any]:
    trades = replay_signals(config, bars, signals, cost_bps_per_side, True)
    stress_trades = replay_signals(
        config,
        bars,
        signals,
        cost_bps_per_side + cost_stress_extra_bps,
        True,
    )
    fold_rows = fold_summaries(trades, folds)
    return {
        "signals": len(signals),
        "summary": summarize_trades(trades),
        "stable_folds": sum(1 for row in fold_rows if row.get("stable")),
        "folds": fold_rows,
        "cost_stress": {
            "extra_bps_per_side": cost_stress_extra_bps,
            "summary": summarize_trades(stress_trades),
        },
        "sample_trades": [asdict(trade) for trade in trades[:3]],
    }


def feature_coverage(features: list[dict[str, Any]], start: int, end: int) -> dict[str, Any]:
    names = ("funding", "oi_delta_pct", "spot_perp_divergence_pct", "volume_z")
    rows = features[max(0, start) : min(end, len(features))]
    coverage: dict[str, Any] = {"bars": len(rows)}
    for name in names:
        present = sum(1 for row in rows if row.get(name) is not None)
        coverage[name] = {
            "present": present,
            "coverage_pct": round(present / len(rows) * 100.0, 3) if rows else 0.0,
        }
    return coverage


def base_rank(row: dict[str, Any]) -> tuple[Any, ...]:
    summary = row["train"]["summary"]
    stress = row["train"]["cost_stress"]["summary"]
    return (
        safe_float(stress.get("expectancy_r")),
        safe_float(summary.get("expectancy_r")),
        safe_int(row["train"].get("stable_folds")),
        safe_int(summary.get("trades")),
    )


def candidate_rank(row: dict[str, Any]) -> tuple[Any, ...]:
    summary = row["train"]["summary"]
    stress = row["train"]["cost_stress"]["summary"]
    trades = max(1, safe_int(summary.get("trades")))
    robust_score = safe_float(stress.get("expectancy_r")) * math.sqrt(trades)
    return (
        robust_score,
        safe_int(row["train"].get("stable_folds")),
        safe_float(summary.get("expectancy_r")),
        trades,
    )


def train_gate(window: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    summary = window["summary"]
    stress = window["cost_stress"]["summary"]
    checks = {
        "min_trades": safe_int(summary.get("trades")) >= args.min_train_trades,
        "min_expectancy_r": safe_float(summary.get("expectancy_r")) >= args.min_train_expectancy_r,
        "min_stable_folds": safe_int(window.get("stable_folds")) >= args.min_train_stable_folds,
        "max_drawdown_r": safe_float(summary.get("max_drawdown_r"), 0.0) >= -abs(args.max_train_drawdown_r),
        "cost_stress_positive": safe_float(stress.get("expectancy_r")) > 0.0,
    }
    return {"pass": all(checks.values()), "checks": checks}


def oos_gate(window: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    summary = window["summary"]
    stress = window["cost_stress"]["summary"]
    checks = {
        "min_trades": safe_int(summary.get("trades")) >= args.min_oos_trades,
        "min_expectancy_r": safe_float(summary.get("expectancy_r")) >= args.min_oos_expectancy_r,
        "min_stable_folds": safe_int(window.get("stable_folds")) >= args.min_oos_stable_folds,
        "max_drawdown_r": safe_float(summary.get("max_drawdown_r"), 0.0) >= -abs(args.max_oos_drawdown_r),
        "cost_stress_positive": safe_float(stress.get("expectancy_r")) > 0.0,
    }
    return {"pass": all(checks.values()), "checks": checks}


def classify_oos_gate(gate: dict[str, Any]) -> str:
    if gate.get("pass") is True:
        return "pass_oos_observer_candidate_not_trade_permission"
    checks = gate.get("checks") if isinstance(gate.get("checks"), dict) else {}
    economic_checks = ("min_expectancy_r", "max_drawdown_r", "cost_stress_positive")
    if any(checks.get(name) is not True for name in economic_checks):
        return "reject_oos_gate_failed"
    return "insufficient_oos_evidence_keep_observer_only"


def public_candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def search_lane(
    lane: str,
    configs: list[RangeConfig],
    bars: list[Any],
    features: list[dict[str, Any]],
    rsi14: list[float | None],
    split_index: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    holds = LANES[lane]["holds"]
    lane_configs = [config for config in configs if config.max_hold_bars in holds]
    base_rows: list[dict[str, Any]] = []
    for config in lane_configs:
        signals = window_signals(
            config,
            bars,
            features,
            rsi14,
            start_index=0,
            end_index=split_index - config.max_hold_bars - 1,
        )
        train = summarize_window(
            config,
            bars,
            signals,
            cost_bps_per_side=args.cost_bps_per_side,
            cost_stress_extra_bps=args.cost_stress_extra_bps,
            folds=args.folds,
        )
        base_rows.append({"strategy_id": config.strategy_id, "train": train, "_config": config, "_signals": signals})

    eligible_bases = [
        row for row in base_rows if safe_int(row["train"]["summary"].get("trades")) >= args.min_base_train_trades
    ]
    eligible_bases.sort(key=base_rank, reverse=True)
    shortlist = eligible_bases[: args.base_shortlist]

    filter_modes = make_filters()
    refined_rows: list[dict[str, Any]] = []
    for base in shortlist:
        base_config = base["_config"]
        for mode, filter_names in filter_modes.items():
            config = replace(base_config, strategy_id=f"{base_config.strategy_id}__refine_{mode}")
            signals = apply_filter_mode(config, base["_signals"], filter_names)
            train = summarize_window(
                config,
                bars,
                signals,
                cost_bps_per_side=args.cost_bps_per_side,
                cost_stress_extra_bps=args.cost_stress_extra_bps,
                folds=args.folds,
            )
            gate = train_gate(train, args)
            refined_rows.append(
                {
                    "base_strategy_id": base_config.strategy_id,
                    "strategy_id": config.strategy_id,
                    "filter_mode": mode,
                    "filters": list(filter_names),
                    "config": asdict(config),
                    "train": train,
                    "train_gate": gate,
                    "_config": config,
                    "_filter_names": filter_names,
                }
            )

    qualified = [row for row in refined_rows if row["train_gate"]["pass"]]
    qualified.sort(key=candidate_rank, reverse=True)
    selected = qualified[0] if qualified else None
    result: dict[str, Any] = {
        "family": lane,
        "lane_policy": {
            "description": LANES[lane]["description"],
            "holds": sorted(holds),
        },
        "base_configs_tested_on_train": len(base_rows),
        "eligible_bases": len(eligible_bases),
        "base_shortlist_size": len(shortlist),
        "refined_candidates_tested_on_train": len(refined_rows),
        "train_qualified_count": len(qualified),
        "top_train_candidates": [public_candidate(row) for row in qualified[:10]],
    }
    if selected is None:
        result.update(
            {
                "selected_on_train": None,
                "oos": None,
                "oos_gate": None,
                "decision": "reject_no_train_candidate",
            }
        )
        return result

    selected_config = selected["_config"]
    oos_base_signals = window_signals(
        selected_config,
        bars,
        features,
        rsi14,
        start_index=split_index,
        end_index=len(bars),
    )
    oos_signals = apply_filter_mode(selected_config, oos_base_signals, selected["_filter_names"])
    oos = summarize_window(
        selected_config,
        bars,
        oos_signals,
        cost_bps_per_side=args.cost_bps_per_side,
        cost_stress_extra_bps=args.cost_stress_extra_bps,
        folds=args.folds,
    )
    gate = oos_gate(oos, args)
    result.update(
        {
            "selected_on_train": public_candidate(selected),
            "oos": oos,
            "oos_gate": gate,
            "decision": classify_oos_gate(gate),
            "_oos_signal_indices": [int(signal["bar_index"]) for signal in oos_signals],
        }
    )
    return result


def signal_overlap(left: list[int], right: list[int]) -> dict[str, Any]:
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    intersection = left_set & right_set
    return {
        "intersection": len(intersection),
        "union": len(union),
        "jaccard": round(len(intersection) / len(union), 6) if union else 0.0,
        "independent_enough": bool(union and len(intersection) / len(union) < 0.8),
    }


def build_grid_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        intervals="4h",
        rr=args.rr,
        max_holds=args.max_holds,
        lookbacks=args.lookbacks,
        edge_pcts=args.edge_pcts,
        min_width_atr=args.min_width_atr,
        max_width_atr=args.max_width_atr,
        max_abs_trend_atr=args.max_abs_trend_atr,
        max_atr_ratio=args.max_atr_ratio,
    )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    cache_dir = resolve_path(args.cache_dir)
    bars, features, rsi14 = load_interval_payload(cache_dir, "4h", args.oi_lag, args.spot_perp_lookback)
    split_index = find_split_index(bars, args.split_ts)
    configs = build_configs(build_grid_args(args))
    family_results = [
        search_lane(lane, configs, bars, features, rsi14, split_index, args)
        for lane in LANES
    ]
    range_indices = family_results[0].pop("_oos_signal_indices", [])
    edge_indices = family_results[1].pop("_oos_signal_indices", [])
    overlap = signal_overlap(range_indices, edge_indices)
    passed = [row["family"] for row in family_results if row["decision"].startswith("pass_oos")]
    rejected = [row["family"] for row in family_results if row["decision"].startswith("reject_oos")]
    insufficient = [row["family"] for row in family_results if row["decision"].startswith("insufficient_oos")]
    if passed:
        decision = "observer_candidates_need_forward_proof"
        next_action = "keep_passed_and_insufficient_families_observer_only"
    elif rejected and insufficient:
        decision = "range_rejected_edge_insufficient_oos_evidence"
        next_action = "pause_rejected_range_family_and_keep_edge_observer_only"
    else:
        decision = "no_range_edge_family_passed_honest_oos"
        next_action = "pause_failed_family_observers_and_redesign_without_oos_reuse"
    return {
        "generated_at": now_iso(),
        "method": "train_only_nested_selection_then_untouched_calendar_oos",
        "selection_frozen_before_oos": True,
        "runtime_boundary": {
            "research_only": True,
            "sends_orders": False,
            "changes_observers": False,
            "can_trade": False,
        },
        "data": {
            "cache_dir": rel_path(cache_dir),
            "bars": len(bars),
            "first_bar_ts": bars[0].ts,
            "last_bar_ts": bars[-1].ts,
            "split_ts": args.split_ts,
            "split_index": split_index,
            "train_bars": split_index,
            "oos_bars": len(bars) - split_index,
            "train_feature_coverage": feature_coverage(features, 0, split_index),
            "oos_feature_coverage": feature_coverage(features, split_index, len(features)),
        },
        "search": {
            "grid": {
                "rr": args.rr,
                "max_holds": args.max_holds,
                "lookbacks": args.lookbacks,
                "edge_pcts": args.edge_pcts,
            },
            "base_shortlist_per_lane": args.base_shortlist,
            "filter_modes": list(make_filters()),
            "oos_not_used_for_ranking_or_selection": True,
        },
        "gates": {
            "train": {
                "min_trades": args.min_train_trades,
                "min_expectancy_r": args.min_train_expectancy_r,
                "min_stable_folds": args.min_train_stable_folds,
                "max_drawdown_r": -abs(args.max_train_drawdown_r),
                "cost_stress_extra_bps_per_side": args.cost_stress_extra_bps,
                "cost_stress_expectancy_must_be_positive": True,
            },
            "oos": {
                "min_trades": args.min_oos_trades,
                "min_expectancy_r": args.min_oos_expectancy_r,
                "min_stable_folds": args.min_oos_stable_folds,
                "max_drawdown_r": -abs(args.max_oos_drawdown_r),
                "cost_stress_extra_bps_per_side": args.cost_stress_extra_bps,
                "cost_stress_expectancy_must_be_positive": True,
            },
        },
        "families": family_results,
        "cross_family_oos_signal_overlap": overlap,
        "passed_oos_families": passed,
        "rejected_oos_families": rejected,
        "insufficient_oos_families": insufficient,
        "decision": decision,
        "next_action": next_action,
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    data = report["data"]
    lines = [
        "# RANGE / EDGE Nested Holdout",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Selection and filter ranking use only bars before the calendar split.",
        "- OOS is opened only after one candidate per lane is frozen.",
        "- Results are research evidence only; no observer, paper, or order permission is changed.",
        "",
        "## Data",
        "",
        f"- Bars: `{data['bars']}` from `{data['first_bar_ts']}` to `{data['last_bar_ts']}`.",
        f"- Split: `{data['split_ts']}`; train `{data['train_bars']}` bars, OOS `{data['oos_bars']}` bars.",
        f"- Train OI coverage: `{data['train_feature_coverage']['oi_delta_pct']['coverage_pct']}%`.",
        f"- OOS OI coverage: `{data['oos_feature_coverage']['oi_delta_pct']['coverage_pct']}%`.",
        "",
        "## Decisions",
        "",
    ]
    for family in report["families"]:
        lines.append(f"### {family['family']}")
        lines.append("")
        lines.append(f"- Decision: `{family['decision']}`.")
        lines.append(f"- Train search: `{family['base_configs_tested_on_train']}` bases, `{family['refined_candidates_tested_on_train']}` refined candidates.")
        selected = family.get("selected_on_train")
        if selected:
            train = selected["train"]["summary"]
            oos = family["oos"]["summary"]
            stress = family["oos"]["cost_stress"]["summary"]
            lines.extend(
                [
                    f"- Frozen candidate: `{selected['strategy_id']}`.",
                    f"- Train: `{train.get('trades')}` trades, `{train.get('expectancy_r')}`R expectancy, DD `{train.get('max_drawdown_r')}`R.",
                    f"- OOS: `{oos.get('trades')}` trades, `{oos.get('winrate_pct')}%` winrate, `{oos.get('expectancy_r')}`R expectancy, DD `{oos.get('max_drawdown_r')}`R.",
                    f"- OOS +10bps/side: `{stress.get('expectancy_r')}`R expectancy.",
                ]
            )
        else:
            lines.append("- No train-qualified candidate; OOS was not used to rescue the family.")
        lines.append("")
    overlap = report["cross_family_oos_signal_overlap"]
    lines.extend(
        [
            "## Cross-family Check",
            "",
            f"- OOS signal Jaccard overlap: `{overlap['jaccard']}`.",
            f"- Independent enough under the precommitted `<0.8` rule: `{overlap['independent_enough']}`.",
            "",
            "## Final",
            "",
            f"- Decision: `{report['decision']}`.",
            f"- Next action: `{report['next_action']}`.",
            "- `can_trade=false`.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Honest train/OOS validation for RANGE and EDGE 4H families")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--split-ts", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--out-prefix", default="docs/RANGE_EDGE_NESTED_HOLDOUT_2026-06-23")
    parser.add_argument("--rr", default="1:1,1:1.2,1:1.5,1:2")
    parser.add_argument("--max-holds", default="8,12,16")
    parser.add_argument("--lookbacks", default="20,40")
    parser.add_argument("--edge-pcts", default="0.15,0.2")
    parser.add_argument("--min-width-atr", type=float, default=2.0)
    parser.add_argument("--max-width-atr", type=float, default=12.0)
    parser.add_argument("--max-abs-trend-atr", type=float, default=2.2)
    parser.add_argument("--max-atr-ratio", type=float, default=1.15)
    parser.add_argument("--oi-lag", type=int, default=12)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--cost-bps-per-side", type=float, default=7.0)
    parser.add_argument("--cost-stress-extra-bps", type=float, default=10.0)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--base-shortlist", type=int, default=12)
    parser.add_argument("--min-base-train-trades", type=int, default=50)
    parser.add_argument("--min-train-trades", type=int, default=40)
    parser.add_argument("--min-train-expectancy-r", type=float, default=0.10)
    parser.add_argument("--min-train-stable-folds", type=int, default=2)
    parser.add_argument("--max-train-drawdown-r", type=float, default=10.0)
    parser.add_argument("--min-oos-trades", type=int, default=20)
    parser.add_argument("--min-oos-expectancy-r", type=float, default=0.10)
    parser.add_argument("--min-oos-stable-folds", type=int, default=2)
    parser.add_argument("--max-oos-drawdown-r", type=float, default=6.0)
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "decision": report["decision"],
                "families": {row["family"]: row["decision"] for row in report["families"]},
                "json": rel_path(out_prefix.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

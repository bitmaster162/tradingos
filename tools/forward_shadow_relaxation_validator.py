#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_hardening import fold_summaries, simulate_trade, summarize_trades  # noqa: E402
from tools.strategy_mix_combo_tester import load_interval_data, stable_fold_count  # noqa: E402
from tools.strategy_mix_holdout_validator import ReplayConfig, result_to_config  # noqa: E402
from tools.strategy_mix_paper_replay import select_candidates  # noqa: E402


@dataclass(frozen=True)
class ShadowVariant:
    variant_id: str
    strategy_id: str
    conditions: tuple[str, ...]
    notes: str
    concept_drift: str


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


def parse_verdicts(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


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


def candidate_from_source(path: Path, verdicts: set[str], top: int) -> ReplayConfig:
    source = read_json(path)
    candidates = select_candidates(source, verdicts, top)
    if not candidates:
        raise ValueError("no_candidate_found_for_shadow_relaxation_validator")
    return result_to_config(candidates[0])


def replace_condition(conditions: tuple[str, ...], old: str, new: str) -> tuple[str, ...]:
    return tuple(new if item == old else item for item in conditions)


def build_variants(config: ReplayConfig) -> list[ShadowVariant]:
    conditions = tuple(config.conditions)
    variants = [
        ShadowVariant(
            variant_id="locked",
            strategy_id=config.strategy_id,
            conditions=conditions,
            notes="Original locked paper-replay candidate.",
            concept_drift="none",
        )
    ]
    if "expansion_atr" in conditions:
        for threshold in (1.10, 1.05, 1.00, 0.95):
            token = f"expansion_atr_ge_{str(threshold).replace('.', '_')}"
            variants.append(
                ShadowVariant(
                    variant_id=token,
                    strategy_id=f"{config.strategy_id}__shadow_{token}",
                    conditions=replace_condition(conditions, "expansion_atr", token),
                    notes=f"Research-only relaxed ATR expansion threshold: atr_ratio >= {threshold:g}.",
                    concept_drift="low",
                )
            )
        reduced = tuple(item for item in conditions if item != "expansion_atr")
        variants.append(
            ShadowVariant(
                variant_id="drop_expansion_atr",
                strategy_id=f"{config.strategy_id}__shadow_drop_expansion_atr",
                conditions=reduced,
                notes="Research-only removal of ATR expansion guard.",
                concept_drift="medium",
            )
        )
    if "breakout_up_20" in conditions:
        reduced = tuple(item for item in conditions if item != "breakout_up_20")
        variants.append(
            ShadowVariant(
                variant_id="drop_breakout_up_20",
                strategy_id=f"{config.strategy_id}__shadow_drop_breakout_up_20",
                conditions=reduced,
                notes="Research-only removal of the 20-bar breakout trigger. This changes the setup meaning.",
                concept_drift="high",
            )
        )
    if "body_accept" in conditions:
        reduced = tuple(item for item in conditions if item != "body_accept")
        variants.append(
            ShadowVariant(
                variant_id="drop_body_accept",
                strategy_id=f"{config.strategy_id}__shadow_drop_body_accept",
                conditions=reduced,
                notes="Research-only removal of candle body acceptance guard.",
                concept_drift="medium",
            )
        )
    return variants


def condition_value(
    *,
    matrix: dict[str, list[bool]],
    features: list[dict[str, Any]],
    condition: str,
    index: int,
) -> bool:
    if condition.startswith("expansion_atr_ge_"):
        raw = condition.removeprefix("expansion_atr_ge_").replace("_", ".")
        threshold = float(raw)
        atr = features[index].get("atr")
        atr_ratio = features[index].get("atr_ratio")
        return atr is not None and atr > 0 and atr_ratio is not None and float(atr_ratio) >= threshold
    values = matrix.get(condition)
    if values is None or index < 0 or index >= len(values):
        return False
    return bool(values[index])


def generate_variant_signals(
    *,
    variant: ShadowVariant,
    config: ReplayConfig,
    bars: list[Any],
    features: list[dict[str, Any]],
    matrix: dict[str, list[bool]],
    start_index: int,
    end_index: int,
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    safe_end = min(end_index, len(bars))
    for index in range(max(0, start_index), safe_end):
        if not all(condition_value(matrix=matrix, features=features, condition=item, index=index) for item in variant.conditions):
            continue
        atr = features[index].get("atr")
        if atr is None or atr <= 0:
            continue
        signals.append(
            {
                "bar_index": index,
                "side_hint": config.side,
                "atr": atr,
                "reason": "+".join(variant.conditions),
                "strategy_id": variant.strategy_id,
                "feature_snapshot": {
                    "conditions": list(variant.conditions),
                    "funding": features[index].get("funding"),
                    "oi_delta_pct": features[index].get("oi_delta_pct"),
                    "spot_perp_divergence_pct": features[index].get("spot_perp_divergence_pct"),
                    "volume_z": features[index].get("volume_z"),
                    "atr_ratio": features[index].get("atr_ratio"),
                    "body_pct": features[index].get("body_pct"),
                },
            }
        )
    return signals


def replay_signals(
    *,
    variant: ShadowVariant,
    config: ReplayConfig,
    bars: list[Any],
    signals: list[dict[str, Any]],
    cost_bps_per_side: float,
    no_overlap: bool,
) -> list[Any]:
    trades = []
    last_exit_bar = -1
    for signal in sorted(signals, key=lambda item: int(item["bar_index"])):
        signal_index = int(signal["bar_index"])
        if no_overlap and signal_index <= last_exit_bar:
            continue
        trade = simulate_trade(
            dataset_id=f"shadow_relaxation_BTCUSDT_{config.interval}",
            strategy_id=variant.strategy_id,
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
    return trades


def bar_segments(total_bars: int, max_hold_bars: int, segment_count: int) -> list[tuple[int, int]]:
    usable_end = max(1, total_bars - max_hold_bars - 1)
    segments: list[tuple[int, int]] = []
    for segment in range(segment_count):
        start = round(usable_end * segment / segment_count)
        end = round(usable_end * (segment + 1) / segment_count)
        if end > start:
            segments.append((start, end))
    return segments


def evaluate_variant(
    *,
    variant: ShadowVariant,
    config: ReplayConfig,
    bars: list[Any],
    features: list[dict[str, Any]],
    matrix: dict[str, list[bool]],
    holdout_fraction: float,
    cost_bps_per_side: float,
    cost_stress_extra_bps: list[float],
    folds: int,
    segments: int,
    no_overlap: bool,
    args: argparse.Namespace,
) -> dict[str, Any]:
    end_index = len(bars) - config.max_hold_bars - 1
    holdout_start = round(end_index * (1.0 - holdout_fraction))
    full_signals = generate_variant_signals(
        variant=variant,
        config=config,
        bars=bars,
        features=features,
        matrix=matrix,
        start_index=0,
        end_index=end_index,
    )
    full_trades = replay_signals(
        variant=variant,
        config=config,
        bars=bars,
        signals=full_signals,
        cost_bps_per_side=cost_bps_per_side,
        no_overlap=no_overlap,
    )
    holdout_signals = [item for item in full_signals if int(item["bar_index"]) >= holdout_start]
    holdout_trades = replay_signals(
        variant=variant,
        config=config,
        bars=bars,
        signals=holdout_signals,
        cost_bps_per_side=cost_bps_per_side,
        no_overlap=no_overlap,
    )

    segment_rows = []
    for idx, (start, end) in enumerate(bar_segments(len(bars), config.max_hold_bars, segments), start=1):
        signals = [item for item in full_signals if start <= int(item["bar_index"]) < end]
        trades = replay_signals(
            variant=variant,
            config=config,
            bars=bars,
            signals=signals,
            cost_bps_per_side=cost_bps_per_side,
            no_overlap=no_overlap,
        )
        summary = summarize_trades(trades)
        segment_rows.append({"segment": idx, "start_index": start, "end_index": end, "signals": len(signals), "summary": summary})

    stress_rows = []
    for extra in cost_stress_extra_bps:
        trades = replay_signals(
            variant=variant,
            config=config,
            bars=bars,
            signals=full_signals,
            cost_bps_per_side=cost_bps_per_side + extra,
            no_overlap=no_overlap,
        )
        stress_rows.append(
            {
                "extra_bps_per_side": extra,
                "total_bps_per_side": cost_bps_per_side + extra,
                "summary": summarize_trades(trades),
            }
        )

    full_summary = summarize_trades(full_trades)
    holdout_summary = summarize_trades(holdout_trades)
    fold_rows = fold_summaries(full_trades, folds)
    segments_positive = sum(1 for item in segment_rows if safe_float(item["summary"].get("expectancy_r")) > 0)
    segment_positive_ratio = segments_positive / len(segment_rows) if segment_rows else 0.0
    worst_segment = min((safe_float(item["summary"].get("expectancy_r")) for item in segment_rows), default=-999.0)
    stress_10 = next((item for item in stress_rows if safe_float(item["extra_bps_per_side"], 0.0) == 10.0), None)
    stress_10_positive = safe_float(stress_10.get("summary", {}).get("expectancy_r")) > 0 if isinstance(stress_10, dict) else False
    checks = {
        "min_full_trades": safe_int(full_summary.get("trades")) >= args.min_full_trades,
        "min_full_expectancy": safe_float(full_summary.get("expectancy_r")) >= args.min_expectancy_r,
        "min_holdout_trades": safe_int(holdout_summary.get("trades")) >= args.min_holdout_trades,
        "min_holdout_expectancy": safe_float(holdout_summary.get("expectancy_r")) >= args.min_expectancy_r,
        "segment_positive_ratio": segment_positive_ratio >= args.min_segment_positive_ratio,
        "worst_segment_floor": worst_segment >= -abs(args.max_worst_segment_expectancy_r),
        "cost_stress_10bps_positive": stress_10_positive,
        "concept_drift_allowed": variant.concept_drift in {"none", "low", "medium"},
    }
    if all(checks.values()) and variant.variant_id != "locked":
        verdict = "shadow_candidate_for_forward_observation"
    elif variant.variant_id == "locked":
        verdict = "locked_baseline"
    elif safe_float(holdout_summary.get("expectancy_r")) > 0 and safe_int(holdout_summary.get("trades")) >= args.min_holdout_trades:
        verdict = "watchlist_only_needs_more_proof"
    else:
        verdict = "reject_or_keep_diagnostic_only"

    return {
        "variant_id": variant.variant_id,
        "strategy_id": variant.strategy_id,
        "conditions": list(variant.conditions),
        "notes": variant.notes,
        "concept_drift": variant.concept_drift,
        "signals": len(full_signals),
        "full": {"summary": full_summary, "stable_folds": stable_fold_count(fold_rows), "folds": fold_rows},
        "holdout": {"start_index": holdout_start, "signals": len(holdout_signals), "summary": holdout_summary},
        "segments": segment_rows,
        "segment_positive_ratio": round(segment_positive_ratio, 6),
        "worst_segment_expectancy_r": round(worst_segment, 6),
        "cost_stress": stress_rows,
        "checks": checks,
        "verdict": verdict,
        "sample_trades": [trade.__dict__ for trade in full_trades[:5]],
    }


def rank_key(item: dict[str, Any]) -> tuple[Any, ...]:
    verdict_rank = {
        "shadow_candidate_for_forward_observation": 4,
        "locked_baseline": 3,
        "watchlist_only_needs_more_proof": 2,
        "reject_or_keep_diagnostic_only": 1,
    }.get(str(item.get("verdict")), 0)
    return (
        verdict_rank,
        safe_float(item.get("holdout", {}).get("summary", {}).get("expectancy_r")),
        safe_int(item.get("holdout", {}).get("summary", {}).get("trades")),
        safe_float(item.get("full", {}).get("summary", {}).get("expectancy_r")),
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Forward Shadow Relaxation Validator",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Research-only validation of relaxed/ablated variants of the locked 4H forward strategy.",
        "- Does not change the forward strategy.",
        "- Does not grant paper or live trading permission.",
        "- Variants must pass full, holdout, segment and cost-stress gates before they can even be observed forward.",
        "",
        "## Decision",
        "",
        f"- Decision: `{report['decision']}`.",
        f"- Next action: `{report['next_action']}`.",
        f"- Can trade: `{report['can_trade']}`.",
        "",
        "## Source Candidate",
        "",
        f"- Strategy: `{report['candidate']['strategy_id']}`.",
        f"- Conditions: `{', '.join(report['candidate']['conditions'])}`.",
        f"- RR / hold: `{report['candidate']['rr']}` / `{report['candidate']['max_hold_bars']}` bars.",
        "",
        "## Results",
        "",
        "| Verdict | Variant | Drift | Signals | Full Trades | Full Exp | Full WR | Holdout Trades | Holdout Exp | Holdout WR | Seg+ | Worst Seg | Cost +10 Exp | Cost +20 Exp |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report["results"]:
        full = item["full"]["summary"]
        holdout = item["holdout"]["summary"]
        cost10 = next((row for row in item["cost_stress"] if safe_float(row["extra_bps_per_side"], 0.0) == 10.0), None)
        cost20 = next((row for row in item["cost_stress"] if safe_float(row["extra_bps_per_side"], 0.0) == 20.0), None)
        cost10_exp = cost10["summary"].get("expectancy_r") if cost10 else None
        cost20_exp = cost20["summary"].get("expectancy_r") if cost20 else None
        lines.append(
            f"| `{item['verdict']}` | `{item['variant_id']}` | `{item['concept_drift']}` | `{item['signals']}` | "
            f"`{full.get('trades')}` | `{full.get('expectancy_r')}` | `{full.get('winrate_pct')}` | "
            f"`{holdout.get('trades')}` | `{holdout.get('expectancy_r')}` | `{holdout.get('winrate_pct')}` | "
            f"`{item['segment_positive_ratio']}` | `{item['worst_segment_expectancy_r']}` | `{cost10_exp}` | `{cost20_exp}` |"
        )
    lines.extend(
        [
            "",
            "## Gate Requirements",
            "",
            f"- Min full trades: `{report['settings']['min_full_trades']}`.",
            f"- Min holdout trades: `{report['settings']['min_holdout_trades']}`.",
            f"- Min expectancy R: `{report['settings']['min_expectancy_r']}`.",
            f"- Min segment positive ratio: `{report['settings']['min_segment_positive_ratio']}`.",
            f"- Worst segment floor: `-{report['settings']['max_worst_segment_expectancy_r']}`.",
            "",
            "## Interpretation",
            "",
            "- A higher signal count is not enough. The variant must survive holdout, cost-stress and segment checks.",
            "- High concept-drift variants are diagnostics only unless separately redefined as a new strategy family.",
            "- `shadow_candidate_for_forward_observation` still means observe forward, not trade live.",
            "",
        ]
    )
    return "\n".join(lines)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "variant_id",
        "verdict",
        "concept_drift",
        "signals",
        "full_trades",
        "full_winrate_pct",
        "full_expectancy_r",
        "holdout_trades",
        "holdout_winrate_pct",
        "holdout_expectancy_r",
        "segment_positive_ratio",
        "worst_segment_expectancy_r",
        "cost10_expectancy_r",
        "cost20_expectancy_r",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in rows:
            full = item["full"]["summary"]
            holdout = item["holdout"]["summary"]
            cost10 = next((row for row in item["cost_stress"] if safe_float(row["extra_bps_per_side"], 0.0) == 10.0), None)
            cost20 = next((row for row in item["cost_stress"] if safe_float(row["extra_bps_per_side"], 0.0) == 20.0), None)
            writer.writerow(
                {
                    "variant_id": item["variant_id"],
                    "verdict": item["verdict"],
                    "concept_drift": item["concept_drift"],
                    "signals": item["signals"],
                    "full_trades": full.get("trades"),
                    "full_winrate_pct": full.get("winrate_pct"),
                    "full_expectancy_r": full.get("expectancy_r"),
                    "holdout_trades": holdout.get("trades"),
                    "holdout_winrate_pct": holdout.get("winrate_pct"),
                    "holdout_expectancy_r": holdout.get("expectancy_r"),
                    "segment_positive_ratio": item["segment_positive_ratio"],
                    "worst_segment_expectancy_r": item["worst_segment_expectancy_r"],
                    "cost10_expectancy_r": cost10["summary"].get("expectancy_r") if cost10 else None,
                    "cost20_expectancy_r": cost20["summary"].get("expectancy_r") if cost20 else None,
                }
            )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    source_report = resolve_path(args.source_report)
    cache_dir = resolve_path(args.cache_dir)
    verdicts = parse_verdicts(args.candidate_verdicts)
    config = candidate_from_source(source_report, verdicts, args.top)
    bars, features, matrix = load_interval_data(cache_dir, config.interval, args.oi_lag, args.spot_perp_lookback)
    if not bars:
        raise ValueError("no_bars_loaded")
    variants = build_variants(config)
    stress = [safe_float(item, 0.0) for item in args.cost_stress_extra_bps.split(",") if item.strip()]
    results = [
        evaluate_variant(
            variant=variant,
            config=config,
            bars=bars,
            features=features,
            matrix=matrix,
            holdout_fraction=args.holdout_fraction,
            cost_bps_per_side=args.cost_bps_per_side,
            cost_stress_extra_bps=stress,
            folds=args.folds,
            segments=args.segments,
            no_overlap=not args.allow_overlap,
            args=args,
        )
        for variant in variants
    ]
    results.sort(key=rank_key, reverse=True)
    shadow_candidates = [item for item in results if item.get("verdict") == "shadow_candidate_for_forward_observation"]
    if shadow_candidates:
        decision = "shadow_candidates_need_forward_observation"
        next_action = "add_top_shadow_to_observer_only_comparison"
    else:
        decision = "no_relaxation_promoted"
        next_action = "keep_locked_strategy_and_continue_forward_observation"
    return {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "mode": "research_only",
            "public_or_cached_data_only": True,
            "sends_orders": False,
            "can_trade": False,
        },
        "inputs": {
            "source_report": rel_path(source_report),
            "cache_dir": rel_path(cache_dir),
            "candidate_verdicts": sorted(verdicts),
            "top": args.top,
        },
        "settings": {
            "cost_bps_per_side": args.cost_bps_per_side,
            "cost_stress_extra_bps": stress,
            "holdout_fraction": args.holdout_fraction,
            "folds": args.folds,
            "segments": args.segments,
            "min_full_trades": args.min_full_trades,
            "min_holdout_trades": args.min_holdout_trades,
            "min_expectancy_r": args.min_expectancy_r,
            "min_segment_positive_ratio": args.min_segment_positive_ratio,
            "max_worst_segment_expectancy_r": args.max_worst_segment_expectancy_r,
        },
        "data": {
            "bars_loaded": len(bars),
            "first_bar_ts": str(bars[0].ts),
            "latest_bar_ts": str(bars[-1].ts),
        },
        "candidate": {
            "strategy_id": config.strategy_id,
            "interval": config.interval,
            "side": config.side,
            "conditions": list(config.conditions),
            "rr": f"{config.stop_atr:g}:{config.take_atr:g}",
            "max_hold_bars": config.max_hold_bars,
        },
        "tested": len(results),
        "shadow_candidate_count": len(shadow_candidates),
        "results": results,
        "decision": decision,
        "next_action": next_action,
        "can_trade": False,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Research-only validator for relaxed locked-forward strategy variants")
    parser.add_argument("--source-report", default="docs/STRATEGY_MIX_FORWARD_LOCKED_CANDIDATE_2026-06-29_4H_GUARDED_SHORT.json")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--candidate-verdicts", default="paper_replay_candidate_locked")
    parser.add_argument("--top", type=int, default=1)
    parser.add_argument("--oi-lag", type=int, default=12)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--cost-bps-per-side", type=float, default=7.0)
    parser.add_argument("--cost-stress-extra-bps", default="0,5,10,20")
    parser.add_argument("--holdout-fraction", type=float, default=0.25)
    parser.add_argument("--folds", type=int, default=8)
    parser.add_argument("--segments", type=int, default=6)
    parser.add_argument("--allow-overlap", action="store_true")
    parser.add_argument("--min-full-trades", type=int, default=100)
    parser.add_argument("--min-holdout-trades", type=int, default=20)
    parser.add_argument("--min-expectancy-r", type=float, default=0.05)
    parser.add_argument("--min-segment-positive-ratio", type=float, default=0.66)
    parser.add_argument("--max-worst-segment-expectancy-r", type=float, default=0.25)
    parser.add_argument("--out-prefix", default="docs/FORWARD_SHADOW_RELAXATION_VALIDATOR_2026-06-16")
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
                "shadow_candidate_count": report["shadow_candidate_count"],
                "tested": report["tested"],
                "out": rel_path(out_prefix.with_suffix(".json")),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

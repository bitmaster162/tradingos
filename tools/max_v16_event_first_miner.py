from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.max_backtest import INTERVAL_MS, candle_value, find_exit  # noqa: E402
from tools.max_v11_candidate_validator import load_or_fetch  # noqa: E402
from tools.max_v13_structural_candidate import gate_candidate, parse_float  # noqa: E402
from tools.max_v15_state_filters import (  # noqa: E402
    build_trade_features,
    load_or_fetch_derivatives,
    precompute_htf_bias,
)


@dataclass(frozen=True, slots=True)
class Condition:
    id: str
    label: str
    family: str
    predicate: Callable[[dict[str, Any]], bool]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def numeric(features: dict[str, Any], name: str) -> float:
    return parse_float(features.get(name))


def flag(name: str, expected: bool = True) -> Callable[[dict[str, Any]], bool]:
    return lambda features: bool(features.get(name)) is expected


def text_eq(name: str, expected: str) -> Callable[[dict[str, Any]], bool]:
    return lambda features: str(features.get(name)) == expected


def ge(name: str, threshold: float) -> Callable[[dict[str, Any]], bool]:
    return lambda features: not math.isnan(numeric(features, name)) and numeric(features, name) >= threshold


def le(name: str, threshold: float) -> Callable[[dict[str, Any]], bool]:
    return lambda features: not math.isnan(numeric(features, name)) and numeric(features, name) <= threshold


def between(name: str, low: float, high: float) -> Callable[[dict[str, Any]], bool]:
    return lambda features: not math.isnan(numeric(features, name)) and low <= numeric(features, name) <= high


def abs_le(name: str, threshold: float) -> Callable[[dict[str, Any]], bool]:
    return lambda features: not math.isnan(numeric(features, name)) and abs(numeric(features, name)) <= threshold


def abs_ge(name: str, threshold: float) -> Callable[[dict[str, Any]], bool]:
    return lambda features: not math.isnan(numeric(features, name)) and abs(numeric(features, name)) >= threshold


def build_event_conditions() -> list[Condition]:
    conditions: list[Condition] = [
        Condition("bullish_sweep", "bullish liquidity sweep", "sweep", flag("bullish_liquidity_sweep", True)),
        Condition("bearish_sweep", "bearish liquidity sweep", "sweep", flag("bearish_liquidity_sweep", True)),
        Condition("no_sweep", "no 20-bar sweep", "sweep", text_eq("sweep_side", "none")),
        Condition("near_low", "near Donchian low", "location", flag("near_low", True)),
        Condition("near_high", "near Donchian high", "location", flag("near_high", True)),
        Condition("htf_LONG", "HTF bias LONG", "htf_bias", text_eq("htf_bias", "LONG")),
        Condition("htf_SHORT", "HTF bias SHORT", "htf_bias", text_eq("htf_bias", "SHORT")),
        Condition("htf_NEUTRAL", "HTF bias NEUTRAL", "htf_bias", text_eq("htf_bias", "NEUTRAL")),
        Condition("funding_negative", "funding <= 0", "funding", le("funding", 0.0)),
        Condition("funding_positive", "funding >= 0", "funding", ge("funding", 0.0)),
        Condition("funding_compressed", "abs funding <= 0.0002", "funding", abs_le("funding", 0.0002)),
        Condition("funding_pos_hot", "funding >= 0.0008", "funding", ge("funding", 0.0008)),
        Condition("funding_neg_hot", "funding <= -0.0008", "funding", le("funding", -0.0008)),
        Condition("oi_down_12", "OI 12-bar delta <= 0", "oi_delta", le("oi_delta_12_pct", 0.0)),
        Condition("oi_up_12", "OI 12-bar delta >= 0", "oi_delta", ge("oi_delta_12_pct", 0.0)),
        Condition("oi_down_12_strong", "OI 12-bar delta <= -0.10%", "oi_delta", le("oi_delta_12_pct", -0.10)),
        Condition("oi_up_12_strong", "OI 12-bar delta >= 0.10%", "oi_delta", ge("oi_delta_12_pct", 0.10)),
        Condition("oi_z_pos_1_5", "OI zscore >= 1.5", "oi_z", ge("oi_zscore_100", 1.5)),
        Condition("oi_z_neg_1_5", "OI zscore <= -1.5", "oi_z", le("oi_zscore_100", -1.5)),
        Condition("oi_z_abs_2", "abs OI zscore >= 2", "oi_z", abs_ge("oi_zscore_100", 2.0)),
        Condition("spot_quiet", "spot volume ratio <= 0.8", "spot_volume", le("spot_volume_ratio", 0.8)),
        Condition("spot_active", "spot volume ratio >= 1.5", "spot_volume", ge("spot_volume_ratio", 1.5)),
        Condition("spot_normal", "spot volume ratio 0.8..1.2", "spot_volume", between("spot_volume_ratio", 0.8, 1.2)),
        Condition("range_tight", "Donchian width ATR <= 4", "width", le("donchian_width_atr", 4.0)),
        Condition("range_wide", "Donchian width ATR >= 6", "width", ge("donchian_width_atr", 6.0)),
        Condition("trend_up_20", "20-bar trend >= +1 ATR", "trend", ge("trend_strength_20_atr", 1.0)),
        Condition("trend_down_20", "20-bar trend <= -1 ATR", "trend", le("trend_strength_20_atr", -1.0)),
    ]
    return conditions


def compatible(combo: tuple[Condition, ...]) -> bool:
    families: set[str] = set()
    for condition in combo:
        if condition.family in families:
            return False
        families.add(condition.family)
    return True


def side_outcome(
    *,
    rows: list[dict[str, str]],
    signal_row: int,
    side: str,
    atr: float,
    stop_atr: float,
    take_atr: float,
    max_hold_bars: int,
    fee_bps: float,
    slippage_bps: float,
) -> dict[str, Any] | None:
    entry_row = signal_row + 1
    if entry_row >= len(rows) or math.isnan(atr) or atr <= 0:
        return None
    entry_open = candle_value(rows[entry_row], "open")
    if math.isnan(entry_open):
        return None
    slip = slippage_bps / 10000
    if side == "SHORT":
        entry = entry_open * (1 - slip)
        risk = stop_atr * atr
        stop = entry + risk
        take_profit = entry - take_atr * atr
    else:
        entry = entry_open * (1 + slip)
        risk = stop_atr * atr
        stop = entry - risk
        take_profit = entry + take_atr * atr
    exit_index, raw_exit, exit_reason = find_exit(
        rows,
        start_index=entry_row,
        side=side,
        entry=entry,
        stop=stop,
        take_profit=take_profit,
        max_hold_bars=max_hold_bars,
    )
    exit_price = raw_exit * (1 + slip) if side == "SHORT" else raw_exit * (1 - slip)
    gross_r = (entry - exit_price) / risk if side == "SHORT" else (exit_price - entry) / risk
    fee_cost = ((entry + exit_price) * (fee_bps / 10000)) / risk
    net_r = gross_r - fee_cost
    return {
        "entry_row": entry_row,
        "exit_row": exit_index,
        "entry": entry,
        "stop": stop,
        "take_profit": take_profit,
        "exit": exit_price,
        "exit_reason": exit_reason,
        "bars_held": max(1, exit_index - entry_row + 1),
        "gross_r": gross_r,
        "net_r": net_r,
    }


def build_event_rows(
    *,
    rows: list[dict[str, str]],
    spot_rows: list[dict[str, str]],
    derivatives_rows: list[dict[str, str]],
    htf_biases: list[dict[str, Any]],
    warmup_bars: int,
    stop_atr: float,
    take_atr: float,
    max_hold_bars: int,
    fee_bps: float,
    slippage_bps: float,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], dict[str, int]]:
    events: list[dict[str, Any]] = []
    by_signal_row: dict[int, dict[str, Any]] = {}
    skipped = {"feature_not_ready": 0, "bad_outcome": 0}
    for i in range(max(warmup_bars, 220), len(rows) - 1):
        features = build_trade_features(
            rows=rows,
            spot_rows=spot_rows,
            derivatives_rows=derivatives_rows,
            htf_biases=htf_biases,
            i=i,
        )
        if features is None or not features.get("derivatives_ready"):
            skipped["feature_not_ready"] += 1
            continue
        atr = parse_float(features.get("atr14"))
        long_outcome = side_outcome(
            rows=rows,
            signal_row=i,
            side="LONG",
            atr=atr,
            stop_atr=stop_atr,
            take_atr=take_atr,
            max_hold_bars=max_hold_bars,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )
        short_outcome = side_outcome(
            rows=rows,
            signal_row=i,
            side="SHORT",
            atr=atr,
            stop_atr=stop_atr,
            take_atr=take_atr,
            max_hold_bars=max_hold_bars,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )
        if long_outcome is None or short_outcome is None:
            skipped["bad_outcome"] += 1
            continue
        event = {
            **features,
            "long_net_r": round(long_outcome["net_r"], 6),
            "short_net_r": round(short_outcome["net_r"], 6),
            "long_win": int(long_outcome["net_r"] > 0),
            "short_win": int(short_outcome["net_r"] > 0),
        }
        events.append(event)
        by_signal_row[i] = features
    return events, by_signal_row, skipped


def summarize_values(values: list[float]) -> dict[str, Any]:
    wins = [value for value in values if value > 0]
    return {
        "events": len(values),
        "winrate_pct": round(len(wins) / len(values) * 100, 3) if values else None,
        "expectancy_r": round(sum(values) / len(values), 6) if values else None,
        "net_r_total": round(sum(values), 6) if values else 0.0,
    }


def fold_ranges(total: int, folds: int) -> list[tuple[int, int]]:
    folds = max(1, folds)
    span = max(1, math.ceil(total / folds))
    ranges: list[tuple[int, int]] = []
    for fold in range(folds):
        start = fold * span
        end = min(total, start + span)
        if start < end:
            ranges.append((start, end))
    return ranges


def mine_event_candidates(
    *,
    events: list[dict[str, Any]],
    conditions: list[Condition],
    max_conditions: int,
    min_events: int,
    min_winrate_pct: float,
    min_expectancy_r: float,
    folds: int,
    min_fold_events: int,
    top: int,
) -> dict[str, Any]:
    baseline = {
        "LONG": summarize_values([parse_float(event.get("long_net_r")) for event in events]),
        "SHORT": summarize_values([parse_float(event.get("short_net_r")) for event in events]),
    }
    total = len(events)
    all_mask = (1 << total) - 1 if total else 0
    condition_masks: list[int] = []
    for condition in conditions:
        mask = 0
        for idx, event in enumerate(events):
            if condition.predicate(event):
                mask |= 1 << idx
        condition_masks.append(mask)

    fold_masks: list[int] = []
    for start, end in fold_ranges(total, folds):
        mask = 0
        for idx in range(start, end):
            mask |= 1 << idx
        fold_masks.append(mask)

    long_values = [parse_float(event.get("long_net_r")) for event in events]
    short_values = [parse_float(event.get("short_net_r")) for event in events]

    def selected_values(mask: int, side: str) -> list[float]:
        source = long_values if side == "LONG" else short_values
        values: list[float] = []
        idx = 0
        current = mask
        while current:
            if current & 1:
                value = source[idx]
                if not math.isnan(value):
                    values.append(value)
            current >>= 1
            idx += 1
        return values

    candidates: list[dict[str, Any]] = []
    tested = 0
    for size in range(1, max(1, max_conditions) + 1):
        for indexes in itertools.combinations(range(len(conditions)), size):
            combo = tuple(conditions[idx] for idx in indexes)
            if not compatible(combo):
                continue
            mask = all_mask
            for idx in indexes:
                mask &= condition_masks[idx]
                if not mask:
                    break
            event_count = mask.bit_count()
            if event_count < min_events:
                continue
            tested += 1
            for side in ("LONG", "SHORT"):
                values = selected_values(mask, side)
                summary = summarize_values(values)
                if summary["winrate_pct"] is None or summary["expectancy_r"] is None:
                    continue
                if summary["winrate_pct"] < min_winrate_pct or summary["expectancy_r"] < min_expectancy_r:
                    continue
                stable_folds = 0
                fold_rows: list[dict[str, Any]] = []
                for fold_idx, fold_mask in enumerate(fold_masks, start=1):
                    fold_values = selected_values(mask & fold_mask, side)
                    fold_summary = summarize_values(fold_values)
                    if (
                        fold_summary["events"] >= min_fold_events
                        and fold_summary["expectancy_r"] is not None
                        and fold_summary["expectancy_r"] >= min_expectancy_r
                    ):
                        stable_folds += 1
                    fold_rows.append({"fold": fold_idx, **fold_summary})
                baseline_exp = parse_float(baseline[side].get("expectancy_r"), 0.0)
                baseline_win = parse_float(baseline[side].get("winrate_pct"), 0.0)
                edge_exp = summary["expectancy_r"] - baseline_exp
                edge_win = summary["winrate_pct"] - baseline_win
                stability_ratio = stable_folds / len(fold_rows) if fold_rows else 0.0
                score = edge_exp * math.log10(event_count + 1) * (0.35 + 0.65 * stability_ratio)
                candidates.append(
                    {
                        "id": "v16_" + side.lower() + "_" + "__".join(condition.id for condition in combo),
                        "side": side,
                        "condition_ids": [condition.id for condition in combo],
                        "labels": [condition.label for condition in combo],
                        "event_summary": summary,
                        "baseline": baseline[side],
                        "edge_expectancy_r": round(edge_exp, 6),
                        "edge_winrate_pct": round(edge_win, 3),
                        "stable_folds": stable_folds,
                        "fold_count": len(fold_rows),
                        "stability_ratio": round(stability_ratio, 3),
                        "score": round(score, 6),
                        "folds": fold_rows,
                    }
                )
    candidates.sort(
        key=lambda item: (
            item["stable_folds"],
            item["score"],
            item["event_summary"]["events"],
            item["event_summary"]["expectancy_r"] or -999.0,
        ),
        reverse=True,
    )
    return {
        "baseline": baseline,
        "condition_count": len(conditions),
        "tested_masks": tested,
        "candidate_count": len(candidates),
        "top_candidates": candidates[:top],
    }


def simulate_mined_candidate(
    *,
    candidate: dict[str, Any],
    conditions_by_id: dict[str, Condition],
    rows: list[dict[str, str]],
    features_by_signal_row: dict[int, dict[str, Any]],
    warmup_bars: int,
    stop_atr: float,
    take_atr: float,
    max_hold_bars: int,
    fee_bps: float,
    slippage_bps: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    side = str(candidate["side"])
    conditions = [conditions_by_id[item] for item in candidate["condition_ids"]]
    trades: list[dict[str, Any]] = []
    skipped = {"feature_not_ready": 0, "rule_not_matched": 0, "bad_outcome": 0}
    i = max(warmup_bars, 220)
    while i < len(rows) - 1:
        features = features_by_signal_row.get(i)
        if not features:
            skipped["feature_not_ready"] += 1
            i += 1
            continue
        if not all(condition.predicate(features) for condition in conditions):
            skipped["rule_not_matched"] += 1
            i += 1
            continue
        outcome = side_outcome(
            rows=rows,
            signal_row=i,
            side=side,
            atr=parse_float(features.get("atr14")),
            stop_atr=stop_atr,
            take_atr=take_atr,
            max_hold_bars=max_hold_bars,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )
        if outcome is None:
            skipped["bad_outcome"] += 1
            i += 1
            continue
        trades.append(
            {
                "index": len(trades) + 1,
                "candidate_id": candidate["id"],
                "side": side,
                "setup": candidate["id"],
                "signal_row": i,
                "entry_row": outcome["entry_row"],
                "exit_row": outcome["exit_row"],
                "signal_time": features["signal_time"],
                "entry_time": rows[outcome["entry_row"]].get("time", str(outcome["entry_row"])),
                "exit_time": rows[outcome["exit_row"]].get("time", str(outcome["exit_row"])),
                "entry": round(outcome["entry"], 8),
                "stop": round(outcome["stop"], 8),
                "take_profit": round(outcome["take_profit"], 8),
                "exit": round(outcome["exit"], 8),
                "exit_reason": outcome["exit_reason"],
                "bars_held": outcome["bars_held"],
                "gross_r": round(outcome["gross_r"], 6),
                "net_r": round(outcome["net_r"], 6),
                **{
                    key: (round(value, 8) if isinstance(value, float) and not math.isnan(value) else value)
                    for key, value in features.items()
                    if key not in {"close"}
                },
            }
        )
        i = int(outcome["exit_row"]) + 1
    return trades, skipped


def render_markdown(report: dict[str, Any]) -> str:
    mining = report["mining"]
    lines = [
        "# MAX Core Lite v1.6 Event-First Miner",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Engine: `{report['engine_version']}`",
        f"- Data: `{report['data']['first_time']}` -> `{report['data']['last_time']}`",
        f"- Rows: `{report['data']['rows']}` futures / `{report['data']['events']}` mined event rows",
        "",
        "## Purpose",
        "",
        "Mines OI/funding/sweep/HTF conditions as primary event features, then validates top candidates with no-overlap trade simulation, folds and bootstrap.",
        "",
        "## Event Baseline",
        "",
        f"- LONG event baseline: `{mining['baseline']['LONG']}`",
        f"- SHORT event baseline: `{mining['baseline']['SHORT']}`",
        f"- Tested masks: `{mining['tested_masks']}`",
        f"- Discovery candidates: `{mining['candidate_count']}`",
        "",
        "## Top No-Overlap Validations",
        "",
        "| Candidate | Side | Trades | Winrate | Expectancy | Net R | Bootstrap P>0 | Stable Folds | Verdict |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in report["validated_candidates"]:
        summary = item["summary"]
        gate = item["research_gate"]
        prob = (item.get("bootstrap", {}).get("expectancy_r") or {}).get("prob_gt_0")
        lines.append(
            f"| `{item['id']}` | {item['side']} | {summary['trades']} | {summary['winrate_pct']} | "
            f"{summary['expectancy_r']} | {summary['net_r_total']} | {prob} | "
            f"{gate['stable_folds']}/{gate['fold_count']} | `{gate['verdict']}` |"
        )
    best = report.get("best_candidate")
    lines.extend(["", "## Best Candidate", ""])
    if best:
        lines.extend(
            [
                f"- ID: `{best['id']}`",
                f"- Side: `{best['side']}`",
                f"- Conditions: `{', '.join(best['labels'])}`",
                f"- Trades: `{best['summary']['trades']}`",
                f"- Winrate: `{best['summary']['winrate_pct']}`",
                f"- Expectancy: `{best['summary']['expectancy_r']}`",
                f"- Verdict: `{best['research_gate']['verdict']}`",
                "",
            ]
        )
    lines.extend(["## Decision", "", report["decision"], "", "## Boundary", "", report["runtime_boundary"], ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="MAX Core Lite v1.6 event-first miner")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--htf-interval", default="4h")
    parser.add_argument("--market", default="futures", choices=["futures"])
    parser.add_argument("--pages", type=int, default=24)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--htf-pages", type=int, default=8)
    parser.add_argument("--derivatives-pages", type=int, default=48)
    parser.add_argument("--derivatives-limit", type=int, default=500)
    parser.add_argument("--warmup-bars", type=int, default=220)
    parser.add_argument("--stop-atr", type=float, default=1.0)
    parser.add_argument("--take-atr", type=float, default=1.5)
    parser.add_argument("--max-hold-bars", type=int, default=16)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--max-conditions", type=int, default=3)
    parser.add_argument("--min-events", type=int, default=80)
    parser.add_argument("--min-event-winrate-pct", type=float, default=50.0)
    parser.add_argument("--min-event-expectancy-r", type=float, default=0.0)
    parser.add_argument("--top-mined", type=int, default=40)
    parser.add_argument("--simulate-top", type=int, default=20)
    parser.add_argument("--folds", type=int, default=8)
    parser.add_argument("--min-fold-events", type=int, default=8)
    parser.add_argument("--bootstrap-iterations", type=int, default=3000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260602)
    parser.add_argument("--min-trades", type=int, default=100)
    parser.add_argument("--min-expectancy-r", type=float, default=0.0)
    parser.add_argument("--min-winrate-pct", type=float, default=50.0)
    parser.add_argument("--min-bootstrap-prob-gt-0", type=float, default=0.8)
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--cache-dir", default="data/cache/binance")
    parser.add_argument("--out-prefix", default="_dl/v16/MAX_CORE_LITE_V16_EVENT_FIRST_MINER")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    if not cache_dir.is_absolute():
        cache_dir = ROOT / cache_dir
    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    rows, source = load_or_fetch(
        use_cache=args.use_cache,
        cache_dir=cache_dir,
        market=args.market,
        symbol=args.symbol,
        interval=args.interval,
        limit=args.limit,
        pages=args.pages,
    )
    spot_rows, spot_source = load_or_fetch(
        use_cache=args.use_cache,
        cache_dir=cache_dir,
        market="spot",
        symbol=args.symbol,
        interval=args.interval,
        limit=args.limit,
        pages=args.pages,
    )
    htf_rows, htf_source = load_or_fetch(
        use_cache=args.use_cache,
        cache_dir=cache_dir,
        market=args.market,
        symbol=args.symbol,
        interval=args.htf_interval,
        limit=args.limit,
        pages=args.htf_pages,
    )
    derivatives_rows, derivatives_source = load_or_fetch_derivatives(
        use_cache=args.use_cache,
        cache_dir=cache_dir,
        symbol=args.symbol,
        interval=args.interval,
        rows=rows,
        limit=args.derivatives_limit,
        pages=args.derivatives_pages,
    )
    interval_ms = INTERVAL_MS.get(args.interval, 3_600_000)
    htf_biases = precompute_htf_bias(rows=rows, htf_rows=htf_rows, interval_ms=interval_ms, htf_interval=args.htf_interval)
    events, features_by_signal_row, skipped = build_event_rows(
        rows=rows,
        spot_rows=spot_rows,
        derivatives_rows=derivatives_rows,
        htf_biases=htf_biases,
        warmup_bars=args.warmup_bars,
        stop_atr=args.stop_atr,
        take_atr=args.take_atr,
        max_hold_bars=args.max_hold_bars,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
    )
    conditions = build_event_conditions()
    mining = mine_event_candidates(
        events=events,
        conditions=conditions,
        max_conditions=args.max_conditions,
        min_events=args.min_events,
        min_winrate_pct=args.min_event_winrate_pct,
        min_expectancy_r=args.min_event_expectancy_r,
        folds=args.folds,
        min_fold_events=args.min_fold_events,
        top=args.top_mined,
    )
    conditions_by_id = {condition.id: condition for condition in conditions}
    rng = random.Random(args.bootstrap_seed)
    validated: list[dict[str, Any]] = []
    for candidate in mining["top_candidates"][: args.simulate_top]:
        trades, candidate_skipped = simulate_mined_candidate(
            candidate=candidate,
            conditions_by_id=conditions_by_id,
            rows=rows,
            features_by_signal_row=features_by_signal_row,
            warmup_bars=args.warmup_bars,
            stop_atr=args.stop_atr,
            take_atr=args.take_atr,
            max_hold_bars=args.max_hold_bars,
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
        )
        gate = gate_candidate(
            trades=trades,
            rows_count=len(rows),
            warmup_bars=args.warmup_bars,
            folds=args.folds,
            bootstrap_iterations=args.bootstrap_iterations,
            bootstrap_seed=rng.randrange(1, 10_000_000),
            min_trades=args.min_trades,
            min_expectancy_r=args.min_expectancy_r,
            min_winrate_pct=args.min_winrate_pct,
            min_bootstrap_prob_gt_0=args.min_bootstrap_prob_gt_0,
        )
        validated.append(
            {
                "id": candidate["id"],
                "side": candidate["side"],
                "condition_ids": candidate["condition_ids"],
                "labels": candidate["labels"],
                "discovery": {
                    "event_summary": candidate["event_summary"],
                    "edge_expectancy_r": candidate["edge_expectancy_r"],
                    "edge_winrate_pct": candidate["edge_winrate_pct"],
                    "stable_folds": candidate["stable_folds"],
                    "fold_count": candidate["fold_count"],
                    "score": candidate["score"],
                },
                "summary": gate["summary"],
                "folds": gate["folds"],
                "stable_folds": gate["stable_folds"],
                "bootstrap": gate["bootstrap"],
                "research_gate": gate["research_gate"],
                "skipped": candidate_skipped,
                "trades": trades,
            }
        )

    validated.sort(
        key=lambda item: (
            1 if item["research_gate"].get("pass") else 0,
            parse_float(item["summary"].get("expectancy_r"), -999.0),
            parse_float(item["summary"].get("winrate_pct"), 0.0),
            int(item["summary"].get("trades") or 0),
        ),
        reverse=True,
    )
    passed = [item for item in validated if item["research_gate"].get("pass")]
    best = validated[0] if validated else None
    decision = (
        "At least one v1.6 event-first candidate passed the research gate and can move to paper-trading design review."
        if passed
        else "No v1.6 event-first candidate passed the research gate. Keep this research-only; do not paper/live trade."
    )
    report = {
        "generated_at": now_iso(),
        "engine": "MAX_CORE_LITE_V16_EVENT_FIRST_MINER",
        "engine_version": "1.6.0",
        "data": {
            "rows": len(rows),
            "spot_rows": len(spot_rows),
            "htf_rows": len(htf_rows),
            "derivatives_rows": len(derivatives_rows),
            "events": len(events),
            "first_time": rows[0].get("time") if rows else None,
            "last_time": rows[-1].get("time") if rows else None,
            "source": source,
            "spot_source": spot_source,
            "htf_source": htf_source,
            "derivatives_source": derivatives_source,
            "skipped": skipped,
        },
        "params": {
            "interval": args.interval,
            "htf_interval": args.htf_interval,
            "pages": args.pages,
            "limit": args.limit,
            "derivatives_pages": args.derivatives_pages,
            "max_conditions": args.max_conditions,
            "min_events": args.min_events,
            "top_mined": args.top_mined,
            "simulate_top": args.simulate_top,
            "stop_atr": args.stop_atr,
            "take_atr": args.take_atr,
            "max_hold_bars": args.max_hold_bars,
            "fee_bps": args.fee_bps,
            "slippage_bps": args.slippage_bps,
            "folds": args.folds,
            "bootstrap_iterations": args.bootstrap_iterations,
        },
        "mining": mining,
        "validated_candidates": validated,
        "best_candidate": best,
        "passed": passed,
        "decision": decision,
        "runtime_boundary": (
            "Research-only event-first mining and deterministic simulation. It uses public market data, "
            "does not use private keys, does not place orders, and does not approve live trading."
        ),
    }
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(json_path),
                "md": str(md_path),
                "events": len(events),
                "top_mined": len(mining["top_candidates"]),
                "validated": len(validated),
                "best_candidate": {
                    "id": best.get("id") if best else None,
                    "side": best.get("side") if best else None,
                    "summary": best.get("summary") if best else None,
                    "research_gate": best.get("research_gate") if best else None,
                },
                "passed": len(passed),
                "decision": decision,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

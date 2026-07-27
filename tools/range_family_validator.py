#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.event_feature_factory import build_features, load_csv_by_time  # noqa: E402
from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402
from tools.liquidity_sweep_hardening import fold_summaries, simulate_trade, summarize_trades  # noqa: E402


@dataclass(frozen=True)
class RangeConfig:
    strategy_id: str
    interval: str
    side: str
    trigger: str
    lookback: int
    edge_pct: float
    min_width_atr: float
    max_width_atr: float
    max_abs_trend_atr: float
    max_atr_ratio: float
    rsi_filter: str
    rsi_threshold: float
    stop_atr: float
    take_atr: float
    max_hold_bars: int


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


def parse_list(value: str, cast: Any = str) -> list[Any]:
    return [cast(item.strip()) for item in value.split(",") if item.strip()]


def parse_rr_list(value: str) -> list[tuple[float, float]]:
    out = []
    for raw in parse_list(value, str):
        left, right = raw.split(":", 1)
        out.append((float(left), float(right)))
    return out


def safe_float(value: Any, default: float = -999.0) -> float:
    try:
        if value is None:
            return default
        result = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(result) else result


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def sma(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    total = 0.0
    for index, value in enumerate(values):
        total += value
        if index >= window:
            total -= values[index - window]
        out.append(total / window if index + 1 >= window else None)
    return out


def rsi(values: list[float], length: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= length:
        return out
    gains: list[float] = []
    losses: list[float] = []
    avg_gain: float | None = None
    avg_loss: float | None = None
    for index in range(1, len(values)):
        delta = values[index] - values[index - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
        if index < length:
            continue
        if index == length:
            avg_gain = sum(gains[:length]) / length
            avg_loss = sum(losses[:length]) / length
        elif avg_gain is not None and avg_loss is not None:
            avg_gain = ((avg_gain * (length - 1)) + gains[-1]) / length
            avg_loss = ((avg_loss * (length - 1)) + losses[-1]) / length
        if avg_gain is None or avg_loss is None:
            continue
        if avg_loss == 0:
            out[index] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[index] = 100.0 - (100.0 / (1.0 + rs))
    return out


def previous_high_low(bars: list[Any], index: int, lookback: int) -> tuple[float | None, float | None]:
    if index < lookback:
        return None, None
    chunk = bars[index - lookback : index]
    return max(bar.high for bar in chunk), min(bar.low for bar in chunk)


def trend_atr(bars: list[Any], features: list[dict[str, Any]], index: int, lookback: int) -> float | None:
    if index < lookback:
        return None
    atr = features[index].get("atr")
    if atr is None or atr <= 0:
        return None
    return (bars[index].close - bars[index - lookback].close) / atr


def load_interval_payload(cache_dir: Path, interval: str, oi_lag: int, spot_perp_lookback: int) -> tuple[list[Any], list[dict[str, Any]], list[float | None]]:
    futures_path = cache_dir / "futures" / "BTCUSDT" / f"{interval}_klines.csv"
    spot_path = cache_dir / "spot" / "BTCUSDT" / f"{interval}_klines.csv"
    derivatives_path = cache_dir / "futures" / "BTCUSDT" / f"{interval}_oi_aligned.csv"
    bars = load_ohlcv(futures_path)
    spot_bars = load_ohlcv(spot_path) if spot_path.exists() else []
    spot_by_time = {bar.ts: bar for bar in spot_bars}
    derivatives_by_time = load_csv_by_time(derivatives_path)
    features = build_features(
        bars=bars,
        spot_by_time=spot_by_time,
        derivatives_by_time=derivatives_by_time,
        oi_lag=oi_lag,
        spot_perp_lookback=spot_perp_lookback,
        volume_window=20,
        atr_window=20,
    )
    return bars, features, rsi([bar.close for bar in bars], 14)


def build_configs(args: argparse.Namespace) -> list[RangeConfig]:
    configs: list[RangeConfig] = []
    intervals = parse_list(args.intervals, str)
    rr_pairs = parse_rr_list(args.rr)
    holds = parse_list(args.max_holds, int)
    looks = parse_list(args.lookbacks, int)
    edge_pcts = parse_list(args.edge_pcts, float)
    for interval in intervals:
        for lookback in looks:
            for edge_pct in edge_pcts:
                for stop, take in rr_pairs:
                    for hold in holds:
                        for side, trigger, rsi_filter, rsi_threshold in (
                            ("LONG", "near_low", "lte", 45.0),
                            ("SHORT", "near_high", "gte", 55.0),
                            ("LONG", "sweep_down_reclaim", "lte", 50.0),
                            ("SHORT", "sweep_up_reclaim", "gte", 50.0),
                        ):
                            strategy_id = (
                                f"range_{interval}_{side.lower()}_{trigger}_lb{lookback}_edge{edge_pct:g}"
                                f"_rr{stop:g}x{take:g}_h{hold}"
                            )
                            configs.append(
                                RangeConfig(
                                    strategy_id=strategy_id,
                                    interval=interval,
                                    side=side,
                                    trigger=trigger,
                                    lookback=lookback,
                                    edge_pct=edge_pct,
                                    min_width_atr=args.min_width_atr,
                                    max_width_atr=args.max_width_atr,
                                    max_abs_trend_atr=args.max_abs_trend_atr,
                                    max_atr_ratio=args.max_atr_ratio,
                                    rsi_filter=rsi_filter,
                                    rsi_threshold=rsi_threshold,
                                    stop_atr=stop,
                                    take_atr=take,
                                    max_hold_bars=hold,
                                )
                            )
    return configs


def rsi_ok(config: RangeConfig, rsi_value: float | None) -> bool:
    if rsi_value is None:
        return False
    if config.rsi_filter == "lte":
        return rsi_value <= config.rsi_threshold
    if config.rsi_filter == "gte":
        return rsi_value >= config.rsi_threshold
    return False


def range_signal_ok(config: RangeConfig, bars: list[Any], features: list[dict[str, Any]], rsi14: list[float | None], index: int) -> tuple[bool, dict[str, Any]]:
    high, low = previous_high_low(bars, index, config.lookback)
    if high is None or low is None or high <= low:
        return False, {}
    bar = bars[index]
    feature = features[index]
    atr = feature.get("atr")
    if atr is None or atr <= 0:
        return False, {}
    width_atr = (high - low) / atr
    trend = trend_atr(bars, features, index, config.lookback)
    atr_ratio = feature.get("atr_ratio")
    if not (config.min_width_atr <= width_atr <= config.max_width_atr):
        return False, {}
    if trend is None or abs(trend) > config.max_abs_trend_atr:
        return False, {}
    if atr_ratio is None or atr_ratio > config.max_atr_ratio:
        return False, {}
    if not rsi_ok(config, rsi14[index]):
        return False, {}

    width = high - low
    lower_edge = low + width * config.edge_pct
    upper_edge = high - width * config.edge_pct
    near_low = bar.close <= lower_edge
    near_high = bar.close >= upper_edge
    sweep_down_reclaim = bar.low < low and bar.close > low
    sweep_up_reclaim = bar.high > high and bar.close < high
    trigger_map = {
        "near_low": near_low,
        "near_high": near_high,
        "sweep_down_reclaim": sweep_down_reclaim,
        "sweep_up_reclaim": sweep_up_reclaim,
    }
    if not trigger_map.get(config.trigger, False):
        return False, {}
    if config.side == "LONG" and config.trigger in {"near_high", "sweep_up_reclaim"}:
        return False, {}
    if config.side == "SHORT" and config.trigger in {"near_low", "sweep_down_reclaim"}:
        return False, {}
    snapshot = {
        "range_high": round(high, 8),
        "range_low": round(low, 8),
        "width_atr": round(width_atr, 6),
        "trend_atr": round(trend, 6),
        "atr_ratio": None if atr_ratio is None else round(float(atr_ratio), 6),
        "rsi14": None if rsi14[index] is None else round(float(rsi14[index]), 6),
        "volume_z": feature.get("volume_z"),
        "funding": feature.get("funding"),
        "oi_delta_pct": feature.get("oi_delta_pct"),
        "spot_perp_divergence_pct": feature.get("spot_perp_divergence_pct"),
    }
    return True, snapshot


def generate_signals(config: RangeConfig, bars: list[Any], features: list[dict[str, Any]], rsi14: list[float | None], start_index: int, end_index: int) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for index in range(max(0, start_index), min(end_index, len(bars))):
        ok, snapshot = range_signal_ok(config, bars, features, rsi14, index)
        if not ok:
            continue
        atr = features[index].get("atr")
        signals.append(
            {
                "bar_index": index,
                "side_hint": config.side,
                "atr": atr,
                "reason": config.trigger,
                "feature_snapshot": snapshot,
            }
        )
    return signals


def replay_signals(config: RangeConfig, bars: list[Any], signals: list[dict[str, Any]], cost_bps_per_side: float, no_overlap: bool) -> list[Any]:
    trades = []
    last_exit_bar = -1
    for signal in sorted(signals, key=lambda item: int(item["bar_index"])):
        signal_index = int(signal["bar_index"])
        if no_overlap and signal_index <= last_exit_bar:
            continue
        trade = simulate_trade(
            dataset_id=f"range_family_BTCUSDT_{config.interval}",
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


def evaluate_config(
    config: RangeConfig,
    *,
    bars: list[Any],
    features: list[dict[str, Any]],
    rsi14: list[float | None],
    args: argparse.Namespace,
) -> dict[str, Any]:
    end_index = len(bars) - config.max_hold_bars - 1
    holdout_start = round(end_index * (1.0 - args.holdout_fraction))
    full_signals = generate_signals(config, bars, features, rsi14, 0, end_index)
    full_trades = replay_signals(config, bars, full_signals, args.cost_bps_per_side, not args.allow_overlap)
    holdout_signals = [item for item in full_signals if int(item["bar_index"]) >= holdout_start]
    holdout_trades = replay_signals(config, bars, holdout_signals, args.cost_bps_per_side, not args.allow_overlap)
    fold_rows = fold_summaries(full_trades, args.folds)
    segment_rows = []
    for idx, (start, end) in enumerate(bar_segments(len(bars), config.max_hold_bars, args.segments), start=1):
        signals = [item for item in full_signals if start <= int(item["bar_index"]) < end]
        trades = replay_signals(config, bars, signals, args.cost_bps_per_side, not args.allow_overlap)
        segment_rows.append({"segment": idx, "signals": len(signals), "summary": summarize_trades(trades)})
    stress_rows = []
    for extra in parse_list(args.cost_stress_extra_bps, float):
        trades = replay_signals(config, bars, full_signals, args.cost_bps_per_side + extra, not args.allow_overlap)
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
    if all(checks.values()):
        verdict = "range_candidate_for_forward_observation"
    elif checks["min_holdout_trades"] and checks["min_holdout_expectancy"]:
        verdict = "range_watchlist_only"
    else:
        verdict = "reject_or_research_only"
    return {
        "strategy_id": config.strategy_id,
        "interval": config.interval,
        "side": config.side,
        "trigger": config.trigger,
        "lookback": config.lookback,
        "edge_pct": config.edge_pct,
        "rr": f"{config.stop_atr:g}:{config.take_atr:g}",
        "max_hold_bars": config.max_hold_bars,
        "signals": len(full_signals),
        "full": {"summary": full_summary, "stable_folds": stable_fold_count(fold_rows), "folds": fold_rows},
        "holdout": {"start_index": holdout_start, "signals": len(holdout_signals), "summary": holdout_summary},
        "segments": segment_rows,
        "segment_positive_ratio": round(segment_ratio, 6),
        "worst_segment_expectancy_r": round(worst_segment, 6),
        "cost_stress": stress_rows,
        "checks": checks,
        "verdict": verdict,
        "sample_signals": full_signals[:3],
        "sample_trades": [trade.__dict__ for trade in full_trades[:3]],
    }


def stable_fold_count(folds: list[dict[str, Any]]) -> int:
    return sum(1 for item in folds if item.get("stable"))


def rank_key(item: dict[str, Any]) -> tuple[Any, ...]:
    ranks = {
        "range_candidate_for_forward_observation": 3,
        "range_watchlist_only": 2,
        "reject_or_research_only": 1,
    }
    return (
        ranks.get(str(item.get("verdict")), 0),
        safe_float(item.get("holdout", {}).get("summary", {}).get("expectancy_r")),
        safe_int(item.get("holdout", {}).get("summary", {}).get("trades")),
        safe_float(item.get("full", {}).get("summary", {}).get("expectancy_r")),
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Range Family Validator",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Research-only validation of BTCUSDT range mean-reversion families.",
        "- Uses cached public market data only.",
        "- Does not change the locked forward breakout strategy.",
        "- Does not grant paper or live trading permission.",
        "",
        "## Decision",
        "",
        f"- Decision: `{report['decision']}`.",
        f"- Next action: `{report['next_action']}`.",
        f"- Can trade: `{report['can_trade']}`.",
        "",
        "## Results",
        "",
        "| Verdict | Strategy | TF | Side | Trigger | RR | Signals | Full Trades | Full Exp | Full WR | Holdout Trades | Holdout Exp | Holdout WR | Seg+ | Worst Seg | Cost +10 Exp |",
        "|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report["top_results"]:
        full = item["full"]["summary"]
        holdout = item["holdout"]["summary"]
        cost10 = next((row for row in item["cost_stress"] if safe_float(row["extra_bps_per_side"], 0.0) == 10.0), None)
        lines.append(
            f"| `{item['verdict']}` | `{item['strategy_id']}` | `{item['interval']}` | `{item['side']}` | `{item['trigger']}` | `{item['rr']}` | "
            f"`{item['signals']}` | `{full.get('trades')}` | `{full.get('expectancy_r')}` | `{full.get('winrate_pct')}` | "
            f"`{holdout.get('trades')}` | `{holdout.get('expectancy_r')}` | `{holdout.get('winrate_pct')}` | "
            f"`{item['segment_positive_ratio']}` | `{item['worst_segment_expectancy_r']}` | "
            f"`{cost10['summary'].get('expectancy_r') if cost10 else None}` |"
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
            "- RANGE mode needs its own strategy family; it should not be created by weakening the breakout strategy.",
            "- A candidate here means only: eligible for forward observation, not live trading.",
            "- If no candidate passes, keep range logic as research-only and continue data/feature work.",
            "",
        ]
    )
    return "\n".join(lines)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "verdict",
        "strategy_id",
        "interval",
        "side",
        "trigger",
        "rr",
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
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in rows:
            full = item["full"]["summary"]
            holdout = item["holdout"]["summary"]
            cost10 = next((row for row in item["cost_stress"] if safe_float(row["extra_bps_per_side"], 0.0) == 10.0), None)
            writer.writerow(
                {
                    "verdict": item["verdict"],
                    "strategy_id": item["strategy_id"],
                    "interval": item["interval"],
                    "side": item["side"],
                    "trigger": item["trigger"],
                    "rr": item["rr"],
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
                }
            )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    cache_dir = resolve_path(args.cache_dir)
    configs = build_configs(args)
    payloads: dict[str, tuple[list[Any], list[dict[str, Any]], list[float | None]]] = {}
    for interval in parse_list(args.intervals, str):
        payloads[interval] = load_interval_payload(cache_dir, interval, args.oi_lag, args.spot_perp_lookback)
    results = []
    for config in configs:
        bars, features, rsi14 = payloads[config.interval]
        results.append(evaluate_config(config, bars=bars, features=features, rsi14=rsi14, args=args))
    results.sort(key=rank_key, reverse=True)
    candidates = [item for item in results if item["verdict"] == "range_candidate_for_forward_observation"]
    watchlist = [item for item in results if item["verdict"] == "range_watchlist_only"]
    if candidates:
        decision = "range_candidates_need_forward_observation"
        next_action = "add_top_range_candidate_to_observer_only_forward_comparison"
    elif watchlist:
        decision = "range_watchlist_only_no_promotion"
        next_action = "tighten_or_retest_range_features_before_forward_observation"
    else:
        decision = "no_range_candidate"
        next_action = "redesign_range_family_or_keep_breakout_only"
    return {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "mode": "research_only",
            "public_or_cached_data_only": True,
            "sends_orders": False,
            "can_trade": False,
        },
        "inputs": {"cache_dir": rel_path(cache_dir), "intervals": parse_list(args.intervals, str)},
        "settings": {
            "rr": args.rr,
            "max_holds": args.max_holds,
            "lookbacks": args.lookbacks,
            "edge_pcts": args.edge_pcts,
            "min_width_atr": args.min_width_atr,
            "max_width_atr": args.max_width_atr,
            "max_abs_trend_atr": args.max_abs_trend_atr,
            "max_atr_ratio": args.max_atr_ratio,
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
        "data": {
            interval: {
                "bars_loaded": len(payloads[interval][0]),
                "first_bar_ts": str(payloads[interval][0][0].ts) if payloads[interval][0] else None,
                "latest_bar_ts": str(payloads[interval][0][-1].ts) if payloads[interval][0] else None,
            }
            for interval in payloads
        },
        "tested": len(results),
        "candidate_count": len(candidates),
        "watchlist_count": len(watchlist),
        "top_results": results[: args.top_results],
        "results": results,
        "decision": decision,
        "next_action": next_action,
        "can_trade": False,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Research-only BTCUSDT range mean-reversion family validator")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--intervals", default="1h,4h")
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
    parser.add_argument("--top-results", type=int, default=30)
    parser.add_argument("--out-prefix", default="docs/RANGE_FAMILY_VALIDATOR_2026-06-16")
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

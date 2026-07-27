#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402
from tools.liquidity_sweep_forward_eval import compute_atr  # noqa: E402
from tools.liquidity_sweep_hardening import fold_summaries, simulate_trade, summarize_trades  # noqa: E402


@dataclass(frozen=True)
class StrategyConfig:
    strategy_id: str
    family: str
    interval: str
    params: dict[str, Any]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_list(value: str, cast: Any) -> list[Any]:
    return [cast(item.strip()) for item in value.split(",") if item.strip()]


def sma(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    total = 0.0
    for index, value in enumerate(values):
        total += value
        if index >= window:
            total -= values[index - window]
        out.append(total / window if index + 1 >= window else None)
    return out


def rolling_std(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    for index in range(len(values)):
        if index + 1 < window:
            out.append(None)
            continue
        chunk = values[index + 1 - window : index + 1]
        out.append(statistics.pstdev(chunk))
    return out


def rsi(values: list[float], length: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= length:
        return out
    gains: list[float] = []
    losses: list[float] = []
    avg_gain = 0.0
    avg_loss = 0.0
    for index in range(1, len(values)):
        delta = values[index] - values[index - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
        if index < length:
            continue
        if index == length:
            avg_gain = sum(gains[:length]) / length
            avg_loss = sum(losses[:length]) / length
        else:
            avg_gain = ((avg_gain * (length - 1)) + gains[-1]) / length
            avg_loss = ((avg_loss * (length - 1)) + losses[-1]) / length
        if avg_loss == 0:
            out[index] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[index] = 100.0 - (100.0 / (1.0 + rs))
    return out


def previous_range(bars: list[Any], index: int, lookback: int) -> tuple[float | None, float | None]:
    if index < lookback:
        return None, None
    chunk = bars[index - lookback : index]
    return max(bar.high for bar in chunk), min(bar.low for bar in chunk)


def true_range(bars: list[Any], index: int) -> float:
    bar = bars[index]
    if index == 0:
        return bar.high - bar.low
    prev_close = bars[index - 1].close
    return max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))


def build_configs(intervals: list[str], max_strategies: int) -> list[StrategyConfig]:
    configs: list[StrategyConfig] = []
    for interval in intervals:
        for lookback in (40, 80, 120):
            for low_rsi, high_rsi in ((30, 70), (25, 75)):
                params = {"lookback": lookback, "low_rsi": low_rsi, "high_rsi": high_rsi, "entry_atr": 0.35, "min_width_atr": 2.0}
                configs.append(StrategyConfig(f"range_fade_{interval}_lb{lookback}_r{low_rsi}_{high_rsi}", "range_fade", interval, params))
    for interval in intervals:
        for window in (20, 40):
            for z in (1.5, 2.0):
                params = {"window": window, "z": z, "low_rsi": 32, "high_rsi": 68}
                configs.append(StrategyConfig(f"bb_fade_{interval}_w{window}_z{z:g}", "bb_fade", interval, params))
    for interval in intervals:
        for lookback in (20, 50, 80):
            params = {"lookback": lookback, "min_wick_atr": 0.15}
            configs.append(StrategyConfig(f"sweep_reversal_{interval}_lb{lookback}", "sweep_reversal", interval, params))
    for interval in intervals:
        for atr_mult in (1.5, 2.0):
            for low_rsi, high_rsi in ((30, 70), (25, 75)):
                params = {"atr_mult": atr_mult, "low_rsi": low_rsi, "high_rsi": high_rsi}
                configs.append(StrategyConfig(f"exhaustion_reversal_{interval}_a{atr_mult:g}_r{low_rsi}_{high_rsi}", "exhaustion_reversal", interval, params))
    for interval in intervals:
        for length in (2, 3):
            for low_rsi, high_rsi in ((10, 90), (5, 95)):
                params = {"length": length, "low_rsi": low_rsi, "high_rsi": high_rsi}
                configs.append(StrategyConfig(f"rsi_fast_fade_{interval}_l{length}_r{low_rsi}_{high_rsi}", "rsi_fast_fade", interval, params))
    for interval in intervals:
        for window in (50, 100):
            for z in (1.5, 2.0, 2.5):
                params = {"window": window, "z": z}
                configs.append(StrategyConfig(f"ma_distance_fade_{interval}_w{window}_z{z:g}", "ma_distance_fade", interval, params))
    for interval in intervals:
        for lookback in (20, 40):
            for atr_ratio in (0.65, 0.8):
                params = {"lookback": lookback, "atr_ratio": atr_ratio}
                configs.append(StrategyConfig(f"compression_breakout_{interval}_lb{lookback}_ar{atr_ratio:g}", "compression_breakout", interval, params))
    for interval in intervals:
        for lookback in (3, 5):
            params = {"lookback": lookback}
            configs.append(StrategyConfig(f"inside_bar_breakout_{interval}_lb{lookback}", "inside_bar_breakout", interval, params))
    for interval in intervals:
        for volume_mult in (1.5, 2.0):
            for low_rsi, high_rsi in ((30, 70), (25, 75)):
                params = {"volume_mult": volume_mult, "low_rsi": low_rsi, "high_rsi": high_rsi, "volume_window": 20}
                configs.append(StrategyConfig(f"volume_climax_reversal_{interval}_v{volume_mult:g}_r{low_rsi}_{high_rsi}", "volume_climax_reversal", interval, params))
    return configs[:max_strategies]


def signal_range_fade(config: StrategyConfig, bars: list[Any], atr: list[float | None], rsi14: list[float | None]) -> list[dict[str, Any]]:
    lookback = int(config.params["lookback"])
    low_rsi = float(config.params["low_rsi"])
    high_rsi = float(config.params["high_rsi"])
    entry_atr = float(config.params["entry_atr"])
    min_width_atr = float(config.params["min_width_atr"])
    signals: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        if atr[index] is None or rsi14[index] is None:
            continue
        high, low = previous_range(bars, index, lookback)
        if high is None or low is None:
            continue
        width = high - low
        if width < atr[index] * min_width_atr:
            continue
        if bar.close <= low + atr[index] * entry_atr and rsi14[index] <= low_rsi:
            signals.append({"bar_index": index, "side_hint": "LONG", "atr": atr[index], "reason": "range_low_rsi_fade"})
        elif bar.close >= high - atr[index] * entry_atr and rsi14[index] >= high_rsi:
            signals.append({"bar_index": index, "side_hint": "SHORT", "atr": atr[index], "reason": "range_high_rsi_fade"})
    return signals


def signal_bb_fade(config: StrategyConfig, bars: list[Any], atr: list[float | None], rsi14: list[float | None]) -> list[dict[str, Any]]:
    closes = [bar.close for bar in bars]
    window = int(config.params["window"])
    z = float(config.params["z"])
    low_rsi = float(config.params["low_rsi"])
    high_rsi = float(config.params["high_rsi"])
    mean = sma(closes, window)
    stdev = rolling_std(closes, window)
    signals: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        if atr[index] is None or rsi14[index] is None or mean[index] is None or stdev[index] is None or stdev[index] <= 0:
            continue
        lower = mean[index] - stdev[index] * z
        upper = mean[index] + stdev[index] * z
        if bar.close < lower and rsi14[index] <= low_rsi:
            signals.append({"bar_index": index, "side_hint": "LONG", "atr": atr[index], "reason": "bb_lower_rsi_fade"})
        elif bar.close > upper and rsi14[index] >= high_rsi:
            signals.append({"bar_index": index, "side_hint": "SHORT", "atr": atr[index], "reason": "bb_upper_rsi_fade"})
    return signals


def signal_sweep_reversal(config: StrategyConfig, bars: list[Any], atr: list[float | None], rsi14: list[float | None]) -> list[dict[str, Any]]:
    del rsi14
    lookback = int(config.params["lookback"])
    min_wick_atr = float(config.params["min_wick_atr"])
    signals: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        if atr[index] is None:
            continue
        high, low = previous_range(bars, index, lookback)
        if high is None or low is None:
            continue
        min_wick = atr[index] * min_wick_atr
        if bar.high > high and bar.close < high and (bar.high - max(bar.open, bar.close)) >= min_wick:
            signals.append({"bar_index": index, "side_hint": "SHORT", "atr": atr[index], "reason": "bearish_sweep_reversal"})
        elif bar.low < low and bar.close > low and (min(bar.open, bar.close) - bar.low) >= min_wick:
            signals.append({"bar_index": index, "side_hint": "LONG", "atr": atr[index], "reason": "bullish_sweep_reversal"})
    return signals


def signal_exhaustion_reversal(config: StrategyConfig, bars: list[Any], atr: list[float | None], rsi14: list[float | None]) -> list[dict[str, Any]]:
    atr_mult = float(config.params["atr_mult"])
    low_rsi = float(config.params["low_rsi"])
    high_rsi = float(config.params["high_rsi"])
    signals: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        if atr[index] is None or rsi14[index] is None or index == 0:
            continue
        tr = true_range(bars, index)
        if tr < atr[index] * atr_mult:
            continue
        if bar.close < bar.open and rsi14[index] <= low_rsi:
            signals.append({"bar_index": index, "side_hint": "LONG", "atr": atr[index], "reason": "down_exhaustion_reversal"})
        elif bar.close > bar.open and rsi14[index] >= high_rsi:
            signals.append({"bar_index": index, "side_hint": "SHORT", "atr": atr[index], "reason": "up_exhaustion_reversal"})
    return signals


def signal_rsi_fast_fade(config: StrategyConfig, bars: list[Any], atr: list[float | None], rsi14: list[float | None]) -> list[dict[str, Any]]:
    del rsi14
    closes = [bar.close for bar in bars]
    fast_rsi = rsi(closes, int(config.params["length"]))
    low_rsi = float(config.params["low_rsi"])
    high_rsi = float(config.params["high_rsi"])
    signals: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        if atr[index] is None or fast_rsi[index] is None:
            continue
        if fast_rsi[index] <= low_rsi and bar.close < bar.open:
            signals.append({"bar_index": index, "side_hint": "LONG", "atr": atr[index], "reason": "fast_rsi_down_fade"})
        elif fast_rsi[index] >= high_rsi and bar.close > bar.open:
            signals.append({"bar_index": index, "side_hint": "SHORT", "atr": atr[index], "reason": "fast_rsi_up_fade"})
    return signals


def signal_ma_distance_fade(config: StrategyConfig, bars: list[Any], atr: list[float | None], rsi14: list[float | None]) -> list[dict[str, Any]]:
    closes = [bar.close for bar in bars]
    window = int(config.params["window"])
    z = float(config.params["z"])
    mean = sma(closes, window)
    stdev = rolling_std(closes, window)
    signals: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        if atr[index] is None or rsi14[index] is None or mean[index] is None or stdev[index] is None or stdev[index] <= 0:
            continue
        distance_z = (bar.close - mean[index]) / stdev[index]
        if distance_z <= -z and rsi14[index] <= 45:
            signals.append({"bar_index": index, "side_hint": "LONG", "atr": atr[index], "reason": "ma_distance_lower_fade"})
        elif distance_z >= z and rsi14[index] >= 55:
            signals.append({"bar_index": index, "side_hint": "SHORT", "atr": atr[index], "reason": "ma_distance_upper_fade"})
    return signals


def signal_compression_breakout(config: StrategyConfig, bars: list[Any], atr: list[float | None], rsi14: list[float | None]) -> list[dict[str, Any]]:
    del rsi14
    lookback = int(config.params["lookback"])
    atr_ratio = float(config.params["atr_ratio"])
    atr_values = [value if value is not None else math.nan for value in atr]
    atr_mean = sma([0.0 if math.isnan(value) else value for value in atr_values], lookback)
    signals: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        if atr[index] is None or atr_mean[index] is None or atr_mean[index] <= 0:
            continue
        if atr[index] / atr_mean[index] > atr_ratio:
            continue
        high, low = previous_range(bars, index, lookback)
        if high is None or low is None:
            continue
        if bar.close > high:
            signals.append({"bar_index": index, "side_hint": "LONG", "atr": atr[index], "reason": "compression_breakout_up"})
        elif bar.close < low:
            signals.append({"bar_index": index, "side_hint": "SHORT", "atr": atr[index], "reason": "compression_breakout_down"})
    return signals


def signal_inside_bar_breakout(config: StrategyConfig, bars: list[Any], atr: list[float | None], rsi14: list[float | None]) -> list[dict[str, Any]]:
    del rsi14
    lookback = int(config.params["lookback"])
    signals: list[dict[str, Any]] = []
    for index in range(lookback + 1, len(bars)):
        if atr[index] is None:
            continue
        inside = True
        mother = bars[index - lookback]
        for offset in range(index - lookback + 1, index):
            if bars[offset].high > mother.high or bars[offset].low < mother.low:
                inside = False
                break
        if not inside:
            continue
        bar = bars[index]
        if bar.close > mother.high:
            signals.append({"bar_index": index, "side_hint": "LONG", "atr": atr[index], "reason": "inside_bar_breakout_up"})
        elif bar.close < mother.low:
            signals.append({"bar_index": index, "side_hint": "SHORT", "atr": atr[index], "reason": "inside_bar_breakout_down"})
    return signals


def signal_volume_climax_reversal(config: StrategyConfig, bars: list[Any], atr: list[float | None], rsi14: list[float | None]) -> list[dict[str, Any]]:
    volumes = [bar.volume for bar in bars]
    volume_mean = sma(volumes, int(config.params["volume_window"]))
    volume_mult = float(config.params["volume_mult"])
    low_rsi = float(config.params["low_rsi"])
    high_rsi = float(config.params["high_rsi"])
    signals: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        if atr[index] is None or rsi14[index] is None or volume_mean[index] is None or volume_mean[index] <= 0:
            continue
        if bar.volume < volume_mean[index] * volume_mult:
            continue
        if bar.close < bar.open and rsi14[index] <= low_rsi:
            signals.append({"bar_index": index, "side_hint": "LONG", "atr": atr[index], "reason": "sell_climax_reversal"})
        elif bar.close > bar.open and rsi14[index] >= high_rsi:
            signals.append({"bar_index": index, "side_hint": "SHORT", "atr": atr[index], "reason": "buy_climax_reversal"})
    return signals


def generate_signals(config: StrategyConfig, bars: list[Any], atr: list[float | None], rsi14: list[float | None]) -> list[dict[str, Any]]:
    if config.family == "range_fade":
        return signal_range_fade(config, bars, atr, rsi14)
    if config.family == "bb_fade":
        return signal_bb_fade(config, bars, atr, rsi14)
    if config.family == "sweep_reversal":
        return signal_sweep_reversal(config, bars, atr, rsi14)
    if config.family == "exhaustion_reversal":
        return signal_exhaustion_reversal(config, bars, atr, rsi14)
    if config.family == "rsi_fast_fade":
        return signal_rsi_fast_fade(config, bars, atr, rsi14)
    if config.family == "ma_distance_fade":
        return signal_ma_distance_fade(config, bars, atr, rsi14)
    if config.family == "compression_breakout":
        return signal_compression_breakout(config, bars, atr, rsi14)
    if config.family == "inside_bar_breakout":
        return signal_inside_bar_breakout(config, bars, atr, rsi14)
    if config.family == "volume_climax_reversal":
        return signal_volume_climax_reversal(config, bars, atr, rsi14)
    raise ValueError(f"unsupported family: {config.family}")


def stable_fold_count(folds: list[dict[str, Any]]) -> int:
    return sum(1 for item in folds if item.get("stable"))


def polygon_verdict(summary: dict[str, Any], folds: list[dict[str, Any]], min_trades: int) -> str:
    trades = summary.get("trades") or 0
    winrate = summary.get("winrate_pct") or 0.0
    expectancy = summary.get("expectancy_r") or -999.0
    drawdown = summary.get("max_drawdown_r") or 0.0
    stable = stable_fold_count(folds)
    if trades >= min_trades and winrate >= 52.0 and expectancy >= 0.05 and stable >= 3 and drawdown >= -25.0:
        return "polygon_candidate_needs_oos"
    if trades >= max(30, min_trades // 2) and expectancy > 0 and stable >= 2:
        return "watchlist_only"
    if trades >= min_trades and expectancy < -0.05:
        return "reject_or_veto_candidate"
    return "research_only"


def evaluate_config(
    config: StrategyConfig,
    *,
    cache_dir: str,
    stop_atr: float,
    take_atr: float,
    max_hold_bars: int,
    cost_bps_per_side: float,
    folds_count: int,
    min_trades: int,
    no_overlap: bool,
) -> dict[str, Any]:
    futures_path = Path(cache_dir) / "futures" / "BTCUSDT" / f"{config.interval}_klines.csv"
    bars = load_ohlcv(futures_path)
    closes = [bar.close for bar in bars]
    atr = compute_atr(bars, 14)
    rsi14 = rsi(closes, 14)
    signals = generate_signals(config, bars, atr, rsi14)
    trades = []
    last_exit_bar = -1
    for signal in sorted(signals, key=lambda item: item["bar_index"]):
        if no_overlap and int(signal["bar_index"]) <= last_exit_bar:
            continue
        trade = simulate_trade(
            dataset_id=f"polygon_BTCUSDT_{config.interval}",
            strategy_id=config.strategy_id,
            bars=bars,
            signal=signal,
            stop_atr=stop_atr,
            take_atr=take_atr,
            max_hold_bars=max_hold_bars,
            cost_bps_per_side=cost_bps_per_side,
        )
        if trade is None:
            continue
        trades.append(trade)
        if no_overlap:
            for index in range(int(signal["bar_index"]) + 1, min(len(bars), int(signal["bar_index"]) + max_hold_bars + 2)):
                if bars[index].ts == trade.exit_ts:
                    last_exit_bar = index
                    break
    summary = summarize_trades(trades)
    folds = fold_summaries(trades, folds_count)
    return {
        "strategy_id": config.strategy_id,
        "family": config.family,
        "interval": config.interval,
        "params": config.params,
        "signals": len(signals),
        "summary": summary,
        "folds": folds,
        "stable_folds": stable_fold_count(folds),
        "verdict": polygon_verdict(summary, folds, min_trades),
        "sample_trades": [trade.__dict__ for trade in trades[:8]],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Strategy Polygon Parallel",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Runtime Boundary",
        "",
        "- Research-only parallel strategy polygon.",
        "- Tests a fixed batch of range/event-first hypotheses.",
        "- No orders are sent and no paper/live permission is granted.",
        "- Any positive result still requires separate out-of-sample validation.",
        "",
        "## Result",
        "",
        f"- Strategies requested: `{report['requested_strategies']}`.",
        f"- Strategies completed: `{report['completed_strategies']}`.",
        f"- Workers: `{report['workers']}`.",
        f"- Polygon candidates: `{report['polygon_candidate_count']}`.",
        f"- Watchlist only: `{report['watchlist_count']}`.",
        f"- Rejected/veto candidates: `{report['reject_count']}`.",
        "",
        "## Top Results",
        "",
        "| Strategy | Family | TF | Trades | Winrate | Exp R | Net R | Stable Folds | Verdict |",
        "|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in report["top_results"]:
        summary = item["summary"]
        lines.append(
            f"| `{item['strategy_id']}` | `{item['family']}` | `{item['interval']}` | "
            f"`{summary['trades']}` | `{summary['winrate_pct']}` | `{summary['expectancy_r']}` | "
            f"`{summary['net_r_total']}` | `{item['stable_folds']}` | `{item['verdict']}` |"
        )
    lines.extend(
        [
            "",
            "## Family Summary",
            "",
            "| Family | Tested | Best Exp R | Best Strategy |",
            "|---|---:|---:|---|",
        ]
    )
    for item in report["family_summary"]:
        lines.append(f"| `{item['family']}` | `{item['tested']}` | `{item['best_expectancy_r']}` | `{item['best_strategy_id']}` |")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"- Recommended next action: `{report['next_action']['id']}`.",
            f"- Reason: {report['next_action']['reason']}",
            "",
        ]
    )
    return "\n".join(lines)


def family_summary(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    families = sorted({item["family"] for item in results})
    out: list[dict[str, Any]] = []
    for family in families:
        items = [item for item in results if item["family"] == family]
        best = max(items, key=lambda item: item["summary"]["expectancy_r"] or -999.0)
        out.append(
            {
                "family": family,
                "tested": len(items),
                "best_expectancy_r": best["summary"]["expectancy_r"],
                "best_strategy_id": best["strategy_id"],
            }
        )
    return out


def choose_next_action(report: dict[str, Any]) -> dict[str, str]:
    if report["polygon_candidate_count"] > 0:
        return {
            "id": "isolate_polygon_candidates_for_oos",
            "reason": "At least one range/event-first hypothesis passed the polygon gate; isolate it for independent out-of-sample replay.",
        }
    if report["watchlist_count"] > 0:
        return {
            "id": "expand_watchlist_family_with_oos_split",
            "reason": "No strategy passed the full polygon gate, but at least one watchlist bucket has positive expectancy and stable folds.",
        }
    return {
        "id": "build_different_event_features_before_more_parameter_grid",
        "reason": "The current range/event-first polygon did not produce a robust candidate; adding blind parameter tweaks is lower value than adding better event features.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Parallel research polygon for BTCUSDT range/event-first strategy hypotheses")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--intervals", default="15m,1h,4h")
    parser.add_argument("--max-strategies", type=int, default=100)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--stop-atr", type=float, default=1.5)
    parser.add_argument("--take-atr", type=float, default=2.0)
    parser.add_argument("--max-hold-bars", type=int, default=12)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--min-trades", type=int, default=100)
    parser.add_argument("--no-overlap", dest="no_overlap", action="store_true", default=True)
    parser.add_argument("--allow-overlap", dest="no_overlap", action="store_false")
    parser.add_argument("--out-prefix", default="docs/STRATEGY_POLYGON_PARALLEL_2026-06-04")
    args = parser.parse_args()

    intervals = parse_list(args.intervals, str)
    configs = build_configs(intervals, args.max_strategies)
    cost_bps_per_side = args.fee_bps + args.slippage_bps
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    workers = max(1, min(args.workers, len(configs) or 1))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                evaluate_config,
                config,
                cache_dir=args.cache_dir,
                stop_atr=args.stop_atr,
                take_atr=args.take_atr,
                max_hold_bars=args.max_hold_bars,
                cost_bps_per_side=cost_bps_per_side,
                folds_count=args.folds,
                min_trades=args.min_trades,
                no_overlap=args.no_overlap,
            ): config
            for config in configs
        }
        for future in as_completed(futures):
            config = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                errors.append({"strategy_id": config.strategy_id, "error": str(exc)})

    ranked = sorted(
        results,
        key=lambda item: (
            1 if item["verdict"] == "polygon_candidate_needs_oos" else 0,
            1 if item["verdict"] == "watchlist_only" else 0,
            item["summary"]["expectancy_r"] or -999.0,
            item["stable_folds"],
            item["summary"]["trades"],
        ),
        reverse=True,
    )
    report: dict[str, Any] = {
        "generated_at": now_iso(),
        "decision": (
            "polygon_candidate_needs_oos"
            if sum(1 for item in results if item["verdict"] == "polygon_candidate_needs_oos") > 0
            else "polygon_watchlist_only"
            if sum(1 for item in results if item["verdict"] == "watchlist_only") > 0
            else "reject_current_polygon_grid"
        ),
        "runtime_boundary": {
            "classification": "research_polygon_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "requested_strategies": len(configs),
        "completed_strategies": len(results),
        "workers": workers,
        "settings": {
            "intervals": intervals,
            "stop_atr": args.stop_atr,
            "take_atr": args.take_atr,
            "max_hold_bars": args.max_hold_bars,
            "fee_bps": args.fee_bps,
            "slippage_bps": args.slippage_bps,
            "folds": args.folds,
            "min_trades": args.min_trades,
            "no_overlap": args.no_overlap,
        },
        "polygon_candidate_count": sum(1 for item in results if item["verdict"] == "polygon_candidate_needs_oos"),
        "watchlist_count": sum(1 for item in results if item["verdict"] == "watchlist_only"),
        "reject_count": sum(1 for item in results if item["verdict"] == "reject_or_veto_candidate"),
        "errors": errors,
        "top_results": ranked[:20],
        "family_summary": family_summary(results),
        "all_results": results,
        "can_trade": False,
    }
    report["next_action"] = choose_next_action(report)
    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(
        {
            "requested_strategies": report["requested_strategies"],
            "completed_strategies": report["completed_strategies"],
            "workers": workers,
            "polygon_candidate_count": report["polygon_candidate_count"],
            "watchlist_count": report["watchlist_count"],
            "reject_count": report["reject_count"],
            "next_action": report["next_action"],
            "top_results": [
                {
                    "strategy_id": item["strategy_id"],
                    "family": item["family"],
                    "interval": item["interval"],
                    "summary": item["summary"],
                    "stable_folds": item["stable_folds"],
                    "verdict": item["verdict"],
                }
                for item in ranked[:5]
            ],
            "json": str(out_prefix.with_suffix(".json")),
            "md": str(out_prefix.with_suffix(".md")),
            "can_trade": False,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

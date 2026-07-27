#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
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
class FeatureConfig:
    strategy_id: str
    family: str
    interval: str
    params: dict[str, Any]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_list(value: str, cast: Any) -> list[Any]:
    return [cast(item.strip()) for item in value.split(",") if item.strip()]


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(str(value).strip())
    except ValueError:
        return None
    return None if math.isnan(out) else out


def load_csv_by_time(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {str(row.get("time") or row.get("timestamp") or "").strip(): row for row in csv.DictReader(handle)}


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
        out.append(statistics.pstdev(values[index + 1 - window : index + 1]))
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


def oi_delta(rows_by_time: dict[str, dict[str, str]], times: list[str], index: int, lag: int) -> float | None:
    if index < lag:
        return None
    current = rows_by_time.get(times[index])
    previous = rows_by_time.get(times[index - lag])
    if current is None or previous is None:
        return None
    current_oi = safe_float(current.get("open_interest"))
    previous_oi = safe_float(previous.get("open_interest"))
    if current_oi is None or previous_oi is None or previous_oi == 0:
        return None
    return (current_oi - previous_oi) / previous_oi * 100.0


def spot_perp_divergence(spot_by_time: dict[str, Any], bars: list[Any], index: int, lookback: int) -> float | None:
    if index < lookback:
        return None
    now_perp = bars[index]
    prev_perp = bars[index - lookback]
    now_spot = spot_by_time.get(now_perp.ts)
    prev_spot = spot_by_time.get(prev_perp.ts)
    if now_spot is None or prev_spot is None or prev_perp.close <= 0 or prev_spot.close <= 0:
        return None
    perp_ret = (now_perp.close - prev_perp.close) / prev_perp.close * 100.0
    spot_ret = (now_spot.close - prev_spot.close) / prev_spot.close * 100.0
    return spot_ret - perp_ret


def inside_cluster_breakout(bars: list[Any], index: int, cluster: int) -> tuple[bool, bool]:
    if index < cluster + 1:
        return False, False
    mother = bars[index - cluster]
    for offset in range(index - cluster + 1, index):
        if bars[offset].high > mother.high or bars[offset].low < mother.low:
            return False, False
    bar = bars[index]
    return bar.close > mother.high, bar.close < mother.low


def build_features(
    *,
    bars: list[Any],
    spot_by_time: dict[str, Any],
    derivatives_by_time: dict[str, dict[str, str]],
    oi_lag: int,
    spot_perp_lookback: int,
    volume_window: int,
    atr_window: int,
) -> list[dict[str, Any]]:
    atr = compute_atr(bars, 14)
    atr_for_ma = [value or 0.0 for value in atr]
    atr_mean = sma(atr_for_ma, atr_window)
    volumes = [bar.volume for bar in bars]
    volume_mean = sma(volumes, volume_window)
    volume_std = rolling_std(volumes, volume_window)
    times = [bar.ts for bar in bars]
    features: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        candle_range = max(bar.high - bar.low, 0.0)
        body = abs(bar.close - bar.open)
        close_location = (bar.close - bar.low) / candle_range if candle_range > 0 else 0.5
        upper_wick = bar.high - max(bar.open, bar.close)
        lower_wick = min(bar.open, bar.close) - bar.low
        atr_value = atr[index]
        atr_ratio = None
        if atr_value is not None and atr_mean[index] is not None and atr_mean[index] > 0:
            atr_ratio = atr_value / atr_mean[index]
        vol_z = None
        if volume_mean[index] is not None and volume_std[index] is not None and volume_std[index] > 0:
            vol_z = (bar.volume - volume_mean[index]) / volume_std[index]
        derivatives_row = derivatives_by_time.get(bar.ts, {})
        features.append(
            {
                "index": index,
                "ts": bar.ts,
                "atr": atr_value,
                "atr_ratio": atr_ratio,
                "volume_z": vol_z,
                "body_pct": body / candle_range if candle_range > 0 else 0.0,
                "close_location": close_location,
                "range_atr": true_range(bars, index) / atr_value if atr_value and atr_value > 0 else None,
                "upper_wick_atr": upper_wick / atr_value if atr_value and atr_value > 0 else None,
                "lower_wick_atr": lower_wick / atr_value if atr_value and atr_value > 0 else None,
                "funding": safe_float(derivatives_row.get("funding")),
                "oi_delta_pct": oi_delta(derivatives_by_time, times, index, oi_lag),
                "spot_perp_divergence_pct": spot_perp_divergence(spot_by_time, bars, index, spot_perp_lookback),
            }
        )
    return features


def feature_ok(value: float | None, threshold: float, op: str) -> bool:
    if value is None:
        return False
    if op == ">=":
        return value >= threshold
    if op == "<=":
        return value <= threshold
    raise ValueError(f"unsupported op: {op}")


def build_configs(intervals: list[str], max_strategies: int) -> list[FeatureConfig]:
    configs: list[FeatureConfig] = []
    for interval in intervals:
        for lookback in (20, 40):
            for atr_ratio in (0.7, 0.85):
                for min_volume_z in (-0.25, 0.25):
                    params = {"lookback": lookback, "max_atr_ratio": atr_ratio, "min_volume_z": min_volume_z, "min_body_pct": 0.35}
                    configs.append(
                        FeatureConfig(
                            f"ff_compression_accept_{interval}_lb{lookback}_ar{atr_ratio:g}_vz{min_volume_z:g}",
                            "compression_acceptance_breakout",
                            interval,
                            params,
                        )
                    )
    for interval in intervals:
        for cluster in (3, 5):
            for min_volume_z in (-0.25, 0.25):
                params = {"cluster": cluster, "min_volume_z": min_volume_z, "min_body_pct": 0.30}
                configs.append(FeatureConfig(f"ff_inside_accept_{interval}_c{cluster}_vz{min_volume_z:g}", "inside_acceptance_breakout", interval, params))
    for interval in intervals:
        for lookback in (20, 50):
            for min_volume_z in (-0.25, 0.5):
                params = {"lookback": lookback, "min_volume_z": min_volume_z, "min_wick_atr": 0.10}
                configs.append(FeatureConfig(f"ff_false_reclaim_{interval}_lb{lookback}_vz{min_volume_z:g}", "false_breakout_reclaim", interval, params))
    for interval in intervals:
        for lookback in (20, 40):
            for min_spot_div in (0.0, 0.03):
                params = {"lookback": lookback, "min_body_pct": 0.30, "min_spot_div_abs": min_spot_div}
                configs.append(FeatureConfig(f"ff_spot_confirm_breakout_{interval}_lb{lookback}_sd{min_spot_div:g}", "spot_confirmed_breakout", interval, params))
    for interval in intervals:
        for lookback in (20, 40):
            for min_oi_delta in (0.0, 0.05):
                params = {"lookback": lookback, "max_atr_ratio": 0.9, "min_oi_delta_pct": min_oi_delta, "min_body_pct": 0.30}
                configs.append(FeatureConfig(f"ff_oi_compression_breakout_{interval}_lb{lookback}_oi{min_oi_delta:g}", "oi_compression_breakout", interval, params))
    for interval in intervals:
        for lookback in (20, 50):
            for min_range_atr in (1.2, 1.8):
                params = {"lookback": lookback, "min_volume_z": 0.5, "min_range_atr": min_range_atr, "min_wick_atr": 0.10}
                configs.append(FeatureConfig(f"ff_climax_reclaim_{interval}_lb{lookback}_ra{min_range_atr:g}", "climax_reclaim", interval, params))
    return configs[:max_strategies]


def append_breakout_signal(
    *,
    signals: list[dict[str, Any]],
    index: int,
    side: str,
    feature: dict[str, Any],
    reason: str,
) -> None:
    atr_value = feature.get("atr")
    if atr_value is None or atr_value <= 0:
        return
    signals.append(
        {
            "bar_index": index,
            "side_hint": side,
            "atr": atr_value,
            "reason": reason,
            "feature_snapshot": {
                "atr_ratio": None if feature.get("atr_ratio") is None else round(float(feature["atr_ratio"]), 6),
                "volume_z": None if feature.get("volume_z") is None else round(float(feature["volume_z"]), 6),
                "body_pct": round(float(feature.get("body_pct") or 0.0), 6),
                "close_location": round(float(feature.get("close_location") or 0.0), 6),
                "range_atr": None if feature.get("range_atr") is None else round(float(feature["range_atr"]), 6),
                "oi_delta_pct": None if feature.get("oi_delta_pct") is None else round(float(feature["oi_delta_pct"]), 6),
                "spot_perp_divergence_pct": None
                if feature.get("spot_perp_divergence_pct") is None
                else round(float(feature["spot_perp_divergence_pct"]), 6),
            },
        }
    )


def generate_signals(config: FeatureConfig, bars: list[Any], features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        feature = features[index]
        if feature.get("atr") is None:
            continue

        if config.family == "inside_acceptance_breakout":
            cluster = int(config.params["cluster"])
            up, down = inside_cluster_breakout(bars, index, cluster)
            if not feature_ok(feature.get("volume_z"), float(config.params["min_volume_z"]), ">="):
                continue
            if float(feature.get("body_pct") or 0.0) < float(config.params["min_body_pct"]):
                continue
            if up and float(feature.get("close_location") or 0.0) >= 0.65:
                append_breakout_signal(signals=signals, index=index, side="LONG", feature=feature, reason="inside_acceptance_breakout_up")
            elif down and float(feature.get("close_location") or 1.0) <= 0.35:
                append_breakout_signal(signals=signals, index=index, side="SHORT", feature=feature, reason="inside_acceptance_breakout_down")
            continue

        lookback = int(config.params.get("lookback", 20))
        high, low = previous_range(bars, index, lookback)
        if high is None or low is None:
            continue
        breakout_up = bar.close > high
        breakout_down = bar.close < low
        sweep_up_reclaim = bar.high > high and bar.close < high
        sweep_down_reclaim = bar.low < low and bar.close > low

        if config.family == "compression_acceptance_breakout":
            if not feature_ok(feature.get("atr_ratio"), float(config.params["max_atr_ratio"]), "<="):
                continue
            if not feature_ok(feature.get("volume_z"), float(config.params["min_volume_z"]), ">="):
                continue
            if float(feature.get("body_pct") or 0.0) < float(config.params["min_body_pct"]):
                continue
            if breakout_up and float(feature.get("close_location") or 0.0) >= 0.70:
                append_breakout_signal(signals=signals, index=index, side="LONG", feature=feature, reason="compression_acceptance_breakout_up")
            elif breakout_down and float(feature.get("close_location") or 1.0) <= 0.30:
                append_breakout_signal(signals=signals, index=index, side="SHORT", feature=feature, reason="compression_acceptance_breakout_down")
            continue

        if config.family == "false_breakout_reclaim":
            if not feature_ok(feature.get("volume_z"), float(config.params["min_volume_z"]), ">="):
                continue
            min_wick = float(config.params["min_wick_atr"])
            if sweep_up_reclaim and feature_ok(feature.get("upper_wick_atr"), min_wick, ">="):
                append_breakout_signal(signals=signals, index=index, side="SHORT", feature=feature, reason="false_up_breakout_reclaim_short")
            elif sweep_down_reclaim and feature_ok(feature.get("lower_wick_atr"), min_wick, ">="):
                append_breakout_signal(signals=signals, index=index, side="LONG", feature=feature, reason="false_down_breakout_reclaim_long")
            continue

        if config.family == "spot_confirmed_breakout":
            if float(feature.get("body_pct") or 0.0) < float(config.params["min_body_pct"]):
                continue
            min_div = float(config.params["min_spot_div_abs"])
            divergence = feature.get("spot_perp_divergence_pct")
            if breakout_up and feature_ok(divergence, min_div, ">=") and float(feature.get("close_location") or 0.0) >= 0.65:
                append_breakout_signal(signals=signals, index=index, side="LONG", feature=feature, reason="spot_confirmed_breakout_up")
            elif breakout_down and feature_ok(divergence, -min_div, "<=") and float(feature.get("close_location") or 1.0) <= 0.35:
                append_breakout_signal(signals=signals, index=index, side="SHORT", feature=feature, reason="spot_confirmed_breakout_down")
            continue

        if config.family == "oi_compression_breakout":
            if not feature_ok(feature.get("atr_ratio"), float(config.params["max_atr_ratio"]), "<="):
                continue
            if not feature_ok(feature.get("oi_delta_pct"), float(config.params["min_oi_delta_pct"]), ">="):
                continue
            if float(feature.get("body_pct") or 0.0) < float(config.params["min_body_pct"]):
                continue
            if breakout_up and float(feature.get("close_location") or 0.0) >= 0.65:
                append_breakout_signal(signals=signals, index=index, side="LONG", feature=feature, reason="oi_compression_breakout_up")
            elif breakout_down and float(feature.get("close_location") or 1.0) <= 0.35:
                append_breakout_signal(signals=signals, index=index, side="SHORT", feature=feature, reason="oi_compression_breakout_down")
            continue

        if config.family == "climax_reclaim":
            if not feature_ok(feature.get("volume_z"), float(config.params["min_volume_z"]), ">="):
                continue
            if not feature_ok(feature.get("range_atr"), float(config.params["min_range_atr"]), ">="):
                continue
            min_wick = float(config.params["min_wick_atr"])
            if sweep_up_reclaim and feature_ok(feature.get("upper_wick_atr"), min_wick, ">="):
                append_breakout_signal(signals=signals, index=index, side="SHORT", feature=feature, reason="climax_up_reclaim_short")
            elif sweep_down_reclaim and feature_ok(feature.get("lower_wick_atr"), min_wick, ">="):
                append_breakout_signal(signals=signals, index=index, side="LONG", feature=feature, reason="climax_down_reclaim_long")
            continue

        raise ValueError(f"unsupported family: {config.family}")
    return signals


def stable_fold_count(folds: list[dict[str, Any]]) -> int:
    return sum(1 for item in folds if item.get("stable"))


def verdict(summary: dict[str, Any], folds: list[dict[str, Any]], min_trades: int) -> str:
    trades = summary.get("trades") or 0
    winrate = summary.get("winrate_pct") or 0.0
    expectancy = summary.get("expectancy_r") or -999.0
    drawdown = summary.get("max_drawdown_r") or 0.0
    stable = stable_fold_count(folds)
    if trades >= min_trades and winrate >= 52.0 and expectancy >= 0.05 and stable >= 3 and drawdown >= -25.0:
        return "feature_candidate_needs_oos"
    if trades >= max(30, min_trades // 2) and expectancy > 0 and stable >= 2:
        return "watchlist_only"
    if trades >= min_trades and expectancy < -0.05:
        return "reject_or_veto_candidate"
    return "research_only"


def evaluate_config(
    config: FeatureConfig,
    *,
    cache_dir: str,
    stop_atr: float,
    take_atr: float,
    max_hold_bars: int,
    cost_bps_per_side: float,
    folds_count: int,
    min_trades: int,
    no_overlap: bool,
    oi_lag: int,
    spot_perp_lookback: int,
) -> dict[str, Any]:
    futures_path = Path(cache_dir) / "futures" / "BTCUSDT" / f"{config.interval}_klines.csv"
    spot_path = Path(cache_dir) / "spot" / "BTCUSDT" / f"{config.interval}_klines.csv"
    derivatives_path = Path(cache_dir) / "futures" / "BTCUSDT" / f"{config.interval}_oi_aligned.csv"
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
    signals = generate_signals(config, bars, features)
    trades = []
    last_exit_bar = -1
    for signal in sorted(signals, key=lambda item: item["bar_index"]):
        if no_overlap and int(signal["bar_index"]) <= last_exit_bar:
            continue
        trade = simulate_trade(
            dataset_id=f"feature_factory_BTCUSDT_{config.interval}",
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
            for offset in range(int(signal["bar_index"]) + 1, min(len(bars), int(signal["bar_index"]) + max_hold_bars + 2)):
                if bars[offset].ts == trade.exit_ts:
                    last_exit_bar = offset
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
        "verdict": verdict(summary, folds, min_trades),
        "sample_signals": signals[:8],
        "sample_trades": [trade.__dict__ for trade in trades[:8]],
        "data_contract": {
            "futures_ohlcv": str(futures_path),
            "spot_ohlcv_exists": spot_path.exists(),
            "derivatives_exists": derivatives_path.exists(),
            "spot_rows": len(spot_bars),
            "derivatives_rows": len(derivatives_by_time),
        },
    }


def family_summary(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for family in sorted({item["family"] for item in results}):
        items = [item for item in results if item["family"] == family]
        best = max(items, key=lambda item: item["summary"]["expectancy_r"] or -999.0)
        out.append(
            {
                "family": family,
                "tested": len(items),
                "best_expectancy_r": best["summary"]["expectancy_r"],
                "best_strategy_id": best["strategy_id"],
                "best_trades": best["summary"]["trades"],
                "best_winrate_pct": best["summary"]["winrate_pct"],
            }
        )
    return out


def choose_next_action(report: dict[str, Any]) -> dict[str, str]:
    if report["candidate_count"] > 0:
        return {
            "id": "run_independent_oos_on_feature_candidates",
            "reason": "At least one engineered feature candidate passed the in-sample feature gate; it still needs independent OOS replay before paper.",
        }
    if report["watchlist_count"] > 0:
        return {
            "id": "turn_watchlist_features_into_oos_hypotheses",
            "reason": "Some engineered feature buckets are positive but not robust enough; isolate them and test on fresh windows.",
        }
    return {
        "id": "derive_new_features_from_processed_docs_before_more_grid",
        "reason": "Current engineered price/OI/spot features did not produce a robust candidate. Next value is extracting more concrete feature definitions from processed docs.",
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Event Feature Factory",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Runtime Boundary",
        "",
        "- Research-only event feature factory.",
        "- Uses closed-bar features and simulates entry on the next bar open.",
        "- No private credentials, no orders, no paper/live permission.",
        "- A positive result still requires independent OOS and paper execution proof.",
        "",
        "## Result",
        "",
        f"- Feature hypotheses requested: `{report['requested']}`.",
        f"- Completed: `{report['completed']}`.",
        f"- Workers: `{report['workers']}`.",
        f"- Candidates needing OOS: `{report['candidate_count']}`.",
        f"- Watchlist only: `{report['watchlist_count']}`.",
        f"- Reject/veto: `{report['reject_count']}`.",
        "",
        "## Top Results",
        "",
        "| Strategy | Family | TF | Signals | Trades | Winrate | Exp R | Net R | Stable Folds | Verdict |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in report["top_results"]:
        summary = item["summary"]
        lines.append(
            f"| `{item['strategy_id']}` | `{item['family']}` | `{item['interval']}` | `{item['signals']}` | "
            f"`{summary['trades']}` | `{summary['winrate_pct']}` | `{summary['expectancy_r']}` | "
            f"`{summary['net_r_total']}` | `{item['stable_folds']}` | `{item['verdict']}` |"
        )
    lines.extend(["", "## Family Summary", "", "| Family | Tested | Best Strategy | Best Trades | Best Winrate | Best Exp R |", "|---|---:|---|---:|---:|---:|"])
    for item in report["family_summary"]:
        lines.append(
            f"| `{item['family']}` | `{item['tested']}` | `{item['best_strategy_id']}` | "
            f"`{item['best_trades']}` | `{item['best_winrate_pct']}` | `{item['best_expectancy_r']}` |"
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"- Next action: `{report['next_action']['id']}`.",
            f"- Reason: {report['next_action']['reason']}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Research-only event feature factory for BTCUSDT")
    parser.add_argument("--cache-dir", default="data/cache/binance")
    parser.add_argument("--intervals", default="15m,1h,4h")
    parser.add_argument("--max-strategies", type=int, default=72)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--stop-atr", type=float, default=1.5)
    parser.add_argument("--take-atr", type=float, default=2.0)
    parser.add_argument("--max-hold-bars", type=int, default=12)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--min-trades", type=int, default=100)
    parser.add_argument("--oi-lag", type=int, default=4)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--allow-overlap", action="store_true")
    parser.add_argument("--out-prefix", default="docs/EVENT_FEATURE_FACTORY_2026-06-04")
    args = parser.parse_args()

    configs = build_configs(parse_list(args.intervals, str), args.max_strategies)
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(
                evaluate_config,
                config,
                cache_dir=args.cache_dir,
                stop_atr=args.stop_atr,
                take_atr=args.take_atr,
                max_hold_bars=args.max_hold_bars,
                cost_bps_per_side=args.fee_bps + args.slippage_bps,
                folds_count=args.folds,
                min_trades=args.min_trades,
                no_overlap=not args.allow_overlap,
                oi_lag=args.oi_lag,
                spot_perp_lookback=args.spot_perp_lookback,
            ): config
            for config in configs
        }
        for future in as_completed(futures):
            config = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                errors.append({"strategy_id": config.strategy_id, "error": str(exc)})

    results.sort(
        key=lambda item: (
            item["verdict"] == "feature_candidate_needs_oos",
            item["verdict"] == "watchlist_only",
            item["summary"]["expectancy_r"] or -999.0,
            item["stable_folds"],
            item["summary"]["trades"] or 0,
        ),
        reverse=True,
    )
    report: dict[str, Any] = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "research_feature_factory_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "requested": len(configs),
        "completed": len(results),
        "workers": args.workers,
        "settings": {
            "intervals": parse_list(args.intervals, str),
            "stop_atr": args.stop_atr,
            "take_atr": args.take_atr,
            "max_hold_bars": args.max_hold_bars,
            "fee_bps": args.fee_bps,
            "slippage_bps": args.slippage_bps,
            "folds": args.folds,
            "min_trades": args.min_trades,
            "oi_lag": args.oi_lag,
            "spot_perp_lookback": args.spot_perp_lookback,
            "no_overlap": not args.allow_overlap,
        },
        "candidate_count": sum(1 for item in results if item["verdict"] == "feature_candidate_needs_oos"),
        "watchlist_count": sum(1 for item in results if item["verdict"] == "watchlist_only"),
        "reject_count": sum(1 for item in results if item["verdict"] == "reject_or_veto_candidate"),
        "errors": errors,
        "top_results": results[:20],
        "family_summary": family_summary(results) if results else [],
    }
    report["next_action"] = choose_next_action(report)

    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "requested": report["requested"],
                "completed": report["completed"],
                "candidate_count": report["candidate_count"],
                "watchlist_count": report["watchlist_count"],
                "reject_count": report["reject_count"],
                "top": report["top_results"][0]["strategy_id"] if report["top_results"] else None,
                "top_summary": report["top_results"][0]["summary"] if report["top_results"] else None,
                "next_action": report["next_action"],
                "json": str(json_path),
                "md": str(md_path),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

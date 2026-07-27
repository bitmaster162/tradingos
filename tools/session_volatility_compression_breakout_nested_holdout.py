#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402
from tools.liquidity_sweep_forward_eval import compute_atr  # noqa: E402
from tools.liquidity_sweep_hardening import simulate_trade, summarize_trades  # noqa: E402


@dataclass(frozen=True)
class SessionWindow:
    name: str
    start_hour: int
    end_hour: int


@dataclass(frozen=True)
class CompressionConfig:
    strategy_id: str
    interval: str
    side: str
    session: SessionWindow
    lookback: int
    max_range_atr: float
    breakout_buffer_atr: float
    min_volume_z: float
    trend_filter: str
    stop_atr: float
    take_atr: float
    max_hold_bars: int


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def parse_ts(value: str) -> datetime:
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def ema(values: list[float], length: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (length + 1.0)
    out: list[float] = []
    current = values[0]
    for value in values:
        current = alpha * value + (1.0 - alpha) * current
        out.append(current)
    return out


def rolling_mean(values: list[float], window: int) -> list[float | None]:
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


def volume_zscores(volumes: list[float], window: int) -> list[float | None]:
    means = rolling_mean(volumes, window)
    stds = rolling_std(volumes, window)
    out: list[float | None] = []
    for index, volume in enumerate(volumes):
        mean = means[index]
        std = stds[index]
        out.append((volume - mean) / std if mean is not None and std is not None and std > 0 else None)
    return out


def in_session(ts: str, window: SessionWindow) -> bool:
    hour = parse_ts(ts).hour
    if window.start_hour <= window.end_hour:
        return window.start_hour <= hour <= window.end_hour
    return hour >= window.start_hour or hour <= window.end_hour


def prior_range(bars: list[Any], index: int, lookback: int) -> tuple[float, float] | None:
    if index < lookback:
        return None
    chunk = bars[index - lookback : index]
    return max(bar.high for bar in chunk), min(bar.low for bar in chunk)


def trend_ok(config: CompressionConfig, closes: list[float], ema50: list[float], ema200: list[float], index: int) -> bool:
    if config.trend_filter == "none":
        return True
    if index < 12:
        return False
    if config.side == "LONG":
        if config.trend_filter == "ema50_slope":
            return closes[index] > ema50[index] and ema50[index] > ema50[index - 6]
        if config.trend_filter == "ema50_stack":
            return closes[index] > ema50[index] > ema200[index]
    if config.side == "SHORT":
        if config.trend_filter == "ema50_slope":
            return closes[index] < ema50[index] and ema50[index] < ema50[index - 6]
        if config.trend_filter == "ema50_stack":
            return closes[index] < ema50[index] < ema200[index]
    raise ValueError(f"unsupported trend_filter={config.trend_filter}")


def generate_signals(config: CompressionConfig, bars: list[Any], features: dict[str, list[Any]]) -> list[dict[str, Any]]:
    closes = features["closes"]
    ema50 = features["ema50"]
    ema200 = features["ema200"]
    atr = features["atr"]
    volume_z = features["volume_z"]
    signals: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        if index + config.max_hold_bars + 1 >= len(bars):
            continue
        if not in_session(str(bar.ts), config.session):
            continue
        atr_value = atr[index]
        vol_z = volume_z[index]
        if atr_value is None or atr_value <= 0 or vol_z is None:
            continue
        if vol_z < config.min_volume_z:
            continue
        rng = prior_range(bars, index, config.lookback)
        if rng is None:
            continue
        prev_high, prev_low = rng
        range_atr = (prev_high - prev_low) / atr_value
        if range_atr > config.max_range_atr:
            continue
        if not trend_ok(config, closes, ema50, ema200, index):
            continue
        buffer = config.breakout_buffer_atr * atr_value
        if config.side == "LONG":
            if bar.close <= prev_high + buffer:
                continue
            close_location = (bar.close - bar.low) / max(bar.high - bar.low, 1e-12)
            if close_location < 0.60:
                continue
        else:
            if bar.close >= prev_low - buffer:
                continue
            close_location = (bar.close - bar.low) / max(bar.high - bar.low, 1e-12)
            if close_location > 0.40:
                continue
        signals.append(
            {
                "bar_index": index,
                "side_hint": config.side,
                "atr": float(atr_value),
                "reason": "session_volatility_compression_breakout",
                "range_atr": round(range_atr, 6),
                "volume_z": round(float(vol_z), 6),
                "session": config.session.name,
            }
        )
    return signals


def replay(config: CompressionConfig, bars: list[Any], signals: list[dict[str, Any]], cost_bps_per_side: float) -> list[Any]:
    trades: list[Any] = []
    last_exit_index = -1
    bar_index_by_ts = {str(bar.ts): index for index, bar in enumerate(bars)}
    for signal in sorted(signals, key=lambda row: int(row["bar_index"])):
        signal_index = int(signal["bar_index"])
        if signal_index <= last_exit_index:
            continue
        trade = simulate_trade(
            dataset_id=f"session_vol_comp_BTCUSDT_{config.interval}",
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
        last_exit_index = bar_index_by_ts.get(str(trade.exit_ts), signal_index)
    return trades


def fold_summaries_expectancy(trades: list[Any], folds: int) -> list[dict[str, Any]]:
    if not trades:
        return []
    ordered = sorted(trades, key=lambda item: item.entry_ts)
    out: list[dict[str, Any]] = []
    for fold in range(folds):
        start = round(len(ordered) * fold / folds)
        end = round(len(ordered) * (fold + 1) / folds)
        chunk = ordered[start:end]
        summary = summarize_trades(chunk)
        summary["fold"] = fold + 1
        summary["stable"] = bool(summary["trades"] >= 10 and (summary["expectancy_r"] or 0.0) > 0)
        out.append(summary)
    return out


def bootstrap_positive_probability(values: list[float], iterations: int = 1000, seed: int = 20260630) -> float | None:
    if not values:
        return None
    rng = random.Random(seed)
    positive = 0
    for _ in range(iterations):
        sample_mean = statistics.mean(rng.choice(values) for _ in values)
        positive += int(sample_mean > 0.0)
    return round(positive / iterations, 6)


def split_bars(bars: list[Any], start: datetime | None, end: datetime | None) -> list[Any]:
    out: list[Any] = []
    for bar in bars:
        ts = parse_ts(str(bar.ts))
        if start is not None and ts < start:
            continue
        if end is not None and ts >= end:
            continue
        out.append(bar)
    return out


def build_features_for_bars(bars: list[Any]) -> dict[str, list[Any]]:
    closes = [bar.close for bar in bars]
    return {
        "closes": closes,
        "ema50": ema(closes, 50),
        "ema200": ema(closes, 200),
        "atr": compute_atr(bars, 14),
        "volume_z": volume_zscores([bar.volume for bar in bars], 48),
    }


def evaluate_window(
    config: CompressionConfig,
    bars: list[Any],
    features: dict[str, list[Any]],
    cost_bps_per_side: float,
    stress_extra_bps: float,
    folds: int,
) -> dict[str, Any]:
    if len(bars) < max(config.lookback + config.max_hold_bars + 50, 250):
        return {
            "signals": 0,
            "summary": summarize_trades([]),
            "stable_folds": 0,
            "folds": [],
            "cost_stress": {"extra_bps_per_side": stress_extra_bps, "summary": summarize_trades([])},
            "bootstrap_probability_expectancy_gt_0": None,
            "trades": [],
        }
    signals = generate_signals(config, bars, features)
    trades = replay(config, bars, signals, cost_bps_per_side)
    stress_trades = replay(config, bars, signals, cost_bps_per_side + stress_extra_bps)
    summary = summarize_trades(trades)
    folds_payload = fold_summaries_expectancy(trades, folds)
    stable_folds = sum(1 for row in folds_payload if row.get("stable"))
    bootstrap_allowed = int(summary.get("trades") or 0) >= 50 and (summary.get("expectancy_r") or -999.0) > 0
    return {
        "signals": len(signals),
        "summary": summary,
        "stable_folds": stable_folds,
        "folds": folds_payload,
        "cost_stress": {"extra_bps_per_side": stress_extra_bps, "summary": summarize_trades(stress_trades)},
        "bootstrap_probability_expectancy_gt_0": bootstrap_positive_probability([trade.r_net for trade in trades]) if bootstrap_allowed else None,
        "trades": [asdict(trade) for trade in trades],
    }


def train_gate(window: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    summary = window["summary"]
    stress = window["cost_stress"]["summary"]
    checks = {
        "min_trades": int(summary.get("trades") or 0) >= args.train_min_trades,
        "min_expectancy_r": float(summary.get("expectancy_r") or -999.0) >= args.train_min_expectancy_r,
        "min_stable_folds": int(window.get("stable_folds") or 0) >= args.train_min_stable_folds,
        "max_drawdown_r": float(summary.get("max_drawdown_r") or -999.0) >= -abs(args.train_max_drawdown_r),
        "bootstrap_probability": float(window.get("bootstrap_probability_expectancy_gt_0") or 0.0) >= args.train_min_bootstrap_p,
        "cost_stress_positive": float(stress.get("expectancy_r") or -999.0) > 0.0,
    }
    return {"pass": all(checks.values()), "checks": checks}


def validation_gate(window: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    summary = window["summary"]
    stress = window["cost_stress"]["summary"]
    checks = {
        "min_trades": int(summary.get("trades") or 0) >= args.validation_min_trades,
        "min_expectancy_r": float(summary.get("expectancy_r") or -999.0) >= args.validation_min_expectancy_r,
        "min_stable_folds": int(window.get("stable_folds") or 0) >= args.validation_min_stable_folds,
        "max_drawdown_r": float(summary.get("max_drawdown_r") or -999.0) >= -abs(args.validation_max_drawdown_r),
        "cost_stress_positive": float(stress.get("expectancy_r") or -999.0) > 0.0,
    }
    return {"pass": all(checks.values()), "checks": checks}


def oos_gate(window: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    summary = window["summary"]
    stress = window["cost_stress"]["summary"]
    checks = {
        "min_trades": int(summary.get("trades") or 0) >= args.oos_min_trades,
        "min_expectancy_r": float(summary.get("expectancy_r") or -999.0) >= args.oos_min_expectancy_r,
        "max_drawdown_r": float(summary.get("max_drawdown_r") or -999.0) >= -abs(args.oos_max_drawdown_r),
        "cost_stress_positive": float(stress.get("expectancy_r") or -999.0) > 0.0,
    }
    return {"pass": all(checks.values()), "checks": checks}


def stable_hash(value: str) -> int:
    return int(hashlib.sha1(value.encode("utf-8")).hexdigest()[:12], 16)


def build_configs(intervals: list[str], max_configs_per_interval: int) -> list[CompressionConfig]:
    sessions = [
        SessionWindow("asia_utc_00_06", 0, 6),
        SessionWindow("london_utc_07_12", 7, 12),
        SessionWindow("ny_utc_13_20", 13, 20),
        SessionWindow("all_day", 0, 23),
    ]
    configs: list[CompressionConfig] = []
    for interval in intervals:
        lookbacks = (16, 32, 64) if interval == "15m" else (8, 12, 24)
        holds = (16, 32) if interval == "15m" else (8, 16)
        interval_configs: list[CompressionConfig] = []
        for session in sessions:
            for side in ("LONG", "SHORT"):
                for lookback in lookbacks:
                    for max_range_atr in (1.8, 2.5, 3.5):
                        for buffer_atr in (0.0, 0.15):
                            for volume_z in (-0.25, 0.25):
                                for trend_filter in ("none", "ema50_slope", "ema50_stack"):
                                    for stop_atr in (1.0, 1.5):
                                        for take_atr in (2.0, 3.0):
                                            for hold in holds:
                                                strategy_id = (
                                                    f"session_vol_comp_{interval}_{side.lower()}_{session.name}_"
                                                    f"lb{lookback}_ra{max_range_atr:g}_buf{buffer_atr:g}_vz{volume_z:g}_"
                                                    f"{trend_filter}_sl{stop_atr:g}_tp{take_atr:g}_h{hold}"
                                                )
                                                interval_configs.append(
                                                    CompressionConfig(
                                                        strategy_id=strategy_id,
                                                        interval=interval,
                                                        side=side,
                                                        session=session,
                                                        lookback=lookback,
                                                        max_range_atr=max_range_atr,
                                                        breakout_buffer_atr=buffer_atr,
                                                        min_volume_z=volume_z,
                                                        trend_filter=trend_filter,
                                                        stop_atr=stop_atr,
                                                        take_atr=take_atr,
                                                        max_hold_bars=hold,
                                                    )
                                                )
        interval_configs.sort(key=lambda item: stable_hash(item.strategy_id))
        configs.extend(interval_configs[:max_configs_per_interval])
    return configs


def load_interval_bars(cache_dir: Path, interval: str) -> list[Any]:
    path = cache_dir / "futures" / "BTCUSDT" / f"{interval}_klines.csv"
    return load_ohlcv(path)


def evaluate_config(config: CompressionConfig, windows: dict[str, dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    train = evaluate_window(
        config,
        windows["train"]["bars"],
        windows["train"]["features"],
        args.cost_bps_per_side,
        args.stress_extra_bps_per_side,
        args.folds,
    )
    train_gate_payload = train_gate(train, args)
    validation = evaluate_window(
        config,
        windows["validation"]["bars"],
        windows["validation"]["features"],
        args.cost_bps_per_side,
        args.stress_extra_bps_per_side,
        args.folds,
    )
    validation_gate_payload = validation_gate(validation, args)
    oos = evaluate_window(
        config,
        windows["oos"]["bars"],
        windows["oos"]["features"],
        args.cost_bps_per_side,
        args.stress_extra_bps_per_side,
        args.folds,
    )
    oos_gate_payload = oos_gate(oos, args)
    decision = "reject_train_gate_failed"
    if train_gate_payload["pass"] and not validation_gate_payload["pass"]:
        decision = "reject_validation_gate_failed_oos_unopened"
    elif train_gate_payload["pass"] and validation_gate_payload["pass"] and not oos_gate_payload["pass"]:
        decision = "reject_oos_gate_failed"
    elif train_gate_payload["pass"] and validation_gate_payload["pass"] and oos_gate_payload["pass"]:
        decision = "candidate_needs_forward_proof"
    return {
        "config": {**asdict(config), "session": asdict(config.session)},
        "strategy_id": config.strategy_id,
        "family": "session_volatility_compression_breakout",
        "train": {key: value for key, value in train.items() if key != "trades"},
        "validation": {key: value for key, value in validation.items() if key != "trades"},
        "oos": {key: value for key, value in oos.items() if key != "trades"},
        "gates": {
            "train": train_gate_payload,
            "validation": validation_gate_payload,
            "oos": oos_gate_payload,
        },
        "decision": decision,
        "can_trade": False,
    }


def result_sort_key(row: dict[str, Any]) -> tuple[int, float, int, float]:
    decision_rank = {
        "candidate_needs_forward_proof": 3,
        "reject_oos_gate_failed": 2,
        "reject_validation_gate_failed_oos_unopened": 1,
        "reject_train_gate_failed": 0,
    }.get(row.get("decision"), 0)
    validation_exp = float(row.get("validation", {}).get("summary", {}).get("expectancy_r") or -999.0)
    validation_trades = int(row.get("validation", {}).get("summary", {}).get("trades") or 0)
    oos_exp = float(row.get("oos", {}).get("summary", {}).get("expectancy_r") or -999.0)
    return (decision_rank, validation_exp, validation_trades, oos_exp)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Session Volatility Compression Breakout Nested Holdout",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `{str(report['can_trade']).lower()}`",
        f"- Tested configs: `{report['summary']['tested_configs']}`",
        f"- Train qualified: `{report['summary']['train_qualified']}`",
        f"- Validation qualified: `{report['summary']['validation_qualified']}`",
        f"- OOS qualified: `{report['summary']['oos_qualified']}`",
        "",
        "## Boundary",
        "- Research-only.",
        "- Completed-bar signal, next-bar open entry.",
        "- Public/local historical cache only.",
        "- No private credentials, no paper entries, no orders.",
        "",
        "## Top Results",
        "",
        "| Strategy | Decision | TF | Side | Session | Train Exp | Val Trades | Val Exp | OOS Trades | OOS Exp |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report.get("top_results", [])[:20]:
        config = row["config"]
        lines.append(
            "| `{sid}` | `{decision}` | `{tf}` | `{side}` | `{session}` | `{tr_exp}` | `{val_trades}` | `{val_exp}` | `{oos_trades}` | `{oos_exp}` |".format(
                sid=row["strategy_id"],
                decision=row["decision"],
                tf=config["interval"],
                side=config["side"],
                session=config["session"]["name"],
                tr_exp=row["train"]["summary"].get("expectancy_r"),
                val_trades=row["validation"]["summary"].get("trades"),
                val_exp=row["validation"]["summary"].get("expectancy_r"),
                oos_trades=row["oos"]["summary"].get("trades"),
                oos_exp=row["oos"]["summary"].get("expectancy_r"),
            )
        )
    lines.extend(
        [
            "",
            "## Next Action",
            f"- {report['next_action']}",
        ]
    )
    return "\n".join(lines) + "\n"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "strategy_id",
        "decision",
        "interval",
        "side",
        "session",
        "train_trades",
        "train_expectancy_r",
        "validation_trades",
        "validation_expectancy_r",
        "oos_trades",
        "oos_expectancy_r",
        "validation_stable_folds",
        "oos_stable_folds",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            config = row["config"]
            writer.writerow(
                {
                    "strategy_id": row["strategy_id"],
                    "decision": row["decision"],
                    "interval": config["interval"],
                    "side": config["side"],
                    "session": config["session"]["name"],
                    "train_trades": row["train"]["summary"].get("trades"),
                    "train_expectancy_r": row["train"]["summary"].get("expectancy_r"),
                    "validation_trades": row["validation"]["summary"].get("trades"),
                    "validation_expectancy_r": row["validation"]["summary"].get("expectancy_r"),
                    "oos_trades": row["oos"]["summary"].get("trades"),
                    "oos_expectancy_r": row["oos"]["summary"].get("expectancy_r"),
                    "validation_stable_folds": row["validation"].get("stable_folds"),
                    "oos_stable_folds": row["oos"].get("stable_folds"),
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Session-aware volatility compression breakout nested holdout")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--intervals", default="15m,1h")
    parser.add_argument("--max-configs-per-interval", type=int, default=500)
    parser.add_argument("--train-end", default="2024-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--cost-bps-per-side", type=float, default=6.0)
    parser.add_argument("--stress-extra-bps-per-side", type=float, default=4.0)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--train-min-trades", type=int, default=80)
    parser.add_argument("--train-min-expectancy-r", type=float, default=0.08)
    parser.add_argument("--train-min-stable-folds", type=int, default=3)
    parser.add_argument("--train-max-drawdown-r", type=float, default=25.0)
    parser.add_argument("--train-min-bootstrap-p", type=float, default=0.90)
    parser.add_argument("--validation-min-trades", type=int, default=25)
    parser.add_argument("--validation-min-expectancy-r", type=float, default=0.04)
    parser.add_argument("--validation-min-stable-folds", type=int, default=2)
    parser.add_argument("--validation-max-drawdown-r", type=float, default=12.0)
    parser.add_argument("--oos-min-trades", type=int, default=25)
    parser.add_argument("--oos-min-expectancy-r", type=float, default=0.02)
    parser.add_argument("--oos-max-drawdown-r", type=float, default=12.0)
    parser.add_argument("--out-prefix", default="docs/SESSION_VOLATILITY_COMPRESSION_BREAKOUT_NESTED_HOLDOUT_2026-06-30")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cache_dir = resolve_path(args.cache_dir)
    intervals = [item.strip() for item in args.intervals.split(",") if item.strip()]
    configs = build_configs(intervals, args.max_configs_per_interval)
    train_end = parse_ts(args.train_end)
    validation_end = parse_ts(args.validation_end)
    windows_by_interval: dict[str, dict[str, dict[str, Any]]] = {}
    for interval in intervals:
        bars = load_interval_bars(cache_dir, interval)
        train_bars = split_bars(bars, None, train_end)
        validation_bars = split_bars(bars, train_end, validation_end)
        oos_bars = split_bars(bars, validation_end, None)
        windows_by_interval[interval] = {
            "train": {"bars": train_bars, "features": build_features_for_bars(train_bars)},
            "validation": {"bars": validation_bars, "features": build_features_for_bars(validation_bars)},
            "oos": {"bars": oos_bars, "features": build_features_for_bars(oos_bars)},
        }
    results: list[dict[str, Any]] = []
    for config in configs:
        results.append(evaluate_config(config, windows_by_interval[config.interval], args))
    results.sort(key=result_sort_key, reverse=True)
    train_qualified = [row for row in results if row["gates"]["train"]["pass"]]
    validation_qualified = [row for row in results if row["gates"]["train"]["pass"] and row["gates"]["validation"]["pass"]]
    oos_qualified = [row for row in validation_qualified if row["gates"]["oos"]["pass"]]
    decision = "reject_no_train_qualified_session_vol_comp_candidate"
    next_action = "reject this mechanism; do not retune without a materially different signal definition"
    if train_qualified and not validation_qualified:
        decision = "reject_validation_gate_failed_oos_unopened"
        next_action = "inspect train-only overfit; do not open OOS/paper"
    elif validation_qualified and not oos_qualified:
        decision = "reject_oos_gate_failed"
        next_action = "reject this mechanism for promotion; keep only as research evidence"
    elif oos_qualified:
        decision = "candidate_needs_forward_proof"
        next_action = "route top OOS-qualified candidate into observer-only forward proof, not live trading"
    report = {
        "generated_at": now_iso(),
        "tool": "tools/session_volatility_compression_breakout_nested_holdout.py",
        "decision": decision,
        "can_trade": False,
        "boundary": {
            "research_only": True,
            "completed_bar_next_open": True,
            "uses_private_credentials": False,
            "sends_orders": False,
            "opens_paper_entries": False,
            "can_trade": False,
        },
        "inputs": {
            "cache_dir": portable(cache_dir),
            "intervals": intervals,
            "train_end": args.train_end,
            "validation_end": args.validation_end,
            "cost_bps_per_side": args.cost_bps_per_side,
            "stress_extra_bps_per_side": args.stress_extra_bps_per_side,
        },
        "summary": {
            "tested_configs": len(results),
            "train_qualified": len(train_qualified),
            "validation_qualified": len(validation_qualified),
            "oos_qualified": len(oos_qualified),
            "candidate_needs_forward_proof": len(oos_qualified),
        },
        "top_results": results[:50],
        "next_action": next_action,
    }
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    write_csv(out_prefix.with_name(out_prefix.name + "_top_results.csv"), results[:200])
    print(
        json.dumps(
            {
                "decision": decision,
                "summary": report["summary"],
                "json": portable(out_prefix.with_suffix(".json")),
                "md": portable(out_prefix.with_suffix(".md")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

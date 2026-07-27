#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import itertools
import json
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
from tools.liquidity_sweep_hardening import Trade, simulate_trade, summarize_trades  # noqa: E402


@dataclass(frozen=True)
class VolTransitionConfig:
    strategy_id: str
    interval: str
    side: str
    transition_mode: str
    compression_lookback: int
    compression_max_atr_ratio: float
    expansion_min_tr_atr: float
    body_min_atr: float
    close_location_filter: str
    volume_z_min: float
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


def load_interval_bars(cache_dir: Path, interval: str) -> list[Any]:
    return load_ohlcv(cache_dir / "futures" / "BTCUSDT" / f"{interval}_klines.csv")


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


def rolling_prev_mean(values: list[float | None], window: int) -> list[float | None]:
    out: list[float | None] = []
    prior: list[float] = []
    for value in values:
        out.append(sum(prior[-window:]) / window if len(prior) >= window else None)
        if value is not None:
            prior.append(float(value))
    return out


def rolling_prev_std(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    prior: list[float] = []
    for value in values:
        out.append(statistics.pstdev(prior[-window:]) if len(prior) >= window else None)
        prior.append(float(value))
    return out


def rolling_prev_mean_max_valid(values: list[float | None], window: int) -> tuple[list[float | None], list[float | None], list[int]]:
    mean_out: list[float | None] = []
    max_out: list[float | None] = []
    count_out: list[int] = []
    for index in range(len(values)):
        if index < window:
            mean_out.append(None)
            max_out.append(None)
            count_out.append(0)
            continue
        chunk = [float(value) for value in values[index - window : index] if value is not None]
        if not chunk:
            mean_out.append(None)
            max_out.append(None)
            count_out.append(0)
            continue
        mean_out.append(sum(chunk) / len(chunk))
        max_out.append(max(chunk))
        count_out.append(len(chunk))
    return mean_out, max_out, count_out


def true_ranges(bars: list[Any]) -> list[float]:
    out: list[float] = []
    prev_close: float | None = None
    for bar in bars:
        high = float(bar.high)
        low = float(bar.low)
        if prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        out.append(tr)
        prev_close = float(bar.close)
    return out


def build_features(bars: list[Any], atr_window: int, atr_ratio_window: int, volume_z_window: int) -> dict[str, Any]:
    closes = [float(bar.close) for bar in bars]
    opens = [float(bar.open) for bar in bars]
    highs = [float(bar.high) for bar in bars]
    lows = [float(bar.low) for bar in bars]
    volumes = [float(bar.volume) for bar in bars]
    atr = compute_atr(bars, atr_window)
    atr_mean = rolling_prev_mean(atr, atr_ratio_window)
    atr_ratio = [
        (float(atr_value) / float(mean_value)) if atr_value is not None and mean_value not in {None, 0} else None
        for atr_value, mean_value in zip(atr, atr_mean)
    ]
    volume_mean = rolling_prev_mean(volumes, volume_z_window)
    volume_std = rolling_prev_std(volumes, volume_z_window)
    volume_z = [
        (volume - mean) / std if mean is not None and std is not None and std > 0 else None
        for volume, mean, std in zip(volumes, volume_mean, volume_std)
    ]
    return {
        "closes": closes,
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "atr": atr,
        "atr_ratio": atr_ratio,
        "true_range": true_ranges(bars),
        "volume_z": volume_z,
        "ema50": ema(closes, 50),
        "ema200": ema(closes, 200),
    }


def add_transition_cache(features: dict[str, Any], lookbacks: list[int]) -> None:
    features["atr_ratio_prev_mean"] = {}
    features["atr_ratio_prev_max"] = {}
    features["atr_ratio_prev_count"] = {}
    for lookback in lookbacks:
        mean_values, max_values, counts = rolling_prev_mean_max_valid(features["atr_ratio"], lookback)
        features["atr_ratio_prev_mean"][lookback] = mean_values
        features["atr_ratio_prev_max"][lookback] = max_values
        features["atr_ratio_prev_count"][lookback] = counts


def prior_compression_ok(config: VolTransitionConfig, features: dict[str, Any], index: int) -> bool:
    lookback = config.compression_lookback
    if index < lookback + 1:
        return False
    count = features["atr_ratio_prev_count"][lookback][index]
    if count < max(3, lookback // 2):
        return False
    prior_mean = features["atr_ratio_prev_mean"][lookback][index]
    prior_max = features["atr_ratio_prev_max"][lookback][index]
    if prior_mean is None or prior_max is None:
        return False
    if config.transition_mode == "low_to_expansion":
        return float(prior_mean) <= config.compression_max_atr_ratio
    if config.transition_mode == "squeeze_release":
        return float(prior_max) <= config.compression_max_atr_ratio
    if config.transition_mode == "vol_reacceleration":
        current_ratio = features["atr_ratio"][index]
        if current_ratio is None:
            return False
        return float(prior_mean) <= config.compression_max_atr_ratio and float(current_ratio) > float(prior_mean)
    raise ValueError(f"unsupported transition_mode={config.transition_mode}")


def trend_ok(config: VolTransitionConfig, features: dict[str, Any], index: int) -> bool:
    if config.trend_filter == "none":
        return True
    if index < 12:
        return False
    closes = features["closes"]
    ema50 = features["ema50"]
    ema200 = features["ema200"]
    if config.trend_filter == "with_ema50_slope":
        if config.side == "LONG":
            return closes[index] > ema50[index] and ema50[index] > ema50[index - 6]
        return closes[index] < ema50[index] and ema50[index] < ema50[index - 6]
    if config.trend_filter == "with_ema_stack":
        if config.side == "LONG":
            return closes[index] > ema50[index] > ema200[index]
        return closes[index] < ema50[index] < ema200[index]
    if config.trend_filter == "counter_ema50_slope":
        if config.side == "LONG":
            return closes[index] < ema50[index] and ema50[index] < ema50[index - 6]
        return closes[index] > ema50[index] and ema50[index] > ema50[index - 6]
    raise ValueError(f"unsupported trend_filter={config.trend_filter}")


def close_location_ok(config: VolTransitionConfig, features: dict[str, Any], index: int) -> bool:
    high = features["highs"][index]
    low = features["lows"][index]
    close = features["closes"][index]
    location = (close - low) / max(high - low, 1e-12)
    if config.close_location_filter == "none":
        return True
    if config.side == "LONG":
        if config.close_location_filter == "directional":
            return location >= 0.65
        if config.close_location_filter == "extreme":
            return location >= 0.80
    else:
        if config.close_location_filter == "directional":
            return location <= 0.35
        if config.close_location_filter == "extreme":
            return location <= 0.20
    raise ValueError(f"unsupported close_location_filter={config.close_location_filter}")


def generate_signals(config: VolTransitionConfig, bars: list[Any], features: dict[str, Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for index, _bar in enumerate(bars):
        if index + config.max_hold_bars + 1 >= len(bars):
            continue
        atr = features["atr"][index]
        if atr is None or float(atr) <= 0:
            continue
        if not prior_compression_ok(config, features, index):
            continue
        tr_atr = features["true_range"][index] / float(atr)
        body_atr = abs(features["closes"][index] - features["opens"][index]) / float(atr)
        if tr_atr < config.expansion_min_tr_atr or body_atr < config.body_min_atr:
            continue
        if config.side == "LONG" and features["closes"][index] <= features["opens"][index]:
            continue
        if config.side == "SHORT" and features["closes"][index] >= features["opens"][index]:
            continue
        volume_z = features["volume_z"][index]
        if volume_z is None or float(volume_z) < config.volume_z_min:
            continue
        if not close_location_ok(config, features, index):
            continue
        if not trend_ok(config, features, index):
            continue
        signals.append(
            {
                "bar_index": index,
                "side_hint": config.side,
                "atr": float(atr),
                "reason": "volatility_regime_transition",
                "transition_mode": config.transition_mode,
                "tr_atr": round(tr_atr, 6),
                "body_atr": round(body_atr, 6),
                "atr_ratio": round(float(features["atr_ratio"][index]), 6) if features["atr_ratio"][index] is not None else None,
                "volume_z": round(float(volume_z), 6),
            }
        )
    return signals


def replay_signals(config: VolTransitionConfig, bars: list[Any], signals: list[dict[str, Any]], cost_bps_per_side: float, no_overlap: bool) -> list[Trade]:
    trades: list[Trade] = []
    last_exit_bar = -1
    bar_index_by_ts = {str(bar.ts): index for index, bar in enumerate(bars)}
    for signal in signals:
        signal_index = int(signal["bar_index"])
        if no_overlap and signal_index <= last_exit_bar:
            continue
        trade = simulate_trade(
            dataset_id=f"volatility_regime_transition_BTCUSDT_{config.interval}",
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
            last_exit_bar = max(last_exit_bar, bar_index_by_ts.get(trade.exit_ts, signal_index))
    return trades


def fold_summaries(trades: list[Trade], folds: int) -> list[dict[str, Any]]:
    ordered = sorted(trades, key=lambda item: item.entry_ts)
    out: list[dict[str, Any]] = []
    for fold in range(folds):
        start = round(len(ordered) * fold / folds)
        end = round(len(ordered) * (fold + 1) / folds)
        chunk = ordered[start:end]
        summary = summarize_trades(chunk)
        summary["fold"] = fold + 1
        summary["stable"] = bool(summary["trades"] >= 5 and (summary["expectancy_r"] or 0.0) > 0)
        out.append(summary)
    return out


def evaluate_window(config: VolTransitionConfig, bars: list[Any], features: dict[str, Any], args: argparse.Namespace, folds: int) -> dict[str, Any]:
    cost = args.fee_bps + args.slippage_bps
    signals = generate_signals(config, bars, features)
    trades = replay_signals(config, bars, signals, cost, args.no_overlap)
    stress_trades = replay_signals(config, bars, signals, cost + args.cost_stress_extra_bps, args.no_overlap)
    folds_payload = fold_summaries(trades, folds)
    return {
        "summary": summarize_trades(trades),
        "folds": folds_payload,
        "stable_folds": sum(1 for item in folds_payload if item.get("stable")),
        "cost_stress": {"summary": summarize_trades(stress_trades)},
        "signals": len(signals),
        "trades": trades,
    }


def gate(window: dict[str, Any], *, min_trades: int, min_expectancy: float, min_stable_folds: int, min_winrate: float, max_drawdown: float) -> dict[str, Any]:
    summary = window["summary"]
    stress = window["cost_stress"]["summary"]
    checks = {
        "min_trades": int(summary.get("trades") or 0) >= min_trades,
        "min_expectancy_r": float(summary.get("expectancy_r") or -999.0) >= min_expectancy,
        "min_winrate_pct": float(summary.get("winrate_pct") or 0.0) >= min_winrate,
        "min_stable_folds": int(window.get("stable_folds") or 0) >= min_stable_folds,
        "max_drawdown_r": float(summary.get("max_drawdown_r") or -999.0) >= -abs(max_drawdown),
        "cost_stress_positive": float(stress.get("expectancy_r") or -999.0) > 0,
    }
    return {"pass": all(checks.values()), "checks": checks}


def evaluate_config(config: VolTransitionConfig, windows: dict[str, dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    train = evaluate_window(config, windows[config.interval]["train"]["bars"], windows[config.interval]["train"]["features"], args, args.folds)
    train_gate = gate(
        train,
        min_trades=args.train_min_trades,
        min_expectancy=args.train_min_expectancy_r,
        min_stable_folds=args.train_min_stable_folds,
        min_winrate=args.train_min_winrate_pct,
        max_drawdown=args.train_max_drawdown_r,
    )
    validation = evaluate_window(config, windows[config.interval]["validation"]["bars"], windows[config.interval]["validation"]["features"], args, args.folds)
    validation_gate = gate(
        validation,
        min_trades=args.validation_min_trades,
        min_expectancy=args.validation_min_expectancy_r,
        min_stable_folds=args.validation_min_stable_folds,
        min_winrate=args.validation_min_winrate_pct,
        max_drawdown=args.validation_max_drawdown_r,
    )
    oos = evaluate_window(config, windows[config.interval]["oos"]["bars"], windows[config.interval]["oos"]["features"], args, args.folds)
    oos_gate = gate(
        oos,
        min_trades=args.oos_min_trades,
        min_expectancy=args.oos_min_expectancy_r,
        min_stable_folds=args.oos_min_stable_folds,
        min_winrate=args.oos_min_winrate_pct,
        max_drawdown=args.oos_max_drawdown_r,
    )
    decision = "reject_train_gate_failed"
    if train_gate["pass"] and not validation_gate["pass"]:
        decision = "reject_validation_gate_failed_oos_unopened"
    elif train_gate["pass"] and validation_gate["pass"] and not oos_gate["pass"]:
        decision = "reject_oos_gate_failed"
    elif train_gate["pass"] and validation_gate["pass"] and oos_gate["pass"]:
        decision = "candidate_needs_forward_proof"
    return {
        "config": asdict(config),
        "strategy_id": config.strategy_id,
        "family": "volatility_regime_transition",
        "train": {key: value for key, value in train.items() if key != "trades"},
        "validation": {key: value for key, value in validation.items() if key != "trades"},
        "oos": {key: value for key, value in oos.items() if key != "trades"},
        "gates": {"train": train_gate, "validation": validation_gate, "oos": oos_gate},
        "decision": decision,
        "can_trade": False,
    }


def build_configs(intervals: list[str], max_configs: int, seed: int) -> list[VolTransitionConfig]:
    rows: list[VolTransitionConfig] = []
    for interval, side, mode, lookback, compression, expansion, body, location, volume_z, trend, hold in itertools.product(
        intervals,
        ["LONG", "SHORT"],
        ["low_to_expansion", "squeeze_release", "vol_reacceleration"],
        [6, 12, 24, 48],
        [0.75, 0.85, 0.95],
        [1.0, 1.25, 1.5, 2.0],
        [0.25, 0.5, 0.75],
        ["none", "directional", "extreme"],
        [-0.5, 0.0, 0.75, 1.5],
        ["none", "with_ema50_slope", "with_ema_stack", "counter_ema50_slope"],
        [8, 16, 24],
    ):
        sid = (
            f"vol_transition_{interval}_{side.lower()}_{mode}_lb{lookback}_c{compression:g}"
            f"_x{expansion:g}_b{body:g}_{location}_vz{volume_z:g}_{trend}_h{hold}"
        )
        rows.append(
            VolTransitionConfig(
                strategy_id=sid,
                interval=interval,
                side=side,
                transition_mode=mode,
                compression_lookback=lookback,
                compression_max_atr_ratio=compression,
                expansion_min_tr_atr=expansion,
                body_min_atr=body,
                close_location_filter=location,
                volume_z_min=volume_z,
                trend_filter=trend,
                stop_atr=1.0,
                take_atr=3.0,
                max_hold_bars=hold,
            )
        )
    rng = random.Random(seed)
    rng.shuffle(rows)
    return rows[:max_configs]


def result_sort_key(row: dict[str, Any]) -> tuple[int, float, int, float]:
    rank = {
        "candidate_needs_forward_proof": 3,
        "reject_oos_gate_failed": 2,
        "reject_validation_gate_failed_oos_unopened": 1,
        "reject_train_gate_failed": 0,
    }.get(row.get("decision"), 0)
    val_exp = float(row.get("validation", {}).get("summary", {}).get("expectancy_r") or -999.0)
    val_trades = int(row.get("validation", {}).get("summary", {}).get("trades") or 0)
    oos_exp = float(row.get("oos", {}).get("summary", {}).get("expectancy_r") or -999.0)
    return (rank, val_exp, val_trades, oos_exp)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Volatility Regime Transition Nested Holdout",
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
        "",
        "- Research-only volatility regime transition test.",
        "- BTCUSDT futures traded in simulation only.",
        "- Completed-bar signal, next-bar open entry.",
        "- No private credentials, no network, no paper/live orders.",
        "- This is not the earlier session breakout family: it tests low-volatility-to-expansion transitions directly.",
        "",
        "## Top Results",
        "",
        "| Strategy | Decision | TF | Side | Mode | Lookback | Comp | Exp | Body | VolZ | Trend | Val Trades | Val Exp | OOS Trades | OOS Exp |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in report.get("top_results", [])[:30]:
        cfg = row["config"]
        lines.append(
            "| `{sid}` | `{decision}` | `{tf}` | `{side}` | `{mode}` | `{lb}` | `{comp}` | `{exp}` | `{body}` | `{vz}` | `{trend}` | `{val_trades}` | `{val_exp}` | `{oos_trades}` | `{oos_exp}` |".format(
                sid=row["strategy_id"],
                decision=row["decision"],
                tf=cfg["interval"],
                side=cfg["side"],
                mode=cfg["transition_mode"],
                lb=cfg["compression_lookback"],
                comp=cfg["compression_max_atr_ratio"],
                exp=cfg["expansion_min_tr_atr"],
                body=cfg["body_min_atr"],
                vz=cfg["volume_z_min"],
                trend=cfg["trend_filter"],
                val_trades=row["validation"]["summary"].get("trades"),
                val_exp=row["validation"]["summary"].get("expectancy_r"),
                oos_trades=row["oos"]["summary"].get("trades"),
                oos_exp=row["oos"]["summary"].get("expectancy_r"),
            )
        )
    lines.extend(["", "## Next Action", "", f"- {report['next_action']}", ""])
    return "\n".join(lines)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "strategy_id",
        "decision",
        "interval",
        "side",
        "transition_mode",
        "compression_lookback",
        "compression_max_atr_ratio",
        "expansion_min_tr_atr",
        "body_min_atr",
        "volume_z_min",
        "trend_filter",
        "train_trades",
        "train_expectancy_r",
        "validation_trades",
        "validation_expectancy_r",
        "oos_trades",
        "oos_expectancy_r",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            cfg = row["config"]
            writer.writerow(
                {
                    "strategy_id": row["strategy_id"],
                    "decision": row["decision"],
                    "interval": cfg["interval"],
                    "side": cfg["side"],
                    "transition_mode": cfg["transition_mode"],
                    "compression_lookback": cfg["compression_lookback"],
                    "compression_max_atr_ratio": cfg["compression_max_atr_ratio"],
                    "expansion_min_tr_atr": cfg["expansion_min_tr_atr"],
                    "body_min_atr": cfg["body_min_atr"],
                    "volume_z_min": cfg["volume_z_min"],
                    "trend_filter": cfg["trend_filter"],
                    "train_trades": row["train"]["summary"].get("trades"),
                    "train_expectancy_r": row["train"]["summary"].get("expectancy_r"),
                    "validation_trades": row["validation"]["summary"].get("trades"),
                    "validation_expectancy_r": row["validation"]["summary"].get("expectancy_r"),
                    "oos_trades": row["oos"]["summary"].get("trades"),
                    "oos_expectancy_r": row["oos"]["summary"].get("expectancy_r"),
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research-only BTC volatility regime transition nested holdout")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--intervals", default="1h,4h")
    parser.add_argument("--max-configs", type=int, default=800)
    parser.add_argument("--seed", type=int, default=2702)
    parser.add_argument("--train-end", default="2022-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2024-01-01T00:00:00+00:00")
    parser.add_argument("--atr-window", type=int, default=20)
    parser.add_argument("--atr-ratio-window", type=int, default=100)
    parser.add_argument("--volume-z-window", type=int, default=100)
    parser.add_argument("--fee-bps", type=float, default=4.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--cost-stress-extra-bps", type=float, default=2.0)
    parser.add_argument("--no-overlap", action="store_true", default=True)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--train-min-trades", type=int, default=60)
    parser.add_argument("--validation-min-trades", type=int, default=40)
    parser.add_argument("--oos-min-trades", type=int, default=40)
    parser.add_argument("--train-min-expectancy-r", type=float, default=0.08)
    parser.add_argument("--validation-min-expectancy-r", type=float, default=0.08)
    parser.add_argument("--oos-min-expectancy-r", type=float, default=0.08)
    parser.add_argument("--train-min-winrate-pct", type=float, default=32.0)
    parser.add_argument("--validation-min-winrate-pct", type=float, default=32.0)
    parser.add_argument("--oos-min-winrate-pct", type=float, default=32.0)
    parser.add_argument("--train-min-stable-folds", type=int, default=2)
    parser.add_argument("--validation-min-stable-folds", type=int, default=2)
    parser.add_argument("--oos-min-stable-folds", type=int, default=2)
    parser.add_argument("--train-max-drawdown-r", type=float, default=40.0)
    parser.add_argument("--validation-max-drawdown-r", type=float, default=30.0)
    parser.add_argument("--oos-max-drawdown-r", type=float, default=30.0)
    parser.add_argument("--out-prefix", default="docs/VOLATILITY_REGIME_TRANSITION_NESTED_HOLDOUT_2026-07-01")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cache_dir = resolve_path(args.cache_dir)
    intervals = [item.strip() for item in args.intervals.split(",") if item.strip()]
    train_end = parse_ts(args.train_end)
    validation_end = parse_ts(args.validation_end)
    windows: dict[str, dict[str, dict[str, Any]]] = {}
    for interval in intervals:
        bars_all = load_interval_bars(cache_dir, interval)
        windows[interval] = {}
        for name, start, end in (
            ("train", None, train_end),
            ("validation", train_end, validation_end),
            ("oos", validation_end, None),
        ):
            bars = split_bars(bars_all, start, end)
            features = build_features(bars, args.atr_window, args.atr_ratio_window, args.volume_z_window)
            add_transition_cache(features, [6, 12, 24, 48])
            windows[interval][name] = {
                "bars": bars,
                "features": features,
            }
    configs = build_configs(intervals, args.max_configs, args.seed)
    results = [evaluate_config(config, windows, args) for config in configs]
    results.sort(key=result_sort_key, reverse=True)
    train_qualified = [row for row in results if row["gates"]["train"]["pass"]]
    validation_qualified = [row for row in results if row["gates"]["train"]["pass"] and row["gates"]["validation"]["pass"]]
    oos_qualified = [row for row in validation_qualified if row["gates"]["oos"]["pass"]]
    decision = "reject_no_train_qualified_volatility_transition"
    next_action = "reject this mechanism as currently parameterized; do not retune without a materially different volatility-transition hypothesis"
    if train_qualified and not validation_qualified:
        decision = "reject_validation_gate_failed_oos_unopened"
        next_action = "volatility transition overfit on train; do not promote"
    elif validation_qualified and not oos_qualified:
        decision = "reject_oos_gate_failed"
        next_action = "reject volatility transition for promotion; keep as tombstone evidence"
    elif oos_qualified:
        decision = "candidate_needs_forward_proof"
        next_action = "route top volatility-transition candidate into observer-only forward proof, not live trading"
    report = {
        "generated_at": now_iso(),
        "tool": "tools/volatility_regime_transition_nested_holdout.py",
        "decision": decision,
        "can_trade": False,
        "boundary": {
            "research_only": True,
            "completed_bar_next_open": True,
            "sends_orders": False,
            "uses_private_credentials": False,
            "network_required": False,
            "distinct_from_session_breakout": True,
        },
        "summary": {
            "tested_configs": len(results),
            "train_qualified": len(train_qualified),
            "validation_qualified": len(validation_qualified),
            "oos_qualified": len(oos_qualified),
            "intervals": intervals,
        },
        "settings": vars(args),
        "windows": {
            interval: {name: {"bars": len(payload["bars"])} for name, payload in by_window.items()}
            for interval, by_window in windows.items()
        },
        "top_results": results[:100],
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

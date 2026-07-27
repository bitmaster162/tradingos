#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.derivatives_event_edge_miner import (  # noqa: E402
    EventConfig,
    atr_series,
    ema_series,
    fold_stats,
    gate,
    regime_matches,
    safe_float,
    signal_matches,
    simulate_exit,
    split_index,
    stable_folds,
    stats,
)


@dataclass(frozen=True)
class CompositeConfig:
    strategy_id: str
    family: str
    side: str
    interval: str
    lookback: int
    price_atr: float
    oi_pct: float
    funding_abs: float
    volume_z: float
    close_location: float
    regime_filter: str
    context_mode: str
    spot_divergence_pct: float
    spot_volume_ratio: float
    sweep_lookback: int
    stop_atr: float
    take_atr: float
    max_hold_bars: int


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_float_grid(value: str) -> list[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def parse_int_grid(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def parse_str_grid(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def join_rows(
    futures_klines: list[dict[str, str]],
    derivatives: list[dict[str, str]],
    spot_klines: list[dict[str, str]],
) -> list[dict[str, Any]]:
    derivatives_by_time = {str(row.get("time")): row for row in derivatives}
    spot_by_time = {str(row.get("time")): row for row in spot_klines}
    rows: list[dict[str, Any]] = []
    for row in futures_klines:
        time_value = str(row.get("time") or "")
        deriv = derivatives_by_time.get(time_value, {})
        spot = spot_by_time.get(time_value, {})
        item: dict[str, Any] = {
            "time": time_value,
            "open": safe_float(row.get("open")),
            "high": safe_float(row.get("high")),
            "low": safe_float(row.get("low")),
            "close": safe_float(row.get("close")),
            "volume": safe_float(row.get("volume")),
            "open_interest": safe_float(deriv.get("open_interest")),
            "funding": safe_float(deriv.get("funding")),
            "spot_close": safe_float(spot.get("close")),
            "spot_volume": safe_float(spot.get("volume")),
        }
        if all(item.get(key) is not None for key in ("open", "high", "low", "close", "volume")):
            rows.append(item)
    return rows


def base_config(config: CompositeConfig) -> EventConfig:
    return EventConfig(
        strategy_id=config.strategy_id,
        family=config.family,
        side=config.side,
        interval=config.interval,
        lookback=config.lookback,
        price_atr=config.price_atr,
        oi_pct=config.oi_pct,
        funding_abs=config.funding_abs,
        volume_z=config.volume_z,
        close_location=config.close_location,
        regime_filter=config.regime_filter,
        stop_atr=config.stop_atr,
        take_atr=config.take_atr,
        max_hold_bars=config.max_hold_bars,
    )


def build_features(
    rows: list[dict[str, Any]],
    *,
    lookbacks: tuple[int, ...],
    sweep_lookbacks: tuple[int, ...],
    atr_window: int = 14,
    volume_window: int = 100,
) -> dict[int, dict[int, dict[str, float]]]:
    atr = atr_series(rows, atr_window)
    ema50 = ema_series(rows, 50)
    ema200 = ema_series(rows, 200)
    features: dict[int, dict[int, dict[str, float]]] = {}
    warmup = max(max(lookbacks), max(sweep_lookbacks), atr_window, volume_window, 220)
    for index in range(warmup, len(rows) - 1):
        close = safe_float(rows[index].get("close"))
        high = safe_float(rows[index].get("high"))
        low = safe_float(rows[index].get("low"))
        volume = safe_float(rows[index].get("volume"))
        spot_volume = safe_float(rows[index].get("spot_volume"))
        oi = safe_float(rows[index].get("open_interest"))
        funding = safe_float(rows[index].get("funding"))
        current_atr = atr[index]
        if None in {close, high, low, volume, spot_volume, oi, funding} or current_atr is None or current_atr <= 0:
            continue
        prior_volumes = [safe_float(row.get("volume")) for row in rows[index - volume_window : index]]
        clean_volumes = [value for value in prior_volumes if value is not None]
        if len(clean_volumes) < volume_window:
            continue
        sigma = statistics.pstdev(clean_volumes)
        volume_z = (float(volume) - statistics.mean(clean_volumes)) / sigma if sigma > 0 else 0.0
        candle_range = max(float(high) - float(low), 1e-12)
        close_location = (float(close) - float(low)) / candle_range
        sweep_values: dict[int, dict[str, bool]] = {}
        for sweep_lookback in sweep_lookbacks:
            previous = rows[index - sweep_lookback : index]
            prev_high = max(float(row["high"]) for row in previous if row.get("high") is not None)
            prev_low = min(float(row["low"]) for row in previous if row.get("low") is not None)
            sweep_values[sweep_lookback] = {
                "bullish_sweep": float(low) < prev_low and float(close) > prev_low,
                "bearish_sweep": float(high) > prev_high and float(close) < prev_high,
            }
        by_lookback: dict[int, dict[str, float]] = {}
        for lookback in lookbacks:
            previous = rows[index - lookback]
            previous_close = safe_float(previous.get("close"))
            previous_spot_close = safe_float(previous.get("spot_close"))
            previous_oi = safe_float(previous.get("open_interest"))
            spot_close = safe_float(rows[index].get("spot_close"))
            if previous_close is None or previous_spot_close is None or spot_close is None or previous_oi in {None, 0.0}:
                continue
            perp_ret_pct = (float(close) - previous_close) / previous_close * 100.0
            spot_ret_pct = (float(spot_close) - previous_spot_close) / previous_spot_close * 100.0
            feature = {
                "price_move_atr": (float(close) - previous_close) / current_atr,
                "oi_delta_pct": (float(oi) - float(previous_oi)) / float(previous_oi) * 100.0,
                "funding": float(funding),
                "volume_z": volume_z,
                "close_location": close_location,
                "atr": current_atr,
                "close": float(close),
                "ema50": ema50[index] if ema50[index] is not None else math.nan,
                "ema200": ema200[index] if ema200[index] is not None else math.nan,
                "ema50_slope_20": (ema50[index] - ema50[index - 20]) if ema50[index] is not None and ema50[index - 20] is not None else math.nan,
                "ema200_slope_20": (ema200[index] - ema200[index - 20]) if ema200[index] is not None and ema200[index - 20] is not None else math.nan,
                "spot_ret_pct": spot_ret_pct,
                "perp_ret_pct": perp_ret_pct,
                "spot_perp_divergence_pct": spot_ret_pct - perp_ret_pct,
                "spot_volume_ratio": float(spot_volume) / max(float(volume), 1e-12),
            }
            for sweep_lookback, values in sweep_values.items():
                feature[f"bullish_sweep_{sweep_lookback}"] = 1.0 if values["bullish_sweep"] else 0.0
                feature[f"bearish_sweep_{sweep_lookback}"] = 1.0 if values["bearish_sweep"] else 0.0
            by_lookback[lookback] = feature
        if by_lookback:
            features[index] = by_lookback
    return features


def context_matches(config: CompositeConfig, feature: dict[str, float]) -> bool:
    mode = config.context_mode
    divergence = float(feature.get("spot_perp_divergence_pct") or 0.0)
    spot_volume_ratio = float(feature.get("spot_volume_ratio") or 0.0)
    bullish_sweep = bool(feature.get(f"bullish_sweep_{config.sweep_lookback}") == 1.0)
    bearish_sweep = bool(feature.get(f"bearish_sweep_{config.sweep_lookback}") == 1.0)
    price_move = float(feature.get("price_move_atr") or 0.0)
    oi_delta = float(feature.get("oi_delta_pct") or 0.0)
    close_location = float(feature.get("close_location") or 0.0)

    if mode == "none":
        return True
    if mode == "spot_confirm":
        return divergence >= config.spot_divergence_pct if config.side == "LONG" else divergence <= -config.spot_divergence_pct
    if mode == "spot_volume_confirm":
        direction_ok = divergence >= config.spot_divergence_pct if config.side == "LONG" else divergence <= -config.spot_divergence_pct
        return direction_ok and spot_volume_ratio >= config.spot_volume_ratio
    if mode == "sweep_confirm":
        return bullish_sweep if config.side == "LONG" else bearish_sweep
    if mode == "liq_proxy":
        if config.side == "LONG":
            return price_move <= -max(0.25, config.price_atr * 0.5) and oi_delta <= -config.oi_pct and close_location >= config.close_location
        return price_move >= max(0.25, config.price_atr * 0.5) and oi_delta <= -config.oi_pct and close_location <= 1.0 - config.close_location
    if mode == "composite2":
        direction_ok = divergence >= config.spot_divergence_pct if config.side == "LONG" else divergence <= -config.spot_divergence_pct
        sweep_ok = bullish_sweep if config.side == "LONG" else bearish_sweep
        return direction_ok and sweep_ok and spot_volume_ratio >= config.spot_volume_ratio
    raise ValueError(f"unsupported context_mode: {mode}")


def composite_signal_matches(config: CompositeConfig, feature: dict[str, float]) -> bool:
    base = base_config(config)
    return regime_matches(base, feature) and signal_matches(base, feature) and context_matches(config, feature)


def simulate_window(
    config: CompositeConfig,
    rows: list[dict[str, Any]],
    features: dict[int, dict[int, dict[str, float]]],
    *,
    start_index: int,
    end_index: int,
    cost_bps_per_side: float,
    no_overlap: bool = True,
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    index = max(0, start_index)
    safe_end = min(end_index, len(rows)) - config.max_hold_bars - 2
    base = base_config(config)
    while index < safe_end:
        feature = features.get(index, {}).get(config.lookback)
        if feature is None or not composite_signal_matches(config, feature):
            index += 1
            continue
        outcome = simulate_exit(base, rows[:end_index], index, feature["atr"], cost_bps_per_side=cost_bps_per_side)
        if outcome is None:
            index += 1
            continue
        trades.append(
            {
                "strategy_id": config.strategy_id,
                "family": config.family,
                "context_mode": config.context_mode,
                "interval": config.interval,
                "side": config.side,
                "signal_time": rows[index]["time"],
                "entry_time": rows[outcome["entry_index"]]["time"],
                "exit_time": rows[outcome["exit_index"]]["time"],
                "entry": round(float(outcome["entry"]), 8),
                "exit": round(float(outcome["exit"]), 8),
                "net_r": round(float(outcome["net_r"]), 6),
                "exit_reason": outcome["exit_reason"],
                "price_move_atr": round(feature["price_move_atr"], 6),
                "oi_delta_pct": round(feature["oi_delta_pct"], 6),
                "funding": round(feature["funding"], 8),
                "spot_perp_divergence_pct": round(feature["spot_perp_divergence_pct"], 6),
                "spot_volume_ratio": round(feature["spot_volume_ratio"], 6),
                "bullish_sweep": bool(feature.get(f"bullish_sweep_{config.sweep_lookback}") == 1.0),
                "bearish_sweep": bool(feature.get(f"bearish_sweep_{config.sweep_lookback}") == 1.0),
            }
        )
        index = int(outcome["exit_index"]) + 1 if no_overlap else index + 1
    return trades


def evaluate_config(
    config: CompositeConfig,
    rows: list[dict[str, Any]],
    features: dict[int, dict[int, dict[str, float]]],
    *,
    start_index: int,
    end_index: int,
    cost_bps_per_side: float,
    folds: int,
) -> dict[str, Any]:
    trades = simulate_window(config, rows, features, start_index=start_index, end_index=end_index, cost_bps_per_side=cost_bps_per_side)
    fold_rows = fold_stats(trades, folds)
    return {"summary": stats(trades), "folds": fold_rows, "stable_folds": stable_folds(fold_rows), "sample_trades": trades[:5]}


def build_configs(args: argparse.Namespace, interval: str) -> list[CompositeConfig]:
    configs: list[CompositeConfig] = []
    for family in parse_str_grid(args.families):
        for side in [item.upper() for item in parse_str_grid(args.sides)]:
            for lookback in parse_int_grid(args.lookbacks):
                for price_atr in parse_float_grid(args.price_atr):
                    for oi_pct in parse_float_grid(args.oi_pct):
                        for funding_abs in parse_float_grid(args.funding_abs):
                            for close_location in parse_float_grid(args.close_location):
                                for regime_filter in parse_str_grid(args.regime_filters):
                                    for context_mode in parse_str_grid(args.context_modes):
                                        for spot_divergence in parse_float_grid(args.spot_divergence_pct):
                                            for spot_volume_ratio in parse_float_grid(args.spot_volume_ratio):
                                                for sweep_lookback in parse_int_grid(args.sweep_lookback):
                                                    for take_atr in parse_float_grid(args.take_atr):
                                                        for hold in parse_int_grid(args.max_hold_bars):
                                                            strategy_id = (
                                                                f"derivctx_{family}_{interval}_{side.lower()}_lb{lookback}"
                                                                f"_p{price_atr:g}_oi{oi_pct:g}_f{funding_abs:g}"
                                                                f"_cl{close_location:g}_rg{regime_filter}_ctx{context_mode}"
                                                                f"_sd{spot_divergence:g}_sv{spot_volume_ratio:g}_sw{sweep_lookback}"
                                                                f"_rr{args.stop_atr:g}x{take_atr:g}_h{hold}"
                                                            )
                                                            configs.append(
                                                                CompositeConfig(
                                                                    strategy_id=strategy_id,
                                                                    family=family,
                                                                    side=side,
                                                                    interval=interval,
                                                                    lookback=lookback,
                                                                    price_atr=price_atr,
                                                                    oi_pct=oi_pct,
                                                                    funding_abs=funding_abs,
                                                                    volume_z=0.0,
                                                                    close_location=close_location,
                                                                    regime_filter=regime_filter,
                                                                    context_mode=context_mode,
                                                                    spot_divergence_pct=spot_divergence,
                                                                    spot_volume_ratio=spot_volume_ratio,
                                                                    sweep_lookback=sweep_lookback,
                                                                    stop_atr=args.stop_atr,
                                                                    take_atr=take_atr,
                                                                    max_hold_bars=hold,
                                                                )
                                                            )
    return configs


def diversified_limit(configs: list[CompositeConfig], limit: int) -> list[CompositeConfig]:
    if limit <= 0 or len(configs) <= limit:
        return configs
    buckets: dict[tuple[str, str, str], list[CompositeConfig]] = {}
    for config in configs:
        buckets.setdefault((config.family, config.side, config.context_mode), []).append(config)
    output: list[CompositeConfig] = []
    while len(output) < limit and any(buckets.values()):
        for key in sorted(buckets):
            if buckets[key]:
                output.append(buckets[key].pop(0))
                if len(output) >= limit:
                    break
    return output


def rank_train(item: dict[str, Any]) -> tuple[Any, ...]:
    summary = item["train"]["summary"]
    return (
        1 if item["train_gate"]["pass"] else 0,
        item["train"].get("stable_folds") or 0,
        summary.get("expectancy_r") if isinstance(summary.get("expectancy_r"), (int, float)) else -999.0,
        summary.get("trades") or 0,
    )


def rank_validation(item: dict[str, Any]) -> tuple[Any, ...]:
    summary = item["validation"]["summary"]
    return (
        1 if item["validation_gate"]["pass"] else 0,
        item["validation"].get("stable_folds") or 0,
        summary.get("expectancy_r") if isinstance(summary.get("expectancy_r"), (int, float)) else -999.0,
        summary.get("trades") or 0,
        rank_train(item),
    )


def load_interval(cache_dir: Path, interval: str, lookbacks: tuple[int, ...], sweep_lookbacks: tuple[int, ...]) -> tuple[list[dict[str, Any]], dict[int, dict[int, dict[str, float]]], dict[str, Any]]:
    futures_dir = cache_dir / "futures" / "BTCUSDT"
    spot_dir = cache_dir / "spot" / "BTCUSDT"
    futures_path = futures_dir / f"{interval}_klines.csv"
    derivatives_path = futures_dir / f"{interval}_oi_aligned.csv"
    spot_path = spot_dir / f"{interval}_klines.csv"
    rows = join_rows(read_csv(futures_path), read_csv(derivatives_path), read_csv(spot_path))
    features = build_features(rows, lookbacks=lookbacks, sweep_lookbacks=sweep_lookbacks)
    oi_rows = sum(1 for row in rows if row.get("open_interest") is not None)
    funding_rows = sum(1 for row in rows if row.get("funding") is not None)
    spot_rows = sum(1 for row in rows if row.get("spot_close") is not None)
    meta = {
        "interval": interval,
        "futures_path": rel(futures_path),
        "spot_path": rel(spot_path),
        "derivatives_path": rel(derivatives_path),
        "rows": len(rows),
        "features": len(features),
        "first_time": rows[0].get("time") if rows else None,
        "last_time": rows[-1].get("time") if rows else None,
        "oi_coverage_pct": round(oi_rows / len(rows) * 100.0, 3) if rows else 0.0,
        "funding_coverage_pct": round(funding_rows / len(rows) * 100.0, 3) if rows else 0.0,
        "spot_coverage_pct": round(spot_rows / len(rows) * 100.0, 3) if rows else 0.0,
    }
    return rows, features, meta


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Derivatives Context Composite Miner",
        "",
        f"- Generated: `{report['generated_at']}`.",
        f"- Decision: `{report['decision']}`.",
        "- Boundary: precommitted composite research only; `can_trade=false`; no orders.",
        "",
        "## Summary",
        "",
        f"- Tested configs: `{report['summary']['tested']}`.",
        f"- Train-qualified: `{report['summary']['train_qualified']}`.",
        f"- Validation-qualified: `{report['summary']['validation_qualified']}`.",
        f"- OOS decision: `{report['summary']['oos_decision']}`.",
        "",
        "## Data",
        "",
        "| TF | rows | features | spot % | OI % | funding % | first | last |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for item in report["data"]:
        lines.append(
            f"| `{item['interval']}` | `{item['rows']}` | `{item['features']}` | `{item['spot_coverage_pct']}` | `{item['oi_coverage_pct']}` | `{item['funding_coverage_pct']}` | `{item['first_time']}` | `{item['last_time']}` |"
        )
    lines.extend(["", "## Selected Candidate", ""])
    selected = report.get("selected")
    if selected:
        lines.append(f"- Strategy: `{selected['strategy_id']}`.")
        for stage in ("train", "validation", "oos"):
            stage_payload = selected.get(stage)
            if not stage_payload:
                continue
            summary = stage_payload["summary"]
            lines.append(
                f"- {stage}: `{summary.get('trades')}` trades, winrate `{summary.get('winrate_pct')}`, expectancy `{summary.get('expectancy_r')}`R, maxDD `{summary.get('max_drawdown_r')}`R."
            )
    else:
        lines.append("- No selected candidate.")
    lines.extend(["", "## Top Train Results", "", "| Strategy | Trades | Winrate | Exp R | Stable folds | Gate |", "|---|---:|---:|---:|---:|---|"])
    for item in report["top_train"][:15]:
        summary = item["train"]["summary"]
        lines.append(
            f"| `{item['strategy_id']}` | `{summary.get('trades')}` | `{summary.get('winrate_pct')}` | `{summary.get('expectancy_r')}` | `{item['train'].get('stable_folds')}` | `{item['train_gate']['pass']}` |"
        )
    lines.extend(["", "## Next Action", "", f"- `{report['next_action']}`.", "- `can_trade=false`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Precommitted derivatives + context composite nested holdout")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--intervals", default="1h,4h")
    parser.add_argument("--families", default="oi_build_continuation,funding_extreme_fade,deleveraging_reversal,squeeze_exhaustion_fade")
    parser.add_argument("--sides", default="LONG,SHORT")
    parser.add_argument("--regime-filters", default="none,ema200_slope,ema50_stack")
    parser.add_argument("--context-modes", default="spot_confirm,spot_volume_confirm,sweep_confirm,liq_proxy,composite2")
    parser.add_argument("--train-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2026-01-01T00:00:00+00:00")
    parser.add_argument("--lookbacks", default="6,12")
    parser.add_argument("--price-atr", default="0.4,0.6")
    parser.add_argument("--oi-pct", default="0.15,0.25")
    parser.add_argument("--funding-abs", default="0.0001,0.0002")
    parser.add_argument("--close-location", default="0.55,0.65")
    parser.add_argument("--spot-divergence-pct", default="0,0.02")
    parser.add_argument("--spot-volume-ratio", default="0.2,0.5")
    parser.add_argument("--sweep-lookback", default="12,24")
    parser.add_argument("--stop-atr", type=float, default=1.0)
    parser.add_argument("--take-atr", default="1.5,2.0,3.0")
    parser.add_argument("--max-hold-bars", default="8,16")
    parser.add_argument("--max-configs-per-interval", type=int, default=500)
    parser.add_argument("--validation-top", type=int, default=80)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--train-min-trades", type=int, default=30)
    parser.add_argument("--train-min-expectancy", type=float, default=0.05)
    parser.add_argument("--train-min-stable-folds", type=int, default=3)
    parser.add_argument("--train-max-drawdown", type=float, default=20.0)
    parser.add_argument("--validation-min-trades", type=int, default=8)
    parser.add_argument("--validation-min-expectancy", type=float, default=0.0)
    parser.add_argument("--validation-min-stable-folds", type=int, default=1)
    parser.add_argument("--validation-max-drawdown", type=float, default=10.0)
    parser.add_argument("--oos-min-trades", type=int, default=8)
    parser.add_argument("--oos-min-expectancy", type=float, default=0.0)
    parser.add_argument("--oos-min-stable-folds", type=int, default=1)
    parser.add_argument("--oos-max-drawdown", type=float, default=10.0)
    parser.add_argument("--out-prefix", default="docs/DERIVATIVES_CONTEXT_COMPOSITE_MINER_2026-06-29")
    args = parser.parse_args()

    cache_dir = resolve_path(args.cache_dir)
    intervals = parse_str_grid(args.intervals)
    lookbacks = tuple(parse_int_grid(args.lookbacks))
    sweep_lookbacks = tuple(parse_int_grid(args.sweep_lookback))
    cost_bps = args.fee_bps + args.slippage_bps
    all_train: list[dict[str, Any]] = []
    data_meta: list[dict[str, Any]] = []
    interval_payloads: dict[str, tuple[list[dict[str, Any]], dict[int, dict[int, dict[str, float]]], int, int]] = {}

    for interval in intervals:
        rows, features, meta = load_interval(cache_dir, interval, lookbacks, sweep_lookbacks)
        data_meta.append(meta)
        if not rows or not features:
            continue
        train_end = split_index(rows, args.train_end)
        validation_end = split_index(rows, args.validation_end)
        interval_payloads[interval] = (rows, features, train_end, validation_end)
        configs = diversified_limit(build_configs(args, interval), max(1, args.max_configs_per_interval))
        for config in configs:
            train = evaluate_config(config, rows, features, start_index=0, end_index=train_end, cost_bps_per_side=cost_bps, folds=4)
            train_gate = gate(
                train["summary"],
                train["folds"],
                min_trades=args.train_min_trades,
                min_expectancy=args.train_min_expectancy,
                min_stable_folds=args.train_min_stable_folds,
                max_drawdown=args.train_max_drawdown,
            )
            all_train.append({"strategy_id": config.strategy_id, "config": asdict(config), "train": train, "train_gate": train_gate})

    ranked_train = sorted(all_train, key=rank_train, reverse=True)
    train_qualified = [item for item in ranked_train if item["train_gate"]["pass"]]
    validation_results: list[dict[str, Any]] = []
    for item in train_qualified[: max(1, args.validation_top)]:
        config = CompositeConfig(**item["config"])
        rows, features, train_end, validation_end = interval_payloads[config.interval]
        validation = evaluate_config(config, rows, features, start_index=train_end, end_index=validation_end, cost_bps_per_side=cost_bps, folds=3)
        validation_gate = gate(
            validation["summary"],
            validation["folds"],
            min_trades=args.validation_min_trades,
            min_expectancy=args.validation_min_expectancy,
            min_stable_folds=args.validation_min_stable_folds,
            max_drawdown=args.validation_max_drawdown,
        )
        validation_results.append({**item, "validation": validation, "validation_gate": validation_gate})

    validation_ranked = sorted(validation_results, key=rank_validation, reverse=True)
    validation_qualified = [item for item in validation_ranked if item["validation_gate"]["pass"]]
    selected: dict[str, Any] | None = None
    oos_decision = "oos_not_opened_no_validation_candidate"
    if validation_qualified:
        selected = validation_qualified[0]
        config = CompositeConfig(**selected["config"])
        rows, features, _train_end, validation_end = interval_payloads[config.interval]
        oos = evaluate_config(config, rows, features, start_index=validation_end, end_index=len(rows), cost_bps_per_side=cost_bps, folds=2)
        oos_gate = gate(
            oos["summary"],
            oos["folds"],
            min_trades=args.oos_min_trades,
            min_expectancy=args.oos_min_expectancy,
            min_stable_folds=args.oos_min_stable_folds,
            max_drawdown=args.oos_max_drawdown,
        )
        selected = {**selected, "oos": oos, "oos_gate": oos_gate}
        oos_decision = "oos_pass_observer_candidate_not_trade_permission" if oos_gate["pass"] else "oos_failed_or_insufficient_research_only"

    decision = "reject_no_train_candidate"
    if train_qualified and not validation_qualified:
        decision = "reject_validation_gate_failed"
    if selected:
        decision = oos_decision

    report = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "settings": {
            "intervals": intervals,
            "families": parse_str_grid(args.families),
            "sides": [item.upper() for item in parse_str_grid(args.sides)],
            "regime_filters": parse_str_grid(args.regime_filters),
            "context_modes": parse_str_grid(args.context_modes),
            "train_end": args.train_end,
            "validation_end": args.validation_end,
            "cost_bps_per_side": cost_bps,
            "max_configs_per_interval": args.max_configs_per_interval,
            "validation_top": args.validation_top,
            "precommitted": True,
        },
        "data": data_meta,
        "summary": {
            "tested": len(all_train),
            "train_qualified": len(train_qualified),
            "validation_tested": len(validation_results),
            "validation_qualified": len(validation_qualified),
            "oos_decision": oos_decision,
        },
        "selected": selected,
        "top_train": ranked_train[:50],
        "top_validation": validation_ranked[:25],
        "next_action": "register_for_forward_observer_review_only" if decision.startswith("oos_pass") else "archive_composite_shape_or_add_new_precommitted_features",
        "runtime_boundary": {
            "research_only": True,
            "precommitted_composite": True,
            "paper_allowed": False,
            "live_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    out = resolve_path(args.out_prefix)
    write_json(out.with_suffix(".json"), report)
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": decision,
                "tested": len(all_train),
                "train_qualified": len(train_qualified),
                "validation_qualified": len(validation_qualified),
                "selected": selected["strategy_id"] if selected else None,
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

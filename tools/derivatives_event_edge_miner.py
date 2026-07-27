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


@dataclass(frozen=True)
class EventConfig:
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


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def split_index(rows: list[dict[str, Any]], timestamp: str) -> int:
    boundary = parse_utc(timestamp)
    if boundary is None:
        raise ValueError(f"invalid split timestamp: {timestamp}")
    for index, row in enumerate(rows):
        current = parse_utc(row.get("time"))
        if current is not None and current >= boundary:
            return index
    return len(rows)


def true_range(rows: list[dict[str, Any]], index: int) -> float | None:
    high = safe_float(rows[index].get("high"))
    low = safe_float(rows[index].get("low"))
    if high is None or low is None:
        return None
    if index == 0:
        return high - low
    prev_close = safe_float(rows[index - 1].get("close"))
    if prev_close is None:
        return None
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr_series(rows: list[dict[str, Any]], window: int = 14) -> list[float | None]:
    ranges = [true_range(rows, index) for index in range(len(rows))]
    out: list[float | None] = []
    for index in range(len(rows)):
        if index + 1 < window:
            out.append(None)
            continue
        chunk = [value for value in ranges[index + 1 - window : index + 1] if value is not None]
        out.append(sum(chunk) / len(chunk) if len(chunk) == window else None)
    return out


def ema_series(rows: list[dict[str, Any]], period: int) -> list[float | None]:
    closes = [safe_float(row.get("close")) for row in rows]
    out: list[float | None] = [None] * len(rows)
    clean_seed = [value for value in closes[:period] if value is not None]
    if len(clean_seed) < period:
        return out
    value = sum(clean_seed) / period
    out[period - 1] = value
    alpha = 2.0 / (period + 1.0)
    for index in range(period, len(rows)):
        close = closes[index]
        if close is None:
            out[index] = None
            continue
        value = close * alpha + value * (1.0 - alpha)
        out[index] = value
    return out


def join_rows(klines: list[dict[str, str]], derivatives: list[dict[str, str]]) -> list[dict[str, Any]]:
    derivatives_by_time = {str(row.get("time")): row for row in derivatives}
    joined: list[dict[str, Any]] = []
    for row in klines:
        time_value = str(row.get("time") or "")
        deriv = derivatives_by_time.get(time_value, {})
        item: dict[str, Any] = {
            "time": time_value,
            "open": safe_float(row.get("open")),
            "high": safe_float(row.get("high")),
            "low": safe_float(row.get("low")),
            "close": safe_float(row.get("close")),
            "volume": safe_float(row.get("volume")),
            "open_interest": safe_float(deriv.get("open_interest")),
            "funding": safe_float(deriv.get("funding")),
        }
        if all(item.get(key) is not None for key in ("open", "high", "low", "close", "volume")):
            joined.append(item)
    return joined


def build_features(
    rows: list[dict[str, Any]],
    *,
    lookbacks: tuple[int, ...] = (6, 12, 24),
    atr_window: int = 14,
    volume_window: int = 100,
) -> dict[int, dict[int, dict[str, float]]]:
    atr = atr_series(rows, atr_window)
    ema50 = ema_series(rows, 50)
    ema200 = ema_series(rows, 200)
    features: dict[int, dict[int, dict[str, float]]] = {}
    warmup = max(max(lookbacks), atr_window, volume_window, 220)
    for index in range(warmup, len(rows) - 1):
        close = safe_float(rows[index].get("close"))
        high = safe_float(rows[index].get("high"))
        low = safe_float(rows[index].get("low"))
        volume = safe_float(rows[index].get("volume"))
        oi = safe_float(rows[index].get("open_interest"))
        funding = safe_float(rows[index].get("funding"))
        current_atr = atr[index]
        if None in {close, high, low, volume, oi, funding} or current_atr is None or current_atr <= 0:
            continue
        prior_volumes = [safe_float(row.get("volume")) for row in rows[index - volume_window : index]]
        clean_volumes = [value for value in prior_volumes if value is not None]
        if len(clean_volumes) < volume_window:
            continue
        sigma = statistics.pstdev(clean_volumes)
        volume_z = (float(volume) - statistics.mean(clean_volumes)) / sigma if sigma > 0 else 0.0
        candle_range = max(float(high) - float(low), 1e-12)
        close_location = (float(close) - float(low)) / candle_range
        by_lookback: dict[int, dict[str, float]] = {}
        for lookback in lookbacks:
            previous = rows[index - lookback]
            previous_close = safe_float(previous.get("close"))
            previous_oi = safe_float(previous.get("open_interest"))
            if previous_close is None or previous_oi in {None, 0.0}:
                continue
            by_lookback[lookback] = {
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
            }
        if by_lookback:
            features[index] = by_lookback
    return features


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def regime_matches(config: EventConfig, feature: dict[str, float]) -> bool:
    if config.regime_filter == "none":
        return True
    close = feature.get("close")
    ema50 = feature.get("ema50")
    ema200 = feature.get("ema200")
    ema50_slope = feature.get("ema50_slope_20")
    ema200_slope = feature.get("ema200_slope_20")
    if not all(finite(value) for value in (close, ema200, ema200_slope)):
        return False
    if config.regime_filter == "ema200_slope":
        if config.side == "LONG":
            return float(close) > float(ema200) and float(ema200_slope) > 0.0
        return float(close) < float(ema200) and float(ema200_slope) < 0.0
    if config.regime_filter == "ema50_stack":
        if not all(finite(value) for value in (ema50, ema50_slope)):
            return False
        if config.side == "LONG":
            return float(close) > float(ema50) > float(ema200) and float(ema50_slope) > 0.0 and float(ema200_slope) >= 0.0
        return float(close) < float(ema50) < float(ema200) and float(ema50_slope) < 0.0 and float(ema200_slope) <= 0.0
    raise ValueError(f"unsupported regime_filter: {config.regime_filter}")


def signal_matches(config: EventConfig, feature: dict[str, float]) -> bool:
    if not regime_matches(config, feature):
        return False
    price = feature["price_move_atr"]
    oi = feature["oi_delta_pct"]
    funding = feature["funding"]
    volume_z = feature["volume_z"]
    close_loc = feature["close_location"]
    if config.family == "oi_build_fade":
        if config.side == "SHORT":
            return price >= config.price_atr and oi >= config.oi_pct and funding >= config.funding_abs
        return price <= -config.price_atr and oi >= config.oi_pct and funding <= -config.funding_abs
    if config.family == "oi_build_continuation":
        if config.side == "LONG":
            return price >= config.price_atr and oi >= config.oi_pct and funding <= config.funding_abs and close_loc >= config.close_location
        return price <= -config.price_atr and oi >= config.oi_pct and funding >= -config.funding_abs and close_loc <= 1.0 - config.close_location
    if config.family == "deleveraging_reversal":
        if volume_z < config.volume_z or oi > -config.oi_pct:
            return False
        if config.side == "LONG":
            return price <= -config.price_atr and close_loc >= config.close_location
        return price >= config.price_atr and close_loc <= 1.0 - config.close_location
    if config.family == "squeeze_exhaustion_fade":
        if volume_z < config.volume_z or oi > -config.oi_pct:
            return False
        if config.side == "SHORT":
            return price >= config.price_atr and close_loc <= 1.0 - config.close_location
        return price <= -config.price_atr and close_loc >= config.close_location
    if config.family == "funding_extreme_fade":
        if config.side == "SHORT":
            return funding >= config.funding_abs and price >= max(0.25, config.price_atr * 0.5)
        return funding <= -config.funding_abs and price <= -max(0.25, config.price_atr * 0.5)
    raise ValueError(f"unsupported family: {config.family}")


def simulate_exit(
    config: EventConfig,
    rows: list[dict[str, Any]],
    signal_index: int,
    atr: float,
    *,
    cost_bps_per_side: float,
) -> dict[str, Any] | None:
    entry_index = signal_index + 1
    if entry_index >= len(rows):
        return None
    entry = safe_float(rows[entry_index].get("open"))
    if entry is None or atr <= 0:
        return None
    if config.side == "LONG":
        stop = entry - config.stop_atr * atr
        take = entry + config.take_atr * atr
    else:
        stop = entry + config.stop_atr * atr
        take = entry - config.take_atr * atr
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    exit_price = entry
    exit_reason = "time"
    exit_index = min(len(rows) - 1, entry_index + config.max_hold_bars)
    for index in range(entry_index, min(len(rows), entry_index + config.max_hold_bars + 1)):
        high = safe_float(rows[index].get("high"))
        low = safe_float(rows[index].get("low"))
        close = safe_float(rows[index].get("close"))
        if high is None or low is None or close is None:
            continue
        if config.side == "LONG":
            stop_hit = low <= stop
            take_hit = high >= take
            if stop_hit and take_hit:
                exit_price, exit_reason, exit_index = stop, "same_bar_stop_first", index
                break
            if stop_hit:
                exit_price, exit_reason, exit_index = stop, "stop", index
                break
            if take_hit:
                exit_price, exit_reason, exit_index = take, "take", index
                break
        else:
            stop_hit = high >= stop
            take_hit = low <= take
            if stop_hit and take_hit:
                exit_price, exit_reason, exit_index = stop, "same_bar_stop_first", index
                break
            if stop_hit:
                exit_price, exit_reason, exit_index = stop, "stop", index
                break
            if take_hit:
                exit_price, exit_reason, exit_index = take, "take", index
                break
        exit_price = close
        exit_index = index
    gross_r = (exit_price - entry) / risk if config.side == "LONG" else (entry - exit_price) / risk
    fee_r = ((entry + exit_price) * cost_bps_per_side / 10_000.0) / risk
    return {
        "entry_index": entry_index,
        "exit_index": exit_index,
        "entry": entry,
        "exit": exit_price,
        "net_r": gross_r - fee_r,
        "exit_reason": exit_reason,
    }


def simulate_window(
    config: EventConfig,
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
    while index < safe_end:
        feature = features.get(index, {}).get(config.lookback)
        if feature is None or not signal_matches(config, feature):
            index += 1
            continue
        outcome = simulate_exit(config, rows[:end_index], index, feature["atr"], cost_bps_per_side=cost_bps_per_side)
        if outcome is None:
            index += 1
            continue
        trades.append(
            {
                "strategy_id": config.strategy_id,
                "family": config.family,
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
                "volume_z": round(feature["volume_z"], 6),
                "close_location": round(feature["close_location"], 6),
            }
        )
        index = int(outcome["exit_index"]) + 1 if no_overlap else index + 1
    return trades


def stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    values = [safe_float(row.get("net_r")) for row in trades]
    clean = [value for value in values if value is not None]
    wins = [value for value in clean if value > 0]
    losses = [value for value in clean if value <= 0]
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    losing_streak = 0
    max_losing_streak = 0
    for value in clean:
        equity += value
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
        if value <= 0:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        else:
            losing_streak = 0
    return {
        "trades": len(clean),
        "wins": len(wins),
        "losses": len(losses),
        "winrate_pct": round(len(wins) / len(clean) * 100.0, 3) if clean else None,
        "expectancy_r": round(sum(clean) / len(clean), 6) if clean else None,
        "net_r_total": round(sum(clean), 6) if clean else 0.0,
        "max_drawdown_r": round(max_dd, 6),
        "max_losing_streak": max_losing_streak,
    }


def fold_stats(trades: list[dict[str, Any]], folds: int = 4) -> list[dict[str, Any]]:
    if not trades:
        return []
    size = max(1, math.ceil(len(trades) / max(1, folds)))
    output: list[dict[str, Any]] = []
    for fold in range(max(1, folds)):
        chunk = trades[fold * size : (fold + 1) * size]
        if not chunk:
            continue
        output.append({"fold": fold + 1, **stats(chunk)})
    return output


def stable_folds(folds: list[dict[str, Any]], min_trades: int = 3) -> int:
    return sum(
        1
        for row in folds
        if int(row.get("trades") or 0) >= min_trades
        and safe_float(row.get("expectancy_r")) is not None
        and float(row["expectancy_r"]) > 0
    )


def gate(summary: dict[str, Any], folds: list[dict[str, Any]], *, min_trades: int, min_expectancy: float, min_stable_folds: int, max_drawdown: float) -> dict[str, Any]:
    checks = {
        "min_trades": int(summary.get("trades") or 0) >= min_trades,
        "min_expectancy": safe_float(summary.get("expectancy_r")) is not None and float(summary["expectancy_r"]) >= min_expectancy,
        "min_stable_folds": stable_folds(folds) >= min_stable_folds,
        "max_drawdown": float(summary.get("max_drawdown_r") or 0.0) >= -abs(max_drawdown),
    }
    return {"pass": all(checks.values()), "checks": checks}


def evaluate_config(
    config: EventConfig,
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


def parse_float_grid(value: str) -> list[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def parse_int_grid(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def parse_str_grid(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def build_configs(args: argparse.Namespace, interval: str) -> list[EventConfig]:
    configs: list[EventConfig] = []
    allowed_families = set(parse_str_grid(args.families))
    allowed_sides = set(item.upper() for item in parse_str_grid(args.sides))
    for family in ("oi_build_fade", "oi_build_continuation", "deleveraging_reversal", "squeeze_exhaustion_fade", "funding_extreme_fade"):
        if family not in allowed_families:
            continue
        for side in ("LONG", "SHORT"):
            if side not in allowed_sides:
                continue
            for lookback in parse_int_grid(args.lookbacks):
                for price_atr in parse_float_grid(args.price_atr):
                    for oi_pct in parse_float_grid(args.oi_pct):
                        for funding_abs in parse_float_grid(args.funding_abs):
                            for volume_z in parse_float_grid(args.volume_z):
                                for close_location in parse_float_grid(args.close_location):
                                    for regime_filter in parse_str_grid(args.regime_filters):
                                        for take_atr in parse_float_grid(args.take_atr):
                                            for hold in parse_int_grid(args.max_hold_bars):
                                                strategy_id = (
                                                    f"deriv_{family}_{interval}_{side.lower()}_lb{lookback}"
                                                    f"_p{price_atr:g}_oi{oi_pct:g}_f{funding_abs:g}"
                                                    f"_vz{volume_z:g}_cl{close_location:g}_rg{regime_filter}"
                                                    f"_rr{args.stop_atr:g}x{take_atr:g}_h{hold}"
                                                )
                                                configs.append(
                                                    EventConfig(
                                                        strategy_id=strategy_id,
                                                        family=family,
                                                        side=side,
                                                        interval=interval,
                                                        lookback=lookback,
                                                        price_atr=price_atr,
                                                        oi_pct=oi_pct,
                                                        funding_abs=funding_abs,
                                                        volume_z=volume_z,
                                                        close_location=close_location,
                                                        regime_filter=regime_filter,
                                                        stop_atr=args.stop_atr,
                                                        take_atr=take_atr,
                                                        max_hold_bars=hold,
                                                    )
                                                )
    return configs


def diversified_limit(configs: list[EventConfig], limit: int) -> list[EventConfig]:
    if limit <= 0 or len(configs) <= limit:
        return configs
    buckets: dict[tuple[str, str], list[EventConfig]] = {}
    for config in configs:
        buckets.setdefault((config.family, config.side), []).append(config)
    output: list[EventConfig] = []
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


def load_interval(cache_dir: Path, interval: str) -> tuple[list[dict[str, Any]], dict[int, dict[int, dict[str, float]]], dict[str, Any]]:
    symbol_dir = cache_dir / "futures" / "BTCUSDT"
    klines_path = symbol_dir / f"{interval}_klines.csv"
    derivatives_path = symbol_dir / f"{interval}_oi_aligned.csv"
    rows = join_rows(read_csv(klines_path), read_csv(derivatives_path))
    features = build_features(rows)
    oi_rows = sum(1 for row in rows if row.get("open_interest") is not None)
    funding_rows = sum(1 for row in rows if row.get("funding") is not None)
    meta = {
        "interval": interval,
        "klines_path": rel(klines_path),
        "derivatives_path": rel(derivatives_path),
        "rows": len(rows),
        "features": len(features),
        "first_time": rows[0].get("time") if rows else None,
        "last_time": rows[-1].get("time") if rows else None,
        "oi_coverage_pct": round(oi_rows / len(rows) * 100.0, 3) if rows else 0.0,
        "funding_coverage_pct": round(funding_rows / len(rows) * 100.0, 3) if rows else 0.0,
    }
    return rows, features, meta


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Derivatives Event Edge Miner",
        "",
        f"- Generated: `{report['generated_at']}`.",
        f"- Decision: `{report['decision']}`.",
        "- Boundary: research-only, no paper/live permission, no credentials, no orders.",
        "",
        "## Summary",
        "",
        f"- Intervals: `{', '.join(report['settings']['intervals'])}`.",
        f"- Tested configs: `{report['summary']['tested']}`.",
        f"- Train-qualified: `{report['summary']['train_qualified']}`.",
        f"- Validation-qualified: `{report['summary']['validation_qualified']}`.",
        f"- OOS decision: `{report['summary']['oos_decision']}`.",
        "",
        "## Data",
        "",
        "| TF | rows | features | first | last | OI % | funding % |",
        "|---|---:|---:|---|---|---:|---:|",
    ]
    for item in report["data"]:
        lines.append(
            f"| `{item['interval']}` | `{item['rows']}` | `{item['features']}` | `{item['first_time']}` | `{item['last_time']}` | `{item['oi_coverage_pct']}` | `{item['funding_coverage_pct']}` |"
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
    parser = argparse.ArgumentParser(description="Nested OI/funding derivatives-event edge miner for BTCUSDT")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--intervals", default="1h,4h")
    parser.add_argument("--families", default="oi_build_fade,oi_build_continuation,deleveraging_reversal,squeeze_exhaustion_fade,funding_extreme_fade")
    parser.add_argument("--sides", default="LONG,SHORT")
    parser.add_argument("--regime-filters", default="none")
    parser.add_argument("--train-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2026-01-01T00:00:00+00:00")
    parser.add_argument("--lookbacks", default="6,12,24")
    parser.add_argument("--price-atr", default="0.8,1.2,1.8")
    parser.add_argument("--oi-pct", default="0.25,0.5,1.0")
    parser.add_argument("--funding-abs", default="0.0002,0.0005,0.0008")
    parser.add_argument("--volume-z", default="0,1.0,1.8")
    parser.add_argument("--close-location", default="0.55,0.65")
    parser.add_argument("--stop-atr", type=float, default=1.0)
    parser.add_argument("--take-atr", default="1.5,2.0,3.0")
    parser.add_argument("--max-hold-bars", default="8,16")
    parser.add_argument("--max-configs-per-interval", type=int, default=400)
    parser.add_argument("--validation-top", type=int, default=30)
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
    parser.add_argument("--out-prefix", default="docs/DERIVATIVES_EVENT_EDGE_MINER_2026-06-25")
    args = parser.parse_args()

    cache_dir = resolve_path(args.cache_dir)
    intervals = [item.strip() for item in args.intervals.split(",") if item.strip()]
    cost_bps = args.fee_bps + args.slippage_bps
    all_train: list[dict[str, Any]] = []
    data_meta: list[dict[str, Any]] = []
    interval_payloads: dict[str, tuple[list[dict[str, Any]], dict[int, dict[int, dict[str, float]]], int, int]] = {}

    for interval in intervals:
        rows, features, meta = load_interval(cache_dir, interval)
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
        config = EventConfig(**item["config"])
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
        config = EventConfig(**selected["config"])
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
            "train_end": args.train_end,
            "validation_end": args.validation_end,
            "cost_bps_per_side": cost_bps,
            "max_configs_per_interval": args.max_configs_per_interval,
            "validation_top": args.validation_top,
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
        "next_action": "register_for_forward_observer_review_only" if decision.startswith("oos_pass") else "mine_new_event_features_or_relax_only_with_new_prereg_budget",
        "runtime_boundary": {"research_only": True, "paper_allowed": False, "live_allowed": False, "orders_allowed": False, "can_trade": False},
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

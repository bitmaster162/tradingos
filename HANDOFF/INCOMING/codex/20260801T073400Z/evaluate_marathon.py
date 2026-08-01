#!/usr/bin/env python3
"""Evaluate the three frozen TradingOS M1 research hypotheses.

The evaluator is stdlib-only, reads immutable public archives, and has no
exchange, account, scheduler, runtime, or order interface.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import random
import statistics
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence


H01 = "M1_H01_PRESSURE_OI_ABSORPTION"
H02 = "M1_H02_BTC_SFP_ETH_SMT_TRIGGER"
H03 = "M1_H03_REGIME_HIDDEN_RSI_CONTINUATION"
HYPOTHESES = (H01, H02, H03)
MINUTE_MS = 60_000
HOUR_MS = 3_600_000
BAR15_MS = 15 * MINUTE_MS
TOTAL_COST = 0.0012
BOOTSTRAP_N = 10_000
BOOTSTRAP_SEED = 8102
CAL_START = 1_767_225_600_000  # 2026-01-01T00:00:00Z
OOS_START = 1_782_864_000_000  # 2026-07-01T00:00:00Z
OOS_SPLIT = 1_784_160_000_000  # 2026-07-16T00:00:00Z
OOS_END = 1_785_456_000_000  # 2026-07-31T00:00:00Z


@dataclass(frozen=True)
class Bar:
    open_ts: int
    close_ts: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Metric:
    ts: int
    open_interest: float
    taker_ratio: float


@dataclass(frozen=True)
class PressureFeature:
    signal_close_ts: int
    entry_ts: int
    entry_open: float
    exit_1h_ts: int
    exit_1h_open: float
    exit_4h_ts: int
    exit_4h_open: float
    oi_z_30d: float
    oi_change_4h: float
    taker_ratio: float
    signal_return_1h: float
    trend_24h: float
    rv_24h: float
    utc_hour: int


@dataclass(frozen=True)
class Observation:
    hypothesis_id: str
    direction: int
    signal_ts: int
    trigger_ts: int
    entry_ts: int
    secondary_exit_ts: int
    primary_exit_ts: int
    secondary_gross_return: float
    primary_gross_return: float
    secondary_net_edge: float
    primary_net_edge: float
    cost_return: float
    half: str
    controls: str = ""


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def normalize_ms(raw: str | int) -> int:
    value = int(raw)
    while value > 99_999_999_999_999:
        value //= 1000
    return value


def parse_utc_ms(raw: str) -> int:
    parsed = datetime.fromisoformat(raw.strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def read_zip_lines(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
        if len(members) != 1:
            raise ValueError(f"{path}: expected exactly one CSV member")
        return archive.read(members[0]).decode("utf-8-sig").splitlines()


def load_bars(paths: Iterable[Path]) -> list[Bar]:
    values: dict[int, Bar] = {}
    for path in sorted(paths):
        for row in csv.reader(read_zip_lines(path)):
            if not row or not row[0].lstrip("-").isdigit():
                continue
            open_ts = normalize_ms(row[0])
            close_ts = normalize_ms(row[6])
            values[open_ts] = Bar(
                open_ts=open_ts,
                close_ts=close_ts,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
            )
    return [values[key] for key in sorted(values)]


def load_metrics(paths: Iterable[Path]) -> list[Metric]:
    values: dict[int, Metric] = {}
    for path in sorted(paths):
        for row in csv.DictReader(read_zip_lines(path)):
            oi = row.get("sum_open_interest")
            ratio = row.get("sum_taker_long_short_vol_ratio")
            created = row.get("create_time")
            if not oi or not ratio or not created:
                continue
            ts = parse_utc_ms(created)
            values[ts] = Metric(ts, float(oi), float(ratio))
    return [values[key] for key in sorted(values)]


def latest_index_at_or_before(times: Sequence[int], ts: int) -> int:
    return bisect.bisect_right(times, ts) - 1


def quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("cannot calculate quantile of empty sample")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def contiguous(bars: Sequence[Bar], start: int, stop: int, step_ms: int) -> bool:
    return all(
        bars[index].open_ts - bars[index - 1].open_ts == step_ms
        for index in range(start + 1, stop + 1)
    )


def half_of(ts: int) -> str:
    return "JULY_FIRST" if ts < OOS_SPLIT else "JULY_SECOND"


def bootstrap_lower(values: Sequence[float], seed: int = BOOTSTRAP_SEED) -> float | None:
    if not values:
        return None
    rng = random.Random(seed)
    size = len(values)
    means = [
        sum(rng.choice(values) for _ in range(size)) / size
        for _ in range(BOOTSTRAP_N)
    ]
    means.sort()
    return means[int(0.025 * BOOTSTRAP_N)]


def summarize(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "bootstrap_95_lower_mean": None,
            "win_rate": None,
            "sum": None,
        }
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "bootstrap_95_lower_mean": bootstrap_lower(values),
        "win_rate": sum(value > 0 for value in values) / len(values),
        "sum": sum(values),
    }


def evidence(observations: Sequence[Observation]) -> dict[str, object]:
    primary = [item.primary_net_edge for item in observations]
    secondary = [item.secondary_net_edge for item in observations]
    first = [item for item in observations if item.half == "JULY_FIRST"]
    second = [item for item in observations if item.half == "JULY_SECOND"]
    return {
        "full": {"primary": summarize(primary), "secondary": summarize(secondary)},
        "chronological_halves": {
            "first": summarize([item.primary_net_edge for item in first]),
            "second": summarize([item.primary_net_edge for item in second]),
        },
    }


def classify(
    observations: Sequence[Observation], coverage: float
) -> tuple[str, str]:
    if coverage < 0.95:
        return "INSUFFICIENT_DATA", "frozen source/feature coverage below 95 percent"
    values = [item.primary_net_edge for item in observations]
    secondary = [item.secondary_net_edge for item in observations]
    n = len(values)
    if n < 3:
        return "INSUFFICIENT_DATA", "fewer than three independent observations"
    mean = statistics.fmean(values)
    if n < 10:
        if mean <= 0:
            return "KILL", "3-9 observations and non-positive primary mean net edge"
        return "INSUFFICIENT_DATA", "3-9 positive observations cannot pass the frozen keep gate"
    first = [item.primary_net_edge for item in observations if item.half == "JULY_FIRST"]
    second = [item.primary_net_edge for item in observations if item.half == "JULY_SECOND"]
    keep = (
        mean > 0
        and statistics.median(values) > 0
        and (bootstrap_lower(values) or float("-inf")) > 0
        and bool(first)
        and bool(second)
        and statistics.fmean(first) >= 0
        and statistics.fmean(second) >= 0
        and statistics.fmean(secondary) >= 0
    )
    if keep:
        return "KEEP_FOR_FORWARD_PAPER", "all frozen mean, median, bootstrap, split, and secondary gates passed"
    return "KILL", "one or more frozen robustness gates failed"


def date_tokens(start: str, end: str) -> list[str]:
    current = date.fromisoformat(start)
    final = date.fromisoformat(end)
    values = []
    while current <= final:
        values.append(current.isoformat())
        current += timedelta(days=1)
    return values


def expand_plan(plan: dict[str, object]) -> list[dict[str, str]]:
    base = str(plan["base_url"]).rstrip("/")
    records: list[dict[str, str]] = []
    for series in plan["series"]:
        tokens = series.get("tokens") or date_tokens(series["start"], series["end"])
        for token in tokens:
            name = str(series["filename_template"]).format(token=token)
            relative = f"{series['folder']}/{name}"
            records.append(
                {
                    "source_class": str(series["source_class"]),
                    "archive_granularity": str(series["archive_granularity"]),
                    "source_id": f"binance-vision:{relative}",
                    "url": f"{base}/{relative}",
                    "path": relative,
                }
            )
    if len(records) != int(plan["expected_file_count"]):
        raise ValueError("expanded source count differs from frozen plan")
    if len({item["source_id"] for item in records}) != len(records):
        raise ValueError("frozen source plan contains duplicates")
    return sorted(records, key=lambda item: item["source_id"])


def verify_sources(
    data: Path, plan: dict[str, object], manifest: dict[str, object]
) -> list[dict[str, object]]:
    expected = expand_plan(plan)
    actual = sorted(manifest["files"], key=lambda item: str(item["source_id"]))
    if len(actual) != len(expected):
        raise ValueError("source manifest count mismatch")
    for wanted, observed in zip(expected, actual):
        for field in (
            "source_class",
            "archive_granularity",
            "source_id",
            "url",
            "path",
        ):
            if wanted[field] != observed[field]:
                raise ValueError(f"source manifest mismatch: {field}")
        path = data / str(observed["path"])
        if not path.is_file():
            raise ValueError(f"missing frozen source: {path}")
        if path.stat().st_size != int(observed["bytes"]) or digest(path) != observed["sha256"]:
            raise ValueError(f"frozen source hash mismatch: {path}")
    return actual


def source_files(root: Path, fragment: str) -> list[Path]:
    return sorted(path for path in root.rglob("*.zip") if fragment in path.as_posix())


def build_pressure_features(
    bars: Sequence[Bar], metrics: Sequence[Metric]
) -> tuple[list[PressureFeature], dict[str, int]]:
    metric_times = [item.ts for item in metrics]
    aligned: list[Metric | None] = []
    for bar in bars:
        index = latest_index_at_or_before(metric_times, bar.close_ts)
        item = metrics[index] if index >= 0 else None
        if item is not None and bar.close_ts - item.ts > 10 * MINUTE_MS:
            item = None
        aligned.append(item)

    features: list[PressureFeature] = []
    candidate_oos = 0
    complete_oos = 0
    for index in range(720, len(bars) - 5):
        if not contiguous(bars, index - 720, index + 5, HOUR_MS):
            continue
        if OOS_START <= bars[index].close_ts < OOS_END - 4 * HOUR_MS:
            candidate_oos += 1
        current = aligned[index]
        four_hours_ago = aligned[index - 4]
        window = aligned[index - 720 : index]
        if current is None or four_hours_ago is None or any(item is None for item in window):
            continue
        oi_values = [item.open_interest for item in window if item is not None]
        oi_std = statistics.pstdev(oi_values)
        if oi_std <= 0 or four_hours_ago.open_interest <= 0:
            continue
        returns = [
            bars[position].close / bars[position - 1].close - 1.0
            for position in range(index - 23, index + 1)
        ]
        features.append(
            PressureFeature(
                signal_close_ts=bars[index].close_ts,
                entry_ts=bars[index + 1].open_ts,
                entry_open=bars[index + 1].open,
                exit_1h_ts=bars[index + 2].open_ts,
                exit_1h_open=bars[index + 2].open,
                exit_4h_ts=bars[index + 5].open_ts,
                exit_4h_open=bars[index + 5].open,
                oi_z_30d=(current.open_interest - statistics.fmean(oi_values)) / oi_std,
                oi_change_4h=current.open_interest / four_hours_ago.open_interest - 1.0,
                taker_ratio=current.taker_ratio,
                signal_return_1h=bars[index].close / bars[index - 1].close - 1.0,
                trend_24h=bars[index].close / bars[index - 24].close - 1.0,
                rv_24h=statistics.pstdev(returns),
                utc_hour=datetime.fromtimestamp(bars[index + 1].open_ts / 1000, timezone.utc).hour,
            )
        )
        if OOS_START <= bars[index].close_ts < OOS_END - 4 * HOUR_MS:
            complete_oos += 1
    return features, {"candidate_oos": candidate_oos, "complete_oos": complete_oos}


def pressure_thresholds(calibration: Sequence[PressureFeature]) -> dict[str, float]:
    if not calibration:
        raise ValueError("pressure calibration sample is empty")
    return {
        "oi_z_q60": quantile([item.oi_z_30d for item in calibration], 0.60),
        "oi_change_q70_nonnegative": max(0.0, quantile([item.oi_change_4h for item in calibration], 0.70)),
        "taker_q15": quantile([item.taker_ratio for item in calibration], 0.15),
        "taker_q85": quantile([item.taker_ratio for item in calibration], 0.85),
        "rv_q33": quantile([item.rv_24h for item in calibration], 1 / 3),
        "rv_q67": quantile([item.rv_24h for item in calibration], 2 / 3),
    }


def pressure_direction(item: PressureFeature, thresholds: dict[str, float]) -> int:
    base = (
        item.oi_z_30d >= thresholds["oi_z_q60"]
        and item.oi_change_4h >= thresholds["oi_change_q70_nonnegative"]
    )
    if not base:
        return 0
    if item.taker_ratio >= thresholds["taker_q85"] and item.signal_return_1h <= 0:
        return -1
    if item.taker_ratio <= thresholds["taker_q15"] and item.signal_return_1h >= 0:
        return 1
    return 0


def volatility_regime(item: PressureFeature, thresholds: dict[str, float]) -> str:
    if item.rv_24h <= thresholds["rv_q33"]:
        return "LOW"
    if item.rv_24h <= thresholds["rv_q67"]:
        return "MID"
    return "HIGH"


def select_pressure_signals(
    features: Sequence[PressureFeature], thresholds: dict[str, float]
) -> tuple[list[tuple[PressureFeature, int]], int]:
    raw = [(item, pressure_direction(item, thresholds)) for item in features]
    raw = [(item, direction) for item, direction in raw if direction]
    selected: list[tuple[PressureFeature, int]] = []
    last_exit = -1
    for item, direction in raw:
        if item.entry_ts < last_exit:
            continue
        selected.append((item, direction))
        last_exit = item.exit_4h_ts
    return selected, len(raw)


def evaluate_h01(
    bars: Sequence[Bar], metrics: Sequence[Metric]
) -> tuple[list[Observation], dict[str, object], float]:
    features, counts = build_pressure_features(bars, metrics)
    calibration = [item for item in features if CAL_START <= item.signal_close_ts < OOS_START]
    oos = [item for item in features if OOS_START <= item.signal_close_ts < OOS_END - 4 * HOUR_MS]
    thresholds = pressure_thresholds(calibration)
    signals, raw_count = select_pressure_signals(oos, thresholds)
    signal_times = [item.entry_ts for item, _ in signals]
    controls = [item for item in oos if pressure_direction(item, thresholds) == 0]
    observations: list[Observation] = []
    unmatched = 0
    for signal, direction in signals:
        eligible = [
            item
            for item in controls
            if half_of(item.entry_ts) == half_of(signal.entry_ts)
            and volatility_regime(item, thresholds) == volatility_regime(signal, thresholds)
            and (item.trend_24h >= 0) == (signal.trend_24h >= 0)
            and item.utc_hour == signal.utc_hour
            and all(abs(item.entry_ts - ts) > 4 * HOUR_MS for ts in signal_times)
        ]
        eligible.sort(key=lambda item: (abs(item.entry_ts - signal.entry_ts), item.entry_ts))
        selected = eligible[:5]
        if len(selected) != 5:
            unmatched += 1
            continue
        signal_1h = signal.exit_1h_open / signal.entry_open - 1.0
        signal_4h = signal.exit_4h_open / signal.entry_open - 1.0
        control_1h = statistics.fmean(item.exit_1h_open / item.entry_open - 1.0 for item in selected)
        control_4h = statistics.fmean(item.exit_4h_open / item.entry_open - 1.0 for item in selected)
        observations.append(
            Observation(
                hypothesis_id=H01,
                direction=direction,
                signal_ts=signal.signal_close_ts,
                trigger_ts=signal.signal_close_ts,
                entry_ts=signal.entry_ts,
                secondary_exit_ts=signal.exit_1h_ts,
                primary_exit_ts=signal.exit_4h_ts,
                secondary_gross_return=direction * signal_1h,
                primary_gross_return=direction * signal_4h,
                secondary_net_edge=direction * (signal_1h - control_1h) - TOTAL_COST,
                primary_net_edge=direction * (signal_4h - control_4h) - TOTAL_COST,
                cost_return=TOTAL_COST,
                half=half_of(signal.entry_ts),
                controls=";".join(str(item.entry_ts) for item in selected),
            )
        )
    coverage = counts["complete_oos"] / counts["candidate_oos"] if counts["candidate_oos"] else 0.0
    diagnostics = {
        "calibration_features": len(calibration),
        "oos_features": len(oos),
        "raw_signals": raw_count,
        "non_overlapping_signals": len(signals),
        "unmatched_signals": unmatched,
        "thresholds": thresholds,
        "feature_counts": counts,
    }
    return observations, diagnostics, coverage


def evaluate_h02(
    btc: Sequence[Bar], eth: Sequence[Bar]
) -> tuple[list[Observation], dict[str, object], float]:
    btc_map = {item.open_ts: item for item in btc if OOS_START <= item.open_ts < OOS_END}
    eth_map = {item.open_ts: item for item in eth if OOS_START <= item.open_ts < OOS_END}
    timestamps = sorted(set(btc_map) & set(eth_map))
    aligned_btc = [btc_map[ts] for ts in timestamps]
    aligned_eth = [eth_map[ts] for ts in timestamps]
    expected = int((min(OOS_END, max(timestamps) + BAR15_MS) - OOS_START) / BAR15_MS) if timestamps else 0
    coverage = len(timestamps) / expected if expected else 0.0
    observations: list[Observation] = []
    raw_signals = 0
    last_exit = -1
    for index in range(24, len(timestamps) - 18):
        if not contiguous(aligned_btc, index - 24, index + 18, BAR15_MS):
            continue
        signal = aligned_btc[index]
        eth_signal = aligned_eth[index]
        prior_btc_high = max(item.high for item in aligned_btc[index - 24 : index])
        prior_btc_low = min(item.low for item in aligned_btc[index - 24 : index])
        prior_eth_high = max(item.high for item in aligned_eth[index - 24 : index])
        prior_eth_low = min(item.low for item in aligned_eth[index - 24 : index])
        bearish = signal.high > prior_btc_high and signal.close < prior_btc_high and eth_signal.high <= prior_eth_high
        bullish = signal.low < prior_btc_low and signal.close > prior_btc_low and eth_signal.low >= prior_eth_low
        if bearish == bullish:
            continue
        direction = -1 if bearish else 1
        trigger = aligned_btc[index + 1]
        midpoint = (signal.high + signal.low) / 2.0
        confirmed = (
            trigger.close < midpoint and trigger.close < signal.close
            if bearish
            else trigger.close > midpoint and trigger.close > signal.close
        )
        if not confirmed:
            continue
        raw_signals += 1
        entry = aligned_btc[index + 2]
        exit_1h = aligned_btc[index + 6]
        exit_4h = aligned_btc[index + 18]
        if entry.open_ts < last_exit:
            continue
        gross_1h = direction * (exit_1h.open / entry.open - 1.0)
        gross_4h = direction * (exit_4h.open / entry.open - 1.0)
        observations.append(
            Observation(
                hypothesis_id=H02,
                direction=direction,
                signal_ts=signal.close_ts,
                trigger_ts=trigger.close_ts,
                entry_ts=entry.open_ts,
                secondary_exit_ts=exit_1h.open_ts,
                primary_exit_ts=exit_4h.open_ts,
                secondary_gross_return=gross_1h,
                primary_gross_return=gross_4h,
                secondary_net_edge=gross_1h - TOTAL_COST,
                primary_net_edge=gross_4h - TOTAL_COST,
                cost_return=TOTAL_COST,
                half=half_of(entry.open_ts),
            )
        )
        last_exit = exit_4h.open_ts
    diagnostics = {
        "btc_oos_bars": len(btc_map),
        "eth_oos_bars": len(eth_map),
        "aligned_bars": len(timestamps),
        "expected_through_last_source_bar": expected,
        "raw_confirmed_signals": raw_signals,
        "non_overlapping_signals": len(observations),
    }
    return observations, diagnostics, coverage


def ema(values: Sequence[float], period: int) -> list[float | None]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    output: list[float | None] = [None] * len(values)
    current = values[0]
    for index, value in enumerate(values):
        current = value if index == 0 else alpha * value + (1.0 - alpha) * current
        if index >= period - 1:
            output[index] = current
    return output


def wilder_rsi(values: Sequence[float], period: int = 14) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return output
    gains = [max(values[index] - values[index - 1], 0.0) for index in range(1, len(values))]
    losses = [max(values[index - 1] - values[index], 0.0) for index in range(1, len(values))]
    avg_gain = statistics.fmean(gains[:period])
    avg_loss = statistics.fmean(losses[:period])

    def value(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    output[period] = value(avg_gain, avg_loss)
    for index in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[index - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[index - 1]) / period
        output[index] = value(avg_gain, avg_loss)
    return output


def strict_pivots(bars: Sequence[Bar], radius: int = 3) -> tuple[list[int], list[int]]:
    lows: list[int] = []
    highs: list[int] = []
    for index in range(radius, len(bars) - radius):
        neighbors = [position for position in range(index - radius, index + radius + 1) if position != index]
        if all(bars[index].low < bars[position].low for position in neighbors):
            lows.append(index)
        if all(bars[index].high > bars[position].high for position in neighbors):
            highs.append(index)
    return lows, highs


def evaluate_h03(bars: Sequence[Bar]) -> tuple[list[Observation], dict[str, object], float]:
    closes = [item.close for item in bars]
    rsi = wilder_rsi(closes, 14)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    lows, highs = strict_pivots(bars, 3)
    candidates: list[tuple[int, int, int]] = []
    for pivots, direction in ((lows, 1), (highs, -1)):
        for previous, current in zip(pivots, pivots[1:]):
            if current - previous > 72 or rsi[previous] is None or rsi[current] is None:
                continue
            confirm = current + 3
            if confirm + 13 >= len(bars) or confirm < 24:
                continue
            if ema50[confirm] is None or ema200[confirm] is None or ema200[confirm - 24] is None:
                continue
            bullish = (
                direction == 1
                and bars[current].low > bars[previous].low
                and rsi[current] < rsi[previous]
                and bars[confirm].close > ema200[confirm]
                and ema50[confirm] > ema200[confirm]
                and ema200[confirm] > ema200[confirm - 24]
            )
            bearish = (
                direction == -1
                and bars[current].high < bars[previous].high
                and rsi[current] > rsi[previous]
                and bars[confirm].close < ema200[confirm]
                and ema50[confirm] < ema200[confirm]
                and ema200[confirm] < ema200[confirm - 24]
            )
            if bullish or bearish:
                candidates.append((confirm, direction, current))
    candidates.sort()
    observations: list[Observation] = []
    oos_raw = 0
    last_exit = -1
    for confirm, direction, pivot in candidates:
        signal_ts = bars[confirm].close_ts
        if not (OOS_START <= signal_ts < OOS_END - 12 * HOUR_MS):
            continue
        oos_raw += 1
        entry = bars[confirm + 1]
        exit_4h = bars[confirm + 5]
        exit_12h = bars[confirm + 13]
        if not contiguous(bars, confirm, confirm + 13, HOUR_MS):
            continue
        if entry.open_ts < last_exit:
            continue
        gross_4h = direction * (exit_4h.open / entry.open - 1.0)
        gross_12h = direction * (exit_12h.open / entry.open - 1.0)
        observations.append(
            Observation(
                hypothesis_id=H03,
                direction=direction,
                signal_ts=bars[pivot].close_ts,
                trigger_ts=bars[confirm].close_ts,
                entry_ts=entry.open_ts,
                secondary_exit_ts=exit_4h.open_ts,
                primary_exit_ts=exit_12h.open_ts,
                secondary_gross_return=gross_4h,
                primary_gross_return=gross_12h,
                secondary_net_edge=gross_4h - TOTAL_COST,
                primary_net_edge=gross_12h - TOTAL_COST,
                cost_return=TOTAL_COST,
                half=half_of(entry.open_ts),
            )
        )
        last_exit = exit_12h.open_ts
    oos_bars = [item for item in bars if OOS_START <= item.open_ts < OOS_END]
    expected = int((min(OOS_END, max((item.open_ts for item in oos_bars), default=OOS_START) + HOUR_MS) - OOS_START) / HOUR_MS)
    coverage = len(oos_bars) / expected if expected else 0.0
    diagnostics = {
        "bars": len(bars),
        "oos_bars": len(oos_bars),
        "expected_through_last_source_bar": expected,
        "strict_pivot_lows": len(lows),
        "strict_pivot_highs": len(highs),
        "raw_oos_signals": oos_raw,
        "non_overlapping_signals": len(observations),
        "pivot_confirmation_delay_bars": 3,
    }
    return observations, diagnostics, coverage


def write_observations(path: Path, observations: Sequence[Observation]) -> None:
    fields = list(Observation.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(asdict(item) for item in observations)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--prereg", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    prereg = json.loads(args.prereg.read_text(encoding="utf-8"))
    if prereg.get("hypothesis_count") != 3 or tuple(item["id"] for item in prereg["hypotheses"]) != HYPOTHESES:
        raise ValueError("preregistration identity does not match evaluator")
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    source_records = verify_sources(args.data, plan, manifest)

    btc_1h = load_bars(source_files(args.data, "klines/BTCUSDT/1h"))
    btc_15m = load_bars(source_files(args.data, "klines/BTCUSDT/15m"))
    eth_15m = load_bars(source_files(args.data, "klines/ETHUSDT/15m"))
    metrics = load_metrics(source_files(args.data, "daily/metrics/BTCUSDT"))

    evaluations = {
        H01: evaluate_h01(btc_1h, metrics),
        H02: evaluate_h02(btc_15m, eth_15m),
        H03: evaluate_h03(btc_1h),
    }
    all_observations: list[Observation] = []
    results: dict[str, object] = {}
    for hypothesis_id in HYPOTHESES:
        observations, diagnostics, coverage = evaluations[hypothesis_id]
        disposition, reason = classify(observations, coverage)
        all_observations.extend(observations)
        results[hypothesis_id] = {
            "disposition": disposition,
            "reason": reason,
            "coverage": coverage,
            "observations": len(observations),
            "evidence": evidence(observations),
            "diagnostics": diagnostics,
        }

    args.out.mkdir(parents=True, exist_ok=True)
    write_observations(args.out / "OBSERVATION_LEDGER.csv", all_observations)
    for hypothesis_id in HYPOTHESES:
        write_observations(
            args.out / f"{hypothesis_id}.csv",
            [item for item in all_observations if item.hypothesis_id == hypothesis_id],
        )
    threshold_document = {
        "schema": "TRADINGOS_EDGE_RESEARCH_M1_THRESHOLDS_V1",
        "derived_from_calibration_only": True,
        "h01": results[H01]["diagnostics"]["thresholds"],
        "h02": {"lookback_bars": 24, "trigger_delay_bars": 1},
        "h03": {"rsi_period": 14, "ema_periods": [50, 200], "pivot_radius": 3, "pivot_pair_max_bars": 72},
    }
    (args.out / "THRESHOLDS.json").write_text(
        json.dumps(threshold_document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    result = {
        "schema": "TRADINGOS_EDGE_RESEARCH_MARATHON_M1_RESULT_V1",
        "terminal": "EDGE_RESEARCH_M1_COMPLETE",
        "hypothesis_count": 3,
        "results": results,
        "source_files": len(source_records),
        "cost_ledger": {
            "entry_fee": 0.0005,
            "exit_fee": 0.0005,
            "entry_slippage": 0.0001,
            "exit_slippage": 0.0001,
            "total_deducted_once": TOTAL_COST,
        },
        "same_snapshot_entry_exit": False,
        "adaptive_changes_after_freeze": False,
        "can_trade": False,
        "capital_permission": "DENY",
        "deploy_permission": "DENY",
        "self_application": False,
        "no_further_agent_work": True,
    }
    (args.out / "RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.out / "CYCLE_RESULT.json").write_text(
        json.dumps(
            {
                "schema": "TRADINGOS_EDGE_RESEARCH_MARATHON_M1_CYCLE_V1",
                "terminal": "EDGE_RESEARCH_M1_COMPLETE",
                "dispositions": {key: value["disposition"] for key, value in results.items()},
                "can_trade": False,
                "capital_permission": "DENY",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    locators = {
        "schema": "TRADINGOS_EDGE_RESEARCH_M1_RAW_EVIDENCE_LOCATORS_V1",
        "source_manifest_sha256": digest(args.source_manifest),
        "source_plan_sha256": digest(args.plan),
        "preregistration_sha256": digest(args.prereg),
        "source_files": [
            {"path": item["path"], "sha256": item["sha256"], "bytes": item["bytes"]}
            for item in source_records
        ],
    }
    (args.out / "RAW_EVIDENCE_LOCATORS.json").write_text(
        json.dumps(locators, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# Trading Edge Research Marathon M1",
        "",
        "Exactly three preregistered hypotheses were evaluated once on the frozen July 2026 OOS set.",
        "",
    ]
    for hypothesis_id in HYPOTHESES:
        item = results[hypothesis_id]
        lines.extend(
            [
                f"## {hypothesis_id}",
                "",
                f"**Disposition:** `{item['disposition']}`",
                "",
                f"Reason: {item['reason']}.",
                f"Independent observations: {item['observations']}.",
                f"Coverage: {item['coverage']:.6f}.",
                f"Primary evidence: `{json.dumps(item['evidence']['full']['primary'], sort_keys=True)}`",
                f"Secondary evidence: `{json.dumps(item['evidence']['full']['secondary'], sort_keys=True)}`",
                "",
            ]
        )
    lines.extend(
        [
            "No adaptive parameter change, replacement hypothesis, live execution, or runtime mutation occurred.",
            "",
            "`can_trade=false`",
            "",
            "`capital_permission=DENY`",
            "",
        ]
    )
    (args.out / "FALSIFICATION_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(
        json.dumps(
            {
                "terminal": "EDGE_RESEARCH_M1_COMPLETE",
                "dispositions": {key: value["disposition"] for key, value in results.items()},
                "observations": {key: value["observations"] for key, value in results.items()},
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

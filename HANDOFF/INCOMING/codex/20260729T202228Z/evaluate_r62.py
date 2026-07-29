#!/usr/bin/env python3
"""Evaluate the single frozen R62 BTC crowding-exhaustion hypothesis."""

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
from typing import Iterable


HYPOTHESIS = "BTC_CROWDING_EXHAUSTION"
BOOTSTRAP_SEED = 6202
BOOTSTRAP_N = 10_000
HOUR_MS = 3_600_000
TOTAL_COST = 0.0012
CAL_START = 1_751_328_000_000  # 2025-07-01T00:00:00Z
OOS_START = 1_767_225_600_000  # 2026-01-01T00:00:00Z
OOS_SPLIT = 1_775_001_600_000  # 2026-04-01T00:00:00Z
OOS_END = 1_782_864_000_000  # 2026-07-01T00:00:00Z


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
    top_position_ratio: float


@dataclass(frozen=True)
class Feature:
    signal_bar_open_ts: int
    signal_close_ts: int
    entry_ts: int
    entry_open: float
    exit_1h_ts: int
    exit_1h_open: float
    exit_4h_ts: int
    exit_4h_open: float
    oi_level_z_30d: float
    oi_change_4h: float
    funding_rate: float
    funding_ts: int
    funding_age_bucket: int
    top_position_ratio: float
    ret_1h: float
    ret_4h: float
    ret_24h: float
    rv_24h: float


@dataclass(frozen=True)
class Observation:
    signal_close_ts: int
    entry_ts: int
    exit_1h_ts: int
    exit_4h_ts: int
    signal_return_1h: float
    signal_return_4h: float
    control_return_1h: float
    control_return_4h: float
    matched_alpha_1h: float
    matched_alpha_4h: float
    short_net_1h: float
    short_net_4h: float
    cost_return: float
    half: str
    vol_regime: str
    funding_age_bucket: int
    control_entry_timestamps: str


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
            raise ValueError(f"{path}: expected one CSV member")
        return archive.read(members[0]).decode("utf-8-sig").splitlines()


def load_bars(paths: Iterable[Path]) -> list[Bar]:
    values: dict[int, Bar] = {}
    for path in sorted(paths):
        for row in csv.reader(read_zip_lines(path)):
            if not row or not row[0].lstrip("-").isdigit():
                continue
            open_ts = normalize_ms(row[0])
            close_ts = normalize_ms(row[6]) + 1 if len(row) > 6 else open_ts + HOUR_MS
            values[open_ts] = Bar(
                open_ts,
                close_ts,
                float(row[1]),
                float(row[2]),
                float(row[3]),
                float(row[4]),
            )
    return [values[key] for key in sorted(values)]


def load_metrics(paths: Iterable[Path]) -> list[Metric]:
    values: dict[int, Metric] = {}
    for path in sorted(paths):
        for row in csv.DictReader(read_zip_lines(path)):
            if not row.get("sum_open_interest") or not row.get(
                "sum_toptrader_long_short_ratio"
            ):
                continue
            ts = parse_utc_ms(row["create_time"])
            values[ts] = Metric(
                ts,
                float(row["sum_open_interest"]),
                float(row["sum_toptrader_long_short_ratio"]),
            )
    return [values[key] for key in sorted(values)]


def load_funding(paths: Iterable[Path]) -> list[tuple[int, float]]:
    values: dict[int, float] = {}
    for path in sorted(paths):
        for row in csv.DictReader(read_zip_lines(path)):
            values[normalize_ms(row["calc_time"])] = float(row["last_funding_rate"])
    return sorted(values.items())


def latest_index_at_or_before(times: list[int], ts: int) -> int:
    return bisect.bisect_right(times, ts) - 1


def quantile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("cannot calculate a quantile of an empty sample")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def contiguous(bars: list[Bar], start: int, stop: int) -> bool:
    return all(
        bars[index].open_ts - bars[index - 1].open_ts == HOUR_MS
        for index in range(start + 1, stop + 1)
    )


def build_features(
    bars: list[Bar], metrics: list[Metric], funding: list[tuple[int, float]]
) -> tuple[list[Feature], dict[str, int]]:
    metric_times = [item.ts for item in metrics]
    funding_times = [item[0] for item in funding]
    aligned_metrics: list[Metric | None] = []
    aligned_funding: list[tuple[int, float] | None] = []
    for bar in bars:
        metric_index = latest_index_at_or_before(metric_times, bar.close_ts)
        metric = metrics[metric_index] if metric_index >= 0 else None
        if metric is not None and bar.close_ts - metric.ts > 10 * 60_000:
            metric = None
        aligned_metrics.append(metric)

        funding_index = latest_index_at_or_before(funding_times, bar.close_ts)
        funding_value = funding[funding_index] if funding_index >= 0 else None
        if funding_value is not None and bar.close_ts - funding_value[0] > 8 * HOUR_MS:
            funding_value = None
        aligned_funding.append(funding_value)

    features: list[Feature] = []
    candidate_oos = 0
    complete_oos = 0
    for index in range(720, len(bars) - 5):
        if not contiguous(bars, index - 720, index + 5):
            continue
        signal_close_ts = bars[index].close_ts
        if OOS_START <= signal_close_ts < OOS_END - 4 * HOUR_MS:
            candidate_oos += 1
        metric = aligned_metrics[index]
        metric_4h = aligned_metrics[index - 4]
        funding_value = aligned_funding[index]
        oi_window = aligned_metrics[index - 720 : index]
        if (
            metric is None
            or metric_4h is None
            or funding_value is None
            or any(item is None for item in oi_window)
        ):
            continue
        oi_values = [item.open_interest for item in oi_window if item is not None]
        oi_mean = statistics.fmean(oi_values)
        oi_std = statistics.pstdev(oi_values)
        if oi_std <= 0 or metric_4h.open_interest <= 0:
            continue
        hourly_returns = [
            bars[position].close / bars[position - 1].close - 1.0
            for position in range(index - 23, index + 1)
        ]
        funding_ts, funding_rate = funding_value
        feature = Feature(
            signal_bar_open_ts=bars[index].open_ts,
            signal_close_ts=signal_close_ts,
            entry_ts=bars[index + 1].open_ts,
            entry_open=bars[index + 1].open,
            exit_1h_ts=bars[index + 2].open_ts,
            exit_1h_open=bars[index + 2].open,
            exit_4h_ts=bars[index + 5].open_ts,
            exit_4h_open=bars[index + 5].open,
            oi_level_z_30d=(metric.open_interest - oi_mean) / oi_std,
            oi_change_4h=metric.open_interest / metric_4h.open_interest - 1.0,
            funding_rate=funding_rate,
            funding_ts=funding_ts,
            funding_age_bucket=int((signal_close_ts - funding_ts) // HOUR_MS),
            top_position_ratio=metric.top_position_ratio,
            ret_1h=bars[index].close / bars[index - 1].close - 1.0,
            ret_4h=bars[index].close / bars[index - 4].close - 1.0,
            ret_24h=bars[index].close / bars[index - 24].close - 1.0,
            rv_24h=statistics.pstdev(hourly_returns),
        )
        features.append(feature)
        if OOS_START <= signal_close_ts < OOS_END - 4 * HOUR_MS:
            complete_oos += 1
    return features, {
        "candidate_oos_feature_bars": candidate_oos,
        "complete_oos_feature_bars": complete_oos,
    }


def frozen_thresholds(calibration: list[Feature]) -> dict[str, dict[str, float]]:
    if not calibration:
        raise ValueError("calibration feature sample is empty")

    def band(oi_q: float, change_q: float, funding_q: float, ratio_q: float) -> dict[str, float]:
        return {
            "oi_level_z_min": quantile(
                [item.oi_level_z_30d for item in calibration], oi_q
            ),
            "oi_change_4h_min": max(
                0.0, quantile([item.oi_change_4h for item in calibration], change_q)
            ),
            "funding_rate_min": max(
                0.0, quantile([item.funding_rate for item in calibration], funding_q)
            ),
            "top_position_ratio_min": quantile(
                [item.top_position_ratio for item in calibration], ratio_q
            ),
        }

    return {
        "primary": band(0.65, 0.55, 0.60, 0.65),
        "neighbor_sensitivity": band(0.60, 0.50, 0.55, 0.60),
        "volatility_regime": {
            "low_max": quantile([item.rv_24h for item in calibration], 1 / 3),
            "mid_max": quantile([item.rv_24h for item in calibration], 2 / 3),
        },
    }


def is_signal(item: Feature, band: dict[str, float]) -> bool:
    return (
        item.oi_level_z_30d >= band["oi_level_z_min"]
        and item.oi_change_4h >= band["oi_change_4h_min"]
        and item.funding_rate >= band["funding_rate_min"]
        and item.top_position_ratio >= band["top_position_ratio_min"]
        and item.ret_24h > 0
        and item.ret_4h <= 0
        and item.ret_1h <= 0
    )


def select_non_overlapping(features: list[Feature], band: dict[str, float]) -> tuple[list[Feature], int]:
    raw = [item for item in features if is_signal(item, band)]
    selected: list[Feature] = []
    last_exit = -1
    for item in raw:
        if item.entry_ts < last_exit:
            continue
        selected.append(item)
        last_exit = item.exit_4h_ts
    return selected, len(raw)


def half_of(ts: int) -> str:
    return "H1_2026_FIRST" if ts < OOS_SPLIT else "H1_2026_SECOND"


def vol_regime(value: float, thresholds: dict[str, float]) -> str:
    if value <= thresholds["low_max"]:
        return "LOW"
    if value <= thresholds["mid_max"]:
        return "MID"
    return "HIGH"


def feature_return(item: Feature, horizon: str) -> float:
    exit_open = item.exit_1h_open if horizon == "1h" else item.exit_4h_open
    return exit_open / item.entry_open - 1.0


def match_observations(
    oos_features: list[Feature],
    signals: list[Feature],
    band: dict[str, float],
    vol_thresholds: dict[str, float],
) -> tuple[list[Observation], int]:
    signal_times = [item.entry_ts for item in signals]
    candidates = [item for item in oos_features if not is_signal(item, band)]
    observations: list[Observation] = []
    unmatched = 0
    for signal in signals:
        signal_regime = vol_regime(signal.rv_24h, vol_thresholds)
        eligible = []
        for candidate in candidates:
            if half_of(candidate.entry_ts) != half_of(signal.entry_ts):
                continue
            if vol_regime(candidate.rv_24h, vol_thresholds) != signal_regime:
                continue
            if (candidate.ret_24h >= 0) != (signal.ret_24h >= 0):
                continue
            if candidate.funding_age_bucket != signal.funding_age_bucket:
                continue
            if any(abs(candidate.entry_ts - ts) <= 4 * HOUR_MS for ts in signal_times):
                continue
            eligible.append(candidate)
        eligible.sort(
            key=lambda item: (abs(item.entry_ts - signal.entry_ts), item.entry_ts)
        )
        controls = eligible[:5]
        if len(controls) != 5:
            unmatched += 1
            continue
        signal_1h = feature_return(signal, "1h")
        signal_4h = feature_return(signal, "4h")
        control_1h = statistics.fmean(feature_return(item, "1h") for item in controls)
        control_4h = statistics.fmean(feature_return(item, "4h") for item in controls)
        observations.append(
            Observation(
                signal_close_ts=signal.signal_close_ts,
                entry_ts=signal.entry_ts,
                exit_1h_ts=signal.exit_1h_ts,
                exit_4h_ts=signal.exit_4h_ts,
                signal_return_1h=signal_1h,
                signal_return_4h=signal_4h,
                control_return_1h=control_1h,
                control_return_4h=control_4h,
                matched_alpha_1h=control_1h - signal_1h - TOTAL_COST,
                matched_alpha_4h=control_4h - signal_4h - TOTAL_COST,
                short_net_1h=-signal_1h - TOTAL_COST,
                short_net_4h=-signal_4h - TOTAL_COST,
                cost_return=TOTAL_COST,
                half=half_of(signal.entry_ts),
                vol_regime=signal_regime,
                funding_age_bucket=signal.funding_age_bucket,
                control_entry_timestamps=";".join(
                    str(item.entry_ts) for item in controls
                ),
            )
        )
    return observations, unmatched


def bootstrap_lower(values: list[float]) -> float | None:
    if not values:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    size = len(values)
    means = [
        sum(rng.choice(values) for _ in range(size)) / size
        for _ in range(BOOTSTRAP_N)
    ]
    means.sort()
    return means[int(0.025 * BOOTSTRAP_N)]


def horizon_metrics(observations: list[Observation], horizon: str) -> dict[str, object]:
    alpha_name = f"matched_alpha_{horizon}"
    short_name = f"short_net_{horizon}"
    signal_name = f"signal_return_{horizon}"
    control_name = f"control_return_{horizon}"
    alpha = [getattr(item, alpha_name) for item in observations]
    return {
        "n": len(observations),
        "mean_matched_underperformance_after_cost": (
            statistics.fmean(alpha) if alpha else None
        ),
        "median_matched_underperformance_after_cost": (
            statistics.median(alpha) if alpha else None
        ),
        "bootstrap_95_lower_mean": bootstrap_lower(alpha),
        "matched_win_rate": (
            sum(value > 0 for value in alpha) / len(alpha) if alpha else None
        ),
        "mean_short_net_return": (
            statistics.fmean(getattr(item, short_name) for item in observations)
            if observations
            else None
        ),
        "mean_signal_return": (
            statistics.fmean(getattr(item, signal_name) for item in observations)
            if observations
            else None
        ),
        "mean_control_return": (
            statistics.fmean(getattr(item, control_name) for item in observations)
            if observations
            else None
        ),
    }


def evidence(observations: list[Observation]) -> dict[str, object]:
    first = [item for item in observations if item.half == "H1_2026_FIRST"]
    second = [item for item in observations if item.half == "H1_2026_SECOND"]
    return {
        "full": {
            "1h": horizon_metrics(observations, "1h"),
            "4h": horizon_metrics(observations, "4h"),
        },
        "chronological_halves": {
            "first": {
                "1h": horizon_metrics(first, "1h"),
                "4h": horizon_metrics(first, "4h"),
            },
            "second": {
                "1h": horizon_metrics(second, "1h"),
                "4h": horizon_metrics(second, "4h"),
            },
        },
    }


def disposition(
    primary: dict[str, object],
    sensitivity: dict[str, object],
    feature_coverage: float,
) -> tuple[str, str]:
    full = primary["full"]
    halves = primary["chronological_halves"]
    n = int(full["4h"]["n"])
    if feature_coverage < 0.95:
        return "INSUFFICIENT_DATA", "OOS source/feature coverage below 95 percent"
    if n < 30:
        return "INSUFFICIENT_DATA", "fewer than 30 matched primary OOS signals"
    values = (
        full["4h"]["mean_matched_underperformance_after_cost"],
        full["4h"]["median_matched_underperformance_after_cost"],
        full["4h"]["bootstrap_95_lower_mean"],
        halves["first"]["4h"]["mean_matched_underperformance_after_cost"],
        halves["second"]["4h"]["mean_matched_underperformance_after_cost"],
        full["1h"]["mean_matched_underperformance_after_cost"],
        sensitivity["full"]["4h"]["mean_matched_underperformance_after_cost"],
    )
    if any(value is None for value in values):
        return "KILL", "one or more frozen robustness statistics are unavailable"
    keep = (
        values[0] > 0
        and values[1] > 0
        and values[2] > 0
        and values[3] > 0
        and values[4] > 0
        and values[5] >= 0
        and values[6] >= 0
    )
    if keep:
        return "KEEP_FOR_LARGER_FORWARD_WATCH", "all frozen OOS gates passed"
    return "KILL", "one or more frozen OOS or robustness gates failed"


def expand_plan(plan: dict[str, object]) -> list[dict[str, str]]:
    base = str(plan["base_url"]).rstrip("/")

    def dates(start: str, end: str) -> list[date]:
        current = date.fromisoformat(start)
        final = date.fromisoformat(end)
        values = []
        while current <= final:
            values.append(current)
            current += timedelta(days=1)
        return values

    daily_period = {
        day.isoformat(): period
        for period, bounds in plan["periods"].items()
        for day in dates(bounds["start"], bounds["end"])
    }
    monthly_period: dict[str, str] = {}
    for token, period in daily_period.items():
        monthly_period[token[:7]] = period
    records: list[dict[str, str]] = []
    for series in plan["series"]:
        granularity = str(series["archive_granularity"])
        token_period = daily_period if granularity == "daily" else monthly_period
        for token in sorted(token_period):
            name = str(series["filename_template"]).format(token=token)
            relative = f"{series['folder']}/{name}"
            records.append(
                {
                    "period": token_period[token],
                    "archive_granularity": granularity,
                    "source_class": str(series["source_class"]),
                    "source_id": f"binance-vision:{relative}",
                    "url": f"{base}/{relative}",
                    "path": relative,
                }
            )
    if len(records) != int(plan["expected_file_count"]):
        raise ValueError("frozen source plan count mismatch")
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
            "period",
            "archive_granularity",
            "source_class",
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


def files(root: Path, fragment: str) -> list[Path]:
    return sorted(path for path in root.rglob("*.zip") if fragment in path.as_posix())


def write_observations(path: Path, observations: list[Observation]) -> None:
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
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    source_manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    source_records = verify_sources(args.data, plan, source_manifest)

    bars = load_bars(files(args.data, "futures/um/monthly/klines/BTCUSDT/1h"))
    metrics_rows = load_metrics(
        files(args.data, "futures/um/daily/metrics/BTCUSDT")
    )
    funding = load_funding(
        files(args.data, "futures/um/monthly/fundingRate/BTCUSDT")
    )
    features, coverage_counts = build_features(bars, metrics_rows, funding)
    calibration = [
        item for item in features if CAL_START <= item.signal_close_ts < OOS_START
    ]
    oos = [
        item
        for item in features
        if OOS_START <= item.signal_close_ts < OOS_END - 4 * HOUR_MS
    ]
    thresholds = frozen_thresholds(calibration)
    primary_signals, primary_raw = select_non_overlapping(
        oos, thresholds["primary"]
    )
    sensitivity_signals, sensitivity_raw = select_non_overlapping(
        oos, thresholds["neighbor_sensitivity"]
    )
    primary_observations, primary_unmatched = match_observations(
        oos,
        primary_signals,
        thresholds["primary"],
        thresholds["volatility_regime"],
    )
    sensitivity_observations, sensitivity_unmatched = match_observations(
        oos,
        sensitivity_signals,
        thresholds["neighbor_sensitivity"],
        thresholds["volatility_regime"],
    )
    primary_evidence = evidence(primary_observations)
    sensitivity_evidence = evidence(sensitivity_observations)
    candidate_count = coverage_counts["candidate_oos_feature_bars"]
    feature_coverage = (
        coverage_counts["complete_oos_feature_bars"] / candidate_count
        if candidate_count
        else 0.0
    )
    decision, reason = disposition(
        primary_evidence, sensitivity_evidence, feature_coverage
    )

    args.out.mkdir(parents=True, exist_ok=True)
    write_observations(args.out / "PRIMARY_SIGNALS.csv", primary_observations)
    write_observations(
        args.out / "NEIGHBOR_SENSITIVITY_SIGNALS.csv", sensitivity_observations
    )
    threshold_document = {
        "schema": "TRADINGOS_R62_FROZEN_THRESHOLDS_V1",
        "calibration_feature_count": len(calibration),
        "thresholds": thresholds,
        "calibration_period": ["2025-07-01T00:00:00Z", "2026-01-01T00:00:00Z"],
        "derived_before_oos_evaluation": True,
    }
    (args.out / "THRESHOLDS.json").write_text(
        json.dumps(threshold_document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result = {
        "schema": "TRADINGOS_R62_BTC_CROWDING_RESULT_V1",
        "hypothesis": HYPOTHESIS,
        "decision": decision,
        "reason": reason,
        "primary": {
            "raw_signal_count": primary_raw,
            "non_overlapping_signal_count": len(primary_signals),
            "unmatched_signal_count": primary_unmatched,
            "matched_signal_count": len(primary_observations),
            "evidence": primary_evidence,
        },
        "neighbor_sensitivity": {
            "raw_signal_count": sensitivity_raw,
            "non_overlapping_signal_count": len(sensitivity_signals),
            "unmatched_signal_count": sensitivity_unmatched,
            "matched_signal_count": len(sensitivity_observations),
            "evidence": sensitivity_evidence,
            "selected_as_replacement": False,
        },
        "data_quality": {
            **coverage_counts,
            "feature_coverage": feature_coverage,
            "kline_bars": len(bars),
            "metrics_rows": len(metrics_rows),
            "funding_rows": len(funding),
            "source_files": len(source_records),
        },
        "cost_ledger": {
            "entry_fee": 0.0005,
            "exit_fee": 0.0005,
            "entry_slippage": 0.0001,
            "exit_slippage": 0.0001,
            "total_deducted_once": TOTAL_COST,
        },
        "same_snapshot_entry_exit": False,
        "parameter_changes_after_freeze": False,
        "can_trade": False,
        "capital_permission": "DENY",
    }
    (args.out / "RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    cycle = {
        "schema": "TRADINGOS_R62_SINGLE_HYPOTHESIS_CYCLE_V1",
        "hypothesis_count": 1,
        "terminal": decision,
        "result": result,
        "can_trade": False,
        "capital_permission": "DENY",
    }
    (args.out / "CYCLE_RESULT.json").write_text(
        json.dumps(cycle, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report = [
        "# R62 BTC crowding-exhaustion falsification",
        "",
        f"**Disposition:** `{decision}`",
        "",
        f"Reason: {reason}.",
        "",
        f"- Primary matched OOS signals: {len(primary_observations)}",
        f"- Neighbor sensitivity matched signals: {len(sensitivity_observations)}",
        f"- OOS feature coverage: {feature_coverage}",
        f"- Primary +1h: `{json.dumps(primary_evidence['full']['1h'], sort_keys=True)}`",
        f"- Primary +4h: `{json.dumps(primary_evidence['full']['4h'], sort_keys=True)}`",
        (
            "- Primary chronological halves +4h: "
            f"`{json.dumps(primary_evidence['chronological_halves'], sort_keys=True)}`"
        ),
        (
            "- Neighbor sensitivity +4h: "
            f"`{json.dumps(sensitivity_evidence['full']['4h'], sort_keys=True)}`"
        ),
        "",
        "The neighboring band was not selected as a replacement. Costs were "
        "deducted once. Entry and exits use distinct bar-open snapshots.",
        "",
        "This result is research-only and cannot authorize execution.",
        "",
        "`can_trade=false`",
        "",
        "`capital_permission=DENY`",
        "",
    ]
    (args.out / "FALSIFICATION.md").write_text(
        "\n".join(report), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "decision": decision,
                "matched_primary_signals": len(primary_observations),
                "matched_sensitivity_signals": len(sensitivity_observations),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Strategy-neutral deterministic research primitives for synthetic/OOS use."""

from __future__ import annotations

import math
import platform
import random
import statistics
import sys
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from .common import ContractError


@dataclass(frozen=True)
class CostModel:
    fees_bps: float
    spread_bps: float
    slippage_bps: float
    funding_bps: float = 0.0
    latency_bps: float = 0.0
    adverse_multiplier: float = 1.0

    def total_return_cost(self, adverse: bool = False) -> float:
        base = self.fees_bps + self.spread_bps + self.slippage_bps + self.funding_bps + self.latency_bps
        return base * (self.adverse_multiplier if adverse else 1.0) / 10_000.0


def extract_events(
    rows: Iterable[dict[str, Any]],
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timestamp_key: str = "timestamp",
    id_key: str = "event_id",
) -> list[dict[str, Any]]:
    events = []
    for row in rows:
        if timestamp_key not in row or id_key not in row:
            raise ContractError("EVENT_CONTRACT_MISSING_FIELD", "event row lacks timestamp or ID")
        if predicate(row):
            events.append(dict(row))
    events.sort(key=lambda item: (int(item[timestamp_key]), str(item[id_key])))
    if len({str(item[id_key]) for item in events}) != len(events):
        raise ContractError("DUPLICATE_EVENT_ID", "event IDs must be unique before clustering")
    return events


def cluster_events(
    events: Sequence[dict[str, Any]], cluster_gap_seconds: int
) -> list[dict[str, Any]]:
    if cluster_gap_seconds < 0:
        raise ContractError("INVALID_CLUSTER_GAP", "cluster gap cannot be negative")
    ordered = sorted(events, key=lambda item: (int(item["timestamp"]), str(item["event_id"])))
    clusters: list[list[dict[str, Any]]] = []
    for event in ordered:
        if not clusters or int(event["timestamp"]) - int(clusters[-1][-1]["timestamp"]) > cluster_gap_seconds:
            clusters.append([event])
        else:
            clusters[-1].append(event)
    return [
        {
            "cluster_id": f"cluster-{index:06d}",
            "first_timestamp": int(group[0]["timestamp"]),
            "last_timestamp": int(group[-1]["timestamp"]),
            "event_count": len(group),
            "event_ids": [str(item["event_id"]) for item in group],
            "representative_event": dict(group[0]),
        }
        for index, group in enumerate(clusters, start=1)
    ]


def assign_time_splits(
    records: Sequence[dict[str, Any]], intervals: dict[str, tuple[int, int]]
) -> dict[str, list[dict[str, Any]]]:
    ordered_names = ("train", "validation", "final_test")
    if any(name not in intervals for name in ordered_names):
        raise ContractError("MISSING_SPLIT_INTERVAL", "train, validation, and final_test are required")
    bounds = [intervals[name] for name in ordered_names]
    if not (bounds[0][0] < bounds[0][1] < bounds[1][0] < bounds[1][1] < bounds[2][0] < bounds[2][1]):
        raise ContractError("CONTAMINATED_OOS_INTERVAL", "split intervals must be strictly ordered")
    output = {name: [] for name in ordered_names}
    for record in records:
        timestamp = int(record["timestamp"])
        for name in ordered_names:
            start, end = intervals[name]
            if start <= timestamp < end:
                output[name].append(dict(record))
                break
    return output


def purge_training_overlap(
    samples: Sequence[dict[str, Any]], evaluation_start: int, purge_seconds: int, embargo_seconds: int
) -> list[dict[str, Any]]:
    if purge_seconds < 0 or embargo_seconds < 0:
        raise ContractError("INVALID_PURGE_EMBARGO", "purge and embargo must be non-negative")
    boundary = evaluation_start - purge_seconds - embargo_seconds
    return [dict(item) for item in samples if int(item["outcome_end_timestamp"]) < boundary]


def walk_forward_windows(
    total: int,
    minimum_train: int,
    validation_size: int,
    test_size: int,
    step: int,
    *,
    anchored: bool = True,
) -> list[dict[str, tuple[int, int]]]:
    if min(total, minimum_train, validation_size, test_size, step) <= 0:
        raise ContractError("INVALID_WALK_FORWARD", "window sizes must be positive")
    windows = []
    test_end = minimum_train + validation_size + test_size
    while test_end <= total:
        test_start = test_end - test_size
        validation_start = test_start - validation_size
        train_start = 0 if anchored else max(0, validation_start - minimum_train)
        windows.append(
            {
                "train": (train_start, validation_start),
                "validation": (validation_start, test_start),
                "final_test": (test_start, test_end),
            }
        )
        test_end += step
    return windows


def apply_costs(gross_returns: Sequence[float], model: CostModel, *, adverse: bool = False) -> list[float]:
    cost = model.total_return_cost(adverse=adverse)
    return [float(value) - cost for value in gross_returns]


def delayed_entry_sensitivity(
    prices: Sequence[float], event_indices: Sequence[int], directions: Sequence[int], delays: Sequence[int], horizon: int, model: CostModel
) -> dict[str, list[float]]:
    if len(event_indices) != len(directions) or horizon <= 0:
        raise ContractError("INVALID_DELAYED_ENTRY_INPUT", "events, directions, and horizon are inconsistent")
    output: dict[str, list[float]] = {}
    for delay in delays:
        values = []
        for index, direction in zip(event_indices, directions):
            entry = index + int(delay)
            exit_index = entry + horizon
            if entry < 0 or exit_index >= len(prices) or prices[entry] <= 0:
                continue
            gross = int(direction) * (float(prices[exit_index]) / float(prices[entry]) - 1.0)
            values.append(gross - model.total_return_cost())
        output[str(delay)] = values
    return output


def moving_block_bootstrap_means(values: Sequence[float], block_size: int, repetitions: int, seed: int) -> list[float]:
    if not values or block_size <= 0 or repetitions <= 0:
        raise ContractError("INVALID_BOOTSTRAP_INPUT", "bootstrap requires values and positive sizes")
    rng = random.Random(seed)
    size = len(values)
    starts = list(range(size))
    means = []
    for _ in range(repetitions):
        sample = []
        while len(sample) < size:
            start = rng.choice(starts)
            sample.extend(values[(start + offset) % size] for offset in range(block_size))
        means.append(statistics.fmean(sample[:size]))
    return means


def stationary_bootstrap_means(values: Sequence[float], restart_probability: float, repetitions: int, seed: int) -> list[float]:
    if not values or not 0 < restart_probability <= 1 or repetitions <= 0:
        raise ContractError("INVALID_BOOTSTRAP_INPUT", "stationary bootstrap inputs are invalid")
    rng = random.Random(seed)
    size = len(values)
    means = []
    for _ in range(repetitions):
        index = rng.randrange(size)
        sample = []
        for _position in range(size):
            sample.append(values[index])
            if rng.random() < restart_probability:
                index = rng.randrange(size)
            else:
                index = (index + 1) % size
        means.append(statistics.fmean(sample))
    return means


def percentile(values: Sequence[float], probability: float) -> float:
    if not values or not 0 <= probability <= 1:
        raise ContractError("INVALID_PERCENTILE_INPUT", "percentile input is invalid")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def sign_permutation_pvalue(values: Sequence[float], repetitions: int, seed: int) -> float:
    if not values or repetitions <= 0:
        raise ContractError("INVALID_PERMUTATION_INPUT", "permutation requires values and repetitions")
    observed = abs(statistics.fmean(values))
    rng = random.Random(seed)
    exceed = 0
    for _ in range(repetitions):
        permuted = [float(value) * (-1 if rng.random() < 0.5 else 1) for value in values]
        if abs(statistics.fmean(permuted)) >= observed:
            exceed += 1
    return (exceed + 1) / (repetitions + 1)


def holm_adjust(pvalues: dict[str, float]) -> dict[str, float]:
    ordered = sorted(pvalues.items(), key=lambda item: (float(item[1]), item[0]))
    total = len(ordered)
    adjusted: dict[str, float] = {}
    running = 0.0
    for index, (name, value) in enumerate(ordered):
        candidate = min(1.0, (total - index) * float(value))
        running = max(running, candidate)
        adjusted[name] = running
    return {name: adjusted[name] for name in sorted(adjusted)}


def grouped_ablation(values: Sequence[float], groups: Sequence[str]) -> dict[str, Any]:
    if len(values) != len(groups) or not values:
        raise ContractError("INVALID_ABLATION_INPUT", "values and groups must be non-empty and aligned")
    unique = sorted(set(groups))
    by_group = {
        group: {
            "n": sum(item == group for item in groups),
            "mean": statistics.fmean(value for value, item in zip(values, groups) if item == group),
        }
        for group in unique
    }
    removed = {}
    for group in unique:
        remaining = [value for value, item in zip(values, groups) if item != group]
        removed[group] = {"n": len(remaining), "mean": statistics.fmean(remaining) if remaining else None}
    return {"by_group": by_group, "one_group_removed": removed}


def tail_sensitivity(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        raise ContractError("INVALID_TAIL_INPUT", "tail analysis requires observations")
    ordered = sorted(float(value) for value in values)
    lower = percentile(ordered, 0.05)
    upper = percentile(ordered, 0.95)
    winsorized = [min(max(value, lower), upper) for value in ordered]
    tail_count = max(1, math.ceil(len(ordered) * 0.05))
    leave_one_out = [statistics.fmean(ordered[:index] + ordered[index + 1 :]) for index in range(len(ordered))] if len(ordered) > 1 else [ordered[0]]
    total_abs = sum(abs(value) for value in ordered)
    return {
        "n": len(ordered),
        "mean": statistics.fmean(ordered),
        "median": statistics.median(ordered),
        "cvar_5": statistics.fmean(ordered[:tail_count]),
        "winsorized_mean_5_95": statistics.fmean(winsorized),
        "max_absolute_event_share": max(abs(value) for value in ordered) / total_abs if total_abs else 0.0,
        "leave_one_out_mean_min": min(leave_one_out),
        "leave_one_out_mean_max": max(leave_one_out),
    }


def environment_capture(seed: int) -> dict[str, Any]:
    return {
        "engine": "TRADING_EDGE_RESEARCH_ENGINE_M2A",
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "byteorder": sys.byteorder,
        "seed": int(seed),
        "hash_seed_requirement": "set PYTHONHASHSEED or avoid unordered iteration",
        "can_trade": False,
    }

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
import json

_ZERO = Decimal("0")
_ONE = Decimal("1")


def _to_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


@dataclass(slots=True)
class ExecutionBaseline:
    source: str = ""
    average_expected_fill_ratio: Decimal | None = None
    average_queue_clear_seconds: Decimal | None = None
    average_queue_ahead_ratio: Decimal | None = None
    average_realized_entry_fill_ratio: Decimal | None = None
    average_entry_fill_ratio_shortfall: Decimal | None = None
    average_entry_fill_latency_seconds: Decimal | None = None
    average_entry_fill_latency_overshoot_seconds: Decimal | None = None
    entry_timeout_rate: Decimal | None = None
    average_exit_depth_sweep_bps: Decimal | None = None
    average_exit_depth_coverage_ratio: Decimal | None = None
    average_exit_terminal_tail_ratio: Decimal | None = None

    @classmethod
    def from_backtest_payload(cls, payload: dict[str, object]) -> "ExecutionBaseline":
        execution_quality = payload.get("execution_quality") or {}
        summary = payload.get("summary") or {}
        return cls(
            source=str(payload.get("baseline_source", payload.get("mode", "backtest")) or "backtest"),
            average_expected_fill_ratio=_to_decimal(execution_quality.get("average_expected_fill_ratio")),
            average_queue_clear_seconds=_to_decimal(execution_quality.get("average_queue_clear_seconds")),
            average_queue_ahead_ratio=_to_decimal(execution_quality.get("average_queue_ahead_ratio")),
            average_realized_entry_fill_ratio=_to_decimal(execution_quality.get("average_realized_entry_fill_ratio")),
            average_entry_fill_ratio_shortfall=_to_decimal(execution_quality.get("average_entry_fill_ratio_shortfall")),
            average_entry_fill_latency_seconds=_to_decimal(execution_quality.get("average_entry_fill_latency_seconds")),
            average_entry_fill_latency_overshoot_seconds=_to_decimal(
                execution_quality.get("average_entry_fill_latency_overshoot_seconds")
            ),
            entry_timeout_rate=_to_decimal(execution_quality.get("entry_timeout_rate")),
            average_exit_depth_sweep_bps=_to_decimal(execution_quality.get("average_exit_depth_sweep_bps")),
            average_exit_depth_coverage_ratio=_to_decimal(execution_quality.get("average_exit_depth_coverage_ratio")),
            average_exit_terminal_tail_ratio=_to_decimal(
                execution_quality.get(
                    "average_exit_terminal_tail_ratio",
                    summary.get("average_exit_terminal_tail_ratio"),
                )
            ),
        )


@dataclass(slots=True)
class ExecutionDriftThresholds:
    min_expected_fill_ratio_factor_reduce: Decimal = Decimal("0.85")
    min_expected_fill_ratio_factor_observe: Decimal = Decimal("0.65")
    max_queue_clear_seconds_factor_reduce: Decimal = Decimal("1.50")
    max_queue_clear_seconds_factor_observe: Decimal = Decimal("2.25")
    max_queue_ahead_ratio_factor_reduce: Decimal = Decimal("1.50")
    max_queue_ahead_ratio_factor_observe: Decimal = Decimal("2.25")
    max_entry_fill_ratio_shortfall_add_reduce: Decimal = Decimal("0.10")
    max_entry_fill_ratio_shortfall_add_observe: Decimal = Decimal("0.25")
    max_entry_fill_latency_seconds_factor_reduce: Decimal = Decimal("1.50")
    max_entry_fill_latency_seconds_factor_observe: Decimal = Decimal("2.25")
    max_entry_timeout_rate_add_reduce: Decimal = Decimal("0.10")
    max_entry_timeout_rate_add_observe: Decimal = Decimal("0.20")
    max_exit_depth_sweep_bps_add_reduce: Decimal = Decimal("1.0")
    max_exit_depth_sweep_bps_add_observe: Decimal = Decimal("2.0")
    min_exit_depth_coverage_ratio_drop_reduce: Decimal = Decimal("0.15")
    min_exit_depth_coverage_ratio_drop_observe: Decimal = Decimal("0.30")
    max_terminal_tail_ratio_reduce: Decimal = Decimal("0.05")
    max_terminal_tail_ratio_observe: Decimal = Decimal("0.12")
    max_entry_reject_rate_reduce: Decimal = Decimal("0.25")
    max_entry_reject_rate_observe: Decimal = Decimal("0.50")


@dataclass(slots=True)
class ExecutionDriftDecision:
    action: str
    size_multiplier: Decimal = Decimal("1")
    score: Decimal = Decimal("0")
    moderate_breaches: int = 0
    severe_breaches: int = 0
    reasons: list[str] = field(default_factory=list)
    compared_at_ms: int = 0
    baseline_source: str = ""

    @property
    def observe_only(self) -> bool:
        return self.action == "observe_only"

    @property
    def reduce_size(self) -> bool:
        return self.action == "reduce_size"

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "ExecutionDriftDecision":
        return cls(
            action=str(payload.get("action", "trade") or "trade"),
            size_multiplier=_to_decimal(payload.get("size_multiplier")) or _ONE,
            score=_to_decimal(payload.get("score")) or _ZERO,
            moderate_breaches=int(payload.get("moderate_breaches", 0) or 0),
            severe_breaches=int(payload.get("severe_breaches", 0) or 0),
            reasons=[str(item) for item in payload.get("reasons", [])],
            compared_at_ms=int(payload.get("compared_at_ms", 0) or 0),
            baseline_source=str(payload.get("baseline_source", "") or ""),
        )


@dataclass(slots=True)
class ExecutionDriftStatus:
    iterations: int = 0
    decisions_written: int = 0
    observe_only_decisions: int = 0
    reduce_size_decisions: int = 0
    last_action: str = ""
    last_path: str = ""
    last_error: str = ""


def _entry_reject_rate(live_payload: dict[str, object]) -> Decimal | None:
    entry_attempts = int(live_payload.get("entry_attempts", 0) or 0)
    if entry_attempts <= 0:
        return None
    entries_rejected = int(live_payload.get("entries_rejected", 0) or 0)
    return Decimal(entries_rejected) / Decimal(entry_attempts)


def evaluate_execution_drift(
    *,
    live_payload: dict[str, object],
    baseline: ExecutionBaseline,
    thresholds: ExecutionDriftThresholds | None = None,
    compared_at_ms: int = 0,
) -> ExecutionDriftDecision:
    thresholds = thresholds or ExecutionDriftThresholds()
    reasons: list[str] = []
    moderate = 0
    severe = 0

    def flag(condition_reduce: bool, condition_observe: bool, reason_reduce: str, reason_observe: str) -> None:
        nonlocal moderate, severe
        if condition_observe:
            severe += 1
            reasons.append(reason_observe)
        elif condition_reduce:
            moderate += 1
            reasons.append(reason_reduce)

    fill_ratio = _to_decimal(live_payload.get("average_expected_fill_ratio"))
    if fill_ratio is not None and baseline.average_expected_fill_ratio is not None and baseline.average_expected_fill_ratio > 0:
        flag(
            fill_ratio < baseline.average_expected_fill_ratio * thresholds.min_expected_fill_ratio_factor_reduce,
            fill_ratio < baseline.average_expected_fill_ratio * thresholds.min_expected_fill_ratio_factor_observe,
            "fill_ratio_below_reduce_threshold",
            "fill_ratio_below_observe_threshold",
        )

    queue_clear = _to_decimal(live_payload.get("average_queue_clear_seconds"))
    if queue_clear is not None and baseline.average_queue_clear_seconds is not None and baseline.average_queue_clear_seconds > 0:
        flag(
            queue_clear > baseline.average_queue_clear_seconds * thresholds.max_queue_clear_seconds_factor_reduce,
            queue_clear > baseline.average_queue_clear_seconds * thresholds.max_queue_clear_seconds_factor_observe,
            "queue_clear_above_reduce_threshold",
            "queue_clear_above_observe_threshold",
        )

    queue_ahead = _to_decimal(live_payload.get("average_queue_ahead_ratio"))
    if queue_ahead is not None and baseline.average_queue_ahead_ratio is not None and baseline.average_queue_ahead_ratio > 0:
        flag(
            queue_ahead > baseline.average_queue_ahead_ratio * thresholds.max_queue_ahead_ratio_factor_reduce,
            queue_ahead > baseline.average_queue_ahead_ratio * thresholds.max_queue_ahead_ratio_factor_observe,
            "queue_ahead_above_reduce_threshold",
            "queue_ahead_above_observe_threshold",
        )

    fill_ratio_shortfall = _to_decimal(live_payload.get("average_entry_fill_ratio_shortfall"))
    if fill_ratio_shortfall is not None:
        baseline_shortfall = baseline.average_entry_fill_ratio_shortfall or _ZERO
        flag(
            fill_ratio_shortfall > baseline_shortfall + thresholds.max_entry_fill_ratio_shortfall_add_reduce,
            fill_ratio_shortfall > baseline_shortfall + thresholds.max_entry_fill_ratio_shortfall_add_observe,
            "entry_fill_shortfall_above_reduce_threshold",
            "entry_fill_shortfall_above_observe_threshold",
        )

    entry_fill_latency = _to_decimal(live_payload.get("average_entry_fill_latency_seconds"))
    if (
        entry_fill_latency is not None
        and baseline.average_entry_fill_latency_seconds is not None
        and baseline.average_entry_fill_latency_seconds > 0
    ):
        flag(
            entry_fill_latency > baseline.average_entry_fill_latency_seconds * thresholds.max_entry_fill_latency_seconds_factor_reduce,
            entry_fill_latency > baseline.average_entry_fill_latency_seconds * thresholds.max_entry_fill_latency_seconds_factor_observe,
            "entry_fill_latency_above_reduce_threshold",
            "entry_fill_latency_above_observe_threshold",
        )

    entry_timeout_rate = _to_decimal(live_payload.get("entry_timeout_rate"))
    if entry_timeout_rate is not None:
        baseline_timeout_rate = baseline.entry_timeout_rate or _ZERO
        flag(
            entry_timeout_rate > baseline_timeout_rate + thresholds.max_entry_timeout_rate_add_reduce,
            entry_timeout_rate > baseline_timeout_rate + thresholds.max_entry_timeout_rate_add_observe,
            "entry_timeout_rate_above_reduce_threshold",
            "entry_timeout_rate_above_observe_threshold",
        )

    exit_sweep = _to_decimal(live_payload.get("average_exit_depth_sweep_bps"))
    if exit_sweep is not None and baseline.average_exit_depth_sweep_bps is not None:
        flag(
            exit_sweep > baseline.average_exit_depth_sweep_bps + thresholds.max_exit_depth_sweep_bps_add_reduce,
            exit_sweep > baseline.average_exit_depth_sweep_bps + thresholds.max_exit_depth_sweep_bps_add_observe,
            "exit_sweep_above_reduce_threshold",
            "exit_sweep_above_observe_threshold",
        )

    coverage = _to_decimal(live_payload.get("average_exit_depth_coverage_ratio"))
    if coverage is not None and baseline.average_exit_depth_coverage_ratio is not None:
        flag(
            coverage < baseline.average_exit_depth_coverage_ratio - thresholds.min_exit_depth_coverage_ratio_drop_reduce,
            coverage < baseline.average_exit_depth_coverage_ratio - thresholds.min_exit_depth_coverage_ratio_drop_observe,
            "exit_coverage_below_reduce_threshold",
            "exit_coverage_below_observe_threshold",
        )

    terminal_tail_ratio = _to_decimal(live_payload.get("average_exit_terminal_tail_ratio"))
    if terminal_tail_ratio is not None:
        flag(
            terminal_tail_ratio > thresholds.max_terminal_tail_ratio_reduce,
            terminal_tail_ratio > thresholds.max_terminal_tail_ratio_observe,
            "terminal_tail_above_reduce_threshold",
            "terminal_tail_above_observe_threshold",
        )

    reject_rate = _entry_reject_rate(live_payload)
    if reject_rate is not None:
        flag(
            reject_rate > thresholds.max_entry_reject_rate_reduce,
            reject_rate > thresholds.max_entry_reject_rate_observe,
            "entry_reject_rate_above_reduce_threshold",
            "entry_reject_rate_above_observe_threshold",
        )

    if severe > 0 or (moderate >= 3):
        action = "observe_only"
        size_multiplier = _ZERO
    elif moderate == 2:
        action = "reduce_size"
        size_multiplier = Decimal("0.50")
    elif moderate == 1:
        action = "reduce_size"
        size_multiplier = Decimal("0.75")
    else:
        action = "trade"
        size_multiplier = _ONE

    score = Decimal(moderate) + Decimal(severe) * Decimal("2")
    return ExecutionDriftDecision(
        action=action,
        size_multiplier=size_multiplier,
        score=score,
        moderate_breaches=moderate,
        severe_breaches=severe,
        reasons=reasons,
        compared_at_ms=compared_at_ms,
        baseline_source=baseline.source,
    )


def load_json(path: str | Path) -> dict[str, object]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_execution_baseline(path: str | Path) -> ExecutionBaseline:
    payload = load_json(path)
    return ExecutionBaseline.from_backtest_payload(payload)


def load_live_execution_payload(path: str | Path) -> dict[str, object]:
    payload = load_json(path)
    if isinstance(payload, dict) and "report" in payload and isinstance(payload["report"], dict):
        return payload["report"]
    return payload

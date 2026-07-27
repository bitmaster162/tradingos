from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

_ZERO = Decimal("0")
_ONE = Decimal("1")


def _clamp(value: Decimal, lower: Decimal = _ZERO, upper: Decimal = _ONE) -> Decimal:
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


@dataclass(slots=True)
class EntryQueueExpectation:
    expected_fill_ratio: Decimal
    expected_queue_clear_seconds: Decimal | None = None
    queue_ahead_ratio: Decimal | None = None
    directional_flow_qty_per_second: Decimal = _ZERO


@dataclass(slots=True)
class EntryQueueOutcome:
    expected_fill_ratio: Decimal | None
    actual_fill_ratio: Decimal
    fill_ratio_shortfall: Decimal | None
    expected_queue_clear_seconds: Decimal | None
    actual_fill_latency_seconds: Decimal
    fill_latency_overshoot_seconds: Decimal | None
    queue_ahead_ratio: Decimal | None
    directional_flow_qty_per_second: Decimal | None
    timed_out: bool
    requested_qty: Decimal
    executed_qty: Decimal
    submitted_at_ms: int
    completed_at_ms: int


class EntryQueueCalibrationModel:
    def evaluate(
        self,
        *,
        expectation: EntryQueueExpectation | None,
        submitted_at_ms: int,
        completed_at_ms: int,
        requested_qty: Decimal,
        executed_qty: Decimal,
        timed_out: bool = False,
    ) -> EntryQueueOutcome:
        requested_qty = max(_ZERO, requested_qty)
        executed_qty = max(_ZERO, min(requested_qty, executed_qty))
        completed_at_ms = max(submitted_at_ms, completed_at_ms)

        if requested_qty <= 0:
            actual_fill_ratio = _ZERO
        else:
            actual_fill_ratio = _clamp(executed_qty / requested_qty)

        actual_fill_latency_seconds = Decimal(completed_at_ms - submitted_at_ms) / Decimal("1000")
        expected_fill_ratio = expectation.expected_fill_ratio if expectation is not None else None
        expected_queue_clear_seconds = expectation.expected_queue_clear_seconds if expectation is not None else None
        queue_ahead_ratio = expectation.queue_ahead_ratio if expectation is not None else None
        directional_flow_rate = expectation.directional_flow_qty_per_second if expectation is not None else None

        fill_ratio_shortfall = None
        if expected_fill_ratio is not None:
            fill_ratio_shortfall = max(_ZERO, expected_fill_ratio - actual_fill_ratio)

        fill_latency_overshoot_seconds = None
        if expected_queue_clear_seconds is not None:
            fill_latency_overshoot_seconds = max(_ZERO, actual_fill_latency_seconds - expected_queue_clear_seconds)

        return EntryQueueOutcome(
            expected_fill_ratio=expected_fill_ratio,
            actual_fill_ratio=actual_fill_ratio,
            fill_ratio_shortfall=fill_ratio_shortfall,
            expected_queue_clear_seconds=expected_queue_clear_seconds,
            actual_fill_latency_seconds=actual_fill_latency_seconds,
            fill_latency_overshoot_seconds=fill_latency_overshoot_seconds,
            queue_ahead_ratio=queue_ahead_ratio,
            directional_flow_qty_per_second=directional_flow_rate,
            timed_out=bool(timed_out),
            requested_qty=requested_qty,
            executed_qty=executed_qty,
            submitted_at_ms=submitted_at_ms,
            completed_at_ms=completed_at_ms,
        )

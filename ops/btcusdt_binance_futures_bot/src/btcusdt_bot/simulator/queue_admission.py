from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from btcusdt_bot.simulator.top_of_book import PassiveOrderState


_ZERO = Decimal("0")
_ONE = Decimal("1")


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


@dataclass(slots=True)
class QueueAdmissionConfig:
    enabled: bool = True
    min_expected_fill_ratio: Decimal = Decimal("0.35")
    max_expected_queue_clear_seconds: Decimal | None = Decimal("4.0")
    max_queue_ahead_to_order_ratio: Decimal | None = Decimal("8.0")
    min_directional_flow_qty_per_second: Decimal = Decimal("0.01")


@dataclass(slots=True)
class QueueAdmissionInputs:
    qty: Decimal
    entry_timeout_seconds: int
    flow_window_seconds: int
    directional_flow_qty: Decimal | None
    queue_state: PassiveOrderState | None


@dataclass(slots=True)
class QueueAdmissionDecision:
    execute: bool
    expected_fill_ratio: Decimal
    expected_queue_clear_seconds: Decimal | None
    directional_flow_qty_per_second: Decimal
    queue_ahead_ratio: Decimal | None
    reason: str = ""


class QueueAdmissionPolicy:
    def __init__(self, config: QueueAdmissionConfig | None = None) -> None:
        self.config = config or QueueAdmissionConfig()

    def evaluate(self, inputs: QueueAdmissionInputs) -> QueueAdmissionDecision:
        if not self.config.enabled:
            return QueueAdmissionDecision(
                execute=True,
                expected_fill_ratio=_ONE,
                expected_queue_clear_seconds=_ZERO,
                directional_flow_qty_per_second=_ZERO,
                queue_ahead_ratio=_ZERO,
            )

        state = inputs.queue_state
        qty = max(_ZERO, inputs.qty)
        timeout_seconds = max(1, inputs.entry_timeout_seconds)
        flow_window_seconds = max(1, inputs.flow_window_seconds)
        directional_flow_qty = max(_ZERO, inputs.directional_flow_qty or _ZERO)
        flow_rate = directional_flow_qty / Decimal(flow_window_seconds)

        if qty <= 0:
            return QueueAdmissionDecision(
                execute=False,
                expected_fill_ratio=_ZERO,
                expected_queue_clear_seconds=None,
                directional_flow_qty_per_second=flow_rate,
                queue_ahead_ratio=None,
                reason="non_positive_qty",
            )

        if state is None:
            return QueueAdmissionDecision(
                execute=True,
                expected_fill_ratio=_ONE,
                expected_queue_clear_seconds=None,
                directional_flow_qty_per_second=flow_rate,
                queue_ahead_ratio=None,
            )

        if state.rejected_as_taker:
            return QueueAdmissionDecision(
                execute=False,
                expected_fill_ratio=_ZERO,
                expected_queue_clear_seconds=None,
                directional_flow_qty_per_second=flow_rate,
                queue_ahead_ratio=None,
                reason="post_only_cross_reject",
            )

        if state.queue_ahead_qty is None:
            return QueueAdmissionDecision(
                execute=True,
                expected_fill_ratio=_ONE,
                expected_queue_clear_seconds=None,
                directional_flow_qty_per_second=flow_rate,
                queue_ahead_ratio=None,
            )

        queue_ahead_qty = max(_ZERO, state.queue_ahead_qty)
        queue_ahead_ratio = queue_ahead_qty / qty if qty > 0 else None
        if queue_ahead_qty <= 0:
            return QueueAdmissionDecision(
                execute=True,
                expected_fill_ratio=_ONE,
                expected_queue_clear_seconds=_ZERO,
                directional_flow_qty_per_second=flow_rate,
                queue_ahead_ratio=queue_ahead_ratio,
            )

        if (
            self.config.max_queue_ahead_to_order_ratio is not None
            and queue_ahead_ratio is not None
            and queue_ahead_ratio > self.config.max_queue_ahead_to_order_ratio
        ):
            return QueueAdmissionDecision(
                execute=False,
                expected_fill_ratio=_ZERO,
                expected_queue_clear_seconds=None,
                directional_flow_qty_per_second=flow_rate,
                queue_ahead_ratio=queue_ahead_ratio,
                reason="queue_ahead_too_large",
            )

        if flow_rate < self.config.min_directional_flow_qty_per_second:
            return QueueAdmissionDecision(
                execute=False,
                expected_fill_ratio=_ZERO,
                expected_queue_clear_seconds=None,
                directional_flow_qty_per_second=flow_rate,
                queue_ahead_ratio=queue_ahead_ratio,
                reason="insufficient_directional_queue_flow",
            )

        expected_queue_clear_seconds = queue_ahead_qty / flow_rate if flow_rate > 0 else None
        if (
            expected_queue_clear_seconds is not None
            and self.config.max_expected_queue_clear_seconds is not None
            and expected_queue_clear_seconds > self.config.max_expected_queue_clear_seconds
        ):
            return QueueAdmissionDecision(
                execute=False,
                expected_fill_ratio=_ZERO,
                expected_queue_clear_seconds=expected_queue_clear_seconds,
                directional_flow_qty_per_second=flow_rate,
                queue_ahead_ratio=queue_ahead_ratio,
                reason="expected_queue_clear_too_slow",
            )

        expected_fill_ratio = _clamp(
            Decimal(timeout_seconds) * flow_rate / (queue_ahead_qty + qty),
            _ZERO,
            _ONE,
        )
        if expected_fill_ratio < self.config.min_expected_fill_ratio:
            return QueueAdmissionDecision(
                execute=False,
                expected_fill_ratio=expected_fill_ratio,
                expected_queue_clear_seconds=expected_queue_clear_seconds,
                directional_flow_qty_per_second=flow_rate,
                queue_ahead_ratio=queue_ahead_ratio,
                reason="expected_fill_ratio_too_low",
            )

        return QueueAdmissionDecision(
            execute=True,
            expected_fill_ratio=expected_fill_ratio,
            expected_queue_clear_seconds=expected_queue_clear_seconds,
            directional_flow_qty_per_second=flow_rate,
            queue_ahead_ratio=queue_ahead_ratio,
        )

from decimal import Decimal

from btcusdt_bot.domain.enums import Side
from btcusdt_bot.simulator.queue_admission import QueueAdmissionInputs, QueueAdmissionPolicy
from btcusdt_bot.simulator.top_of_book import PassiveOrderState


def test_queue_admission_rejects_when_queue_ahead_is_large_and_flow_is_weak() -> None:
    policy = QueueAdmissionPolicy()
    state = PassiveOrderState(
        side=Side.BUY,
        limit_price=Decimal("100"),
        total_qty=Decimal("1"),
        remaining_qty=Decimal("1"),
        queue_ahead_qty=Decimal("20"),
        mode="at_best",
    )

    decision = policy.evaluate(
        QueueAdmissionInputs(
            qty=Decimal("1"),
            entry_timeout_seconds=5,
            flow_window_seconds=10,
            directional_flow_qty=Decimal("0.1"),
            queue_state=state,
        )
    )

    assert decision.execute is False
    assert decision.reason == "queue_ahead_too_large"


def test_queue_admission_allows_when_inside_spread_has_no_queue_ahead() -> None:
    policy = QueueAdmissionPolicy()
    state = PassiveOrderState(
        side=Side.BUY,
        limit_price=Decimal("100.1"),
        total_qty=Decimal("1"),
        remaining_qty=Decimal("1"),
        queue_ahead_qty=Decimal("0"),
        mode="inside_spread",
    )

    decision = policy.evaluate(
        QueueAdmissionInputs(
            qty=Decimal("1"),
            entry_timeout_seconds=5,
            flow_window_seconds=10,
            directional_flow_qty=Decimal("0"),
            queue_state=state,
        )
    )

    assert decision.execute is True
    assert decision.expected_fill_ratio == Decimal("1")

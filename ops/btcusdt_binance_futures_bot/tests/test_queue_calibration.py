from decimal import Decimal

from btcusdt_bot.simulator.queue_calibration import (
    EntryQueueCalibrationModel,
    EntryQueueExpectation,
)


def test_queue_calibration_computes_shortfall_and_latency_overshoot() -> None:
    model = EntryQueueCalibrationModel()
    outcome = model.evaluate(
        expectation=EntryQueueExpectation(
            expected_fill_ratio=Decimal("0.80"),
            expected_queue_clear_seconds=Decimal("2.0"),
            queue_ahead_ratio=Decimal("0.50"),
            directional_flow_qty_per_second=Decimal("0.20"),
        ),
        submitted_at_ms=1_000,
        completed_at_ms=4_000,
        requested_qty=Decimal("1.0"),
        executed_qty=Decimal("0.5"),
        timed_out=True,
    )

    assert outcome.actual_fill_ratio == Decimal("0.5")
    assert outcome.fill_ratio_shortfall == Decimal("0.30")
    assert outcome.actual_fill_latency_seconds == Decimal("3")
    assert outcome.fill_latency_overshoot_seconds == Decimal("1.0")
    assert outcome.timed_out is True

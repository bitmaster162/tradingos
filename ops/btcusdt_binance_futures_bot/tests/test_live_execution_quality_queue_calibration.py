from decimal import Decimal

from btcusdt_bot.live_breakout import LiveBreakoutStatus
from btcusdt_bot.live_execution_quality import build_live_execution_quality_report


def test_live_execution_quality_report_includes_entry_calibration_metrics() -> None:
    status = LiveBreakoutStatus(
        session_started_at_ms=1_000,
        session_last_update_at_ms=3_000,
        entry_outcome_count=2,
        entry_timeout_count=1,
        realized_entry_fill_ratio_sum=Decimal("1.5"),
        entry_fill_ratio_shortfall_sum=Decimal("0.3"),
        entry_fill_ratio_shortfall_count=2,
        entry_fill_latency_seconds_sum=Decimal("5"),
        entry_fill_latency_count=2,
        entry_fill_latency_overshoot_seconds_sum=Decimal("1"),
        entry_fill_latency_overshoot_count=1,
    )

    report = build_live_execution_quality_report(status)

    assert report.average_realized_entry_fill_ratio == Decimal("0.75")
    assert report.average_entry_fill_ratio_shortfall == Decimal("0.15")
    assert report.average_entry_fill_latency_seconds == Decimal("2.5")
    assert report.average_entry_fill_latency_overshoot_seconds == Decimal("1")
    assert report.entry_timeout_rate == Decimal("0.5")

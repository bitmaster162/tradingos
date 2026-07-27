from decimal import Decimal

from btcusdt_bot.live_breakout import LiveBreakoutStatus
from btcusdt_bot.live_execution_quality import build_live_execution_quality_report


def test_live_execution_quality_report_exposes_averages() -> None:
    status = LiveBreakoutStatus(
        session_started_at_ms=1000,
        session_last_update_at_ms=2500,
        market_messages=25,
        entry_attempts=4,
        entries_sent=2,
        queue_decision_count=2,
        expected_fill_ratio_sum=Decimal("1.2"),
        queue_clear_seconds_sum=Decimal("3.0"),
        queue_clear_seconds_count=2,
        queue_ahead_ratio_sum=Decimal("0.6"),
        queue_ahead_ratio_count=2,
        directional_queue_flow_rate_sum=Decimal("0.3"),
        exit_depth_estimate_count=2,
        exit_depth_sweep_bps_sum=Decimal("4.0"),
        exit_depth_coverage_ratio_sum=Decimal("1.4"),
        exit_depth_levels_consumed_sum=Decimal("3"),
        exit_synthetic_tail_coverage_ratio_sum=Decimal("0.4"),
        exit_synthetic_tail_levels_consumed_sum=Decimal("1"),
        exit_terminal_tail_ratio_sum=Decimal("0.1"),
        notional_decision_count=2,
        target_notional_sum=Decimal("190"),
        notional_multiplier_sum=Decimal("2.1"),
        volatility_decision_count=2,
        volatility_multiplier_sum=Decimal("1.7"),
    )

    report = build_live_execution_quality_report(status)

    assert report.session_duration_ms == 1500
    assert report.average_target_notional_usdt == Decimal("95")
    assert report.average_notional_multiplier == Decimal("1.05")
    assert report.average_volatility_multiplier == Decimal("0.85")
    assert report.average_expected_fill_ratio == Decimal("0.6")
    assert report.average_queue_clear_seconds == Decimal("1.5")
    assert report.average_queue_ahead_ratio == Decimal("0.3")
    assert report.average_directional_queue_flow_qty_per_second == Decimal("0.15")
    assert report.average_exit_depth_sweep_bps == Decimal("2")
    assert report.average_exit_synthetic_tail_coverage_ratio == Decimal("0.2")
    assert report.average_exit_terminal_tail_ratio == Decimal("0.05")

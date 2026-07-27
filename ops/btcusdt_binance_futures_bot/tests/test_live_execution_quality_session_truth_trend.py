from decimal import Decimal

from btcusdt_bot.live_breakout import LiveBreakoutStatus
from btcusdt_bot.live_execution_quality import build_live_execution_quality_report



def test_live_execution_quality_report_includes_session_truth_trend_fields() -> None:
    status = LiveBreakoutStatus(
        session_started_at_ms=1000,
        session_last_update_at_ms=2000,
        last_session_truth_trend_action="reduce_size",
        last_session_truth_trend_size_multiplier="0.5",
        last_session_truth_trend_active_bucket_count=4,
        last_session_truth_trend_negative_bucket_ratio="0.50",
        last_session_truth_trend_trailing_negative_bucket_streak=2,
        last_session_truth_trend_recent_bucket_net_realized_bps="-1.5",
        last_session_truth_trend_cumulative_drawdown_usdt="6.0",
    )

    report = build_live_execution_quality_report(status)

    assert report.last_session_truth_trend_action == "reduce_size"
    assert report.last_session_truth_trend_size_multiplier == Decimal("0.5")
    assert report.last_session_truth_trend_active_bucket_count == 4
    assert report.last_session_truth_trend_negative_bucket_ratio == Decimal("0.50")
    assert report.last_session_truth_trend_recent_bucket_net_realized_bps == Decimal("-1.5")
    assert report.last_session_truth_trend_cumulative_drawdown_usdt == Decimal("6.0")

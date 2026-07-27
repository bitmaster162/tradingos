from decimal import Decimal

from btcusdt_bot.live_breakout import LiveBreakoutStatus
from btcusdt_bot.live_execution_quality import build_live_execution_quality_report



def test_live_execution_quality_report_includes_session_truth_fields() -> None:
    status = LiveBreakoutStatus(
        session_started_at_ms=1000,
        session_last_update_at_ms=2000,
        last_session_truth_action="reduce_size",
        last_session_truth_size_multiplier="0.5",
        last_session_truth_window_mode="session",
        last_session_truth_session_started_at_ms=1500,
        last_session_truth_net_realized_pnl_usdt="-1.5",
        last_session_truth_net_realized_bps="-2.5",
        last_session_truth_maker_ratio="0.30",
    )

    report = build_live_execution_quality_report(status)

    assert report.last_session_truth_action == "reduce_size"
    assert report.last_session_truth_size_multiplier == Decimal("0.5")
    assert report.last_session_truth_window_mode == "session"
    assert report.last_session_truth_session_started_at_ms == 1500
    assert report.last_session_truth_net_realized_pnl_usdt == Decimal("-1.5")
    assert report.last_session_truth_net_realized_bps == Decimal("-2.5")
    assert report.last_session_truth_maker_ratio == Decimal("0.30")

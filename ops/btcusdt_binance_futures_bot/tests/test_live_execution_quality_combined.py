from decimal import Decimal

from btcusdt_bot.live_breakout import LiveBreakoutStatus
from btcusdt_bot.live_execution_quality import build_live_execution_quality_report


def test_live_execution_quality_report_includes_combined_protection_fields() -> None:
    status = LiveBreakoutStatus(
        session_started_at_ms=1000,
        session_last_update_at_ms=2000,
        combined_protection_reduce_size_applications=2,
        combined_protection_observe_rejections=1,
        last_combined_protection_action="reduce_size",
        last_combined_protection_size_multiplier="0.5",
        last_combined_protection_cooldown_until_ms=3000,
        last_pnl_session_loss_usdt="10",
        last_pnl_drawdown_usdt="12",
        last_pnl_unrealized_loss_usdt="3",
    )

    report = build_live_execution_quality_report(status)

    assert report.combined_protection_reduce_size_applications == 2
    assert report.combined_protection_observe_rejections == 1
    assert report.last_combined_protection_action == "reduce_size"
    assert report.last_combined_protection_size_multiplier == Decimal("0.5")
    assert report.last_combined_protection_cooldown_until_ms == 3000

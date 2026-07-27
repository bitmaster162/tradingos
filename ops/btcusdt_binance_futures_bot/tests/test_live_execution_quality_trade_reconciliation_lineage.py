from decimal import Decimal

from btcusdt_bot.live_breakout import LiveBreakoutStatus
from btcusdt_bot.live_execution_quality import build_live_execution_quality_report



def test_live_execution_quality_report_includes_trade_reconciliation_lineage_fields() -> None:
    status = LiveBreakoutStatus(
        session_started_at_ms=1000,
        session_last_update_at_ms=2000,
        last_trade_reconciliation_action="reduce_size",
        last_trade_reconciliation_size_multiplier="0.5",
        last_trade_reconciliation_window_mode="session",
        last_trade_reconciliation_session_started_at_ms=1500,
        last_trade_reconciliation_missing_local_trade_ratio="0.10",
        last_trade_reconciliation_missing_local_order_ratio="0.20",
        last_trade_reconciliation_realized_pnl_diff_usdt="1.2",
        last_trade_reconciliation_commission_abs_diff_usdt="0.3",
        last_trade_reconciliation_quote_qty_abs_diff_usdt="12.5",
        last_trade_reconciliation_income_trade_link_gap_ratio="0.25",
    )

    report = build_live_execution_quality_report(status)

    assert report.last_trade_reconciliation_action == "reduce_size"
    assert report.last_trade_reconciliation_size_multiplier == Decimal("0.5")
    assert report.last_trade_reconciliation_window_mode == "session"
    assert report.last_trade_reconciliation_session_started_at_ms == 1500
    assert report.last_trade_reconciliation_missing_local_order_ratio == Decimal("0.20")
    assert report.last_trade_reconciliation_quote_qty_abs_diff_usdt == Decimal("12.5")
    assert report.last_trade_reconciliation_income_trade_link_gap_ratio == Decimal("0.25")

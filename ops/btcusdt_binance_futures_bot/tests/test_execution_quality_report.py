from decimal import Decimal

from btcusdt_bot.backtest.engine import BacktestReport, BacktestTrade
from btcusdt_bot.backtest.execution_quality import build_execution_quality_report
from btcusdt_bot.domain.enums import Side


def test_execution_quality_report_exposes_averages() -> None:
    report = BacktestReport(ticks=2)
    report.trades = [
        BacktestTrade(
            side=Side.BUY,
            qty=Decimal("1"),
            entry_price=Decimal("100"),
            exit_price=Decimal("101"),
            entry_time_ms=1,
            exit_time_ms=2,
            gross_pnl=Decimal("1"),
            fee_pnl=Decimal("0"),
            funding_pnl=Decimal("0"),
            net_pnl=Decimal("1"),
            exit_reason="tp",
            entry_notional_usdt=Decimal("100"),
            sizing_multiplier=Decimal("1.1"),
        ),
        BacktestTrade(
            side=Side.SELL,
            qty=Decimal("1"),
            entry_price=Decimal("102"),
            exit_price=Decimal("101"),
            entry_time_ms=3,
            exit_time_ms=4,
            gross_pnl=Decimal("1"),
            fee_pnl=Decimal("0"),
            funding_pnl=Decimal("0"),
            net_pnl=Decimal("1"),
            exit_reason="tp",
            entry_notional_usdt=Decimal("102"),
            sizing_multiplier=Decimal("1.3"),
        ),
    ]
    report.notional_sum = Decimal("202")
    report.sizing_multiplier_sum = Decimal("2.4")
    report.queue_decision_count = 2
    report.expected_fill_ratio_sum = Decimal("1.2")
    report.queue_clear_seconds_sum = Decimal("3.0")
    report.queue_clear_seconds_count = 2
    report.queue_ahead_ratio_sum = Decimal("0.9")
    report.queue_ahead_ratio_count = 2
    report.directional_queue_flow_rate_sum = Decimal("0.3")
    report.exit_depth_estimate_count = 2
    report.exit_depth_sweep_bps_sum = Decimal("7.0")
    report.exit_depth_coverage_ratio_sum = Decimal("1.6")
    report.exit_depth_levels_consumed_sum = Decimal("3")
    report.depth_liquidity_gate_rejections = 1
    report.modeled_partial_entry_count = 2
    report.modeled_partial_entry_qty = Decimal("0.7")
    report.entry_remainder_cancel_count = 3
    report.unmodeled_partial_entry_count = 1
    report.unmodeled_partial_entry_qty = Decimal("0.4")
    report.last_entry_completion_reason = "timeout"
    report.last_exit_pricing_source = "mark"
    report.last_exit_pricing_fallback_reason = "stale_exit_depth"
    report.last_exit_depth_age_ms = 500
    report.last_exit_book_age_ms = 200
    report.exit_depth_pricing_count = 1
    report.exit_book_pricing_count = 2
    report.exit_mark_pricing_count = 3
    report.exit_depth_fallback_count = 4
    report.exit_book_fallback_count = 5

    quality = build_execution_quality_report(report)

    assert quality.average_entry_notional == Decimal("101")
    assert quality.average_notional_multiplier == Decimal("1.2")
    assert quality.average_expected_fill_ratio == Decimal("0.6")
    assert quality.average_exit_depth_sweep_bps == Decimal("3.5")
    assert quality.average_exit_depth_coverage_ratio == Decimal("0.8")
    assert quality.average_exit_depth_levels_consumed == Decimal("1.5")
    assert quality.depth_liquidity_gate_rejections == 1
    assert quality.modeled_partial_entry_count == 2
    assert quality.modeled_partial_entry_qty == Decimal("0.7")
    assert quality.entry_remainder_cancel_count == 3
    assert quality.unmodeled_partial_entry_count == 1
    assert quality.unmodeled_partial_entry_qty == Decimal("0.4")
    assert quality.last_entry_completion_reason == "timeout"
    assert quality.last_exit_pricing_source == "mark"
    assert quality.last_exit_pricing_fallback_reason == "stale_exit_depth"
    assert quality.last_exit_depth_age_ms == 500
    assert quality.last_exit_book_age_ms == 200
    assert quality.exit_depth_pricing_count == 1
    assert quality.exit_book_pricing_count == 2
    assert quality.exit_mark_pricing_count == 3
    assert quality.exit_depth_fallback_count == 4
    assert quality.exit_book_fallback_count == 5
    assert quality.promotion_blocked_by_partial_fills is True
    assert quality.execution_fidelity_status == "blocked_unmodeled_partial_entry_exposure"

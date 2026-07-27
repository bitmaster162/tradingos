from decimal import Decimal

from btcusdt_bot.backtest.engine import BacktestReport
from btcusdt_bot.backtest.execution_quality import build_execution_quality_report


def test_execution_quality_report_exposes_tail_metrics() -> None:
    report = BacktestReport(ticks=1)
    report.exit_depth_estimate_count = 2
    report.exit_synthetic_tail_coverage_ratio_sum = Decimal("0.4")
    report.exit_synthetic_tail_levels_consumed_sum = Decimal("3")
    report.exit_terminal_tail_ratio_sum = Decimal("0.2")

    quality = build_execution_quality_report(report)

    assert quality.average_exit_synthetic_tail_coverage_ratio == Decimal("0.2")
    assert quality.average_exit_synthetic_tail_levels_consumed == Decimal("1.5")
    assert quality.average_exit_terminal_tail_ratio == Decimal("0.1")

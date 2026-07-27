from decimal import Decimal

from btcusdt_bot.backtest.engine import BacktestReport
from btcusdt_bot.backtest.execution_quality import build_execution_quality_report
from btcusdt_bot.live_breakout import LiveBreakoutStatus
from btcusdt_bot.live_execution_quality import build_live_execution_quality_report


def test_backtest_execution_quality_exposes_ensemble_metrics() -> None:
    report = BacktestReport(
        ticks=1,
        ensemble_breakout_signal_count=2,
        ensemble_reversion_signal_count=1,
        ensemble_override_signal_count=1,
        last_ensemble_regime="range",
        last_ensemble_selected_strategy_kind="breakout",
        last_ensemble_preferred_strategy_kind="reversion",
        last_ensemble_breakout_score=Decimal("0.22"),
        last_ensemble_reversion_score=Decimal("-0.13"),
    )

    quality = build_execution_quality_report(report)

    assert quality.ensemble_breakout_signal_count == 2
    assert quality.ensemble_reversion_signal_count == 1
    assert quality.ensemble_override_signal_count == 1
    assert quality.last_ensemble_selected_strategy_kind == "breakout"
    assert quality.last_ensemble_breakout_score == Decimal("0.22")


def test_live_execution_quality_exposes_ensemble_metrics() -> None:
    status = LiveBreakoutStatus(
        session_started_at_ms=1,
        session_last_update_at_ms=2,
        ensemble_breakout_signal_count=3,
        ensemble_reversion_signal_count=1,
        ensemble_override_signal_count=1,
        last_ensemble_regime="range",
        last_ensemble_selected_strategy_kind="breakout",
        last_ensemble_preferred_strategy_kind="reversion",
        last_ensemble_breakout_score="0.22",
        last_ensemble_reversion_score="-0.13",
    )

    report = build_live_execution_quality_report(status)

    assert report.ensemble_breakout_signal_count == 3
    assert report.ensemble_reversion_signal_count == 1
    assert report.ensemble_override_signal_count == 1
    assert report.last_ensemble_selected_strategy_kind == "breakout"
    assert report.last_ensemble_breakout_score == Decimal("0.22")

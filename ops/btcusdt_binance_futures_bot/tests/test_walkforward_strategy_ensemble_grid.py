from decimal import Decimal

from btcusdt_bot.backtest.engine import BacktestReport
from btcusdt_bot.backtest.walkforward import (
    WalkForwardScoreConfig,
    build_breakout_parameter_grid,
    build_walkforward_folds,
    run_walkforward,
)


def _report(*, net_pnl: str, drawdown: str) -> BacktestReport:
    return BacktestReport(
        ticks=100,
        trades=[object()],
        net_pnl=Decimal(net_pnl),
        gross_pnl=Decimal(net_pnl),
        max_drawdown=Decimal(drawdown),
        wins=1,
    )


def test_walkforward_can_select_ensemble_strategy_family() -> None:
    candidates = build_breakout_parameter_grid(
        strategy_kinds=["breakout", "reversion", "router", "ensemble"],
        lookbacks=[60],
        hold_seconds=[300],
        min_flow_imbalances=[Decimal("0")],
        min_crowding_scores=[None],
        min_depth_imbalances=[None],
        max_book_spread_bps_values=[None],
        min_expected_fill_ratios=[Decimal("0.35")],
        reversion_entry_atr_multiples=[Decimal("1.0")],
        reversion_max_atr_fractions=[Decimal("0.0040")],
        reversion_min_flow_flips=[Decimal("0")],
    )
    assert {candidate.strategy_kind for candidate in candidates} == {"breakout", "reversion", "router", "ensemble"}

    available_dates = ["2026-04-01", "2026-04-02", "2026-04-03"]
    folds = build_walkforward_folds(
        available_dates=available_dates,
        train_days=1,
        test_days=1,
        step_days=1,
    )

    def evaluator(candidate, start_date: str, end_date: str) -> BacktestReport:
        if start_date == "2026-04-01":
            if candidate.strategy_kind == "ensemble":
                return _report(net_pnl="8", drawdown="1")
            if candidate.strategy_kind == "router":
                return _report(net_pnl="7", drawdown="1")
            if candidate.strategy_kind == "reversion":
                return _report(net_pnl="5", drawdown="1")
            return _report(net_pnl="1", drawdown="1")
        if candidate.strategy_kind == "ensemble":
            return _report(net_pnl="4", drawdown="1")
        return _report(net_pnl="0", drawdown="1")

    report = run_walkforward(
        symbol="BTCUSDT",
        mode="mark_only",
        available_dates=available_dates,
        folds=folds,
        candidates=candidates,
        evaluator=evaluator,
        score_config=WalkForwardScoreConfig(min_trade_count=1),
    )

    assert report.folds[0].selected_candidate is not None
    assert report.folds[0].selected_candidate.strategy_kind == "ensemble"
    assert report.selected_parameter_value_counts["strategy_kind"] == {"ensemble": 2}

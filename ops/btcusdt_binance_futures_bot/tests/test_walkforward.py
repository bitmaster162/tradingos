from decimal import Decimal
from pathlib import Path

from btcusdt_bot.backtest.engine import BacktestReport
from btcusdt_bot.backtest.walkforward import (
    BreakoutParameterCandidate,
    WalkForwardScoreConfig,
    build_breakout_parameter_grid,
    build_walkforward_folds,
    discover_available_market_dates,
    run_walkforward,
    score_backtest_report,
)


def _report(
    *,
    net_pnl: str,
    max_drawdown: str,
    trade_count: int,
    entry_timeout_rate: str | None = None,
    average_exit_depth_sweep_bps: str | None = None,
) -> BacktestReport:
    report = BacktestReport(
        ticks=100,
        trades=[object()] * trade_count,
        net_pnl=Decimal(net_pnl),
        gross_pnl=Decimal(net_pnl),
        max_drawdown=Decimal(max_drawdown),
        wins=trade_count,
    )
    if entry_timeout_rate is not None:
        report.entry_outcome_count = trade_count
        report.entry_timeout_count = int(Decimal(entry_timeout_rate) * Decimal(trade_count))
    if average_exit_depth_sweep_bps is not None:
        report.exit_depth_estimate_count = trade_count
        report.exit_depth_sweep_bps_sum = Decimal(average_exit_depth_sweep_bps) * Decimal(trade_count)
    return report


def test_discover_available_market_dates_filters_range(tmp_path: Path) -> None:
    for date in ("2026-04-01", "2026-04-02", "2026-04-03"):
        day_dir = tmp_path / "market" / date
        day_dir.mkdir(parents=True, exist_ok=True)
        (day_dir / "btcusdt_markPrice_1s.jsonl").write_text("{}\n", encoding="utf-8")

    dates = discover_available_market_dates(
        data_dir=tmp_path,
        symbol="BTCUSDT",
        start_date="2026-04-02",
        end_date="2026-04-03",
    )

    assert dates == ["2026-04-02", "2026-04-03"]


def test_build_walkforward_folds_supports_anchored_and_rolling() -> None:
    available_dates = [f"2026-04-0{i}" for i in range(1, 8)]

    rolling = build_walkforward_folds(
        available_dates=available_dates,
        train_days=2,
        test_days=2,
        step_days=2,
        anchored_train=False,
    )
    anchored = build_walkforward_folds(
        available_dates=available_dates,
        train_days=2,
        test_days=2,
        step_days=2,
        anchored_train=True,
    )

    assert [(fold.train_dates, fold.test_dates) for fold in rolling] == [
        (["2026-04-01", "2026-04-02"], ["2026-04-03", "2026-04-04"]),
        (["2026-04-03", "2026-04-04"], ["2026-04-05", "2026-04-06"]),
    ]
    assert [(fold.train_dates, fold.test_dates) for fold in anchored] == [
        (["2026-04-01", "2026-04-02"], ["2026-04-03", "2026-04-04"]),
        (["2026-04-01", "2026-04-02", "2026-04-03", "2026-04-04"], ["2026-04-05", "2026-04-06"]),
    ]


def test_score_backtest_report_enforces_min_trade_count() -> None:
    candidate = BreakoutParameterCandidate(breakout_lookback_ticks=60, max_hold_seconds=300)
    report = _report(net_pnl="10", max_drawdown="2", trade_count=0)

    scored = score_backtest_report(
        report,
        candidate=candidate,
        score_config=WalkForwardScoreConfig(min_trade_count=1),
    )

    assert scored.eligible is False
    assert scored.reason == "insufficient_trade_count"


def test_score_backtest_report_rejects_unmodeled_partial_entry_exposure() -> None:
    candidate = BreakoutParameterCandidate(breakout_lookback_ticks=60, max_hold_seconds=300)
    report = _report(net_pnl="100", max_drawdown="1", trade_count=10)
    report.unmodeled_partial_entry_count = 1
    report.unmodeled_partial_entry_qty = Decimal("0.4")

    scored = score_backtest_report(
        report,
        candidate=candidate,
        score_config=WalkForwardScoreConfig(min_trade_count=1),
    )

    assert scored.eligible is False
    assert scored.reason == "unmodeled_partial_entry_exposure"
    assert scored.score < Decimal("-1E17")


def test_score_backtest_report_accepts_modeled_partial_entry_exposure() -> None:
    candidate = BreakoutParameterCandidate(breakout_lookback_ticks=60, max_hold_seconds=300)
    report = _report(net_pnl="10", max_drawdown="1", trade_count=10)
    report.modeled_partial_entry_count = 2
    report.modeled_partial_entry_qty = Decimal("0.7")
    report.entry_remainder_cancel_count = 2

    scored = score_backtest_report(
        report,
        candidate=candidate,
        score_config=WalkForwardScoreConfig(min_trade_count=1),
    )

    assert scored.eligible is True
    assert scored.reason == "ok"


def test_run_walkforward_selects_best_candidate_and_tracks_turnover() -> None:
    available_dates = [
        "2026-04-01",
        "2026-04-02",
        "2026-04-03",
        "2026-04-04",
        "2026-04-05",
    ]
    folds = build_walkforward_folds(
        available_dates=available_dates,
        train_days=2,
        test_days=1,
        step_days=2,
    )
    candidates = build_breakout_parameter_grid(
        lookbacks=[60, 120],
        hold_seconds=[300],
        min_flow_imbalances=[Decimal("0")],
        min_crowding_scores=[None],
        min_depth_imbalances=[None],
        max_book_spread_bps_values=[None],
        min_expected_fill_ratios=[Decimal("0.35")],
    )
    assert len(candidates) == 2

    by_label = {candidate.label: candidate for candidate in candidates}

    def evaluator(candidate: BreakoutParameterCandidate, start_date: str, end_date: str) -> BacktestReport:
        key = (candidate.label, start_date, end_date)
        mapping = {
            (by_label[candidates[0].label].label, "2026-04-01", "2026-04-02"): _report(
                net_pnl="12", max_drawdown="2", trade_count=4, entry_timeout_rate="0.25", average_exit_depth_sweep_bps="1.0"
            ),
            (by_label[candidates[1].label].label, "2026-04-01", "2026-04-02"): _report(
                net_pnl="9", max_drawdown="1", trade_count=4, entry_timeout_rate="0.00", average_exit_depth_sweep_bps="0.5"
            ),
            (by_label[candidates[0].label].label, "2026-04-03", "2026-04-03"): _report(
                net_pnl="3", max_drawdown="1", trade_count=2
            ),
            (by_label[candidates[0].label].label, "2026-04-03", "2026-04-04"): _report(
                net_pnl="2", max_drawdown="3", trade_count=4, entry_timeout_rate="0.50", average_exit_depth_sweep_bps="2.0"
            ),
            (by_label[candidates[1].label].label, "2026-04-03", "2026-04-04"): _report(
                net_pnl="8", max_drawdown="1", trade_count=4, entry_timeout_rate="0.00", average_exit_depth_sweep_bps="0.5"
            ),
            (by_label[candidates[1].label].label, "2026-04-05", "2026-04-05"): _report(
                net_pnl="4", max_drawdown="1", trade_count=2
            ),
        }
        try:
            return mapping[key]
        except KeyError as exc:  # pragma: no cover - helps debug failing test inputs
            raise AssertionError(f"unexpected evaluation key: {key}") from exc

    report = run_walkforward(
        symbol="BTCUSDT",
        mode="multistream_parity",
        available_dates=available_dates,
        folds=folds,
        candidates=candidates,
        evaluator=evaluator,
        score_config=WalkForwardScoreConfig(
            max_drawdown_penalty=Decimal("0.5"),
            entry_timeout_rate_penalty=Decimal("1"),
            exit_depth_sweep_bps_penalty=Decimal("1"),
            min_trade_count=1,
        ),
    )

    assert report.fold_count == 2
    assert report.skipped_fold_count == 0
    assert report.total_test_net_pnl == Decimal("7")
    assert report.total_test_trade_count == 4
    assert report.selection_turnover_ratio == Decimal("1")
    assert report.selected_candidate_counts[candidates[0].label] == 1
    assert report.selected_candidate_counts[candidates[1].label] == 1
    assert report.selected_parameter_value_counts["breakout_lookback_ticks"] == {"60": 1, "120": 1}
    assert report.folds[0].selected_candidate_label == candidates[0].label
    assert report.folds[1].selected_candidate_label == candidates[1].label

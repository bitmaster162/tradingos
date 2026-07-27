from decimal import Decimal

from btcusdt_bot.backtest.engine import BacktestReport, BreakoutBacktestConfig, ParityBreakoutBacktester, PendingEntry
from btcusdt_bot.backtest.reader import BacktestEvent
from btcusdt_bot.domain.enums import Side
from btcusdt_bot.domain.models import SymbolFilters


FILTERS = SymbolFilters(
    symbol="BTCUSDT",
    tick_size=Decimal("0.01"),
    step_size=Decimal("0.001"),
    market_step_size=Decimal("0.001"),
    min_qty=Decimal("0.001"),
    market_min_qty=Decimal("0.001"),
    min_notional=Decimal("5"),
)


def _mark(event_time_ms: int, price: str) -> BacktestEvent:
    return BacktestEvent(
        event_time_ms=event_time_ms,
        stream="btcusdt@markPrice@1s",
        event_type="markPriceUpdate",
        payload={"e": "markPriceUpdate", "E": event_time_ms, "p": price, "r": "0", "T": 0},
        price=Decimal(price),
        funding_rate=Decimal("0"),
        next_funding_time_ms=0,
    )


def _depth(event_time_ms: int) -> BacktestEvent:
    return BacktestEvent(
        event_time_ms=event_time_ms,
        stream="btcusdt@depth@100ms",
        event_type="localDepthSnapshot",
        payload={
            "e": "localDepthSnapshot",
            "E": event_time_ms,
            "T": event_time_ms,
            "s": "BTCUSDT",
            "u": 200,
            "levels": 2,
            "bids": [["100.97", "20.0"]],
            "asks": [["101.50", "20.0"]],
        },
    )


def _sell_agg_trade(event_time_ms: int, *, price: str, qty: str) -> BacktestEvent:
    return BacktestEvent(
        event_time_ms=event_time_ms,
        stream="btcusdt@aggTrade",
        event_type="aggTrade",
        payload={"e": "aggTrade", "T": event_time_ms, "p": price, "q": qty, "m": True},
        price=Decimal(price),
        qty=Decimal(qty),
        buyer_is_market_maker=True,
    )


def _backtester() -> ParityBreakoutBacktester:
    return ParityBreakoutBacktester(
        symbol="BTCUSDT",
        config=BreakoutBacktestConfig(
            breakout_lookback_ticks=3,
            atr_window_ticks=2,
            entry_timeout_seconds=5,
            max_hold_seconds=300,
            position_notional_usdt=Decimal("100"),
            maker_fee_bps=Decimal("0"),
            taker_fee_bps=Decimal("0"),
            volatility_target_atr_fraction=None,
            require_contract_trading_status=False,
            use_book_ticker_fills=False,
            use_local_depth_fills=True,
            use_rpi_depth_fills=False,
            min_depth_imbalance=None,
            min_expected_fill_ratio=None,
            max_expected_queue_clear_seconds=None,
            max_queue_ahead_to_order_ratio=None,
            min_exit_depth_coverage_ratio=None,
            max_exit_depth_sweep_bps=None,
        ),
        filters=FILTERS,
    )


def test_partial_entry_timeout_keeps_and_accounts_executed_fraction() -> None:
    events = [
        _depth(900),
        _mark(1_000, "100"),
        _mark(2_000, "100"),
        _mark(3_000, "100"),
        _mark(4_000, "101"),
        _sell_agg_trade(4_500, price="100.97", qty="20.2"),
        _sell_agg_trade(4_600, price="100.97", qty="0.3"),
        _mark(5_000, "101"),
        _mark(7_000, "101"),
        _mark(10_000, "101"),
    ]
    report = _backtester().run(events)

    assert report.trade_count == 1
    assert report.missed_entries == 0
    assert report.entry_timeout_rate == Decimal("1")
    assert Decimal("0") < report.average_realized_entry_fill_ratio < Decimal("1")
    assert report.modeled_partial_entry_count == 1
    assert report.modeled_partial_entry_qty == Decimal("0.500")
    assert report.entry_remainder_cancel_count == 1
    assert report.unmodeled_partial_entry_count == 0
    assert report.promotion_blocked_by_partial_fills is False
    assert report.execution_fidelity_status == "modeled_partial_entry_exposure"
    assert report.last_entry_completion_reason == "timeout"
    assert report.trades[0].qty == Decimal("0.500")
    assert report.trades[0].entry_time_ms == 4_500
    assert report.trades[0].exit_time_ms == 10_000
    assert report.trades[0].exit_reason == "end_of_data"


def test_partial_entry_protective_exit_cancels_unfilled_remainder_first() -> None:
    events = [
        _depth(900),
        _mark(1_000, "100"),
        _mark(2_000, "100"),
        _mark(3_000, "100"),
        _mark(4_000, "101"),
        _sell_agg_trade(4_500, price="100.97", qty="20.5"),
        _mark(5_000, "100"),
    ]

    report = _backtester().run(events)

    assert report.trade_count == 1
    assert report.trades[0].qty == Decimal("0.500")
    assert report.trades[0].entry_time_ms == 4_500
    assert report.trades[0].exit_time_ms == 5_000
    assert report.trades[0].exit_reason == "stop"
    assert report.trades[0].net_pnl < 0
    assert report.last_exit_pricing_source == "mark"
    assert report.last_exit_pricing_fallback_reason == "exit_depth_freshness_budget_not_configured"
    assert report.last_entry_completion_reason == "protective_exit"
    assert report.entry_timeout_rate == Decimal("0")
    assert report.modeled_partial_entry_count == 1
    assert report.entry_remainder_cancel_count == 1
    assert report.unmodeled_partial_entry_count == 0


def test_entry_outcome_fails_closed_on_any_materialization_mismatch() -> None:
    pending = PendingEntry(
        side=Side.BUY,
        qty=Decimal("1"),
        limit_price=Decimal("100"),
        atr=Decimal("1"),
        submitted_at_ms=1_000,
        expires_at_ms=6_000,
        stop_price=Decimal("98"),
        take_profit_price=Decimal("103"),
        target_notional_usdt=Decimal("100"),
        sizing_multiplier=Decimal("1"),
        materialized_qty=Decimal("0.4"),
    )
    report = BacktestReport(ticks=0)

    _backtester()._record_entry_queue_outcome(
        report,
        pending=pending,
        completed_at_ms=2_000,
        executed_qty=Decimal("1"),
        timed_out=False,
        completion_reason="full_fill",
    )

    assert report.unmodeled_partial_entry_count == 1
    assert report.unmodeled_partial_entry_qty == Decimal("0.6")
    assert report.promotion_blocked_by_partial_fills is True
    assert report.execution_fidelity_status == "blocked_unmodeled_partial_entry_exposure"

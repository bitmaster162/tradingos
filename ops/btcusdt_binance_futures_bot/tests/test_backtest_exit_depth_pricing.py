from decimal import Decimal

from btcusdt_bot.backtest.engine import BacktestReport, BreakoutBacktestConfig, BreakoutBacktester, OpenPosition
from btcusdt_bot.backtest.reader import BacktestTick
from btcusdt_bot.domain.enums import Side
from btcusdt_bot.domain.models import SymbolFilters
from btcusdt_bot.simulator.depth_book import DepthBookSnapshot, DepthLevel
from btcusdt_bot.simulator.top_of_book import TopOfBookSnapshot

FILTERS = SymbolFilters(
    symbol="BTCUSDT",
    tick_size=Decimal("0.01"),
    step_size=Decimal("0.001"),
    market_step_size=Decimal("0.001"),
    min_qty=Decimal("0.001"),
    market_min_qty=Decimal("0.001"),
    min_notional=Decimal("5"),
)


def _position() -> OpenPosition:
    return OpenPosition(
        side=Side.BUY,
        qty=Decimal("2.0"),
        entry_price=Decimal("99.5"),
        entry_time_ms=500,
        stop_price=Decimal("98.0"),
        take_profit_price=Decimal("101.0"),
        hold_until_ms=3000,
        entry_fee=Decimal("0"),
        target_notional_usdt=Decimal("199.0"),
        sizing_multiplier=Decimal("1.0"),
    )


def _depth(event_time_ms: int = 1000) -> DepthBookSnapshot:
    return DepthBookSnapshot(
        event_time_ms=event_time_ms,
        transaction_time_ms=event_time_ms - 1,
        last_update_id=10,
        levels=2,
        bids=[DepthLevel(Decimal("100.0"), Decimal("1.0")), DepthLevel(Decimal("99.9"), Decimal("1.5"))],
        asks=[DepthLevel(Decimal("100.1"), Decimal("1.0")), DepthLevel(Decimal("100.2"), Decimal("1.0"))],
    )


def test_backtester_uses_depth_sweep_for_taker_exit_price() -> None:
    backtester = BreakoutBacktester(
        symbol="BTCUSDT",
        config=BreakoutBacktestConfig(
            maker_fee_bps=Decimal("0"),
            taker_fee_bps=Decimal("0"),
            taker_slippage_bps=Decimal("0"),
            max_depth_snapshot_staleness_ms=1000,
        ),
        filters=FILTERS,
    )
    backtester._latest_depth = _depth()
    report = BacktestReport(ticks=1)

    trade = backtester._close_position(
        _position(),
        BacktestTick(event_time_ms=1500, price=Decimal("100.0"), funding_rate=Decimal("0"), next_funding_time_ms=0),
        "time_stop",
        latest_book=None,
        report=report,
    )

    assert trade.exit_price == Decimal("99.95")
    assert report.last_exit_depth_sweep_bps == Decimal("5")
    assert report.last_exit_depth_levels_consumed == 2
    assert report.last_exit_pricing_source == "depth"
    assert report.last_exit_depth_age_ms == 500
    assert report.exit_depth_pricing_count == 1
    assert report.exit_depth_fallback_count == 0


def test_backtester_rejects_stale_depth_for_exit_pricing() -> None:
    backtester = BreakoutBacktester(
        symbol="BTCUSDT",
        config=BreakoutBacktestConfig(
            taker_fee_bps=Decimal("0"),
            taker_slippage_bps=Decimal("0"),
            max_depth_snapshot_staleness_ms=100,
        ),
        filters=FILTERS,
    )
    backtester._latest_depth = _depth()
    report = BacktestReport(ticks=1)

    trade = backtester._close_position(
        _position(),
        BacktestTick(event_time_ms=1500, price=Decimal("100.0"), funding_rate=Decimal("0"), next_funding_time_ms=0),
        "time_stop",
        latest_book=None,
        report=report,
    )

    assert trade.exit_price == Decimal("100.0")
    assert report.last_exit_pricing_source == "mark"
    assert report.last_exit_pricing_fallback_reason == "stale_exit_depth"
    assert report.exit_depth_fallback_count == 1
    assert report.exit_mark_pricing_count == 1


def test_backtester_requires_explicit_depth_freshness_budget() -> None:
    backtester = BreakoutBacktester(
        symbol="BTCUSDT",
        config=BreakoutBacktestConfig(taker_fee_bps=Decimal("0"), taker_slippage_bps=Decimal("0")),
        filters=FILTERS,
    )
    backtester._latest_depth = _depth(event_time_ms=1500)
    report = BacktestReport(ticks=1)

    trade = backtester._close_position(
        _position(),
        BacktestTick(event_time_ms=1500, price=Decimal("100.0"), funding_rate=Decimal("0"), next_funding_time_ms=0),
        "time_stop",
        latest_book=None,
        report=report,
    )

    assert trade.exit_price == Decimal("100.0")
    assert report.last_exit_pricing_source == "mark"
    assert report.last_exit_pricing_fallback_reason == "exit_depth_freshness_budget_not_configured"


def test_backtester_rejects_stale_book_for_exit_pricing() -> None:
    backtester = BreakoutBacktester(
        symbol="BTCUSDT",
        config=BreakoutBacktestConfig(
            taker_fee_bps=Decimal("0"),
            taker_slippage_bps=Decimal("0"),
            max_book_ticker_staleness_ms=100,
        ),
        filters=FILTERS,
    )
    book = TopOfBookSnapshot(
        event_time_ms=1000,
        bid_price=Decimal("99.8"),
        bid_qty=Decimal("2"),
        ask_price=Decimal("100.2"),
        ask_qty=Decimal("2"),
    )
    report = BacktestReport(ticks=1)

    trade = backtester._close_position(
        _position(),
        BacktestTick(event_time_ms=1500, price=Decimal("100.0"), funding_rate=Decimal("0"), next_funding_time_ms=0),
        "time_stop",
        latest_book=book,
        report=report,
    )

    assert trade.exit_price == Decimal("100.0")
    assert report.last_exit_pricing_source == "mark"
    assert report.last_exit_book_age_ms == 500
    assert report.last_exit_pricing_fallback_reason == "stale_exit_book"
    assert report.exit_book_fallback_count == 1

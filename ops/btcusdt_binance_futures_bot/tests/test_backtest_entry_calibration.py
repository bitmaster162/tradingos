from decimal import Decimal

from btcusdt_bot.backtest.engine import BreakoutBacktestConfig, BreakoutBacktester
from btcusdt_bot.backtest.reader import BacktestTick
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


def test_mark_only_backtester_records_entry_fill_latency() -> None:
    ticks = [
        BacktestTick(event_time_ms=1_000, price=Decimal("100"), funding_rate=Decimal("0"), next_funding_time_ms=0),
        BacktestTick(event_time_ms=2_000, price=Decimal("100"), funding_rate=Decimal("0"), next_funding_time_ms=0),
        BacktestTick(event_time_ms=3_000, price=Decimal("100"), funding_rate=Decimal("0"), next_funding_time_ms=0),
        BacktestTick(event_time_ms=4_000, price=Decimal("101"), funding_rate=Decimal("0"), next_funding_time_ms=0),
        BacktestTick(event_time_ms=5_000, price=Decimal("100"), funding_rate=Decimal("0"), next_funding_time_ms=0),
    ]
    backtester = BreakoutBacktester(
        symbol="BTCUSDT",
        config=BreakoutBacktestConfig(
            breakout_lookback_ticks=3,
            atr_window_ticks=2,
            entry_timeout_seconds=5,
            max_hold_seconds=1,
            position_notional_usdt=Decimal("100"),
            maker_fee_bps=Decimal("0"),
            taker_fee_bps=Decimal("0"),
            use_book_ticker_fills=False,
            use_local_depth_fills=False,
        ),
        filters=FILTERS,
    )

    report = backtester.run(ticks)

    assert report.average_realized_entry_fill_ratio == Decimal("1")
    assert report.average_entry_fill_latency_seconds == Decimal("1")
    assert report.entry_timeout_rate == Decimal("0")


def test_mark_only_backtester_records_entry_timeout_rate() -> None:
    ticks = [
        BacktestTick(event_time_ms=1_000, price=Decimal("100"), funding_rate=Decimal("0"), next_funding_time_ms=0),
        BacktestTick(event_time_ms=2_000, price=Decimal("100"), funding_rate=Decimal("0"), next_funding_time_ms=0),
        BacktestTick(event_time_ms=3_000, price=Decimal("100"), funding_rate=Decimal("0"), next_funding_time_ms=0),
        BacktestTick(event_time_ms=4_000, price=Decimal("101"), funding_rate=Decimal("0"), next_funding_time_ms=0),
        BacktestTick(event_time_ms=5_000, price=Decimal("102"), funding_rate=Decimal("0"), next_funding_time_ms=0),
        BacktestTick(event_time_ms=6_000, price=Decimal("103"), funding_rate=Decimal("0"), next_funding_time_ms=0),
        BacktestTick(event_time_ms=7_000, price=Decimal("104"), funding_rate=Decimal("0"), next_funding_time_ms=0),
    ]
    backtester = BreakoutBacktester(
        symbol="BTCUSDT",
        config=BreakoutBacktestConfig(
            breakout_lookback_ticks=3,
            atr_window_ticks=2,
            entry_timeout_seconds=2,
            max_hold_seconds=1,
            position_notional_usdt=Decimal("100"),
            maker_fee_bps=Decimal("0"),
            taker_fee_bps=Decimal("0"),
            use_book_ticker_fills=False,
            use_local_depth_fills=False,
        ),
        filters=FILTERS,
    )

    report = backtester.run(ticks)

    assert report.entry_timeout_rate == Decimal("1")
    assert report.average_realized_entry_fill_ratio == Decimal("0")
    assert report.average_entry_fill_latency_seconds == Decimal("3")

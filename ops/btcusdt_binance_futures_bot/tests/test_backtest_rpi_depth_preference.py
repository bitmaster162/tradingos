from decimal import Decimal

from btcusdt_bot.backtest.engine import BreakoutBacktestConfig, ParityBreakoutBacktester, BacktestReport
from btcusdt_bot.domain.models import SymbolFilters
from btcusdt_bot.simulator.depth_book import DepthBookSnapshot, DepthLevel

FILTERS = SymbolFilters(
    symbol="BTCUSDT",
    tick_size=Decimal("0.01"),
    step_size=Decimal("0.001"),
    market_step_size=Decimal("0.001"),
    min_qty=Decimal("0.001"),
    market_min_qty=Decimal("0.001"),
    min_notional=Decimal("5"),
)



def _snapshot(event_time_ms: int, imbalance: str) -> DepthBookSnapshot:
    return DepthBookSnapshot(
        event_time_ms=event_time_ms,
        transaction_time_ms=event_time_ms,
        last_update_id=100,
        levels=2,
        bids=[DepthLevel(Decimal("100.0"), Decimal("3.0" if imbalance == "0.30" else "1.0"))],
        asks=[DepthLevel(Decimal("100.5"), Decimal("1.0" if imbalance == "0.30" else "4.0"))],
    )



def test_parity_backtester_prefers_rpi_depth_when_enabled() -> None:
    backtester = ParityBreakoutBacktester(
        symbol="BTCUSDT",
        config=BreakoutBacktestConfig(
            breakout_lookback_ticks=3,
            atr_window_ticks=2,
            entry_timeout_seconds=5,
            max_hold_seconds=300,
            position_notional_usdt=Decimal("100"),
            synthetic_spread_bps=Decimal("1.0"),
            taker_slippage_bps=Decimal("0"),
            maker_fee_bps=Decimal("0"),
            taker_fee_bps=Decimal("0"),
            use_local_depth_fills=True,
            use_rpi_depth_fills=True,
            depth_levels=2,
        ),
        filters=FILTERS,
    )
    report = BacktestReport(ticks=0)

    backtester._update_latest_depth(report, _snapshot(1000, "-0.40"), event_time_ms=1000)
    backtester._update_latest_rpi_depth(report, _snapshot(1050, "0.30"), event_time_ms=1100)

    effective_depth = backtester._effective_depth_snapshot()
    assert effective_depth is not None
    assert effective_depth.best_bid_price == Decimal("100.0")
    assert effective_depth.bids[0].qty == Decimal("3.0")
    assert effective_depth.asks[0].qty == Decimal("1.0")
    assert report.last_depth_source == "rpi"
    assert report.last_rpi_depth_age_ms == 50
    assert report.last_rpi_depth_levels == 2

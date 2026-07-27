from decimal import Decimal

from btcusdt_bot.backtest.engine import BreakoutBacktestConfig, ParityBreakoutBacktester
from btcusdt_bot.backtest.reader import BacktestEvent
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
        payload={"e": "markPriceUpdate", "E": event_time_ms, "p": price, "r": "0", "T": event_time_ms + 28_800_000},
        price=Decimal(price),
        funding_rate=Decimal("0"),
        next_funding_time_ms=event_time_ms + 28_800_000,
    )


def _agg(event_time_ms: int, qty: str, *, buy_aggressor: bool) -> BacktestEvent:
    return BacktestEvent(
        event_time_ms=event_time_ms,
        stream="btcusdt@aggTrade",
        event_type="aggTrade",
        payload={"e": "aggTrade", "T": event_time_ms, "p": "100", "nq": qty, "m": not buy_aggressor},
        price=Decimal("100"),
        qty=Decimal(qty),
        buyer_is_market_maker=not buy_aggressor,
    )


def _depth(event_time_ms: int, imbalance: str) -> BacktestEvent:
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
            "imbalance": imbalance,
            "bids": [["100.0", "1.0"]],
            "asks": [["100.5", "4.0"]],
        },
    )


def test_parity_backtester_blocks_entry_when_depth_imbalance_disagrees() -> None:
    events = [
        _depth(900, "-0.40"),
        _agg(950, "1.0", buy_aggressor=True),
        _agg(980, "1.2", buy_aggressor=True),
        _mark(1000, "100"),
        _mark(2000, "100"),
        _mark(3000, "100"),
        _mark(4000, "101"),
        _mark(5000, "100.9"),
    ]
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
            min_recent_agg_trades=2,
            min_flow_imbalance=Decimal("0.25"),
            min_depth_imbalance=Decimal("0.10"),
            use_local_depth_fills=True,
            depth_levels=2,
        ),
        filters=FILTERS,
    )

    report = backtester.run(events)

    assert report.trade_count == 0
    assert report.signal_gate_rejections == 1
    assert report.depth_gate_rejections == 1
    assert report.last_depth_imbalance is not None
    assert report.last_depth_imbalance < Decimal("-0.10")

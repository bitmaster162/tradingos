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


def _mark(event_time_ms: int, price: str, funding_rate: str = "0") -> BacktestEvent:
    return BacktestEvent(
        event_time_ms=event_time_ms,
        stream="btcusdt@markPrice@1s",
        event_type="markPriceUpdate",
        payload={"e": "markPriceUpdate", "E": event_time_ms, "p": price, "r": funding_rate, "T": event_time_ms + 28_800_000},
        price=Decimal(price),
        funding_rate=Decimal(funding_rate),
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


def _contract(event_time_ms: int, status: str) -> BacktestEvent:
    return BacktestEvent(
        event_time_ms=event_time_ms,
        stream="contractInfo",
        event_type="contractInfo",
        payload={"e": "contractInfo", "E": event_time_ms, "s": "BTCUSDT", "cs": status, "bks": [{"bs": 1, "ma": 50}]},
    )


def test_parity_backtester_trades_when_flow_confirms_and_contract_is_trading() -> None:
    events = [
        _contract(900, "TRADING"),
        _agg(950, "1.0", buy_aggressor=True),
        _agg(980, "1.2", buy_aggressor=True),
        _mark(1000, "100"),
        _mark(2000, "100"),
        _mark(3000, "100"),
        _mark(4000, "101"),
        _mark(5000, "100.97"),
        _mark(6000, "101.20"),
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
        ),
        filters=FILTERS,
    )

    report = backtester.run(events)

    assert report.ticks == 6
    assert report.market_events == 9
    assert report.trade_count == 1
    assert report.wins == 1
    assert report.signal_gate_rejections == 0
    assert report.contract_gate_rejections == 0
    assert report.last_contract_status == "TRADING"


def test_parity_backtester_blocks_signal_when_contract_not_trading() -> None:
    events = [
        _contract(900, "SETTLING"),
        _agg(950, "1.0", buy_aggressor=True),
        _agg(980, "1.2", buy_aggressor=True),
        _mark(1000, "100"),
        _mark(2000, "100"),
        _mark(3000, "100"),
        _mark(4000, "101"),
        _mark(5000, "100.97"),
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
            require_contract_trading_status=True,
        ),
        filters=FILTERS,
    )

    report = backtester.run(events)

    assert report.trade_count == 0
    assert report.signal_gate_rejections == 1
    assert report.contract_gate_rejections == 1
    assert report.last_contract_status == "SETTLING"

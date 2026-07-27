import json
from decimal import Decimal
from pathlib import Path

from btcusdt_bot.backtest.engine import BreakoutBacktestConfig, BreakoutBacktester
from btcusdt_bot.backtest.reader import iter_mark_price_ticks
from btcusdt_bot.domain.models import SymbolFilters


def _write_mark_price_file(root: Path, prices: list[str]) -> None:
    day_dir = root / "market" / "2026-04-06"
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / "btcusdt_markPrice_1s.jsonl"
    ts = 1_700_000_000_000
    with path.open("w", encoding="utf-8") as handle:
        for price in prices:
            row = {
                "received_at_ms": ts,
                "stream": "btcusdt@markPrice@1s",
                "event_type": "markPriceUpdate",
                "payload": {
                    "e": "markPriceUpdate",
                    "E": ts,
                    "p": price,
                    "r": "0",
                    "T": ts + 28_800_000,
                    "ap": price,
                },
            }
            handle.write(json.dumps(row) + "\n")
            ts += 1000


def test_breakout_backtester_replays_jsonl_and_closes_profitable_trade(tmp_path) -> None:
    _write_mark_price_file(tmp_path, ["100", "100", "100", "101", "100.97", "101.2"])
    filters = SymbolFilters(
        symbol="BTCUSDT",
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.001"),
        market_step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        market_min_qty=Decimal("0.001"),
        min_notional=Decimal("5"),
    )
    backtester = BreakoutBacktester(
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
        ),
        filters=filters,
    )

    report = backtester.run(iter_mark_price_ticks(tmp_path, symbol="BTCUSDT"))

    assert report.ticks == 6
    assert report.trade_count == 1
    assert report.wins == 1
    assert report.missed_entries == 0
    assert report.net_pnl > 0
    assert report.trades[0].exit_reason in {"take_profit", "end_of_data"}

import json
from decimal import Decimal
from pathlib import Path

from btcusdt_bot.backtest.engine import BreakoutBacktestConfig, BreakoutBacktester
from btcusdt_bot.backtest.reader import iter_mark_price_ticks
from btcusdt_bot.domain.models import SymbolFilters


def _write_mark_price_file(root: Path, prices: list[str]) -> None:
    day_dir = root / "market" / "2026-04-08"
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / "btcusdt_markPrice_1s.jsonl"
    ts = 1_700_100_000_000
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


def test_backtester_supports_ensemble_strategy_on_mark_stream(tmp_path: Path) -> None:
    _write_mark_price_file(tmp_path, ["100", "100", "100", "101", "101.10", "100.20", "100.10"])
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
            strategy_kind="ensemble",
            breakout_lookback_ticks=3,
            atr_window_ticks=2,
            reversion_entry_atr_multiple=Decimal("0.50"),
            reversion_max_atr_fraction=Decimal("0.0500"),
            router_range_max_atr_fraction=Decimal("0.0060"),
            router_trend_min_atr_fraction=Decimal("0.0100"),
            entry_timeout_seconds=5,
            max_hold_seconds=300,
            position_notional_usdt=Decimal("100"),
            synthetic_spread_bps=Decimal("1.0"),
            taker_slippage_bps=Decimal("0"),
            maker_fee_bps=Decimal("0"),
            taker_fee_bps=Decimal("0"),
            min_expected_fill_ratio=None,
            max_expected_queue_clear_seconds=None,
            max_queue_ahead_to_order_ratio=None,
            min_exit_depth_coverage_ratio=None,
            max_exit_depth_sweep_bps=None,
        ),
        filters=filters,
    )

    report = backtester.run(iter_mark_price_ticks(tmp_path, symbol="BTCUSDT"))

    assert report.ticks == 7
    assert report.trade_count == 1
    assert report.ensemble_reversion_signal_count >= 1
    assert report.last_ensemble_selected_strategy_kind == "reversion"
    assert report.net_pnl > 0

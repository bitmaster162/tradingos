from __future__ import annotations

from tools.bybit_all_liquidation_context_intake import EventRow
from tools.bybit_all_liquidation_context_intake_v2 import aggregate_events
from tools.liquidation_side_semantics import dominant_liquidation_context, liquidated_position_side


def event(side: str, notional: float) -> EventRow:
    return EventRow(
        event_time_ms=1_700_000_000_000,
        event_time="2023-11-14T22:13:20.000Z",
        liquidation_time_ms=1_700_000_000_000,
        liquidation_time="2023-11-14T22:13:20.000Z",
        symbol="BTCUSDT",
        side=side,
        price=1.0,
        quantity=notional,
        notional_usd=notional,
        source="bybit_v5_allLiquidation_websocket",
        path="fixture.jsonl",
        line=1,
    )


def test_source_specific_raw_side_mapping() -> None:
    assert liquidated_position_side("binance_force_order", "BUY") == "SHORT"
    assert liquidated_position_side("binance_force_order", "SELL") == "LONG"
    assert liquidated_position_side("bybit_all_liquidation", "BUY") == "LONG"
    assert liquidated_position_side("bybit_all_liquidation", "SELL") == "SHORT"


def test_bybit_buy_dominance_is_long_liquidation_flush() -> None:
    rows = aggregate_events([event("BUY", 90), event("SELL", 10)], "1h", {"BTCUSDT": set()})
    assert len(rows) == 1
    assert rows[0]["long_liquidated_notional_usd"] == 90
    assert rows[0]["short_liquidated_notional_usd"] == 10
    assert rows[0]["dominant_context"] == "long_liquidation_flush"


def test_bybit_sell_dominance_is_short_liquidation_squeeze() -> None:
    rows = aggregate_events([event("BUY", 10), event("SELL", 90)], "1h", {"BTCUSDT": set()})
    assert rows[0]["dominant_context"] == "short_liquidation_squeeze"


def test_balanced_position_notional_is_mixed() -> None:
    assert dominant_liquidation_context(60, 40) == "mixed"

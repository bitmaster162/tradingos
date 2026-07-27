from decimal import Decimal

from btcusdt_bot.domain.enums import Side
from btcusdt_bot.simulator.depth_book import DepthBookPassiveFillModel, DepthBookSnapshot, DepthLevel


def _depth_snapshot() -> DepthBookSnapshot:
    return DepthBookSnapshot(
        event_time_ms=1000,
        transaction_time_ms=999,
        last_update_id=101,
        levels=3,
        bids=[DepthLevel(Decimal("100.0"), Decimal("1.0")), DepthLevel(Decimal("99.9"), Decimal("2.0"))],
        asks=[DepthLevel(Decimal("100.5"), Decimal("1.5")), DepthLevel(Decimal("100.6"), Decimal("2.0"))],
    )


def test_depth_fill_model_consumes_queue_and_fills_from_trade_flow() -> None:
    model = DepthBookPassiveFillModel()
    state = model.place_order(side=Side.BUY, limit_price=Decimal("100.0"), qty=Decimal("1.0"), book=_depth_snapshot())

    first_fill = model.process_agg_trade(
        state,
        trade_price=Decimal("100.0"),
        trade_qty=Decimal("1.4"),
        buyer_is_market_maker=True,
    )
    second_fill = model.process_agg_trade(
        state,
        trade_price=Decimal("99.9"),
        trade_qty=Decimal("0.8"),
        buyer_is_market_maker=True,
    )

    assert state.mode == "at_depth_level"
    assert first_fill == Decimal("0.4")
    assert second_fill == Decimal("0.6")
    assert state.filled is True


def test_depth_fill_model_fills_when_book_crosses_resting_order() -> None:
    model = DepthBookPassiveFillModel()
    state = model.place_order(side=Side.BUY, limit_price=Decimal("100.2"), qty=Decimal("0.5"), book=_depth_snapshot())
    cross_book = DepthBookSnapshot(
        event_time_ms=1100,
        transaction_time_ms=1099,
        last_update_id=102,
        levels=3,
        bids=[DepthLevel(Decimal("100.1"), Decimal("1.0"))],
        asks=[DepthLevel(Decimal("100.2"), Decimal("0.3"))],
    )

    filled = model.process_depth_snapshot(state, book=cross_book)

    assert filled == Decimal("0.3")
    assert state.remaining_qty == Decimal("0.2")
    assert state.filled is False

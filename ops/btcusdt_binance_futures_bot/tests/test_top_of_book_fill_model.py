from decimal import Decimal

from btcusdt_bot.domain.enums import Side
from btcusdt_bot.simulator.top_of_book import TopOfBookPassiveFillModel, TopOfBookSnapshot


def test_top_of_book_fill_model_consumes_queue_and_then_fills_order() -> None:
    model = TopOfBookPassiveFillModel()
    book = TopOfBookSnapshot(
        event_time_ms=1000,
        bid_price=Decimal("100"),
        bid_qty=Decimal("1.0"),
        ask_price=Decimal("101"),
        ask_qty=Decimal("2.0"),
    )
    state = model.place_order(side=Side.BUY, limit_price=Decimal("100"), qty=Decimal("1.0"), book=book)

    first_fill = model.process_agg_trade(
        state,
        trade_price=Decimal("100"),
        trade_qty=Decimal("1.4"),
        buyer_is_market_maker=True,
    )
    assert state.executed_qty == Decimal("0.4")

    second_fill = model.process_agg_trade(
        state,
        trade_price=Decimal("99.9"),
        trade_qty=Decimal("0.8"),
        buyer_is_market_maker=True,
    )

    assert state.mode == "at_best"
    assert first_fill == Decimal("0.4")
    assert second_fill == Decimal("0.6")
    assert state.executed_qty == Decimal("1.0")
    assert state.filled is True


def test_top_of_book_fill_model_rejects_crossing_post_only_order() -> None:
    model = TopOfBookPassiveFillModel()
    book = TopOfBookSnapshot(
        event_time_ms=1000,
        bid_price=Decimal("100"),
        bid_qty=Decimal("1.0"),
        ask_price=Decimal("101"),
        ask_qty=Decimal("2.0"),
    )

    state = model.place_order(side=Side.BUY, limit_price=Decimal("101"), qty=Decimal("1.0"), book=book)

    assert state.rejected_as_taker is True
    assert state.mode == "rejected_crossing"

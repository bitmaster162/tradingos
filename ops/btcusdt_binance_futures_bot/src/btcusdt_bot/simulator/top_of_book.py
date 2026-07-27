from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from btcusdt_bot.domain.enums import Side


_ZERO = Decimal("0")


@dataclass(slots=True)
class TopOfBookSnapshot:
    event_time_ms: int
    bid_price: Decimal
    bid_qty: Decimal
    ask_price: Decimal
    ask_qty: Decimal

    @property
    def spread(self) -> Decimal:
        return max(_ZERO, self.ask_price - self.bid_price)

    @property
    def mid_price(self) -> Decimal:
        if self.bid_price <= 0 and self.ask_price <= 0:
            return _ZERO
        return (self.bid_price + self.ask_price) / Decimal("2")

    @property
    def spread_bps(self) -> Decimal:
        mid = self.mid_price
        if mid <= 0:
            return _ZERO
        return self.spread / mid * Decimal("10000")


@dataclass(slots=True)
class PassiveOrderState:
    side: Side
    limit_price: Decimal
    total_qty: Decimal
    remaining_qty: Decimal
    queue_ahead_qty: Decimal | None
    mode: str
    rejected_as_taker: bool = False

    @property
    def filled(self) -> bool:
        return self.remaining_qty <= 0

    @property
    def executed_qty(self) -> Decimal:
        return max(_ZERO, min(self.total_qty, self.total_qty - self.remaining_qty))


class TopOfBookPassiveFillModel:
    def place_order(
        self,
        *,
        side: Side,
        limit_price: Decimal,
        qty: Decimal,
        book: TopOfBookSnapshot | None,
    ) -> PassiveOrderState:
        if book is None:
            return PassiveOrderState(
                side=side,
                limit_price=limit_price,
                total_qty=qty,
                remaining_qty=qty,
                queue_ahead_qty=None,
                mode="no_book",
            )

        if side == Side.BUY:
            if limit_price >= book.ask_price:
                return PassiveOrderState(
                    side=side,
                    limit_price=limit_price,
                    total_qty=qty,
                    remaining_qty=qty,
                    queue_ahead_qty=None,
                    mode="rejected_crossing",
                    rejected_as_taker=True,
                )
            if limit_price > book.bid_price:
                mode = "inside_spread"
                queue_ahead = _ZERO
            elif limit_price == book.bid_price:
                mode = "at_best"
                queue_ahead = max(_ZERO, book.bid_qty)
            else:
                mode = "behind_best"
                queue_ahead = None
        else:
            if limit_price <= book.bid_price:
                return PassiveOrderState(
                    side=side,
                    limit_price=limit_price,
                    total_qty=qty,
                    remaining_qty=qty,
                    queue_ahead_qty=None,
                    mode="rejected_crossing",
                    rejected_as_taker=True,
                )
            if limit_price < book.ask_price:
                mode = "inside_spread"
                queue_ahead = _ZERO
            elif limit_price == book.ask_price:
                mode = "at_best"
                queue_ahead = max(_ZERO, book.ask_qty)
            else:
                mode = "behind_best"
                queue_ahead = None

        return PassiveOrderState(
            side=side,
            limit_price=limit_price,
            total_qty=qty,
            remaining_qty=qty,
            queue_ahead_qty=queue_ahead,
            mode=mode,
        )

    def process_agg_trade(
        self,
        state: PassiveOrderState,
        *,
        trade_price: Decimal,
        trade_qty: Decimal,
        buyer_is_market_maker: bool,
    ) -> Decimal:
        if state.rejected_as_taker or state.filled or trade_qty <= 0:
            return _ZERO

        relevant_sell_hits_bid = state.side == Side.BUY and buyer_is_market_maker
        relevant_buy_hits_ask = state.side == Side.SELL and not buyer_is_market_maker
        if not (relevant_sell_hits_bid or relevant_buy_hits_ask):
            return _ZERO

        if state.side == Side.BUY and trade_price > state.limit_price:
            return _ZERO
        if state.side == Side.SELL and trade_price < state.limit_price:
            return _ZERO

        if state.mode == "behind_best":
            return _ZERO

        available_qty = trade_qty
        if state.queue_ahead_qty is not None and state.queue_ahead_qty > 0:
            queue_consumed = min(state.queue_ahead_qty, available_qty)
            state.queue_ahead_qty -= queue_consumed
            available_qty -= queue_consumed
        if available_qty <= 0:
            return _ZERO

        fill_qty = min(state.remaining_qty, available_qty)
        state.remaining_qty -= fill_qty
        return fill_qty

    def process_book_ticker(self, state: PassiveOrderState, *, book: TopOfBookSnapshot) -> Decimal:
        if state.rejected_as_taker or state.filled:
            return _ZERO

        if state.side == Side.BUY:
            if book.ask_price <= state.limit_price:
                fill_qty = state.remaining_qty
                state.remaining_qty = _ZERO
                return fill_qty
            if state.mode == "behind_best":
                if state.limit_price > book.bid_price and state.limit_price < book.ask_price:
                    state.mode = "inside_spread"
                    state.queue_ahead_qty = _ZERO
                elif state.limit_price == book.bid_price:
                    state.mode = "at_best"
                    state.queue_ahead_qty = max(_ZERO, book.bid_qty)
        else:
            if book.bid_price >= state.limit_price:
                fill_qty = state.remaining_qty
                state.remaining_qty = _ZERO
                return fill_qty
            if state.mode == "behind_best":
                if state.limit_price < book.ask_price and state.limit_price > book.bid_price:
                    state.mode = "inside_spread"
                    state.queue_ahead_qty = _ZERO
                elif state.limit_price == book.ask_price:
                    state.mode = "at_best"
                    state.queue_ahead_qty = max(_ZERO, book.ask_qty)
        return _ZERO

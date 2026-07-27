from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable

from btcusdt_bot.domain.enums import Side
from btcusdt_bot.simulator.top_of_book import PassiveOrderState

_ZERO = Decimal("0")


@dataclass(slots=True)
class DepthLevel:
    price: Decimal
    qty: Decimal


@dataclass(slots=True)
class DepthBookSnapshot:
    event_time_ms: int
    transaction_time_ms: int
    last_update_id: int
    levels: int
    bids: list[DepthLevel] = field(default_factory=list)
    asks: list[DepthLevel] = field(default_factory=list)

    @property
    def best_bid_price(self) -> Decimal:
        return self.bids[0].price if self.bids else _ZERO

    @property
    def best_ask_price(self) -> Decimal:
        return self.asks[0].price if self.asks else _ZERO

    @property
    def spread(self) -> Decimal:
        if not self.bids or not self.asks:
            return _ZERO
        return max(_ZERO, self.best_ask_price - self.best_bid_price)

    @property
    def mid_price(self) -> Decimal:
        if self.best_bid_price <= 0 and self.best_ask_price <= 0:
            return _ZERO
        return (self.best_bid_price + self.best_ask_price) / Decimal("2")

    @property
    def spread_bps(self) -> Decimal:
        mid = self.mid_price
        if mid <= 0:
            return _ZERO
        return self.spread / mid * Decimal("10000")

    @property
    def imbalance(self) -> Decimal:
        bid_notional = sum((level.price * level.qty for level in self.bids), start=_ZERO)
        ask_notional = sum((level.price * level.qty for level in self.asks), start=_ZERO)
        total = bid_notional + ask_notional
        if total <= 0:
            return _ZERO
        return (bid_notional - ask_notional) / total

    def qty_at_price(self, side: Side, price: Decimal) -> Decimal:
        levels = self.bids if side == Side.BUY else self.asks
        for level in levels:
            if level.price == price:
                return level.qty
        return _ZERO

    def queue_ahead_qty_for_order(self, side: Side, price: Decimal) -> Decimal:
        if side == Side.BUY:
            return sum((level.qty for level in self.bids if level.price >= price), start=_ZERO)
        return sum((level.qty for level in self.asks if level.price <= price), start=_ZERO)

    def crossing_qty_for_order(self, side: Side, price: Decimal) -> Decimal:
        if side == Side.BUY:
            return sum((level.qty for level in self.asks if level.price <= price), start=_ZERO)
        return sum((level.qty for level in self.bids if level.price >= price), start=_ZERO)

    def to_payload(self, *, symbol: str, event_type: str = "localDepthSnapshot") -> dict[str, object]:
        return {
            "e": event_type,
            "E": self.event_time_ms,
            "T": self.transaction_time_ms,
            "s": symbol,
            "u": self.last_update_id,
            "levels": self.levels,
            "imbalance": str(self.imbalance),
            "bestBidPrice": str(self.best_bid_price),
            "bestAskPrice": str(self.best_ask_price),
            "bids": [[str(level.price), str(level.qty)] for level in self.bids],
            "asks": [[str(level.price), str(level.qty)] for level in self.asks],
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "DepthBookSnapshot":
        bids = [DepthLevel(Decimal(str(price)), Decimal(str(qty))) for price, qty in payload.get("bids", [])]
        asks = [DepthLevel(Decimal(str(price)), Decimal(str(qty))) for price, qty in payload.get("asks", [])]
        return cls(
            event_time_ms=int(payload.get("E", 0) or 0),
            transaction_time_ms=int(payload.get("T", payload.get("E", 0)) or 0),
            last_update_id=int(payload.get("u", payload.get("lastUpdateId", 0)) or 0),
            levels=int(payload.get("levels", len(bids) or len(asks) or 0) or 0),
            bids=bids,
            asks=asks,
        )


class DepthBookSyncError(RuntimeError):
    pass


class LocalDepthBook:
    def __init__(self, *, symbol: str, levels: int = 20) -> None:
        self.symbol = symbol
        self.levels = max(1, levels)
        self.reset()

    def reset(self) -> None:
        self._bids: dict[Decimal, Decimal] = {}
        self._asks: dict[Decimal, Decimal] = {}
        self.last_update_id = 0
        self.prev_final_update_id: int | None = None
        self.initialized = False
        self.last_event_time_ms = 0
        self.last_transaction_time_ms = 0

    def load_snapshot(self, snapshot: dict[str, object]) -> None:
        self._bids = {}
        self._asks = {}
        for price, qty in snapshot.get("bids", []):
            self._set_level(self._bids, Decimal(str(price)), Decimal(str(qty)))
        for price, qty in snapshot.get("asks", []):
            self._set_level(self._asks, Decimal(str(price)), Decimal(str(qty)))
        self.last_update_id = int(snapshot.get("lastUpdateId", 0) or 0)
        self.prev_final_update_id = None
        self.last_event_time_ms = int(snapshot.get("E", 0) or 0)
        self.last_transaction_time_ms = int(snapshot.get("T", 0) or 0)
        self.initialized = False

    def bootstrap_from_buffer(self, snapshot: dict[str, object], buffered_events: Iterable[dict[str, object]]) -> int:
        self.load_snapshot(snapshot)
        events = [event for event in buffered_events if int(event.get("u", 0) or 0) >= self.last_update_id]
        first_idx: int | None = None
        for idx, event in enumerate(events):
            first_update_id = int(event.get("U", 0) or 0)
            final_update_id = int(event.get("u", 0) or 0)
            if first_update_id <= self.last_update_id <= final_update_id:
                first_idx = idx
                break
        if first_idx is None:
            raise DepthBookSyncError("no_sync_event_covering_snapshot")

        applied = 0
        for idx, event in enumerate(events[first_idx:]):
            self.apply_diff_event(event, allow_initial=(idx == 0))
            applied += 1
        return applied

    def apply_diff_event(self, payload: dict[str, object], *, allow_initial: bool = False) -> None:
        first_update_id = int(payload.get("U", 0) or 0)
        final_update_id = int(payload.get("u", 0) or 0)
        prev_update_id = int(payload.get("pu", 0) or 0)
        if not allow_initial and self.prev_final_update_id is not None and prev_update_id != self.prev_final_update_id:
            raise DepthBookSyncError("depth_sequence_gap")
        if not allow_initial and final_update_id < self.last_update_id:
            return
        if allow_initial and not (first_update_id <= self.last_update_id <= final_update_id):
            raise DepthBookSyncError("initial_event_does_not_cover_snapshot")

        for price, qty in payload.get("b", []):
            self._set_level(self._bids, Decimal(str(price)), Decimal(str(qty)))
        for price, qty in payload.get("a", []):
            self._set_level(self._asks, Decimal(str(price)), Decimal(str(qty)))

        self.last_update_id = final_update_id
        self.prev_final_update_id = final_update_id
        self.last_event_time_ms = int(payload.get("E", self.last_event_time_ms) or self.last_event_time_ms)
        self.last_transaction_time_ms = int(payload.get("T", self.last_transaction_time_ms) or self.last_transaction_time_ms)
        self.initialized = True

    def snapshot(self, *, levels: int | None = None) -> DepthBookSnapshot:
        depth_levels = max(1, levels or self.levels)
        bids = sorted(self._bids.items(), key=lambda item: item[0], reverse=True)[:depth_levels]
        asks = sorted(self._asks.items(), key=lambda item: item[0])[:depth_levels]
        return DepthBookSnapshot(
            event_time_ms=self.last_event_time_ms,
            transaction_time_ms=self.last_transaction_time_ms,
            last_update_id=self.last_update_id,
            levels=depth_levels,
            bids=[DepthLevel(price, qty) for price, qty in bids],
            asks=[DepthLevel(price, qty) for price, qty in asks],
        )

    @staticmethod
    def _set_level(book: dict[Decimal, Decimal], price: Decimal, qty: Decimal) -> None:
        if qty <= 0:
            book.pop(price, None)
            return
        book[price] = qty


class DepthBookPassiveFillModel:
    def place_order(
        self,
        *,
        side: Side,
        limit_price: Decimal,
        qty: Decimal,
        book: DepthBookSnapshot | None,
    ) -> PassiveOrderState:
        if book is None:
            return PassiveOrderState(
                side=side,
                limit_price=limit_price,
                total_qty=qty,
                remaining_qty=qty,
                queue_ahead_qty=None,
                mode="no_depth",
            )

        if side == Side.BUY and limit_price >= book.best_ask_price > 0:
            return PassiveOrderState(
                side=side,
                limit_price=limit_price,
                total_qty=qty,
                remaining_qty=qty,
                queue_ahead_qty=None,
                mode="rejected_crossing",
                rejected_as_taker=True,
            )
        if side == Side.SELL and limit_price <= book.best_bid_price > 0:
            return PassiveOrderState(
                side=side,
                limit_price=limit_price,
                total_qty=qty,
                remaining_qty=qty,
                queue_ahead_qty=None,
                mode="rejected_crossing",
                rejected_as_taker=True,
            )

        if side == Side.BUY:
            if book.best_bid_price < limit_price < book.best_ask_price:
                queue_ahead = _ZERO
                mode = "inside_spread"
            else:
                queue_ahead = book.queue_ahead_qty_for_order(side, limit_price)
                mode = "at_depth_level" if book.qty_at_price(side, limit_price) > 0 else "resting_away"
        else:
            if book.best_bid_price < limit_price < book.best_ask_price:
                queue_ahead = _ZERO
                mode = "inside_spread"
            else:
                queue_ahead = book.queue_ahead_qty_for_order(side, limit_price)
                mode = "at_depth_level" if book.qty_at_price(side, limit_price) > 0 else "resting_away"

        return PassiveOrderState(
            side=side,
            limit_price=limit_price,
            total_qty=qty,
            remaining_qty=qty,
            queue_ahead_qty=queue_ahead,
            mode=mode,
        )

    def process_depth_snapshot(self, state: PassiveOrderState, *, book: DepthBookSnapshot) -> Decimal:
        if state.rejected_as_taker or state.filled:
            return _ZERO

        crossing_qty = book.crossing_qty_for_order(state.side, state.limit_price)
        if crossing_qty > 0:
            available_qty = crossing_qty
            if state.queue_ahead_qty is not None and state.queue_ahead_qty > 0:
                queue_consumed = min(state.queue_ahead_qty, available_qty)
                state.queue_ahead_qty -= queue_consumed
                available_qty -= queue_consumed
            if available_qty > 0:
                fill_qty = min(state.remaining_qty, available_qty)
                state.remaining_qty -= fill_qty
                return fill_qty

        queue_ahead = book.queue_ahead_qty_for_order(state.side, state.limit_price)
        if queue_ahead > 0:
            state.mode = "at_depth_level" if book.qty_at_price(state.side, state.limit_price) > 0 else "resting_away"
            state.queue_ahead_qty = queue_ahead
        elif state.side == Side.BUY and book.best_bid_price < state.limit_price < book.best_ask_price:
            state.mode = "inside_spread"
            state.queue_ahead_qty = _ZERO
        elif state.side == Side.SELL and book.best_bid_price < state.limit_price < book.best_ask_price:
            state.mode = "inside_spread"
            state.queue_ahead_qty = _ZERO
        return _ZERO

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

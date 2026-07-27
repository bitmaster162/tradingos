from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Deque

from btcusdt_bot.domain.enums import Side
from btcusdt_bot.strategies.models import SignalContext


@dataclass(slots=True)
class AggTradePoint:
    event_time_ms: int
    price: Decimal
    qty: Decimal
    aggressor_side: Side


class RollingSignalBase:
    strategy_kind = "base"

    def __init__(
        self,
        *,
        lookback_ticks: int,
        atr_window_ticks: int,
        trade_flow_window_seconds: int = 10,
        min_recent_agg_trades: int = 0,
        max_mark_trade_divergence_bps: Decimal | None = None,
        max_positive_funding_rate: Decimal | None = None,
        min_negative_funding_rate: Decimal | None = None,
    ) -> None:
        self.lookback_ticks = max(2, lookback_ticks)
        self.atr_window_ticks = max(1, atr_window_ticks)
        self.trade_flow_window_ms = max(1000, trade_flow_window_seconds * 1000)
        self.min_recent_agg_trades = max(0, min_recent_agg_trades)
        self.max_mark_trade_divergence_bps = max_mark_trade_divergence_bps
        self.max_positive_funding_rate = max_positive_funding_rate
        self.min_negative_funding_rate = min_negative_funding_rate
        maxlen = max(self.lookback_ticks + 1, self.atr_window_ticks + 1)
        self._prices: Deque[Decimal] = deque(maxlen=maxlen)
        self._times: Deque[int] = deque(maxlen=maxlen)
        self._agg_trades: Deque[AggTradePoint] = deque()
        self.last_context: SignalContext | None = None
        self.last_rejection_reason: str = ""

    def on_agg_trade(
        self,
        *,
        event_time_ms: int,
        price: Decimal,
        qty: Decimal,
        buyer_is_market_maker: bool,
    ) -> SignalContext:
        aggressor_side = Side.SELL if buyer_is_market_maker else Side.BUY
        self._agg_trades.append(
            AggTradePoint(
                event_time_ms=event_time_ms,
                price=price,
                qty=qty,
                aggressor_side=aggressor_side,
            )
        )
        self._prune_agg_trades(event_time_ms)
        return self.current_context(mark_price=None, funding_rate=None, event_time_ms=event_time_ms)

    def current_context(
        self,
        *,
        mark_price: Decimal | None,
        funding_rate: Decimal | None,
        event_time_ms: int,
    ) -> SignalContext:
        self._prune_agg_trades(event_time_ms)
        buy_qty = Decimal("0")
        sell_qty = Decimal("0")
        last_trade_price: Decimal | None = None
        for trade in self._agg_trades:
            if trade.aggressor_side == Side.BUY:
                buy_qty += trade.qty
            else:
                sell_qty += trade.qty
            last_trade_price = trade.price
        total_qty = buy_qty + sell_qty
        flow_imbalance = Decimal("0")
        if total_qty > 0:
            flow_imbalance = (buy_qty - sell_qty) / total_qty
        mark_trade_divergence_bps: Decimal | None = None
        if mark_price is not None and last_trade_price is not None and mark_price != 0:
            mark_trade_divergence_bps = abs(last_trade_price - mark_price) / mark_price * Decimal("10000")
        return SignalContext(
            recent_trade_count=len(self._agg_trades),
            buy_aggressor_qty=buy_qty,
            sell_aggressor_qty=sell_qty,
            flow_imbalance=flow_imbalance,
            last_trade_price=last_trade_price,
            mark_trade_divergence_bps=mark_trade_divergence_bps,
            funding_rate=funding_rate,
        )

    def _prune_agg_trades(self, event_time_ms: int) -> None:
        cutoff = event_time_ms - self.trade_flow_window_ms
        while self._agg_trades and self._agg_trades[0].event_time_ms < cutoff:
            self._agg_trades.popleft()

    def _common_gate(self, side: Side, context: SignalContext) -> str:
        if self.min_recent_agg_trades > 0 and context.recent_trade_count < self.min_recent_agg_trades:
            return "insufficient_agg_trade_count"
        if (
            self.max_mark_trade_divergence_bps is not None
            and context.mark_trade_divergence_bps is not None
            and context.mark_trade_divergence_bps > self.max_mark_trade_divergence_bps
        ):
            return "mark_trade_divergence_too_wide"
        if (
            side == Side.BUY
            and self.max_positive_funding_rate is not None
            and context.funding_rate is not None
            and context.funding_rate > self.max_positive_funding_rate
        ):
            return "funding_too_positive_for_long"
        if (
            side == Side.SELL
            and self.min_negative_funding_rate is not None
            and context.funding_rate is not None
            and context.funding_rate < self.min_negative_funding_rate
        ):
            return "funding_too_negative_for_short"
        return ""


def average_true_range_like(prices: list[Decimal]) -> Decimal:
    if len(prices) < 2:
        return Decimal("0")
    diffs = [abs(curr - prev) for prev, curr in zip(prices[:-1], prices[1:], strict=True)]
    if not diffs:
        return Decimal("0")
    return sum(diffs, start=Decimal("0")) / Decimal(len(diffs))

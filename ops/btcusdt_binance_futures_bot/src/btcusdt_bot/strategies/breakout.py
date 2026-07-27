from __future__ import annotations

from decimal import Decimal

from btcusdt_bot.domain.enums import Side
from btcusdt_bot.strategies.common import RollingSignalBase, average_true_range_like
from btcusdt_bot.strategies.models import SignalEvaluation, StrategySignal


class RollingBreakoutModel(RollingSignalBase):
    strategy_kind = "breakout"

    def __init__(
        self,
        *,
        lookback_ticks: int,
        atr_window_ticks: int,
        trade_flow_window_seconds: int = 10,
        min_recent_agg_trades: int = 0,
        min_flow_imbalance: Decimal = Decimal("0"),
        max_mark_trade_divergence_bps: Decimal | None = None,
        max_positive_funding_rate: Decimal | None = None,
        min_negative_funding_rate: Decimal | None = None,
    ) -> None:
        super().__init__(
            lookback_ticks=lookback_ticks,
            atr_window_ticks=atr_window_ticks,
            trade_flow_window_seconds=trade_flow_window_seconds,
            min_recent_agg_trades=min_recent_agg_trades,
            max_mark_trade_divergence_bps=max_mark_trade_divergence_bps,
            max_positive_funding_rate=max_positive_funding_rate,
            min_negative_funding_rate=min_negative_funding_rate,
        )
        self.min_flow_imbalance = min_flow_imbalance

    def evaluate_price(
        self,
        *,
        event_time_ms: int,
        price: Decimal,
        funding_rate: Decimal | None = None,
    ) -> SignalEvaluation:
        self._prices.append(price)
        self._times.append(event_time_ms)
        self._prune_agg_trades(event_time_ms)
        context = self.current_context(mark_price=price, funding_rate=funding_rate, event_time_ms=event_time_ms)
        self.last_context = context
        self.last_rejection_reason = ""

        if len(self._prices) < max(self.lookback_ticks + 1, self.atr_window_ticks + 1):
            return SignalEvaluation(signal=None, context=context)

        historical = list(self._prices)
        current = historical[-1]
        lookback_slice = historical[-(self.lookback_ticks + 1) : -1]
        recent_high = max(lookback_slice)
        recent_low = min(lookback_slice)
        atr = average_true_range_like(historical[-(self.atr_window_ticks + 1) :])
        if current != 0:
            context.atr_fraction = atr / current
        if atr <= 0:
            return SignalEvaluation(signal=None, context=context)

        breakout_side: Side | None = None
        breakout_level = Decimal("0")
        if current > recent_high:
            breakout_side = Side.BUY
            breakout_level = recent_high
        elif current < recent_low:
            breakout_side = Side.SELL
            breakout_level = recent_low
        else:
            return SignalEvaluation(signal=None, context=context)

        rejection_reason = self._gate_breakout(breakout_side, context)
        if rejection_reason:
            self.last_rejection_reason = rejection_reason
            return SignalEvaluation(
                signal=None,
                context=context,
                candidate_side=breakout_side,
                rejection_reason=rejection_reason,
                preferred_strategy_kind=self.strategy_kind,
            )

        return SignalEvaluation(
            signal=StrategySignal(
                side=breakout_side,
                price=current,
                atr=atr,
                reference_level=breakout_level,
                event_time_ms=event_time_ms,
                context=context,
                strategy_kind=self.strategy_kind,
                regime="trend",
                selected_strategy_kind=self.strategy_kind,
                preferred_strategy_kind=self.strategy_kind,
            ),
            context=context,
            candidate_side=breakout_side,
            preferred_strategy_kind=self.strategy_kind,
            selected_strategy_kind=self.strategy_kind,
        )

    def on_price(
        self,
        *,
        event_time_ms: int,
        price: Decimal,
        funding_rate: Decimal | None = None,
    ) -> StrategySignal | None:
        return self.evaluate_price(event_time_ms=event_time_ms, price=price, funding_rate=funding_rate).signal

    def _gate_breakout(self, side: Side, context) -> str:
        if self.min_flow_imbalance > 0:
            if side == Side.BUY and context.flow_imbalance < self.min_flow_imbalance:
                return "flow_imbalance_not_confirmed_for_buy"
            if side == Side.SELL and context.flow_imbalance > -self.min_flow_imbalance:
                return "flow_imbalance_not_confirmed_for_sell"
        return self._common_gate(side, context)

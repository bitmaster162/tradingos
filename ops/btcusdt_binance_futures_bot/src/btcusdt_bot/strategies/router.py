from __future__ import annotations

from decimal import Decimal

from btcusdt_bot.domain.enums import Side
from btcusdt_bot.strategies.breakout import RollingBreakoutModel
from btcusdt_bot.strategies.models import SignalContext, SignalEvaluation, StrategySignal
from btcusdt_bot.strategies.reversion import RollingReversionModel


class RegimeRoutingModel:
    strategy_kind = "router"

    def __init__(
        self,
        *,
        breakout_model: RollingBreakoutModel,
        reversion_model: RollingReversionModel,
        range_max_atr_fraction: Decimal = Decimal("0.0040"),
        trend_min_atr_fraction: Decimal = Decimal("0.0060"),
        trend_min_abs_flow_imbalance: Decimal = Decimal("0.20"),
        range_max_abs_flow_imbalance: Decimal = Decimal("0.12"),
        neutral_preference: str = "breakout",
        opportunistic_fallback: bool = True,
    ) -> None:
        self.breakout_model = breakout_model
        self.reversion_model = reversion_model
        self.range_max_atr_fraction = max(Decimal("0"), range_max_atr_fraction)
        self.trend_min_atr_fraction = max(self.range_max_atr_fraction, trend_min_atr_fraction)
        self.trend_min_abs_flow_imbalance = max(Decimal("0"), trend_min_abs_flow_imbalance)
        self.range_max_abs_flow_imbalance = max(Decimal("0"), range_max_abs_flow_imbalance)
        neutral_preference = (neutral_preference or "breakout").strip().lower()
        if neutral_preference not in {"breakout", "reversion"}:
            neutral_preference = "breakout"
        self.neutral_preference = neutral_preference
        self.opportunistic_fallback = opportunistic_fallback
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
        breakout_context = self.breakout_model.on_agg_trade(
            event_time_ms=event_time_ms,
            price=price,
            qty=qty,
            buyer_is_market_maker=buyer_is_market_maker,
        )
        self.reversion_model.on_agg_trade(
            event_time_ms=event_time_ms,
            price=price,
            qty=qty,
            buyer_is_market_maker=buyer_is_market_maker,
        )
        self.last_context = breakout_context
        return breakout_context

    def current_context(
        self,
        *,
        mark_price: Decimal | None,
        funding_rate: Decimal | None,
        event_time_ms: int,
    ) -> SignalContext:
        context = self.breakout_model.current_context(
            mark_price=mark_price,
            funding_rate=funding_rate,
            event_time_ms=event_time_ms,
        )
        self.reversion_model.current_context(
            mark_price=mark_price,
            funding_rate=funding_rate,
            event_time_ms=event_time_ms,
        )
        self.last_context = context
        return context

    def evaluate_price(
        self,
        *,
        event_time_ms: int,
        price: Decimal,
        funding_rate: Decimal | None = None,
    ) -> SignalEvaluation:
        breakout_eval = self.breakout_model.evaluate_price(
            event_time_ms=event_time_ms,
            price=price,
            funding_rate=funding_rate,
        )
        reversion_eval = self.reversion_model.evaluate_price(
            event_time_ms=event_time_ms,
            price=price,
            funding_rate=funding_rate,
        )
        context = breakout_eval.context or reversion_eval.context
        if context is None:
            context = self.current_context(
                mark_price=price,
                funding_rate=funding_rate,
                event_time_ms=event_time_ms,
            )
        regime, preferred_strategy_kind = self._classify_regime(context)
        preferred_eval, alternate_eval = self._ordered_evaluations(
            preferred_strategy_kind=preferred_strategy_kind,
            breakout_eval=breakout_eval,
            reversion_eval=reversion_eval,
        )
        selected_strategy_kind = ""
        chosen_eval = preferred_eval
        if chosen_eval.signal is not None:
            selected_strategy_kind = chosen_eval.signal.strategy_kind
        elif self.opportunistic_fallback and alternate_eval.signal is not None:
            chosen_eval = alternate_eval
            selected_strategy_kind = alternate_eval.signal.strategy_kind

        if chosen_eval.signal is None:
            rejection_reason = preferred_eval.rejection_reason or alternate_eval.rejection_reason
            candidate_side = preferred_eval.candidate_side or alternate_eval.candidate_side
            self.last_context = context
            self.last_rejection_reason = rejection_reason
            return SignalEvaluation(
                signal=None,
                context=context,
                candidate_side=candidate_side,
                rejection_reason=rejection_reason,
                router_regime=regime,
                preferred_strategy_kind=preferred_strategy_kind,
                selected_strategy_kind=selected_strategy_kind,
            )

        chosen_signal = chosen_eval.signal
        signal = StrategySignal(
            side=chosen_signal.side,
            price=chosen_signal.price,
            atr=chosen_signal.atr,
            reference_level=chosen_signal.reference_level,
            event_time_ms=chosen_signal.event_time_ms,
            context=chosen_signal.context or context,
            strategy_kind=self.strategy_kind,
            regime=regime,
            selected_strategy_kind=selected_strategy_kind or chosen_signal.strategy_kind,
            preferred_strategy_kind=preferred_strategy_kind,
        )
        self.last_context = context
        self.last_rejection_reason = ""
        return SignalEvaluation(
            signal=signal,
            context=signal.context,
            candidate_side=signal.side,
            router_regime=regime,
            preferred_strategy_kind=preferred_strategy_kind,
            selected_strategy_kind=signal.selected_strategy_kind,
        )

    def on_price(
        self,
        *,
        event_time_ms: int,
        price: Decimal,
        funding_rate: Decimal | None = None,
    ) -> StrategySignal | None:
        return self.evaluate_price(event_time_ms=event_time_ms, price=price, funding_rate=funding_rate).signal

    def _classify_regime(self, context: SignalContext) -> tuple[str, str]:
        atr_fraction = context.atr_fraction
        abs_flow = abs(context.flow_imbalance)
        if (
            atr_fraction is not None
            and atr_fraction <= self.range_max_atr_fraction
            and abs_flow <= self.range_max_abs_flow_imbalance
        ):
            return "range", "reversion"
        if (
            (atr_fraction is not None and atr_fraction >= self.trend_min_atr_fraction)
            or abs_flow >= self.trend_min_abs_flow_imbalance
        ):
            return "trend", "breakout"
        return "neutral", self.neutral_preference

    @staticmethod
    def _ordered_evaluations(
        *,
        preferred_strategy_kind: str,
        breakout_eval: SignalEvaluation,
        reversion_eval: SignalEvaluation,
    ) -> tuple[SignalEvaluation, SignalEvaluation]:
        if preferred_strategy_kind == "reversion":
            return reversion_eval, breakout_eval
        return breakout_eval, reversion_eval

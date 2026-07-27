from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from btcusdt_bot.strategies.breakout import RollingBreakoutModel
from btcusdt_bot.strategies.models import SignalContext, SignalEvaluation, StrategySignal
from btcusdt_bot.strategies.reversion import RollingReversionModel

_ZERO = Decimal("0")
_ONE = Decimal("1")
_NEG_ONE = Decimal("-1")


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


@dataclass(slots=True)
class StrategyPerformanceState:
    entry_outcome_count: int = 0
    trade_count: int = 0
    timeout_count: int = 0
    ewma_fill_ratio_shortfall: Decimal = _ZERO
    ewma_latency_overshoot_seconds: Decimal = _ZERO
    ewma_net_pnl_bps: Decimal = _ZERO

    @property
    def total_observations(self) -> int:
        return self.entry_outcome_count + self.trade_count

    @property
    def timeout_rate(self) -> Decimal:
        if self.entry_outcome_count <= 0:
            return _ZERO
        return Decimal(self.timeout_count) / Decimal(self.entry_outcome_count)


class OnlineEnsembleModel:
    strategy_kind = "ensemble"

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
        regime_prior_weight: Decimal = Decimal("0.35"),
        performance_weight: Decimal = Decimal("0.65"),
        min_observations: int = 3,
        timeout_penalty_weight: Decimal = Decimal("0.75"),
        fill_shortfall_penalty_weight: Decimal = Decimal("0.50"),
        latency_overshoot_penalty_weight: Decimal = Decimal("0.25"),
        latency_overshoot_scale_seconds: Decimal = Decimal("3.0"),
        pnl_weight: Decimal = Decimal("0.35"),
        pnl_scale_bps: Decimal = Decimal("5.0"),
        outcome_ewma_alpha: Decimal = Decimal("0.35"),
    ) -> None:
        self.breakout_model = breakout_model
        self.reversion_model = reversion_model
        self.range_max_atr_fraction = max(_ZERO, range_max_atr_fraction)
        self.trend_min_atr_fraction = max(self.range_max_atr_fraction, trend_min_atr_fraction)
        self.trend_min_abs_flow_imbalance = max(_ZERO, trend_min_abs_flow_imbalance)
        self.range_max_abs_flow_imbalance = max(_ZERO, range_max_abs_flow_imbalance)
        neutral_preference = (neutral_preference or "breakout").strip().lower()
        if neutral_preference not in {"breakout", "reversion"}:
            neutral_preference = "breakout"
        self.neutral_preference = neutral_preference
        self.opportunistic_fallback = opportunistic_fallback
        self.regime_prior_weight = max(_ZERO, regime_prior_weight)
        self.performance_weight = max(_ZERO, performance_weight)
        self.min_observations = max(1, int(min_observations))
        self.timeout_penalty_weight = max(_ZERO, timeout_penalty_weight)
        self.fill_shortfall_penalty_weight = max(_ZERO, fill_shortfall_penalty_weight)
        self.latency_overshoot_penalty_weight = max(_ZERO, latency_overshoot_penalty_weight)
        self.latency_overshoot_scale_seconds = max(Decimal("0.1"), latency_overshoot_scale_seconds)
        self.pnl_weight = max(_ZERO, pnl_weight)
        self.pnl_scale_bps = max(Decimal("0.1"), pnl_scale_bps)
        self.outcome_ewma_alpha = _clamp(outcome_ewma_alpha, Decimal("0.05"), _ONE)
        self.states: dict[str, StrategyPerformanceState] = {
            "breakout": StrategyPerformanceState(),
            "reversion": StrategyPerformanceState(),
        }
        self.last_context: SignalContext | None = None
        self.last_rejection_reason: str = ""
        self.last_regime: str = ""
        self.last_preferred_strategy_kind: str = ""
        self.last_selected_strategy_kind: str = ""
        self.last_breakout_score: Decimal = _ZERO
        self.last_reversion_score: Decimal = _ZERO

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
        breakout_score = self._combined_score("breakout", preferred_strategy_kind)
        reversion_score = self._combined_score("reversion", preferred_strategy_kind)
        self.last_context = context
        self.last_regime = regime
        self.last_preferred_strategy_kind = preferred_strategy_kind
        self.last_breakout_score = breakout_score
        self.last_reversion_score = reversion_score

        chosen_eval: SignalEvaluation | None = None
        selected_strategy_kind = ""
        preferred_eval = breakout_eval if preferred_strategy_kind == "breakout" else reversion_eval
        alternate_eval = reversion_eval if preferred_strategy_kind == "breakout" else breakout_eval

        breakout_signal = breakout_eval.signal
        reversion_signal = reversion_eval.signal
        if breakout_signal is not None and reversion_signal is not None:
            if reversion_score > breakout_score:
                chosen_eval = reversion_eval
                selected_strategy_kind = "reversion"
            else:
                chosen_eval = breakout_eval
                selected_strategy_kind = "breakout"
        elif preferred_eval.signal is not None:
            chosen_eval = preferred_eval
            selected_strategy_kind = preferred_strategy_kind
        elif self.opportunistic_fallback and alternate_eval.signal is not None:
            chosen_eval = alternate_eval
            selected_strategy_kind = alternate_eval.signal.selected_strategy_kind or alternate_eval.signal.strategy_kind

        self.last_selected_strategy_kind = selected_strategy_kind
        if chosen_eval is None or chosen_eval.signal is None:
            rejection_reason = preferred_eval.rejection_reason or alternate_eval.rejection_reason
            candidate_side = preferred_eval.candidate_side or alternate_eval.candidate_side
            self.last_rejection_reason = rejection_reason
            return SignalEvaluation(
                signal=None,
                context=context,
                candidate_side=candidate_side,
                rejection_reason=rejection_reason,
                router_regime=regime,
                preferred_strategy_kind=preferred_strategy_kind,
                selected_strategy_kind=selected_strategy_kind,
                ensemble_breakout_score=breakout_score,
                ensemble_reversion_score=reversion_score,
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
            selected_strategy_kind=selected_strategy_kind or chosen_signal.selected_strategy_kind or chosen_signal.strategy_kind,
            preferred_strategy_kind=preferred_strategy_kind,
        )
        self.last_rejection_reason = ""
        self.last_selected_strategy_kind = signal.selected_strategy_kind
        return SignalEvaluation(
            signal=signal,
            context=signal.context,
            candidate_side=signal.side,
            router_regime=regime,
            preferred_strategy_kind=preferred_strategy_kind,
            selected_strategy_kind=signal.selected_strategy_kind,
            ensemble_breakout_score=breakout_score,
            ensemble_reversion_score=reversion_score,
        )

    def on_price(
        self,
        *,
        event_time_ms: int,
        price: Decimal,
        funding_rate: Decimal | None = None,
    ) -> StrategySignal | None:
        return self.evaluate_price(event_time_ms=event_time_ms, price=price, funding_rate=funding_rate).signal

    def record_entry_outcome(
        self,
        *,
        strategy_kind: str,
        actual_fill_ratio: Decimal,
        fill_ratio_shortfall: Decimal | None,
        fill_latency_overshoot_seconds: Decimal | None,
        timed_out: bool,
    ) -> None:
        state = self.states.get((strategy_kind or "").strip().lower())
        if state is None:
            return
        state.entry_outcome_count += 1
        if timed_out:
            state.timeout_count += 1
        shortfall = fill_ratio_shortfall
        if shortfall is None:
            shortfall = max(_ZERO, _ONE - actual_fill_ratio)
        overshoot = fill_latency_overshoot_seconds or _ZERO
        state.ewma_fill_ratio_shortfall = self._ewma(state.ewma_fill_ratio_shortfall, shortfall)
        state.ewma_latency_overshoot_seconds = self._ewma(state.ewma_latency_overshoot_seconds, max(_ZERO, overshoot))

    def record_trade_outcome(
        self,
        *,
        strategy_kind: str,
        net_pnl_bps: Decimal,
    ) -> None:
        state = self.states.get((strategy_kind or "").strip().lower())
        if state is None:
            return
        state.trade_count += 1
        state.ewma_net_pnl_bps = self._ewma(state.ewma_net_pnl_bps, net_pnl_bps)

    def performance_state(self, strategy_kind: str) -> StrategyPerformanceState:
        key = (strategy_kind or "").strip().lower()
        return self.states.get(key, StrategyPerformanceState())

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

    def _combined_score(self, strategy_kind: str, preferred_strategy_kind: str) -> Decimal:
        prior = _ONE if strategy_kind == preferred_strategy_kind else _ZERO
        return (self.regime_prior_weight * prior) + (self.performance_weight * self._performance_component(strategy_kind))

    def _performance_component(self, strategy_kind: str) -> Decimal:
        state = self.states[strategy_kind]
        total_observations = state.total_observations
        if total_observations <= 0:
            return _ZERO
        timeout_penalty = self.timeout_penalty_weight * state.timeout_rate
        shortfall_penalty = self.fill_shortfall_penalty_weight * _clamp(state.ewma_fill_ratio_shortfall, _ZERO, _ONE)
        latency_penalty = self.latency_overshoot_penalty_weight * _clamp(
            state.ewma_latency_overshoot_seconds / self.latency_overshoot_scale_seconds,
            _ZERO,
            _ONE,
        )
        pnl_component = _ZERO
        if state.trade_count > 0 and self.pnl_weight > 0:
            pnl_component = self.pnl_weight * _clamp(state.ewma_net_pnl_bps / self.pnl_scale_bps, _NEG_ONE, _ONE)
        confidence = _clamp(Decimal(total_observations) / Decimal(self.min_observations), _ZERO, _ONE)
        return confidence * (pnl_component - timeout_penalty - shortfall_penalty - latency_penalty)

    def _ewma(self, previous: Decimal, current: Decimal) -> Decimal:
        alpha = self.outcome_ewma_alpha
        return (alpha * current) + ((_ONE - alpha) * previous)

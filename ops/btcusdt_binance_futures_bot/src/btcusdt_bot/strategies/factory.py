from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from btcusdt_bot.strategies.breakout import RollingBreakoutModel
from btcusdt_bot.strategies.ensemble import OnlineEnsembleModel
from btcusdt_bot.strategies.models import SignalModel
from btcusdt_bot.strategies.reversion import RollingReversionModel
from btcusdt_bot.strategies.router import RegimeRoutingModel


@dataclass(frozen=True, slots=True)
class StrategyModelConfig:
    strategy_kind: str = "breakout"
    lookback_ticks: int = 120
    atr_window_ticks: int = 30
    trade_flow_window_seconds: int = 10
    min_recent_agg_trades: int = 0
    min_flow_imbalance: Decimal = Decimal("0")
    max_mark_trade_divergence_bps: Decimal | None = None
    max_positive_funding_rate: Decimal | None = None
    min_negative_funding_rate: Decimal | None = None
    reversion_lookback_ticks: int | None = None
    reversion_entry_atr_multiple: Decimal = Decimal("1.25")
    reversion_max_atr_fraction: Decimal | None = Decimal("0.0040")
    reversion_min_flow_flip: Decimal = Decimal("0")
    router_range_max_atr_fraction: Decimal = Decimal("0.0040")
    router_trend_min_atr_fraction: Decimal = Decimal("0.0060")
    router_trend_min_abs_flow_imbalance: Decimal = Decimal("0.20")
    router_range_max_abs_flow_imbalance: Decimal = Decimal("0.12")
    router_neutral_preference: str = "breakout"
    router_opportunistic_fallback: bool = True
    ensemble_regime_prior_weight: Decimal = Decimal("0.35")
    ensemble_performance_weight: Decimal = Decimal("0.65")
    ensemble_min_observations: int = 3
    ensemble_timeout_penalty_weight: Decimal = Decimal("0.75")
    ensemble_fill_shortfall_penalty_weight: Decimal = Decimal("0.50")
    ensemble_latency_overshoot_penalty_weight: Decimal = Decimal("0.25")
    ensemble_latency_overshoot_scale_seconds: Decimal = Decimal("3.0")
    ensemble_pnl_weight: Decimal = Decimal("0.35")
    ensemble_pnl_scale_bps: Decimal = Decimal("5.0")
    ensemble_outcome_ewma_alpha: Decimal = Decimal("0.35")


def _build_breakout(config: StrategyModelConfig) -> RollingBreakoutModel:
    return RollingBreakoutModel(
        lookback_ticks=config.lookback_ticks,
        atr_window_ticks=config.atr_window_ticks,
        trade_flow_window_seconds=config.trade_flow_window_seconds,
        min_recent_agg_trades=config.min_recent_agg_trades,
        min_flow_imbalance=config.min_flow_imbalance,
        max_mark_trade_divergence_bps=config.max_mark_trade_divergence_bps,
        max_positive_funding_rate=config.max_positive_funding_rate,
        min_negative_funding_rate=config.min_negative_funding_rate,
    )


def _build_reversion(config: StrategyModelConfig) -> RollingReversionModel:
    return RollingReversionModel(
        lookback_ticks=config.reversion_lookback_ticks or config.lookback_ticks,
        atr_window_ticks=config.atr_window_ticks,
        trade_flow_window_seconds=config.trade_flow_window_seconds,
        min_recent_agg_trades=config.min_recent_agg_trades,
        entry_atr_multiple=config.reversion_entry_atr_multiple,
        max_atr_fraction=config.reversion_max_atr_fraction,
        min_flow_flip=config.reversion_min_flow_flip,
        max_mark_trade_divergence_bps=config.max_mark_trade_divergence_bps,
        max_positive_funding_rate=config.max_positive_funding_rate,
        min_negative_funding_rate=config.min_negative_funding_rate,
    )


def build_strategy_model(config: StrategyModelConfig) -> SignalModel:
    strategy_kind = (config.strategy_kind or "breakout").strip().lower()
    if strategy_kind == "reversion":
        return _build_reversion(config)
    if strategy_kind == "router":
        return RegimeRoutingModel(
            breakout_model=_build_breakout(config),
            reversion_model=_build_reversion(config),
            range_max_atr_fraction=config.router_range_max_atr_fraction,
            trend_min_atr_fraction=config.router_trend_min_atr_fraction,
            trend_min_abs_flow_imbalance=config.router_trend_min_abs_flow_imbalance,
            range_max_abs_flow_imbalance=config.router_range_max_abs_flow_imbalance,
            neutral_preference=config.router_neutral_preference,
            opportunistic_fallback=config.router_opportunistic_fallback,
        )
    if strategy_kind == "ensemble":
        return OnlineEnsembleModel(
            breakout_model=_build_breakout(config),
            reversion_model=_build_reversion(config),
            range_max_atr_fraction=config.router_range_max_atr_fraction,
            trend_min_atr_fraction=config.router_trend_min_atr_fraction,
            trend_min_abs_flow_imbalance=config.router_trend_min_abs_flow_imbalance,
            range_max_abs_flow_imbalance=config.router_range_max_abs_flow_imbalance,
            neutral_preference=config.router_neutral_preference,
            opportunistic_fallback=config.router_opportunistic_fallback,
            regime_prior_weight=config.ensemble_regime_prior_weight,
            performance_weight=config.ensemble_performance_weight,
            min_observations=config.ensemble_min_observations,
            timeout_penalty_weight=config.ensemble_timeout_penalty_weight,
            fill_shortfall_penalty_weight=config.ensemble_fill_shortfall_penalty_weight,
            latency_overshoot_penalty_weight=config.ensemble_latency_overshoot_penalty_weight,
            latency_overshoot_scale_seconds=config.ensemble_latency_overshoot_scale_seconds,
            pnl_weight=config.ensemble_pnl_weight,
            pnl_scale_bps=config.ensemble_pnl_scale_bps,
            outcome_ewma_alpha=config.ensemble_outcome_ewma_alpha,
        )
    if strategy_kind != "breakout":
        raise ValueError(f"unsupported strategy_kind: {config.strategy_kind}")
    return _build_breakout(config)

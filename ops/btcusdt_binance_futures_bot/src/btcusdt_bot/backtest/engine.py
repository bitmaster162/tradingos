from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from btcusdt_bot.backtest.reader import BacktestEvent, BacktestTick
from btcusdt_bot.backtest.economics import BacktestEconomicsProvider, BacktestEconomicsSnapshot
from btcusdt_bot.crowding.scoring import CrowdingGateConfig, CrowdingScore, evaluate_crowding_gate
from btcusdt_bot.domain.enums import PositionSide, Side
from btcusdt_bot.domain.models import SymbolFilters
from btcusdt_bot.execution.planner import ExecutionPlanner
from btcusdt_bot.execution.validator import ExecutionValidator
from btcusdt_bot.monitoring.economics_regime import EconomicsRegimeDecision, EconomicsRegimeThresholds
from btcusdt_bot.strategies import SignalContext, StrategyModelConfig, StrategySignal as BreakoutSignal, build_strategy_model
from btcusdt_bot.simulator.depth_book import DepthBookPassiveFillModel, DepthBookSnapshot
from btcusdt_bot.simulator.queue_calibration import EntryQueueCalibrationModel, EntryQueueExpectation
from btcusdt_bot.simulator.depth_liquidity import (
    DepthLiquidityConfig,
    DepthLiquidityDecision,
    DepthLiquidityPolicy,
    DepthSweepEstimate,
)
from btcusdt_bot.simulator.queue_admission import (
    QueueAdmissionConfig,
    QueueAdmissionDecision,
    QueueAdmissionInputs,
    QueueAdmissionPolicy,
)
from btcusdt_bot.simulator.top_of_book import PassiveOrderState, TopOfBookPassiveFillModel, TopOfBookSnapshot
from btcusdt_bot.sizing.policy import (
    AdaptiveEntryDecision,
    AdaptiveEntryInputs,
    AdaptiveEntryPolicy,
    AdaptiveEntryPolicyConfig,
)
from btcusdt_bot.sizing.economics_feedback import EconomicsFeedbackConfig, EconomicsFeedbackDecision
from btcusdt_bot.sizing.volatility import (
    VolatilitySizingConfig,
    VolatilitySizingDecision,
    VolatilitySizingInputs,
    VolatilitySizingPolicy,
)


def _mean(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, start=Decimal("0")) / Decimal(len(values))


@dataclass(slots=True)
class BreakoutBacktestConfig:
    strategy_kind: str = "breakout"
    breakout_lookback_ticks: int = 120
    atr_window_ticks: int = 30
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
    entry_timeout_seconds: int = 5
    max_hold_seconds: int = 300
    position_notional_usdt: Decimal = Decimal("100")
    synthetic_spread_bps: Decimal = Decimal("0.8")
    taker_slippage_bps: Decimal = Decimal("0.8")
    maker_fee_bps: Decimal = Decimal("2.0")
    taker_fee_bps: Decimal = Decimal("5.0")
    trade_flow_window_seconds: int = 10
    min_recent_agg_trades: int = 0
    min_flow_imbalance: Decimal = Decimal("0")
    max_mark_trade_divergence_bps: Decimal | None = None
    max_positive_funding_rate: Decimal | None = None
    min_negative_funding_rate: Decimal | None = None
    require_contract_trading_status: bool = True
    crowding_period: str = "5m"
    max_crowding_snapshot_age_seconds: int | None = None
    min_crowding_score: Decimal | None = None
    crowding_oi_expansion_weight: Decimal = Decimal("0.5")
    use_book_ticker_fills: bool = True
    use_local_depth_fills: bool = True
    use_rpi_depth_fills: bool = True
    max_book_spread_bps: Decimal | None = None
    max_book_ticker_staleness_ms: int | None = None
    max_depth_snapshot_staleness_ms: int | None = None
    min_depth_imbalance: Decimal | None = None
    depth_levels: int = 20
    min_notional_multiplier: Decimal = Decimal("0.35")
    max_notional_multiplier: Decimal = Decimal("1.75")
    abstain_below_multiplier: Decimal = Decimal("0.50")
    min_effective_notional_usdt: Decimal = Decimal("25")
    sizing_flow_weight: Decimal = Decimal("0.60")
    sizing_crowding_weight: Decimal = Decimal("0.40")
    sizing_divergence_penalty_weight: Decimal = Decimal("0.25")
    sizing_funding_penalty_weight: Decimal = Decimal("0.15")
    sizing_divergence_penalty_cap_bps: Decimal = Decimal("3.0")
    sizing_funding_penalty_cap_rate: Decimal = Decimal("0.0005")
    volatility_target_atr_fraction: Decimal | None = Decimal("0.0020")
    volatility_abstain_above_atr_fraction: Decimal | None = Decimal("0.0080")
    volatility_min_notional_multiplier: Decimal = Decimal("0.50")
    volatility_max_notional_multiplier: Decimal = Decimal("1.60")
    min_expected_fill_ratio: Decimal | None = Decimal("0.35")
    max_expected_queue_clear_seconds: Decimal | None = Decimal("4.0")
    max_queue_ahead_to_order_ratio: Decimal | None = Decimal("8.0")
    min_directional_queue_flow_qty_per_second: Decimal = Decimal("0.01")
    min_exit_depth_coverage_ratio: Decimal | None = Decimal("0.75")
    max_exit_depth_sweep_bps: Decimal | None = Decimal("3.0")
    exit_depth_tail_penalty_bps: Decimal = Decimal("5.0")
    synthetic_tail_levels: int = 3
    synthetic_tail_replenishment_ratio: Decimal = Decimal("0.50")
    synthetic_tail_step_bps: Decimal = Decimal("1.0")
    economics_lookback_days: int = 7
    economics_feedback_enabled: bool = True
    economics_feedback_min_active_day_count: int = 3
    economics_feedback_min_multiplier: Decimal = Decimal("0.70")
    economics_regime_enabled: bool = True
    economics_regime_min_active_day_count: int = 3



@dataclass(slots=True)
class PendingEntry:
    side: Side
    qty: Decimal
    limit_price: Decimal
    atr: Decimal
    submitted_at_ms: int
    expires_at_ms: int
    stop_price: Decimal
    take_profit_price: Decimal
    target_notional_usdt: Decimal
    sizing_multiplier: Decimal
    strategy_kind: str = ""
    selected_strategy_kind: str = ""
    queue_state: PassiveOrderState | None = None
    queue_expectation: EntryQueueExpectation | None = None
    materialized_qty: Decimal = Decimal("0")


@dataclass(slots=True)
class PendingEntryResult:
    pending: PendingEntry | None
    rejection_reason: str = ""
    sizing_decision: AdaptiveEntryDecision | None = None
    volatility_decision: VolatilitySizingDecision | None = None
    queue_decision: QueueAdmissionDecision | None = None
    depth_liquidity_decision: DepthLiquidityDecision | None = None
    economics_feedback_decision: EconomicsFeedbackDecision | None = None
    economics_regime_decision: EconomicsRegimeDecision | None = None
    economics_dashboard_end_date: str = ""


@dataclass(slots=True)
class OpenPosition:
    side: Side
    qty: Decimal
    entry_price: Decimal
    entry_time_ms: int
    stop_price: Decimal
    take_profit_price: Decimal
    hold_until_ms: int
    entry_fee: Decimal
    target_notional_usdt: Decimal
    sizing_multiplier: Decimal
    strategy_kind: str = ""
    selected_strategy_kind: str = ""
    funding_pnl: Decimal = Decimal("0")
    next_funding_time_ms: int = 0


@dataclass(slots=True)
class BacktestTrade:
    side: Side
    qty: Decimal
    entry_price: Decimal
    exit_price: Decimal
    entry_time_ms: int
    exit_time_ms: int
    gross_pnl: Decimal
    fee_pnl: Decimal
    funding_pnl: Decimal
    net_pnl: Decimal
    exit_reason: str
    entry_notional_usdt: Decimal
    sizing_multiplier: Decimal
    strategy_kind: str = ""
    selected_strategy_kind: str = ""


@dataclass(slots=True)
class BacktestReport:
    ticks: int
    trades: list[BacktestTrade] = field(default_factory=list)
    gross_pnl: Decimal = Decimal("0")
    fee_pnl: Decimal = Decimal("0")
    funding_pnl: Decimal = Decimal("0")
    net_pnl: Decimal = Decimal("0")
    wins: int = 0
    losses: int = 0
    missed_entries: int = 0
    rejected_entries: int = 0
    signal_gate_rejections: int = 0
    contract_gate_rejections: int = 0
    crowding_gate_rejections: int = 0
    book_gate_rejections: int = 0
    depth_gate_rejections: int = 0
    queue_gate_rejections: int = 0
    depth_liquidity_gate_rejections: int = 0
    adaptive_abstentions: int = 0
    crowding_events: int = 0
    depth_events: int = 0
    rpi_depth_events: int = 0
    market_events: int = 0
    max_drawdown: Decimal = Decimal("0")
    equity_curve: list[Decimal] = field(default_factory=list)
    last_contract_status: str = ""
    last_contract_bracket_count: int = 0
    last_crowding_side_score: Decimal | None = None
    last_crowding_snapshot_age_ms: int | None = None
    last_crowding_period: str = ""
    last_book_spread_bps: Decimal | None = None
    last_book_age_ms: int | None = None
    last_depth_imbalance: Decimal | None = None
    last_depth_age_ms: int | None = None
    last_depth_levels: int = 0
    last_depth_source: str = ""
    last_rpi_depth_age_ms: int | None = None
    last_rpi_depth_levels: int = 0
    last_volatility_multiplier: Decimal | None = None
    last_atr_fraction_bps: Decimal | None = None
    last_economics_dashboard_end_date: str = ""
    last_economics_dashboard_active_day_count: int = 0
    last_economics_regime_action: str = ""
    last_economics_regime_size_multiplier: Decimal | None = None
    last_economics_regime_negative_day_ratio: Decimal | None = None
    last_economics_regime_recent_day_net_realized_bps: Decimal | None = None
    last_economics_regime_average_maker_ratio: Decimal | None = None
    last_economics_feedback_multiplier: Decimal | None = None
    last_economics_feedback_total_penalty: Decimal | None = None
    last_economics_feedback_reason: str = ""
    router_breakout_signal_count: int = 0
    router_reversion_signal_count: int = 0
    router_fallback_signal_count: int = 0
    last_router_regime: str = ""
    last_router_selected_strategy_kind: str = ""
    last_router_preferred_strategy_kind: str = ""
    ensemble_breakout_signal_count: int = 0
    ensemble_reversion_signal_count: int = 0
    ensemble_override_signal_count: int = 0
    last_ensemble_regime: str = ""
    last_ensemble_selected_strategy_kind: str = ""
    last_ensemble_preferred_strategy_kind: str = ""
    last_ensemble_breakout_score: Decimal | None = None
    last_ensemble_reversion_score: Decimal | None = None
    last_expected_fill_ratio: Decimal | None = None
    last_expected_queue_clear_seconds: Decimal | None = None
    last_queue_ahead_ratio: Decimal | None = None
    last_directional_queue_flow_qty_per_second: Decimal | None = None
    last_exit_depth_coverage_ratio: Decimal | None = None
    last_exit_depth_sweep_bps: Decimal | None = None
    last_exit_depth_levels_consumed: int = 0
    last_exit_synthetic_tail_coverage_ratio: Decimal | None = None
    last_exit_synthetic_tail_levels_consumed: int = 0
    last_exit_terminal_tail_ratio: Decimal | None = None
    last_exit_pricing_source: str = ""
    last_exit_pricing_fallback_reason: str = ""
    last_exit_depth_age_ms: int | None = None
    last_exit_book_age_ms: int | None = None
    exit_depth_pricing_count: int = 0
    exit_book_pricing_count: int = 0
    exit_mark_pricing_count: int = 0
    exit_depth_fallback_count: int = 0
    exit_book_fallback_count: int = 0
    entry_outcome_count: int = 0
    entry_timeout_count: int = 0
    modeled_partial_entry_count: int = 0
    modeled_partial_entry_qty: Decimal = Decimal("0")
    entry_remainder_cancel_count: int = 0
    unmodeled_partial_entry_count: int = 0
    unmodeled_partial_entry_qty: Decimal = Decimal("0")
    last_entry_completion_reason: str = ""
    last_actual_entry_fill_ratio: Decimal | None = None
    last_entry_fill_latency_seconds: Decimal | None = None
    last_entry_fill_ratio_shortfall: Decimal | None = None
    last_entry_fill_latency_overshoot_seconds: Decimal | None = None
    queue_decision_count: int = 0
    expected_fill_ratio_sum: Decimal = Decimal("0")
    queue_clear_seconds_sum: Decimal = Decimal("0")
    queue_clear_seconds_count: int = 0
    queue_ahead_ratio_sum: Decimal = Decimal("0")
    queue_ahead_ratio_count: int = 0
    directional_queue_flow_rate_sum: Decimal = Decimal("0")
    realized_entry_fill_ratio_sum: Decimal = Decimal("0")
    entry_fill_ratio_shortfall_sum: Decimal = Decimal("0")
    entry_fill_ratio_shortfall_count: int = 0
    entry_fill_latency_seconds_sum: Decimal = Decimal("0")
    entry_fill_latency_count: int = 0
    entry_fill_latency_overshoot_seconds_sum: Decimal = Decimal("0")
    entry_fill_latency_overshoot_count: int = 0
    exit_depth_estimate_count: int = 0
    exit_depth_sweep_bps_sum: Decimal = Decimal("0")
    exit_depth_coverage_ratio_sum: Decimal = Decimal("0")
    exit_depth_levels_consumed_sum: Decimal = Decimal("0")
    exit_synthetic_tail_coverage_ratio_sum: Decimal = Decimal("0")
    exit_synthetic_tail_levels_consumed_sum: Decimal = Decimal("0")
    exit_terminal_tail_ratio_sum: Decimal = Decimal("0")
    economics_feedback_decision_count: int = 0
    economics_feedback_multiplier_sum: Decimal = Decimal("0")
    economics_regime_reduce_size_applications: int = 0
    economics_regime_observe_rejections: int = 0
    notional_sum: Decimal = Decimal("0")
    sizing_multiplier_sum: Decimal = Decimal("0")

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> Decimal:
        if not self.trades:
            return Decimal("0")
        return Decimal(self.wins) / Decimal(len(self.trades))

    @property
    def average_entry_notional(self) -> Decimal:
        if not self.trades:
            return Decimal("0")
        return self.notional_sum / Decimal(len(self.trades))

    @property
    def average_notional_multiplier(self) -> Decimal:
        if not self.trades:
            return Decimal("0")
        return self.sizing_multiplier_sum / Decimal(len(self.trades))

    @property
    def average_economics_feedback_multiplier(self) -> Decimal | None:
        if self.economics_feedback_decision_count <= 0:
            return None
        return self.economics_feedback_multiplier_sum / Decimal(self.economics_feedback_decision_count)

    @property
    def average_expected_fill_ratio(self) -> Decimal | None:
        if self.queue_decision_count <= 0:
            return None
        return self.expected_fill_ratio_sum / Decimal(self.queue_decision_count)

    @property
    def average_queue_clear_seconds(self) -> Decimal | None:
        if self.queue_clear_seconds_count <= 0:
            return None
        return self.queue_clear_seconds_sum / Decimal(self.queue_clear_seconds_count)

    @property
    def average_queue_ahead_ratio(self) -> Decimal | None:
        if self.queue_ahead_ratio_count <= 0:
            return None
        return self.queue_ahead_ratio_sum / Decimal(self.queue_ahead_ratio_count)

    @property
    def average_directional_queue_flow_qty_per_second(self) -> Decimal | None:
        if self.queue_decision_count <= 0:
            return None
        return self.directional_queue_flow_rate_sum / Decimal(self.queue_decision_count)

    @property
    def average_realized_entry_fill_ratio(self) -> Decimal | None:
        if self.entry_outcome_count <= 0:
            return None
        return self.realized_entry_fill_ratio_sum / Decimal(self.entry_outcome_count)

    @property
    def average_entry_fill_ratio_shortfall(self) -> Decimal | None:
        if self.entry_fill_ratio_shortfall_count <= 0:
            return None
        return self.entry_fill_ratio_shortfall_sum / Decimal(self.entry_fill_ratio_shortfall_count)

    @property
    def average_entry_fill_latency_seconds(self) -> Decimal | None:
        if self.entry_fill_latency_count <= 0:
            return None
        return self.entry_fill_latency_seconds_sum / Decimal(self.entry_fill_latency_count)

    @property
    def average_entry_fill_latency_overshoot_seconds(self) -> Decimal | None:
        if self.entry_fill_latency_overshoot_count <= 0:
            return None
        return self.entry_fill_latency_overshoot_seconds_sum / Decimal(self.entry_fill_latency_overshoot_count)

    @property
    def entry_timeout_rate(self) -> Decimal | None:
        if self.entry_outcome_count <= 0:
            return None
        return Decimal(self.entry_timeout_count) / Decimal(self.entry_outcome_count)

    @property
    def promotion_blocked_by_partial_fills(self) -> bool:
        return self.unmodeled_partial_entry_count > 0

    @property
    def execution_fidelity_status(self) -> str:
        if self.promotion_blocked_by_partial_fills:
            return "blocked_unmodeled_partial_entry_exposure"
        if self.modeled_partial_entry_count > 0:
            return "modeled_partial_entry_exposure"
        return "no_unmodeled_partial_entry_exposure_observed"

    @property
    def average_exit_depth_sweep_bps(self) -> Decimal | None:
        if self.exit_depth_estimate_count <= 0:
            return None
        return self.exit_depth_sweep_bps_sum / Decimal(self.exit_depth_estimate_count)

    @property
    def average_exit_depth_coverage_ratio(self) -> Decimal | None:
        if self.exit_depth_estimate_count <= 0:
            return None
        return self.exit_depth_coverage_ratio_sum / Decimal(self.exit_depth_estimate_count)

    @property
    def average_exit_depth_levels_consumed(self) -> Decimal | None:
        if self.exit_depth_estimate_count <= 0:
            return None
        return self.exit_depth_levels_consumed_sum / Decimal(self.exit_depth_estimate_count)

    @property
    def average_exit_synthetic_tail_coverage_ratio(self) -> Decimal | None:
        if self.exit_depth_estimate_count <= 0:
            return None
        return self.exit_synthetic_tail_coverage_ratio_sum / Decimal(self.exit_depth_estimate_count)

    @property
    def average_exit_synthetic_tail_levels_consumed(self) -> Decimal | None:
        if self.exit_depth_estimate_count <= 0:
            return None
        return self.exit_synthetic_tail_levels_consumed_sum / Decimal(self.exit_depth_estimate_count)

    @property
    def average_exit_terminal_tail_ratio(self) -> Decimal | None:
        if self.exit_depth_estimate_count <= 0:
            return None
        return self.exit_terminal_tail_ratio_sum / Decimal(self.exit_depth_estimate_count)


class BreakoutBacktester:
    def __init__(
        self,
        *,
        symbol: str,
        config: BreakoutBacktestConfig | None = None,
        filters: SymbolFilters | None = None,
        economics_data_dir: Path | None = None,
    ) -> None:
        self.symbol = symbol
        self.config = config or BreakoutBacktestConfig()
        self.planner = ExecutionPlanner()
        self.validator = ExecutionValidator(filters) if filters is not None else None
        self.adaptive_entry_policy = AdaptiveEntryPolicy(
            AdaptiveEntryPolicyConfig(
                enabled=True,
                min_notional_multiplier=self.config.min_notional_multiplier,
                max_notional_multiplier=self.config.max_notional_multiplier,
                abstain_below_multiplier=self.config.abstain_below_multiplier,
                min_effective_notional_usdt=self.config.min_effective_notional_usdt,
                flow_weight=self.config.sizing_flow_weight,
                crowding_weight=self.config.sizing_crowding_weight,
                divergence_penalty_weight=self.config.sizing_divergence_penalty_weight,
                funding_penalty_weight=self.config.sizing_funding_penalty_weight,
                divergence_penalty_cap_bps=self.config.sizing_divergence_penalty_cap_bps,
                funding_penalty_cap_rate=self.config.sizing_funding_penalty_cap_rate,
            )
        )
        self.volatility_sizing_policy = VolatilitySizingPolicy(
            VolatilitySizingConfig(
                enabled=self.config.volatility_target_atr_fraction is not None,
                target_atr_fraction=self.config.volatility_target_atr_fraction or Decimal("0.0020"),
                min_notional_multiplier=self.config.volatility_min_notional_multiplier,
                max_notional_multiplier=self.config.volatility_max_notional_multiplier,
                abstain_above_atr_fraction=self.config.volatility_abstain_above_atr_fraction,
            )
        )
        self.queue_admission_policy = QueueAdmissionPolicy(
            QueueAdmissionConfig(
                enabled=(
                    self.config.min_expected_fill_ratio is not None
                    or self.config.max_expected_queue_clear_seconds is not None
                    or self.config.max_queue_ahead_to_order_ratio is not None
                ),
                min_expected_fill_ratio=self.config.min_expected_fill_ratio or Decimal("0"),
                max_expected_queue_clear_seconds=self.config.max_expected_queue_clear_seconds,
                max_queue_ahead_to_order_ratio=self.config.max_queue_ahead_to_order_ratio,
                min_directional_flow_qty_per_second=self.config.min_directional_queue_flow_qty_per_second,
            )
        )
        self.queue_calibration_model = EntryQueueCalibrationModel()
        self.depth_liquidity_policy = DepthLiquidityPolicy(
            DepthLiquidityConfig(
                enabled=(
                    self.config.min_exit_depth_coverage_ratio is not None
                    or self.config.max_exit_depth_sweep_bps is not None
                ),
                min_displayed_coverage_ratio=self.config.min_exit_depth_coverage_ratio,
                max_sweep_slippage_bps=self.config.max_exit_depth_sweep_bps,
                tail_penalty_bps=self.config.exit_depth_tail_penalty_bps,
                synthetic_tail_levels=self.config.synthetic_tail_levels,
                synthetic_tail_replenishment_ratio=self.config.synthetic_tail_replenishment_ratio,
                synthetic_tail_step_bps=self.config.synthetic_tail_step_bps,
            )
        )
        self.book_fill_model = TopOfBookPassiveFillModel()
        self.depth_fill_model = DepthBookPassiveFillModel()
        self.depth_sweep_model = self.depth_liquidity_policy.sweep_model
        self.economics_provider = (
            BacktestEconomicsProvider(
                data_dir=Path(economics_data_dir),
                symbol=self.symbol,
                lookback_days=self.config.economics_lookback_days,
                economics_feedback_config=EconomicsFeedbackConfig(
                    enabled=self.config.economics_feedback_enabled,
                    min_active_day_count=self.config.economics_feedback_min_active_day_count,
                    min_multiplier=self.config.economics_feedback_min_multiplier,
                ),
                economics_regime_thresholds=EconomicsRegimeThresholds(
                    min_active_day_count=self.config.economics_regime_min_active_day_count,
                ),
                enable_economics_feedback=self.config.economics_feedback_enabled,
                enable_economics_regime=self.config.economics_regime_enabled,
            )
            if economics_data_dir is not None
            else None
        )
        self.model = build_strategy_model(
            StrategyModelConfig(
                strategy_kind=self.config.strategy_kind,
                lookback_ticks=self.config.breakout_lookback_ticks,
                atr_window_ticks=self.config.atr_window_ticks,
                trade_flow_window_seconds=self.config.trade_flow_window_seconds,
                min_recent_agg_trades=self.config.min_recent_agg_trades,
                min_flow_imbalance=self.config.min_flow_imbalance,
                max_mark_trade_divergence_bps=self.config.max_mark_trade_divergence_bps,
                max_positive_funding_rate=self.config.max_positive_funding_rate,
                min_negative_funding_rate=self.config.min_negative_funding_rate,
                reversion_lookback_ticks=self.config.reversion_lookback_ticks,
                reversion_entry_atr_multiple=self.config.reversion_entry_atr_multiple,
                reversion_max_atr_fraction=self.config.reversion_max_atr_fraction,
                reversion_min_flow_flip=self.config.reversion_min_flow_flip,
                router_range_max_atr_fraction=self.config.router_range_max_atr_fraction,
                router_trend_min_atr_fraction=self.config.router_trend_min_atr_fraction,
                router_trend_min_abs_flow_imbalance=self.config.router_trend_min_abs_flow_imbalance,
                router_range_max_abs_flow_imbalance=self.config.router_range_max_abs_flow_imbalance,
                router_neutral_preference=self.config.router_neutral_preference,
                router_opportunistic_fallback=self.config.router_opportunistic_fallback,
            )
        )
        self._latest_book: TopOfBookSnapshot | None = None
        self._latest_depth: DepthBookSnapshot | None = None
        self._latest_rpi_depth: DepthBookSnapshot | None = None

    def run(self, ticks: Iterable[BacktestTick]) -> BacktestReport:
        report = BacktestReport(ticks=0)
        pending: PendingEntry | None = None
        position: OpenPosition | None = None
        running_equity = Decimal("0")
        equity_peak = Decimal("0")
        last_tick: BacktestTick | None = None

        for tick in ticks:
            last_tick = tick
            report.ticks += 1
            report.market_events += 1
            closed_this_tick = False

            if pending is not None and tick.event_time_ms > pending.expires_at_ms:
                position, executed_qty = self._cancel_pending_remainder(
                    report,
                    pending=pending,
                    position=position,
                    event_time_ms=tick.event_time_ms,
                    next_funding_time_ms=tick.next_funding_time_ms,
                    completion_reason="timeout",
                    timed_out=True,
                )
                pending = None
                if executed_qty <= 0:
                    report.missed_entries += 1

            if position is not None:
                self._maybe_apply_funding(position, tick)
                exit_reason = self._check_exit(position, tick)
                if exit_reason is not None:
                    if pending is not None:
                        position, _ = self._cancel_pending_remainder(
                            report,
                            pending=pending,
                            position=position,
                            event_time_ms=tick.event_time_ms,
                            next_funding_time_ms=tick.next_funding_time_ms,
                            completion_reason="protective_exit",
                            timed_out=False,
                        )
                        pending = None
                    trade = self._close_position(position, tick, exit_reason, latest_book=self._latest_book, report=report)
                    self._record_closed_trade(report, trade)
                    running_equity += trade.net_pnl
                    equity_peak = max(equity_peak, running_equity)
                    report.max_drawdown = max(report.max_drawdown, equity_peak - running_equity)
                    report.equity_curve.append(running_equity)
                    position = None
                    closed_this_tick = True

            if not closed_this_tick and pending is not None:
                fill = self._simulate_entry_fill(pending, tick)
                if fill is not None:
                    filled_qty, fill_price = fill
                    position = self._materialize_entry_exposure(
                        pending=pending,
                        position=position,
                        cumulative_executed_qty=filled_qty,
                        event_time_ms=tick.event_time_ms,
                        fill_price=fill_price,
                        next_funding_time_ms=tick.next_funding_time_ms,
                    )
                    self._record_entry_queue_outcome(
                        report,
                        pending=pending,
                        completed_at_ms=tick.event_time_ms,
                        executed_qty=filled_qty,
                        timed_out=False,
                        completion_reason="full_fill",
                    )
                    pending = None

            if closed_this_tick or position is not None or pending is not None:
                continue

            evaluation = self.model.evaluate_price(
                event_time_ms=tick.event_time_ms,
                price=tick.price,
                funding_rate=tick.funding_rate,
            )
            self._apply_router_evaluation(report, evaluation)
            if evaluation.signal is None:
                if evaluation.rejection_reason:
                    report.signal_gate_rejections += 1
                continue

            build = self._build_pending_entry_from_signal(evaluation.signal)
            self._apply_entry_diagnostics(report, build)
            pending = build.pending
            self._apply_pending_rejection(report, build)

        if pending is not None:
            end_time_ms = last_tick.event_time_ms if last_tick is not None else pending.submitted_at_ms
            next_funding_time_ms = last_tick.next_funding_time_ms if last_tick is not None else 0
            position, executed_qty = self._cancel_pending_remainder(
                report,
                pending=pending,
                position=position,
                event_time_ms=end_time_ms,
                next_funding_time_ms=next_funding_time_ms,
                completion_reason="end_of_data",
                timed_out=False,
            )
            if executed_qty <= 0:
                report.missed_entries += 1
            pending = None

        if position is not None and last_tick is not None:
            trade = self._close_position(position, last_tick, "end_of_data", latest_book=self._latest_book, report=report)
            self._record_closed_trade(report, trade)
            running_equity += trade.net_pnl
            equity_peak = max(equity_peak, running_equity)
            report.max_drawdown = max(report.max_drawdown, equity_peak - running_equity)
            report.equity_curve.append(running_equity)

        return report

    def _record_closed_trade(self, report: BacktestReport, trade: BacktestTrade) -> None:
        report.trades.append(trade)
        report.gross_pnl += trade.gross_pnl
        report.fee_pnl += trade.fee_pnl
        report.funding_pnl += trade.funding_pnl
        report.net_pnl += trade.net_pnl
        report.notional_sum += trade.entry_notional_usdt
        report.sizing_multiplier_sum += trade.sizing_multiplier
        if trade.net_pnl >= 0:
            report.wins += 1
        else:
            report.losses += 1
        selected_strategy_kind = trade.selected_strategy_kind or trade.strategy_kind
        if hasattr(self.model, "record_trade_outcome") and selected_strategy_kind:
            entry_notional = trade.entry_notional_usdt
            net_pnl_bps = Decimal("0")
            if entry_notional > 0:
                net_pnl_bps = (trade.net_pnl / entry_notional) * Decimal("10000")
            self.model.record_trade_outcome(
                strategy_kind=selected_strategy_kind,
                net_pnl_bps=net_pnl_bps,
            )

    def _detect_signal(self, history: list[Decimal], current_price: Decimal) -> Side | None:
        upper = max(history)
        lower = min(history)
        if current_price > upper:
            return Side.BUY
        if current_price < lower:
            return Side.SELL
        return None

    def _estimate_atr(self, history: list[Decimal]) -> Decimal:
        if len(history) < 2:
            return Decimal("0")
        diffs = [abs(history[idx] - history[idx - 1]) for idx in range(1, len(history))]
        atr_window = min(self.config.atr_window_ticks, len(diffs))
        atr = _mean(diffs[-atr_window:]) if atr_window > 0 else Decimal("0")
        if atr == 0:
            atr = history[-1] * Decimal("0.001")
        return atr

    def _build_pending_entry(self, side: Side, history: list[Decimal], tick: BacktestTick) -> PendingEntryResult:
        atr = self._estimate_atr(history)
        return self._build_pending_entry_core(
            side=side,
            signal_price=tick.price,
            atr=atr,
            event_time_ms=tick.event_time_ms,
            signal_context=None,
            crowding_score=None,
            strategy_kind=self.config.strategy_kind,
            selected_strategy_kind=self.config.strategy_kind,
        )

    def _build_pending_entry_from_signal(
        self,
        signal: BreakoutSignal,
        *,
        crowding_score: CrowdingScore | None = None,
    ) -> PendingEntryResult:
        return self._build_pending_entry_core(
            side=signal.side,
            signal_price=signal.price,
            atr=signal.atr,
            event_time_ms=signal.event_time_ms,
            signal_context=signal.context,
            crowding_score=crowding_score,
            strategy_kind=signal.strategy_kind,
            selected_strategy_kind=signal.selected_strategy_kind or signal.strategy_kind,
        )

    def _build_pending_entry_core(
        self,
        *,
        side: Side,
        signal_price: Decimal,
        atr: Decimal,
        event_time_ms: int,
        signal_context: SignalContext | None,
        crowding_score: CrowdingScore | None,
        strategy_kind: str,
        selected_strategy_kind: str,
    ) -> PendingEntryResult:
        economics_snapshot = self._economics_snapshot_for_event(event_time_ms=event_time_ms)
        economics_feedback_decision = (
            economics_snapshot.feedback_decision if economics_snapshot is not None else None
        )
        economics_regime_decision = (
            economics_snapshot.regime_decision if economics_snapshot is not None else None
        )
        economics_dashboard_end_date = (
            economics_snapshot.dashboard_end_date if economics_snapshot is not None else ""
        )
        if economics_regime_decision is not None and economics_regime_decision.observe_only:
            return PendingEntryResult(
                pending=None,
                rejection_reason="economics_regime_observe_only",
                economics_feedback_decision=economics_feedback_decision,
                economics_regime_decision=economics_regime_decision,
                economics_dashboard_end_date=economics_dashboard_end_date,
            )

        sizing = self.adaptive_entry_policy.evaluate(
            AdaptiveEntryInputs(
                side=side,
                base_notional_usdt=self.config.position_notional_usdt,
                flow_imbalance=signal_context.flow_imbalance if signal_context is not None else None,
                crowding_side_score=crowding_score.side_score if crowding_score is not None else None,
                funding_rate=signal_context.funding_rate if signal_context is not None else None,
                mark_trade_divergence_bps=(
                    signal_context.mark_trade_divergence_bps if signal_context is not None else None
                ),
            )
        )
        if not sizing.execute:
            return PendingEntryResult(
                pending=None,
                rejection_reason=sizing.reason,
                sizing_decision=sizing,
                economics_feedback_decision=economics_feedback_decision,
                economics_regime_decision=economics_regime_decision,
                economics_dashboard_end_date=economics_dashboard_end_date,
            )

        volatility = self.volatility_sizing_policy.evaluate(
            VolatilitySizingInputs(
                base_notional_usdt=sizing.target_notional_usdt,
                atr=atr,
                reference_price=signal_price,
            )
        )
        if not volatility.execute:
            return PendingEntryResult(
                pending=None,
                rejection_reason=volatility.reason,
                sizing_decision=sizing,
                volatility_decision=volatility,
                economics_feedback_decision=economics_feedback_decision,
                economics_regime_decision=economics_regime_decision,
                economics_dashboard_end_date=economics_dashboard_end_date,
            )

        economics_feedback_multiplier = (
            economics_feedback_decision.multiplier if economics_feedback_decision is not None else Decimal("1")
        )
        economics_regime_multiplier = (
            economics_regime_decision.size_multiplier
            if economics_regime_decision is not None and economics_regime_decision.reduce_size
            else Decimal("1")
        )
        effective_target_notional_usdt = (
            volatility.target_notional_usdt
            * economics_feedback_multiplier
            * economics_regime_multiplier
        )
        if effective_target_notional_usdt < self.config.min_effective_notional_usdt:
            return PendingEntryResult(
                pending=None,
                rejection_reason="effective_notional_too_small_after_volatility",
                sizing_decision=sizing,
                volatility_decision=volatility,
                economics_feedback_decision=economics_feedback_decision,
                economics_regime_decision=economics_regime_decision,
                economics_dashboard_end_date=economics_dashboard_end_date,
            )

        qty = effective_target_notional_usdt / signal_price
        combined_multiplier = (
            sizing.multiplier
            * volatility.multiplier
            * economics_feedback_multiplier
            * economics_regime_multiplier
        )
        order = self.planner.entry_order(
            symbol=self.symbol,
            side=side,
            qty=qty,
            mark_price=signal_price,
            position_side=PositionSide.BOTH,
        )
        stop_algo, tp_algo = self.planner.bracket_exits(
            symbol=self.symbol,
            entry_side=side,
            qty=qty,
            entry_price=order.price or signal_price,
            atr=atr,
            position_side=PositionSide.BOTH,
        )

        if self.validator is not None:
            result = self.validator.validate_limit(
                price=order.price or signal_price,
                qty=qty,
                reference_price=signal_price,
            )
            if not result.ok or result.normalized_price is None or result.normalized_qty is None:
                return PendingEntryResult(
                    pending=None,
                    rejection_reason="validation_reject",
                    sizing_decision=sizing,
                    volatility_decision=volatility,
                    economics_feedback_decision=economics_feedback_decision,
                    economics_regime_decision=economics_regime_decision,
                    economics_dashboard_end_date=economics_dashboard_end_date,
                )
            qty = result.normalized_qty
            limit_price = result.normalized_price
        else:
            limit_price = order.price or signal_price

        queue_state = self._place_passive_order(side=side, limit_price=limit_price, qty=qty)
        queue_decision = self.queue_admission_policy.evaluate(
            QueueAdmissionInputs(
                qty=qty,
                entry_timeout_seconds=self.config.entry_timeout_seconds,
                flow_window_seconds=self.config.trade_flow_window_seconds,
                directional_flow_qty=self._directional_queue_flow_qty(signal_context, side),
                queue_state=queue_state,
            )
        )
        if not queue_decision.execute:
            return PendingEntryResult(
                pending=None,
                rejection_reason=queue_decision.reason,
                sizing_decision=sizing,
                volatility_decision=volatility,
                queue_decision=queue_decision,
                economics_feedback_decision=economics_feedback_decision,
                economics_regime_decision=economics_regime_decision,
                economics_dashboard_end_date=economics_dashboard_end_date,
            )

        depth_liquidity_decision = self.depth_liquidity_policy.evaluate_for_entry(
            entry_side=side,
            qty=qty,
            book=self._latest_depth,
        )
        if not depth_liquidity_decision.execute:
            return PendingEntryResult(
                pending=None,
                rejection_reason=depth_liquidity_decision.reason,
                sizing_decision=sizing,
                volatility_decision=volatility,
                queue_decision=queue_decision,
                depth_liquidity_decision=depth_liquidity_decision,
                economics_feedback_decision=economics_feedback_decision,
                economics_regime_decision=economics_regime_decision,
                economics_dashboard_end_date=economics_dashboard_end_date,
            )
        if queue_state is None and self._would_cross_as_taker(side=side, limit_price=limit_price, reference_price=signal_price):
            return PendingEntryResult(
                pending=None,
                rejection_reason="post_only_cross_reject",
                sizing_decision=sizing,
                volatility_decision=volatility,
                queue_decision=queue_decision,
                depth_liquidity_decision=depth_liquidity_decision,
                economics_feedback_decision=economics_feedback_decision,
                economics_regime_decision=economics_regime_decision,
                economics_dashboard_end_date=economics_dashboard_end_date,
            )

        return PendingEntryResult(
            pending=PendingEntry(
                side=side,
                qty=qty,
                limit_price=limit_price,
                atr=atr,
                submitted_at_ms=event_time_ms,
                expires_at_ms=event_time_ms + self.config.entry_timeout_seconds * 1000,
                stop_price=stop_algo.trigger_price,
                take_profit_price=tp_algo.trigger_price,
                target_notional_usdt=effective_target_notional_usdt,
                sizing_multiplier=combined_multiplier,
                strategy_kind=strategy_kind,
                selected_strategy_kind=selected_strategy_kind,
                queue_state=queue_state,
                queue_expectation=self._queue_expectation_from_decision(queue_decision),
            ),
            sizing_decision=sizing,
            volatility_decision=volatility,
            queue_decision=queue_decision,
            depth_liquidity_decision=depth_liquidity_decision,
            economics_feedback_decision=economics_feedback_decision,
            economics_regime_decision=economics_regime_decision,
            economics_dashboard_end_date=economics_dashboard_end_date,
        )

    def _economics_snapshot_for_event(self, *, event_time_ms: int) -> BacktestEconomicsSnapshot | None:
        if self.economics_provider is None:
            return None
        return self.economics_provider.snapshot_for_event(event_time_ms=event_time_ms)

    def _place_passive_order(self, *, side: Side, limit_price: Decimal, qty: Decimal) -> PassiveOrderState | None:
        effective_depth = self._effective_depth_snapshot()
        if self.config.use_local_depth_fills and effective_depth is not None:
            return self.depth_fill_model.place_order(side=side, limit_price=limit_price, qty=qty, book=effective_depth)
        if not self.config.use_book_ticker_fills or self._latest_book is None:
            return None
        return self.book_fill_model.place_order(side=side, limit_price=limit_price, qty=qty, book=self._latest_book)

    def _would_cross_as_taker(self, *, side: Side, limit_price: Decimal, reference_price: Decimal) -> bool:
        effective_depth = self._effective_depth_snapshot()
        if effective_depth is not None and effective_depth.best_bid_price > 0 and effective_depth.best_ask_price > 0:
            if side == Side.BUY:
                return limit_price >= effective_depth.best_ask_price
            return limit_price <= effective_depth.best_bid_price
        if self._latest_book is not None:
            if side == Side.BUY:
                return limit_price >= self._latest_book.ask_price
            return limit_price <= self._latest_book.bid_price

        spread = reference_price * self.config.synthetic_spread_bps / Decimal("10000")
        best_bid = reference_price - spread / Decimal("2")
        best_ask = reference_price + spread / Decimal("2")
        if side == Side.BUY:
            return limit_price >= best_ask
        return limit_price <= best_bid

    def _simulate_entry_fill(self, pending: PendingEntry, tick: BacktestTick) -> tuple[Decimal, Decimal] | None:
        if pending.queue_state is not None:
            return (pending.qty, pending.limit_price) if pending.queue_state.filled else None
        if pending.side == Side.BUY and tick.price <= pending.limit_price:
            return pending.qty, pending.limit_price
        if pending.side == Side.SELL and tick.price >= pending.limit_price:
            return pending.qty, pending.limit_price
        return None

    @staticmethod
    def _pending_executed_qty(pending: PendingEntry) -> Decimal:
        if pending.queue_state is None:
            return pending.materialized_qty
        return pending.queue_state.executed_qty

    def _materialize_entry_exposure(
        self,
        *,
        pending: PendingEntry,
        position: OpenPosition | None,
        cumulative_executed_qty: Decimal,
        event_time_ms: int,
        fill_price: Decimal,
        next_funding_time_ms: int,
    ) -> OpenPosition | None:
        cumulative_executed_qty = max(Decimal("0"), min(pending.qty, cumulative_executed_qty))
        if cumulative_executed_qty <= pending.materialized_qty:
            return position

        fill_qty = cumulative_executed_qty - pending.materialized_qty
        if position is None:
            position = self._open_position(
                pending=pending,
                event_time_ms=event_time_ms,
                fill_price=fill_price,
                next_funding_time_ms=next_funding_time_ms,
                fill_qty=fill_qty,
            )
        else:
            if position.side != pending.side:
                raise RuntimeError("partial_entry_side_mismatch")
            combined_qty = position.qty + fill_qty
            if combined_qty <= 0:
                raise RuntimeError("partial_entry_non_positive_combined_qty")
            position.entry_price = (
                position.entry_price * position.qty + fill_price * fill_qty
            ) / combined_qty
            position.entry_fee += fill_qty * fill_price * self.config.maker_fee_bps / Decimal("10000")
            position.qty = combined_qty
            if position.next_funding_time_ms <= 0 and next_funding_time_ms > 0:
                position.next_funding_time_ms = next_funding_time_ms

        pending.materialized_qty = cumulative_executed_qty
        return position

    def _synchronize_pending_queue_progress(
        self,
        report: BacktestReport,
        *,
        pending: PendingEntry,
        position: OpenPosition | None,
        event_time_ms: int,
        next_funding_time_ms: int,
    ) -> tuple[PendingEntry | None, OpenPosition | None]:
        executed_qty = self._pending_executed_qty(pending)
        position = self._materialize_entry_exposure(
            pending=pending,
            position=position,
            cumulative_executed_qty=executed_qty,
            event_time_ms=event_time_ms,
            fill_price=pending.limit_price,
            next_funding_time_ms=next_funding_time_ms,
        )
        if pending.queue_state is None or not pending.queue_state.filled:
            return pending, position

        self._record_entry_queue_outcome(
            report,
            pending=pending,
            completed_at_ms=event_time_ms,
            executed_qty=executed_qty,
            timed_out=False,
            completion_reason="full_fill",
        )
        return None, position

    def _cancel_pending_remainder(
        self,
        report: BacktestReport,
        *,
        pending: PendingEntry,
        position: OpenPosition | None,
        event_time_ms: int,
        next_funding_time_ms: int,
        completion_reason: str,
        timed_out: bool,
    ) -> tuple[OpenPosition | None, Decimal]:
        executed_qty = self._pending_executed_qty(pending)
        position = self._materialize_entry_exposure(
            pending=pending,
            position=position,
            cumulative_executed_qty=executed_qty,
            event_time_ms=event_time_ms,
            fill_price=pending.limit_price,
            next_funding_time_ms=next_funding_time_ms,
        )
        self._record_entry_queue_outcome(
            report,
            pending=pending,
            completed_at_ms=event_time_ms,
            executed_qty=executed_qty,
            timed_out=timed_out,
            completion_reason=completion_reason,
        )
        return position, executed_qty

    def _check_exit(self, position: OpenPosition, tick: BacktestTick) -> str | None:
        if position.side == Side.BUY:
            if tick.price <= position.stop_price:
                return "stop"
            if tick.price >= position.take_profit_price:
                return "take_profit"
        else:
            if tick.price >= position.stop_price:
                return "stop"
            if tick.price <= position.take_profit_price:
                return "take_profit"

        if tick.event_time_ms >= position.hold_until_ms:
            return "time_stop"
        return None

    def _close_position(
        self,
        position: OpenPosition,
        tick: BacktestTick,
        exit_reason: str,
        *,
        latest_book: TopOfBookSnapshot | None,
        report: BacktestReport | None = None,
    ) -> BacktestTrade:
        exit_price = self._resolve_taker_exit_price(position.side, tick, latest_book=latest_book, report=report, qty=position.qty)
        if position.side == Side.BUY:
            gross_pnl = (exit_price - position.entry_price) * position.qty
        else:
            gross_pnl = (position.entry_price - exit_price) * position.qty

        exit_fee = position.qty * exit_price * self.config.taker_fee_bps / Decimal("10000")
        fee_pnl = -(position.entry_fee + exit_fee)
        net_pnl = gross_pnl + fee_pnl + position.funding_pnl
        return BacktestTrade(
            side=position.side,
            qty=position.qty,
            entry_price=position.entry_price,
            exit_price=exit_price,
            entry_time_ms=position.entry_time_ms,
            exit_time_ms=tick.event_time_ms,
            gross_pnl=gross_pnl,
            fee_pnl=fee_pnl,
            funding_pnl=position.funding_pnl,
            net_pnl=net_pnl,
            exit_reason=exit_reason,
            entry_notional_usdt=position.entry_price * position.qty,
            sizing_multiplier=position.sizing_multiplier,
            strategy_kind=position.strategy_kind,
            selected_strategy_kind=position.selected_strategy_kind,
        )

    def _resolve_taker_exit_price(
        self,
        side: Side,
        tick: BacktestTick,
        *,
        latest_book: TopOfBookSnapshot | None,
        report: BacktestReport | None,
        qty: Decimal,
    ) -> Decimal:
        fallback_reasons: list[str] = []
        if report is not None:
            report.last_exit_pricing_source = ""
            report.last_exit_pricing_fallback_reason = ""
            report.last_exit_depth_age_ms = None
            report.last_exit_book_age_ms = None

        effective_depth = self._effective_depth_snapshot()
        if effective_depth is not None:
            depth_age_ms = tick.event_time_ms - effective_depth.event_time_ms
            if report is not None:
                report.last_exit_depth_age_ms = depth_age_ms
            depth_rejection = self._exit_snapshot_rejection_reason(
                age_ms=depth_age_ms,
                max_age_ms=self.config.max_depth_snapshot_staleness_ms,
                source="depth",
            )
            if depth_rejection:
                fallback_reasons.append(depth_rejection)
                if report is not None:
                    report.exit_depth_fallback_count += 1
            else:
                exit_side = Side.SELL if side == Side.BUY else Side.BUY
                estimate = self.depth_sweep_model.estimate(side=exit_side, qty=qty, book=effective_depth)
                self._apply_exit_depth_estimate(report, estimate)
                if estimate is not None and estimate.avg_price is not None:
                    if report is not None:
                        report.last_exit_pricing_source = "depth"
                        report.last_exit_pricing_fallback_reason = "|".join(fallback_reasons)
                        report.exit_depth_pricing_count += 1
                    return estimate.avg_price
                fallback_reasons.append("exit_depth_sweep_unavailable")
                if report is not None:
                    report.exit_depth_fallback_count += 1

        trusted_book = latest_book
        if latest_book is not None:
            book_age_ms = tick.event_time_ms - latest_book.event_time_ms
            if report is not None:
                report.last_exit_book_age_ms = book_age_ms
            book_rejection = self._exit_snapshot_rejection_reason(
                age_ms=book_age_ms,
                max_age_ms=self.config.max_book_ticker_staleness_ms,
                source="book",
            )
            if book_rejection:
                fallback_reasons.append(book_rejection)
                trusted_book = None
                if report is not None:
                    report.exit_book_fallback_count += 1

        if trusted_book is not None:
            if report is not None:
                report.last_exit_pricing_source = "book_ticker"
                report.last_exit_pricing_fallback_reason = "|".join(fallback_reasons)
                report.exit_book_pricing_count += 1
            slip_reference = trusted_book.bid_price if side == Side.BUY else trusted_book.ask_price
        else:
            if not fallback_reasons:
                fallback_reasons.append("no_exit_liquidity_snapshot")
            if report is not None:
                report.last_exit_pricing_source = "mark"
                report.last_exit_pricing_fallback_reason = "|".join(fallback_reasons)
                report.exit_mark_pricing_count += 1
            slip_reference = tick.price

        slip = slip_reference * self.config.taker_slippage_bps / Decimal("10000")
        if side == Side.BUY:
            return max(Decimal("0"), slip_reference - slip)
        return slip_reference + slip

    @staticmethod
    def _exit_snapshot_rejection_reason(*, age_ms: int, max_age_ms: int | None, source: str) -> str:
        if age_ms < 0:
            return f"future_exit_{source}_snapshot"
        if max_age_ms is None:
            return f"exit_{source}_freshness_budget_not_configured"
        if max_age_ms < 0:
            return f"invalid_exit_{source}_freshness_budget"
        if age_ms > max_age_ms:
            return f"stale_exit_{source}"
        return ""

    @staticmethod
    def _apply_exit_depth_estimate(report: BacktestReport | None, estimate: DepthSweepEstimate | None) -> None:
        if report is None or estimate is None:
            return
        report.last_exit_depth_coverage_ratio = estimate.displayed_coverage_ratio
        report.last_exit_depth_sweep_bps = estimate.sweep_slippage_bps
        report.last_exit_depth_levels_consumed = estimate.levels_consumed
        report.last_exit_synthetic_tail_coverage_ratio = estimate.synthetic_tail_coverage_ratio
        report.last_exit_synthetic_tail_levels_consumed = estimate.synthetic_tail_levels_consumed
        report.last_exit_terminal_tail_ratio = estimate.terminal_tail_ratio
        report.exit_depth_estimate_count += 1
        report.exit_depth_coverage_ratio_sum += estimate.displayed_coverage_ratio
        report.exit_depth_levels_consumed_sum += Decimal(estimate.levels_consumed)
        report.exit_synthetic_tail_coverage_ratio_sum += estimate.synthetic_tail_coverage_ratio
        report.exit_synthetic_tail_levels_consumed_sum += Decimal(estimate.synthetic_tail_levels_consumed)
        report.exit_terminal_tail_ratio_sum += estimate.terminal_tail_ratio
        if estimate.sweep_slippage_bps is not None:
            report.exit_depth_sweep_bps_sum += estimate.sweep_slippage_bps

    def _open_position(
        self,
        *,
        pending: PendingEntry,
        event_time_ms: int,
        fill_price: Decimal,
        next_funding_time_ms: int,
        fill_qty: Decimal | None = None,
    ) -> OpenPosition:
        position_qty = pending.qty if fill_qty is None else fill_qty
        entry_fee = position_qty * fill_price * self.config.maker_fee_bps / Decimal("10000")
        return OpenPosition(
            side=pending.side,
            qty=position_qty,
            entry_price=fill_price,
            entry_time_ms=event_time_ms,
            stop_price=pending.stop_price,
            take_profit_price=pending.take_profit_price,
            hold_until_ms=event_time_ms + self.config.max_hold_seconds * 1000,
            entry_fee=entry_fee,
            target_notional_usdt=pending.target_notional_usdt,
            sizing_multiplier=pending.sizing_multiplier,
            strategy_kind=pending.strategy_kind,
            selected_strategy_kind=pending.selected_strategy_kind,
            next_funding_time_ms=next_funding_time_ms,
        )

    def _maybe_apply_funding(self, position: OpenPosition, tick: BacktestTick) -> Decimal:
        if position.next_funding_time_ms <= 0:
            position.next_funding_time_ms = tick.next_funding_time_ms
            return Decimal("0")

        if tick.event_time_ms < position.next_funding_time_ms:
            return Decimal("0")

        notional = abs(position.qty) * tick.price
        funding = -(notional * tick.funding_rate) if position.side == Side.BUY else notional * tick.funding_rate
        position.funding_pnl += funding

        if tick.next_funding_time_ms > position.next_funding_time_ms:
            position.next_funding_time_ms = tick.next_funding_time_ms
        else:
            position.next_funding_time_ms += 8 * 60 * 60 * 1000
        return funding

    @staticmethod
    def _directional_queue_flow_qty(signal_context: SignalContext | None, side: Side) -> Decimal:
        if signal_context is None:
            return Decimal("0")
        return signal_context.sell_aggressor_qty if side == Side.BUY else signal_context.buy_aggressor_qty

    @staticmethod
    def _queue_expectation_from_decision(decision: QueueAdmissionDecision | None) -> EntryQueueExpectation | None:
        if decision is None:
            return None
        return EntryQueueExpectation(
            expected_fill_ratio=decision.expected_fill_ratio,
            expected_queue_clear_seconds=decision.expected_queue_clear_seconds,
            queue_ahead_ratio=decision.queue_ahead_ratio,
            directional_flow_qty_per_second=decision.directional_flow_qty_per_second,
        )

    def _record_entry_queue_outcome(
        self,
        report: BacktestReport,
        *,
        pending: PendingEntry,
        completed_at_ms: int,
        executed_qty: Decimal,
        timed_out: bool,
        completion_reason: str,
    ) -> None:
        outcome = self.queue_calibration_model.evaluate(
            expectation=pending.queue_expectation,
            submitted_at_ms=pending.submitted_at_ms,
            completed_at_ms=completed_at_ms,
            requested_qty=pending.qty,
            executed_qty=executed_qty,
            timed_out=timed_out,
        )
        materialization_mismatch = abs(outcome.executed_qty - pending.materialized_qty)
        if materialization_mismatch > 0:
            report.unmodeled_partial_entry_count += 1
            report.unmodeled_partial_entry_qty += materialization_mismatch
        elif Decimal("0") < outcome.executed_qty < outcome.requested_qty:
            report.modeled_partial_entry_count += 1
            report.modeled_partial_entry_qty += outcome.executed_qty
        if outcome.executed_qty < outcome.requested_qty:
            report.entry_remainder_cancel_count += 1
        report.entry_outcome_count += 1
        if outcome.timed_out:
            report.entry_timeout_count += 1
        report.last_entry_completion_reason = completion_reason
        report.last_actual_entry_fill_ratio = outcome.actual_fill_ratio
        report.last_entry_fill_latency_seconds = outcome.actual_fill_latency_seconds
        report.last_entry_fill_ratio_shortfall = outcome.fill_ratio_shortfall
        report.last_entry_fill_latency_overshoot_seconds = outcome.fill_latency_overshoot_seconds
        report.realized_entry_fill_ratio_sum += outcome.actual_fill_ratio
        report.entry_fill_latency_seconds_sum += outcome.actual_fill_latency_seconds
        report.entry_fill_latency_count += 1
        if outcome.fill_ratio_shortfall is not None:
            report.entry_fill_ratio_shortfall_sum += outcome.fill_ratio_shortfall
            report.entry_fill_ratio_shortfall_count += 1
        if outcome.fill_latency_overshoot_seconds is not None:
            report.entry_fill_latency_overshoot_seconds_sum += outcome.fill_latency_overshoot_seconds
            report.entry_fill_latency_overshoot_count += 1
        selected_strategy_kind = pending.selected_strategy_kind or pending.strategy_kind
        if hasattr(self.model, "record_entry_outcome") and selected_strategy_kind:
            self.model.record_entry_outcome(
                strategy_kind=selected_strategy_kind,
                actual_fill_ratio=outcome.actual_fill_ratio,
                fill_ratio_shortfall=outcome.fill_ratio_shortfall,
                fill_latency_overshoot_seconds=outcome.fill_latency_overshoot_seconds,
                timed_out=outcome.timed_out,
            )

    @staticmethod
    def _apply_router_evaluation(report: BacktestReport, evaluation) -> None:
        if evaluation is None:
            return
        strategy_kind = evaluation.signal.strategy_kind if evaluation.signal is not None else ""
        if evaluation.router_regime:
            if strategy_kind == "ensemble":
                report.last_ensemble_regime = evaluation.router_regime
            else:
                report.last_router_regime = evaluation.router_regime
        if evaluation.preferred_strategy_kind:
            if strategy_kind == "ensemble":
                report.last_ensemble_preferred_strategy_kind = evaluation.preferred_strategy_kind
            else:
                report.last_router_preferred_strategy_kind = evaluation.preferred_strategy_kind
        if evaluation.selected_strategy_kind:
            if strategy_kind == "ensemble":
                report.last_ensemble_selected_strategy_kind = evaluation.selected_strategy_kind
            else:
                report.last_router_selected_strategy_kind = evaluation.selected_strategy_kind
        if evaluation.ensemble_breakout_score is not None:
            report.last_ensemble_breakout_score = evaluation.ensemble_breakout_score
        if evaluation.ensemble_reversion_score is not None:
            report.last_ensemble_reversion_score = evaluation.ensemble_reversion_score
        if evaluation.signal is None or not evaluation.selected_strategy_kind:
            return
        if strategy_kind == "ensemble":
            if evaluation.selected_strategy_kind == "breakout":
                report.ensemble_breakout_signal_count += 1
            elif evaluation.selected_strategy_kind == "reversion":
                report.ensemble_reversion_signal_count += 1
            if (
                evaluation.preferred_strategy_kind
                and evaluation.selected_strategy_kind != evaluation.preferred_strategy_kind
            ):
                report.ensemble_override_signal_count += 1
            return
        if evaluation.selected_strategy_kind == "breakout":
            report.router_breakout_signal_count += 1
        elif evaluation.selected_strategy_kind == "reversion":
            report.router_reversion_signal_count += 1
        if (
            evaluation.preferred_strategy_kind
            and evaluation.selected_strategy_kind != evaluation.preferred_strategy_kind
        ):
            report.router_fallback_signal_count += 1

    @staticmethod
    def _apply_entry_diagnostics(report: BacktestReport, build: PendingEntryResult) -> None:
        if build.volatility_decision is not None:
            report.last_volatility_multiplier = build.volatility_decision.multiplier
            report.last_atr_fraction_bps = build.volatility_decision.atr_fraction * Decimal("10000")
        report.last_economics_dashboard_end_date = build.economics_dashboard_end_date
        if build.economics_regime_decision is not None:
            report.last_economics_dashboard_active_day_count = build.economics_regime_decision.active_day_count
            report.last_economics_regime_action = build.economics_regime_decision.action
            report.last_economics_regime_size_multiplier = build.economics_regime_decision.size_multiplier
            report.last_economics_regime_negative_day_ratio = build.economics_regime_decision.negative_day_ratio
            report.last_economics_regime_recent_day_net_realized_bps = (
                build.economics_regime_decision.recent_day_net_realized_bps
            )
            report.last_economics_regime_average_maker_ratio = build.economics_regime_decision.average_maker_ratio
            if build.pending is not None and build.economics_regime_decision.reduce_size:
                report.economics_regime_reduce_size_applications += 1
        if build.economics_feedback_decision is not None:
            report.last_economics_feedback_multiplier = build.economics_feedback_decision.multiplier
            report.last_economics_feedback_total_penalty = build.economics_feedback_decision.total_penalty
            report.last_economics_feedback_reason = build.economics_feedback_decision.reason
            if build.economics_feedback_decision.applied:
                report.economics_feedback_decision_count += 1
                report.economics_feedback_multiplier_sum += build.economics_feedback_decision.multiplier
        if build.queue_decision is not None:
            report.last_expected_fill_ratio = build.queue_decision.expected_fill_ratio
            report.last_expected_queue_clear_seconds = build.queue_decision.expected_queue_clear_seconds
            report.last_queue_ahead_ratio = build.queue_decision.queue_ahead_ratio
            report.last_directional_queue_flow_qty_per_second = build.queue_decision.directional_flow_qty_per_second
            report.queue_decision_count += 1
            report.expected_fill_ratio_sum += build.queue_decision.expected_fill_ratio
            report.directional_queue_flow_rate_sum += build.queue_decision.directional_flow_qty_per_second
            if build.queue_decision.expected_queue_clear_seconds is not None:
                report.queue_clear_seconds_sum += build.queue_decision.expected_queue_clear_seconds
                report.queue_clear_seconds_count += 1
            if build.queue_decision.queue_ahead_ratio is not None:
                report.queue_ahead_ratio_sum += build.queue_decision.queue_ahead_ratio
                report.queue_ahead_ratio_count += 1
        if build.depth_liquidity_decision is not None:
            estimate = build.depth_liquidity_decision.estimate
            if estimate is not None:
                report.last_exit_depth_coverage_ratio = estimate.displayed_coverage_ratio
                report.last_exit_depth_sweep_bps = estimate.sweep_slippage_bps
                report.last_exit_depth_levels_consumed = estimate.levels_consumed

    @staticmethod
    def _apply_pending_rejection(report: BacktestReport, build: PendingEntryResult) -> None:
        if build.pending is not None or not build.rejection_reason:
            return
        if build.rejection_reason == "economics_regime_observe_only":
            report.signal_gate_rejections += 1
            report.economics_regime_observe_rejections += 1
            return
        if build.rejection_reason in {
            "entry_quality_below_threshold",
            "effective_notional_too_small",
            "atr_fraction_too_high",
            "effective_notional_too_small_after_volatility",
        }:
            report.adaptive_abstentions += 1
            return
        if build.rejection_reason in {
            "queue_ahead_too_large",
            "insufficient_directional_queue_flow",
            "expected_queue_clear_too_slow",
            "expected_fill_ratio_too_low",
            "post_only_cross_reject",
        }:
            report.signal_gate_rejections += 1
            report.queue_gate_rejections += 1
            return
        if build.rejection_reason in {
            "insufficient_exit_depth_coverage",
            "exit_depth_sweep_too_costly",
            "no_exit_depth",
        }:
            report.signal_gate_rejections += 1
            report.depth_liquidity_gate_rejections += 1
            return
        report.rejected_entries += 1

    def _update_latest_book(self, report: BacktestReport, book: TopOfBookSnapshot, *, event_time_ms: int) -> None:
        self._latest_book = book
        report.last_book_spread_bps = book.spread_bps
        report.last_book_age_ms = max(0, event_time_ms - book.event_time_ms)

    def _update_latest_depth(self, report: BacktestReport, depth: DepthBookSnapshot, *, event_time_ms: int) -> None:
        self._latest_depth = depth
        report.depth_events += 1
        self._apply_effective_depth_report(report, event_time_ms=event_time_ms)

    def _update_latest_rpi_depth(self, report: BacktestReport, depth: DepthBookSnapshot, *, event_time_ms: int) -> None:
        self._latest_rpi_depth = depth
        report.rpi_depth_events += 1
        report.last_rpi_depth_age_ms = max(0, event_time_ms - depth.event_time_ms)
        report.last_rpi_depth_levels = depth.levels
        self._apply_effective_depth_report(report, event_time_ms=event_time_ms)

    def _effective_depth_snapshot(self) -> DepthBookSnapshot | None:
        if self.config.use_rpi_depth_fills and self._latest_rpi_depth is not None:
            return self._latest_rpi_depth
        return self._latest_depth

    def _apply_effective_depth_report(self, report: BacktestReport, *, event_time_ms: int) -> None:
        effective_depth = self._effective_depth_snapshot()
        if effective_depth is None:
            report.last_depth_imbalance = None
            report.last_depth_age_ms = None
            report.last_depth_levels = 0
            report.last_depth_source = ""
            return
        report.last_depth_imbalance = effective_depth.imbalance
        report.last_depth_age_ms = max(0, event_time_ms - effective_depth.event_time_ms)
        report.last_depth_levels = effective_depth.levels
        report.last_depth_source = "rpi" if effective_depth is self._latest_rpi_depth else "standard"

    def _book_gate_reason(self, *, event_time_ms: int) -> str:
        if self._latest_book is None:
            return ""
        if (
            self.config.max_book_ticker_staleness_ms is not None
            and event_time_ms - self._latest_book.event_time_ms > self.config.max_book_ticker_staleness_ms
        ):
            return "stale_book_ticker"
        if (
            self.config.max_book_spread_bps is not None
            and self._latest_book.spread_bps > self.config.max_book_spread_bps
        ):
            return "book_spread_too_wide"
        return ""

    def _depth_gate_reason(self, *, side: Side, event_time_ms: int) -> str:
        effective_depth = self._effective_depth_snapshot()
        if effective_depth is None:
            return ""
        if (
            self.config.max_depth_snapshot_staleness_ms is not None
            and event_time_ms - effective_depth.event_time_ms > self.config.max_depth_snapshot_staleness_ms
        ):
            return "stale_local_depth"
        if self.config.min_depth_imbalance is not None:
            imbalance = effective_depth.imbalance
            if side == Side.BUY and imbalance < self.config.min_depth_imbalance:
                return "depth_imbalance_not_confirmed_for_buy"
            if side == Side.SELL and imbalance > -self.config.min_depth_imbalance:
                return "depth_imbalance_not_confirmed_for_sell"
        return ""


class ParityBreakoutBacktester(BreakoutBacktester):
    def __init__(
        self,
        *,
        symbol: str,
        config: BreakoutBacktestConfig | None = None,
        filters: SymbolFilters | None = None,
        economics_data_dir: Path | None = None,
    ) -> None:
        super().__init__(symbol=symbol, config=config, filters=filters, economics_data_dir=economics_data_dir)
        self.crowding_gate_config = CrowdingGateConfig(
            enabled=(
                self.config.max_crowding_snapshot_age_seconds is not None
                or self.config.min_crowding_score is not None
            ),
            max_snapshot_age_ms=(
                None
                if self.config.max_crowding_snapshot_age_seconds is None
                else self.config.max_crowding_snapshot_age_seconds * 1000
            ),
            min_side_score=self.config.min_crowding_score,
            oi_expansion_weight=self.config.crowding_oi_expansion_weight,
        )

    def run(self, events: Iterable[BacktestEvent]) -> BacktestReport:  # type: ignore[override]
        report = BacktestReport(ticks=0)
        pending: PendingEntry | None = None
        position: OpenPosition | None = None
        running_equity = Decimal("0")
        equity_peak = Decimal("0")
        last_tick: BacktestTick | None = None
        last_event_time_ms = 0
        contract_status = ""
        latest_crowding_snapshot: dict[str, object] | None = None

        for event in events:
            last_event_time_ms = max(last_event_time_ms, event.event_time_ms)
            report.market_events += 1

            if pending is not None and event.event_time_ms > pending.expires_at_ms:
                next_funding_time_ms = last_tick.next_funding_time_ms if last_tick is not None else 0
                position, executed_qty = self._cancel_pending_remainder(
                    report,
                    pending=pending,
                    position=position,
                    event_time_ms=event.event_time_ms,
                    next_funding_time_ms=next_funding_time_ms,
                    completion_reason="timeout",
                    timed_out=True,
                )
                pending = None
                if executed_qty <= 0:
                    report.missed_entries += 1

            if event.event_type == "contractInfo":
                symbol = str(event.payload.get("s", ""))
                if symbol and symbol != self.symbol:
                    continue
                contract_status = str(event.payload.get("cs", ""))
                report.last_contract_status = contract_status
                report.last_contract_bracket_count = len(event.payload.get("bks") or [])
                continue

            if event.event_type == "crowdingSnapshot":
                latest_crowding_snapshot = event.crowding_snapshot or event.payload
                report.crowding_events += 1
                if latest_crowding_snapshot:
                    report.last_crowding_period = str(latest_crowding_snapshot.get("period", ""))
                continue

            if event.event_type == "bookTicker":
                if event.bid_price is None or event.ask_price is None:
                    continue
                self._update_latest_book(
                    report,
                    TopOfBookSnapshot(
                        event_time_ms=event.event_time_ms,
                        bid_price=event.bid_price,
                        bid_qty=event.bid_qty or Decimal("0"),
                        ask_price=event.ask_price,
                        ask_qty=event.ask_qty or Decimal("0"),
                    ),
                    event_time_ms=event.event_time_ms,
                )
                if pending is not None and pending.queue_state is not None:
                    self.book_fill_model.process_book_ticker(pending.queue_state, book=self._latest_book)
                    pending, position = self._synchronize_pending_queue_progress(
                        report,
                        pending=pending,
                        position=position,
                        event_time_ms=event.event_time_ms,
                        next_funding_time_ms=last_tick.next_funding_time_ms if last_tick is not None else 0,
                    )
                continue

            if event.event_type == "localDepthSnapshot":
                depth = DepthBookSnapshot.from_payload(event.payload)
                self._update_latest_depth(report, depth, event_time_ms=event.event_time_ms)
                effective_depth = self._effective_depth_snapshot()
                if pending is not None and pending.queue_state is not None and effective_depth is not None:
                    self.depth_fill_model.process_depth_snapshot(pending.queue_state, book=effective_depth)
                    pending, position = self._synchronize_pending_queue_progress(
                        report,
                        pending=pending,
                        position=position,
                        event_time_ms=event.event_time_ms,
                        next_funding_time_ms=last_tick.next_funding_time_ms if last_tick is not None else 0,
                    )
                continue

            if event.event_type == "localRpiDepthSnapshot":
                depth = DepthBookSnapshot.from_payload(event.payload)
                self._update_latest_rpi_depth(report, depth, event_time_ms=event.event_time_ms)
                effective_depth = self._effective_depth_snapshot()
                if pending is not None and pending.queue_state is not None and effective_depth is not None:
                    self.depth_fill_model.process_depth_snapshot(pending.queue_state, book=effective_depth)
                    pending, position = self._synchronize_pending_queue_progress(
                        report,
                        pending=pending,
                        position=position,
                        event_time_ms=event.event_time_ms,
                        next_funding_time_ms=last_tick.next_funding_time_ms if last_tick is not None else 0,
                    )
                continue

            if event.event_type == "aggTrade":
                if event.price is None or event.qty is None or event.buyer_is_market_maker is None:
                    continue
                self.model.on_agg_trade(
                    event_time_ms=event.event_time_ms,
                    price=event.price,
                    qty=event.qty,
                    buyer_is_market_maker=event.buyer_is_market_maker,
                )
                if pending is not None and pending.queue_state is not None:
                    self.book_fill_model.process_agg_trade(
                        pending.queue_state,
                        trade_price=event.price,
                        trade_qty=event.qty,
                        buyer_is_market_maker=event.buyer_is_market_maker,
                    )
                    pending, position = self._synchronize_pending_queue_progress(
                        report,
                        pending=pending,
                        position=position,
                        event_time_ms=event.event_time_ms,
                        next_funding_time_ms=last_tick.next_funding_time_ms if last_tick is not None else 0,
                    )
                continue

            if event.event_type != "markPriceUpdate" or event.price is None:
                continue

            tick = BacktestTick(
                event_time_ms=event.event_time_ms,
                price=event.price,
                funding_rate=event.funding_rate or Decimal("0"),
                next_funding_time_ms=event.next_funding_time_ms,
                moving_average_price=event.moving_average_price,
            )
            last_tick = tick
            report.ticks += 1
            closed_this_tick = False

            if position is not None:
                self._maybe_apply_funding(position, tick)
                exit_reason = self._check_exit(position, tick)
                if exit_reason is not None:
                    if pending is not None:
                        position, _ = self._cancel_pending_remainder(
                            report,
                            pending=pending,
                            position=position,
                            event_time_ms=tick.event_time_ms,
                            next_funding_time_ms=tick.next_funding_time_ms,
                            completion_reason="protective_exit",
                            timed_out=False,
                        )
                        pending = None
                    trade = self._close_position(position, tick, exit_reason, latest_book=self._latest_book, report=report)
                    self._record_closed_trade(report, trade)
                    running_equity += trade.net_pnl
                    equity_peak = max(equity_peak, running_equity)
                    report.max_drawdown = max(report.max_drawdown, equity_peak - running_equity)
                    report.equity_curve.append(running_equity)
                    position = None
                    closed_this_tick = True

            if not closed_this_tick and pending is not None:
                fill = self._simulate_entry_fill(pending, tick)
                if fill is not None:
                    filled_qty, fill_price = fill
                    position = self._materialize_entry_exposure(
                        pending=pending,
                        position=position,
                        cumulative_executed_qty=filled_qty,
                        event_time_ms=tick.event_time_ms,
                        fill_price=fill_price,
                        next_funding_time_ms=tick.next_funding_time_ms,
                    )
                    self._record_entry_queue_outcome(
                        report,
                        pending=pending,
                        completed_at_ms=tick.event_time_ms,
                        executed_qty=filled_qty,
                        timed_out=False,
                        completion_reason="full_fill",
                    )
                    pending = None

            if closed_this_tick or position is not None or pending is not None:
                continue

            evaluation = self.model.evaluate_price(
                event_time_ms=tick.event_time_ms,
                price=tick.price,
                funding_rate=tick.funding_rate,
            )
            self._apply_router_evaluation(report, evaluation)
            if evaluation.signal is None:
                if evaluation.rejection_reason:
                    report.signal_gate_rejections += 1
                continue

            if self.config.require_contract_trading_status and contract_status and contract_status != "TRADING":
                report.signal_gate_rejections += 1
                report.contract_gate_rejections += 1
                report.last_contract_status = contract_status
                continue

            book_rejection = self._book_gate_reason(event_time_ms=tick.event_time_ms)
            if book_rejection:
                report.signal_gate_rejections += 1
                report.book_gate_rejections += 1
                continue

            depth_rejection = self._depth_gate_reason(side=evaluation.signal.side, event_time_ms=tick.event_time_ms)
            if depth_rejection:
                report.signal_gate_rejections += 1
                report.depth_gate_rejections += 1
                continue

            crowding_score, crowding_rejection = self._evaluate_crowding_gate(
                side=evaluation.signal.side,
                snapshot=latest_crowding_snapshot,
                event_time_ms=tick.event_time_ms,
            )
            self._apply_crowding_report(report, crowding_score)
            if crowding_rejection:
                report.signal_gate_rejections += 1
                report.crowding_gate_rejections += 1
                continue

            build = self._build_pending_entry_from_signal(evaluation.signal, crowding_score=crowding_score)
            self._apply_entry_diagnostics(report, build)
            pending = build.pending
            self._apply_pending_rejection(report, build)

        if pending is not None:
            end_time_ms = max(last_event_time_ms, pending.submitted_at_ms)
            next_funding_time_ms = last_tick.next_funding_time_ms if last_tick is not None else 0
            position, executed_qty = self._cancel_pending_remainder(
                report,
                pending=pending,
                position=position,
                event_time_ms=end_time_ms,
                next_funding_time_ms=next_funding_time_ms,
                completion_reason="end_of_data",
                timed_out=False,
            )
            if executed_qty <= 0:
                report.missed_entries += 1
            pending = None

        if position is not None and last_tick is not None:
            terminal_tick = last_tick
            if last_event_time_ms > last_tick.event_time_ms:
                terminal_tick = BacktestTick(
                    event_time_ms=last_event_time_ms,
                    price=last_tick.price,
                    funding_rate=last_tick.funding_rate,
                    next_funding_time_ms=last_tick.next_funding_time_ms,
                    moving_average_price=last_tick.moving_average_price,
                )
            trade = self._close_position(position, terminal_tick, "end_of_data", latest_book=self._latest_book, report=report)
            self._record_closed_trade(report, trade)
            running_equity += trade.net_pnl
            equity_peak = max(equity_peak, running_equity)
            report.max_drawdown = max(report.max_drawdown, equity_peak - running_equity)
            report.equity_curve.append(running_equity)

        return report

    def _evaluate_crowding_gate(
        self,
        *,
        side: Side,
        snapshot: dict[str, object] | None,
        event_time_ms: int,
    ) -> tuple[CrowdingScore | None, str]:
        return evaluate_crowding_gate(
            side=side,
            snapshot=snapshot,
            config=self.crowding_gate_config,
            now_ms_value=event_time_ms,
        )

    @staticmethod
    def _apply_crowding_report(report: BacktestReport, score: CrowdingScore | None) -> None:
        if score is None:
            return
        report.last_crowding_side_score = score.side_score
        report.last_crowding_snapshot_age_ms = score.snapshot_age_ms
        report.last_crowding_period = score.period

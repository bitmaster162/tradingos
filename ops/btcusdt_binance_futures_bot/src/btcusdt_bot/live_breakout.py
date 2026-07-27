from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from dataclasses import dataclass, field
from decimal import Decimal

import websockets
from websockets.exceptions import ConnectionClosed

from btcusdt_bot.bootstrap.reconcile import BootstrapSynchronizer
from btcusdt_bot.collector.book_ticker import BookTickerCollector
from btcusdt_bot.collector.depth_book import DepthBookCollector, RPIDepthBookCollector
from btcusdt_bot.collector.crowding import CrowdingCollector
from btcusdt_bot.config import BotConfig
from btcusdt_bot.crowding.scoring import CrowdingGateConfig, CrowdingScore, evaluate_crowding_gate
from btcusdt_bot.connectors.rest_client import BinanceRESTClient
from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.connectors.ws_urls import build_combined_stream_url
from btcusdt_bot.domain.enums import AlgoStatus, OrderStatus, PositionSide, Side
from btcusdt_bot.domain.models import AlgoOrderProposal, OrderProposal
from btcusdt_bot.execution.gateway import ExecutionGateway, GatewayResult
from btcusdt_bot.execution.planner import ExecutionPlanner
from btcusdt_bot.heartbeat_daemon import CountdownHeartbeatDaemon
from btcusdt_bot.live_execution_quality import build_live_execution_quality_report
from btcusdt_bot.monitoring.execution_drift import ExecutionDriftDecision
from btcusdt_bot.monitoring.intraday_protection import IntradayProtectionDecision
from btcusdt_bot.monitoring.pnl_protection import PnLProtectionDecision
from btcusdt_bot.monitoring.combined_protection import CombinedProtectionDecision
from btcusdt_bot.monitoring.trade_reconciliation import TradeReconciliationDecision
from btcusdt_bot.monitoring.session_truth import SessionTruthDecision
from btcusdt_bot.monitoring.session_truth_trend import SessionTruthTrendDecision
from btcusdt_bot.monitoring.economics_regime import EconomicsRegimeDecision
from btcusdt_bot.private.consumer import PrivateStreamConsumer
from btcusdt_bot.reconcile_daemon import ReconcileDaemon
from btcusdt_bot.risk.engine import RiskContext, RiskLimits
from btcusdt_bot.state.store import StateStore
from btcusdt_bot.simulator.depth_book import DepthBookPassiveFillModel, DepthBookSnapshot
from btcusdt_bot.simulator.depth_liquidity import DepthLiquidityConfig, DepthLiquidityPolicy
from btcusdt_bot.simulator.queue_calibration import EntryQueueCalibrationModel, EntryQueueExpectation
from btcusdt_bot.simulator.queue_admission import (
    QueueAdmissionConfig,
    QueueAdmissionDecision,
    QueueAdmissionInputs,
    QueueAdmissionPolicy,
)
from btcusdt_bot.simulator.top_of_book import TopOfBookPassiveFillModel, TopOfBookSnapshot
from btcusdt_bot.sizing.economics_feedback import EconomicsFeedbackConfig, EconomicsFeedbackDecision, EconomicsFeedbackPolicy
from btcusdt_bot.sizing.policy import AdaptiveEntryInputs, AdaptiveEntryPolicy, AdaptiveEntryPolicyConfig
from btcusdt_bot.sizing.volatility import VolatilitySizingConfig, VolatilitySizingDecision, VolatilitySizingInputs, VolatilitySizingPolicy
from btcusdt_bot.storage.jsonl import JSONLWriter
from btcusdt_bot.reporting.economics_dashboard import EconomicsDashboard
from btcusdt_bot.strategies import (
    RollingBreakoutModel,
    RollingReversionModel,
    SignalContext,
    SignalEvaluation,
    StrategyModelConfig,
    StrategySignal as BreakoutSignal,
    build_strategy_model,
)
from btcusdt_bot.ws.messages import decode_ws_message


TERMINAL_ORDER_STATUSES = {
    OrderStatus.FILLED,
    OrderStatus.CANCELED,
    OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
    OrderStatus.EXPIRED_IN_MATCH,
}
TERMINAL_ALGO_STATUSES = {
    AlgoStatus.CANCELED,
    AlgoStatus.FINISHED,
    AlgoStatus.REJECTED,
    AlgoStatus.EXPIRED,
}


@dataclass(slots=True)
class LiveBreakoutConfig:
    strategy_kind: str = "breakout"
    lookback_ticks: int = 120
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
    hold_seconds: int = 300
    cooldown_after_cancel_seconds: int = 3
    position_notional_usdt: Decimal = Decimal("100")
    stop_atr_multiple: Decimal = Decimal("1.0")
    take_profit_atr_multiple: Decimal = Decimal("1.5")
    reconcile_interval_seconds: float = 30.0
    max_reconcile_staleness_ms: int | None = None
    trade_flow_window_seconds: int = 10
    min_recent_agg_trades: int = 0
    min_flow_imbalance: Decimal = Decimal("0")
    max_mark_trade_divergence_bps: Decimal | None = None
    max_positive_funding_rate: Decimal | None = None
    min_negative_funding_rate: Decimal | None = None
    require_contract_trading_status: bool = True
    with_private_consumer: bool = True
    with_reconcile_daemon: bool = True
    with_crowding_collector: bool = True
    with_book_ticker_collector: bool = True
    with_depth_book_collector: bool = True
    with_rpi_depth_book_collector: bool = False
    use_rpi_depth_if_available: bool = True
    heal_on_reconcile_divergence: bool = False
    targeted_heal_on_reconcile_divergence: bool = False
    crowding_period: str = "5m"
    crowding_interval_seconds: float = 30.0
    max_crowding_snapshot_age_seconds: int | None = None
    min_crowding_score: Decimal | None = None
    crowding_oi_expansion_weight: Decimal = Decimal("0.5")
    max_book_spread_bps: Decimal | None = None
    max_book_ticker_staleness_ms: int | None = None
    max_depth_snapshot_staleness_ms: int | None = None
    min_depth_imbalance: Decimal | None = None
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
    execution_drift_guard_path: str | None = None
    intraday_protection_guard_path: str | None = None
    pnl_protection_guard_path: str | None = None
    trade_reconciliation_guard_path: str | None = None
    session_truth_guard_path: str | None = None
    session_truth_trend_guard_path: str | None = None
    economics_regime_guard_path: str | None = None
    economics_dashboard_path: str | None = None
    economics_feedback_enabled: bool = True
    economics_feedback_min_active_day_count: int = 3
    economics_feedback_min_multiplier: Decimal = Decimal("0.70")
    combined_protection_guard_path: str | None = None
    send_orders: bool = False
    test_orders: bool = False


@dataclass(slots=True)
class ActiveEntry:
    client_id: str
    side: Side
    qty: Decimal
    proposed_price: Decimal | None
    submitted_at_ms: int
    atr: Decimal
    target_notional_usdt: Decimal = Decimal("0")
    sizing_multiplier: Decimal = Decimal("1")
    strategy_kind: str = ""
    selected_strategy_kind: str = ""
    protected_qty: Decimal = Decimal("0")
    algo_client_ids: set[str] = field(default_factory=set)
    pending_exchange_confirmation: bool = False
    queue_expectation: EntryQueueExpectation | None = None
    entry_outcome_recorded: bool = False


@dataclass(slots=True)
class LiveBreakoutStatus:
    strategy_kind: str = ""
    market_messages: int = 0
    actions_emitted: int = 0
    entry_attempts: int = 0
    entries_sent: int = 0
    entries_rejected: int = 0
    entry_unknown_submissions: int = 0
    stale_cancels: int = 0
    exit_brackets_armed: int = 0
    exit_cancels: int = 0
    targeted_queries: int = 0
    signal_gate_rejections: int = 0
    contract_gated_signals: int = 0
    crowding_gate_rejections: int = 0
    book_gate_rejections: int = 0
    depth_gate_rejections: int = 0
    queue_gate_rejections: int = 0
    depth_liquidity_gate_rejections: int = 0
    sizing_abstentions: int = 0
    reconnects: int = 0
    last_mark_price: Decimal | None = None
    last_event_time_ms: int = 0
    last_signal_side: str = ""
    last_router_regime: str = ""
    last_router_selected_strategy_kind: str = ""
    last_router_preferred_strategy_kind: str = ""
    router_breakout_signal_count: int = 0
    router_reversion_signal_count: int = 0
    router_fallback_signal_count: int = 0
    last_ensemble_regime: str = ""
    last_ensemble_selected_strategy_kind: str = ""
    last_ensemble_preferred_strategy_kind: str = ""
    last_ensemble_breakout_score: str = ""
    last_ensemble_reversion_score: str = ""
    ensemble_breakout_signal_count: int = 0
    ensemble_reversion_signal_count: int = 0
    ensemble_override_signal_count: int = 0
    last_action_path: str = ""
    last_error: str = ""
    active_entry_client_id: str = ""
    last_gate_reason: str = ""
    last_flow_imbalance: str = ""
    last_recent_trade_count: int = 0
    last_funding_rate: str = ""
    last_mark_trade_divergence_bps: str = ""
    last_contract_status: str = ""
    last_contract_bracket_count: int = 0
    last_crowding_side_score: str = ""
    last_crowding_penalty: str = ""
    last_crowding_snapshot_age_ms: int = 0
    last_crowding_global_ratio: str = ""
    last_crowding_taker_ratio: str = ""
    last_crowding_period: str = ""
    last_book_bid: str = ""
    last_book_ask: str = ""
    last_book_spread_bps: str = ""
    last_book_age_ms: int = 0
    last_depth_imbalance: str = ""
    last_depth_age_ms: int = 0
    last_depth_levels: int = 0
    last_depth_source: str = ""
    last_rpi_depth_age_ms: int = 0
    last_rpi_depth_levels: int = 0
    last_notional_multiplier: str = ""
    last_target_notional_usdt: str = ""
    last_volatility_multiplier: str = ""
    last_atr_fraction_bps: str = ""
    last_expected_fill_ratio: str = ""
    last_expected_queue_clear_seconds: str = ""
    last_queue_ahead_ratio: str = ""
    last_directional_queue_flow_qty_per_second: str = ""
    last_exit_depth_coverage_ratio: str = ""
    last_exit_depth_sweep_bps: str = ""
    last_exit_depth_levels_consumed: int = 0
    last_exit_synthetic_tail_coverage_ratio: str = ""
    last_exit_synthetic_tail_levels_consumed: int = 0
    last_exit_terminal_tail_ratio: str = ""
    execution_drift_observe_rejections: int = 0
    execution_drift_reduce_size_applications: int = 0
    intraday_protection_observe_rejections: int = 0
    intraday_protection_reduce_size_applications: int = 0
    pnl_protection_observe_rejections: int = 0
    pnl_protection_reduce_size_applications: int = 0
    trade_reconciliation_observe_rejections: int = 0
    trade_reconciliation_reduce_size_applications: int = 0
    session_truth_observe_rejections: int = 0
    session_truth_reduce_size_applications: int = 0
    session_truth_trend_observe_rejections: int = 0
    session_truth_trend_reduce_size_applications: int = 0
    economics_regime_observe_rejections: int = 0
    economics_regime_reduce_size_applications: int = 0
    combined_protection_observe_rejections: int = 0
    combined_protection_reduce_size_applications: int = 0
    last_execution_drift_action: str = ""
    last_execution_drift_size_multiplier: str = ""
    last_intraday_protection_action: str = ""
    last_intraday_protection_size_multiplier: str = ""
    last_pnl_protection_action: str = ""
    last_pnl_protection_size_multiplier: str = ""
    last_trade_reconciliation_action: str = ""
    last_trade_reconciliation_size_multiplier: str = ""
    last_trade_reconciliation_window_mode: str = ""
    last_trade_reconciliation_session_started_at_ms: int = 0
    last_trade_reconciliation_missing_local_trade_ratio: str = ""
    last_trade_reconciliation_missing_local_order_ratio: str = ""
    last_trade_reconciliation_realized_pnl_diff_usdt: str = ""
    last_trade_reconciliation_commission_abs_diff_usdt: str = ""
    last_trade_reconciliation_quote_qty_abs_diff_usdt: str = ""
    last_trade_reconciliation_income_trade_link_gap_ratio: str = ""
    last_session_truth_action: str = ""
    last_session_truth_size_multiplier: str = ""
    last_session_truth_window_mode: str = ""
    last_session_truth_session_started_at_ms: int = 0
    last_session_truth_net_realized_pnl_usdt: str = ""
    last_session_truth_net_realized_bps: str = ""
    last_session_truth_maker_ratio: str = ""
    last_session_truth_trend_action: str = ""
    last_session_truth_trend_size_multiplier: str = ""
    last_session_truth_trend_active_bucket_count: int = 0
    last_session_truth_trend_negative_bucket_ratio: str = ""
    last_session_truth_trend_trailing_negative_bucket_streak: int = 0
    last_session_truth_trend_recent_bucket_net_realized_bps: str = ""
    last_session_truth_trend_cumulative_drawdown_usdt: str = ""
    last_economics_regime_action: str = ""
    last_economics_regime_size_multiplier: str = ""
    last_economics_regime_negative_day_ratio: str = ""
    last_economics_regime_recent_day_net_realized_bps: str = ""
    last_economics_regime_average_maker_ratio: str = ""
    last_economics_feedback_multiplier: str = ""
    last_economics_feedback_total_penalty: str = ""
    last_economics_feedback_reason: str = ""
    last_combined_protection_action: str = ""
    last_combined_protection_size_multiplier: str = ""
    last_combined_protection_cooldown_until_ms: int = 0
    last_pnl_session_loss_usdt: str = ""
    last_pnl_drawdown_usdt: str = ""
    last_pnl_unrealized_loss_usdt: str = ""
    entry_outcome_count: int = 0
    entry_timeout_count: int = 0
    last_actual_entry_fill_ratio: str = ""
    last_entry_fill_latency_seconds: str = ""
    last_entry_fill_ratio_shortfall: str = ""
    last_entry_fill_latency_overshoot_seconds: str = ""
    realized_entry_fill_ratio_sum: Decimal = Decimal("0")
    entry_fill_ratio_shortfall_sum: Decimal = Decimal("0")
    entry_fill_ratio_shortfall_count: int = 0
    entry_fill_latency_seconds_sum: Decimal = Decimal("0")
    entry_fill_latency_count: int = 0
    entry_fill_latency_overshoot_seconds_sum: Decimal = Decimal("0")
    entry_fill_latency_overshoot_count: int = 0
    session_started_at_ms: int = 0
    session_last_update_at_ms: int = 0
    notional_decision_count: int = 0
    target_notional_sum: Decimal = Decimal("0")
    notional_multiplier_sum: Decimal = Decimal("0")
    volatility_decision_count: int = 0
    volatility_multiplier_sum: Decimal = Decimal("0")
    economics_feedback_decision_count: int = 0
    economics_feedback_multiplier_sum: Decimal = Decimal("0")
    queue_decision_count: int = 0
    expected_fill_ratio_sum: Decimal = Decimal("0")
    queue_clear_seconds_sum: Decimal = Decimal("0")
    queue_clear_seconds_count: int = 0
    queue_ahead_ratio_sum: Decimal = Decimal("0")
    queue_ahead_ratio_count: int = 0
    directional_queue_flow_rate_sum: Decimal = Decimal("0")
    exit_depth_estimate_count: int = 0
    exit_depth_sweep_bps_sum: Decimal = Decimal("0")
    exit_depth_coverage_ratio_sum: Decimal = Decimal("0")
    exit_depth_levels_consumed_sum: Decimal = Decimal("0")
    exit_synthetic_tail_coverage_ratio_sum: Decimal = Decimal("0")
    exit_synthetic_tail_levels_consumed_sum: Decimal = Decimal("0")
    exit_terminal_tail_ratio_sum: Decimal = Decimal("0")


class LiveBreakoutRunner:
    def __init__(
        self,
        config: BotConfig,
        *,
        client: BinanceRESTClient,
        store: StateStore,
        writer: JSONLWriter,
        gateway: ExecutionGateway,
        live_config: LiveBreakoutConfig,
        planner: ExecutionPlanner | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.store = store
        self.writer = writer
        self.gateway = gateway
        self.live_config = live_config
        self.planner = planner or ExecutionPlanner()
        self.logger = logger or logging.getLogger("btcusdt_bot.live_breakout")
        self.status = LiveBreakoutStatus(
            strategy_kind=live_config.strategy_kind,
            session_started_at_ms=now_ms(),
            session_last_update_at_ms=now_ms(),
        )
        self.model = build_strategy_model(
            StrategyModelConfig(
                strategy_kind=live_config.strategy_kind,
                lookback_ticks=live_config.lookback_ticks,
                atr_window_ticks=live_config.atr_window_ticks,
                trade_flow_window_seconds=live_config.trade_flow_window_seconds,
                min_recent_agg_trades=live_config.min_recent_agg_trades,
                min_flow_imbalance=live_config.min_flow_imbalance,
                max_mark_trade_divergence_bps=live_config.max_mark_trade_divergence_bps,
                max_positive_funding_rate=live_config.max_positive_funding_rate,
                min_negative_funding_rate=live_config.min_negative_funding_rate,
                reversion_lookback_ticks=live_config.reversion_lookback_ticks,
                reversion_entry_atr_multiple=live_config.reversion_entry_atr_multiple,
                reversion_max_atr_fraction=live_config.reversion_max_atr_fraction,
                reversion_min_flow_flip=live_config.reversion_min_flow_flip,
                router_range_max_atr_fraction=live_config.router_range_max_atr_fraction,
                router_trend_min_atr_fraction=live_config.router_trend_min_atr_fraction,
                router_trend_min_abs_flow_imbalance=live_config.router_trend_min_abs_flow_imbalance,
                router_range_max_abs_flow_imbalance=live_config.router_range_max_abs_flow_imbalance,
                router_neutral_preference=live_config.router_neutral_preference,
                router_opportunistic_fallback=live_config.router_opportunistic_fallback,
            )
        )
        self.crowding_gate_config = CrowdingGateConfig(
            enabled=(
                live_config.max_crowding_snapshot_age_seconds is not None
                or live_config.min_crowding_score is not None
            ),
            max_snapshot_age_ms=(
                None
                if live_config.max_crowding_snapshot_age_seconds is None
                else live_config.max_crowding_snapshot_age_seconds * 1000
            ),
            min_side_score=live_config.min_crowding_score,
            oi_expansion_weight=live_config.crowding_oi_expansion_weight,
        )
        self.adaptive_entry_policy = AdaptiveEntryPolicy(
            AdaptiveEntryPolicyConfig(
                enabled=True,
                min_notional_multiplier=live_config.min_notional_multiplier,
                max_notional_multiplier=live_config.max_notional_multiplier,
                abstain_below_multiplier=live_config.abstain_below_multiplier,
                min_effective_notional_usdt=live_config.min_effective_notional_usdt,
                flow_weight=live_config.sizing_flow_weight,
                crowding_weight=live_config.sizing_crowding_weight,
                divergence_penalty_weight=live_config.sizing_divergence_penalty_weight,
                funding_penalty_weight=live_config.sizing_funding_penalty_weight,
                divergence_penalty_cap_bps=live_config.sizing_divergence_penalty_cap_bps,
                funding_penalty_cap_rate=live_config.sizing_funding_penalty_cap_rate,
            )
        )
        self.volatility_sizing_policy = VolatilitySizingPolicy(
            VolatilitySizingConfig(
                enabled=live_config.volatility_target_atr_fraction is not None,
                target_atr_fraction=live_config.volatility_target_atr_fraction or Decimal("0.0020"),
                min_notional_multiplier=live_config.volatility_min_notional_multiplier,
                max_notional_multiplier=live_config.volatility_max_notional_multiplier,
                abstain_above_atr_fraction=live_config.volatility_abstain_above_atr_fraction,
            )
        )
        self.economics_feedback_policy = EconomicsFeedbackPolicy(
            EconomicsFeedbackConfig(
                enabled=live_config.economics_feedback_enabled,
                min_active_day_count=live_config.economics_feedback_min_active_day_count,
                min_multiplier=live_config.economics_feedback_min_multiplier,
            )
        )
        self.queue_admission_policy = QueueAdmissionPolicy(
            QueueAdmissionConfig(
                enabled=(
                    live_config.min_expected_fill_ratio is not None
                    or live_config.max_expected_queue_clear_seconds is not None
                    or live_config.max_queue_ahead_to_order_ratio is not None
                ),
                min_expected_fill_ratio=live_config.min_expected_fill_ratio or Decimal("0"),
                max_expected_queue_clear_seconds=live_config.max_expected_queue_clear_seconds,
                max_queue_ahead_to_order_ratio=live_config.max_queue_ahead_to_order_ratio,
                min_directional_flow_qty_per_second=live_config.min_directional_queue_flow_qty_per_second,
            )
        )
        self.depth_liquidity_policy = DepthLiquidityPolicy(
            DepthLiquidityConfig(
                enabled=(
                    live_config.min_exit_depth_coverage_ratio is not None
                    or live_config.max_exit_depth_sweep_bps is not None
                ),
                min_displayed_coverage_ratio=live_config.min_exit_depth_coverage_ratio,
                max_sweep_slippage_bps=live_config.max_exit_depth_sweep_bps,
                tail_penalty_bps=live_config.exit_depth_tail_penalty_bps,
                synthetic_tail_levels=live_config.synthetic_tail_levels,
                synthetic_tail_replenishment_ratio=live_config.synthetic_tail_replenishment_ratio,
                synthetic_tail_step_bps=live_config.synthetic_tail_step_bps,
            )
        )
        self.book_fill_model = TopOfBookPassiveFillModel()
        self.depth_fill_model = DepthBookPassiveFillModel()
        self.queue_calibration_model = EntryQueueCalibrationModel()
        self.risk_limits = RiskLimits(
            max_leverage=Decimal(str(config.max_leverage)),
            max_position_notional=Decimal(str(config.max_position_notional_usdt)),
            max_daily_loss=Decimal(str(config.max_daily_loss_usdt)),
            max_normal_open_orders=config.max_normal_open_orders,
            max_algo_open_orders=config.max_algo_open_orders,
            stale_data_limit_ms=config.stale_data_limit_ms,
            stale_reconcile_limit_ms=live_config.max_reconcile_staleness_ms,
        )
        self.active_entry: ActiveEntry | None = None
        self.cooldown_until_ms: int = 0

    async def run(self, *, stop_after_messages: int | None = None) -> LiveBreakoutStatus:
        if self.live_config.send_orders and not self.config.has_api_credentials:
            raise ValueError("send_orders=True requires BINANCE_API_KEY and BINANCE_API_SECRET")

        symbol_streams = [
            f"{self.config.symbol.lower()}@markPrice@1s",
            f"{self.config.symbol.lower()}@aggTrade",
        ]
        if self.config.enable_contract_info_stream:
            symbol_streams.append("!contractInfo")
        url = build_combined_stream_url(self.config.ws_market_base_url, symbol_streams)
        tasks: list[asyncio.Task[object]] = []
        try:
            if self.live_config.with_book_ticker_collector:
                book_ticker_collector = BookTickerCollector(
                    self.config,
                    writer=self.writer,
                    store=self.store,
                )
                tasks.append(asyncio.create_task(book_ticker_collector.run()))
            if self.live_config.with_depth_book_collector:
                depth_book_collector = DepthBookCollector(
                    self.config,
                    client=self.client,
                    writer=self.writer,
                    store=self.store,
                )
                tasks.append(asyncio.create_task(depth_book_collector.run()))
            if self.live_config.with_rpi_depth_book_collector:
                rpi_depth_book_collector = RPIDepthBookCollector(
                    self.config,
                    client=self.client,
                    writer=self.writer,
                    store=self.store,
                )
                tasks.append(asyncio.create_task(rpi_depth_book_collector.run()))
            if self.live_config.with_crowding_collector:
                crowding_collector = CrowdingCollector(
                    self.config,
                    client=self.client,
                    writer=self.writer,
                    store=self.store,
                )
                tasks.append(
                    asyncio.create_task(
                        crowding_collector.run(
                            period=self.live_config.crowding_period,
                            interval_seconds=self.live_config.crowding_interval_seconds,
                        )
                    )
                )
            if self.config.has_api_credentials:
                await asyncio.to_thread(self._bootstrap_shared_state)
                if self.live_config.with_private_consumer:
                    consumer = PrivateStreamConsumer(
                        self.config,
                        client=self.client,
                        store=self.store,
                        writer=self.writer,
                    )
                    tasks.append(asyncio.create_task(consumer.run()))
                if self.live_config.with_reconcile_daemon:
                    daemon = ReconcileDaemon(
                        self.config,
                        client=self.client,
                        store=self.store,
                        writer=self.writer,
                    )
                    tasks.append(
                        asyncio.create_task(
                            daemon.run(
                                interval_seconds=self.live_config.reconcile_interval_seconds,
                                heal_on_divergence=self.live_config.heal_on_reconcile_divergence,
                                targeted_heal_on_divergence=self.live_config.targeted_heal_on_reconcile_divergence,
                            )
                        )
                    )
                if self.config.enable_countdown_heartbeat and self.live_config.send_orders and not self.live_config.test_orders:
                    heartbeat = CountdownHeartbeatDaemon(
                        self.config,
                        gateway=self.gateway,
                        writer=self.writer,
                    )
                    tasks.append(
                        asyncio.create_task(
                            heartbeat.run(
                                interval_seconds=max(1.0, self.config.heartbeat_interval_ms / 1000),
                                send=True,
                            )
                        )
                    )

            backoff_s = max(0.2, self.config.reconnect_initial_backoff_ms / 1000)
            max_backoff_s = max(backoff_s, self.config.reconnect_max_backoff_ms / 1000)

            while True:
                try:
                    async with websockets.connect(
                        url,
                        ping_interval=None,
                        max_size=None,
                        open_timeout=self.config.timeout_s,
                    ) as websocket:
                        self.logger.info("live breakout connected", extra={"url": url})
                        backoff_s = max(0.2, self.config.reconnect_initial_backoff_ms / 1000)
                        async for raw_message in websocket:
                            ws_message = decode_ws_message(raw_message)
                            payload = ws_message.payload
                            event_type = str(payload.get("e", ""))
                            self.status.market_messages += 1
                            if event_type == "aggTrade":
                                trade_time_ms = int(payload.get("T", payload.get("E", now_ms())))
                                await self._on_agg_trade(
                                    event_time_ms=trade_time_ms,
                                    price=Decimal(str(payload["p"])),
                                    qty=Decimal(str(payload.get("nq", payload.get("q", "0")))),
                                    buyer_is_market_maker=bool(payload.get("m", False)),
                                )
                            elif event_type == "markPriceUpdate":
                                price = Decimal(str(payload["p"]))
                                funding_rate = Decimal(str(payload.get("r", "0")))
                                event_time_ms = int(payload.get("E", now_ms()))
                                self.status.last_mark_price = price
                                self.status.last_event_time_ms = event_time_ms
                                await self._on_mark_price_tick(
                                    event_time_ms=event_time_ms,
                                    mark_price=price,
                                    funding_rate=funding_rate,
                                    received_at_ms=now_ms(),
                                )
                            elif event_type == "contractInfo":
                                await self._on_contract_info(payload)
                            if stop_after_messages is not None and self.status.market_messages >= stop_after_messages:
                                await websocket.close()
                                self._flush_status()
                                return self.status
                except asyncio.CancelledError:
                    raise
                except ConnectionClosed as exc:
                    self.status.last_error = f"connection_closed code={exc.code} reason={exc.reason}"
                    self.logger.warning("live breakout connection closed: %s", self.status.last_error)
                except Exception as exc:  # noqa: BLE001
                    self.status.last_error = str(exc)
                    self.logger.exception("live breakout loop error")

                self.status.reconnects += 1
                await asyncio.sleep(backoff_s)
                backoff_s = min(max_backoff_s, backoff_s * 2)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self.status.session_last_update_at_ms = now_ms()
            final_report = build_live_execution_quality_report(self.status)
            self.writer.append_record(
                "reports",
                f"{self.config.symbol.lower()}_live_execution_quality",
                {"report": final_report, "status": self.status},
                event_time_ms=self.status.session_last_update_at_ms,
            )
            self._flush_status()

    async def _on_agg_trade(
        self,
        *,
        event_time_ms: int,
        price: Decimal,
        qty: Decimal,
        buyer_is_market_maker: bool,
    ) -> None:
        context = self.model.on_agg_trade(
            event_time_ms=event_time_ms,
            price=price,
            qty=qty,
            buyer_is_market_maker=buyer_is_market_maker,
        )
        self._apply_signal_context(context=context, rejection_reason="")

    async def _on_contract_info(self, payload: dict[str, object]) -> None:
        symbol = str(payload.get("s", ""))
        if symbol and symbol != self.config.symbol:
            return
        self.store.patch_contract_info(payload)
        self.status.last_contract_status = str(payload.get("cs", ""))
        self.status.last_contract_bracket_count = len(payload.get("bks") or [])
        self._flush_status()

    async def _on_mark_price_tick(
        self,
        *,
        event_time_ms: int,
        mark_price: Decimal,
        funding_rate: Decimal | None = None,
        received_at_ms: int | None = None,
    ) -> None:
        await self._maybe_cleanup_flat_position(mark_price=mark_price, event_time_ms=event_time_ms)
        await self._maybe_cancel_stale_entry(event_time_ms=event_time_ms)
        await self._maybe_arm_exits(mark_price=mark_price, event_time_ms=event_time_ms)
        self._apply_book_status(event_time_ms=event_time_ms)
        self._apply_depth_status(event_time_ms=event_time_ms)

        if event_time_ms < self.cooldown_until_ms:
            return

        evaluation = self.model.evaluate_price(
            event_time_ms=event_time_ms,
            price=mark_price,
            funding_rate=funding_rate,
        )
        self._apply_router_evaluation(evaluation)
        self._apply_signal_context(context=evaluation.context, rejection_reason=evaluation.rejection_reason)
        if evaluation.signal is None:
            if evaluation.rejection_reason:
                self.status.signal_gate_rejections += 1
            return
        signal = evaluation.signal
        self.status.last_signal_side = signal.side

        contract_rejection = self._contract_gate_reason()
        if contract_rejection:
            self.status.signal_gate_rejections += 1
            self.status.contract_gated_signals += 1
            self.status.last_gate_reason = contract_rejection
            return

        book_rejection = self._book_gate_reason(event_time_ms=event_time_ms)
        if book_rejection:
            self.status.signal_gate_rejections += 1
            self.status.book_gate_rejections += 1
            self.status.last_gate_reason = book_rejection
            return

        depth_rejection = self._depth_gate_reason(side=signal.side, event_time_ms=event_time_ms)
        if depth_rejection:
            self.status.signal_gate_rejections += 1
            self.status.depth_gate_rejections += 1
            self.status.last_gate_reason = depth_rejection
            return

        crowding_score, crowding_rejection = self._evaluate_crowding_gate(
            side=signal.side,
            event_time_ms=event_time_ms,
        )
        self._apply_crowding_status(crowding_score)
        if crowding_rejection:
            self.status.signal_gate_rejections += 1
            self.status.crowding_gate_rejections += 1
            self.status.last_gate_reason = crowding_rejection
            return

        execution_drift_decision = self._load_execution_drift_decision()
        intraday_protection_decision = self._load_intraday_protection_decision()
        pnl_protection_decision = self._load_pnl_protection_decision()
        trade_reconciliation_decision = self._load_trade_reconciliation_decision()
        session_truth_decision = self._load_session_truth_decision()
        session_truth_trend_decision = self._load_session_truth_trend_decision()
        economics_regime_decision = self._load_economics_regime_decision()
        combined_protection_decision = self._load_combined_protection_decision()
        economics_dashboard = self._load_economics_dashboard()
        economics_feedback_decision = self._evaluate_economics_feedback(
            economics_dashboard=economics_dashboard,
            economics_regime_decision=economics_regime_decision,
        )

        observe_payload: dict[str, object] = {"signal": signal}
        observe_reasons: list[str] = []
        if execution_drift_decision is not None and execution_drift_decision.observe_only:
            self.status.execution_drift_observe_rejections += 1
            observe_payload["execution_drift_decision"] = execution_drift_decision
            observe_reasons.append("execution_drift_observe_only")
        if intraday_protection_decision is not None and intraday_protection_decision.observe_only:
            self.status.intraday_protection_observe_rejections += 1
            observe_payload["intraday_protection_decision"] = intraday_protection_decision
            observe_reasons.append("intraday_protection_observe_only")
        if pnl_protection_decision is not None and pnl_protection_decision.observe_only:
            self.status.pnl_protection_observe_rejections += 1
            observe_payload["pnl_protection_decision"] = pnl_protection_decision
            observe_reasons.append("pnl_protection_observe_only")
        if trade_reconciliation_decision is not None and trade_reconciliation_decision.observe_only:
            self.status.trade_reconciliation_observe_rejections += 1
            observe_payload["trade_reconciliation_decision"] = trade_reconciliation_decision
            observe_reasons.append("trade_reconciliation_observe_only")
        if session_truth_decision is not None and session_truth_decision.observe_only:
            self.status.session_truth_observe_rejections += 1
            observe_payload["session_truth_decision"] = session_truth_decision
            observe_reasons.append("session_truth_observe_only")
        if session_truth_trend_decision is not None and session_truth_trend_decision.observe_only:
            self.status.session_truth_trend_observe_rejections += 1
            observe_payload["session_truth_trend_decision"] = session_truth_trend_decision
            observe_reasons.append("session_truth_trend_observe_only")
        if (
            combined_protection_decision is None
            and economics_regime_decision is not None
            and economics_regime_decision.observe_only
        ):
            self.status.economics_regime_observe_rejections += 1
            observe_payload["economics_regime_decision"] = economics_regime_decision
            observe_reasons.append("economics_regime_observe_only")
        if combined_protection_decision is not None and combined_protection_decision.observe_only:
            self.status.combined_protection_observe_rejections += 1
            observe_payload["combined_protection_decision"] = combined_protection_decision
            observe_reasons.append("combined_protection_observe_only")
        if observe_reasons:
            self.status.signal_gate_rejections += 1
            self.status.last_gate_reason = (
                observe_reasons[0] if len(observe_reasons) == 1 else "combined_guard_observe_only"
            )
            self._record_action(
                self.status.last_gate_reason,
                observe_payload,
                event_time_ms=event_time_ms,
            )
            return

        if self.active_entry is not None:
            return
        if self._current_position_qty() != 0:
            return
        if self.store.state.open_normal_orders > 0:
            return

        adaptive_sizing = self.adaptive_entry_policy.evaluate(
            AdaptiveEntryInputs(
                side=signal.side,
                base_notional_usdt=self.live_config.position_notional_usdt,
                flow_imbalance=signal.context.flow_imbalance if signal.context is not None else None,
                crowding_side_score=crowding_score.side_score if crowding_score is not None else None,
                funding_rate=funding_rate,
                mark_trade_divergence_bps=(
                    signal.context.mark_trade_divergence_bps if signal.context is not None else None
                ),
            )
        )
        if not adaptive_sizing.execute:
            self._record_notional_decision(
                target_notional_usdt=adaptive_sizing.target_notional_usdt,
                multiplier=adaptive_sizing.multiplier,
            )
            self.status.last_notional_multiplier = str(adaptive_sizing.multiplier)
            self.status.last_target_notional_usdt = str(adaptive_sizing.target_notional_usdt)
            self.status.signal_gate_rejections += 1
            self.status.sizing_abstentions += 1
            self.status.last_gate_reason = adaptive_sizing.reason
            self._record_action(
                "adaptive_abstain",
                {"signal": signal, "adaptive_sizing": adaptive_sizing},
                event_time_ms=event_time_ms,
            )
            return

        volatility_sizing = self.volatility_sizing_policy.evaluate(
            VolatilitySizingInputs(
                base_notional_usdt=adaptive_sizing.target_notional_usdt,
                atr=signal.atr,
                reference_price=mark_price,
            )
        )
        self._apply_volatility_status(volatility_sizing)
        if not volatility_sizing.execute:
            self._record_notional_decision(
                target_notional_usdt=volatility_sizing.target_notional_usdt,
                multiplier=adaptive_sizing.multiplier * volatility_sizing.multiplier,
            )
            self.status.last_notional_multiplier = str(adaptive_sizing.multiplier * volatility_sizing.multiplier)
            self.status.last_target_notional_usdt = str(volatility_sizing.target_notional_usdt)
            self.status.signal_gate_rejections += 1
            self.status.sizing_abstentions += 1
            self.status.last_gate_reason = volatility_sizing.reason
            self._record_action(
                "volatility_abstain",
                {
                    "signal": signal,
                    "adaptive_sizing": adaptive_sizing,
                    "volatility_sizing": volatility_sizing,
                    "execution_drift_decision": execution_drift_decision,
                    "pnl_protection_decision": pnl_protection_decision,
                    "trade_reconciliation_decision": trade_reconciliation_decision,
                    "session_truth_decision": session_truth_decision,
                    "session_truth_trend_decision": session_truth_trend_decision,
                    "combined_protection_decision": combined_protection_decision,
                },
                event_time_ms=event_time_ms,
            )
            return

        execution_drift_multiplier = Decimal("1")
        intraday_protection_multiplier = Decimal("1")
        pnl_protection_multiplier = Decimal("1")
        trade_reconciliation_multiplier = Decimal("1")
        session_truth_multiplier = Decimal("1")
        session_truth_trend_multiplier = Decimal("1")
        economics_regime_multiplier = Decimal("1")
        economics_feedback_multiplier = economics_feedback_decision.multiplier
        combined_protection_multiplier = Decimal("1")
        if execution_drift_decision is not None and execution_drift_decision.reduce_size:
            execution_drift_multiplier = execution_drift_decision.size_multiplier
            self.status.execution_drift_reduce_size_applications += 1
        if intraday_protection_decision is not None and intraday_protection_decision.reduce_size:
            intraday_protection_multiplier = intraday_protection_decision.size_multiplier
            self.status.intraday_protection_reduce_size_applications += 1
        if pnl_protection_decision is not None and pnl_protection_decision.reduce_size:
            pnl_protection_multiplier = pnl_protection_decision.size_multiplier
            self.status.pnl_protection_reduce_size_applications += 1
        if trade_reconciliation_decision is not None and trade_reconciliation_decision.reduce_size:
            trade_reconciliation_multiplier = trade_reconciliation_decision.size_multiplier
            self.status.trade_reconciliation_reduce_size_applications += 1
        if session_truth_decision is not None and session_truth_decision.reduce_size:
            session_truth_multiplier = session_truth_decision.size_multiplier
            self.status.session_truth_reduce_size_applications += 1
        if session_truth_trend_decision is not None and session_truth_trend_decision.reduce_size:
            session_truth_trend_multiplier = session_truth_trend_decision.size_multiplier
            self.status.session_truth_trend_reduce_size_applications += 1
        if (
            combined_protection_decision is None
            and economics_regime_decision is not None
            and economics_regime_decision.reduce_size
        ):
            economics_regime_multiplier = economics_regime_decision.size_multiplier
            self.status.economics_regime_reduce_size_applications += 1
        if combined_protection_decision is not None and combined_protection_decision.reduce_size:
            combined_protection_multiplier = combined_protection_decision.size_multiplier
            self.status.combined_protection_reduce_size_applications += 1
        combined_guard_multiplier = min(
            execution_drift_multiplier,
            intraday_protection_multiplier,
            pnl_protection_multiplier,
            trade_reconciliation_multiplier,
            session_truth_multiplier,
            session_truth_trend_multiplier,
            economics_regime_multiplier,
            combined_protection_multiplier,
        )
        combined_multiplier = (
            adaptive_sizing.multiplier
            * volatility_sizing.multiplier
            * economics_feedback_multiplier
            * combined_guard_multiplier
        )
        target_notional_usdt = volatility_sizing.target_notional_usdt * economics_feedback_multiplier * combined_guard_multiplier
        self._record_notional_decision(
            target_notional_usdt=target_notional_usdt,
            multiplier=combined_multiplier,
        )
        self.status.last_notional_multiplier = str(combined_multiplier)
        self.status.last_target_notional_usdt = str(target_notional_usdt)
        self.status.last_execution_drift_action = (
            execution_drift_decision.action if execution_drift_decision is not None else ""
        )
        self.status.last_execution_drift_size_multiplier = str(execution_drift_multiplier)
        self.status.last_intraday_protection_action = (
            intraday_protection_decision.action if intraday_protection_decision is not None else ""
        )
        self.status.last_intraday_protection_size_multiplier = str(intraday_protection_multiplier)
        self.status.last_pnl_protection_action = (
            pnl_protection_decision.action if pnl_protection_decision is not None else ""
        )
        self.status.last_pnl_protection_size_multiplier = str(pnl_protection_multiplier)
        self.status.last_trade_reconciliation_action = (
            trade_reconciliation_decision.action if trade_reconciliation_decision is not None else ""
        )
        self.status.last_trade_reconciliation_size_multiplier = str(trade_reconciliation_multiplier)
        self.status.last_trade_reconciliation_missing_local_trade_ratio = (
            str(trade_reconciliation_decision.missing_local_trade_ratio)
            if trade_reconciliation_decision is not None else ""
        )
        self.status.last_trade_reconciliation_realized_pnl_diff_usdt = (
            str(trade_reconciliation_decision.realized_pnl_diff_usdt)
            if trade_reconciliation_decision is not None else ""
        )
        self.status.last_trade_reconciliation_commission_abs_diff_usdt = (
            str(trade_reconciliation_decision.commission_abs_diff_usdt)
            if trade_reconciliation_decision is not None else ""
        )
        self.status.last_session_truth_action = (
            session_truth_decision.action if session_truth_decision is not None else ""
        )
        self.status.last_session_truth_size_multiplier = str(session_truth_multiplier)
        self.status.last_session_truth_window_mode = (
            session_truth_decision.window_mode if session_truth_decision is not None else ""
        )
        self.status.last_session_truth_session_started_at_ms = (
            session_truth_decision.session_started_at_ms if session_truth_decision is not None else 0
        )
        self.status.last_session_truth_net_realized_pnl_usdt = (
            str(session_truth_decision.net_realized_pnl_usdt) if session_truth_decision is not None else ""
        )
        self.status.last_session_truth_net_realized_bps = (
            str(session_truth_decision.net_realized_bps) if session_truth_decision is not None else ""
        )
        self.status.last_session_truth_maker_ratio = (
            str(session_truth_decision.maker_ratio) if session_truth_decision is not None else ""
        )
        self.status.last_session_truth_trend_action = (
            session_truth_trend_decision.action if session_truth_trend_decision is not None else ""
        )
        self.status.last_session_truth_trend_size_multiplier = str(session_truth_trend_multiplier)
        self.status.last_session_truth_trend_active_bucket_count = (
            session_truth_trend_decision.active_bucket_count if session_truth_trend_decision is not None else 0
        )
        self.status.last_session_truth_trend_negative_bucket_ratio = (
            str(session_truth_trend_decision.negative_bucket_ratio) if session_truth_trend_decision is not None else ""
        )
        self.status.last_session_truth_trend_trailing_negative_bucket_streak = (
            session_truth_trend_decision.trailing_negative_bucket_streak if session_truth_trend_decision is not None else 0
        )
        self.status.last_session_truth_trend_recent_bucket_net_realized_bps = (
            str(session_truth_trend_decision.recent_bucket_net_realized_bps) if session_truth_trend_decision is not None else ""
        )
        self.status.last_session_truth_trend_cumulative_drawdown_usdt = (
            str(session_truth_trend_decision.cumulative_drawdown_usdt) if session_truth_trend_decision is not None else ""
        )
        self.status.last_economics_regime_action = (
            economics_regime_decision.action if economics_regime_decision is not None else ""
        )
        self.status.last_economics_regime_size_multiplier = str(economics_regime_multiplier)
        self.status.last_economics_regime_negative_day_ratio = (
            str(economics_regime_decision.negative_day_ratio) if economics_regime_decision is not None else ""
        )
        self.status.last_economics_regime_recent_day_net_realized_bps = (
            str(economics_regime_decision.recent_day_net_realized_bps) if economics_regime_decision is not None else ""
        )
        self.status.last_economics_regime_average_maker_ratio = (
            str(economics_regime_decision.average_maker_ratio) if economics_regime_decision is not None else ""
        )
        self.status.last_economics_feedback_multiplier = str(economics_feedback_multiplier)
        self.status.last_economics_feedback_total_penalty = str(economics_feedback_decision.total_penalty)
        self.status.last_economics_feedback_reason = economics_feedback_decision.reason
        self.status.last_combined_protection_action = (
            combined_protection_decision.action if combined_protection_decision is not None else ""
        )
        self.status.last_combined_protection_size_multiplier = str(combined_protection_multiplier)
        self.status.last_combined_protection_cooldown_until_ms = (
            combined_protection_decision.cooldown_until_ms if combined_protection_decision is not None else 0
        )

        if target_notional_usdt < self.live_config.min_effective_notional_usdt:
            self.status.signal_gate_rejections += 1
            self.status.sizing_abstentions += 1
            self.status.last_gate_reason = "effective_notional_too_small_after_volatility"
            self._record_action(
                "volatility_effective_notional_reject",
                {
                    "signal": signal,
                    "adaptive_sizing": adaptive_sizing,
                    "volatility_sizing": volatility_sizing,
                    "execution_drift_decision": execution_drift_decision,
                    "intraday_protection_decision": intraday_protection_decision,
                    "pnl_protection_decision": pnl_protection_decision,
                    "trade_reconciliation_decision": trade_reconciliation_decision,
                    "session_truth_decision": session_truth_decision,
                    "economics_regime_decision": economics_regime_decision,
                    "economics_feedback_decision": economics_feedback_decision,
                    "combined_protection_decision": combined_protection_decision,
                    "execution_drift_multiplier": execution_drift_multiplier,
                    "intraday_protection_multiplier": intraday_protection_multiplier,
                    "pnl_protection_multiplier": pnl_protection_multiplier,
                    "trade_reconciliation_multiplier": trade_reconciliation_multiplier,
                    "economics_regime_multiplier": economics_regime_multiplier,
                    "economics_feedback_multiplier": economics_feedback_multiplier,
                    "combined_protection_multiplier": combined_protection_multiplier,
                    "combined_guard_multiplier": combined_guard_multiplier,
                },
                event_time_ms=event_time_ms,
            )
            return

        qty = target_notional_usdt / mark_price
        proposal = self.planner.entry_order(
            symbol=self.config.symbol,
            side=signal.side,
            qty=qty,
            mark_price=mark_price,
            position_side=PositionSide.BOTH,
        )
        proposal, preview_validation, preview_rejection = self._normalize_entry_proposal(
            proposal,
            reference_price=mark_price,
        )
        if preview_rejection:
            self.status.entries_rejected += 1
            self._record_action(
                "entry_validation_preview_reject",
                {
                    "signal": signal,
                    "proposal": proposal,
                    "validation": preview_validation,
                    "adaptive_sizing": adaptive_sizing,
                    "volatility_sizing": volatility_sizing,
                },
                event_time_ms=event_time_ms,
            )
            return

        queue_state = self._build_queue_state(side=proposal.side, limit_price=proposal.price, qty=proposal.qty)
        queue_decision = self.queue_admission_policy.evaluate(
            QueueAdmissionInputs(
                qty=proposal.qty,
                entry_timeout_seconds=self.live_config.entry_timeout_seconds,
                flow_window_seconds=self.live_config.trade_flow_window_seconds,
                directional_flow_qty=self._directional_queue_flow_qty(signal.context, proposal.side),
                queue_state=queue_state,
            )
        )
        self._apply_queue_status(queue_decision)
        if not queue_decision.execute:
            self.status.signal_gate_rejections += 1
            self.status.queue_gate_rejections += 1
            self.status.last_gate_reason = queue_decision.reason
            self._record_action(
                "queue_abstain",
                {
                    "signal": signal,
                    "proposal": proposal,
                    "adaptive_sizing": adaptive_sizing,
                    "volatility_sizing": volatility_sizing,
                    "queue_decision": queue_decision,
                    "queue_state": queue_state,
                },
                event_time_ms=event_time_ms,
            )
            return

        depth_liquidity_decision = self.depth_liquidity_policy.evaluate_for_entry(
            entry_side=proposal.side,
            qty=proposal.qty,
            book=self._current_depth_snapshot(),
        )
        self._apply_depth_liquidity_status(depth_liquidity_decision)
        if not depth_liquidity_decision.execute:
            self.status.signal_gate_rejections += 1
            self.status.depth_liquidity_gate_rejections += 1
            self.status.last_gate_reason = depth_liquidity_decision.reason
            self._record_action(
                "depth_liquidity_abstain",
                {
                    "signal": signal,
                    "proposal": proposal,
                    "adaptive_sizing": adaptive_sizing,
                    "volatility_sizing": volatility_sizing,
                    "queue_decision": queue_decision,
                    "depth_liquidity_decision": depth_liquidity_decision,
                },
                event_time_ms=event_time_ms,
            )
            return

        evaluated_at_ms = event_time_ms if received_at_ms is None else received_at_ms
        risk_context = self._build_risk_context(
            mark_price=mark_price,
            market_event_time_ms=event_time_ms,
            evaluated_at_ms=evaluated_at_ms,
        )
        decision = self.risk_limits.evaluate_new_entry(proposed_qty=proposal.qty, ctx=risk_context)
        if not decision.allow_new_entry:
            self.status.entries_rejected += 1
            self._record_action(
                "risk_reject",
                {
                    "signal": signal,
                    "qty": proposal.qty,
                    "proposal": proposal,
                    "adaptive_sizing": adaptive_sizing,
                    "volatility_sizing": volatility_sizing,
                    "queue_decision": queue_decision,
                    "depth_liquidity_decision": depth_liquidity_decision,
                    "hard_reasons": decision.hard_reasons,
                    "soft_warnings": decision.soft_warnings,
                },
                event_time_ms=event_time_ms,
            )
            return

        self.status.entry_attempts += 1
        result = await asyncio.to_thread(
            self.gateway.submit_normal,
            proposal,
            reference_price=mark_price,
            dry_run=not self.live_config.send_orders,
            test=self.live_config.test_orders,
        )
        if result.validation is not None and not result.validation.ok:
            self.status.entries_rejected += 1
            self._record_action(
                "entry_validation_reject",
                {"signal": signal, "proposal": proposal, "result": result, "adaptive_sizing": adaptive_sizing, "volatility_sizing": volatility_sizing, "queue_decision": queue_decision, "depth_liquidity_decision": depth_liquidity_decision},
                event_time_ms=event_time_ms,
            )
            return
        if result.error is not None and not result.execution_unknown:
            self.status.entries_rejected += 1
            self._record_action(
                "entry_submit_error",
                {"signal": signal, "proposal": proposal, "result": result, "adaptive_sizing": adaptive_sizing, "volatility_sizing": volatility_sizing, "queue_decision": queue_decision, "depth_liquidity_decision": depth_liquidity_decision},
                event_time_ms=event_time_ms,
            )
            return

        self.active_entry = ActiveEntry(
            client_id=proposal.client_id,
            side=proposal.side,
            qty=result.validation.normalized_qty if result.validation and result.validation.normalized_qty else proposal.qty,
            proposed_price=result.validation.normalized_price if result.validation else proposal.price,
            submitted_at_ms=event_time_ms,
            atr=signal.atr,
            target_notional_usdt=target_notional_usdt,
            sizing_multiplier=combined_multiplier,
            strategy_kind=signal.strategy_kind,
            selected_strategy_kind=signal.selected_strategy_kind or signal.strategy_kind,
            pending_exchange_confirmation=result.execution_unknown,
            queue_expectation=self._queue_expectation_from_decision(queue_decision),
        )
        self.status.active_entry_client_id = proposal.client_id
        if result.sent:
            self.status.entries_sent += 1
        if result.execution_unknown:
            self.status.entry_unknown_submissions += 1
        self._record_action(
            "entry_submit",
            {"signal": signal, "proposal": proposal, "result": result, "adaptive_sizing": adaptive_sizing, "volatility_sizing": volatility_sizing, "queue_decision": queue_decision, "depth_liquidity_decision": depth_liquidity_decision},
            event_time_ms=event_time_ms,
        )

    async def _maybe_cancel_stale_entry(self, *, event_time_ms: int) -> None:
        if self.active_entry is None:
            return
        age_ms = event_time_ms - self.active_entry.submitted_at_ms
        if age_ms < self.live_config.entry_timeout_seconds * 1000:
            return

        order_record = self.store.state.normal_orders.get(self.active_entry.client_id)
        if (order_record is None or self.active_entry.pending_exchange_confirmation) and self.live_config.send_orders:
            resolution = await self._query_active_entry(event_time_ms=event_time_ms, reason="stale_entry_check")
            order_record = self.store.state.normal_orders.get(self.active_entry.client_id)
            if resolution is not None and resolution.execution_unknown:
                return

        if order_record is not None and order_record.executed_qty > 0:
            return
        if order_record is not None and order_record.status in TERMINAL_ORDER_STATUSES:
            self._record_entry_queue_outcome(
                executed_qty=order_record.executed_qty,
                completed_at_ms=max(event_time_ms, order_record.update_time_ms),
                timed_out=True,
                reason="terminal_before_cancel",
            )
            self._record_action(
                "entry_terminal_before_cancel",
                {"client_id": self.active_entry.client_id, "order_record": order_record},
                event_time_ms=event_time_ms,
            )
            self.active_entry = None
            self.status.active_entry_client_id = ""
            self.cooldown_until_ms = event_time_ms + self.live_config.cooldown_after_cancel_seconds * 1000
            return

        result = await asyncio.to_thread(
            self.gateway.cancel_normal,
            symbol=self.config.symbol,
            client_order_id=self.active_entry.client_id,
            dry_run=not self.live_config.send_orders or self.live_config.test_orders,
        )
        self.status.stale_cancels += 1
        self._record_entry_queue_outcome(
            executed_qty=order_record.executed_qty if order_record is not None else Decimal("0"),
            completed_at_ms=event_time_ms,
            timed_out=True,
            reason="stale_cancel",
        )
        self._record_action(
            "entry_cancel_stale",
            {"client_id": self.active_entry.client_id, "age_ms": age_ms, "result": result},
            event_time_ms=event_time_ms,
        )
        self.active_entry = None
        self.status.active_entry_client_id = ""
        self.cooldown_until_ms = event_time_ms + self.live_config.cooldown_after_cancel_seconds * 1000

    async def _maybe_arm_exits(self, *, mark_price: Decimal, event_time_ms: int) -> None:
        if self.active_entry is None:
            return
        if not self.live_config.send_orders or self.live_config.test_orders:
            return

        order_record = self.store.state.normal_orders.get(self.active_entry.client_id)
        if (order_record is None or self.active_entry.pending_exchange_confirmation) and self.live_config.send_orders:
            resolution = await self._query_active_entry(event_time_ms=event_time_ms, reason="arm_exit_check")
            order_record = self.store.state.normal_orders.get(self.active_entry.client_id)
            if resolution is not None and resolution.execution_unknown:
                return
        if order_record is None:
            return
        if (
            order_record.executed_qty >= self.active_entry.qty
            or order_record.status in TERMINAL_ORDER_STATUSES
        ):
            self._record_entry_queue_outcome(
                executed_qty=order_record.executed_qty,
                completed_at_ms=max(event_time_ms, order_record.update_time_ms),
                timed_out=order_record.executed_qty < self.active_entry.qty,
                reason="filled_or_terminal",
            )

        newly_filled_qty = order_record.executed_qty - self.active_entry.protected_qty
        if newly_filled_qty <= 0:
            return

        entry_price = order_record.avg_price if order_record.avg_price > 0 else order_record.price
        if entry_price <= 0:
            entry_price = self.active_entry.proposed_price or mark_price

        stop_algo, tp_algo = self.planner.bracket_exits(
            symbol=self.config.symbol,
            entry_side=self.active_entry.side,
            qty=newly_filled_qty,
            entry_price=entry_price,
            atr=self.active_entry.atr,
            position_side=PositionSide.BOTH,
        )
        results = await self._submit_exit_pair(stop_algo, tp_algo)
        self.active_entry.protected_qty += newly_filled_qty
        self.active_entry.algo_client_ids.update(
            proposal.client_algo_id for proposal in (stop_algo, tp_algo) if proposal.client_algo_id
        )
        self.status.exit_brackets_armed += 1
        self._record_action(
            "exit_bracket_arm",
            {
                "entry_client_id": self.active_entry.client_id,
                "protected_qty": self.active_entry.protected_qty,
                "order_status": order_record.status,
                "results": results,
            },
            event_time_ms=event_time_ms,
        )

    async def _submit_exit_pair(
        self,
        stop_algo: AlgoOrderProposal,
        tp_algo: AlgoOrderProposal,
    ) -> list[GatewayResult]:
        stop_result = await asyncio.to_thread(self.gateway.submit_algo, stop_algo, dry_run=not self.live_config.send_orders)
        tp_result = await asyncio.to_thread(self.gateway.submit_algo, tp_algo, dry_run=not self.live_config.send_orders)
        return [stop_result, tp_result]

    async def _maybe_cleanup_flat_position(self, *, mark_price: Decimal, event_time_ms: int) -> None:
        if self.active_entry is None:
            return
        if self._current_position_qty() != 0:
            return

        order_record = self.store.state.normal_orders.get(self.active_entry.client_id)
        if order_record is None and self.live_config.send_orders and not self.live_config.test_orders:
            resolution = await self._query_active_entry(event_time_ms=event_time_ms, reason="flat_cleanup_check")
            if resolution is not None and resolution.execution_unknown:
                return
            order_record = self.store.state.normal_orders.get(self.active_entry.client_id)

        if self.active_entry.protected_qty <= 0:
            if order_record is None or order_record.status not in TERMINAL_ORDER_STATUSES:
                return
            self._record_entry_queue_outcome(
                executed_qty=order_record.executed_qty,
                completed_at_ms=max(event_time_ms, order_record.update_time_ms),
                timed_out=order_record.executed_qty < self.active_entry.qty,
                reason="flat_cleanup_terminal",
            )

        if self.active_entry.algo_client_ids:
            for client_algo_id in sorted(self.active_entry.algo_client_ids):
                algo_record = self.store.state.algo_orders.get(client_algo_id)
                if self.live_config.send_orders and not self.live_config.test_orders:
                    await self._query_algo(client_algo_id=client_algo_id, event_time_ms=event_time_ms, reason="orphan_exit_cleanup")
                    algo_record = self.store.state.algo_orders.get(client_algo_id)
                if algo_record is not None and algo_record.status in TERMINAL_ALGO_STATUSES:
                    self._record_action(
                        "exit_skip_terminal",
                        {"client_algo_id": client_algo_id, "algo_record": algo_record},
                        event_time_ms=event_time_ms,
                    )
                    continue
                result = await asyncio.to_thread(
                    self.gateway.cancel_algo,
                    symbol=self.config.symbol,
                    client_algo_id=client_algo_id,
                    dry_run=not self.live_config.send_orders,
                )
                self.status.exit_cancels += 1
                self._record_action(
                    "exit_cancel_orphan",
                    {"client_algo_id": client_algo_id, "result": result},
                    event_time_ms=event_time_ms,
                )
        self._record_action(
            "entry_session_closed",
            {"client_id": self.active_entry.client_id, "last_price": mark_price},
            event_time_ms=event_time_ms,
        )
        self.active_entry = None
        self.status.active_entry_client_id = ""

    async def _query_active_entry(self, *, event_time_ms: int, reason: str):
        if self.active_entry is None:
            return None
        resolution = await asyncio.to_thread(
            self.gateway.query_normal,
            symbol=self.config.symbol,
            client_order_id=self.active_entry.client_id,
        )
        self.status.targeted_queries += 1
        if resolution.found:
            self.active_entry.pending_exchange_confirmation = False
        self._record_action(
            "query_active_entry",
            {"reason": reason, "client_id": self.active_entry.client_id, "resolution": resolution},
            event_time_ms=event_time_ms,
        )
        return resolution

    async def _query_algo(self, *, client_algo_id: str, event_time_ms: int, reason: str):
        resolution = await asyncio.to_thread(
            self.gateway.query_algo,
            symbol=self.config.symbol,
            client_algo_id=client_algo_id,
        )
        self.status.targeted_queries += 1
        self._record_action(
            "query_algo_order",
            {"reason": reason, "client_algo_id": client_algo_id, "resolution": resolution},
            event_time_ms=event_time_ms,
        )
        return resolution

    def _contract_gate_reason(self) -> str:
        if not self.live_config.require_contract_trading_status:
            return ""
        contract_info = self.store.state.latest_contract_info
        if not contract_info:
            return ""
        status = str(contract_info.get("cs", ""))
        if status:
            self.status.last_contract_status = status
        bracket_rows = contract_info.get("bks") or []
        self.status.last_contract_bracket_count = len(bracket_rows)
        if status and status != "TRADING":
            return "contract_not_trading"
        return ""

    def _apply_book_status(self, *, event_time_ms: int) -> None:
        book = self.store.state.latest_book_ticker
        if not book:
            self.status.last_book_bid = ""
            self.status.last_book_ask = ""
            self.status.last_book_spread_bps = ""
            self.status.last_book_age_ms = 0
            return
        bid = Decimal(str(book.get("b", "0")))
        ask = Decimal(str(book.get("a", "0")))
        self.status.last_book_bid = str(bid)
        self.status.last_book_ask = str(ask)
        self.status.last_book_age_ms = max(0, event_time_ms - int(book.get("E", book.get("T", event_time_ms)) or event_time_ms))
        if bid > 0 and ask > 0:
            mid = (bid + ask) / Decimal("2")
            spread_bps = Decimal("0") if mid <= 0 else (ask - bid) / mid * Decimal("10000")
            self.status.last_book_spread_bps = str(spread_bps)
        else:
            self.status.last_book_spread_bps = ""

    def _book_gate_reason(self, *, event_time_ms: int) -> str:
        book = self.store.state.latest_book_ticker
        if not book:
            return ""
        event_book_time_ms = int(book.get("E", book.get("T", event_time_ms)) or event_time_ms)
        if (
            self.live_config.max_book_ticker_staleness_ms is not None
            and event_time_ms - event_book_time_ms > self.live_config.max_book_ticker_staleness_ms
        ):
            return "stale_book_ticker"
        if self.live_config.max_book_spread_bps is not None:
            try:
                bid = Decimal(str(book.get("b", "0")))
                ask = Decimal(str(book.get("a", "0")))
                if bid > 0 and ask > 0:
                    mid = (bid + ask) / Decimal("2")
                    spread_bps = Decimal("0") if mid <= 0 else (ask - bid) / mid * Decimal("10000")
                    if spread_bps > self.live_config.max_book_spread_bps:
                        return "book_spread_too_wide"
            except Exception:  # noqa: BLE001
                return "invalid_book_ticker"
        return ""

    def _apply_depth_status(self, *, event_time_ms: int) -> None:
        standard_depth = self._current_standard_depth_snapshot()
        rpi_depth = self._current_rpi_depth_snapshot()
        if self.live_config.use_rpi_depth_if_available and rpi_depth is not None:
            depth = rpi_depth
            depth_source = "rpi"
        else:
            depth = standard_depth
            depth_source = "standard" if standard_depth is not None else ""
        if depth is None:
            self.status.last_depth_imbalance = ""
            self.status.last_depth_age_ms = 0
            self.status.last_depth_levels = 0
            self.status.last_depth_source = ""
        else:
            self.status.last_depth_imbalance = str(depth.imbalance)
            self.status.last_depth_age_ms = max(0, event_time_ms - depth.event_time_ms)
            self.status.last_depth_levels = depth.levels
            self.status.last_depth_source = depth_source
        if rpi_depth is None:
            self.status.last_rpi_depth_age_ms = 0
            self.status.last_rpi_depth_levels = 0
        else:
            self.status.last_rpi_depth_age_ms = max(0, event_time_ms - rpi_depth.event_time_ms)
            self.status.last_rpi_depth_levels = rpi_depth.levels

    def _depth_gate_reason(self, *, side: Side, event_time_ms: int) -> str:
        depth = self._current_depth_snapshot()
        if depth is None:
            return ""
        if (
            self.live_config.max_depth_snapshot_staleness_ms is not None
            and event_time_ms - depth.event_time_ms > self.live_config.max_depth_snapshot_staleness_ms
        ):
            return "stale_local_depth"
        if self.live_config.min_depth_imbalance is not None:
            imbalance = depth.imbalance
            if side == Side.BUY and imbalance < self.live_config.min_depth_imbalance:
                return "depth_imbalance_not_confirmed_for_buy"
            if side == Side.SELL and imbalance > -self.live_config.min_depth_imbalance:
                return "depth_imbalance_not_confirmed_for_sell"
        return ""

    def _evaluate_crowding_gate(self, *, side: Side, event_time_ms: int) -> tuple[CrowdingScore | None, str]:
        return evaluate_crowding_gate(
            side=side,
            snapshot=self.store.state.latest_crowding_snapshot,
            config=self.crowding_gate_config,
            now_ms_value=event_time_ms,
        )

    def _apply_crowding_status(self, score: CrowdingScore | None) -> None:
        if score is None:
            return
        self.status.last_crowding_side_score = str(score.side_score)
        self.status.last_crowding_penalty = str(score.crowding_penalty)
        self.status.last_crowding_snapshot_age_ms = score.snapshot_age_ms
        self.status.last_crowding_global_ratio = (
            "" if score.global_long_short_ratio is None else str(score.global_long_short_ratio)
        )
        self.status.last_crowding_taker_ratio = (
            "" if score.taker_buy_sell_ratio is None else str(score.taker_buy_sell_ratio)
        )
        self.status.last_crowding_period = score.period

    def _load_execution_drift_decision(self) -> ExecutionDriftDecision | None:
        path_value = self.live_config.execution_drift_guard_path
        if not path_value:
            self.status.last_execution_drift_action = ""
            self.status.last_execution_drift_size_multiplier = ""
            return None
        try:
            payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001
            self.status.last_error = f"execution_drift_guard_read_error:{exc}"
            return None
        if not isinstance(payload, dict):
            return None
        decision = ExecutionDriftDecision.from_payload(payload)
        self.status.last_execution_drift_action = decision.action
        self.status.last_execution_drift_size_multiplier = str(decision.size_multiplier)
        return decision


    def _load_intraday_protection_decision(self) -> IntradayProtectionDecision | None:
        path_value = self.live_config.intraday_protection_guard_path
        if not path_value:
            self.status.last_intraday_protection_action = ""
            self.status.last_intraday_protection_size_multiplier = ""
            return None
        try:
            payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001
            self.status.last_error = f"intraday_protection_guard_read_error:{exc}"
            return None
        if not isinstance(payload, dict):
            return None
        decision = IntradayProtectionDecision.from_payload(payload)
        self.status.last_intraday_protection_action = decision.action
        self.status.last_intraday_protection_size_multiplier = str(decision.size_multiplier)
        return decision

    def _load_pnl_protection_decision(self) -> PnLProtectionDecision | None:
        path_value = self.live_config.pnl_protection_guard_path
        if not path_value:
            self.status.last_pnl_protection_action = ""
            self.status.last_pnl_protection_size_multiplier = ""
            self.status.last_pnl_session_loss_usdt = ""
            self.status.last_pnl_drawdown_usdt = ""
            self.status.last_pnl_unrealized_loss_usdt = ""
            return None
        try:
            payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001
            self.status.last_error = f"pnl_protection_guard_read_error:{exc}"
            return None
        if not isinstance(payload, dict):
            return None
        decision = PnLProtectionDecision.from_payload(payload)
        self.status.last_pnl_protection_action = decision.action
        self.status.last_pnl_protection_size_multiplier = str(decision.size_multiplier)
        self.status.last_pnl_session_loss_usdt = str(decision.session_loss_usdt)
        self.status.last_pnl_drawdown_usdt = str(decision.drawdown_usdt)
        self.status.last_pnl_unrealized_loss_usdt = str(decision.unrealized_loss_usdt)
        return decision


    def _load_trade_reconciliation_decision(self) -> TradeReconciliationDecision | None:
        path_value = self.live_config.trade_reconciliation_guard_path
        if not path_value:
            self.status.last_trade_reconciliation_action = ""
            self.status.last_trade_reconciliation_size_multiplier = ""
            self.status.last_trade_reconciliation_window_mode = ""
            self.status.last_trade_reconciliation_session_started_at_ms = 0
            self.status.last_trade_reconciliation_missing_local_trade_ratio = ""
            self.status.last_trade_reconciliation_missing_local_order_ratio = ""
            self.status.last_trade_reconciliation_realized_pnl_diff_usdt = ""
            self.status.last_trade_reconciliation_commission_abs_diff_usdt = ""
            self.status.last_trade_reconciliation_quote_qty_abs_diff_usdt = ""
            self.status.last_trade_reconciliation_income_trade_link_gap_ratio = ""
            return None
        try:
            payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001
            self.status.last_error = f"trade_reconciliation_guard_read_error:{exc}"
            return None
        if not isinstance(payload, dict):
            return None
        decision = TradeReconciliationDecision.from_payload(payload)
        self.status.last_trade_reconciliation_action = decision.action
        self.status.last_trade_reconciliation_size_multiplier = str(decision.size_multiplier)
        self.status.last_trade_reconciliation_window_mode = decision.window_mode
        self.status.last_trade_reconciliation_session_started_at_ms = decision.session_started_at_ms
        self.status.last_trade_reconciliation_missing_local_trade_ratio = str(decision.missing_local_trade_ratio)
        self.status.last_trade_reconciliation_missing_local_order_ratio = str(decision.missing_local_order_ratio)
        self.status.last_trade_reconciliation_realized_pnl_diff_usdt = str(decision.realized_pnl_diff_usdt)
        self.status.last_trade_reconciliation_commission_abs_diff_usdt = str(decision.commission_abs_diff_usdt)
        self.status.last_trade_reconciliation_quote_qty_abs_diff_usdt = str(decision.quote_qty_abs_diff_usdt)
        self.status.last_trade_reconciliation_income_trade_link_gap_ratio = str(decision.income_trade_link_gap_ratio)
        return decision

    def _load_session_truth_decision(self) -> SessionTruthDecision | None:
        path_value = self.live_config.session_truth_guard_path
        if not path_value:
            self.status.last_session_truth_action = ""
            self.status.last_session_truth_size_multiplier = ""
            self.status.last_session_truth_window_mode = ""
            self.status.last_session_truth_session_started_at_ms = 0
            self.status.last_session_truth_net_realized_pnl_usdt = ""
            self.status.last_session_truth_net_realized_bps = ""
            self.status.last_session_truth_maker_ratio = ""
            return None
        try:
            payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001
            self.status.last_error = f"session_truth_guard_read_error:{exc}"
            return None
        if not isinstance(payload, dict):
            return None
        decision = SessionTruthDecision.from_payload(payload)
        self.status.last_session_truth_action = decision.action
        self.status.last_session_truth_size_multiplier = str(decision.size_multiplier)
        self.status.last_session_truth_window_mode = decision.window_mode
        self.status.last_session_truth_session_started_at_ms = decision.session_started_at_ms
        self.status.last_session_truth_net_realized_pnl_usdt = str(decision.net_realized_pnl_usdt)
        self.status.last_session_truth_net_realized_bps = str(decision.net_realized_bps)
        self.status.last_session_truth_maker_ratio = str(decision.maker_ratio)
        return decision

    def _load_session_truth_trend_decision(self) -> SessionTruthTrendDecision | None:
        path_value = self.live_config.session_truth_trend_guard_path
        if not path_value:
            self.status.last_session_truth_trend_action = ""
            self.status.last_session_truth_trend_size_multiplier = ""
            self.status.last_session_truth_trend_active_bucket_count = 0
            self.status.last_session_truth_trend_negative_bucket_ratio = ""
            self.status.last_session_truth_trend_trailing_negative_bucket_streak = 0
            self.status.last_session_truth_trend_recent_bucket_net_realized_bps = ""
            self.status.last_session_truth_trend_cumulative_drawdown_usdt = ""
            return None
        try:
            payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001
            self.status.last_error = f"session_truth_trend_guard_read_error:{exc}"
            return None
        if not isinstance(payload, dict):
            return None
        decision = SessionTruthTrendDecision.from_payload(payload)
        self.status.last_session_truth_trend_action = decision.action
        self.status.last_session_truth_trend_size_multiplier = str(decision.size_multiplier)
        self.status.last_session_truth_trend_active_bucket_count = decision.active_bucket_count
        self.status.last_session_truth_trend_negative_bucket_ratio = str(decision.negative_bucket_ratio)
        self.status.last_session_truth_trend_trailing_negative_bucket_streak = decision.trailing_negative_bucket_streak
        self.status.last_session_truth_trend_recent_bucket_net_realized_bps = str(decision.recent_bucket_net_realized_bps)
        self.status.last_session_truth_trend_cumulative_drawdown_usdt = str(decision.cumulative_drawdown_usdt)
        return decision

    def _load_economics_regime_decision(self) -> EconomicsRegimeDecision | None:
        path_value = self.live_config.economics_regime_guard_path
        if not path_value:
            self.status.last_economics_regime_action = ""
            self.status.last_economics_regime_size_multiplier = ""
            self.status.last_economics_regime_negative_day_ratio = ""
            self.status.last_economics_regime_recent_day_net_realized_bps = ""
            self.status.last_economics_regime_average_maker_ratio = ""
            return None
        try:
            payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001
            self.status.last_error = f"economics_regime_guard_read_error:{exc}"
            return None
        if not isinstance(payload, dict):
            return None
        decision = EconomicsRegimeDecision.from_payload(payload)
        self.status.last_economics_regime_action = decision.action
        self.status.last_economics_regime_size_multiplier = str(decision.size_multiplier)
        self.status.last_economics_regime_negative_day_ratio = str(decision.negative_day_ratio)
        self.status.last_economics_regime_recent_day_net_realized_bps = str(decision.recent_day_net_realized_bps)
        self.status.last_economics_regime_average_maker_ratio = str(decision.average_maker_ratio)
        return decision

    def _load_economics_dashboard(self) -> EconomicsDashboard | None:
        path_value = self.live_config.economics_dashboard_path
        if not path_value:
            path_value = str(self.config.data_dir / "live" / "reports" / "latest_economics_dashboard.json")
        try:
            payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001
            self.status.last_error = f"economics_dashboard_read_error:{exc}"
            return None
        if not isinstance(payload, dict):
            return None
        dashboard_payload = payload.get("dashboard") if isinstance(payload.get("dashboard"), dict) else payload
        if not isinstance(dashboard_payload, dict):
            return None
        return EconomicsDashboard.from_payload(dashboard_payload)

    def _evaluate_economics_feedback(
        self,
        *,
        economics_dashboard: EconomicsDashboard | None,
        economics_regime_decision: EconomicsRegimeDecision | None,
    ) -> EconomicsFeedbackDecision:
        if economics_regime_decision is not None and economics_regime_decision.action != "trade":
            decision = EconomicsFeedbackDecision(
                applied=False,
                multiplier=Decimal("1"),
                reason="economics_regime_non_trade",
            )
        else:
            decision = self.economics_feedback_policy.evaluate(economics_dashboard)
        self.status.last_economics_feedback_multiplier = str(decision.multiplier)
        self.status.last_economics_feedback_total_penalty = str(decision.total_penalty)
        self.status.last_economics_feedback_reason = decision.reason
        if decision.applied:
            self.status.economics_feedback_decision_count += 1
            self.status.economics_feedback_multiplier_sum += decision.multiplier
        return decision

    def _load_combined_protection_decision(self) -> CombinedProtectionDecision | None:
        path_value = self.live_config.combined_protection_guard_path
        if not path_value:
            self.status.last_combined_protection_action = ""
            self.status.last_combined_protection_size_multiplier = ""
            self.status.last_combined_protection_cooldown_until_ms = 0
            return None
        try:
            payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001
            self.status.last_error = f"combined_protection_guard_read_error:{exc}"
            return None
        if not isinstance(payload, dict):
            return None
        decision = CombinedProtectionDecision.from_payload(payload)
        self.status.last_combined_protection_action = decision.action
        self.status.last_combined_protection_size_multiplier = str(decision.size_multiplier)
        self.status.last_combined_protection_cooldown_until_ms = decision.cooldown_until_ms
        return decision

    def _normalize_entry_proposal(
        self,
        proposal: OrderProposal,
        *,
        reference_price: Decimal,
    ) -> tuple[OrderProposal, object | None, str]:
        validator = getattr(self.gateway, "validator", None)
        if validator is None:
            return proposal, None, ""
        if proposal.price is None:
            return proposal, None, "missing_limit_price"
        validation = validator.validate_limit(
            price=proposal.price,
            qty=proposal.qty,
            reference_price=reference_price,
        )
        if not validation.ok or validation.normalized_price is None or validation.normalized_qty is None:
            return proposal, validation, "validation_reject"
        normalized = OrderProposal(
            symbol=proposal.symbol,
            side=proposal.side,
            position_side=proposal.position_side,
            order_type=proposal.order_type,
            tif=proposal.tif,
            qty=validation.normalized_qty,
            price=validation.normalized_price,
            reduce_only=proposal.reduce_only,
            close_position=proposal.close_position,
            working_type=proposal.working_type,
            client_id=proposal.client_id,
        )
        return normalized, validation, ""

    def _current_book_snapshot(self) -> TopOfBookSnapshot | None:
        payload = self.store.state.latest_book_ticker
        if not payload:
            return None
        try:
            bid_price = Decimal(str(payload.get("b", payload.get("bidPrice", "0"))))
            ask_price = Decimal(str(payload.get("a", payload.get("askPrice", "0"))))
            bid_qty = Decimal(str(payload.get("B", payload.get("bidQty", "0"))))
            ask_qty = Decimal(str(payload.get("A", payload.get("askQty", "0"))))
        except Exception:  # noqa: BLE001
            return None
        return TopOfBookSnapshot(
            event_time_ms=int(payload.get("E", payload.get("eventTime", 0)) or 0),
            bid_price=bid_price,
            bid_qty=bid_qty,
            ask_price=ask_price,
            ask_qty=ask_qty,
        )

    def _current_standard_depth_snapshot(self) -> DepthBookSnapshot | None:
        payload = self.store.state.latest_depth_snapshot
        if not payload:
            return None
        try:
            return DepthBookSnapshot.from_payload(payload)
        except Exception:  # noqa: BLE001
            return None

    def _current_rpi_depth_snapshot(self) -> DepthBookSnapshot | None:
        payload = self.store.state.latest_rpi_depth_snapshot
        if not payload:
            return None
        try:
            return DepthBookSnapshot.from_payload(payload)
        except Exception:  # noqa: BLE001
            return None

    def _current_depth_snapshot(self) -> DepthBookSnapshot | None:
        if self.live_config.use_rpi_depth_if_available:
            rpi_depth = self._current_rpi_depth_snapshot()
            if rpi_depth is not None:
                return rpi_depth
        return self._current_standard_depth_snapshot()

    def _build_queue_state(
        self,
        *,
        side: Side,
        limit_price: Decimal | None,
        qty: Decimal,
    ):
        if limit_price is None:
            return None
        depth = self._current_depth_snapshot()
        if depth is not None:
            return self.depth_fill_model.place_order(side=side, limit_price=limit_price, qty=qty, book=depth)
        book = self._current_book_snapshot()
        if book is not None:
            return self.book_fill_model.place_order(side=side, limit_price=limit_price, qty=qty, book=book)
        return None

    @staticmethod
    def _directional_queue_flow_qty(context: SignalContext | None, side: Side) -> Decimal:
        if context is None:
            return Decimal("0")
        return context.sell_aggressor_qty if side == Side.BUY else context.buy_aggressor_qty

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
        *,
        executed_qty: Decimal,
        completed_at_ms: int,
        timed_out: bool,
        reason: str,
    ) -> None:
        if self.active_entry is None or self.active_entry.entry_outcome_recorded:
            return
        outcome = self.queue_calibration_model.evaluate(
            expectation=self.active_entry.queue_expectation,
            submitted_at_ms=self.active_entry.submitted_at_ms,
            completed_at_ms=completed_at_ms,
            requested_qty=self.active_entry.qty,
            executed_qty=executed_qty,
            timed_out=timed_out,
        )
        self.active_entry.entry_outcome_recorded = True
        self.status.entry_outcome_count += 1
        if outcome.timed_out:
            self.status.entry_timeout_count += 1
        self.status.last_actual_entry_fill_ratio = str(outcome.actual_fill_ratio)
        self.status.last_entry_fill_latency_seconds = str(outcome.actual_fill_latency_seconds)
        self.status.last_entry_fill_ratio_shortfall = (
            "" if outcome.fill_ratio_shortfall is None else str(outcome.fill_ratio_shortfall)
        )
        self.status.last_entry_fill_latency_overshoot_seconds = (
            "" if outcome.fill_latency_overshoot_seconds is None else str(outcome.fill_latency_overshoot_seconds)
        )
        self.status.realized_entry_fill_ratio_sum += outcome.actual_fill_ratio
        self.status.entry_fill_latency_seconds_sum += outcome.actual_fill_latency_seconds
        self.status.entry_fill_latency_count += 1
        if outcome.fill_ratio_shortfall is not None:
            self.status.entry_fill_ratio_shortfall_sum += outcome.fill_ratio_shortfall
            self.status.entry_fill_ratio_shortfall_count += 1
        if outcome.fill_latency_overshoot_seconds is not None:
            self.status.entry_fill_latency_overshoot_seconds_sum += outcome.fill_latency_overshoot_seconds
            self.status.entry_fill_latency_overshoot_count += 1
        selected_strategy_kind = self.active_entry.selected_strategy_kind or self.active_entry.strategy_kind
        if hasattr(self.model, "record_entry_outcome") and selected_strategy_kind:
            self.model.record_entry_outcome(
                strategy_kind=selected_strategy_kind,
                actual_fill_ratio=outcome.actual_fill_ratio,
                fill_ratio_shortfall=outcome.fill_ratio_shortfall,
                fill_latency_overshoot_seconds=outcome.fill_latency_overshoot_seconds,
                timed_out=outcome.timed_out,
            )
        self._record_action(
            "entry_queue_outcome",
            {
                "reason": reason,
                "client_id": self.active_entry.client_id,
                "outcome": outcome,
            },
            event_time_ms=completed_at_ms,
        )

    def _apply_volatility_status(self, decision: VolatilitySizingDecision | None) -> None:
        if decision is None:
            self.status.last_volatility_multiplier = ""
            self.status.last_atr_fraction_bps = ""
            return
        self.status.last_volatility_multiplier = str(decision.multiplier)
        self.status.last_atr_fraction_bps = str(decision.atr_fraction * Decimal("10000"))
        self.status.volatility_decision_count += 1
        self.status.volatility_multiplier_sum += decision.multiplier

    def _apply_queue_status(self, decision: QueueAdmissionDecision | None) -> None:
        if decision is None:
            self.status.last_expected_fill_ratio = ""
            self.status.last_expected_queue_clear_seconds = ""
            self.status.last_queue_ahead_ratio = ""
            self.status.last_directional_queue_flow_qty_per_second = ""
            return
        self.status.last_expected_fill_ratio = str(decision.expected_fill_ratio)
        self.status.last_expected_queue_clear_seconds = (
            "" if decision.expected_queue_clear_seconds is None else str(decision.expected_queue_clear_seconds)
        )
        self.status.last_queue_ahead_ratio = (
            "" if decision.queue_ahead_ratio is None else str(decision.queue_ahead_ratio)
        )
        self.status.last_directional_queue_flow_qty_per_second = str(decision.directional_flow_qty_per_second)
        self.status.queue_decision_count += 1
        self.status.expected_fill_ratio_sum += decision.expected_fill_ratio
        self.status.directional_queue_flow_rate_sum += decision.directional_flow_qty_per_second
        if decision.expected_queue_clear_seconds is not None:
            self.status.queue_clear_seconds_sum += decision.expected_queue_clear_seconds
            self.status.queue_clear_seconds_count += 1
        if decision.queue_ahead_ratio is not None:
            self.status.queue_ahead_ratio_sum += decision.queue_ahead_ratio
            self.status.queue_ahead_ratio_count += 1

    def _apply_depth_liquidity_status(self, decision) -> None:
        if decision is None or decision.estimate is None:
            self.status.last_exit_depth_coverage_ratio = ""
            self.status.last_exit_depth_sweep_bps = ""
            self.status.last_exit_depth_levels_consumed = 0
            self.status.last_exit_synthetic_tail_coverage_ratio = ""
            self.status.last_exit_synthetic_tail_levels_consumed = 0
            self.status.last_exit_terminal_tail_ratio = ""
            return
        estimate = decision.estimate
        self.status.last_exit_depth_coverage_ratio = str(estimate.displayed_coverage_ratio)
        self.status.last_exit_depth_sweep_bps = "" if estimate.sweep_slippage_bps is None else str(estimate.sweep_slippage_bps)
        self.status.last_exit_depth_levels_consumed = estimate.levels_consumed
        self.status.last_exit_synthetic_tail_coverage_ratio = str(estimate.synthetic_tail_coverage_ratio)
        self.status.last_exit_synthetic_tail_levels_consumed = estimate.synthetic_tail_levels_consumed
        self.status.last_exit_terminal_tail_ratio = str(estimate.terminal_tail_ratio)
        self.status.exit_depth_estimate_count += 1
        self.status.exit_depth_coverage_ratio_sum += estimate.displayed_coverage_ratio
        self.status.exit_depth_levels_consumed_sum += Decimal(estimate.levels_consumed)
        self.status.exit_synthetic_tail_coverage_ratio_sum += estimate.synthetic_tail_coverage_ratio
        self.status.exit_synthetic_tail_levels_consumed_sum += Decimal(estimate.synthetic_tail_levels_consumed)
        self.status.exit_terminal_tail_ratio_sum += estimate.terminal_tail_ratio
        if estimate.sweep_slippage_bps is not None:
            self.status.exit_depth_sweep_bps_sum += estimate.sweep_slippage_bps

    def _build_risk_context(
        self,
        *,
        mark_price: Decimal,
        market_event_time_ms: int,
        evaluated_at_ms: int,
    ) -> RiskContext:
        summary = self.store.state.last_bootstrap_summary
        last_reconcile_at_ms = self.store.state.last_reconcile_at_ms
        reconcile_age_ms = None
        if last_reconcile_at_ms is not None:
            reconcile_age_ms = evaluated_at_ms - last_reconcile_at_ms
        market_data_age_ms = max(0, evaluated_at_ms - market_event_time_ms)
        return RiskContext(
            mark_price=mark_price,
            current_position_qty=self._current_position_qty(),
            current_leverage=Decimal("0"),
            realized_pnl_today=Decimal("0"),
            open_normal_orders=self.store.state.open_normal_orders,
            open_algo_orders=self.store.state.open_algo_orders,
            last_market_data_age_ms=market_data_age_ms,
            reconcile_age_ms=reconcile_age_ms,
            reconcile_mismatch_count=self.store.state.last_reconcile_mismatch_count,
            reconcile_required=self.live_config.send_orders,
            quantitative_lock=bool(summary.get("quantitative_lock", False)),
            cooling_off=bool(summary.get("cooling_off", False)),
            emergency_only=self.store.state.listen_key_expired_at_ms is not None,
        )

    def _record_notional_decision(self, *, target_notional_usdt: Decimal, multiplier: Decimal) -> None:
        self.status.notional_decision_count += 1
        self.status.target_notional_sum += target_notional_usdt
        self.status.notional_multiplier_sum += multiplier

    def _apply_router_evaluation(self, evaluation: SignalEvaluation) -> None:
        if not evaluation.router_regime and not evaluation.selected_strategy_kind and not evaluation.preferred_strategy_kind:
            return
        strategy_kind = evaluation.signal.strategy_kind if evaluation.signal is not None else ""
        if evaluation.router_regime:
            if strategy_kind == "ensemble":
                self.status.last_ensemble_regime = evaluation.router_regime
            else:
                self.status.last_router_regime = evaluation.router_regime
        if evaluation.preferred_strategy_kind:
            if strategy_kind == "ensemble":
                self.status.last_ensemble_preferred_strategy_kind = evaluation.preferred_strategy_kind
            else:
                self.status.last_router_preferred_strategy_kind = evaluation.preferred_strategy_kind
        if evaluation.selected_strategy_kind:
            if strategy_kind == "ensemble":
                self.status.last_ensemble_selected_strategy_kind = evaluation.selected_strategy_kind
            else:
                self.status.last_router_selected_strategy_kind = evaluation.selected_strategy_kind
        if evaluation.ensemble_breakout_score is not None:
            self.status.last_ensemble_breakout_score = str(evaluation.ensemble_breakout_score)
        if evaluation.ensemble_reversion_score is not None:
            self.status.last_ensemble_reversion_score = str(evaluation.ensemble_reversion_score)
        if evaluation.signal is None or not evaluation.selected_strategy_kind:
            return
        if strategy_kind == "ensemble":
            if evaluation.selected_strategy_kind == "breakout":
                self.status.ensemble_breakout_signal_count += 1
            elif evaluation.selected_strategy_kind == "reversion":
                self.status.ensemble_reversion_signal_count += 1
            if (
                evaluation.preferred_strategy_kind
                and evaluation.selected_strategy_kind != evaluation.preferred_strategy_kind
            ):
                self.status.ensemble_override_signal_count += 1
            return
        if evaluation.selected_strategy_kind == "breakout":
            self.status.router_breakout_signal_count += 1
        elif evaluation.selected_strategy_kind == "reversion":
            self.status.router_reversion_signal_count += 1
        if (
            evaluation.preferred_strategy_kind
            and evaluation.selected_strategy_kind != evaluation.preferred_strategy_kind
        ):
            self.status.router_fallback_signal_count += 1

    def _apply_signal_context(self, *, context: SignalContext | None, rejection_reason: str) -> None:
        self.status.last_gate_reason = rejection_reason
        if context is None:
            self.status.last_flow_imbalance = ""
            self.status.last_recent_trade_count = 0
            self.status.last_funding_rate = ""
            self.status.last_mark_trade_divergence_bps = ""
            return
        self.status.last_flow_imbalance = str(context.flow_imbalance)
        self.status.last_recent_trade_count = context.recent_trade_count
        self.status.last_funding_rate = "" if context.funding_rate is None else str(context.funding_rate)
        self.status.last_mark_trade_divergence_bps = "" if context.mark_trade_divergence_bps is None else str(context.mark_trade_divergence_bps)

    def _current_position_qty(self) -> Decimal:
        return self.store.current_position_qty(self.config.symbol, PositionSide.BOTH)

    def _bootstrap_shared_state(self) -> None:
        synchronizer = BootstrapSynchronizer(self.config, client=self.client, store=self.store)
        result, raw_snapshot = synchronizer.sync()
        self.writer.write_json("live/bootstrap_raw_snapshot.json", raw_snapshot)
        self.writer.write_json("live/bootstrap_state.json", self.store.snapshot())
        self.writer.write_json("live/bootstrap_result.json", result)

    def _record_action(self, action_type: str, payload: object, *, event_time_ms: int) -> None:
        self.status.actions_emitted += 1
        path = self.writer.append_record(
            "live",
            f"{self.config.symbol.lower()}_actions",
            {
                "action_type": action_type,
                "payload": payload,
                "active_entry_client_id": self.status.active_entry_client_id,
                "market_messages": self.status.market_messages,
            },
            event_time_ms=event_time_ms,
        )
        self.status.last_action_path = str(path)
        self._flush_status()

    def _flush_status(self) -> None:
        self.status.session_last_update_at_ms = now_ms()
        self.writer.write_json("live/status/latest.json", self.status)
        self.writer.write_json("live/reports/latest_execution_quality.json", build_live_execution_quality_report(self.status))


def _average_true_range_like(prices: list[Decimal]) -> Decimal:
    if len(prices) < 2:
        return Decimal("0")
    diffs = [abs(curr - prev) for prev, curr in zip(prices[:-1], prices[1:], strict=True)]
    if not diffs:
        return Decimal("0")
    return sum(diffs, start=Decimal("0")) / Decimal(len(diffs))

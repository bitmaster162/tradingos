from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from btcusdt_bot.live_breakout import LiveBreakoutStatus


@dataclass(slots=True)
class LiveExecutionQualityReport:
    session_started_at_ms: int
    session_last_update_at_ms: int
    session_duration_ms: int
    market_messages: int
    entry_attempts: int
    entries_sent: int
    entries_rejected: int
    entry_unknown_submissions: int
    stale_cancels: int
    exit_brackets_armed: int
    exit_cancels: int
    targeted_queries: int
    reconnects: int
    average_target_notional_usdt: Decimal | None
    average_notional_multiplier: Decimal | None
    average_volatility_multiplier: Decimal | None
    average_economics_feedback_multiplier: Decimal | None
    average_expected_fill_ratio: Decimal | None
    average_queue_clear_seconds: Decimal | None
    average_queue_ahead_ratio: Decimal | None
    average_directional_queue_flow_qty_per_second: Decimal | None
    average_realized_entry_fill_ratio: Decimal | None
    average_entry_fill_ratio_shortfall: Decimal | None
    average_entry_fill_latency_seconds: Decimal | None
    average_entry_fill_latency_overshoot_seconds: Decimal | None
    entry_timeout_rate: Decimal | None
    average_exit_depth_sweep_bps: Decimal | None
    average_exit_depth_coverage_ratio: Decimal | None
    average_exit_depth_levels_consumed: Decimal | None
    average_exit_synthetic_tail_coverage_ratio: Decimal | None
    average_exit_synthetic_tail_levels_consumed: Decimal | None
    average_exit_terminal_tail_ratio: Decimal | None
    signal_gate_rejections: int
    crowding_gate_rejections: int
    router_breakout_signal_count: int
    router_reversion_signal_count: int
    router_fallback_signal_count: int
    last_router_regime: str
    last_router_selected_strategy_kind: str
    last_router_preferred_strategy_kind: str
    ensemble_breakout_signal_count: int
    ensemble_reversion_signal_count: int
    ensemble_override_signal_count: int
    last_ensemble_regime: str
    last_ensemble_selected_strategy_kind: str
    last_ensemble_preferred_strategy_kind: str
    last_ensemble_breakout_score: Decimal | None
    last_ensemble_reversion_score: Decimal | None
    book_gate_rejections: int
    depth_gate_rejections: int
    last_depth_source: str
    last_rpi_depth_age_ms: int
    last_rpi_depth_levels: int
    queue_gate_rejections: int
    depth_liquidity_gate_rejections: int
    sizing_abstentions: int
    execution_drift_reduce_size_applications: int
    execution_drift_observe_rejections: int
    intraday_protection_reduce_size_applications: int
    intraday_protection_observe_rejections: int
    pnl_protection_reduce_size_applications: int
    pnl_protection_observe_rejections: int
    trade_reconciliation_reduce_size_applications: int
    trade_reconciliation_observe_rejections: int
    session_truth_reduce_size_applications: int
    session_truth_observe_rejections: int
    session_truth_trend_reduce_size_applications: int
    session_truth_trend_observe_rejections: int
    economics_regime_reduce_size_applications: int
    economics_regime_observe_rejections: int
    combined_protection_reduce_size_applications: int
    combined_protection_observe_rejections: int
    last_execution_drift_action: str
    last_execution_drift_size_multiplier: Decimal | None
    last_intraday_protection_action: str
    last_intraday_protection_size_multiplier: Decimal | None
    last_pnl_protection_action: str
    last_pnl_protection_size_multiplier: Decimal | None
    last_trade_reconciliation_action: str
    last_trade_reconciliation_size_multiplier: Decimal | None
    last_trade_reconciliation_window_mode: str
    last_trade_reconciliation_session_started_at_ms: int
    last_trade_reconciliation_missing_local_trade_ratio: Decimal | None
    last_trade_reconciliation_missing_local_order_ratio: Decimal | None
    last_trade_reconciliation_realized_pnl_diff_usdt: Decimal | None
    last_trade_reconciliation_commission_abs_diff_usdt: Decimal | None
    last_trade_reconciliation_quote_qty_abs_diff_usdt: Decimal | None
    last_trade_reconciliation_income_trade_link_gap_ratio: Decimal | None
    last_session_truth_action: str
    last_session_truth_size_multiplier: Decimal | None
    last_session_truth_window_mode: str
    last_session_truth_session_started_at_ms: int
    last_session_truth_net_realized_pnl_usdt: Decimal | None
    last_session_truth_net_realized_bps: Decimal | None
    last_session_truth_maker_ratio: Decimal | None
    last_session_truth_trend_action: str
    last_session_truth_trend_size_multiplier: Decimal | None
    last_session_truth_trend_active_bucket_count: int
    last_session_truth_trend_negative_bucket_ratio: Decimal | None
    last_session_truth_trend_trailing_negative_bucket_streak: int
    last_session_truth_trend_recent_bucket_net_realized_bps: Decimal | None
    last_session_truth_trend_cumulative_drawdown_usdt: Decimal | None
    last_economics_regime_action: str
    last_economics_regime_size_multiplier: Decimal | None
    last_economics_regime_negative_day_ratio: Decimal | None
    last_economics_regime_recent_day_net_realized_bps: Decimal | None
    last_economics_regime_average_maker_ratio: Decimal | None
    last_economics_feedback_multiplier: Decimal | None
    last_economics_feedback_total_penalty: Decimal | None
    last_economics_feedback_reason: str
    last_combined_protection_action: str
    last_combined_protection_size_multiplier: Decimal | None
    last_combined_protection_cooldown_until_ms: int
    last_pnl_session_loss_usdt: Decimal | None
    last_pnl_drawdown_usdt: Decimal | None
    last_pnl_unrealized_loss_usdt: Decimal | None


def _average(total: Decimal, count: int) -> Decimal | None:
    if count <= 0:
        return None
    return total / Decimal(count)


def build_live_execution_quality_report(status: LiveBreakoutStatus) -> LiveExecutionQualityReport:
    duration_ms = max(0, status.session_last_update_at_ms - status.session_started_at_ms)
    return LiveExecutionQualityReport(
        session_started_at_ms=status.session_started_at_ms,
        session_last_update_at_ms=status.session_last_update_at_ms,
        session_duration_ms=duration_ms,
        market_messages=status.market_messages,
        entry_attempts=status.entry_attempts,
        entries_sent=status.entries_sent,
        entries_rejected=status.entries_rejected,
        entry_unknown_submissions=status.entry_unknown_submissions,
        stale_cancels=status.stale_cancels,
        exit_brackets_armed=status.exit_brackets_armed,
        exit_cancels=status.exit_cancels,
        targeted_queries=status.targeted_queries,
        reconnects=status.reconnects,
        average_target_notional_usdt=_average(status.target_notional_sum, status.notional_decision_count),
        average_notional_multiplier=_average(status.notional_multiplier_sum, status.notional_decision_count),
        average_volatility_multiplier=_average(status.volatility_multiplier_sum, status.volatility_decision_count),
        average_economics_feedback_multiplier=_average(
            status.economics_feedback_multiplier_sum,
            status.economics_feedback_decision_count,
        ),
        average_expected_fill_ratio=_average(status.expected_fill_ratio_sum, status.queue_decision_count),
        average_queue_clear_seconds=_average(status.queue_clear_seconds_sum, status.queue_clear_seconds_count),
        average_queue_ahead_ratio=_average(status.queue_ahead_ratio_sum, status.queue_ahead_ratio_count),
        average_directional_queue_flow_qty_per_second=_average(
            status.directional_queue_flow_rate_sum,
            status.queue_decision_count,
        ),
        average_realized_entry_fill_ratio=_average(
            status.realized_entry_fill_ratio_sum,
            status.entry_outcome_count,
        ),
        average_entry_fill_ratio_shortfall=_average(
            status.entry_fill_ratio_shortfall_sum,
            status.entry_fill_ratio_shortfall_count,
        ),
        average_entry_fill_latency_seconds=_average(
            status.entry_fill_latency_seconds_sum,
            status.entry_fill_latency_count,
        ),
        average_entry_fill_latency_overshoot_seconds=_average(
            status.entry_fill_latency_overshoot_seconds_sum,
            status.entry_fill_latency_overshoot_count,
        ),
        entry_timeout_rate=_average(Decimal(status.entry_timeout_count), status.entry_outcome_count),
        average_exit_depth_sweep_bps=_average(status.exit_depth_sweep_bps_sum, status.exit_depth_estimate_count),
        average_exit_depth_coverage_ratio=_average(
            status.exit_depth_coverage_ratio_sum,
            status.exit_depth_estimate_count,
        ),
        average_exit_depth_levels_consumed=_average(
            status.exit_depth_levels_consumed_sum,
            status.exit_depth_estimate_count,
        ),
        average_exit_synthetic_tail_coverage_ratio=_average(
            status.exit_synthetic_tail_coverage_ratio_sum,
            status.exit_depth_estimate_count,
        ),
        average_exit_synthetic_tail_levels_consumed=_average(
            status.exit_synthetic_tail_levels_consumed_sum,
            status.exit_depth_estimate_count,
        ),
        average_exit_terminal_tail_ratio=_average(
            status.exit_terminal_tail_ratio_sum,
            status.exit_depth_estimate_count,
        ),
        signal_gate_rejections=status.signal_gate_rejections,
        crowding_gate_rejections=status.crowding_gate_rejections,
        router_breakout_signal_count=status.router_breakout_signal_count,
        router_reversion_signal_count=status.router_reversion_signal_count,
        router_fallback_signal_count=status.router_fallback_signal_count,
        last_router_regime=status.last_router_regime,
        last_router_selected_strategy_kind=status.last_router_selected_strategy_kind,
        last_router_preferred_strategy_kind=status.last_router_preferred_strategy_kind,
        ensemble_breakout_signal_count=status.ensemble_breakout_signal_count,
        ensemble_reversion_signal_count=status.ensemble_reversion_signal_count,
        ensemble_override_signal_count=status.ensemble_override_signal_count,
        last_ensemble_regime=status.last_ensemble_regime,
        last_ensemble_selected_strategy_kind=status.last_ensemble_selected_strategy_kind,
        last_ensemble_preferred_strategy_kind=status.last_ensemble_preferred_strategy_kind,
        last_ensemble_breakout_score=(Decimal(status.last_ensemble_breakout_score) if status.last_ensemble_breakout_score not in {'', None} else None),
        last_ensemble_reversion_score=(Decimal(status.last_ensemble_reversion_score) if status.last_ensemble_reversion_score not in {'', None} else None),
        book_gate_rejections=status.book_gate_rejections,
        depth_gate_rejections=status.depth_gate_rejections,
        last_depth_source=status.last_depth_source,
        last_rpi_depth_age_ms=status.last_rpi_depth_age_ms,
        last_rpi_depth_levels=status.last_rpi_depth_levels,
        queue_gate_rejections=status.queue_gate_rejections,
        depth_liquidity_gate_rejections=status.depth_liquidity_gate_rejections,
        sizing_abstentions=status.sizing_abstentions,
        execution_drift_reduce_size_applications=status.execution_drift_reduce_size_applications,
        execution_drift_observe_rejections=status.execution_drift_observe_rejections,
        intraday_protection_reduce_size_applications=status.intraday_protection_reduce_size_applications,
        intraday_protection_observe_rejections=status.intraday_protection_observe_rejections,
        pnl_protection_reduce_size_applications=status.pnl_protection_reduce_size_applications,
        pnl_protection_observe_rejections=status.pnl_protection_observe_rejections,
        trade_reconciliation_reduce_size_applications=status.trade_reconciliation_reduce_size_applications,
        trade_reconciliation_observe_rejections=status.trade_reconciliation_observe_rejections,
        session_truth_reduce_size_applications=status.session_truth_reduce_size_applications,
        session_truth_observe_rejections=status.session_truth_observe_rejections,
        session_truth_trend_reduce_size_applications=status.session_truth_trend_reduce_size_applications,
        session_truth_trend_observe_rejections=status.session_truth_trend_observe_rejections,
        economics_regime_reduce_size_applications=status.economics_regime_reduce_size_applications,
        economics_regime_observe_rejections=status.economics_regime_observe_rejections,
        combined_protection_reduce_size_applications=status.combined_protection_reduce_size_applications,
        combined_protection_observe_rejections=status.combined_protection_observe_rejections,
        last_execution_drift_action=status.last_execution_drift_action,
        last_execution_drift_size_multiplier=(
            Decimal(status.last_execution_drift_size_multiplier)
            if status.last_execution_drift_size_multiplier not in {'', None}
            else None
        ),
        last_intraday_protection_action=status.last_intraday_protection_action,
        last_intraday_protection_size_multiplier=(
            Decimal(status.last_intraday_protection_size_multiplier)
            if status.last_intraday_protection_size_multiplier not in {'', None}
            else None
        ),
        last_pnl_protection_action=status.last_pnl_protection_action,
        last_pnl_protection_size_multiplier=(
            Decimal(status.last_pnl_protection_size_multiplier)
            if status.last_pnl_protection_size_multiplier not in {'', None}
            else None
        ),
        last_trade_reconciliation_action=status.last_trade_reconciliation_action,
        last_trade_reconciliation_size_multiplier=(
            Decimal(status.last_trade_reconciliation_size_multiplier)
            if status.last_trade_reconciliation_size_multiplier not in {'', None}
            else None
        ),
        last_trade_reconciliation_window_mode=status.last_trade_reconciliation_window_mode,
        last_trade_reconciliation_session_started_at_ms=status.last_trade_reconciliation_session_started_at_ms,
        last_trade_reconciliation_missing_local_trade_ratio=(
            Decimal(status.last_trade_reconciliation_missing_local_trade_ratio)
            if status.last_trade_reconciliation_missing_local_trade_ratio not in {'', None}
            else None
        ),
        last_trade_reconciliation_missing_local_order_ratio=(
            Decimal(status.last_trade_reconciliation_missing_local_order_ratio)
            if status.last_trade_reconciliation_missing_local_order_ratio not in {'', None}
            else None
        ),
        last_trade_reconciliation_realized_pnl_diff_usdt=(
            Decimal(status.last_trade_reconciliation_realized_pnl_diff_usdt)
            if status.last_trade_reconciliation_realized_pnl_diff_usdt not in {'', None}
            else None
        ),
        last_trade_reconciliation_commission_abs_diff_usdt=(
            Decimal(status.last_trade_reconciliation_commission_abs_diff_usdt)
            if status.last_trade_reconciliation_commission_abs_diff_usdt not in {'', None}
            else None
        ),
        last_trade_reconciliation_quote_qty_abs_diff_usdt=(
            Decimal(status.last_trade_reconciliation_quote_qty_abs_diff_usdt)
            if status.last_trade_reconciliation_quote_qty_abs_diff_usdt not in {'', None}
            else None
        ),
        last_trade_reconciliation_income_trade_link_gap_ratio=(
            Decimal(status.last_trade_reconciliation_income_trade_link_gap_ratio)
            if status.last_trade_reconciliation_income_trade_link_gap_ratio not in {'', None}
            else None
        ),
        last_session_truth_action=status.last_session_truth_action,
        last_session_truth_size_multiplier=(
            Decimal(status.last_session_truth_size_multiplier)
            if status.last_session_truth_size_multiplier not in {'', None}
            else None
        ),
        last_session_truth_window_mode=status.last_session_truth_window_mode,
        last_session_truth_session_started_at_ms=status.last_session_truth_session_started_at_ms,
        last_session_truth_net_realized_pnl_usdt=(
            Decimal(status.last_session_truth_net_realized_pnl_usdt)
            if status.last_session_truth_net_realized_pnl_usdt not in {'', None}
            else None
        ),
        last_session_truth_net_realized_bps=(
            Decimal(status.last_session_truth_net_realized_bps)
            if status.last_session_truth_net_realized_bps not in {'', None}
            else None
        ),
        last_session_truth_maker_ratio=(
            Decimal(status.last_session_truth_maker_ratio)
            if status.last_session_truth_maker_ratio not in {'', None}
            else None
        ),
        last_session_truth_trend_action=status.last_session_truth_trend_action,
        last_session_truth_trend_size_multiplier=(
            Decimal(status.last_session_truth_trend_size_multiplier)
            if status.last_session_truth_trend_size_multiplier not in {'', None}
            else None
        ),
        last_session_truth_trend_active_bucket_count=status.last_session_truth_trend_active_bucket_count,
        last_session_truth_trend_negative_bucket_ratio=(
            Decimal(status.last_session_truth_trend_negative_bucket_ratio)
            if status.last_session_truth_trend_negative_bucket_ratio not in {'', None}
            else None
        ),
        last_session_truth_trend_trailing_negative_bucket_streak=status.last_session_truth_trend_trailing_negative_bucket_streak,
        last_session_truth_trend_recent_bucket_net_realized_bps=(
            Decimal(status.last_session_truth_trend_recent_bucket_net_realized_bps)
            if status.last_session_truth_trend_recent_bucket_net_realized_bps not in {'', None}
            else None
        ),
        last_session_truth_trend_cumulative_drawdown_usdt=(
            Decimal(status.last_session_truth_trend_cumulative_drawdown_usdt)
            if status.last_session_truth_trend_cumulative_drawdown_usdt not in {'', None}
            else None
        ),
        last_economics_regime_action=status.last_economics_regime_action,
        last_economics_regime_size_multiplier=(
            Decimal(status.last_economics_regime_size_multiplier)
            if status.last_economics_regime_size_multiplier not in {'', None}
            else None
        ),
        last_economics_regime_negative_day_ratio=(
            Decimal(status.last_economics_regime_negative_day_ratio)
            if status.last_economics_regime_negative_day_ratio not in {'', None}
            else None
        ),
        last_economics_regime_recent_day_net_realized_bps=(
            Decimal(status.last_economics_regime_recent_day_net_realized_bps)
            if status.last_economics_regime_recent_day_net_realized_bps not in {'', None}
            else None
        ),
        last_economics_regime_average_maker_ratio=(
            Decimal(status.last_economics_regime_average_maker_ratio)
            if status.last_economics_regime_average_maker_ratio not in {'', None}
            else None
        ),
        last_economics_feedback_multiplier=(
            Decimal(status.last_economics_feedback_multiplier)
            if status.last_economics_feedback_multiplier not in {'', None}
            else None
        ),
        last_economics_feedback_total_penalty=(
            Decimal(status.last_economics_feedback_total_penalty)
            if status.last_economics_feedback_total_penalty not in {'', None}
            else None
        ),
        last_economics_feedback_reason=status.last_economics_feedback_reason,
        last_combined_protection_action=status.last_combined_protection_action,
        last_combined_protection_size_multiplier=(
            Decimal(status.last_combined_protection_size_multiplier)
            if status.last_combined_protection_size_multiplier not in {'', None}
            else None
        ),
        last_combined_protection_cooldown_until_ms=status.last_combined_protection_cooldown_until_ms,
        last_pnl_session_loss_usdt=(
            Decimal(status.last_pnl_session_loss_usdt)
            if status.last_pnl_session_loss_usdt not in {'', None}
            else None
        ),
        last_pnl_drawdown_usdt=(
            Decimal(status.last_pnl_drawdown_usdt)
            if status.last_pnl_drawdown_usdt not in {'', None}
            else None
        ),
        last_pnl_unrealized_loss_usdt=(
            Decimal(status.last_pnl_unrealized_loss_usdt)
            if status.last_pnl_unrealized_loss_usdt not in {'', None}
            else None
        ),
    )

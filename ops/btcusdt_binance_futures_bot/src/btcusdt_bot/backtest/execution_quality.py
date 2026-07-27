from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from btcusdt_bot.backtest.engine import BacktestReport


@dataclass(slots=True)
class ExecutionQualityReport:
    average_entry_notional: Decimal
    average_notional_multiplier: Decimal
    average_economics_feedback_multiplier: Decimal | None
    economics_regime_reduce_size_applications: int
    economics_regime_observe_rejections: int
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
    average_expected_fill_ratio: Decimal | None
    average_queue_clear_seconds: Decimal | None
    average_queue_ahead_ratio: Decimal | None
    average_directional_queue_flow_qty_per_second: Decimal | None
    average_realized_entry_fill_ratio: Decimal | None
    average_entry_fill_ratio_shortfall: Decimal | None
    average_entry_fill_latency_seconds: Decimal | None
    average_entry_fill_latency_overshoot_seconds: Decimal | None
    entry_timeout_rate: Decimal | None
    modeled_partial_entry_count: int
    modeled_partial_entry_qty: Decimal
    entry_remainder_cancel_count: int
    unmodeled_partial_entry_count: int
    unmodeled_partial_entry_qty: Decimal
    last_entry_completion_reason: str
    promotion_blocked_by_partial_fills: bool
    execution_fidelity_status: str
    average_exit_depth_sweep_bps: Decimal | None
    average_exit_depth_coverage_ratio: Decimal | None
    average_exit_depth_levels_consumed: Decimal | None
    average_exit_synthetic_tail_coverage_ratio: Decimal | None
    average_exit_synthetic_tail_levels_consumed: Decimal | None
    average_exit_terminal_tail_ratio: Decimal | None
    last_exit_pricing_source: str
    last_exit_pricing_fallback_reason: str
    last_exit_depth_age_ms: int | None
    last_exit_book_age_ms: int | None
    exit_depth_pricing_count: int
    exit_book_pricing_count: int
    exit_mark_pricing_count: int
    exit_depth_fallback_count: int
    exit_book_fallback_count: int
    book_gate_rejections: int
    depth_gate_rejections: int
    last_depth_source: str
    last_rpi_depth_age_ms: int | None
    last_rpi_depth_levels: int
    queue_gate_rejections: int
    depth_liquidity_gate_rejections: int
    adaptive_abstentions: int


def build_execution_quality_report(report: BacktestReport) -> ExecutionQualityReport:
    return ExecutionQualityReport(
        average_entry_notional=report.average_entry_notional,
        average_notional_multiplier=report.average_notional_multiplier,
        average_economics_feedback_multiplier=report.average_economics_feedback_multiplier,
        economics_regime_reduce_size_applications=report.economics_regime_reduce_size_applications,
        economics_regime_observe_rejections=report.economics_regime_observe_rejections,
        router_breakout_signal_count=report.router_breakout_signal_count,
        router_reversion_signal_count=report.router_reversion_signal_count,
        router_fallback_signal_count=report.router_fallback_signal_count,
        last_router_regime=report.last_router_regime,
        last_router_selected_strategy_kind=report.last_router_selected_strategy_kind,
        last_router_preferred_strategy_kind=report.last_router_preferred_strategy_kind,
        ensemble_breakout_signal_count=report.ensemble_breakout_signal_count,
        ensemble_reversion_signal_count=report.ensemble_reversion_signal_count,
        ensemble_override_signal_count=report.ensemble_override_signal_count,
        last_ensemble_regime=report.last_ensemble_regime,
        last_ensemble_selected_strategy_kind=report.last_ensemble_selected_strategy_kind,
        last_ensemble_preferred_strategy_kind=report.last_ensemble_preferred_strategy_kind,
        last_ensemble_breakout_score=report.last_ensemble_breakout_score,
        last_ensemble_reversion_score=report.last_ensemble_reversion_score,
        average_expected_fill_ratio=report.average_expected_fill_ratio,
        average_queue_clear_seconds=report.average_queue_clear_seconds,
        average_queue_ahead_ratio=report.average_queue_ahead_ratio,
        average_directional_queue_flow_qty_per_second=report.average_directional_queue_flow_qty_per_second,
        average_realized_entry_fill_ratio=report.average_realized_entry_fill_ratio,
        average_entry_fill_ratio_shortfall=report.average_entry_fill_ratio_shortfall,
        average_entry_fill_latency_seconds=report.average_entry_fill_latency_seconds,
        average_entry_fill_latency_overshoot_seconds=report.average_entry_fill_latency_overshoot_seconds,
        entry_timeout_rate=report.entry_timeout_rate,
        modeled_partial_entry_count=report.modeled_partial_entry_count,
        modeled_partial_entry_qty=report.modeled_partial_entry_qty,
        entry_remainder_cancel_count=report.entry_remainder_cancel_count,
        unmodeled_partial_entry_count=report.unmodeled_partial_entry_count,
        unmodeled_partial_entry_qty=report.unmodeled_partial_entry_qty,
        last_entry_completion_reason=report.last_entry_completion_reason,
        promotion_blocked_by_partial_fills=report.promotion_blocked_by_partial_fills,
        execution_fidelity_status=report.execution_fidelity_status,
        average_exit_depth_sweep_bps=report.average_exit_depth_sweep_bps,
        average_exit_depth_coverage_ratio=report.average_exit_depth_coverage_ratio,
        average_exit_depth_levels_consumed=report.average_exit_depth_levels_consumed,
        average_exit_synthetic_tail_coverage_ratio=report.average_exit_synthetic_tail_coverage_ratio,
        average_exit_synthetic_tail_levels_consumed=report.average_exit_synthetic_tail_levels_consumed,
        average_exit_terminal_tail_ratio=report.average_exit_terminal_tail_ratio,
        last_exit_pricing_source=report.last_exit_pricing_source,
        last_exit_pricing_fallback_reason=report.last_exit_pricing_fallback_reason,
        last_exit_depth_age_ms=report.last_exit_depth_age_ms,
        last_exit_book_age_ms=report.last_exit_book_age_ms,
        exit_depth_pricing_count=report.exit_depth_pricing_count,
        exit_book_pricing_count=report.exit_book_pricing_count,
        exit_mark_pricing_count=report.exit_mark_pricing_count,
        exit_depth_fallback_count=report.exit_depth_fallback_count,
        exit_book_fallback_count=report.exit_book_fallback_count,
        book_gate_rejections=report.book_gate_rejections,
        depth_gate_rejections=report.depth_gate_rejections,
        last_depth_source=report.last_depth_source,
        last_rpi_depth_age_ms=report.last_rpi_depth_age_ms,
        last_rpi_depth_levels=report.last_rpi_depth_levels,
        queue_gate_rejections=report.queue_gate_rejections,
        depth_liquidity_gate_rejections=report.depth_liquidity_gate_rejections,
        adaptive_abstentions=report.adaptive_abstentions,
    )

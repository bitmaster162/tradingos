from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from btcusdt_bot.domain.models import RiskDecision


@dataclass(slots=True)
class RiskContext:
    mark_price: Decimal
    current_position_qty: Decimal
    current_leverage: Decimal
    realized_pnl_today: Decimal
    open_normal_orders: int
    open_algo_orders: int
    last_market_data_age_ms: int
    reconcile_age_ms: int | None = None
    reconcile_mismatch_count: int | None = None
    reconcile_required: bool = False
    quantitative_lock: bool = False
    cooling_off: bool = False
    emergency_only: bool = False


@dataclass(slots=True)
class RiskLimits:
    max_leverage: Decimal = Decimal("3")
    max_position_notional: Decimal = Decimal("500")
    max_daily_loss: Decimal = Decimal("50")
    max_normal_open_orders: int = 8
    max_algo_open_orders: int = 20
    stale_data_limit_ms: int = 4000
    stale_reconcile_limit_ms: int | None = None

    def evaluate_new_entry(self, *, proposed_qty: Decimal, ctx: RiskContext) -> RiskDecision:
        hard_reasons: list[str] = []
        soft_warnings: list[str] = []

        current_notional = abs(ctx.current_position_qty) * ctx.mark_price
        proposal_notional = abs(proposed_qty) * ctx.mark_price
        combined_notional = current_notional + proposal_notional

        if ctx.quantitative_lock:
            hard_reasons.append("quantitative_rule_lock")

        if ctx.cooling_off:
            hard_reasons.append("exchange_cooling_off")

        if ctx.emergency_only:
            hard_reasons.append("emergency_only_mode")

        if ctx.last_market_data_age_ms > self.stale_data_limit_ms:
            hard_reasons.append("stale_market_data")

        if ctx.reconcile_required:
            if self.stale_reconcile_limit_ms is None:
                hard_reasons.append("reconcile_freshness_limit_missing")
            if ctx.reconcile_age_ms is None:
                hard_reasons.append("reconcile_state_missing")
            elif ctx.reconcile_age_ms < 0:
                hard_reasons.append("reconcile_timestamp_in_future")
            elif (
                self.stale_reconcile_limit_ms is not None
                and ctx.reconcile_age_ms > self.stale_reconcile_limit_ms
            ):
                hard_reasons.append("stale_reconcile_state")

            if ctx.reconcile_mismatch_count is None:
                hard_reasons.append("reconcile_health_missing")
            elif ctx.reconcile_mismatch_count > 0:
                hard_reasons.append("reconcile_state_divergence")

        if ctx.current_leverage > self.max_leverage:
            hard_reasons.append("current_leverage_above_limit")

        if combined_notional > self.max_position_notional:
            hard_reasons.append("position_notional_limit")

        if ctx.realized_pnl_today <= -self.max_daily_loss:
            hard_reasons.append("daily_loss_limit")

        if ctx.open_normal_orders >= self.max_normal_open_orders:
            hard_reasons.append("too_many_normal_orders")

        if ctx.open_algo_orders >= self.max_algo_open_orders:
            hard_reasons.append("too_many_algo_orders")

        if combined_notional > self.max_position_notional * Decimal("0.8"):
            soft_warnings.append("position_notional_above_80pct")

        return RiskDecision(
            allow_new_entry=not hard_reasons,
            allow_reduce_only=True,
            hard_reasons=hard_reasons,
            soft_warnings=soft_warnings,
        )

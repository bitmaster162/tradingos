from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from btcusdt_bot.reporting.economics_dashboard import EconomicsDashboard

_ZERO = Decimal("0")
_ONE = Decimal("1")


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


@dataclass(slots=True)
class EconomicsFeedbackConfig:
    enabled: bool = True
    min_active_day_count: int = 3
    min_multiplier: Decimal = Decimal("0.70")
    negative_day_ratio_weight: Decimal = Decimal("0.30")
    recent_day_loss_weight: Decimal = Decimal("0.20")
    recent_two_day_loss_weight: Decimal = Decimal("0.20")
    maker_ratio_weight: Decimal = Decimal("0.15")
    commission_weight: Decimal = Decimal("0.10")
    funding_weight: Decimal = Decimal("0.05")
    negative_day_ratio_soft: Decimal = Decimal("0.34")
    negative_day_ratio_cap: Decimal = Decimal("1.00")
    recent_day_loss_bps_soft: Decimal = Decimal("0.50")
    recent_day_loss_bps_cap: Decimal = Decimal("4.00")
    recent_two_day_loss_bps_soft: Decimal = Decimal("0.25")
    recent_two_day_loss_bps_cap: Decimal = Decimal("3.00")
    maker_ratio_floor: Decimal = Decimal("0.45")
    commission_bps_soft: Decimal = Decimal("4.00")
    commission_bps_cap: Decimal = Decimal("10.00")
    negative_funding_bps_soft: Decimal = Decimal("0.25")
    negative_funding_bps_cap: Decimal = Decimal("2.00")


@dataclass(slots=True)
class EconomicsFeedbackDecision:
    applied: bool
    multiplier: Decimal
    total_penalty: Decimal = _ZERO
    negative_day_ratio_penalty: Decimal = _ZERO
    recent_day_loss_penalty: Decimal = _ZERO
    recent_two_day_loss_penalty: Decimal = _ZERO
    maker_ratio_penalty: Decimal = _ZERO
    commission_penalty: Decimal = _ZERO
    funding_penalty: Decimal = _ZERO
    reason: str = ""


class EconomicsFeedbackPolicy:
    def __init__(self, config: EconomicsFeedbackConfig | None = None) -> None:
        self.config = config or EconomicsFeedbackConfig()

    def evaluate(self, dashboard: EconomicsDashboard | None) -> EconomicsFeedbackDecision:
        if not self.config.enabled:
            return EconomicsFeedbackDecision(applied=False, multiplier=_ONE, reason="disabled")
        if dashboard is None:
            return EconomicsFeedbackDecision(applied=False, multiplier=_ONE, reason="missing_dashboard")
        if dashboard.active_day_count < self.config.min_active_day_count:
            return EconomicsFeedbackDecision(applied=False, multiplier=_ONE, reason="insufficient_sample")

        negative_day_ratio_penalty = self._ratio_penalty(
            value=dashboard.negative_day_ratio,
            soft=self.config.negative_day_ratio_soft,
            cap=self.config.negative_day_ratio_cap,
        )
        recent_day_loss_penalty = self._loss_penalty(
            value_bps=dashboard.recent_day_net_realized_bps,
            soft_loss_bps=self.config.recent_day_loss_bps_soft,
            cap_loss_bps=self.config.recent_day_loss_bps_cap,
        )
        recent_two_day_loss_penalty = self._loss_penalty(
            value_bps=dashboard.recent_two_day_net_realized_bps,
            soft_loss_bps=self.config.recent_two_day_loss_bps_soft,
            cap_loss_bps=self.config.recent_two_day_loss_bps_cap,
        )
        maker_ratio_penalty = self._maker_ratio_penalty(
            value=dashboard.average_maker_ratio,
            floor=self.config.maker_ratio_floor,
        )
        commission_penalty = self._ratio_penalty(
            value=dashboard.average_commission_bps,
            soft=self.config.commission_bps_soft,
            cap=self.config.commission_bps_cap,
        )
        funding_penalty = self._negative_value_penalty(
            value=dashboard.average_funding_bps,
            soft_negative=self.config.negative_funding_bps_soft,
            cap_negative=self.config.negative_funding_bps_cap,
        )

        total_penalty = (
            self.config.negative_day_ratio_weight * negative_day_ratio_penalty
            + self.config.recent_day_loss_weight * recent_day_loss_penalty
            + self.config.recent_two_day_loss_weight * recent_two_day_loss_penalty
            + self.config.maker_ratio_weight * maker_ratio_penalty
            + self.config.commission_weight * commission_penalty
            + self.config.funding_weight * funding_penalty
        )
        total_penalty = _clamp(total_penalty, _ZERO, _ONE)
        reduction_range = _clamp(_ONE - self.config.min_multiplier, _ZERO, _ONE)
        multiplier = _clamp(_ONE - reduction_range * total_penalty, self.config.min_multiplier, _ONE)
        return EconomicsFeedbackDecision(
            applied=True,
            multiplier=multiplier,
            total_penalty=total_penalty,
            negative_day_ratio_penalty=negative_day_ratio_penalty,
            recent_day_loss_penalty=recent_day_loss_penalty,
            recent_two_day_loss_penalty=recent_two_day_loss_penalty,
            maker_ratio_penalty=maker_ratio_penalty,
            commission_penalty=commission_penalty,
            funding_penalty=funding_penalty,
            reason="sample_ready",
        )

    @staticmethod
    def _ratio_penalty(*, value: Decimal, soft: Decimal, cap: Decimal) -> Decimal:
        if cap <= soft:
            return _ZERO
        if value <= soft:
            return _ZERO
        return _clamp((value - soft) / (cap - soft), _ZERO, _ONE)

    @staticmethod
    def _loss_penalty(*, value_bps: Decimal, soft_loss_bps: Decimal, cap_loss_bps: Decimal) -> Decimal:
        if cap_loss_bps <= soft_loss_bps:
            return _ZERO
        loss = max(_ZERO, -value_bps)
        if loss <= soft_loss_bps:
            return _ZERO
        return _clamp((loss - soft_loss_bps) / (cap_loss_bps - soft_loss_bps), _ZERO, _ONE)

    @staticmethod
    def _maker_ratio_penalty(*, value: Decimal, floor: Decimal) -> Decimal:
        if floor <= 0:
            return _ZERO
        if value >= floor:
            return _ZERO
        return _clamp((floor - value) / floor, _ZERO, _ONE)

    @staticmethod
    def _negative_value_penalty(*, value: Decimal, soft_negative: Decimal, cap_negative: Decimal) -> Decimal:
        if cap_negative <= soft_negative:
            return _ZERO
        negative_value = max(_ZERO, -value)
        if negative_value <= soft_negative:
            return _ZERO
        return _clamp((negative_value - soft_negative) / (cap_negative - soft_negative), _ZERO, _ONE)

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from btcusdt_bot.domain.enums import Side


_ZERO = Decimal("0")
_ONE = Decimal("1")


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


@dataclass(slots=True)
class AdaptiveEntryPolicyConfig:
    enabled: bool = True
    min_notional_multiplier: Decimal = Decimal("0.35")
    max_notional_multiplier: Decimal = Decimal("1.75")
    abstain_below_multiplier: Decimal = Decimal("0.50")
    min_effective_notional_usdt: Decimal = Decimal("25")
    flow_weight: Decimal = Decimal("0.60")
    crowding_weight: Decimal = Decimal("0.40")
    divergence_penalty_weight: Decimal = Decimal("0.25")
    funding_penalty_weight: Decimal = Decimal("0.15")
    divergence_penalty_cap_bps: Decimal = Decimal("3.0")
    funding_penalty_cap_rate: Decimal = Decimal("0.0005")
    max_signal_component: Decimal = Decimal("1.50")


@dataclass(slots=True)
class AdaptiveEntryInputs:
    side: Side
    base_notional_usdt: Decimal
    flow_imbalance: Decimal | None = None
    crowding_side_score: Decimal | None = None
    funding_rate: Decimal | None = None
    mark_trade_divergence_bps: Decimal | None = None


@dataclass(slots=True)
class AdaptiveEntryDecision:
    execute: bool
    base_notional_usdt: Decimal
    target_notional_usdt: Decimal
    multiplier: Decimal
    directional_flow_component: Decimal
    crowding_component: Decimal
    divergence_penalty: Decimal
    funding_penalty: Decimal
    reason: str = ""


class AdaptiveEntryPolicy:
    def __init__(self, config: AdaptiveEntryPolicyConfig | None = None) -> None:
        self.config = config or AdaptiveEntryPolicyConfig()

    def evaluate(self, inputs: AdaptiveEntryInputs) -> AdaptiveEntryDecision:
        base_notional = max(_ZERO, inputs.base_notional_usdt)
        if not self.config.enabled:
            return AdaptiveEntryDecision(
                execute=base_notional > 0,
                base_notional_usdt=base_notional,
                target_notional_usdt=base_notional,
                multiplier=_ONE,
                directional_flow_component=_ZERO,
                crowding_component=_ZERO,
                divergence_penalty=_ZERO,
                funding_penalty=_ZERO,
            )

        if base_notional <= 0:
            return AdaptiveEntryDecision(
                execute=False,
                base_notional_usdt=base_notional,
                target_notional_usdt=_ZERO,
                multiplier=_ZERO,
                directional_flow_component=_ZERO,
                crowding_component=_ZERO,
                divergence_penalty=_ZERO,
                funding_penalty=_ZERO,
                reason="non_positive_base_notional",
            )

        flow_component = self._directional_flow_component(inputs.side, inputs.flow_imbalance)
        crowding_component = self._crowding_component(inputs.crowding_side_score)
        divergence_penalty = self._divergence_penalty(inputs.mark_trade_divergence_bps)
        funding_penalty = self._funding_penalty(inputs.side, inputs.funding_rate)

        max_component = max(_ZERO, self.config.max_signal_component)
        flow_component = _clamp(flow_component, _ZERO, max_component)
        crowding_component = _clamp(crowding_component, _ZERO, max_component)
        divergence_penalty = _clamp(divergence_penalty, _ZERO, max_component)
        funding_penalty = _clamp(funding_penalty, _ZERO, max_component)

        raw_multiplier = (
            _ONE
            + self.config.flow_weight * flow_component
            + self.config.crowding_weight * crowding_component
            - self.config.divergence_penalty_weight * divergence_penalty
            - self.config.funding_penalty_weight * funding_penalty
        )
        multiplier = _clamp(
            raw_multiplier,
            max(_ZERO, self.config.min_notional_multiplier),
            max(self.config.min_notional_multiplier, self.config.max_notional_multiplier),
        )
        target_notional = base_notional * multiplier

        if multiplier < self.config.abstain_below_multiplier:
            return AdaptiveEntryDecision(
                execute=False,
                base_notional_usdt=base_notional,
                target_notional_usdt=target_notional,
                multiplier=multiplier,
                directional_flow_component=flow_component,
                crowding_component=crowding_component,
                divergence_penalty=divergence_penalty,
                funding_penalty=funding_penalty,
                reason="entry_quality_below_threshold",
            )

        if target_notional < self.config.min_effective_notional_usdt:
            return AdaptiveEntryDecision(
                execute=False,
                base_notional_usdt=base_notional,
                target_notional_usdt=target_notional,
                multiplier=multiplier,
                directional_flow_component=flow_component,
                crowding_component=crowding_component,
                divergence_penalty=divergence_penalty,
                funding_penalty=funding_penalty,
                reason="effective_notional_too_small",
            )

        return AdaptiveEntryDecision(
            execute=True,
            base_notional_usdt=base_notional,
            target_notional_usdt=target_notional,
            multiplier=multiplier,
            directional_flow_component=flow_component,
            crowding_component=crowding_component,
            divergence_penalty=divergence_penalty,
            funding_penalty=funding_penalty,
        )

    @staticmethod
    def _directional_flow_component(side: Side, flow_imbalance: Decimal | None) -> Decimal:
        if flow_imbalance is None:
            return _ZERO
        if side == Side.BUY:
            return max(_ZERO, flow_imbalance)
        return max(_ZERO, -flow_imbalance)

    @staticmethod
    def _crowding_component(crowding_side_score: Decimal | None) -> Decimal:
        if crowding_side_score is None:
            return _ZERO
        return max(_ZERO, crowding_side_score)

    def _divergence_penalty(self, divergence_bps: Decimal | None) -> Decimal:
        if divergence_bps is None or self.config.divergence_penalty_cap_bps <= 0:
            return _ZERO
        return _clamp(divergence_bps / self.config.divergence_penalty_cap_bps, _ZERO, _ONE)

    def _funding_penalty(self, side: Side, funding_rate: Decimal | None) -> Decimal:
        if funding_rate is None or self.config.funding_penalty_cap_rate <= 0:
            return _ZERO
        directional_funding = funding_rate if side == Side.BUY else -funding_rate
        if directional_funding <= 0:
            return _ZERO
        return _clamp(directional_funding / self.config.funding_penalty_cap_rate, _ZERO, _ONE)

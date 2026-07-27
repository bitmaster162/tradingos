from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


_ZERO = Decimal("0")
_ONE = Decimal("1")


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


@dataclass(slots=True)
class VolatilitySizingConfig:
    enabled: bool = True
    target_atr_fraction: Decimal = Decimal("0.0020")
    min_notional_multiplier: Decimal = Decimal("0.50")
    max_notional_multiplier: Decimal = Decimal("1.60")
    atr_fraction_floor: Decimal = Decimal("0.0002")
    abstain_above_atr_fraction: Decimal | None = Decimal("0.0080")


@dataclass(slots=True)
class VolatilitySizingInputs:
    base_notional_usdt: Decimal
    atr: Decimal
    reference_price: Decimal


@dataclass(slots=True)
class VolatilitySizingDecision:
    execute: bool
    base_notional_usdt: Decimal
    target_notional_usdt: Decimal
    multiplier: Decimal
    atr_fraction: Decimal
    reason: str = ""


class VolatilitySizingPolicy:
    def __init__(self, config: VolatilitySizingConfig | None = None) -> None:
        self.config = config or VolatilitySizingConfig()

    def evaluate(self, inputs: VolatilitySizingInputs) -> VolatilitySizingDecision:
        base_notional = max(_ZERO, inputs.base_notional_usdt)
        if not self.config.enabled:
            return VolatilitySizingDecision(
                execute=base_notional > 0,
                base_notional_usdt=base_notional,
                target_notional_usdt=base_notional,
                multiplier=_ONE,
                atr_fraction=_ZERO,
            )

        if base_notional <= 0:
            return VolatilitySizingDecision(
                execute=False,
                base_notional_usdt=base_notional,
                target_notional_usdt=_ZERO,
                multiplier=_ZERO,
                atr_fraction=_ZERO,
                reason="non_positive_base_notional",
            )

        if inputs.reference_price <= 0 or inputs.atr <= 0:
            return VolatilitySizingDecision(
                execute=False,
                base_notional_usdt=base_notional,
                target_notional_usdt=_ZERO,
                multiplier=_ZERO,
                atr_fraction=_ZERO,
                reason="invalid_volatility_inputs",
            )

        atr_fraction = inputs.atr / inputs.reference_price
        if (
            self.config.abstain_above_atr_fraction is not None
            and atr_fraction > self.config.abstain_above_atr_fraction
        ):
            return VolatilitySizingDecision(
                execute=False,
                base_notional_usdt=base_notional,
                target_notional_usdt=_ZERO,
                multiplier=_ZERO,
                atr_fraction=atr_fraction,
                reason="atr_fraction_too_high",
            )

        effective_atr_fraction = max(self.config.atr_fraction_floor, atr_fraction)
        raw_multiplier = self.config.target_atr_fraction / effective_atr_fraction
        multiplier = _clamp(
            raw_multiplier,
            max(_ZERO, self.config.min_notional_multiplier),
            max(self.config.min_notional_multiplier, self.config.max_notional_multiplier),
        )
        target_notional = base_notional * multiplier
        return VolatilitySizingDecision(
            execute=True,
            base_notional_usdt=base_notional,
            target_notional_usdt=target_notional,
            multiplier=multiplier,
            atr_fraction=atr_fraction,
        )

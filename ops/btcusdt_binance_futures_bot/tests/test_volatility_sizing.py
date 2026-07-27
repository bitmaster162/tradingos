from decimal import Decimal

from btcusdt_bot.sizing.volatility import VolatilitySizingInputs, VolatilitySizingPolicy


def test_volatility_sizing_scales_down_when_atr_fraction_is_high() -> None:
    policy = VolatilitySizingPolicy()

    decision = policy.evaluate(
        VolatilitySizingInputs(
            base_notional_usdt=Decimal("100"),
            atr=Decimal("4"),
            reference_price=Decimal("1000"),
        )
    )

    assert decision.execute is True
    assert decision.multiplier < Decimal("1")
    assert decision.target_notional_usdt < Decimal("100")


def test_volatility_sizing_can_abstain_above_cap() -> None:
    policy = VolatilitySizingPolicy()

    decision = policy.evaluate(
        VolatilitySizingInputs(
            base_notional_usdt=Decimal("100"),
            atr=Decimal("12"),
            reference_price=Decimal("1000"),
        )
    )

    assert decision.execute is False
    assert decision.reason == "atr_fraction_too_high"

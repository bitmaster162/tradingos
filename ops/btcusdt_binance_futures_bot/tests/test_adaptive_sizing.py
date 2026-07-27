from decimal import Decimal

from btcusdt_bot.domain.enums import Side
from btcusdt_bot.sizing.policy import AdaptiveEntryInputs, AdaptiveEntryPolicy, AdaptiveEntryPolicyConfig


def test_adaptive_entry_policy_scales_up_when_flow_and_crowding_support_trade() -> None:
    policy = AdaptiveEntryPolicy()

    decision = policy.evaluate(
        AdaptiveEntryInputs(
            side=Side.BUY,
            base_notional_usdt=Decimal("100"),
            flow_imbalance=Decimal("0.80"),
            crowding_side_score=Decimal("0.50"),
            funding_rate=Decimal("0.0001"),
            mark_trade_divergence_bps=Decimal("0.20"),
        )
    )

    assert decision.execute is True
    assert decision.multiplier > Decimal("1")
    assert decision.target_notional_usdt > Decimal("100")
    assert decision.directional_flow_component > Decimal("0")
    assert decision.crowding_component > Decimal("0")


def test_adaptive_entry_policy_can_abstain_when_penalties_dominate() -> None:
    policy = AdaptiveEntryPolicy(
        AdaptiveEntryPolicyConfig(
            min_notional_multiplier=Decimal("0.10"),
            max_notional_multiplier=Decimal("1.75"),
            abstain_below_multiplier=Decimal("0.75"),
            min_effective_notional_usdt=Decimal("25"),
        )
    )

    decision = policy.evaluate(
        AdaptiveEntryInputs(
            side=Side.BUY,
            base_notional_usdt=Decimal("100"),
            flow_imbalance=Decimal("0"),
            crowding_side_score=Decimal("0"),
            funding_rate=Decimal("0.0005"),
            mark_trade_divergence_bps=Decimal("3.0"),
        )
    )

    assert decision.execute is False
    assert decision.multiplier < Decimal("0.75")
    assert decision.reason == "entry_quality_below_threshold"

from decimal import Decimal

from btcusdt_bot.reporting.economics_dashboard import EconomicsDashboard
from btcusdt_bot.sizing.economics_feedback import EconomicsFeedbackPolicy



def test_economics_feedback_reduces_multiplier_on_soft_degradation() -> None:
    dashboard = EconomicsDashboard(
        symbol="BTCUSDT",
        start_date="2026-04-01",
        end_date="2026-04-07",
        lookback_days=7,
        active_day_count=5,
        negative_day_count=3,
        negative_day_ratio=Decimal("0.60"),
        recent_day_net_realized_bps=Decimal("-2.0"),
        recent_two_day_net_realized_bps=Decimal("-1.25"),
        average_maker_ratio=Decimal("0.20"),
        average_commission_bps=Decimal("8.0"),
        average_funding_bps=Decimal("-1.0"),
    )
    policy = EconomicsFeedbackPolicy()

    decision = policy.evaluate(dashboard)

    assert decision.applied is True
    assert Decimal("0.70") <= decision.multiplier < Decimal("1")
    assert decision.total_penalty > 0
    assert decision.reason == "sample_ready"



def test_economics_feedback_skips_without_sample() -> None:
    dashboard = EconomicsDashboard(
        symbol="BTCUSDT",
        start_date="2026-04-06",
        end_date="2026-04-07",
        lookback_days=2,
        active_day_count=1,
    )
    policy = EconomicsFeedbackPolicy()

    decision = policy.evaluate(dashboard)

    assert decision.applied is False
    assert decision.multiplier == Decimal("1")
    assert decision.reason == "insufficient_sample"

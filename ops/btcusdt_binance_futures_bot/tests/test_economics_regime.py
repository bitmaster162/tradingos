from decimal import Decimal

from btcusdt_bot.monitoring.economics_regime import EconomicsRegimeThresholds, evaluate_economics_regime
from btcusdt_bot.reporting.economics_dashboard import EconomicsDashboard



def test_evaluate_economics_regime_observes_on_multi_day_economic_breakdown() -> None:
    dashboard = EconomicsDashboard(
        symbol="BTCUSDT",
        start_date="2026-04-01",
        end_date="2026-04-07",
        lookback_days=7,
        available_day_count=7,
        active_day_count=7,
        negative_day_count=6,
        negative_day_ratio=Decimal("0.857"),
        trailing_negative_day_streak=4,
        recent_day_net_realized_bps=Decimal("-3.5"),
        recent_two_day_net_realized_bps=Decimal("-3.0"),
        average_maker_ratio=Decimal("0.10"),
        average_commission_bps=Decimal("12.0"),
        average_funding_bps=Decimal("-2.5"),
        average_negative_bucket_ratio=Decimal("0.80"),
        cumulative_drawdown_usdt=Decimal("30.0"),
        aggregate_net_realized_bps=Decimal("-5.0"),
        total_net_realized_pnl_usdt=Decimal("-25.0"),
    )

    decision = evaluate_economics_regime(
        dashboard=dashboard,
        thresholds=EconomicsRegimeThresholds(),
        compared_at_ms=1_700_000_000_000,
    )

    assert decision.action == "observe_only"
    assert decision.sample_ready is True
    assert decision.negative_day_ratio == Decimal("0.857")
    assert "negative_day_ratio_above_observe_threshold" in decision.reasons
    assert "average_maker_ratio_below_observe_threshold" in decision.reasons
    assert decision.severe_breaches >= 3

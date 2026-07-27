from decimal import Decimal

from btcusdt_bot.monitoring.session_truth_trend import SessionTruthTrendThresholds, evaluate_session_truth_trend
from btcusdt_bot.reporting.session_truth_report import SessionTruthReport



def test_session_truth_trend_observe_only_on_severe_recent_deterioration() -> None:
    report = SessionTruthReport(
        compared_at_ms=1,
        lookback_start_ms=0,
        lookback_end_ms=10,
        window_mode="session",
        session_started_at_ms=0,
        bucket_ms=60_000,
        bucket_count=4,
        active_bucket_count=4,
        negative_bucket_count=3,
        negative_bucket_ratio=Decimal("0.75"),
        trailing_negative_bucket_streak=3,
        recent_bucket_net_realized_bps=Decimal("-4.0"),
        recent_two_bucket_net_realized_bps=Decimal("-3.5"),
        recent_bucket_maker_ratio=Decimal("0.10"),
        worst_bucket_net_realized_bps=Decimal("-9.0"),
        cumulative_drawdown_usdt=Decimal("16.0"),
    )

    decision = evaluate_session_truth_trend(report=report, thresholds=SessionTruthTrendThresholds())

    assert decision.action == "observe_only"
    assert decision.sample_ready is True
    assert "negative_bucket_ratio_above_reduce_threshold" in decision.reasons
    assert "cumulative_drawdown_above_observe_threshold" in decision.reasons



def test_session_truth_trend_reduce_size_on_moderate_degradation() -> None:
    report = SessionTruthReport(
        compared_at_ms=1,
        lookback_start_ms=0,
        lookback_end_ms=10,
        bucket_ms=60_000,
        bucket_count=4,
        active_bucket_count=4,
        negative_bucket_count=2,
        negative_bucket_ratio=Decimal("0.50"),
        trailing_negative_bucket_streak=2,
        recent_bucket_net_realized_bps=Decimal("-1.5"),
        recent_two_bucket_net_realized_bps=Decimal("-0.90"),
        recent_bucket_maker_ratio=Decimal("0.30"),
        worst_bucket_net_realized_bps=Decimal("-4.0"),
        cumulative_drawdown_usdt=Decimal("6.0"),
    )

    decision = evaluate_session_truth_trend(report=report, thresholds=SessionTruthTrendThresholds())

    assert decision.action == "reduce_size"
    assert decision.size_multiplier == Decimal("0.60")
    assert decision.moderate_breaches >= 1

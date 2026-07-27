from decimal import Decimal

from btcusdt_bot.live_breakout import LiveBreakoutStatus
from btcusdt_bot.live_execution_quality import build_live_execution_quality_report



def test_live_execution_quality_report_includes_economics_feedback_fields() -> None:
    status = LiveBreakoutStatus(
        session_started_at_ms=1000,
        session_last_update_at_ms=2000,
        economics_feedback_decision_count=2,
        economics_feedback_multiplier_sum=Decimal("1.5"),
        economics_regime_reduce_size_applications=1,
        economics_regime_observe_rejections=2,
        last_economics_regime_action="reduce_size",
        last_economics_regime_size_multiplier="0.6",
        last_economics_regime_negative_day_ratio="0.50",
        last_economics_regime_recent_day_net_realized_bps="-1.5",
        last_economics_regime_average_maker_ratio="0.25",
        last_economics_feedback_multiplier="0.75",
        last_economics_feedback_total_penalty="0.50",
        last_economics_feedback_reason="sample_ready",
    )

    report = build_live_execution_quality_report(status)

    assert report.average_economics_feedback_multiplier == Decimal("0.75")
    assert report.economics_regime_reduce_size_applications == 1
    assert report.economics_regime_observe_rejections == 2
    assert report.last_economics_regime_action == "reduce_size"
    assert report.last_economics_feedback_multiplier == Decimal("0.75")
    assert report.last_economics_feedback_reason == "sample_ready"

from decimal import Decimal

from btcusdt_bot.monitoring.execution_drift import ExecutionBaseline, evaluate_execution_drift


def test_execution_drift_reduce_size_when_multiple_metrics_degrade() -> None:
    baseline = ExecutionBaseline(
        source="backtest",
        average_expected_fill_ratio=Decimal("0.60"),
        average_queue_clear_seconds=Decimal("1.0"),
        average_queue_ahead_ratio=Decimal("0.5"),
        average_exit_depth_sweep_bps=Decimal("1.5"),
        average_exit_depth_coverage_ratio=Decimal("0.90"),
        average_exit_terminal_tail_ratio=Decimal("0.01"),
    )

    live_payload = {
        "average_expected_fill_ratio": "0.45",
        "average_queue_clear_seconds": "1.8",
        "average_queue_ahead_ratio": "0.60",
        "average_exit_depth_sweep_bps": "2.0",
        "average_exit_depth_coverage_ratio": "0.82",
        "average_exit_terminal_tail_ratio": "0.02",
        "entry_attempts": 10,
        "entries_rejected": 1,
    }

    decision = evaluate_execution_drift(live_payload=live_payload, baseline=baseline, compared_at_ms=123)

    assert decision.action == "reduce_size"
    assert decision.size_multiplier == Decimal("0.50")
    assert decision.moderate_breaches == 2
    assert decision.severe_breaches == 0
    assert decision.compared_at_ms == 123
    assert decision.baseline_source == "backtest"


def test_execution_drift_observe_only_on_severe_fill_and_tail() -> None:
    baseline = ExecutionBaseline(
        source="backtest",
        average_expected_fill_ratio=Decimal("0.65"),
        average_queue_clear_seconds=Decimal("1.2"),
        average_queue_ahead_ratio=Decimal("0.4"),
        average_exit_depth_sweep_bps=Decimal("1.2"),
        average_exit_depth_coverage_ratio=Decimal("0.92"),
        average_exit_terminal_tail_ratio=Decimal("0.01"),
    )

    live_payload = {
        "average_expected_fill_ratio": "0.30",
        "average_queue_clear_seconds": "3.2",
        "average_queue_ahead_ratio": "1.3",
        "average_exit_depth_sweep_bps": "4.0",
        "average_exit_depth_coverage_ratio": "0.50",
        "average_exit_terminal_tail_ratio": "0.20",
        "entry_attempts": 10,
        "entries_rejected": 6,
    }

    decision = evaluate_execution_drift(live_payload=live_payload, baseline=baseline)

    assert decision.action == "observe_only"
    assert decision.size_multiplier == Decimal("0")
    assert decision.severe_breaches >= 1
    assert "fill_ratio_below_observe_threshold" in decision.reasons

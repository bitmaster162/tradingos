from decimal import Decimal

from btcusdt_bot.monitoring.execution_drift import ExecutionBaseline, evaluate_execution_drift


def test_execution_drift_can_observe_only_on_entry_calibration_decay() -> None:
    baseline = ExecutionBaseline(
        source="backtest",
        average_expected_fill_ratio=Decimal("0.70"),
        average_queue_clear_seconds=Decimal("1.0"),
        average_queue_ahead_ratio=Decimal("0.5"),
        average_entry_fill_ratio_shortfall=Decimal("0.05"),
        average_entry_fill_latency_seconds=Decimal("1.0"),
        entry_timeout_rate=Decimal("0.02"),
        average_exit_depth_sweep_bps=Decimal("1.5"),
        average_exit_depth_coverage_ratio=Decimal("0.90"),
        average_exit_terminal_tail_ratio=Decimal("0.01"),
    )

    live_payload = {
        "average_expected_fill_ratio": "0.68",
        "average_queue_clear_seconds": "1.1",
        "average_queue_ahead_ratio": "0.55",
        "average_entry_fill_ratio_shortfall": "0.20",
        "average_entry_fill_latency_seconds": "2.0",
        "entry_timeout_rate": "0.15",
        "average_exit_depth_sweep_bps": "1.6",
        "average_exit_depth_coverage_ratio": "0.89",
        "average_exit_terminal_tail_ratio": "0.01",
        "entry_attempts": 10,
        "entries_rejected": 0,
    }

    decision = evaluate_execution_drift(live_payload=live_payload, baseline=baseline)

    assert decision.action == "observe_only"
    assert "entry_fill_shortfall_above_reduce_threshold" in decision.reasons
    assert "entry_fill_latency_above_reduce_threshold" in decision.reasons
    assert "entry_timeout_rate_above_reduce_threshold" in decision.reasons

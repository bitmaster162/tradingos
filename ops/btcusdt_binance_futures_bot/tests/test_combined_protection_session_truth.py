from decimal import Decimal

from btcusdt_bot.monitoring.combined_protection import evaluate_combined_protection
from btcusdt_bot.monitoring.execution_drift import ExecutionDriftDecision
from btcusdt_bot.monitoring.session_truth import SessionTruthDecision



def test_combined_protection_escalates_execution_and_session_truth_reduce_to_observe() -> None:
    execution = ExecutionDriftDecision(action="reduce_size", size_multiplier=Decimal("0.75"), score=Decimal("1"))
    session_truth = SessionTruthDecision(action="reduce_size", size_multiplier=Decimal("0.60"), score=Decimal("1"))

    decision, state = evaluate_combined_protection(
        execution_drift=execution,
        session_truth=session_truth,
        compared_at_ms=1_700_000_000_000,
    )

    assert decision.action == "observe_only"
    assert decision.co_degrade_triggered is True
    assert "execution_session_truth_co_degrade" in decision.reasons
    assert state.last_action == "observe_only"

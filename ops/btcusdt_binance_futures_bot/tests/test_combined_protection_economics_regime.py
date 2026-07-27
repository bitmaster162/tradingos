from decimal import Decimal

from btcusdt_bot.monitoring.combined_protection import evaluate_combined_protection
from btcusdt_bot.monitoring.economics_regime import EconomicsRegimeDecision
from btcusdt_bot.monitoring.execution_drift import ExecutionDriftDecision



def test_combined_protection_escalates_execution_and_economics_regime_reduce_to_observe() -> None:
    execution = ExecutionDriftDecision(action="reduce_size", size_multiplier=Decimal("0.75"), score=Decimal("1"))
    economics = EconomicsRegimeDecision(action="reduce_size", size_multiplier=Decimal("0.60"), score=Decimal("1"))

    decision, state = evaluate_combined_protection(
        execution_drift=execution,
        economics_regime=economics,
        compared_at_ms=1_700_000_000_000,
    )

    assert decision.action == "observe_only"
    assert decision.co_degrade_triggered is True
    assert "execution_economics_regime_co_degrade" in decision.reasons
    assert state.last_action == "observe_only"

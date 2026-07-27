from decimal import Decimal

from btcusdt_bot.monitoring.combined_protection import (
    CombinedProtectionState,
    CombinedProtectionThresholds,
    evaluate_combined_protection,
)
from btcusdt_bot.monitoring.execution_drift import ExecutionDriftDecision
from btcusdt_bot.monitoring.pnl_protection import PnLProtectionDecision
from btcusdt_bot.monitoring.trade_reconciliation import TradeReconciliationDecision


def test_combined_protection_escalates_execution_and_pnl_reduce_to_observe() -> None:
    execution = ExecutionDriftDecision(action="reduce_size", size_multiplier=Decimal("0.75"), score=Decimal("1"))
    pnl = PnLProtectionDecision(action="reduce_size", size_multiplier=Decimal("0.50"), score=Decimal("1"))

    decision, state = evaluate_combined_protection(
        execution_drift=execution,
        pnl_protection=pnl,
        compared_at_ms=1_700_000_000_000,
    )

    assert decision.action == "observe_only"
    assert decision.size_multiplier == Decimal("0")
    assert decision.co_degrade_triggered is True
    assert "execution_pnl_co_degrade" in decision.reasons
    assert state.last_action == "observe_only"
    assert state.cooldown_until_ms > decision.compared_at_ms


def test_combined_protection_holds_observe_until_trade_confirmations_accumulate() -> None:
    previous_state = CombinedProtectionState(
        last_action="observe_only",
        last_size_multiplier=Decimal("0"),
        cooldown_until_ms=0,
        consecutive_trade_signals=1,
        consecutive_reduce_signals=0,
        last_compared_at_ms=1_700_000_000_000,
    )
    thresholds = CombinedProtectionThresholds(min_trade_confirmations_to_relax_observe=3)

    decision, state = evaluate_combined_protection(
        previous_state=previous_state,
        thresholds=thresholds,
        compared_at_ms=1_700_000_010_000,
    )

    assert decision.action == "observe_only"
    assert decision.hysteresis_applied is True
    assert "observe_hysteresis_wait_trade" in decision.reasons
    assert state.consecutive_trade_signals == 2

    decision2, state2 = evaluate_combined_protection(
        previous_state=state,
        thresholds=thresholds,
        compared_at_ms=1_700_000_020_000,
    )

    assert decision2.action == "trade"
    assert decision2.size_multiplier == Decimal("1")
    assert state2.last_action == "trade"
    assert state2.cooldown_until_ms == 0



def test_combined_protection_escalates_execution_and_trade_reconciliation_reduce_to_observe() -> None:
    execution = ExecutionDriftDecision(action="reduce_size", size_multiplier=Decimal("0.75"), score=Decimal("1"))
    trade_reconciliation = TradeReconciliationDecision(
        action="reduce_size",
        size_multiplier=Decimal("0.60"),
        score=Decimal("1"),
    )

    decision, state = evaluate_combined_protection(
        execution_drift=execution,
        trade_reconciliation=trade_reconciliation,
        compared_at_ms=1_700_000_000_000,
    )

    assert decision.action == "observe_only"
    assert decision.co_degrade_triggered is True
    assert "execution_trade_reconciliation_co_degrade" in decision.reasons
    assert state.last_action == "observe_only"

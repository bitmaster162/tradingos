from tools.deribit_options_research_runtime_audit import classify_decision


def test_runtime_decision_waits_for_forward_readiness() -> None:
    assert classify_decision(
        integrity_ok=True,
        sealed=True,
        runtime_ok=True,
        readiness_ready=False,
        outcomes_ready=False,
    ) == "deribit_options_stack_forward_collecting_readiness"


def test_runtime_decision_fails_closed_on_unsealed_external_surface() -> None:
    assert classify_decision(
        integrity_ok=True,
        sealed=False,
        runtime_ok=True,
        readiness_ready=False,
        outcomes_ready=False,
    ) == "deribit_options_stack_integrity_blocked"


def test_runtime_decision_never_claims_trade_readiness() -> None:
    assert classify_decision(
        integrity_ok=True,
        sealed=True,
        runtime_ok=True,
        readiness_ready=True,
        outcomes_ready=True,
    ) == "deribit_options_skew_forward_terminal_review_required"

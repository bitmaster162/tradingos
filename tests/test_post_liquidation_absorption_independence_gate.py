from __future__ import annotations

from tools.post_liquidation_absorption_forward_observer_runner import (
    apply_independence_gate,
    apply_operational_freshness_gate,
    report_is_fresh,
)


PASSED = "post_liq_absorption_forward_observer_passed_for_manual_review"
COLLECTING = "post_liq_absorption_forward_observer_collecting_sample"
FAILED = "post_liq_absorption_forward_observer_failed_gate_for_tombstone_review"


def audit(decision: str, *, eligible: bool, integrity: bool = True) -> dict:
    return {
        "decision": decision,
        "source_lock_verified": integrity,
        "eligible_for_manual_review": eligible,
        "automatic_promotion_allowed": False,
        "runtime_boundary": {"orders_allowed": False, "can_trade": False},
        "can_trade": False,
    }


def test_non_passing_raw_decision_is_not_overridden() -> None:
    result = apply_independence_gate(COLLECTING, ["minimum_new_events"], "collect", {})

    assert result == (COLLECTING, ["minimum_new_events"], "collect")


def test_raw_pass_waits_for_independent_sample() -> None:
    result = apply_independence_gate(
        PASSED,
        [],
        "manual review",
        audit("post_liq_independence_audit_waiting_independent_sample", eligible=False),
    )

    assert result[0] == COLLECTING
    assert result[1] == ["independence_sample_not_ready"]


def test_raw_pass_fails_when_independence_cost_gate_fails() -> None:
    result = apply_independence_gate(
        PASSED,
        [],
        "manual review",
        audit("post_liq_independence_audit_sample_ready_but_cost_gate_failed", eligible=False),
    )

    assert result[0] == FAILED
    assert result[1] == ["independence_cost_gate_failed"]


def test_raw_pass_survives_only_a_verified_independence_pass() -> None:
    result = apply_independence_gate(
        PASSED,
        [],
        "manual review",
        audit("post_liq_independence_audit_sample_ready_for_manual_review", eligible=True),
    )

    assert result == (PASSED, [], "manual review")


def test_untrusted_audit_cannot_unlock_manual_review() -> None:
    result = apply_independence_gate(
        PASSED,
        [],
        "manual review",
        audit("post_liq_independence_audit_sample_ready_for_manual_review", eligible=True, integrity=False),
    )

    assert result[0] == COLLECTING
    assert result[1] == ["independence_audit_integrity_blocked"]


def test_report_must_be_generated_after_command_start() -> None:
    command = {"returncode": 0, "started_at": "2026-07-12T01:00:00Z"}

    assert report_is_fresh({"generated_at": "2026-07-12T01:00:01Z"}, command) is True
    assert report_is_fresh({"generated_at": "2026-07-12T00:59:59Z"}, command) is False
    assert report_is_fresh({"generated_at": "2026-07-12T01:00:01Z"}, {**command, "returncode": 1}) is False


def test_stale_subprocess_artifact_cannot_preserve_pass() -> None:
    result = apply_operational_freshness_gate(
        PASSED,
        [],
        "manual review",
        context_fresh=True,
        observer_fresh=True,
        independence_fresh=False,
    )

    assert result[0] == COLLECTING
    assert result[1] == ["independence_audit_failed_or_stale"]

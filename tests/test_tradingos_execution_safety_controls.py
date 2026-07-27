from __future__ import annotations

import json

import pytest

from tools.tradingos_execution_safety_controls import (
    DeadManLatched,
    DeadManSwitch,
    IdentityMismatch,
    IdempotencyLedger,
    ImmutableIntent,
    InvalidAdmissionEvidence,
    ObservedPosition,
    SafetyControlError,
    admit_order_state,
    authorize_reduce_only,
    order_detail_evidence,
    private_ws_evidence,
)


NOW = 1_700_000_000_000


def intent(**overrides: object) -> ImmutableIntent:
    values: dict[str, object] = {
        "intent_id": "intent-001",
        "strategy_id": "strategy-a",
        "strategy_version": "1",
        "symbol": "BTCUSDT",
        "side": "SELL",
        "qty": "0.25",
        "decision_ts_ms": NOW,
        "price": "60000",
        "reduce_only": False,
    }
    values.update(overrides)
    return ImmutableIntent(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("field,value", [("qty", "NaN"), ("qty", "Infinity"), ("price", "-Inf")])
def test_nonfinite_intent_numbers_fail_closed(field: str, value: str) -> None:
    with pytest.raises(SafetyControlError):
        intent(**{field: value})


def test_supplied_client_id_must_match_immutable_identity() -> None:
    with pytest.raises(IdentityMismatch):
        intent(client_id="cx-attacker-controlled")


def test_exact_replay_is_suppressed_across_restart() -> None:
    original = intent()
    ledger = IdempotencyLedger()
    assert ledger.register(original)
    ledger.update(original, "OPEN")

    restored = IdempotencyLedger.load(ledger.snapshot())
    assert not restored.register(original)


def test_same_client_id_with_changed_payload_is_never_reused() -> None:
    original = intent()
    mutated = intent(qty="0.50")
    assert original.client_id == mutated.client_id
    assert original.fingerprint != mutated.fingerprint

    ledger = IdempotencyLedger()
    assert ledger.register(original)
    ledger.update(original, "FILLED")
    with pytest.raises(IdentityMismatch):
        ledger.register(mutated)


def test_tampered_ledger_identity_fails_closed() -> None:
    payload = json.loads(IdempotencyLedger().snapshot())
    payload["entries"]["cx-bad"] = {"intent_fingerprint": "not-a-hash", "state": "OPEN"}
    with pytest.raises(IdentityMismatch):
        IdempotencyLedger.load(json.dumps(payload))


def test_boolean_reconciled_flag_cannot_admit_state() -> None:
    with pytest.raises(InvalidAdmissionEvidence):
        admit_order_state(
            expected_client_id=intent().client_id,
            requested_state="OPEN",
            evidence=True,
        )


def test_private_ws_evidence_must_bind_exact_client_id() -> None:
    expected = intent().client_id
    with pytest.raises(InvalidAdmissionEvidence):
        private_ws_evidence(
            {"clientId": "cx-other", "orderId": "order-1", "state": "OPEN"},
            expected_client_id=expected,
            observed_at_ms=NOW,
        )


def test_order_detail_must_prove_found_order() -> None:
    expected = intent().client_id
    with pytest.raises(InvalidAdmissionEvidence):
        order_detail_evidence(
            {"found": False, "clientId": expected, "orderId": "order-1", "state": "OPEN"},
            expected_client_id=expected,
            observed_at_ms=NOW,
        )


def test_validated_evidence_admits_only_its_exact_state() -> None:
    expected = intent().client_id
    evidence = order_detail_evidence(
        {"found": True, "clientId": expected, "orderId": "order-1", "state": "OPEN"},
        expected_client_id=expected,
        observed_at_ms=NOW,
    )
    assert (
        admit_order_state(
            expected_client_id=expected,
            requested_state="OPEN",
            evidence=evidence,
        )
        == "OPEN"
    )
    with pytest.raises(InvalidAdmissionEvidence):
        admit_order_state(
            expected_client_id=expected,
            requested_state="FILLED",
            evidence=evidence,
        )


def test_nonfinite_reconciliation_payload_fails_closed() -> None:
    expected = intent().client_id
    with pytest.raises(InvalidAdmissionEvidence):
        private_ws_evidence(
            {
                "clientId": expected,
                "orderId": "order-1",
                "state": "FILLED",
                "filledQty": "NaN",
            },
            expected_client_id=expected,
            observed_at_ms=NOW,
        )


def test_deadman_is_closed_before_first_heartbeat() -> None:
    switch = DeadManSwitch(timeout_ms=1_000)
    assert switch.is_tripped(NOW)
    assert switch.reason == "first_heartbeat_missing"
    switch.heartbeat(NOW)
    assert not switch.is_tripped(NOW + 500)


def test_deadman_timeout_latches_until_explicit_health_reset() -> None:
    switch = DeadManSwitch(timeout_ms=1_000)
    switch.heartbeat(NOW)
    assert switch.is_tripped(NOW + 1_001)
    assert switch.reason == "deadman_timeout"
    with pytest.raises(DeadManLatched):
        switch.heartbeat(NOW + 1_002)
    switch.reset_after_healthcheck(NOW + 1_003)
    assert not switch.is_tripped(NOW + 1_004)


def test_deadman_rejects_clock_regression() -> None:
    switch = DeadManSwitch(timeout_ms=1_000)
    switch.heartbeat(NOW)
    with pytest.raises(DeadManLatched):
        switch.heartbeat(NOW - 1)
    assert switch.is_tripped(NOW)
    assert switch.reason == "heartbeat_clock_regression"


def observed_position(**overrides: object) -> ObservedPosition:
    values: dict[str, object] = {
        "symbol": "BTCUSDT",
        "signed_qty": "0.50",
        "observed_at_ms": NOW,
        "source": "private_ws",
        "position_id": "position-1",
    }
    values.update(overrides)
    return ObservedPosition(**values)  # type: ignore[arg-type]


def reduce_intent(**overrides: object) -> ImmutableIntent:
    values: dict[str, object] = {
        "reduce_only": True,
        "side": "SELL",
        "qty": "0.25",
        "position_id": "position-1",
    }
    values.update(overrides)
    return intent(**values)


def test_reduce_only_requires_nonzero_observed_position() -> None:
    decision = authorize_reduce_only(
        reduce_intent(),
        observed_position(signed_qty="0"),
        now_ms=NOW + 100,
    )
    assert not decision.approved
    assert decision.reason == "no_observed_position_to_reduce"


def test_reduce_only_rejects_wrong_side_oversize_and_stale_position() -> None:
    wrong_side = authorize_reduce_only(
        reduce_intent(side="BUY"),
        observed_position(),
        now_ms=NOW + 100,
    )
    oversize = authorize_reduce_only(
        reduce_intent(qty="0.75"),
        observed_position(),
        now_ms=NOW + 100,
    )
    stale = authorize_reduce_only(
        reduce_intent(),
        observed_position(),
        now_ms=NOW + 5_001,
        max_position_age_ms=5_000,
    )
    assert wrong_side.reason == "order_side_would_increase_exposure"
    assert oversize.reason == "reduce_qty_exceeds_observed_position"
    assert stale.reason == "observed_position_stale"


def test_reduce_only_requires_matching_position_identity() -> None:
    decision = authorize_reduce_only(
        reduce_intent(position_id="position-other"),
        observed_position(),
        now_ms=NOW + 100,
    )
    assert not decision.approved
    assert decision.reason == "observed_position_identity_mismatch"


def test_reduce_only_allows_bounded_reduction_of_observed_long_or_short() -> None:
    reduce_long = authorize_reduce_only(
        reduce_intent(side="SELL"),
        observed_position(signed_qty="0.50"),
        now_ms=NOW + 100,
    )
    reduce_short = authorize_reduce_only(
        reduce_intent(side="BUY"),
        observed_position(signed_qty="-0.50"),
        now_ms=NOW + 100,
    )
    assert reduce_long.approved
    assert reduce_short.approved

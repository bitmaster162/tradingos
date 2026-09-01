from __future__ import annotations

import json

import pytest

from r83_attestation_set_fixtures import set_policy as r83_set_policy
from r84_reviewer_key_possession_fixtures import m as r84m, policy as r84_policy
from r85_external_verifier_provenance_fixtures import (
    AUTHORITY_ROOT_SHA256,
    m as r85m,
    provenance_policy,
)
from r86_external_assertion_replay_guard_fixtures import m as r86m, replay_policy
from r87_external_assertion_replay_atomic_cas_fixtures import m as r87m, atomic_cas_policy
from r88_external_assertion_replay_writer_fencing_recovery_fixtures import (
    ROOT,
    m,
    writer_fencing_recovery_policy,
    r87_context,
    recovery_verification,
    build,
    clone,
)


def build_with(
    *,
    r87_binding_override=None,
    recovery_receipt=None,
    expected_recovery_sha=None,
    recovery_policy_override=None,
):
    (
        items, manifest, aid, assertion, r84_binding, verifier_reg,
        r85_binding, replay_reg, r86_binding, atomic_receipt, r87_binding,
    ) = r87_context()
    r87_binding = r87_binding if r87_binding_override is None else r87_binding_override
    recovery_receipt = (
        recovery_verification(r87_binding) if recovery_receipt is None else recovery_receipt
    )
    expected_recovery_sha = (
        m.stable_sha256(recovery_receipt)
        if expected_recovery_sha is None
        else expected_recovery_sha
    )
    return m.build_external_assertion_replay_writer_fencing_recovery_binding(
        r87_binding,
        r86_binding,
        r85_binding,
        r84_binding,
        manifest,
        items,
        r83_set_policy(),
        aid,
        assertion,
        verifier_reg,
        replay_reg,
        atomic_receipt,
        recovery_receipt,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),
        key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=r85m.stable_sha256(verifier_reg),
        expected_authority_root_sha256=AUTHORITY_ROOT_SHA256,
        provenance_policy=provenance_policy(),
        expected_replay_registry_sha256=r86m.stable_sha256(replay_reg),
        replay_guard_policy=replay_policy(),
        expected_atomic_verification_sha256=r87m.stable_sha256(atomic_receipt),
        atomic_cas_policy=atomic_cas_policy(),
        expected_recovery_verification_sha256=expected_recovery_sha,
        writer_fencing_recovery_policy=(
            writer_fencing_recovery_policy()
            if recovery_policy_override is None
            else recovery_policy_override
        ),
    )


def test_build_and_validate_writer_fencing_recovery_binding():
    x = build()
    assert x["schema"] == m.BINDING_SCHEMA
    assert x["writer_fencing_recovery_evidence_bound"] is True
    assert x["lease_digest_bound"] is True
    assert x["fencing_protocol_bound"] is True
    assert x["crash_recovery_protocol_bound"] is True


def test_binding_is_deterministic():
    assert build() == build()
    assert build()["binding_id"] == build()["binding_id"]


def test_exact_r87_and_recovery_receipt_are_digest_bound():
    *_, r87_binding = r87_context()
    receipt = recovery_verification(r87_binding)
    x = build_with(r87_binding_override=r87_binding, recovery_receipt=receipt)
    assert x["r87_binding_id"] == r87_binding["binding_id"]
    assert x["r87_binding_sha256"] == m.stable_sha256(r87_binding)
    assert x["recovery_verification_sha256"] == m.stable_sha256(receipt)
    assert x["recovery_verification_digest_consumed"] is True
    assert x["recovery_policy_sha256"] == m.stable_sha256(writer_fencing_recovery_policy())


def test_exact_atomic_prior_next_and_generation_are_bound():
    *_, r87_binding = r87_context()
    x = build()
    assert x["atomic_verification_sha256"] == r87_binding["atomic_verification_sha256"]
    assert x["replay_registry_sha256"] == r87_binding["replay_registry_sha256"]
    assert x["next_registry_candidate_sha256"] == r87_binding["next_registry_candidate_sha256"]
    assert x["cas_generation_from"] == r87_binding["cas_generation_from"]
    assert x["cas_generation_to"] == r87_binding["cas_generation_to"]


def test_substituted_recovery_receipt_rejected_against_retained_digest():
    *_, r87_binding = r87_context()
    original = recovery_verification(r87_binding)
    retained = m.stable_sha256(original)
    changed = clone(original)
    changed["writer_lease_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="verification digest mismatch"):
        build_with(
            r87_binding_override=r87_binding,
            recovery_receipt=changed,
            expected_recovery_sha=retained,
        )


@pytest.mark.parametrize(
    "field",
    [
        "atomic_verification_sha256",
        "replay_registry_sha256",
        "next_registry_candidate_sha256",
    ],
)
def test_r87_transition_transplant_rejected(field):
    *_, r87_binding = r87_context()
    receipt = recovery_verification(r87_binding)
    receipt[field] = "e" * 64
    with pytest.raises(ValueError, match="R87 mismatch"):
        build_with(r87_binding_override=r87_binding, recovery_receipt=receipt)


def test_generation_transition_transplant_rejected():
    *_, r87_binding = r87_context()
    receipt = recovery_verification(r87_binding)
    receipt["cas_generation_to"] = receipt["cas_generation_to"] + 1
    with pytest.raises(ValueError, match="R87 mismatch"):
        build_with(r87_binding_override=r87_binding, recovery_receipt=receipt)


def test_fencing_model_is_mandatory():
    *_, r87_binding = r87_context()
    receipt = recovery_verification(r87_binding)
    receipt["fencing_model"] = "NO_FENCING"
    with pytest.raises(ValueError, match="fencing model invalid"):
        build_with(r87_binding_override=r87_binding, recovery_receipt=receipt)


def test_crash_recovery_protocol_is_mandatory():
    *_, r87_binding = r87_context()
    receipt = recovery_verification(r87_binding)
    receipt["crash_recovery_protocol"] = "BLIND_RETRY"
    with pytest.raises(ValueError, match="crash-recovery protocol invalid"):
        build_with(r87_binding_override=r87_binding, recovery_receipt=receipt)


def test_blind_retry_is_forbidden():
    *_, r87_binding = r87_context()
    receipt = recovery_verification(r87_binding)
    receipt["blind_retry_allowed"] = True
    with pytest.raises(ValueError, match="blind retry forbidden"):
        build_with(r87_binding_override=r87_binding, recovery_receipt=receipt)


def test_split_brain_same_token_guard_is_mandatory():
    *_, r87_binding = r87_context()
    receipt = recovery_verification(r87_binding)
    receipt["split_brain_same_token_rejected"] = False
    with pytest.raises(ValueError, match="split-brain same-token guard missing"):
        build_with(r87_binding_override=r87_binding, recovery_receipt=receipt)


def test_stale_writer_requires_lower_attempt_token():
    *_, r87_binding = r87_context()
    receipt = recovery_verification(r87_binding)
    receipt["stale_writer_fenced"] = True
    receipt["recovery_status"] = "STALE_WRITER_FENCED_REACQUIRE_REQUIRED"
    receipt["recovery_action"] = "REACQUIRE_LEASE"
    receipt["attempt_fencing_token"] = 11
    receipt["current_fencing_token"] = 12
    x = build_with(r87_binding_override=r87_binding, recovery_receipt=receipt)
    assert x["stale_writer_fenced"] is True
    assert x["attempt_fencing_token"] < x["current_fencing_token"]


def test_stale_writer_equal_token_rejected():
    *_, r87_binding = r87_context()
    receipt = recovery_verification(r87_binding)
    receipt["stale_writer_fenced"] = True
    receipt["recovery_status"] = "STALE_WRITER_FENCED_REACQUIRE_REQUIRED"
    receipt["recovery_action"] = "REACQUIRE_LEASE"
    with pytest.raises(ValueError, match="stale writer fencing token relation invalid"):
        build_with(r87_binding_override=r87_binding, recovery_receipt=receipt)


def test_non_stale_writer_requires_equal_token():
    *_, r87_binding = r87_context()
    receipt = recovery_verification(r87_binding)
    receipt["attempt_fencing_token"] = 11
    with pytest.raises(ValueError, match="non-stale writer fencing token relation invalid"):
        build_with(r87_binding_override=r87_binding, recovery_receipt=receipt)


def test_attempt_token_cannot_exceed_current():
    *_, r87_binding = r87_context()
    receipt = recovery_verification(r87_binding)
    receipt["attempt_fencing_token"] = 13
    with pytest.raises(ValueError, match="cannot exceed current"):
        build_with(r87_binding_override=r87_binding, recovery_receipt=receipt)


def test_recovery_status_action_map_is_bounded():
    *_, r87_binding = r87_context()
    receipt = recovery_verification(r87_binding)
    receipt["recovery_action"] = "EXECUTE"
    with pytest.raises(ValueError, match="recovery status/action invalid"):
        build_with(r87_binding_override=r87_binding, recovery_receipt=receipt)


@pytest.mark.parametrize(
    "field",
    [
        "live_writer_backend_proven",
        "commit_performed",
        "registry_write_performed",
        "lease_registry_write_performed",
        "receipt_index_write_performed",
        "backend_write_performed",
        "durable_commit_proven",
        "global_current_state_verified",
        "concurrent_writer_exclusion_proven",
        "can_execute",
        "apply_allowed",
        "confers_authority",
    ],
)
def test_recovery_receipt_overclaims_rejected(field):
    *_, r87_binding = r87_context()
    receipt = recovery_verification(r87_binding)
    receipt[field] = True
    with pytest.raises(ValueError, match="writer-fencing recovery overclaim"):
        build_with(r87_binding_override=r87_binding, recovery_receipt=receipt)


def test_recovery_execution_authority_overclaim_rejected():
    *_, r87_binding = r87_context()
    receipt = recovery_verification(r87_binding)
    receipt["execution_authority"] = "LIVE"
    with pytest.raises(ValueError, match="execution authority overclaim"):
        build_with(r87_binding_override=r87_binding, recovery_receipt=receipt)


def test_recovery_extra_key_rejected():
    *_, r87_binding = r87_context()
    receipt = recovery_verification(r87_binding)
    receipt["hidden_writer_claim"] = True
    with pytest.raises(ValueError, match="verification key set mismatch"):
        build_with(r87_binding_override=r87_binding, recovery_receipt=receipt)


def test_unsafe_policy_drift_rejected():
    p = writer_fencing_recovery_policy()
    p["backend_write_allowed"] = True
    with pytest.raises(ValueError, match="unsafe writer-fencing recovery policy"):
        build_with(recovery_policy_override=p)


def test_bool_schema_version_rejected():
    p = writer_fencing_recovery_policy()
    p["schema_version"] = True
    with pytest.raises(ValueError, match="unsupported writer-fencing recovery policy"):
        build_with(recovery_policy_override=p)


def test_tampered_r87_binding_rejected_by_full_r87_validation():
    *_, r87_binding = r87_context()
    tampered = clone(r87_binding)
    tampered["replay_registry_sha256"] = "d" * 64
    receipt = recovery_verification(tampered)
    with pytest.raises(ValueError):
        build_with(r87_binding_override=tampered, recovery_receipt=receipt)


def test_schema_required_keys_match_contract():
    schema = json.loads(
        (ROOT / "schemas" / "TRADINGOS_EXTERNAL_ASSERTION_REPLAY_WRITER_FENCING_RECOVERY_BINDING_V1.schema.json")
        .read_text(encoding="utf-8")
    )
    assert set(schema["required"]) == m.BINDING_KEYS


def test_no_durable_live_writer_current_state_or_authority_upgrade():
    x = build()
    assert x["live_writer_backend_proven"] is False
    assert x["durable_commit_proven"] is False
    assert x["durable_single_use_enforced"] is False
    assert x["global_current_state_verified"] is False
    assert x["concurrent_writer_exclusion_proven"] is False
    assert x["registry_write_performed"] is False
    assert x["lease_registry_write_performed"] is False
    assert x["receipt_index_write_performed"] is False
    assert x["backend_write_performed"] is False
    assert x["assertion_freshness_verified"] is False
    assert x["liveness_verified"] is False
    assert x["verifier_trust_root_verified"] is False
    assert x["review_identity_verified"] is False
    assert x["physical_human_presence_proven"] is False
    assert x["distinct_reviewer_count_allowed"] is False
    assert x["consensus_inference_allowed"] is False
    assert x["approval_state_allowed"] is False


def test_policy_and_output_authority_ceiling_is_exact():
    m.validate_writer_fencing_recovery_policy(writer_fencing_recovery_policy())
    x = build()
    assert x["shadow_only"] is True
    assert x["human_review_only"] is True
    assert x["attestation_set_consumption_authority"] == "NONE"
    assert x["memory_write_authority"] == "NONE"
    assert x["policy_update_allowed"] is False
    assert x["live_decision_feedback_allowed"] is False
    assert x["live_decision_use_allowed"] is False
    assert x["model_selection_use_allowed"] is False
    assert x["execution_authority"] == "NONE"
    assert x["can_trade"] is False
    assert x["capital_permission"] == "DENY"
    assert x["confers_authority"] is False

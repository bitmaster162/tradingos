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
from r87_external_assertion_replay_atomic_cas_fixtures import (
    ROOT,
    m,
    atomic_cas_policy,
    r86_context,
    atomic_verification,
    build,
    clone,
)


def build_with(
    *,
    r86_binding_override=None,
    atomic_receipt=None,
    expected_atomic_sha=None,
    atomic_policy_override=None,
):
    (
        items, manifest, aid, assertion, r84_binding, verifier_reg,
        r85_binding, replay_reg, r86_binding,
    ) = r86_context()
    r86_binding = r86_binding if r86_binding_override is None else r86_binding_override
    atomic_receipt = (
        atomic_verification(r86_binding) if atomic_receipt is None else atomic_receipt
    )
    expected_atomic_sha = (
        m.stable_sha256(atomic_receipt) if expected_atomic_sha is None else expected_atomic_sha
    )
    return m.build_external_assertion_replay_atomic_cas_binding(
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
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),
        key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=r85m.stable_sha256(verifier_reg),
        expected_authority_root_sha256=AUTHORITY_ROOT_SHA256,
        provenance_policy=provenance_policy(),
        expected_replay_registry_sha256=r86m.stable_sha256(replay_reg),
        replay_guard_policy=replay_policy(),
        expected_atomic_verification_sha256=expected_atomic_sha,
        atomic_cas_policy=(
            atomic_cas_policy()
            if atomic_policy_override is None
            else atomic_policy_override
        ),
    )


def test_build_and_validate_atomic_cas_binding():
    x = build()
    assert x["schema"] == m.BINDING_SCHEMA
    assert x["atomic_transition_candidate_verified"] is True
    assert x["cas_precondition_bound"] is True


def test_binding_is_deterministic():
    assert build() == build()
    assert build()["binding_id"] == build()["binding_id"]


def test_exact_r86_and_atomic_receipt_are_digest_bound():
    *_, r86_binding = r86_context()
    receipt = atomic_verification(r86_binding)
    x = build_with(r86_binding_override=r86_binding, atomic_receipt=receipt)
    assert x["r86_binding_id"] == r86_binding["binding_id"]
    assert x["r86_binding_sha256"] == m.stable_sha256(r86_binding)
    assert x["atomic_verification_sha256"] == m.stable_sha256(receipt)
    assert x["atomic_verification_digest_consumed"] is True
    assert x["atomic_cas_policy_sha256"] == m.stable_sha256(atomic_cas_policy())


def test_exact_prior_next_and_generation_transition_are_bound():
    *_, r86_binding = r86_context()
    x = build()
    assert x["replay_registry_sha256"] == r86_binding["replay_registry_sha256"]
    assert x["next_registry_candidate_sha256"] == r86_binding["next_registry_candidate_sha256"]
    assert x["cas_generation_from"] == r86_binding["prior_generation"]
    assert x["cas_generation_to"] == r86_binding["next_generation"]
    assert x["cas_generation_to"] == x["cas_generation_from"] + 1


def test_substituted_atomic_receipt_rejected_against_retained_digest():
    *_, r86_binding = r86_context()
    original = atomic_verification(r86_binding)
    retained = m.stable_sha256(original)
    changed = clone(original)
    changed["atomic_scope"] = "OTHER"
    with pytest.raises(ValueError, match="atomic verification digest mismatch"):
        build_with(
            r86_binding_override=r86_binding,
            atomic_receipt=changed,
            expected_atomic_sha=retained,
        )


def test_next_candidate_transplant_rejected_even_if_receipt_rehashed():
    *_, r86_binding = r86_context()
    receipt = atomic_verification(r86_binding)
    receipt["next_registry_candidate_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="atomic verification R86 mismatch"):
        build_with(r86_binding_override=r86_binding, atomic_receipt=receipt)


def test_prior_registry_transplant_rejected_even_if_receipt_rehashed():
    *_, r86_binding = r86_context()
    receipt = atomic_verification(r86_binding)
    receipt["replay_registry_sha256"] = "e" * 64
    with pytest.raises(ValueError, match="atomic verification R86 mismatch"):
        build_with(r86_binding_override=r86_binding, atomic_receipt=receipt)


def test_generation_transition_mismatch_rejected():
    *_, r86_binding = r86_context()
    receipt = atomic_verification(r86_binding)
    receipt["cas_generation_to"] = receipt["cas_generation_from"] + 2
    with pytest.raises(ValueError, match="atomic verification R86 mismatch|generation transition invalid"):
        build_with(r86_binding_override=r86_binding, atomic_receipt=receipt)


def test_compare_and_swap_guard_is_mandatory():
    *_, r86_binding = r86_context()
    receipt = atomic_verification(r86_binding)
    receipt["toctou_guard_model"] = "READ_THEN_WRITE"
    with pytest.raises(ValueError, match="CAS guard missing"):
        build_with(r86_binding_override=r86_binding, atomic_receipt=receipt)


@pytest.mark.parametrize(
    "field",
    [
        "commit_performed",
        "registry_write_performed",
        "durable_commit_proven",
        "global_current_state_verified",
        "concurrent_writer_exclusion_proven",
        "can_execute",
        "apply_allowed",
        "confers_authority",
    ],
)
def test_atomic_receipt_overclaims_rejected(field):
    *_, r86_binding = r86_context()
    receipt = atomic_verification(r86_binding)
    receipt[field] = True
    with pytest.raises(ValueError, match="atomic verification overclaim"):
        build_with(r86_binding_override=r86_binding, atomic_receipt=receipt)


def test_atomic_receipt_execution_authority_overclaim_rejected():
    *_, r86_binding = r86_context()
    receipt = atomic_verification(r86_binding)
    receipt["execution_authority"] = "LIVE"
    with pytest.raises(ValueError, match="execution authority overclaim"):
        build_with(r86_binding_override=r86_binding, atomic_receipt=receipt)


def test_atomic_receipt_extra_key_rejected():
    *_, r86_binding = r86_context()
    receipt = atomic_verification(r86_binding)
    receipt["hidden_backend_claim"] = True
    with pytest.raises(ValueError, match="atomic verification key set mismatch"):
        build_with(r86_binding_override=r86_binding, atomic_receipt=receipt)


def test_unsafe_policy_drift_rejected():
    p = atomic_cas_policy()
    p["registry_write_allowed"] = True
    with pytest.raises(ValueError, match="unsafe atomic-CAS policy"):
        build_with(atomic_policy_override=p)


def test_bool_schema_version_rejected():
    p = atomic_cas_policy()
    p["schema_version"] = True
    with pytest.raises(ValueError, match="unsupported atomic-CAS policy"):
        build_with(atomic_policy_override=p)


def test_tampered_r86_binding_rejected_by_full_r86_validation():
    *_, r86_binding = r86_context()
    tampered = clone(r86_binding)
    tampered["registry_id"] = "substituted-registry"
    receipt = atomic_verification(tampered)
    with pytest.raises(ValueError):
        build_with(r86_binding_override=tampered, atomic_receipt=receipt)


def test_schema_required_keys_match_contract():
    schema = json.loads(
        (ROOT / "schemas" / "TRADINGOS_EXTERNAL_ASSERTION_REPLAY_ATOMIC_CAS_BINDING_V1.schema.json")
        .read_text(encoding="utf-8")
    )
    assert set(schema["required"]) == m.BINDING_KEYS


def test_no_durable_current_state_writer_exclusion_freshness_or_authority_upgrade():
    x = build()
    assert x["durable_commit_proven"] is False
    assert x["durable_single_use_enforced"] is False
    assert x["global_current_state_verified"] is False
    assert x["concurrent_writer_exclusion_proven"] is False
    assert x["registry_write_performed"] is False
    assert x["assertion_freshness_verified"] is False
    assert x["liveness_verified"] is False
    assert x["verifier_trust_root_verified"] is False
    assert x["review_identity_verified"] is False
    assert x["physical_human_presence_proven"] is False
    assert x["distinct_reviewer_count_allowed"] is False
    assert x["consensus_inference_allowed"] is False
    assert x["approval_state_allowed"] is False


def test_policy_and_output_authority_ceiling_is_exact():
    m.validate_atomic_cas_policy(atomic_cas_policy())
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

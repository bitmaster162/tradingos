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
    m as r88m,
    writer_fencing_recovery_policy,
)
from r89_external_assertion_replay_writer_authority_anchor_fixtures import (
    WRITER_AUTHORITY_ROOT_SHA256,
    m as r89m,
    writer_authority_anchor_policy,
)
from r90_external_assertion_replay_dual_state_atomicity_fixtures import (
    ROOT,
    m,
    dual_state_atomicity_policy,
    r89_context,
    atomicity_verification,
    build,
    clone,
)

def build_with(
    *,
    r89_binding_override=None,
    atomicity_receipt=None,
    expected_atomicity_sha=None,
    expected_verifier_root=AUTHORITY_ROOT_SHA256,
    expected_writer_root=WRITER_AUTHORITY_ROOT_SHA256,
    policy_override=None,
):
    (
        items, manifest, aid, assertion, r84_binding, verifier_reg,
        r85_binding, replay_reg, r86_binding, atomic_receipt, r87_binding,
        recovery_receipt, r88_binding, anchor, r89_binding,
    ) = r89_context()
    r89_binding = r89_binding if r89_binding_override is None else r89_binding_override
    atomicity_receipt = (
        atomicity_verification(r89_binding)
        if atomicity_receipt is None
        else atomicity_receipt
    )
    expected_atomicity_sha = (
        m.stable_sha256(atomicity_receipt)
        if expected_atomicity_sha is None
        else expected_atomicity_sha
    )
    return m.build_external_assertion_replay_dual_state_atomicity_binding(
        r89_binding, r88_binding, r87_binding, r86_binding, r85_binding, r84_binding,
        manifest, items, r83_set_policy(), aid, assertion, verifier_reg, replay_reg,
        atomic_receipt, recovery_receipt, anchor, atomicity_receipt,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),
        key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=r85m.stable_sha256(verifier_reg),
        expected_verifier_authority_root_sha256=expected_verifier_root,
        expected_writer_authority_root_sha256=expected_writer_root,
        provenance_policy=provenance_policy(),
        expected_replay_registry_sha256=r86m.stable_sha256(replay_reg),
        replay_guard_policy=replay_policy(),
        expected_atomic_verification_sha256=r87m.stable_sha256(atomic_receipt),
        atomic_cas_policy=atomic_cas_policy(),
        expected_recovery_verification_sha256=r88m.stable_sha256(recovery_receipt),
        writer_fencing_recovery_policy=writer_fencing_recovery_policy(),
        expected_authority_anchor_sha256=r89m.stable_sha256(anchor),
        writer_authority_anchor_policy=writer_authority_anchor_policy(),
        expected_atomicity_verification_sha256=expected_atomicity_sha,
        dual_state_atomicity_policy=(
            dual_state_atomicity_policy() if policy_override is None else policy_override
        ),
    )

def test_build_and_validate_dual_state_atomicity_binding():
    x = build()
    assert x["schema"] == m.BINDING_SCHEMA
    assert x["dual_state_atomicity_evidence_bound"] is True

def test_binding_is_deterministic():
    assert build() == build()
    assert build()["binding_id"] == build()["binding_id"]

def test_r89_and_atomicity_receipt_are_digest_bound():
    *_, r89_binding = r89_context()
    receipt = atomicity_verification(r89_binding)
    x = build_with(r89_binding_override=r89_binding, atomicity_receipt=receipt)
    assert x["r89_binding_id"] == r89_binding["binding_id"]
    assert x["r89_binding_sha256"] == m.stable_sha256(r89_binding)
    assert x["atomicity_verification_sha256"] == m.stable_sha256(receipt)
    assert x["atomicity_verification_digest_consumed"] is True

def test_root_domains_remain_distinct_and_bound():
    assert AUTHORITY_ROOT_SHA256 != WRITER_AUTHORITY_ROOT_SHA256
    x = build_with(
        expected_verifier_root=AUTHORITY_ROOT_SHA256,
        expected_writer_root=WRITER_AUTHORITY_ROOT_SHA256,
    )
    assert x["verifier_authority_root_sha256"] == AUTHORITY_ROOT_SHA256
    assert x["writer_authority_root_sha256"] == WRITER_AUTHORITY_ROOT_SHA256

def test_writer_root_cannot_substitute_for_verifier_root():
    with pytest.raises(ValueError):
        build_with(expected_verifier_root=WRITER_AUTHORITY_ROOT_SHA256)

def test_verifier_root_cannot_substitute_for_writer_root():
    with pytest.raises(ValueError):
        build_with(expected_writer_root=AUTHORITY_ROOT_SHA256)

def test_substituted_atomicity_receipt_rejected_against_retained_digest():
    *_, r89_binding = r89_context()
    original = atomicity_verification(r89_binding)
    retained = m.stable_sha256(original)
    changed = clone(original)
    changed["lease_lineage_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="atomicity verification digest mismatch"):
        build_with(
            r89_binding_override=r89_binding,
            atomicity_receipt=changed,
            expected_atomicity_sha=retained,
        )

@pytest.mark.parametrize(
    "field",
    ["authority_anchor_sha256", "writer_lease_sha256", "prior_receipt_index_sha256"],
)
def test_r89_atomicity_transplant_rejected(field):
    *_, r89_binding = r89_context()
    receipt = atomicity_verification(r89_binding)
    receipt[field] = "e" * 64
    with pytest.raises(ValueError, match="R89 mismatch"):
        build_with(r89_binding_override=r89_binding, atomicity_receipt=receipt)

def test_writer_root_transplant_rejected():
    *_, r89_binding = r89_context()
    receipt = atomicity_verification(r89_binding)
    receipt["writer_authority_root_sha256"] = "e" * 64
    with pytest.raises(ValueError, match="R89 mismatch"):
        build_with(r89_binding_override=r89_binding, atomicity_receipt=receipt)

def test_dual_state_model_is_mandatory():
    *_, r89_binding = r89_context()
    receipt = atomicity_verification(r89_binding)
    receipt["dual_state_atomicity_model"] = "TWO_INDEPENDENT_WRITES"
    with pytest.raises(ValueError, match="atomicity model invalid"):
        build_with(r89_binding_override=r89_binding, atomicity_receipt=receipt)

@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("split_state_rejected", "split-state guard missing"),
        ("lease_epoch_lineage_verified", "lease lineage guard missing"),
        ("aba_guard_verified", "ABA guard missing"),
    ],
)
def test_atomicity_guards_are_mandatory(field, message):
    *_, r89_binding = r89_context()
    receipt = atomicity_verification(r89_binding)
    receipt[field] = False
    with pytest.raises(ValueError, match=message):
        build_with(r89_binding_override=r89_binding, atomicity_receipt=receipt)

def test_durability_status_cannot_upgrade():
    *_, r89_binding = r89_context()
    receipt = atomicity_verification(r89_binding)
    receipt["durability_status"] = "DURABLE_BACKEND_COMMITTED"
    with pytest.raises(ValueError, match="durability status invalid"):
        build_with(r89_binding_override=r89_binding, atomicity_receipt=receipt)

@pytest.mark.parametrize(
    "field",
    [
        "write_performed", "live_backend_observed", "durable_commit_proven",
        "durable_dual_state_atomicity_proven", "global_current_state_verified",
        "concurrent_writer_exclusion_proven", "registry_write_performed",
        "lease_registry_write_performed", "receipt_index_write_performed",
        "backend_write_performed", "can_execute", "apply_allowed", "confers_authority",
    ],
)
def test_atomicity_overclaims_rejected(field):
    *_, r89_binding = r89_context()
    receipt = atomicity_verification(r89_binding)
    receipt[field] = True
    with pytest.raises(ValueError, match="dual-state atomicity overclaim"):
        build_with(r89_binding_override=r89_binding, atomicity_receipt=receipt)

def test_execution_authority_overclaim_rejected():
    *_, r89_binding = r89_context()
    receipt = atomicity_verification(r89_binding)
    receipt["execution_authority"] = "LIVE"
    with pytest.raises(ValueError, match="execution authority overclaim"):
        build_with(r89_binding_override=r89_binding, atomicity_receipt=receipt)

def test_atomicity_extra_key_rejected():
    *_, r89_binding = r89_context()
    receipt = atomicity_verification(r89_binding)
    receipt["hidden_durability_claim"] = True
    with pytest.raises(ValueError, match="verification key set mismatch"):
        build_with(r89_binding_override=r89_binding, atomicity_receipt=receipt)

def test_unsafe_policy_drift_rejected():
    p = dual_state_atomicity_policy()
    p["durable_commit_inference_allowed"] = True
    with pytest.raises(ValueError, match="unsafe dual-state atomicity policy"):
        build_with(policy_override=p)

def test_bool_schema_version_rejected():
    p = dual_state_atomicity_policy()
    p["schema_version"] = True
    with pytest.raises(ValueError, match="unsupported dual-state atomicity policy"):
        build_with(policy_override=p)

def test_tampered_r89_binding_rejected_by_full_r89_validation():
    *_, r89_binding = r89_context()
    tampered = clone(r89_binding)
    tampered["writer_lease_sha256"] = "d" * 64
    receipt = atomicity_verification(tampered)
    with pytest.raises(ValueError):
        build_with(r89_binding_override=tampered, atomicity_receipt=receipt)

def test_schema_required_keys_match_contract():
    schema = json.loads(
        (ROOT / "schemas" / "TRADINGOS_EXTERNAL_ASSERTION_REPLAY_DUAL_STATE_ATOMICITY_BINDING_V1.schema.json")
        .read_text(encoding="utf-8")
    )
    assert set(schema["required"]) == m.BINDING_KEYS

def test_no_durable_or_authority_upgrade():
    x = build()
    assert x["write_performed"] is False
    assert x["live_backend_observed"] is False
    assert x["durable_dual_state_atomicity_proven"] is False
    assert x["durable_commit_proven"] is False
    assert x["durable_single_use_enforced"] is False
    assert x["global_current_state_verified"] is False
    assert x["concurrent_writer_exclusion_proven"] is False
    assert x["writer_authority_root_verified"] is False
    assert x["verifier_trust_root_verified"] is False
    assert x["review_identity_verified"] is False
    assert x["consensus_inference_allowed"] is False
    assert x["approval_state_allowed"] is False

def test_policy_and_output_authority_ceiling_is_exact():
    m.validate_dual_state_atomicity_policy(dual_state_atomicity_policy())
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

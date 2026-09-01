from __future__ import annotations

import json
import pytest

from r83_attestation_set_fixtures import set_policy as r83_set_policy
from r84_reviewer_key_possession_fixtures import m as r84m, policy as r84_policy
from r85_external_verifier_provenance_fixtures import AUTHORITY_ROOT_SHA256, m as r85m, provenance_policy
from r86_external_assertion_replay_guard_fixtures import m as r86m, replay_policy
from r87_external_assertion_replay_atomic_cas_fixtures import m as r87m, atomic_cas_policy
from r88_external_assertion_replay_writer_fencing_recovery_fixtures import m as r88m, writer_fencing_recovery_policy
from r89_external_assertion_replay_writer_authority_anchor_fixtures import (
    ROOT,
    WRITER_AUTHORITY_ROOT_SHA256,
    m,
    writer_authority_anchor_policy,
    r88_context,
    authority_anchor,
    build,
    clone,
)


def build_with(*, r88_binding_override=None, anchor_override=None, expected_anchor_sha=None,
               expected_root=WRITER_AUTHORITY_ROOT_SHA256, policy_override=None):
    (
        items, manifest, aid, assertion, r84_binding, verifier_reg,
        r85_binding, replay_reg, r86_binding, atomic_receipt, r87_binding,
        recovery_receipt, r88_binding,
    ) = r88_context()
    r88_binding = r88_binding if r88_binding_override is None else r88_binding_override
    anchor = authority_anchor(r88_binding) if anchor_override is None else anchor_override
    expected_anchor_sha = m.stable_sha256(anchor) if expected_anchor_sha is None else expected_anchor_sha
    return m.build_external_assertion_replay_writer_authority_anchor_binding(
        r88_binding, r87_binding, r86_binding, r85_binding, r84_binding, manifest, items,
        r83_set_policy(), aid, assertion, verifier_reg, replay_reg, atomic_receipt,
        recovery_receipt, anchor,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),
        key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=r85m.stable_sha256(verifier_reg),
        expected_authority_root_sha256=expected_root,
        provenance_policy=provenance_policy(),
        expected_replay_registry_sha256=r86m.stable_sha256(replay_reg),
        replay_guard_policy=replay_policy(),
        expected_atomic_verification_sha256=r87m.stable_sha256(atomic_receipt),
        atomic_cas_policy=atomic_cas_policy(),
        expected_recovery_verification_sha256=r88m.stable_sha256(recovery_receipt),
        writer_fencing_recovery_policy=writer_fencing_recovery_policy(),
        expected_authority_anchor_sha256=expected_anchor_sha,
        writer_authority_anchor_policy=(writer_authority_anchor_policy() if policy_override is None else policy_override),
    )


def test_build_and_validate_writer_authority_anchor_binding():
    x = build()
    assert x["schema"] == m.BINDING_SCHEMA
    assert x["writer_authority_anchor_bound"] is True
    assert x["authority_root_digest_consumed"] is True


def test_binding_is_deterministic():
    assert build() == build()
    assert build()["binding_id"] == build()["binding_id"]


def test_exact_r88_and_anchor_are_digest_bound():
    *_, r88_binding = r88_context()
    anchor = authority_anchor(r88_binding)
    x = build_with(r88_binding_override=r88_binding, anchor_override=anchor)
    assert x["r88_binding_id"] == r88_binding["binding_id"]
    assert x["r88_binding_sha256"] == m.stable_sha256(r88_binding)
    assert x["authority_anchor_sha256"] == m.stable_sha256(anchor)
    assert x["authority_anchor_digest_consumed"] is True
    assert x["authority_root_sha256"] == WRITER_AUTHORITY_ROOT_SHA256


def test_substituted_anchor_rejected_against_retained_digest():
    *_, r88_binding = r88_context()
    original = authority_anchor(r88_binding)
    retained = m.stable_sha256(original)
    changed = clone(original)
    changed["writer_lease_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="anchor digest mismatch"):
        build_with(r88_binding_override=r88_binding, anchor_override=changed, expected_anchor_sha=retained)


def test_substituted_authority_root_rejected():
    with pytest.raises(ValueError, match="root digest mismatch"):
        build_with(expected_root="f" * 64)


@pytest.mark.parametrize("field", [
    "recovery_verification_sha256", "writer_lease_sha256",
    "current_receipt_index_sha256", "receipt_candidate_sha256",
])
def test_r88_anchor_transplant_rejected(field):
    *_, r88_binding = r88_context()
    anchor = authority_anchor(r88_binding)
    anchor[field] = "e" * 64
    with pytest.raises(ValueError, match="R88 mismatch"):
        build_with(r88_binding_override=r88_binding, anchor_override=anchor)


def test_fencing_token_transplant_rejected():
    *_, r88_binding = r88_context()
    anchor = authority_anchor(r88_binding)
    anchor["current_fencing_token"] += 1
    with pytest.raises(ValueError, match="R88 mismatch"):
        build_with(r88_binding_override=r88_binding, anchor_override=anchor)


def test_retained_reference_guard_is_mandatory():
    *_, r88_binding = r88_context()
    anchor = authority_anchor(r88_binding)
    anchor["retained_reference_required"] = False
    with pytest.raises(ValueError, match="retained-reference guard missing"):
        build_with(r88_binding_override=r88_binding, anchor_override=anchor)


@pytest.mark.parametrize("field", [
    "root_trust_verified", "anchor_operator_identity_verified", "live_writer_backend_proven",
    "durable_commit_proven", "global_current_state_verified", "concurrent_writer_exclusion_proven",
    "registry_write_performed", "lease_registry_write_performed", "receipt_index_write_performed",
    "backend_write_performed", "can_execute", "apply_allowed", "confers_authority",
])
def test_anchor_overclaims_rejected(field):
    *_, r88_binding = r88_context()
    anchor = authority_anchor(r88_binding)
    anchor[field] = True
    with pytest.raises(ValueError, match="anchor overclaim"):
        build_with(r88_binding_override=r88_binding, anchor_override=anchor)


def test_anchor_execution_authority_overclaim_rejected():
    *_, r88_binding = r88_context()
    anchor = authority_anchor(r88_binding)
    anchor["execution_authority"] = "LIVE"
    with pytest.raises(ValueError, match="execution authority overclaim"):
        build_with(r88_binding_override=r88_binding, anchor_override=anchor)


def test_anchor_extra_key_rejected():
    *_, r88_binding = r88_context()
    anchor = authority_anchor(r88_binding)
    anchor["hidden_trust_claim"] = True
    with pytest.raises(ValueError, match="anchor key set mismatch"):
        build_with(r88_binding_override=r88_binding, anchor_override=anchor)


def test_unsafe_policy_drift_rejected():
    p = writer_authority_anchor_policy()
    p["authority_root_trust_inference_allowed"] = True
    with pytest.raises(ValueError, match="unsafe writer-authority-anchor policy"):
        build_with(policy_override=p)


def test_bool_schema_version_rejected():
    p = writer_authority_anchor_policy()
    p["schema_version"] = True
    with pytest.raises(ValueError, match="unsupported writer-authority-anchor policy"):
        build_with(policy_override=p)


def test_tampered_r88_binding_rejected_by_full_r88_validation():
    *_, r88_binding = r88_context()
    tampered = clone(r88_binding)
    tampered["writer_lease_sha256"] = "d" * 64
    anchor = authority_anchor(tampered)
    with pytest.raises(ValueError):
        build_with(r88_binding_override=tampered, anchor_override=anchor)


def test_schema_required_keys_match_contract():
    schema = json.loads((ROOT / "schemas" / "TRADINGOS_EXTERNAL_ASSERTION_REPLAY_WRITER_AUTHORITY_ANCHOR_BINDING_V1.schema.json").read_text(encoding="utf-8"))
    assert set(schema["required"]) == m.BINDING_KEYS


def test_no_trust_durable_current_state_or_authority_upgrade():
    x = build()
    assert x["writer_authority_root_verified"] is False
    assert x["authority_anchor_operator_identity_verified"] is False
    assert x["live_writer_backend_proven"] is False
    assert x["durable_commit_proven"] is False
    assert x["durable_single_use_enforced"] is False
    assert x["global_current_state_verified"] is False
    assert x["concurrent_writer_exclusion_proven"] is False
    assert x["registry_write_performed"] is False
    assert x["lease_registry_write_performed"] is False
    assert x["receipt_index_write_performed"] is False
    assert x["backend_write_performed"] is False


def test_policy_and_output_authority_ceiling_is_exact():
    m.validate_writer_authority_anchor_policy(writer_authority_anchor_policy())
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

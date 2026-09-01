from __future__ import annotations

import json

import pytest

from r83_attestation_set_fixtures import set_policy as r83_set_policy
from r84_reviewer_key_possession_fixtures import m as r84m, policy as r84_policy
from r85_external_verifier_provenance_fixtures import (
    ROOT,
    AUTHORITY_ROOT_SHA256,
    m,
    provenance_policy,
    r84_context,
    verifier_registry,
    binding,
    clone,
)


def build_with(
    *,
    r84_binding_override=None,
    registry_snapshot=None,
    expected_registry_sha=None,
    expected_root=AUTHORITY_ROOT_SHA256,
    provenance_policy_override=None,
):
    items, manifest, aid, assertion, r84_binding = r84_context()
    r84_binding = r84_binding if r84_binding_override is None else r84_binding_override
    registry_snapshot = verifier_registry(r84_binding) if registry_snapshot is None else registry_snapshot
    expected_registry_sha = (
        m.stable_sha256(registry_snapshot)
        if expected_registry_sha is None
        else expected_registry_sha
    )
    return m.build_external_verifier_provenance_binding(
        r84_binding,
        manifest,
        items,
        r83_set_policy(),
        aid,
        assertion,
        registry_snapshot,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),
        key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=expected_registry_sha,
        expected_authority_root_sha256=expected_root,
        provenance_policy=(
            provenance_policy()
            if provenance_policy_override is None
            else provenance_policy_override
        ),
    )


def test_build_and_validate_provenance_binding():
    items, manifest, aid, assertion, r84_binding = r84_context()
    registry_snapshot = verifier_registry(r84_binding)
    x = build_with(r84_binding_override=r84_binding, registry_snapshot=registry_snapshot)
    m.validate_external_verifier_provenance_binding(
        x,
        r84_binding,
        manifest,
        items,
        r83_set_policy(),
        aid,
        assertion,
        registry_snapshot,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),
        key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=m.stable_sha256(registry_snapshot),
        expected_authority_root_sha256=AUTHORITY_ROOT_SHA256,
        provenance_policy=provenance_policy(),
    )
    assert x["schema"] == m.BINDING_SCHEMA


def test_binding_is_deterministic():
    assert binding() == binding()
    assert binding()["binding_id"] == binding()["binding_id"]


def test_exact_r84_binding_and_policy_are_digest_bound():
    *_, r84_binding = r84_context()
    x = binding()
    assert x["r84_binding_id"] == r84_binding["binding_id"]
    assert x["r84_binding_sha256"] == m.stable_sha256(r84_binding)
    assert x["provenance_policy_sha256"] == m.stable_sha256(provenance_policy())


def test_exact_registry_entry_is_bound():
    *_, r84_binding = r84_context()
    registry_snapshot = verifier_registry(r84_binding)
    x = build_with(r84_binding_override=r84_binding, registry_snapshot=registry_snapshot)
    expected_entry = registry_snapshot["entries"][0]
    assert x["verifier_registry_sha256"] == m.stable_sha256(registry_snapshot)
    assert x["registry_entry_sha256"] == m.stable_sha256(expected_entry)
    assert x["verifier_id"] == r84_binding["verifier_id"]
    assert x["verifier_key_id"] == r84_binding["verifier_key_id"]
    assert x["public_key_sha256"] == r84_binding["public_key_sha256"]
    assert x["algorithm"] == r84_binding["algorithm"]
    assert x["verifier_registry_entry_exact_match"] is True
    assert x["verifier_provenance_bound"] is True


def test_substituted_registry_rejected_against_retained_digest():
    *_, r84_binding = r84_context()
    original = verifier_registry(r84_binding)
    retained_digest = m.stable_sha256(original)
    changed = clone(original)
    changed["registry_id"] = "offline-verifier-registry-r85-substituted"
    with pytest.raises(ValueError, match="verifier registry digest mismatch"):
        build_with(
            r84_binding_override=r84_binding,
            registry_snapshot=changed,
            expected_registry_sha=retained_digest,
        )


def test_substituted_authority_root_rejected():
    with pytest.raises(ValueError, match="authority root digest mismatch"):
        build_with(expected_root="f" * 64)


def test_verifier_metadata_transplant_rejected():
    *_, r84_binding = r84_context()
    registry_snapshot = verifier_registry(r84_binding)
    registry_snapshot["entries"][0]["verifier_key_id"] = "other-verifier-key"
    with pytest.raises(ValueError, match="R84 verifier registry entry must match exactly once"):
        build_with(r84_binding_override=r84_binding, registry_snapshot=registry_snapshot)


def test_public_key_transplant_rejected():
    *_, r84_binding = r84_context()
    registry_snapshot = verifier_registry(r84_binding)
    registry_snapshot["entries"][0]["public_key_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="R84 verifier registry entry must match exactly once"):
        build_with(r84_binding_override=r84_binding, registry_snapshot=registry_snapshot)


def test_duplicate_registry_entry_rejected():
    *_, r84_binding = r84_context()
    registry_snapshot = verifier_registry(r84_binding)
    registry_snapshot["entries"].append(clone(registry_snapshot["entries"][0]))
    with pytest.raises(ValueError, match="duplicate verifier registry entry"):
        build_with(r84_binding_override=r84_binding, registry_snapshot=registry_snapshot)


def test_registry_trust_root_overclaim_rejected():
    *_, r84_binding = r84_context()
    registry_snapshot = verifier_registry(r84_binding)
    registry_snapshot["trust_root_verified"] = True
    with pytest.raises(ValueError, match="verifier registry trust-root overclaim"):
        build_with(r84_binding_override=r84_binding, registry_snapshot=registry_snapshot)


def test_unsafe_policy_drift_rejected():
    p = provenance_policy()
    p["network_access_in_core_allowed"] = True
    with pytest.raises(ValueError, match="unsafe provenance policy"):
        build_with(provenance_policy_override=p)


def test_tampered_r84_binding_rejected_by_full_r84_validation():
    *_, r84_binding = r84_context()
    tampered = clone(r84_binding)
    tampered["verifier_id"] = "substituted-verifier"
    registry_snapshot = verifier_registry(tampered)
    with pytest.raises(ValueError):
        build_with(r84_binding_override=tampered, registry_snapshot=registry_snapshot)


def test_schema_required_keys_match_contract():
    schema = json.loads(
        (ROOT / "schemas" / "TRADINGOS_EXTERNAL_VERIFIER_PROVENANCE_BINDING_V1.schema.json")
        .read_text(encoding="utf-8")
    )
    assert set(schema["required"]) == m.BINDING_KEYS


def test_no_trust_identity_freshness_consensus_or_approval_upgrade():
    x = binding()
    assert x["verifier_trust_root_verified"] is False
    assert x["registry_operator_identity_verified"] is False
    assert x["review_identity_verified"] is False
    assert x["physical_human_presence_proven"] is False
    assert x["assertion_freshness_verified"] is False
    assert x["distinct_reviewer_count_allowed"] is False
    assert x["consensus_inference_allowed"] is False
    assert x["approval_state_allowed"] is False
    forbidden = {
        "trusted", "trust_score", "reviewer_id", "reviewer_count", "distinct_reviewers",
        "vote", "votes", "quorum", "majority", "consensus", "approval", "approved",
        "recommendation", "recommended_action", "fresh", "liveness_verified",
    }
    assert forbidden.isdisjoint(x)


def test_policy_and_output_authority_ceiling_is_exact():
    m.validate_provenance_policy(provenance_policy())
    x = binding()
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

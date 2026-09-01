from __future__ import annotations

import json

from r84_reviewer_key_possession_fixtures import (
    ROOT, m, policy, upstream, challenge, external_assertion, binding
)
from r83_attestation_set_fixtures import set_policy as r83_set_policy


def test_build_and_validate_binding():
    items, evidence_set = upstream()
    aid = evidence_set["bindings"][0]["attestation_id"]
    assertion = external_assertion(aid)
    x = m.build_reviewer_key_possession_binding(
        evidence_set, items, r83_set_policy(),
        aid, assertion,
        expected_external_assertion_sha256=m.stable_sha256(assertion),
        key_possession_policy=policy(),
    )
    m.validate_reviewer_key_possession_binding(
        x, evidence_set, items, r83_set_policy(),
        aid, assertion,
        expected_external_assertion_sha256=m.stable_sha256(assertion),
        key_possession_policy=policy(),
    )
    assert x["schema"] == m.BINDING_SCHEMA


def test_canonical_challenge_is_deterministic():
    assert challenge() == challenge()
    assert m.stable_sha256(challenge()) == m.stable_sha256(challenge())


def test_challenge_binds_exact_r83_attestation_row():
    items, evidence_set = upstream()
    row = evidence_set["bindings"][0]
    c = challenge(row["attestation_id"])
    assert c["evidence_set_id"] == evidence_set["evidence_set_id"]
    assert c["evidence_set_sha256"] == m.stable_sha256(evidence_set)
    assert c["attestation_id"] == row["attestation_id"]
    assert c["attestation_sha256"] == row["attestation_sha256"]
    assert c["shadow_report_sha256"] == row["shadow_report_sha256"]
    assert c["review_policy_sha256"] == row["review_policy_sha256"]


def test_external_assertion_digest_is_exactly_bound():
    items, evidence_set = upstream()
    aid = evidence_set["bindings"][0]["attestation_id"]
    assertion = external_assertion(aid)
    x = binding(aid)
    assert x["external_assertion_sha256"] == m.stable_sha256(assertion)
    assert x["external_assertion_digest_consumed"] is True


def test_no_identity_or_freshness_upgrade():
    x = binding()
    assert x["review_identity_verified"] is False
    assert x["physical_human_presence_proven"] is False
    assert x["assertion_freshness_verified"] is False
    assert x["distinct_reviewer_count_allowed"] is False
    assert x["same_key_same_human_inference_allowed"] is False
    assert x["different_keys_different_humans_inference_allowed"] is False


def test_no_consensus_or_approval_upgrade():
    x = binding()
    assert x["consensus_inference_allowed"] is False
    assert x["approval_state_allowed"] is False
    forbidden = {
        "reviewer_id", "reviewer_count", "distinct_reviewers", "vote", "votes", "quorum",
        "majority", "consensus", "approval", "approved", "recommendation", "recommended_action",
    }
    assert forbidden.isdisjoint(x)


def test_same_key_on_two_attestations_does_not_create_same_human_claim():
    items, evidence_set = upstream()
    first, second = evidence_set["bindings"][:2]
    a = binding(first["attestation_id"], "b" * 64)
    b = binding(second["attestation_id"], "b" * 64)
    assert a["public_key_sha256"] == b["public_key_sha256"]
    assert a["same_key_same_human_inference_allowed"] is False
    assert b["same_key_same_human_inference_allowed"] is False


def test_schema_required_keys_match_contract():
    schema = json.loads(
        (ROOT / "schemas" / "TRADINGOS_REVIEWER_KEY_POSSESSION_ASSERTION_BINDING_V1.schema.json")
        .read_text(encoding="utf-8")
    )
    assert set(schema["required"]) == m.BINDING_KEYS


def test_policy_validates_and_authority_ceiling_is_exact():
    m.validate_key_possession_policy(policy())
    x = binding()
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

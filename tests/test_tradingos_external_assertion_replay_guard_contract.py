from __future__ import annotations

import json

import pytest

from r83_attestation_set_fixtures import set_policy as r83_set_policy
from r84_reviewer_key_possession_fixtures import m as r84m, policy as r84_policy
from r85_external_verifier_provenance_fixtures import (
    ROOT,
    AUTHORITY_ROOT_SHA256,
    m as r85m,
    provenance_policy,
)
from r86_external_assertion_replay_guard_fixtures import (
    m,
    replay_policy,
    r85_context,
    replay_registry,
    build,
    clone,
)


def build_with(
    *,
    r85_binding_override=None,
    replay_registry_override=None,
    expected_replay_registry_sha=None,
    replay_policy_override=None,
):
    items, manifest, aid, assertion, r84_binding, verifier_reg, r85_binding = r85_context()
    r85_binding = r85_binding if r85_binding_override is None else r85_binding_override
    replay_reg = replay_registry(r84_binding) if replay_registry_override is None else replay_registry_override
    expected_replay_registry_sha = (
        m.stable_sha256(replay_reg)
        if expected_replay_registry_sha is None
        else expected_replay_registry_sha
    )
    return m.build_external_assertion_replay_guard_binding(
        r85_binding,
        r84_binding,
        manifest,
        items,
        r83_set_policy(),
        aid,
        assertion,
        verifier_reg,
        replay_reg,
        expected_external_assertion_sha256=r84m.stable_sha256(assertion),
        key_possession_policy=r84_policy(),
        expected_verifier_registry_sha256=r85m.stable_sha256(verifier_reg),
        expected_authority_root_sha256=AUTHORITY_ROOT_SHA256,
        provenance_policy=provenance_policy(),
        expected_replay_registry_sha256=expected_replay_registry_sha,
        replay_guard_policy=(
            replay_policy() if replay_policy_override is None else replay_policy_override
        ),
    )


def test_build_and_validate_replay_guard_binding():
    x = build()
    assert x["schema"] == m.BINDING_SCHEMA
    assert x["replay_guard_candidate_bound"] is True


def test_binding_is_deterministic():
    assert build() == build()
    assert build()["binding_id"] == build()["binding_id"]


def test_r85_assertion_challenge_policy_and_registry_are_bound():
    *_, r84_binding, _, r85_binding = r85_context()
    reg = replay_registry(r84_binding)
    x = build_with(replay_registry_override=reg)
    assert x["r85_binding_id"] == r85_binding["binding_id"]
    assert x["r85_binding_sha256"] == m.stable_sha256(r85_binding)
    assert x["r84_binding_id"] == r84_binding["binding_id"]
    assert x["external_assertion_sha256"] == r84_binding["external_assertion_sha256"]
    assert x["challenge_sha256"] == r84_binding["challenge_sha256"]
    assert x["replay_policy_sha256"] == m.stable_sha256(replay_policy())
    assert x["replay_registry_sha256"] == m.stable_sha256(reg)


def test_generation_increments_exactly_one_and_candidate_is_digest_bound():
    *_, r84_binding, _, _ = r85_context()
    reg = replay_registry(r84_binding)
    x = build_with(replay_registry_override=reg)
    candidate = m._build_next_registry_candidate(
        reg,
        m.stable_sha256(reg),
        reg["generation"],
        reg["used_external_assertion_sha256s"],
        reg["used_challenge_sha256s"],
        r84_binding["external_assertion_sha256"],
        r84_binding["challenge_sha256"],
    )
    assert x["prior_generation"] == 7
    assert x["next_generation"] == 8
    assert x["next_registry_candidate_sha256"] == m.stable_sha256(candidate)


def test_substituted_replay_registry_rejected_against_retained_digest():
    *_, r84_binding, _, _ = r85_context()
    original = replay_registry(r84_binding)
    retained = m.stable_sha256(original)
    changed = clone(original)
    changed["generation"] = 8
    with pytest.raises(ValueError, match="replay registry digest mismatch"):
        build_with(
            replay_registry_override=changed,
            expected_replay_registry_sha=retained,
        )


def test_external_assertion_replay_rejected():
    *_, r84_binding, _, _ = r85_context()
    reg = replay_registry(r84_binding)
    reg["used_external_assertion_sha256s"] = sorted(
        [*reg["used_external_assertion_sha256s"], r84_binding["external_assertion_sha256"]]
    )
    with pytest.raises(ValueError, match="external assertion replay detected"):
        build_with(replay_registry_override=reg)


def test_challenge_replay_rejected():
    *_, r84_binding, _, _ = r85_context()
    reg = replay_registry(r84_binding)
    reg["used_challenge_sha256s"] = sorted(
        [*reg["used_challenge_sha256s"], r84_binding["challenge_sha256"]]
    )
    with pytest.raises(ValueError, match="challenge replay detected"):
        build_with(replay_registry_override=reg)


@pytest.mark.parametrize(
    "field",
    ["used_external_assertion_sha256s", "used_challenge_sha256s"],
)
def test_unsorted_or_duplicate_digest_sets_rejected(field):
    *_, r84_binding, _, _ = r85_context()
    reg = replay_registry(r84_binding)
    reg[field] = ["b" * 64, "a" * 64]
    with pytest.raises(ValueError, match="must be sorted and unique"):
        build_with(replay_registry_override=reg)


def test_registry_durability_overclaim_rejected():
    *_, r84_binding, _, _ = r85_context()
    reg = replay_registry(r84_binding)
    reg["durable_commit_proven"] = True
    with pytest.raises(ValueError, match="durability overclaim"):
        build_with(replay_registry_override=reg)


def test_unsafe_policy_drift_rejected():
    p = replay_policy()
    p["registry_write_allowed"] = True
    with pytest.raises(ValueError, match="unsafe replay policy"):
        build_with(replay_policy_override=p)


def test_tampered_r85_binding_rejected_by_full_r85_validation():
    *_, r84_binding, _, r85_binding = r85_context()
    tampered = clone(r85_binding)
    tampered["registry_id"] = "substituted-registry"
    with pytest.raises(ValueError):
        build_with(r85_binding_override=tampered)


def test_schema_required_keys_match_contract():
    schema = json.loads(
        (ROOT / "schemas" / "TRADINGOS_EXTERNAL_ASSERTION_REPLAY_GUARD_BINDING_V1.schema.json")
        .read_text(encoding="utf-8")
    )
    assert set(schema["required"]) == m.BINDING_KEYS


def test_no_durable_freshness_identity_consensus_or_authority_upgrade():
    x = build()
    assert x["durable_single_use_enforced"] is False
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
    m.validate_replay_guard_policy(replay_policy())
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

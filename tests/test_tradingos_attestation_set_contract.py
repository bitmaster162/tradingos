from __future__ import annotations

import json
from pathlib import Path

from r83_attestation_set_fixtures import m, set_policy, evidence_items, same_report_multiple_attestations, manifest, ROOT


def test_build_valid_manifest():
    items = evidence_items()
    x = m.build_attestation_evidence_set(items, set_policy())
    m.validate_attestation_evidence_set(x, items, set_policy())
    assert x["schema"] == m.EVIDENCE_SET_SCHEMA
    assert x["item_count"] == 3
    assert x["review_identity_verified"] is False
    assert x["consensus_inference_allowed"] is False
    assert x["approval_state_allowed"] is False


def test_input_order_normalized():
    items = evidence_items()
    a = m.build_attestation_evidence_set(items, set_policy())
    b = m.build_attestation_evidence_set(list(reversed(items)), set_policy())
    assert a == b


def test_same_report_multiple_attestations_allowed_without_identity_claim():
    items = same_report_multiple_attestations()
    x = m.build_attestation_evidence_set(items, set_policy())
    assert x["item_count"] == 2
    assert len({b["shadow_report_id"] for b in x["bindings"]}) == 1
    assert "reviewer_count" not in x
    assert "consensus" not in x


def test_bindings_expose_no_disposition_or_reason_aggregation():
    x = manifest()
    for b in x["bindings"]:
        assert "disposition" not in b
        assert "reason_codes" not in b
        assert "reviewer_id" not in b
    forbidden = {"reviewer_count", "distinct_reviewers", "majority", "consensus", "approval", "approved", "votes", "counts_by_disposition", "counts_by_reason"}
    assert forbidden.isdisjoint(x)


def test_manifest_id_is_deterministic():
    items = evidence_items()
    ids = {m.build_attestation_evidence_set(items, set_policy())["evidence_set_id"] for _ in range(100)}
    assert len(ids) == 1


def test_schema_required_keys_match_contract():
    schema = json.loads((ROOT / "schemas" / "TRADINGOS_ATTESTATION_EVIDENCE_SET_V1.schema.json").read_text(encoding="utf-8"))
    assert set(schema["required"]) == m.MANIFEST_KEYS
    assert set(schema["properties"]["bindings"]["items"]["required"]) == m.BINDING_KEYS
    assert set(schema["properties"]["integrity"]["required"]) == m.INTEGRITY_KEYS


def test_policy_validates():
    m.validate_evidence_set_policy(set_policy())


def test_exact_authority_ceiling():
    x = manifest()
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


def test_manifest_bindings_are_sorted():
    x = manifest()
    ids = [b["attestation_id"] for b in x["bindings"]]
    assert ids == sorted(ids)

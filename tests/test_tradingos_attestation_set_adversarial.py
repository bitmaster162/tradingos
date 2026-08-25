from __future__ import annotations

import ast
import copy
from pathlib import Path
import pytest

from r83_attestation_set_fixtures import m, set_policy, evidence_item, evidence_items, manifest, clone, ROOT


@pytest.mark.parametrize("extra_key", [
    "reviewer_id", "reviewer_name", "reviewer_email", "signature", "public_key",
    "vote", "weight", "quorum", "majority", "consensus", "approval", "approved",
    "recommendation", "recommended_action", "probability", "rate", "confidence",
    "pnl", "price", "return", "model_rank", "provider_rank", "model_choice",
    "provider_choice", "policy_update", "prompt_update", "weight_update", "signal",
    "order", "wallet", "position_size", "entry", "stop", "take_profit", "execute",
    "credential", "free_text", "notes",
])
def test_evidence_item_rejects_extra_fields(extra_key):
    item = evidence_item(); item[extra_key] = "forbidden"
    with pytest.raises(ValueError, match="evidence item key set mismatch"):
        m.build_attestation_evidence_set([item], set_policy())


@pytest.mark.parametrize("bad", [None, {}, "x", 7, True, tuple()])
def test_evidence_items_must_be_list(bad):
    with pytest.raises(ValueError):
        m.build_attestation_evidence_set(bad, set_policy())


def test_empty_set_rejected():
    with pytest.raises(ValueError, match="outside policy bounds"):
        m.build_attestation_evidence_set([], set_policy())


def test_over_max_set_rejected():
    item = evidence_item()
    with pytest.raises(ValueError, match="outside policy bounds"):
        m.build_attestation_evidence_set([copy.deepcopy(item) for _ in range(65)], set_policy())


def test_duplicate_attestation_id_rejected():
    item = evidence_item()
    with pytest.raises(ValueError, match="duplicate attestation id"):
        m.build_attestation_evidence_set([copy.deepcopy(item), copy.deepcopy(item)], set_policy())


def test_same_attestation_payload_rejected_even_if_id_forged():
    a = evidence_item(); b = clone(a)
    b["attestation"]["attestation_id"] = "f" * 24
    with pytest.raises(ValueError):
        m.build_attestation_evidence_set([a, b], set_policy())


def test_substituted_report_rejected():
    item = evidence_item(); item["report"]["record_count"] += 1
    with pytest.raises(ValueError):
        m.build_attestation_evidence_set([item], set_policy())


def test_substituted_review_policy_rejected():
    item = evidence_item(); item["review_policy"]["allow_free_text"] = True
    with pytest.raises(ValueError):
        m.build_attestation_evidence_set([item], set_policy())


def test_mixed_review_policy_hashes_rejected():
    a = evidence_item("ACKNOWLEDGED", ["INTEGRITY_CONFIRMED"], "a")
    b = evidence_item("DISPUTED", ["SOURCE_BINDING_CONCERN"], "b")
    # Create a second, internally valid R82 review policy variant is intentionally impossible under R82 V1.
    # Simulate a mixed-policy artifact only after construction; R82 validation must fail before set admission.
    b["attestation"]["review_policy_sha256"] = "e" * 64
    with pytest.raises(ValueError):
        m.build_attestation_evidence_set([a, b], set_policy())


@pytest.mark.parametrize("field,bad", [
    ("require_full_r82_validation", False),
    ("require_homogeneous_review_policy", False),
    ("allow_same_shadow_report_multiple_attestations", False),
    ("human_review_only", False),
    ("shadow_only", False),
    ("reviewer_identity_inference_allowed", True),
    ("distinct_reviewer_count_allowed", True),
    ("consensus_inference_allowed", True),
    ("disposition_aggregation_allowed", True),
    ("reason_aggregation_allowed", True),
    ("approval_state_allowed", True),
    ("recommendations_allowed", True),
    ("probability_outputs_allowed", True),
    ("rate_outputs_allowed", True),
    ("confidence_outputs_allowed", True),
    ("pnl_fields_allowed", True),
    ("price_return_fields_allowed", True),
    ("model_ranking_allowed", True),
    ("provider_ranking_allowed", True),
    ("external_sources_allowed", True),
    ("persistence_in_core_allowed", True),
    ("policy_update_allowed", True),
    ("live_decision_feedback_allowed", True),
    ("live_decision_use_allowed", True),
    ("model_selection_use_allowed", True),
    ("attestation_set_consumption_authority", "READ"),
    ("memory_write_authority", "WRITE"),
])
def test_policy_guard_drift_rejected(field, bad):
    p = set_policy(); p[field] = bad
    with pytest.raises(ValueError):
        m.build_attestation_evidence_set(evidence_items(), p)


@pytest.mark.parametrize("field,bad", [
    ("shadow_only", False), ("human_review_only", False), ("review_identity_verified", True),
    ("consensus_inference_allowed", True), ("approval_state_allowed", True),
    ("attestation_set_consumption_authority", "READ"), ("memory_write_authority", "WRITE"),
    ("policy_update_allowed", True), ("live_decision_feedback_allowed", True),
    ("live_decision_use_allowed", True), ("model_selection_use_allowed", True),
    ("execution_authority", "TRADE"), ("can_trade", True), ("capital_permission", "ALLOW"),
    ("confers_authority", True),
])
def test_manifest_ceiling_tamper_rejected(field, bad):
    items=evidence_items(); x=manifest(items); x[field]=bad
    with pytest.raises(ValueError):
        m.validate_attestation_evidence_set(x, items, set_policy())


@pytest.mark.parametrize("field", [
    "schema", "review_policy_sha256", "item_count", "bindings", "integrity", "shadow_only",
    "human_review_only", "review_identity_verified", "consensus_inference_allowed",
    "approval_state_allowed", "attestation_set_consumption_authority", "memory_write_authority",
    "policy_update_allowed", "live_decision_feedback_allowed", "live_decision_use_allowed",
    "model_selection_use_allowed", "execution_authority", "can_trade", "capital_permission",
    "confers_authority",
])
def test_manifest_identity_tamper_rejected(field):
    items=evidence_items(); x=manifest(items)
    if field == "schema": x[field] = "bad"
    elif field == "review_policy_sha256": x[field] = "0" * 64
    elif field == "item_count": x[field] += 1
    elif field == "bindings": x[field][0]["attestation_sha256"] = "0" * 64
    elif field == "integrity": x[field]["bindings_exact"] = False
    elif isinstance(x[field], bool): x[field] = not x[field]
    elif field in ("attestation_set_consumption_authority", "memory_write_authority", "execution_authority"): x[field] = "BAD"
    elif field == "capital_permission": x[field] = "ALLOW"
    else: x[field] = "bad"
    with pytest.raises(ValueError):
        m.validate_attestation_evidence_set(x, items, set_policy())


@pytest.mark.parametrize("field", ["attestation_id", "attestation_sha256", "shadow_report_id", "shadow_report_sha256", "review_policy_sha256"])
def test_binding_tamper_rejected(field):
    items=evidence_items(); x=manifest(items)
    x["bindings"][0][field] = ("f" * 24 if field.endswith("_id") else "f" * 64)
    with pytest.raises(ValueError):
        m.validate_attestation_evidence_set(x, items, set_policy())


def test_binding_reorder_rejected():
    items=evidence_items(); x=manifest(items); x["bindings"] = list(reversed(x["bindings"]))
    with pytest.raises(ValueError):
        m.validate_attestation_evidence_set(x, items, set_policy())


def test_manifest_extra_key_rejected():
    items=evidence_items(); x=manifest(items); x["reviewer_count"] = 3
    with pytest.raises(ValueError, match="manifest key set mismatch"):
        m.validate_attestation_evidence_set(x, items, set_policy())


def test_binding_extra_key_rejected():
    items=evidence_items(); x=manifest(items); x["bindings"][0]["disposition"] = "ACKNOWLEDGED"
    with pytest.raises(ValueError):
        m.validate_attestation_evidence_set(x, items, set_policy())


def test_integrity_extra_key_rejected():
    items=evidence_items(); x=manifest(items); x["integrity"]["consensus_reached"] = True
    with pytest.raises(ValueError):
        m.validate_attestation_evidence_set(x, items, set_policy())


def test_evidence_set_id_tamper_rejected():
    items=evidence_items(); x=manifest(items); x["evidence_set_id"] = "f" * 24
    with pytest.raises(ValueError, match="binding mismatch"):
        m.validate_attestation_evidence_set(x, items, set_policy())


def test_core_imports_are_side_effect_free():
    forbidden = {"os", "subprocess", "socket", "requests", "urllib", "httpx", "sqlite3", "pathlib"}
    for rel in ("tools/tradingos_attestation_set_common.py", "tools/tradingos_attestation_set_contract.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import): imported.update(a.name.split('.')[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module: imported.add(node.module.split('.')[0])
        assert forbidden.isdisjoint(imported)


def test_core_contains_no_dangerous_call_names():
    forbidden_names = {"open", "exec", "eval", "compile"}
    forbidden_attrs = {"system", "popen", "run", "call", "check_output", "remove", "unlink", "rmdir", "mkdir", "write_text", "write_bytes", "connect", "send", "post", "put", "delete"}
    for rel in ("tools/tradingos_attestation_set_common.py", "tools/tradingos_attestation_set_contract.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        called_names = set()
        called_attrs = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name): called_names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute): called_attrs.add(node.func.attr)
        assert forbidden_names.isdisjoint(called_names)
        assert forbidden_attrs.isdisjoint(called_attrs)

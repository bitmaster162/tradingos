from __future__ import annotations

import ast
import pytest

from r83_attestation_set_fixtures import set_policy as r83_set_policy
from r84_reviewer_key_possession_fixtures import (
    ROOT, m, policy, upstream, challenge, external_assertion, binding, clone
)


def build_with(assertion, *, expected_sha=None, aid=None, p=None):
    items, evidence_set = upstream()
    if aid is None:
        aid = evidence_set["bindings"][0]["attestation_id"]
    if expected_sha is None:
        expected_sha = m.stable_sha256(assertion)
    if p is None:
        p = policy()
    return m.build_reviewer_key_possession_binding(
        evidence_set, items, r83_set_policy(), aid, assertion,
        expected_external_assertion_sha256=expected_sha,
        key_possession_policy=p,
    )


def test_wrong_external_assertion_digest_rejected():
    a = external_assertion()
    with pytest.raises(ValueError, match="external assertion digest mismatch"):
        build_with(a, expected_sha="f" * 64)


def test_substituted_r83_manifest_rejected_before_binding():
    items, evidence_set = upstream()
    aid = evidence_set["bindings"][0]["attestation_id"]
    a = external_assertion(aid)
    evidence_set["bindings"][0]["attestation_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        m.build_reviewer_key_possession_binding(
            evidence_set, items, r83_set_policy(), aid, a,
            expected_external_assertion_sha256=m.stable_sha256(a),
            key_possession_policy=policy(),
        )


def test_unknown_attestation_id_rejected():
    items, evidence_set = upstream()
    with pytest.raises(ValueError, match="attestation binding must exist exactly once"):
        m.build_reviewer_key_possession_challenge(
            evidence_set, items, r83_set_policy(), "f" * 24, policy()
        )


def test_assertion_challenge_substitution_rejected():
    a = external_assertion()
    a["challenge_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="challenge mismatch"):
        build_with(a)


def test_external_assertion_extra_key_rejected():
    a = external_assertion()
    a["reviewer_id"] = "someone"
    with pytest.raises(ValueError, match="external assertion key set mismatch"):
        build_with(a)


@pytest.mark.parametrize("field,bad", [
    ("signature_verified_by_external_asymmetric_verifier", False),
    ("local_signature_math_verified", True),
    ("assertion_scope", "IDENTITY_VERIFIED"),
    ("review_identity_verified", True),
    ("physical_human_presence_proven", True),
    ("confers_authority", True),
    ("algorithm", "RSA"),
])
def test_external_assertion_overclaim_or_algorithm_drift_rejected(field, bad):
    a = external_assertion()
    a[field] = bad
    with pytest.raises(ValueError):
        build_with(a)


@pytest.mark.parametrize("field,bad", [
    ("require_full_r83_validation", False),
    ("require_exact_attestation_binding", False),
    ("require_expected_external_assertion_digest", False),
    ("require_external_signature_verifier_assertion", False),
    ("require_local_signature_math_false", False),
    ("external_assertion_input_allowed", False),
    ("network_access_in_core_allowed", True),
    ("credential_access_in_core_allowed", True),
    ("raw_signature_bytes_in_core_allowed", True),
    ("raw_public_key_bytes_in_core_allowed", True),
    ("reviewer_identity_inference_allowed", True),
    ("distinct_reviewer_count_allowed", True),
    ("same_key_same_human_inference_allowed", True),
    ("different_keys_different_humans_inference_allowed", True),
    ("physical_human_presence_inference_allowed", True),
    ("assertion_freshness_inference_allowed", True),
    ("consensus_inference_allowed", True),
    ("approval_state_allowed", True),
    ("recommendations_allowed", True),
    ("policy_update_allowed", True),
    ("live_decision_feedback_allowed", True),
    ("live_decision_use_allowed", True),
    ("model_selection_use_allowed", True),
    ("persistence_in_core_allowed", True),
    ("human_review_only", False),
    ("shadow_only", False),
    ("attestation_set_consumption_authority", "READ"),
    ("memory_write_authority", "WRITE"),
])
def test_policy_guard_drift_rejected(field, bad):
    p = policy()
    p[field] = bad
    with pytest.raises(ValueError):
        m.validate_key_possession_policy(p)


def test_algorithm_allowlist_drift_rejected():
    p = policy()
    p["allowed_algorithms"] = ["ED25519", "ES256", "RSA"]
    with pytest.raises(ValueError, match="algorithm allowlist drift"):
        m.validate_key_possession_policy(p)


@pytest.mark.parametrize("field,bad", [
    ("evidence_set_sha256", "f" * 64),
    ("attestation_sha256", "f" * 64),
    ("challenge_sha256", "f" * 64),
    ("external_assertion_sha256", "f" * 64),
    ("external_assertion_digest_consumed", False),
    ("key_possession_evidence", "IDENTITY_VERIFIED"),
    ("local_signature_math_verified", True),
    ("review_identity_verified", True),
    ("distinct_reviewer_count_allowed", True),
    ("same_key_same_human_inference_allowed", True),
    ("different_keys_different_humans_inference_allowed", True),
    ("physical_human_presence_proven", True),
    ("assertion_freshness_verified", True),
    ("consensus_inference_allowed", True),
    ("approval_state_allowed", True),
    ("attestation_set_consumption_authority", "READ"),
    ("memory_write_authority", "WRITE"),
    ("policy_update_allowed", True),
    ("live_decision_feedback_allowed", True),
    ("live_decision_use_allowed", True),
    ("model_selection_use_allowed", True),
    ("execution_authority", "TRADE"),
    ("can_trade", True),
    ("capital_permission", "ALLOW"),
    ("confers_authority", True),
])
def test_binding_tamper_rejected(field, bad):
    items, evidence_set = upstream()
    aid = evidence_set["bindings"][0]["attestation_id"]
    assertion = external_assertion(aid)
    x = binding(aid)
    x[field] = bad
    with pytest.raises(ValueError):
        m.validate_reviewer_key_possession_binding(
            x, evidence_set, items, r83_set_policy(), aid, assertion,
            expected_external_assertion_sha256=m.stable_sha256(assertion),
            key_possession_policy=policy(),
        )


def test_binding_id_tamper_rejected():
    items, evidence_set = upstream()
    aid = evidence_set["bindings"][0]["attestation_id"]
    assertion = external_assertion(aid)
    x = binding(aid)
    x["binding_id"] = "f" * 24
    with pytest.raises(ValueError, match="binding_id binding mismatch"):
        m.validate_reviewer_key_possession_binding(
            x, evidence_set, items, r83_set_policy(), aid, assertion,
            expected_external_assertion_sha256=m.stable_sha256(assertion),
            key_possession_policy=policy(),
        )


def test_binding_extra_key_rejected():
    items, evidence_set = upstream()
    aid = evidence_set["bindings"][0]["attestation_id"]
    assertion = external_assertion(aid)
    x = binding(aid)
    x["reviewer_name"] = "forbidden"
    with pytest.raises(ValueError, match="binding key set mismatch"):
        m.validate_reviewer_key_possession_binding(
            x, evidence_set, items, r83_set_policy(), aid, assertion,
            expected_external_assertion_sha256=m.stable_sha256(assertion),
            key_possession_policy=policy(),
        )


def test_core_imports_are_side_effect_free():
    forbidden = {"os", "subprocess", "socket", "requests", "urllib", "httpx", "sqlite3", "pathlib"}
    for rel in (
        "tools/tradingos_reviewer_key_possession_common.py",
        "tools/tradingos_reviewer_key_possession_contract.py",
    ):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert forbidden.isdisjoint(imported)


def test_core_contains_no_dangerous_call_names():
    forbidden_names = {"open", "exec", "eval", "compile"}
    forbidden_attrs = {
        "system", "popen", "run", "call", "check_output", "remove", "unlink", "rmdir",
        "mkdir", "write_text", "write_bytes", "connect", "send", "post", "put", "delete",
    }
    for rel in (
        "tools/tradingos_reviewer_key_possession_common.py",
        "tools/tradingos_reviewer_key_possession_contract.py",
    ):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        called_names = set()
        called_attrs = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    called_names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    called_attrs.add(node.func.attr)
        assert forbidden_names.isdisjoint(called_names)
        assert forbidden_attrs.isdisjoint(called_attrs)

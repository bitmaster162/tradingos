"""TradingOS R84 deterministic reviewer key-possession assertion-binding contract."""
from __future__ import annotations

import hashlib
from typing import Any

from tools import tradingos_attestation_set_contract as r83
from tools.tradingos_reviewer_key_possession_common import *
from tools.tradingos_reviewer_key_possession_common import _ID24_RE, _SHA64_RE

CHALLENGE_KEYS = {
    "schema", "purpose", "evidence_set_id", "evidence_set_sha256", "attestation_id",
    "attestation_sha256", "shadow_report_id", "shadow_report_sha256", "review_policy_sha256",
}
EXTERNAL_ASSERTION_KEYS = {
    "schema", "challenge_sha256", "public_key_sha256", "key_id", "algorithm",
    "verifier_id", "verifier_key_id", "signature_verified_by_external_asymmetric_verifier",
    "local_signature_math_verified", "assertion_scope", "review_identity_verified",
    "physical_human_presence_proven", "confers_authority",
}
BINDING_KEYS = {
    "schema", "binding_id", "evidence_set_id", "evidence_set_sha256", "attestation_id",
    "attestation_sha256", "shadow_report_id", "shadow_report_sha256", "review_policy_sha256",
    "challenge_sha256", "external_assertion_sha256", "external_assertion_digest_consumed",
    "public_key_sha256", "key_id", "algorithm", "verifier_id", "verifier_key_id",
    "key_possession_evidence", "local_signature_math_verified", "review_identity_verified",
    "distinct_reviewer_count_allowed", "same_key_same_human_inference_allowed",
    "different_keys_different_humans_inference_allowed", "physical_human_presence_proven",
    "assertion_freshness_verified", "consensus_inference_allowed", "approval_state_allowed",
    "attestation_set_consumption_authority", "memory_write_authority", "policy_update_allowed",
    "live_decision_feedback_allowed", "live_decision_use_allowed", "model_selection_use_allowed",
    "execution_authority", "can_trade", "capital_permission", "confers_authority",
}


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA64_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be lowercase sha256")
    return value


def _id24(value: Any, field: str) -> str:
    if not isinstance(value, str) or _ID24_RE.fullmatch(value) is None:
        raise ValueError(f"{field} invalid")
    return value


def _token(value: Any, field: str) -> str:
    if not isinstance(value, str) or value != value.strip() or not 1 <= len(value) <= 128:
        raise ValueError(f"{field} invalid")
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in value):
        raise ValueError(f"{field} invalid")
    return value


def _find_binding(evidence_set: dict[str, Any], attestation_id: str) -> dict[str, Any]:
    aid = _id24(attestation_id, "attestation_id")
    matches = [row for row in evidence_set["bindings"] if row.get("attestation_id") == aid]
    if len(matches) != 1:
        raise ValueError("attestation binding must exist exactly once")
    return matches[0]


def build_reviewer_key_possession_challenge(
    evidence_set: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    set_policy: dict[str, Any],
    attestation_id: str,
    key_possession_policy: dict[str, Any],
) -> dict[str, Any]:
    validate_key_possession_policy(key_possession_policy)
    r83.validate_attestation_evidence_set(evidence_set, evidence_items, set_policy)
    binding = _find_binding(evidence_set, attestation_id)
    return {
        "schema": CHALLENGE_SCHEMA,
        "purpose": "R84_REVIEWER_KEY_POSSESSION_BINDING_ONLY",
        "evidence_set_id": evidence_set["evidence_set_id"],
        "evidence_set_sha256": stable_sha256(evidence_set),
        "attestation_id": binding["attestation_id"],
        "attestation_sha256": binding["attestation_sha256"],
        "shadow_report_id": binding["shadow_report_id"],
        "shadow_report_sha256": binding["shadow_report_sha256"],
        "review_policy_sha256": binding["review_policy_sha256"],
    }


def _validate_external_assertion(
    external_assertion: Any,
    challenge: dict[str, Any],
    expected_external_assertion_sha256: str,
    key_possession_policy: dict[str, Any],
) -> str:
    if not isinstance(external_assertion, dict) or set(external_assertion) != EXTERNAL_ASSERTION_KEYS:
        raise ValueError("external assertion key set mismatch")
    if external_assertion.get("schema") != EXTERNAL_ASSERTION_SCHEMA:
        raise ValueError("unsupported external assertion schema")
    expected_digest = _sha(expected_external_assertion_sha256, "expected_external_assertion_sha256")
    computed_digest = stable_sha256(external_assertion)
    if computed_digest != expected_digest:
        raise ValueError("external assertion digest mismatch")
    challenge_sha = stable_sha256(challenge)
    if external_assertion.get("challenge_sha256") != challenge_sha:
        raise ValueError("external assertion challenge mismatch")
    _sha(external_assertion.get("public_key_sha256"), "public_key_sha256")
    _token(external_assertion.get("key_id"), "key_id")
    algorithm = _token(external_assertion.get("algorithm"), "algorithm")
    if algorithm not in key_possession_policy["allowed_algorithms"]:
        raise ValueError("unsupported external assertion algorithm")
    _token(external_assertion.get("verifier_id"), "verifier_id")
    _token(external_assertion.get("verifier_key_id"), "verifier_key_id")
    if external_assertion.get("signature_verified_by_external_asymmetric_verifier") is not True:
        raise ValueError("external asymmetric verifier assertion missing")
    if external_assertion.get("local_signature_math_verified") is not False:
        raise ValueError("local signature math overclaim")
    if external_assertion.get("assertion_scope") != "REVIEWER_KEY_POSSESSION_ONLY":
        raise ValueError("external assertion scope invalid")
    if external_assertion.get("review_identity_verified") is not False:
        raise ValueError("review identity overclaim")
    if external_assertion.get("physical_human_presence_proven") is not False:
        raise ValueError("physical human presence overclaim")
    if external_assertion.get("confers_authority") is not False:
        raise ValueError("external assertion authority overclaim")
    return computed_digest


def _binding_identity_payload(binding: dict[str, Any]) -> dict[str, Any]:
    return {key: binding[key] for key in BINDING_KEYS if key != "binding_id"}


def _expected_binding_id(binding: dict[str, Any]) -> str:
    return hashlib.sha256(
        f"{BINDING_SCHEMA}:{VERSION}:".encode("utf-8")
        + stable_json_bytes(_binding_identity_payload(binding))
    ).hexdigest()[:24]


def build_reviewer_key_possession_binding(
    evidence_set: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    set_policy: dict[str, Any],
    attestation_id: str,
    external_assertion: dict[str, Any],
    *,
    expected_external_assertion_sha256: str,
    key_possession_policy: dict[str, Any],
) -> dict[str, Any]:
    challenge = build_reviewer_key_possession_challenge(
        evidence_set, evidence_items, set_policy, attestation_id, key_possession_policy
    )
    assertion_sha = _validate_external_assertion(
        external_assertion, challenge, expected_external_assertion_sha256, key_possession_policy
    )
    binding = {
        "schema": BINDING_SCHEMA,
        "evidence_set_id": challenge["evidence_set_id"],
        "evidence_set_sha256": challenge["evidence_set_sha256"],
        "attestation_id": challenge["attestation_id"],
        "attestation_sha256": challenge["attestation_sha256"],
        "shadow_report_id": challenge["shadow_report_id"],
        "shadow_report_sha256": challenge["shadow_report_sha256"],
        "review_policy_sha256": challenge["review_policy_sha256"],
        "challenge_sha256": stable_sha256(challenge),
        "external_assertion_sha256": assertion_sha,
        "external_assertion_digest_consumed": True,
        "public_key_sha256": external_assertion["public_key_sha256"],
        "key_id": external_assertion["key_id"],
        "algorithm": external_assertion["algorithm"],
        "verifier_id": external_assertion["verifier_id"],
        "verifier_key_id": external_assertion["verifier_key_id"],
        "key_possession_evidence": "EXTERNAL_ASYMMETRIC_VERIFIER_ASSERTION_DIGEST_BOUND",
        "local_signature_math_verified": False,
        "review_identity_verified": False,
        "distinct_reviewer_count_allowed": False,
        "same_key_same_human_inference_allowed": False,
        "different_keys_different_humans_inference_allowed": False,
        "physical_human_presence_proven": False,
        "assertion_freshness_verified": False,
        "consensus_inference_allowed": False,
        "approval_state_allowed": False,
        "attestation_set_consumption_authority": "NONE",
        "memory_write_authority": "NONE",
        "policy_update_allowed": False,
        "live_decision_feedback_allowed": False,
        "live_decision_use_allowed": False,
        "model_selection_use_allowed": False,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }
    binding["binding_id"] = _expected_binding_id(binding)
    validate_reviewer_key_possession_binding(
        binding,
        evidence_set,
        evidence_items,
        set_policy,
        attestation_id,
        external_assertion,
        expected_external_assertion_sha256=expected_external_assertion_sha256,
        key_possession_policy=key_possession_policy,
    )
    return binding


def validate_reviewer_key_possession_binding(
    binding: Any,
    evidence_set: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    set_policy: dict[str, Any],
    attestation_id: str,
    external_assertion: dict[str, Any],
    *,
    expected_external_assertion_sha256: str,
    key_possession_policy: dict[str, Any],
) -> None:
    challenge = build_reviewer_key_possession_challenge(
        evidence_set, evidence_items, set_policy, attestation_id, key_possession_policy
    )
    assertion_sha = _validate_external_assertion(
        external_assertion, challenge, expected_external_assertion_sha256, key_possession_policy
    )
    if not isinstance(binding, dict) or set(binding) != BINDING_KEYS:
        raise ValueError("key-possession binding key set mismatch")
    if binding.get("schema") != BINDING_SCHEMA:
        raise ValueError("unsupported key-possession binding schema")
    _id24(binding.get("binding_id"), "binding_id")

    exact = {
        "evidence_set_id": challenge["evidence_set_id"],
        "evidence_set_sha256": challenge["evidence_set_sha256"],
        "attestation_id": challenge["attestation_id"],
        "attestation_sha256": challenge["attestation_sha256"],
        "shadow_report_id": challenge["shadow_report_id"],
        "shadow_report_sha256": challenge["shadow_report_sha256"],
        "review_policy_sha256": challenge["review_policy_sha256"],
        "challenge_sha256": stable_sha256(challenge),
        "external_assertion_sha256": assertion_sha,
        "external_assertion_digest_consumed": True,
        "public_key_sha256": external_assertion["public_key_sha256"],
        "key_id": external_assertion["key_id"],
        "algorithm": external_assertion["algorithm"],
        "verifier_id": external_assertion["verifier_id"],
        "verifier_key_id": external_assertion["verifier_key_id"],
        "key_possession_evidence": "EXTERNAL_ASYMMETRIC_VERIFIER_ASSERTION_DIGEST_BOUND",
        "local_signature_math_verified": False,
        "review_identity_verified": False,
        "distinct_reviewer_count_allowed": False,
        "same_key_same_human_inference_allowed": False,
        "different_keys_different_humans_inference_allowed": False,
        "physical_human_presence_proven": False,
        "assertion_freshness_verified": False,
        "consensus_inference_allowed": False,
        "approval_state_allowed": False,
        "attestation_set_consumption_authority": "NONE",
        "memory_write_authority": "NONE",
        "policy_update_allowed": False,
        "live_decision_feedback_allowed": False,
        "live_decision_use_allowed": False,
        "model_selection_use_allowed": False,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }
    for key, expected in exact.items():
        if binding.get(key) != expected or type(binding.get(key)) is not type(expected):
            raise ValueError(f"key-possession binding mismatch: {key}")
    if binding["binding_id"] != _expected_binding_id(binding):
        raise ValueError("binding_id binding mismatch")

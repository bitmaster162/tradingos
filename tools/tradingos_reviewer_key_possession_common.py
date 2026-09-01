"""Shared deterministic helpers for TradingOS R84 reviewer key-possession assertion binding."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

BINDING_SCHEMA = "tradingos.reviewer_key_possession_binding.v1"
CHALLENGE_SCHEMA = "tradingos.reviewer_key_possession_challenge.v1"
EXTERNAL_ASSERTION_SCHEMA = "control_center.reviewer_key_possession_assertion.v1"
POLICY_ID = "TRADINGOS_REVIEWER_KEY_POSSESSION_ASSERTION_BINDING_POLICY_V1"
VERSION = "1.0.0"

_ID24_RE = re.compile(r"^[0-9a-f]{24}$")
_SHA64_RE = re.compile(r"^[0-9a-f]{64}$")

OUTPUT_PERMISSIONS = {
    "execution_authority": "NONE",
    "signals_allowed": False,
    "orders_allowed": False,
    "can_trade": False,
    "capital_permission": "DENY",
    "confers_authority": False,
}

POLICY_KEYS = {
    "schema_version", "policy_id", "mode", "input_evidence_set_schema",
    "external_assertion_schema", "require_full_r83_validation",
    "require_exact_attestation_binding", "require_expected_external_assertion_digest",
    "require_external_signature_verifier_assertion", "require_local_signature_math_false",
    "allowed_algorithms", "external_assertion_input_allowed", "network_access_in_core_allowed",
    "credential_access_in_core_allowed", "raw_signature_bytes_in_core_allowed",
    "raw_public_key_bytes_in_core_allowed", "reviewer_identity_inference_allowed",
    "distinct_reviewer_count_allowed", "same_key_same_human_inference_allowed",
    "different_keys_different_humans_inference_allowed", "physical_human_presence_inference_allowed",
    "assertion_freshness_inference_allowed", "consensus_inference_allowed",
    "approval_state_allowed", "recommendations_allowed", "policy_update_allowed",
    "live_decision_feedback_allowed", "live_decision_use_allowed", "model_selection_use_allowed",
    "persistence_in_core_allowed", "human_review_only", "shadow_only",
    "attestation_set_consumption_authority", "memory_write_authority", "output_permissions",
}


def stable_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("value is not canonically serializable") from exc


def stable_sha256(value: Any) -> str:
    return hashlib.sha256(stable_json_bytes(value)).hexdigest()


def validate_key_possession_policy(policy: Any) -> None:
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        raise ValueError("key-possession policy key set mismatch")
    if policy.get("schema_version") != 1 or policy.get("policy_id") != POLICY_ID:
        raise ValueError("unsupported key-possession policy")
    if policy.get("mode") != "OFFLINE_REVIEWER_KEY_POSSESSION_ASSERTION_BINDING_ONLY":
        raise ValueError("key-possession mode drift")
    if policy.get("input_evidence_set_schema") != "tradingos.attestation_evidence_set.v1":
        raise ValueError("input evidence-set schema drift")
    if policy.get("external_assertion_schema") != EXTERNAL_ASSERTION_SCHEMA:
        raise ValueError("external assertion schema drift")
    for field in (
        "require_full_r83_validation", "require_exact_attestation_binding",
        "require_expected_external_assertion_digest", "require_external_signature_verifier_assertion",
        "require_local_signature_math_false", "external_assertion_input_allowed",
        "human_review_only", "shadow_only",
    ):
        if policy.get(field) is not True:
            raise ValueError(f"required key-possession guard disabled: {field}")
    if policy.get("allowed_algorithms") != ["ED25519", "ES256"]:
        raise ValueError("key-possession algorithm allowlist drift")
    for field in (
        "network_access_in_core_allowed", "credential_access_in_core_allowed",
        "raw_signature_bytes_in_core_allowed", "raw_public_key_bytes_in_core_allowed",
        "reviewer_identity_inference_allowed", "distinct_reviewer_count_allowed",
        "same_key_same_human_inference_allowed", "different_keys_different_humans_inference_allowed",
        "physical_human_presence_inference_allowed", "assertion_freshness_inference_allowed",
        "consensus_inference_allowed", "approval_state_allowed", "recommendations_allowed",
        "policy_update_allowed", "live_decision_feedback_allowed", "live_decision_use_allowed",
        "model_selection_use_allowed", "persistence_in_core_allowed",
    ):
        if policy.get(field) is not False:
            raise ValueError(f"unsafe key-possession policy: {field}")
    if policy.get("attestation_set_consumption_authority") != "NONE":
        raise ValueError("attestation-set consumption authority must remain NONE")
    if policy.get("memory_write_authority") != "NONE":
        raise ValueError("memory write authority must remain NONE")
    if policy.get("output_permissions") != OUTPUT_PERMISSIONS:
        raise ValueError("unsafe key-possession output permissions")

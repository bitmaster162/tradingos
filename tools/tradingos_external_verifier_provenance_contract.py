"""TradingOS R85 deterministic external verifier provenance-binding contract."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from tools import tradingos_reviewer_key_possession_contract as r84
from tools.tradingos_reviewer_key_possession_common import stable_json_bytes, stable_sha256

BINDING_SCHEMA = "tradingos.external_verifier_provenance_binding.v1"
VERIFIER_REGISTRY_SCHEMA = "control_center.external_verifier_registry_snapshot.v1"
POLICY_ID = "TRADINGOS_EXTERNAL_VERIFIER_PROVENANCE_BINDING_POLICY_V1"
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
    "schema_version", "policy_id", "mode", "input_r84_binding_schema",
    "verifier_registry_schema", "require_full_r84_validation",
    "require_expected_verifier_registry_digest", "require_expected_authority_root_digest",
    "require_exact_registry_entry_match", "allowed_algorithms",
    "network_access_in_core_allowed", "credential_access_in_core_allowed",
    "raw_signature_bytes_in_core_allowed", "raw_public_key_bytes_in_core_allowed",
    "registry_write_allowed", "verifier_trust_inference_allowed",
    "authority_root_trust_inference_allowed", "registry_operator_identity_inference_allowed",
    "reviewer_identity_inference_allowed", "physical_human_presence_inference_allowed",
    "assertion_freshness_inference_allowed", "distinct_reviewer_count_allowed",
    "consensus_inference_allowed", "approval_state_allowed", "recommendations_allowed",
    "policy_update_allowed", "live_decision_feedback_allowed", "live_decision_use_allowed",
    "model_selection_use_allowed", "persistence_in_core_allowed", "human_review_only",
    "shadow_only", "attestation_set_consumption_authority", "memory_write_authority",
    "output_permissions",
}

REGISTRY_KEYS = {
    "schema", "registry_id", "authority_root_sha256", "entries", "registry_scope",
    "trust_root_verified", "confers_authority",
}
REGISTRY_ENTRY_KEYS = {"verifier_id", "verifier_key_id", "public_key_sha256", "algorithm"}

BINDING_KEYS = {
    "schema", "binding_id", "r84_binding_id", "r84_binding_sha256", "evidence_set_id",
    "attestation_id", "provenance_policy_sha256", "verifier_registry_sha256",
    "verifier_registry_digest_consumed", "registry_id", "authority_root_sha256",
    "authority_root_digest_consumed", "registry_entry_sha256", "verifier_id",
    "verifier_key_id", "public_key_sha256", "algorithm",
    "verifier_registry_entry_exact_match", "verifier_provenance_bound",
    "verifier_trust_root_verified", "registry_operator_identity_verified",
    "review_identity_verified", "physical_human_presence_proven",
    "assertion_freshness_verified", "distinct_reviewer_count_allowed",
    "consensus_inference_allowed", "approval_state_allowed", "shadow_only",
    "human_review_only", "attestation_set_consumption_authority", "memory_write_authority",
    "policy_update_allowed", "live_decision_feedback_allowed", "live_decision_use_allowed",
    "model_selection_use_allowed", "execution_authority", "can_trade", "capital_permission",
    "confers_authority",
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


def validate_provenance_policy(policy: Any) -> None:
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        raise ValueError("provenance policy key set mismatch")
    if policy.get("schema_version") != 1 or policy.get("policy_id") != POLICY_ID:
        raise ValueError("unsupported provenance policy")
    if policy.get("mode") != "OFFLINE_EXTERNAL_VERIFIER_PROVENANCE_BINDING_ONLY":
        raise ValueError("provenance mode drift")
    if policy.get("input_r84_binding_schema") != r84.BINDING_SCHEMA:
        raise ValueError("input R84 binding schema drift")
    if policy.get("verifier_registry_schema") != VERIFIER_REGISTRY_SCHEMA:
        raise ValueError("verifier registry schema drift")
    for field in (
        "require_full_r84_validation", "require_expected_verifier_registry_digest",
        "require_expected_authority_root_digest", "require_exact_registry_entry_match",
        "human_review_only", "shadow_only",
    ):
        if policy.get(field) is not True:
            raise ValueError(f"required provenance guard disabled: {field}")
    if policy.get("allowed_algorithms") != ["ED25519", "ES256"]:
        raise ValueError("provenance algorithm allowlist drift")
    for field in (
        "network_access_in_core_allowed", "credential_access_in_core_allowed",
        "raw_signature_bytes_in_core_allowed", "raw_public_key_bytes_in_core_allowed",
        "registry_write_allowed", "verifier_trust_inference_allowed",
        "authority_root_trust_inference_allowed", "registry_operator_identity_inference_allowed",
        "reviewer_identity_inference_allowed", "physical_human_presence_inference_allowed",
        "assertion_freshness_inference_allowed", "distinct_reviewer_count_allowed",
        "consensus_inference_allowed", "approval_state_allowed", "recommendations_allowed",
        "policy_update_allowed", "live_decision_feedback_allowed", "live_decision_use_allowed",
        "model_selection_use_allowed", "persistence_in_core_allowed",
    ):
        if policy.get(field) is not False:
            raise ValueError(f"unsafe provenance policy: {field}")
    if policy.get("attestation_set_consumption_authority") != "NONE":
        raise ValueError("attestation-set consumption authority must remain NONE")
    if policy.get("memory_write_authority") != "NONE":
        raise ValueError("memory write authority must remain NONE")
    if policy.get("output_permissions") != OUTPUT_PERMISSIONS:
        raise ValueError("unsafe provenance output permissions")


def _validate_registry_entry(entry: Any, policy: dict[str, Any]) -> dict[str, str]:
    if not isinstance(entry, dict) or set(entry) != REGISTRY_ENTRY_KEYS:
        raise ValueError("verifier registry entry key set mismatch")
    normalized = {
        "verifier_id": _token(entry.get("verifier_id"), "verifier_id"),
        "verifier_key_id": _token(entry.get("verifier_key_id"), "verifier_key_id"),
        "public_key_sha256": _sha(entry.get("public_key_sha256"), "public_key_sha256"),
        "algorithm": _token(entry.get("algorithm"), "algorithm"),
    }
    if normalized["algorithm"] not in policy["allowed_algorithms"]:
        raise ValueError("unsupported verifier registry algorithm")
    return normalized


def _validate_registry(
    registry: Any,
    *,
    expected_verifier_registry_sha256: str,
    expected_authority_root_sha256: str,
    r84_binding: dict[str, Any],
    provenance_policy: dict[str, Any],
) -> tuple[str, dict[str, str]]:
    if not isinstance(registry, dict) or set(registry) != REGISTRY_KEYS:
        raise ValueError("verifier registry key set mismatch")
    if registry.get("schema") != VERIFIER_REGISTRY_SCHEMA:
        raise ValueError("unsupported verifier registry schema")
    _token(registry.get("registry_id"), "registry_id")
    expected_root = _sha(expected_authority_root_sha256, "expected_authority_root_sha256")
    registry_root = _sha(registry.get("authority_root_sha256"), "authority_root_sha256")
    if registry_root != expected_root:
        raise ValueError("authority root digest mismatch")
    if registry.get("registry_scope") != "VERIFIER_METADATA_PROVENANCE_ONLY":
        raise ValueError("verifier registry scope invalid")
    if registry.get("trust_root_verified") is not False:
        raise ValueError("verifier registry trust-root overclaim")
    if registry.get("confers_authority") is not False:
        raise ValueError("verifier registry authority overclaim")

    expected_digest = _sha(expected_verifier_registry_sha256, "expected_verifier_registry_sha256")
    computed_digest = stable_sha256(registry)
    if computed_digest != expected_digest:
        raise ValueError("verifier registry digest mismatch")

    rows = registry.get("entries")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1024:
        raise ValueError("verifier registry entries invalid")
    normalized_rows = [_validate_registry_entry(row, provenance_policy) for row in rows]
    row_digests = [stable_sha256(row) for row in normalized_rows]
    if len(set(row_digests)) != len(row_digests):
        raise ValueError("duplicate verifier registry entry")

    target = {
        "verifier_id": r84_binding["verifier_id"],
        "verifier_key_id": r84_binding["verifier_key_id"],
        "public_key_sha256": r84_binding["public_key_sha256"],
        "algorithm": r84_binding["algorithm"],
    }
    matches = [row for row in normalized_rows if row == target]
    if len(matches) != 1:
        raise ValueError("R84 verifier registry entry must match exactly once")
    return computed_digest, matches[0]


def _validate_inputs(
    r84_binding: dict[str, Any],
    evidence_set: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    set_policy: dict[str, Any],
    attestation_id: str,
    external_assertion: dict[str, Any],
    verifier_registry_snapshot: dict[str, Any],
    *,
    expected_external_assertion_sha256: str,
    key_possession_policy: dict[str, Any],
    expected_verifier_registry_sha256: str,
    expected_authority_root_sha256: str,
    provenance_policy: dict[str, Any],
) -> tuple[str, dict[str, str]]:
    validate_provenance_policy(provenance_policy)
    r84.validate_reviewer_key_possession_binding(
        r84_binding,
        evidence_set,
        evidence_items,
        set_policy,
        attestation_id,
        external_assertion,
        expected_external_assertion_sha256=expected_external_assertion_sha256,
        key_possession_policy=key_possession_policy,
    )
    return _validate_registry(
        verifier_registry_snapshot,
        expected_verifier_registry_sha256=expected_verifier_registry_sha256,
        expected_authority_root_sha256=expected_authority_root_sha256,
        r84_binding=r84_binding,
        provenance_policy=provenance_policy,
    )


def _binding_payload(
    r84_binding: dict[str, Any],
    verifier_registry_snapshot: dict[str, Any],
    registry_digest: str,
    registry_entry: dict[str, str],
    provenance_policy: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": BINDING_SCHEMA,
        "r84_binding_id": r84_binding["binding_id"],
        "r84_binding_sha256": stable_sha256(r84_binding),
        "evidence_set_id": r84_binding["evidence_set_id"],
        "attestation_id": r84_binding["attestation_id"],
        "provenance_policy_sha256": stable_sha256(provenance_policy),
        "verifier_registry_sha256": registry_digest,
        "verifier_registry_digest_consumed": True,
        "registry_id": verifier_registry_snapshot["registry_id"],
        "authority_root_sha256": verifier_registry_snapshot["authority_root_sha256"],
        "authority_root_digest_consumed": True,
        "registry_entry_sha256": stable_sha256(registry_entry),
        "verifier_id": registry_entry["verifier_id"],
        "verifier_key_id": registry_entry["verifier_key_id"],
        "public_key_sha256": registry_entry["public_key_sha256"],
        "algorithm": registry_entry["algorithm"],
        "verifier_registry_entry_exact_match": True,
        "verifier_provenance_bound": True,
        "verifier_trust_root_verified": False,
        "registry_operator_identity_verified": False,
        "review_identity_verified": False,
        "physical_human_presence_proven": False,
        "assertion_freshness_verified": False,
        "distinct_reviewer_count_allowed": False,
        "consensus_inference_allowed": False,
        "approval_state_allowed": False,
        "shadow_only": True,
        "human_review_only": True,
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


def _expected_binding_id(binding: dict[str, Any]) -> str:
    payload = {key: binding[key] for key in BINDING_KEYS if key != "binding_id"}
    return hashlib.sha256(
        f"{BINDING_SCHEMA}:{VERSION}:".encode("utf-8") + stable_json_bytes(payload)
    ).hexdigest()[:24]


def build_external_verifier_provenance_binding(
    r84_binding: dict[str, Any],
    evidence_set: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    set_policy: dict[str, Any],
    attestation_id: str,
    external_assertion: dict[str, Any],
    verifier_registry_snapshot: dict[str, Any],
    *,
    expected_external_assertion_sha256: str,
    key_possession_policy: dict[str, Any],
    expected_verifier_registry_sha256: str,
    expected_authority_root_sha256: str,
    provenance_policy: dict[str, Any],
) -> dict[str, Any]:
    registry_digest, registry_entry = _validate_inputs(
        r84_binding,
        evidence_set,
        evidence_items,
        set_policy,
        attestation_id,
        external_assertion,
        verifier_registry_snapshot,
        expected_external_assertion_sha256=expected_external_assertion_sha256,
        key_possession_policy=key_possession_policy,
        expected_verifier_registry_sha256=expected_verifier_registry_sha256,
        expected_authority_root_sha256=expected_authority_root_sha256,
        provenance_policy=provenance_policy,
    )
    binding = _binding_payload(
        r84_binding, verifier_registry_snapshot, registry_digest, registry_entry, provenance_policy
    )
    binding["binding_id"] = _expected_binding_id(binding)
    validate_external_verifier_provenance_binding(
        binding,
        r84_binding,
        evidence_set,
        evidence_items,
        set_policy,
        attestation_id,
        external_assertion,
        verifier_registry_snapshot,
        expected_external_assertion_sha256=expected_external_assertion_sha256,
        key_possession_policy=key_possession_policy,
        expected_verifier_registry_sha256=expected_verifier_registry_sha256,
        expected_authority_root_sha256=expected_authority_root_sha256,
        provenance_policy=provenance_policy,
    )
    return binding


def validate_external_verifier_provenance_binding(
    binding: Any,
    r84_binding: dict[str, Any],
    evidence_set: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    set_policy: dict[str, Any],
    attestation_id: str,
    external_assertion: dict[str, Any],
    verifier_registry_snapshot: dict[str, Any],
    *,
    expected_external_assertion_sha256: str,
    key_possession_policy: dict[str, Any],
    expected_verifier_registry_sha256: str,
    expected_authority_root_sha256: str,
    provenance_policy: dict[str, Any],
) -> None:
    registry_digest, registry_entry = _validate_inputs(
        r84_binding,
        evidence_set,
        evidence_items,
        set_policy,
        attestation_id,
        external_assertion,
        verifier_registry_snapshot,
        expected_external_assertion_sha256=expected_external_assertion_sha256,
        key_possession_policy=key_possession_policy,
        expected_verifier_registry_sha256=expected_verifier_registry_sha256,
        expected_authority_root_sha256=expected_authority_root_sha256,
        provenance_policy=provenance_policy,
    )
    if not isinstance(binding, dict) or set(binding) != BINDING_KEYS:
        raise ValueError("provenance binding key set mismatch")
    if binding.get("schema") != BINDING_SCHEMA:
        raise ValueError("unsupported provenance binding schema")
    _id24(binding.get("binding_id"), "binding_id")
    expected = _binding_payload(
        r84_binding, verifier_registry_snapshot, registry_digest, registry_entry, provenance_policy
    )
    for key, value in expected.items():
        if binding.get(key) != value or type(binding.get(key)) is not type(value):
            raise ValueError(f"provenance binding mismatch: {key}")
    if binding["binding_id"] != _expected_binding_id(binding):
        raise ValueError("binding_id binding mismatch")

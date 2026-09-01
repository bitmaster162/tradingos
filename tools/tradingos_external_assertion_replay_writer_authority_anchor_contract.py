"""TradingOS R89 deterministic writer-authority-anchor binding contract."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from tools import tradingos_external_assertion_replay_writer_fencing_recovery_contract as r88
from tools.tradingos_reviewer_key_possession_common import stable_json_bytes, stable_sha256

BINDING_SCHEMA = "tradingos.external_assertion_replay_writer_authority_anchor_binding.v1"
AUTHORITY_ANCHOR_SCHEMA = "control_center.external_assertion_replay_writer_authority_anchor.v1"
POLICY_ID = "TRADINGOS_EXTERNAL_ASSERTION_REPLAY_WRITER_AUTHORITY_ANCHOR_POLICY_V1"
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
    "schema_version", "policy_id", "mode", "input_r88_binding_schema",
    "authority_anchor_schema", "require_full_r88_validation",
    "require_expected_authority_anchor_digest", "require_expected_authority_root_digest",
    "require_exact_r88_anchor_binding", "require_retained_reference",
    "network_access_in_core_allowed", "credential_access_in_core_allowed",
    "registry_write_allowed", "lease_registry_write_allowed", "receipt_index_write_allowed",
    "backend_write_allowed", "authority_root_trust_inference_allowed",
    "authority_anchor_operator_identity_inference_allowed", "durable_commit_inference_allowed",
    "durable_single_use_inference_allowed", "global_current_state_inference_allowed",
    "concurrent_writer_exclusion_inference_allowed", "freshness_inference_allowed",
    "liveness_inference_allowed", "verifier_trust_inference_allowed",
    "reviewer_identity_inference_allowed", "physical_human_presence_inference_allowed",
    "distinct_reviewer_count_allowed", "consensus_inference_allowed",
    "approval_state_allowed", "recommendations_allowed", "policy_update_allowed",
    "live_decision_feedback_allowed", "live_decision_use_allowed",
    "model_selection_use_allowed", "persistence_in_core_allowed",
    "human_review_only", "shadow_only", "attestation_set_consumption_authority",
    "memory_write_authority", "output_permissions",
}

ANCHOR_KEYS = {
    "schema", "anchor_scope", "r88_binding_id", "r88_binding_sha256",
    "recovery_verification_sha256", "writer_lease_sha256",
    "current_receipt_index_sha256", "receipt_candidate_sha256",
    "current_fencing_token", "authority_root_sha256", "retained_reference_required",
    "root_trust_verified", "anchor_operator_identity_verified",
    "live_writer_backend_proven", "durable_commit_proven",
    "global_current_state_verified", "concurrent_writer_exclusion_proven",
    "registry_write_performed", "lease_registry_write_performed",
    "receipt_index_write_performed", "backend_write_performed",
    "execution_authority", "can_execute", "apply_allowed", "confers_authority",
}

BINDING_KEYS = {
    "schema", "binding_id", "r88_binding_id", "r88_binding_sha256",
    "r87_binding_id", "r86_binding_id", "r85_binding_id", "r84_binding_id",
    "authority_anchor_policy_sha256", "authority_anchor_sha256",
    "authority_anchor_digest_consumed", "authority_root_sha256",
    "authority_root_digest_consumed", "anchor_scope",
    "recovery_verification_sha256", "writer_lease_sha256",
    "current_receipt_index_sha256", "receipt_candidate_sha256",
    "current_fencing_token", "retained_reference_required",
    "writer_authority_anchor_bound", "writer_authority_root_verified",
    "authority_anchor_operator_identity_verified", "live_writer_backend_proven",
    "durable_commit_proven", "durable_single_use_enforced",
    "global_current_state_verified", "concurrent_writer_exclusion_proven",
    "registry_write_performed", "lease_registry_write_performed",
    "receipt_index_write_performed", "backend_write_performed",
    "assertion_freshness_verified", "liveness_verified",
    "verifier_trust_root_verified", "review_identity_verified",
    "physical_human_presence_proven", "distinct_reviewer_count_allowed",
    "consensus_inference_allowed", "approval_state_allowed",
    "shadow_only", "human_review_only", "attestation_set_consumption_authority",
    "memory_write_authority", "policy_update_allowed",
    "live_decision_feedback_allowed", "live_decision_use_allowed",
    "model_selection_use_allowed", "execution_authority", "can_trade",
    "capital_permission", "confers_authority",
}


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA64_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be lowercase sha256")
    return value


def _id24(value: Any, field: str) -> str:
    if not isinstance(value, str) or _ID24_RE.fullmatch(value) is None:
        raise ValueError(f"{field} invalid")
    return value


def _counter(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value <= 2147483647:
        raise ValueError(f"{field} invalid")
    return value


def _validate_output_permissions(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != set(OUTPUT_PERMISSIONS):
        raise ValueError("unsafe writer-authority-anchor output permissions")
    for key, expected in OUTPUT_PERMISSIONS.items():
        if value.get(key) != expected or type(value.get(key)) is not type(expected):
            raise ValueError("unsafe writer-authority-anchor output permissions")


def validate_writer_authority_anchor_policy(policy: Any) -> None:
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        raise ValueError("writer-authority-anchor policy key set mismatch")
    if type(policy.get("schema_version")) is not int or policy.get("schema_version") != 1:
        raise ValueError("unsupported writer-authority-anchor policy")
    if policy.get("policy_id") != POLICY_ID:
        raise ValueError("unsupported writer-authority-anchor policy")
    if policy.get("mode") != "OFFLINE_EXTERNAL_ASSERTION_REPLAY_WRITER_AUTHORITY_ANCHOR_BINDING_ONLY":
        raise ValueError("writer-authority-anchor policy mode drift")
    if policy.get("input_r88_binding_schema") != r88.BINDING_SCHEMA:
        raise ValueError("input R88 binding schema drift")
    if policy.get("authority_anchor_schema") != AUTHORITY_ANCHOR_SCHEMA:
        raise ValueError("authority anchor schema drift")
    for field in (
        "require_full_r88_validation", "require_expected_authority_anchor_digest",
        "require_expected_authority_root_digest", "require_exact_r88_anchor_binding",
        "require_retained_reference", "human_review_only", "shadow_only",
    ):
        if policy.get(field) is not True:
            raise ValueError(f"required writer-authority-anchor guard disabled: {field}")
    for field in (
        "network_access_in_core_allowed", "credential_access_in_core_allowed",
        "registry_write_allowed", "lease_registry_write_allowed",
        "receipt_index_write_allowed", "backend_write_allowed",
        "authority_root_trust_inference_allowed",
        "authority_anchor_operator_identity_inference_allowed",
        "durable_commit_inference_allowed", "durable_single_use_inference_allowed",
        "global_current_state_inference_allowed",
        "concurrent_writer_exclusion_inference_allowed",
        "freshness_inference_allowed", "liveness_inference_allowed",
        "verifier_trust_inference_allowed", "reviewer_identity_inference_allowed",
        "physical_human_presence_inference_allowed", "distinct_reviewer_count_allowed",
        "consensus_inference_allowed", "approval_state_allowed", "recommendations_allowed",
        "policy_update_allowed", "live_decision_feedback_allowed",
        "live_decision_use_allowed", "model_selection_use_allowed",
        "persistence_in_core_allowed",
    ):
        if policy.get(field) is not False:
            raise ValueError(f"unsafe writer-authority-anchor policy: {field}")
    if policy.get("attestation_set_consumption_authority") != "NONE":
        raise ValueError("attestation-set consumption authority must remain NONE")
    if policy.get("memory_write_authority") != "NONE":
        raise ValueError("memory write authority must remain NONE")
    _validate_output_permissions(policy.get("output_permissions"))


def _validate_authority_anchor(
    authority_anchor: Any,
    *,
    expected_authority_anchor_sha256: str,
    expected_authority_root_sha256: str,
    r88_binding: dict[str, Any],
) -> str:
    if not isinstance(authority_anchor, dict) or set(authority_anchor) != ANCHOR_KEYS:
        raise ValueError("writer-authority anchor key set mismatch")
    if authority_anchor.get("schema") != AUTHORITY_ANCHOR_SCHEMA:
        raise ValueError("unsupported writer-authority anchor schema")
    expected_digest = _sha(expected_authority_anchor_sha256, "expected_authority_anchor_sha256")
    computed_digest = stable_sha256(authority_anchor)
    if computed_digest != expected_digest:
        raise ValueError("writer-authority anchor digest mismatch")
    if authority_anchor.get("anchor_scope") != "WRITER_LEASE_AND_RECEIPT_INDEX_ONLY":
        raise ValueError("writer-authority anchor scope invalid")

    exact = {
        "r88_binding_id": r88_binding["binding_id"],
        "r88_binding_sha256": stable_sha256(r88_binding),
        "recovery_verification_sha256": r88_binding["recovery_verification_sha256"],
        "writer_lease_sha256": r88_binding["writer_lease_sha256"],
        "current_receipt_index_sha256": r88_binding["current_receipt_index_sha256"],
        "receipt_candidate_sha256": r88_binding["receipt_candidate_sha256"],
        "current_fencing_token": r88_binding["current_fencing_token"],
    }
    for key, expected in exact.items():
        if authority_anchor.get(key) != expected or type(authority_anchor.get(key)) is not type(expected):
            raise ValueError(f"writer-authority anchor R88 mismatch: {key}")

    root = _sha(authority_anchor.get("authority_root_sha256"), "authority_root_sha256")
    if root != _sha(expected_authority_root_sha256, "expected_authority_root_sha256"):
        raise ValueError("writer-authority root digest mismatch")
    _counter(authority_anchor.get("current_fencing_token"), "current_fencing_token")
    if authority_anchor.get("retained_reference_required") is not True:
        raise ValueError("writer-authority retained-reference guard missing")
    for field in (
        "root_trust_verified", "anchor_operator_identity_verified", "live_writer_backend_proven",
        "durable_commit_proven", "global_current_state_verified",
        "concurrent_writer_exclusion_proven", "registry_write_performed",
        "lease_registry_write_performed", "receipt_index_write_performed",
        "backend_write_performed", "can_execute", "apply_allowed", "confers_authority",
    ):
        if authority_anchor.get(field) is not False:
            raise ValueError(f"writer-authority anchor overclaim: {field}")
    if authority_anchor.get("execution_authority") != "NONE":
        raise ValueError("writer-authority anchor execution authority overclaim")
    return computed_digest


def _validate_inputs(
    r88_binding: dict[str, Any], r87_binding: dict[str, Any], r86_binding: dict[str, Any],
    r85_binding: dict[str, Any], r84_binding: dict[str, Any], evidence_set: dict[str, Any],
    evidence_items: list[dict[str, Any]], set_policy: dict[str, Any], attestation_id: str,
    external_assertion: dict[str, Any], verifier_registry_snapshot: dict[str, Any],
    replay_registry_snapshot: dict[str, Any], atomic_verification: dict[str, Any],
    recovery_verification: dict[str, Any], authority_anchor: dict[str, Any], *,
    expected_external_assertion_sha256: str, key_possession_policy: dict[str, Any],
    expected_verifier_registry_sha256: str, expected_authority_root_sha256: str,
    provenance_policy: dict[str, Any], expected_replay_registry_sha256: str,
    replay_guard_policy: dict[str, Any], expected_atomic_verification_sha256: str,
    atomic_cas_policy: dict[str, Any], expected_recovery_verification_sha256: str,
    writer_fencing_recovery_policy: dict[str, Any], expected_authority_anchor_sha256: str,
    writer_authority_anchor_policy: dict[str, Any],
) -> str:
    validate_writer_authority_anchor_policy(writer_authority_anchor_policy)
    r88.validate_external_assertion_replay_writer_fencing_recovery_binding(
        r88_binding, r87_binding, r86_binding, r85_binding, r84_binding, evidence_set,
        evidence_items, set_policy, attestation_id, external_assertion,
        verifier_registry_snapshot, replay_registry_snapshot, atomic_verification,
        recovery_verification,
        expected_external_assertion_sha256=expected_external_assertion_sha256,
        key_possession_policy=key_possession_policy,
        expected_verifier_registry_sha256=expected_verifier_registry_sha256,
        expected_authority_root_sha256=expected_authority_root_sha256,
        provenance_policy=provenance_policy,
        expected_replay_registry_sha256=expected_replay_registry_sha256,
        replay_guard_policy=replay_guard_policy,
        expected_atomic_verification_sha256=expected_atomic_verification_sha256,
        atomic_cas_policy=atomic_cas_policy,
        expected_recovery_verification_sha256=expected_recovery_verification_sha256,
        writer_fencing_recovery_policy=writer_fencing_recovery_policy,
    )
    return _validate_authority_anchor(
        authority_anchor,
        expected_authority_anchor_sha256=expected_authority_anchor_sha256,
        expected_authority_root_sha256=expected_authority_root_sha256,
        r88_binding=r88_binding,
    )


def _binding_payload(
    r88_binding: dict[str, Any], r87_binding: dict[str, Any], r86_binding: dict[str, Any],
    r85_binding: dict[str, Any], r84_binding: dict[str, Any], authority_anchor: dict[str, Any],
    authority_anchor_sha256: str, writer_authority_anchor_policy: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": BINDING_SCHEMA,
        "r88_binding_id": r88_binding["binding_id"],
        "r88_binding_sha256": stable_sha256(r88_binding),
        "r87_binding_id": r87_binding["binding_id"],
        "r86_binding_id": r86_binding["binding_id"],
        "r85_binding_id": r85_binding["binding_id"],
        "r84_binding_id": r84_binding["binding_id"],
        "authority_anchor_policy_sha256": stable_sha256(writer_authority_anchor_policy),
        "authority_anchor_sha256": authority_anchor_sha256,
        "authority_anchor_digest_consumed": True,
        "authority_root_sha256": authority_anchor["authority_root_sha256"],
        "authority_root_digest_consumed": True,
        "anchor_scope": "WRITER_LEASE_AND_RECEIPT_INDEX_ONLY",
        "recovery_verification_sha256": r88_binding["recovery_verification_sha256"],
        "writer_lease_sha256": r88_binding["writer_lease_sha256"],
        "current_receipt_index_sha256": r88_binding["current_receipt_index_sha256"],
        "receipt_candidate_sha256": r88_binding["receipt_candidate_sha256"],
        "current_fencing_token": r88_binding["current_fencing_token"],
        "retained_reference_required": True,
        "writer_authority_anchor_bound": True,
        "writer_authority_root_verified": False,
        "authority_anchor_operator_identity_verified": False,
        "live_writer_backend_proven": False,
        "durable_commit_proven": False,
        "durable_single_use_enforced": False,
        "global_current_state_verified": False,
        "concurrent_writer_exclusion_proven": False,
        "registry_write_performed": False,
        "lease_registry_write_performed": False,
        "receipt_index_write_performed": False,
        "backend_write_performed": False,
        "assertion_freshness_verified": False,
        "liveness_verified": False,
        "verifier_trust_root_verified": False,
        "review_identity_verified": False,
        "physical_human_presence_proven": False,
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


def build_external_assertion_replay_writer_authority_anchor_binding(
    r88_binding: dict[str, Any], r87_binding: dict[str, Any], r86_binding: dict[str, Any],
    r85_binding: dict[str, Any], r84_binding: dict[str, Any], evidence_set: dict[str, Any],
    evidence_items: list[dict[str, Any]], set_policy: dict[str, Any], attestation_id: str,
    external_assertion: dict[str, Any], verifier_registry_snapshot: dict[str, Any],
    replay_registry_snapshot: dict[str, Any], atomic_verification: dict[str, Any],
    recovery_verification: dict[str, Any], authority_anchor: dict[str, Any], *,
    expected_external_assertion_sha256: str, key_possession_policy: dict[str, Any],
    expected_verifier_registry_sha256: str, expected_authority_root_sha256: str,
    provenance_policy: dict[str, Any], expected_replay_registry_sha256: str,
    replay_guard_policy: dict[str, Any], expected_atomic_verification_sha256: str,
    atomic_cas_policy: dict[str, Any], expected_recovery_verification_sha256: str,
    writer_fencing_recovery_policy: dict[str, Any], expected_authority_anchor_sha256: str,
    writer_authority_anchor_policy: dict[str, Any],
) -> dict[str, Any]:
    anchor_sha = _validate_inputs(
        r88_binding, r87_binding, r86_binding, r85_binding, r84_binding, evidence_set,
        evidence_items, set_policy, attestation_id, external_assertion,
        verifier_registry_snapshot, replay_registry_snapshot, atomic_verification,
        recovery_verification, authority_anchor,
        expected_external_assertion_sha256=expected_external_assertion_sha256,
        key_possession_policy=key_possession_policy,
        expected_verifier_registry_sha256=expected_verifier_registry_sha256,
        expected_authority_root_sha256=expected_authority_root_sha256,
        provenance_policy=provenance_policy,
        expected_replay_registry_sha256=expected_replay_registry_sha256,
        replay_guard_policy=replay_guard_policy,
        expected_atomic_verification_sha256=expected_atomic_verification_sha256,
        atomic_cas_policy=atomic_cas_policy,
        expected_recovery_verification_sha256=expected_recovery_verification_sha256,
        writer_fencing_recovery_policy=writer_fencing_recovery_policy,
        expected_authority_anchor_sha256=expected_authority_anchor_sha256,
        writer_authority_anchor_policy=writer_authority_anchor_policy,
    )
    binding = _binding_payload(
        r88_binding, r87_binding, r86_binding, r85_binding, r84_binding,
        authority_anchor, anchor_sha, writer_authority_anchor_policy
    )
    binding["binding_id"] = _expected_binding_id(binding)
    validate_external_assertion_replay_writer_authority_anchor_binding(
        binding, r88_binding, r87_binding, r86_binding, r85_binding, r84_binding,
        evidence_set, evidence_items, set_policy, attestation_id, external_assertion,
        verifier_registry_snapshot, replay_registry_snapshot, atomic_verification,
        recovery_verification, authority_anchor,
        expected_external_assertion_sha256=expected_external_assertion_sha256,
        key_possession_policy=key_possession_policy,
        expected_verifier_registry_sha256=expected_verifier_registry_sha256,
        expected_authority_root_sha256=expected_authority_root_sha256,
        provenance_policy=provenance_policy,
        expected_replay_registry_sha256=expected_replay_registry_sha256,
        replay_guard_policy=replay_guard_policy,
        expected_atomic_verification_sha256=expected_atomic_verification_sha256,
        atomic_cas_policy=atomic_cas_policy,
        expected_recovery_verification_sha256=expected_recovery_verification_sha256,
        writer_fencing_recovery_policy=writer_fencing_recovery_policy,
        expected_authority_anchor_sha256=expected_authority_anchor_sha256,
        writer_authority_anchor_policy=writer_authority_anchor_policy,
    )
    return binding


def validate_external_assertion_replay_writer_authority_anchor_binding(
    binding: Any, r88_binding: dict[str, Any], r87_binding: dict[str, Any],
    r86_binding: dict[str, Any], r85_binding: dict[str, Any], r84_binding: dict[str, Any],
    evidence_set: dict[str, Any], evidence_items: list[dict[str, Any]], set_policy: dict[str, Any],
    attestation_id: str, external_assertion: dict[str, Any],
    verifier_registry_snapshot: dict[str, Any], replay_registry_snapshot: dict[str, Any],
    atomic_verification: dict[str, Any], recovery_verification: dict[str, Any],
    authority_anchor: dict[str, Any], *, expected_external_assertion_sha256: str,
    key_possession_policy: dict[str, Any], expected_verifier_registry_sha256: str,
    expected_authority_root_sha256: str, provenance_policy: dict[str, Any],
    expected_replay_registry_sha256: str, replay_guard_policy: dict[str, Any],
    expected_atomic_verification_sha256: str, atomic_cas_policy: dict[str, Any],
    expected_recovery_verification_sha256: str,
    writer_fencing_recovery_policy: dict[str, Any], expected_authority_anchor_sha256: str,
    writer_authority_anchor_policy: dict[str, Any],
) -> None:
    anchor_sha = _validate_inputs(
        r88_binding, r87_binding, r86_binding, r85_binding, r84_binding, evidence_set,
        evidence_items, set_policy, attestation_id, external_assertion,
        verifier_registry_snapshot, replay_registry_snapshot, atomic_verification,
        recovery_verification, authority_anchor,
        expected_external_assertion_sha256=expected_external_assertion_sha256,
        key_possession_policy=key_possession_policy,
        expected_verifier_registry_sha256=expected_verifier_registry_sha256,
        expected_authority_root_sha256=expected_authority_root_sha256,
        provenance_policy=provenance_policy,
        expected_replay_registry_sha256=expected_replay_registry_sha256,
        replay_guard_policy=replay_guard_policy,
        expected_atomic_verification_sha256=expected_atomic_verification_sha256,
        atomic_cas_policy=atomic_cas_policy,
        expected_recovery_verification_sha256=expected_recovery_verification_sha256,
        writer_fencing_recovery_policy=writer_fencing_recovery_policy,
        expected_authority_anchor_sha256=expected_authority_anchor_sha256,
        writer_authority_anchor_policy=writer_authority_anchor_policy,
    )
    if not isinstance(binding, dict) or set(binding) != BINDING_KEYS:
        raise ValueError("writer-authority-anchor binding key set mismatch")
    if binding.get("schema") != BINDING_SCHEMA:
        raise ValueError("unsupported writer-authority-anchor binding schema")
    _id24(binding.get("binding_id"), "binding_id")
    expected = _binding_payload(
        r88_binding, r87_binding, r86_binding, r85_binding, r84_binding,
        authority_anchor, anchor_sha, writer_authority_anchor_policy
    )
    for key, value in expected.items():
        if binding.get(key) != value or type(binding.get(key)) is not type(value):
            raise ValueError(f"writer-authority-anchor binding mismatch: {key}")
    if binding["binding_id"] != _expected_binding_id(binding):
        raise ValueError("binding_id binding mismatch")

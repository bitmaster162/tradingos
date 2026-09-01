"TradingOS R88 deterministic writer-fencing/crash-recovery evidence-binding contract."
from __future__ import annotations

import hashlib
import re
from typing import Any

from tools import tradingos_external_assertion_replay_atomic_cas_contract as r87
from tools.tradingos_reviewer_key_possession_common import stable_json_bytes, stable_sha256

BINDING_SCHEMA = "tradingos.external_assertion_replay_writer_fencing_recovery_binding.v1"
RECOVERY_VERIFICATION_SCHEMA = "control_center.external_assertion_replay_writer_fencing_recovery_verification.v1"
POLICY_ID = "TRADINGOS_EXTERNAL_ASSERTION_REPLAY_WRITER_FENCING_RECOVERY_POLICY_V1"
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
    "schema_version", "policy_id", "mode", "input_r87_binding_schema",
    "recovery_verification_schema", "require_full_r87_validation",
    "require_expected_recovery_verification_digest", "require_exact_r87_recovery_binding",
    "require_monotonic_fencing_model", "require_readback_receipt_dedup_protocol",
    "require_blind_retry_false", "require_split_brain_same_token_rejected",
    "network_access_in_core_allowed", "credential_access_in_core_allowed",
    "registry_write_allowed", "lease_registry_write_allowed", "receipt_index_write_allowed",
    "backend_write_allowed", "durable_commit_inference_allowed",
    "durable_single_use_inference_allowed", "global_current_state_inference_allowed",
    "concurrent_writer_exclusion_inference_allowed", "freshness_inference_allowed",
    "liveness_inference_allowed", "verifier_trust_inference_allowed",
    "reviewer_identity_inference_allowed", "physical_human_presence_inference_allowed",
    "distinct_reviewer_count_allowed", "consensus_inference_allowed",
    "approval_state_allowed", "recommendations_allowed", "policy_update_allowed",
    "live_decision_feedback_allowed", "live_decision_use_allowed",
    "model_selection_use_allowed", "persistence_in_core_allowed", "human_review_only",
    "shadow_only", "attestation_set_consumption_authority", "memory_write_authority",
    "output_permissions",
}

RECOVERY_KEYS = {
    "schema", "recovery_scope", "r87_binding_id", "r87_binding_sha256",
    "atomic_verification_sha256", "replay_registry_sha256",
    "next_registry_candidate_sha256", "cas_generation_from", "cas_generation_to",
    "writer_lease_sha256", "receipt_candidate_sha256", "current_receipt_index_sha256",
    "attempt_fencing_token", "current_fencing_token", "fencing_model",
    "crash_recovery_protocol", "blind_retry_allowed", "split_brain_same_token_rejected",
    "stale_writer_fenced", "crash_point", "recovery_status", "recovery_action",
    "live_writer_backend_proven", "commit_performed", "registry_write_performed",
    "lease_registry_write_performed", "receipt_index_write_performed",
    "backend_write_performed", "durable_commit_proven", "global_current_state_verified",
    "concurrent_writer_exclusion_proven", "execution_authority", "can_execute",
    "apply_allowed", "confers_authority",
}

BINDING_KEYS = {
    "schema", "binding_id", "r87_binding_id", "r87_binding_sha256",
    "r86_binding_id", "r85_binding_id", "r84_binding_id",
    "recovery_policy_sha256", "recovery_verification_sha256",
    "recovery_verification_digest_consumed", "atomic_verification_sha256",
    "replay_registry_sha256", "next_registry_candidate_sha256",
    "cas_generation_from", "cas_generation_to", "writer_lease_sha256",
    "receipt_candidate_sha256", "current_receipt_index_sha256",
    "attempt_fencing_token", "current_fencing_token", "stale_writer_fenced",
    "crash_point", "recovery_status", "recovery_action", "fencing_model",
    "crash_recovery_protocol", "blind_retry_allowed", "split_brain_same_token_rejected",
    "writer_fencing_recovery_evidence_bound", "lease_digest_bound",
    "fencing_protocol_bound", "crash_recovery_protocol_bound",
    "live_writer_backend_proven", "durable_commit_proven",
    "durable_single_use_enforced", "global_current_state_verified",
    "concurrent_writer_exclusion_proven", "registry_write_performed",
    "lease_registry_write_performed", "receipt_index_write_performed",
    "backend_write_performed", "assertion_freshness_verified", "liveness_verified",
    "verifier_trust_root_verified", "review_identity_verified",
    "physical_human_presence_proven", "distinct_reviewer_count_allowed",
    "consensus_inference_allowed", "approval_state_allowed",
    "shadow_only", "human_review_only", "attestation_set_consumption_authority",
    "memory_write_authority", "policy_update_allowed",
    "live_decision_feedback_allowed", "live_decision_use_allowed",
    "model_selection_use_allowed", "execution_authority", "can_trade",
    "capital_permission", "confers_authority",
}

RECOVERY_RULES = {
    "STALE_WRITER_FENCED_REACQUIRE_REQUIRED": ("REACQUIRE_LEASE", True),
    "NO_WRITE_OBSERVED_RETRY_REQUIRES_FRESH_CAS": ("RETRY_WITH_FRESH_CAS", False),
    "WRITE_OBSERVED_RECEIPT_ABSENT_HOLD": ("HOLD", False),
    "RECEIPT_INDEXED_DEDUP_NO_RETRY": ("DEDUP_NO_RETRY", False),
}

ALLOWED_CRASH_POINTS = {
    "BEFORE_CAS",
    "AFTER_CAS_BEFORE_COMMIT",
    "AFTER_COMMIT_BEFORE_RECEIPT_INDEX",
    "AFTER_RECEIPT_INDEX",
    "UNKNOWN",
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


def _counter(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value <= 2147483647:
        raise ValueError(f"{field} invalid")
    return value


def _validate_output_permissions(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != set(OUTPUT_PERMISSIONS):
        raise ValueError("unsafe recovery output permissions")
    for key, expected in OUTPUT_PERMISSIONS.items():
        if value.get(key) != expected or type(value.get(key)) is not type(expected):
            raise ValueError("unsafe recovery output permissions")


def validate_writer_fencing_recovery_policy(policy: Any) -> None:
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        raise ValueError("writer-fencing recovery policy key set mismatch")
    if type(policy.get("schema_version")) is not int or policy.get("schema_version") != 1:
        raise ValueError("unsupported writer-fencing recovery policy")
    if policy.get("policy_id") != POLICY_ID:
        raise ValueError("unsupported writer-fencing recovery policy")
    if policy.get("mode") != "OFFLINE_EXTERNAL_ASSERTION_REPLAY_WRITER_FENCING_RECOVERY_BINDING_ONLY":
        raise ValueError("writer-fencing recovery policy mode drift")
    if policy.get("input_r87_binding_schema") != r87.BINDING_SCHEMA:
        raise ValueError("input R87 binding schema drift")
    if policy.get("recovery_verification_schema") != RECOVERY_VERIFICATION_SCHEMA:
        raise ValueError("recovery verification schema drift")
    for field in (
        "require_full_r87_validation", "require_expected_recovery_verification_digest",
        "require_exact_r87_recovery_binding", "require_monotonic_fencing_model",
        "require_readback_receipt_dedup_protocol", "require_blind_retry_false",
        "require_split_brain_same_token_rejected", "human_review_only", "shadow_only",
    ):
        if policy.get(field) is not True:
            raise ValueError(f"required writer-fencing recovery guard disabled: {field}")
    for field in (
        "network_access_in_core_allowed", "credential_access_in_core_allowed",
        "registry_write_allowed", "lease_registry_write_allowed", "receipt_index_write_allowed",
        "backend_write_allowed", "durable_commit_inference_allowed",
        "durable_single_use_inference_allowed", "global_current_state_inference_allowed",
        "concurrent_writer_exclusion_inference_allowed", "freshness_inference_allowed",
        "liveness_inference_allowed", "verifier_trust_inference_allowed",
        "reviewer_identity_inference_allowed", "physical_human_presence_inference_allowed",
        "distinct_reviewer_count_allowed", "consensus_inference_allowed",
        "approval_state_allowed", "recommendations_allowed", "policy_update_allowed",
        "live_decision_feedback_allowed", "live_decision_use_allowed",
        "model_selection_use_allowed", "persistence_in_core_allowed",
    ):
        if policy.get(field) is not False:
            raise ValueError(f"unsafe writer-fencing recovery policy: {field}")
    if policy.get("attestation_set_consumption_authority") != "NONE":
        raise ValueError("attestation-set consumption authority must remain NONE")
    if policy.get("memory_write_authority") != "NONE":
        raise ValueError("memory write authority must remain NONE")
    _validate_output_permissions(policy.get("output_permissions"))


def _validate_recovery_verification(
    recovery_verification: Any,
    *,
    expected_recovery_verification_sha256: str,
    r87_binding: dict[str, Any],
) -> str:
    if not isinstance(recovery_verification, dict) or set(recovery_verification) != RECOVERY_KEYS:
        raise ValueError("writer-fencing recovery verification key set mismatch")
    if recovery_verification.get("schema") != RECOVERY_VERIFICATION_SCHEMA:
        raise ValueError("unsupported writer-fencing recovery verification schema")
    expected_digest = _sha(
        expected_recovery_verification_sha256, "expected_recovery_verification_sha256"
    )
    computed_digest = stable_sha256(recovery_verification)
    if computed_digest != expected_digest:
        raise ValueError("writer-fencing recovery verification digest mismatch")
    if recovery_verification.get("recovery_scope") != "EXTERNAL_ASSERTION_REPLAY_REGISTRY_WRITER_ONLY":
        raise ValueError("writer-fencing recovery verification scope invalid")

    exact = {
        "r87_binding_id": r87_binding["binding_id"],
        "r87_binding_sha256": stable_sha256(r87_binding),
        "atomic_verification_sha256": r87_binding["atomic_verification_sha256"],
        "replay_registry_sha256": r87_binding["replay_registry_sha256"],
        "next_registry_candidate_sha256": r87_binding["next_registry_candidate_sha256"],
        "cas_generation_from": r87_binding["cas_generation_from"],
        "cas_generation_to": r87_binding["cas_generation_to"],
    }
    for key, expected in exact.items():
        if recovery_verification.get(key) != expected or type(recovery_verification.get(key)) is not type(expected):
            raise ValueError(f"writer-fencing recovery R87 mismatch: {key}")

    _sha(recovery_verification.get("writer_lease_sha256"), "writer_lease_sha256")
    _sha(recovery_verification.get("receipt_candidate_sha256"), "receipt_candidate_sha256")
    _sha(recovery_verification.get("current_receipt_index_sha256"), "current_receipt_index_sha256")

    attempt = _counter(recovery_verification.get("attempt_fencing_token"), "attempt_fencing_token")
    current = _counter(recovery_verification.get("current_fencing_token"), "current_fencing_token")
    if attempt > current:
        raise ValueError("attempt fencing token cannot exceed current token")

    if recovery_verification.get("fencing_model") != "MONOTONIC_FENCING_TOKEN_PLUS_LEASE_DIGEST":
        raise ValueError("writer-fencing model invalid")
    if recovery_verification.get("crash_recovery_protocol") != "READBACK_PLUS_RECEIPT_INDEX_DEDUP":
        raise ValueError("crash-recovery protocol invalid")
    if recovery_verification.get("blind_retry_allowed") is not False:
        raise ValueError("blind retry forbidden")
    if recovery_verification.get("split_brain_same_token_rejected") is not True:
        raise ValueError("split-brain same-token guard missing")

    crash_point = recovery_verification.get("crash_point")
    if crash_point not in ALLOWED_CRASH_POINTS:
        raise ValueError("crash point invalid")

    status = recovery_verification.get("recovery_status")
    action = recovery_verification.get("recovery_action")
    rule = RECOVERY_RULES.get(status)
    if rule is None or action != rule[0]:
        raise ValueError("recovery status/action invalid")
    stale = recovery_verification.get("stale_writer_fenced")
    if type(stale) is not bool or stale is not rule[1]:
        raise ValueError("stale writer recovery status mismatch")
    if stale:
        if not attempt < current:
            raise ValueError("stale writer fencing token relation invalid")
    elif attempt != current:
        raise ValueError("non-stale writer fencing token relation invalid")

    for field in (
        "live_writer_backend_proven", "commit_performed", "registry_write_performed",
        "lease_registry_write_performed", "receipt_index_write_performed",
        "backend_write_performed", "durable_commit_proven",
        "global_current_state_verified", "concurrent_writer_exclusion_proven",
        "can_execute", "apply_allowed", "confers_authority",
    ):
        if recovery_verification.get(field) is not False:
            raise ValueError(f"writer-fencing recovery overclaim: {field}")
    if recovery_verification.get("execution_authority") != "NONE":
        raise ValueError("writer-fencing recovery execution authority overclaim")
    return computed_digest


def _validate_inputs(
    r87_binding: dict[str, Any],
    r86_binding: dict[str, Any],
    r85_binding: dict[str, Any],
    r84_binding: dict[str, Any],
    evidence_set: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    set_policy: dict[str, Any],
    attestation_id: str,
    external_assertion: dict[str, Any],
    verifier_registry_snapshot: dict[str, Any],
    replay_registry_snapshot: dict[str, Any],
    atomic_verification: dict[str, Any],
    recovery_verification: dict[str, Any],
    *,
    expected_external_assertion_sha256: str,
    key_possession_policy: dict[str, Any],
    expected_verifier_registry_sha256: str,
    expected_authority_root_sha256: str,
    provenance_policy: dict[str, Any],
    expected_replay_registry_sha256: str,
    replay_guard_policy: dict[str, Any],
    expected_atomic_verification_sha256: str,
    atomic_cas_policy: dict[str, Any],
    expected_recovery_verification_sha256: str,
    writer_fencing_recovery_policy: dict[str, Any],
) -> str:
    validate_writer_fencing_recovery_policy(writer_fencing_recovery_policy)
    r87.validate_external_assertion_replay_atomic_cas_binding(
        r87_binding,
        r86_binding, r85_binding, r84_binding, evidence_set, evidence_items, set_policy,
        attestation_id, external_assertion, verifier_registry_snapshot, replay_registry_snapshot,
        atomic_verification,
        expected_external_assertion_sha256=expected_external_assertion_sha256,
        key_possession_policy=key_possession_policy,
        expected_verifier_registry_sha256=expected_verifier_registry_sha256,
        expected_authority_root_sha256=expected_authority_root_sha256,
        provenance_policy=provenance_policy,
        expected_replay_registry_sha256=expected_replay_registry_sha256,
        replay_guard_policy=replay_guard_policy,
        expected_atomic_verification_sha256=expected_atomic_verification_sha256,
        atomic_cas_policy=atomic_cas_policy,
    )
    return _validate_recovery_verification(
        recovery_verification,
        expected_recovery_verification_sha256=expected_recovery_verification_sha256,
        r87_binding=r87_binding,
    )


def _binding_payload(
    r87_binding: dict[str, Any],
    r86_binding: dict[str, Any],
    r85_binding: dict[str, Any],
    r84_binding: dict[str, Any],
    recovery_verification: dict[str, Any],
    recovery_verification_sha256: str,
    writer_fencing_recovery_policy: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": BINDING_SCHEMA,
        "r87_binding_id": r87_binding["binding_id"],
        "r87_binding_sha256": stable_sha256(r87_binding),
        "r86_binding_id": r86_binding["binding_id"],
        "r85_binding_id": r85_binding["binding_id"],
        "r84_binding_id": r84_binding["binding_id"],
        "recovery_policy_sha256": stable_sha256(writer_fencing_recovery_policy),
        "recovery_verification_sha256": recovery_verification_sha256,
        "recovery_verification_digest_consumed": True,
        "atomic_verification_sha256": r87_binding["atomic_verification_sha256"],
        "replay_registry_sha256": r87_binding["replay_registry_sha256"],
        "next_registry_candidate_sha256": r87_binding["next_registry_candidate_sha256"],
        "cas_generation_from": r87_binding["cas_generation_from"],
        "cas_generation_to": r87_binding["cas_generation_to"],
        "writer_lease_sha256": recovery_verification["writer_lease_sha256"],
        "receipt_candidate_sha256": recovery_verification["receipt_candidate_sha256"],
        "current_receipt_index_sha256": recovery_verification["current_receipt_index_sha256"],
        "attempt_fencing_token": recovery_verification["attempt_fencing_token"],
        "current_fencing_token": recovery_verification["current_fencing_token"],
        "stale_writer_fenced": recovery_verification["stale_writer_fenced"],
        "crash_point": recovery_verification["crash_point"],
        "recovery_status": recovery_verification["recovery_status"],
        "recovery_action": recovery_verification["recovery_action"],
        "fencing_model": "MONOTONIC_FENCING_TOKEN_PLUS_LEASE_DIGEST",
        "crash_recovery_protocol": "READBACK_PLUS_RECEIPT_INDEX_DEDUP",
        "blind_retry_allowed": False,
        "split_brain_same_token_rejected": True,
        "writer_fencing_recovery_evidence_bound": True,
        "lease_digest_bound": True,
        "fencing_protocol_bound": True,
        "crash_recovery_protocol_bound": True,
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


def build_external_assertion_replay_writer_fencing_recovery_binding(
    r87_binding: dict[str, Any],
    r86_binding: dict[str, Any],
    r85_binding: dict[str, Any],
    r84_binding: dict[str, Any],
    evidence_set: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    set_policy: dict[str, Any],
    attestation_id: str,
    external_assertion: dict[str, Any],
    verifier_registry_snapshot: dict[str, Any],
    replay_registry_snapshot: dict[str, Any],
    atomic_verification: dict[str, Any],
    recovery_verification: dict[str, Any],
    *,
    expected_external_assertion_sha256: str,
    key_possession_policy: dict[str, Any],
    expected_verifier_registry_sha256: str,
    expected_authority_root_sha256: str,
    provenance_policy: dict[str, Any],
    expected_replay_registry_sha256: str,
    replay_guard_policy: dict[str, Any],
    expected_atomic_verification_sha256: str,
    atomic_cas_policy: dict[str, Any],
    expected_recovery_verification_sha256: str,
    writer_fencing_recovery_policy: dict[str, Any],
) -> dict[str, Any]:
    recovery_sha = _validate_inputs(
        r87_binding, r86_binding, r85_binding, r84_binding, evidence_set, evidence_items,
        set_policy, attestation_id, external_assertion, verifier_registry_snapshot,
        replay_registry_snapshot, atomic_verification, recovery_verification,
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
    binding = _binding_payload(
        r87_binding, r86_binding, r85_binding, r84_binding, recovery_verification,
        recovery_sha, writer_fencing_recovery_policy
    )
    binding["binding_id"] = _expected_binding_id(binding)
    validate_external_assertion_replay_writer_fencing_recovery_binding(
        binding,
        r87_binding, r86_binding, r85_binding, r84_binding, evidence_set, evidence_items,
        set_policy, attestation_id, external_assertion, verifier_registry_snapshot,
        replay_registry_snapshot, atomic_verification, recovery_verification,
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
    return binding


def validate_external_assertion_replay_writer_fencing_recovery_binding(
    binding: Any,
    r87_binding: dict[str, Any],
    r86_binding: dict[str, Any],
    r85_binding: dict[str, Any],
    r84_binding: dict[str, Any],
    evidence_set: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    set_policy: dict[str, Any],
    attestation_id: str,
    external_assertion: dict[str, Any],
    verifier_registry_snapshot: dict[str, Any],
    replay_registry_snapshot: dict[str, Any],
    atomic_verification: dict[str, Any],
    recovery_verification: dict[str, Any],
    *,
    expected_external_assertion_sha256: str,
    key_possession_policy: dict[str, Any],
    expected_verifier_registry_sha256: str,
    expected_authority_root_sha256: str,
    provenance_policy: dict[str, Any],
    expected_replay_registry_sha256: str,
    replay_guard_policy: dict[str, Any],
    expected_atomic_verification_sha256: str,
    atomic_cas_policy: dict[str, Any],
    expected_recovery_verification_sha256: str,
    writer_fencing_recovery_policy: dict[str, Any],
) -> None:
    recovery_sha = _validate_inputs(
        r87_binding, r86_binding, r85_binding, r84_binding, evidence_set, evidence_items,
        set_policy, attestation_id, external_assertion, verifier_registry_snapshot,
        replay_registry_snapshot, atomic_verification, recovery_verification,
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
    if not isinstance(binding, dict) or set(binding) != BINDING_KEYS:
        raise ValueError("writer-fencing recovery binding key set mismatch")
    if binding.get("schema") != BINDING_SCHEMA:
        raise ValueError("unsupported writer-fencing recovery binding schema")
    _id24(binding.get("binding_id"), "binding_id")
    expected = _binding_payload(
        r87_binding, r86_binding, r85_binding, r84_binding, recovery_verification,
        recovery_sha, writer_fencing_recovery_policy
    )
    for key, value in expected.items():
        if binding.get(key) != value or type(binding.get(key)) is not type(value):
            raise ValueError(f"writer-fencing recovery binding mismatch: {key}")
    if binding["binding_id"] != _expected_binding_id(binding):
        raise ValueError("binding_id binding mismatch")

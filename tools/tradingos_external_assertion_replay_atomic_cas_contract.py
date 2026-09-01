"""TradingOS R87 deterministic external-assertion replay atomic-CAS binding contract."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from tools import tradingos_external_assertion_replay_guard_contract as r86
from tools.tradingos_reviewer_key_possession_common import stable_json_bytes, stable_sha256

BINDING_SCHEMA = "tradingos.external_assertion_replay_atomic_cas_binding.v1"
ATOMIC_VERIFICATION_SCHEMA = "control_center.external_assertion_replay_atomic_cas_verification.v1"
POLICY_ID = "TRADINGOS_EXTERNAL_ASSERTION_REPLAY_ATOMIC_CAS_POLICY_V1"
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
    "schema_version", "policy_id", "mode", "input_r86_binding_schema",
    "atomic_verification_schema", "require_full_r86_validation",
    "require_expected_atomic_verification_digest", "require_exact_r86_receipt_binding",
    "require_compare_and_swap_precondition", "require_exact_generation_transition",
    "network_access_in_core_allowed", "credential_access_in_core_allowed",
    "registry_write_allowed", "durable_commit_inference_allowed",
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

ATOMIC_KEYS = {
    "schema", "atomic_scope", "r86_binding_id", "r86_binding_sha256",
    "replay_registry_sha256", "next_registry_candidate_sha256",
    "external_assertion_sha256", "challenge_sha256",
    "cas_generation_from", "cas_generation_to", "toctou_guard_model",
    "atomicity_status", "single_use_status", "commit_performed",
    "registry_write_performed", "durable_commit_proven",
    "global_current_state_verified", "concurrent_writer_exclusion_proven",
    "execution_authority", "can_execute", "apply_allowed", "confers_authority",
}

BINDING_KEYS = {
    "schema", "binding_id", "r86_binding_id", "r86_binding_sha256",
    "r85_binding_id", "r84_binding_id", "atomic_cas_policy_sha256",
    "atomic_verification_sha256", "atomic_verification_digest_consumed",
    "replay_registry_sha256", "next_registry_candidate_sha256",
    "external_assertion_sha256", "challenge_sha256",
    "cas_generation_from", "cas_generation_to", "cas_precondition_bound",
    "atomic_transition_candidate_verified", "durable_commit_proven",
    "durable_single_use_enforced", "global_current_state_verified",
    "concurrent_writer_exclusion_proven", "registry_write_performed",
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


def _generation(value: Any, field: str, *, allow_max: bool = False) -> int:
    maximum = 2147483647 if allow_max else 2147483646
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{field} invalid")
    return value


def _validate_output_permissions(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != set(OUTPUT_PERMISSIONS):
        raise ValueError("unsafe atomic-CAS output permissions")
    for key, expected in OUTPUT_PERMISSIONS.items():
        if value.get(key) != expected or type(value.get(key)) is not type(expected):
            raise ValueError("unsafe atomic-CAS output permissions")


def validate_atomic_cas_policy(policy: Any) -> None:
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        raise ValueError("atomic-CAS policy key set mismatch")
    if type(policy.get("schema_version")) is not int or policy.get("schema_version") != 1:
        raise ValueError("unsupported atomic-CAS policy")
    if policy.get("policy_id") != POLICY_ID:
        raise ValueError("unsupported atomic-CAS policy")
    if policy.get("mode") != "OFFLINE_EXTERNAL_ASSERTION_REPLAY_ATOMIC_CAS_BINDING_ONLY":
        raise ValueError("atomic-CAS policy mode drift")
    if policy.get("input_r86_binding_schema") != r86.BINDING_SCHEMA:
        raise ValueError("input R86 binding schema drift")
    if policy.get("atomic_verification_schema") != ATOMIC_VERIFICATION_SCHEMA:
        raise ValueError("atomic verification schema drift")
    for field in (
        "require_full_r86_validation", "require_expected_atomic_verification_digest",
        "require_exact_r86_receipt_binding", "require_compare_and_swap_precondition",
        "require_exact_generation_transition", "human_review_only", "shadow_only",
    ):
        if policy.get(field) is not True:
            raise ValueError(f"required atomic-CAS guard disabled: {field}")
    for field in (
        "network_access_in_core_allowed", "credential_access_in_core_allowed",
        "registry_write_allowed", "durable_commit_inference_allowed",
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
            raise ValueError(f"unsafe atomic-CAS policy: {field}")
    if policy.get("attestation_set_consumption_authority") != "NONE":
        raise ValueError("attestation-set consumption authority must remain NONE")
    if policy.get("memory_write_authority") != "NONE":
        raise ValueError("memory write authority must remain NONE")
    _validate_output_permissions(policy.get("output_permissions"))


def _validate_atomic_verification(
    atomic_verification: Any,
    *,
    expected_atomic_verification_sha256: str,
    r86_binding: dict[str, Any],
) -> str:
    if not isinstance(atomic_verification, dict) or set(atomic_verification) != ATOMIC_KEYS:
        raise ValueError("atomic verification key set mismatch")
    if atomic_verification.get("schema") != ATOMIC_VERIFICATION_SCHEMA:
        raise ValueError("unsupported atomic verification schema")
    expected_digest = _sha(
        expected_atomic_verification_sha256, "expected_atomic_verification_sha256"
    )
    computed_digest = stable_sha256(atomic_verification)
    if computed_digest != expected_digest:
        raise ValueError("atomic verification digest mismatch")
    if atomic_verification.get("atomic_scope") != "EXTERNAL_ASSERTION_REPLAY_REGISTRY_ONLY":
        raise ValueError("atomic verification scope invalid")

    exact = {
        "r86_binding_id": r86_binding["binding_id"],
        "r86_binding_sha256": stable_sha256(r86_binding),
        "replay_registry_sha256": r86_binding["replay_registry_sha256"],
        "next_registry_candidate_sha256": r86_binding["next_registry_candidate_sha256"],
        "external_assertion_sha256": r86_binding["external_assertion_sha256"],
        "challenge_sha256": r86_binding["challenge_sha256"],
        "cas_generation_from": r86_binding["prior_generation"],
        "cas_generation_to": r86_binding["next_generation"],
    }
    for key, expected in exact.items():
        if atomic_verification.get(key) != expected or type(atomic_verification.get(key)) is not type(expected):
            raise ValueError(f"atomic verification R86 mismatch: {key}")

    generation_from = _generation(
        atomic_verification.get("cas_generation_from"), "cas_generation_from"
    )
    generation_to = _generation(
        atomic_verification.get("cas_generation_to"), "cas_generation_to", allow_max=True
    )
    if generation_to != generation_from + 1:
        raise ValueError("atomic verification generation transition invalid")
    if atomic_verification.get("toctou_guard_model") != "COMPARE_AND_SWAP_PRECONDITION":
        raise ValueError("atomic verification CAS guard missing")
    if atomic_verification.get("atomicity_status") != "PROTOCOL_VERIFIED_NO_DURABLE_COMMIT":
        raise ValueError("atomic verification status invalid")
    if atomic_verification.get("single_use_status") != "CANDIDATE_ONLY_NOT_DURABLY_ENFORCED":
        raise ValueError("atomic verification single-use status invalid")

    false_fields = (
        "commit_performed", "registry_write_performed", "durable_commit_proven",
        "global_current_state_verified", "concurrent_writer_exclusion_proven",
        "can_execute", "apply_allowed", "confers_authority",
    )
    for field in false_fields:
        if atomic_verification.get(field) is not False:
            raise ValueError(f"atomic verification overclaim: {field}")
    if atomic_verification.get("execution_authority") != "NONE":
        raise ValueError("atomic verification execution authority overclaim")
    return computed_digest


def _validate_inputs(
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
) -> str:
    validate_atomic_cas_policy(atomic_cas_policy)
    r86.validate_external_assertion_replay_guard_binding(
        r86_binding,
        r85_binding,
        r84_binding,
        evidence_set,
        evidence_items,
        set_policy,
        attestation_id,
        external_assertion,
        verifier_registry_snapshot,
        replay_registry_snapshot,
        expected_external_assertion_sha256=expected_external_assertion_sha256,
        key_possession_policy=key_possession_policy,
        expected_verifier_registry_sha256=expected_verifier_registry_sha256,
        expected_authority_root_sha256=expected_authority_root_sha256,
        provenance_policy=provenance_policy,
        expected_replay_registry_sha256=expected_replay_registry_sha256,
        replay_guard_policy=replay_guard_policy,
    )
    return _validate_atomic_verification(
        atomic_verification,
        expected_atomic_verification_sha256=expected_atomic_verification_sha256,
        r86_binding=r86_binding,
    )


def _binding_payload(
    r86_binding: dict[str, Any],
    r85_binding: dict[str, Any],
    r84_binding: dict[str, Any],
    atomic_verification_sha256: str,
    atomic_cas_policy: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": BINDING_SCHEMA,
        "r86_binding_id": r86_binding["binding_id"],
        "r86_binding_sha256": stable_sha256(r86_binding),
        "r85_binding_id": r85_binding["binding_id"],
        "r84_binding_id": r84_binding["binding_id"],
        "atomic_cas_policy_sha256": stable_sha256(atomic_cas_policy),
        "atomic_verification_sha256": atomic_verification_sha256,
        "atomic_verification_digest_consumed": True,
        "replay_registry_sha256": r86_binding["replay_registry_sha256"],
        "next_registry_candidate_sha256": r86_binding["next_registry_candidate_sha256"],
        "external_assertion_sha256": r86_binding["external_assertion_sha256"],
        "challenge_sha256": r86_binding["challenge_sha256"],
        "cas_generation_from": r86_binding["prior_generation"],
        "cas_generation_to": r86_binding["next_generation"],
        "cas_precondition_bound": True,
        "atomic_transition_candidate_verified": True,
        "durable_commit_proven": False,
        "durable_single_use_enforced": False,
        "global_current_state_verified": False,
        "concurrent_writer_exclusion_proven": False,
        "registry_write_performed": False,
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


def build_external_assertion_replay_atomic_cas_binding(
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
) -> dict[str, Any]:
    atomic_sha = _validate_inputs(
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
    binding = _binding_payload(
        r86_binding, r85_binding, r84_binding, atomic_sha, atomic_cas_policy
    )
    binding["binding_id"] = _expected_binding_id(binding)
    validate_external_assertion_replay_atomic_cas_binding(
        binding,
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
    return binding


def validate_external_assertion_replay_atomic_cas_binding(
    binding: Any,
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
) -> None:
    atomic_sha = _validate_inputs(
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
    if not isinstance(binding, dict) or set(binding) != BINDING_KEYS:
        raise ValueError("atomic-CAS binding key set mismatch")
    if binding.get("schema") != BINDING_SCHEMA:
        raise ValueError("unsupported atomic-CAS binding schema")
    _id24(binding.get("binding_id"), "binding_id")
    expected = _binding_payload(
        r86_binding, r85_binding, r84_binding, atomic_sha, atomic_cas_policy
    )
    for key, value in expected.items():
        if binding.get(key) != value or type(binding.get(key)) is not type(value):
            raise ValueError(f"atomic-CAS binding mismatch: {key}")
    if binding["binding_id"] != _expected_binding_id(binding):
        raise ValueError("binding_id binding mismatch")

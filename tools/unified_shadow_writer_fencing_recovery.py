#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj

R7_CLOSURE_SCHEMA = "bitevo.shadow_human_gate_consume_closure.v1"
R8_RECOVERY_SCHEMA = "control_center.shadow_human_gate_crash_recovery_verification.v1"
R8_CLOSURE_SCHEMA = "bitevo.shadow_writer_fencing_recovery_closure.v1"

_R7_EFFECTS = {
    "human_gate_write": False,
    "credential_registry_write": False,
    "nonce_registry_write": False,
    "current_truth_apply": False,
    "registry_write": False,
    "ledger_write": False,
    "return_index_write": False,
    "runtime_activation": False,
    "executor_dispatch": False,
    "signal": False,
    "order": False,
    "capital_effect": False,
}

_CONTROL_R8_EFFECTS = {
    "human_gate_write": False,
    "credential_registry_write": False,
    "nonce_registry_write": False,
    "lease_registry_write": False,
    "commit_receipt_registry_write": False,
    "backend_write": False,
    "current_truth_apply": False,
    "decision_ledger_write": False,
    "command_queue_write": False,
    "runtime_activation": False,
    "executor_dispatch": False,
    "signal": False,
    "order": False,
    "capital_effect": False,
}

_R8_EFFECTS = {
    "human_gate_write": False,
    "credential_registry_write": False,
    "nonce_registry_write": False,
    "lease_registry_write": False,
    "commit_receipt_registry_write": False,
    "backend_write": False,
    "current_truth_apply": False,
    "registry_write": False,
    "ledger_write": False,
    "return_index_write": False,
    "runtime_activation": False,
    "executor_dispatch": False,
    "signal": False,
    "order": False,
    "capital_effect": False,
}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShadowIntegrationError(f"writer_recovery_{field}_required")
    return value.strip()


def _sha(value: Any, field: str) -> str:
    text = _text(value, field).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ShadowIntegrationError(f"writer_recovery_{field}_must_be_sha256")
    return text


def _iso(value: Any, field: str) -> str:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise ShadowIntegrationError(f"writer_recovery_{field}_must_be_iso8601") from exc
    if parsed.tzinfo is None:
        raise ShadowIntegrationError(f"writer_recovery_{field}_timezone_required")
    return text


def _verify_hash(record: Mapping[str, Any], field: str, code: str) -> str:
    if not isinstance(record, Mapping):
        raise ShadowIntegrationError(code)
    supplied = _sha(record.get(field), field)
    expected = sha256_obj({k: v for k, v in record.items() if k != field})
    if supplied != expected:
        raise ShadowIntegrationError(code)
    return supplied


def _verify_safety(record: Mapping[str, Any], field: str) -> None:
    safety = record.get("safety") if isinstance(record, Mapping) else None
    if not isinstance(safety, Mapping):
        raise ShadowIntegrationError(f"writer_recovery_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"writer_recovery_unsafe_{field}:{key}")


def _verify_false_map(value: Any, expected: Mapping[str, bool], code: str) -> None:
    if not isinstance(value, Mapping) or set(value) != set(expected):
        raise ShadowIntegrationError(code)
    if any(value.get(key) is not False for key in expected):
        raise ShadowIntegrationError(code)


def _verify_r7_closure(closure: Mapping[str, Any], expected_sha256: str) -> str:
    if not isinstance(closure, Mapping) or closure.get("schema") != R7_CLOSURE_SCHEMA:
        raise ShadowIntegrationError("writer_recovery_wrong_r7_closure_schema")
    digest = _verify_hash(closure, "human_gate_consume_closure_sha256", "writer_recovery_r7_closure_hash_mismatch")
    if digest != _sha(expected_sha256, "expected_r7_closure_sha256"):
        raise ShadowIntegrationError("writer_recovery_r7_closure_external_digest_mismatch")
    _verify_safety(closure, "r7_closure")
    _verify_false_map(closure.get("effects"), _R7_EFFECTS, "writer_recovery_r7_effect_boundary_breached")
    if closure.get("status") != "HUMAN_GATE_CONSUME_BOUND_SHADOW_ONLY":
        raise ShadowIntegrationError("writer_recovery_r7_status_invalid")
    if closure.get("decision") != "HOLD" or closure.get("action") != "WAIT":
        raise ShadowIntegrationError("writer_recovery_r7_gate_widening_forbidden")
    if closure.get("toctou_guard_model") != "COMPARE_AND_SWAP_PRECONDITION":
        raise ShadowIntegrationError("writer_recovery_r7_cas_guard_missing")
    if closure.get("durable_commit_proven") is not False or closure.get("human_gate_write_performed") is not False:
        raise ShadowIntegrationError("writer_recovery_r7_durability_or_write_overclaim")
    if closure.get("current_truth_promotion_allowed") is not False or closure.get("apply_allowed") is not False:
        raise ShadowIntegrationError("writer_recovery_r7_truth_or_apply_breached")
    if closure.get("semantic_acceptance") != "NOT_PERFORMED":
        raise ShadowIntegrationError("writer_recovery_r7_acceptance_breached")
    if closure.get("execution_authority") != "NONE" or closure.get("can_execute") is not False:
        raise ShadowIntegrationError("writer_recovery_r7_authority_breached")
    return digest


def _verify_recovery(receipt: Mapping[str, Any], expected_sha256: str) -> str:
    if not isinstance(receipt, Mapping) or receipt.get("schema") != R8_RECOVERY_SCHEMA:
        raise ShadowIntegrationError("writer_recovery_wrong_recovery_schema")
    digest = _verify_hash(receipt, "recovery_verification_sha256", "writer_recovery_recovery_hash_mismatch")
    if digest != _sha(expected_sha256, "expected_recovery_sha256"):
        raise ShadowIntegrationError("writer_recovery_recovery_external_digest_mismatch")
    _verify_safety(receipt, "recovery")
    _verify_false_map(receipt.get("effects"), _CONTROL_R8_EFFECTS, "writer_recovery_control_effect_boundary_breached")
    if receipt.get("protocol_status") != "FENCING_AND_CRASH_RECOVERY_VERIFIED_SHADOW_ONLY":
        raise ShadowIntegrationError("writer_recovery_protocol_status_invalid")
    if receipt.get("fencing_model") != "MONOTONIC_FENCING_TOKEN_PLUS_LEASE_DIGEST":
        raise ShadowIntegrationError("writer_recovery_fencing_model_invalid")
    if receipt.get("crash_recovery_protocol") != "READBACK_PLUS_RECEIPT_INDEX_DEDUP":
        raise ShadowIntegrationError("writer_recovery_crash_protocol_invalid")
    if receipt.get("blind_retry_allowed") is not False:
        raise ShadowIntegrationError("writer_recovery_blind_retry_forbidden")
    if receipt.get("split_brain_same_token_rejected") is not True:
        raise ShadowIntegrationError("writer_recovery_split_brain_guard_missing")
    if receipt.get("live_writer_backend_proven") is not False or receipt.get("durable_commit_proven") is not False:
        raise ShadowIntegrationError("writer_recovery_live_or_durable_overclaim")
    if receipt.get("human_gate_write_performed") is not False or receipt.get("current_truth_promotion_allowed") is not False:
        raise ShadowIntegrationError("writer_recovery_write_or_truth_breached")
    if receipt.get("apply_allowed") is not False or receipt.get("execution_authority") != "NONE" or receipt.get("can_execute") is not False:
        raise ShadowIntegrationError("writer_recovery_apply_or_authority_breached")
    if receipt.get("recovery_status") not in {
        "STALE_WRITER_FENCED_REACQUIRE_REQUIRED",
        "NO_WRITE_OBSERVED_RETRY_REQUIRES_FRESH_CAS",
        "WRITE_OBSERVED_RECEIPT_ABSENT_HOLD",
        "RECEIPT_INDEXED_DEDUP_NO_RETRY",
    }:
        raise ShadowIntegrationError("writer_recovery_status_invalid")
    if receipt.get("retry_allowed") is True and receipt.get("recovery_status") != "NO_WRITE_OBSERVED_RETRY_REQUIRES_FRESH_CAS":
        raise ShadowIntegrationError("writer_recovery_retry_scope_invalid")
    return digest


def build_writer_fencing_recovery_closure(
    human_gate_consume_closure: Mapping[str, Any],
    crash_recovery_verification: Mapping[str, Any],
    *,
    expected_human_gate_consume_closure_sha256: str,
    expected_crash_recovery_verification_sha256: str,
    generated_at: str,
) -> dict[str, Any]:
    r7_sha = _verify_r7_closure(human_gate_consume_closure, expected_human_gate_consume_closure_sha256)
    recovery_sha = _verify_recovery(crash_recovery_verification, expected_crash_recovery_verification_sha256)
    if crash_recovery_verification.get("case_id") != human_gate_consume_closure.get("case_id"):
        raise ShadowIntegrationError("writer_recovery_case_mismatch")
    if crash_recovery_verification.get("case_sha256") != human_gate_consume_closure.get("case_sha256"):
        raise ShadowIntegrationError("writer_recovery_case_sha_mismatch")
    if crash_recovery_verification.get("challenge_id") != human_gate_consume_closure.get("challenge_id"):
        raise ShadowIntegrationError("writer_recovery_challenge_mismatch")
    if crash_recovery_verification.get("atomic_consume_verification_sha256") != human_gate_consume_closure.get("atomic_consume_verification_sha256"):
        raise ShadowIntegrationError("writer_recovery_atomic_binding_mismatch")
    if crash_recovery_verification.get("approval_verification_sha256") != human_gate_consume_closure.get("asymmetric_approval_verification_sha256"):
        raise ShadowIntegrationError("writer_recovery_approval_binding_mismatch")
    generated = _iso(generated_at, "generated_at")
    body = {
        "schema": R8_CLOSURE_SCHEMA,
        "case_id": human_gate_consume_closure["case_id"],
        "case_sha256": human_gate_consume_closure["case_sha256"],
        "challenge_id": human_gate_consume_closure["challenge_id"],
        "human_gate_consume_closure_sha256": r7_sha,
        "recovery_verification_sha256": recovery_sha,
        "writer_lease_sha256": crash_recovery_verification["current_writer_lease_sha256"],
        "receipt_candidate_sha256": crash_recovery_verification["receipt_candidate_sha256"],
        "current_receipt_index_sha256": crash_recovery_verification["current_receipt_index_sha256"],
        "attempt_fencing_token": crash_recovery_verification["attempt_fencing_token"],
        "current_fencing_token": crash_recovery_verification["current_fencing_token"],
        "stale_writer_fenced": crash_recovery_verification["stale_writer_fenced"],
        "crash_point": crash_recovery_verification["crash_point"],
        "recovery_status": crash_recovery_verification["recovery_status"],
        "recovery_action": crash_recovery_verification["recovery_action"],
        "fencing_model": "MONOTONIC_FENCING_TOKEN_PLUS_LEASE_DIGEST",
        "crash_recovery_protocol": "READBACK_PLUS_RECEIPT_INDEX_DEDUP",
        "status": "WRITER_FENCING_RECOVERY_BOUND_SHADOW_ONLY",
        "live_writer_backend_proven": False,
        "durable_commit_proven": False,
        "human_gate_write_performed": False,
        "current_truth_promotion_allowed": False,
        "semantic_acceptance": "NOT_PERFORMED",
        "apply_allowed": False,
        "execution_authority": "NONE",
        "can_execute": False,
        "decision": "HOLD",
        "action": "WAIT",
        "effects": dict(_R8_EFFECTS),
        "safety": dict(SHADOW_SAFETY),
        "generated_at": generated,
    }
    body["writer_fencing_recovery_closure_sha256"] = sha256_obj(body)
    return body

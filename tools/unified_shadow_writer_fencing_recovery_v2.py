#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any, Mapping

R8_CLOSURE_SCHEMA = "bitevo.shadow_writer_fencing_recovery_closure.v1"
RECOVERY_V2_SCHEMA = "control_center.shadow_human_gate_crash_recovery_verification.v2"
AUTHORITY_ANCHOR_SCHEMA = "control_center.shadow_human_gate_writer_authority_anchor.v1"
R8_1_CLOSURE_SCHEMA = "bitevo.shadow_writer_fencing_recovery_closure.v2"

SHADOW_SAFETY = {
    "mode": "SHADOW",
    "execution_authority": "NONE",
    "can_trade": False,
    "capital_permission": "DENY",
    "orders_allowed": False,
    "signals_allowed": False,
}

CONTROL_R8_EFFECTS = {
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

R8_EFFECTS = {
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

ALLOWED_RECOVERY = {
    "STALE_WRITER_FENCED_REACQUIRE_REQUIRED",
    "NO_WRITE_OBSERVED_RETRY_REQUIRES_FRESH_CAS",
    "WRITE_OBSERVED_RECEIPT_ABSENT_HOLD",
    "RECEIPT_INDEXED_DEDUP_NO_RETRY",
}


class WriterRecoveryV2Error(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_obj(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WriterRecoveryV2Error(f"{field}_required")
    return value.strip()


def _sha(value: Any, field: str) -> str:
    text = _text(value, field).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise WriterRecoveryV2Error(f"{field}_must_be_sha256")
    return text


def _iso(value: Any, field: str) -> str:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise WriterRecoveryV2Error(f"{field}_must_be_iso8601") from exc
    if parsed.tzinfo is None:
        raise WriterRecoveryV2Error(f"{field}_timezone_required")
    return text


def _verify_hash(record: Mapping[str, Any], field: str, code: str) -> str:
    if not isinstance(record, Mapping):
        raise WriterRecoveryV2Error(code)
    supplied = _sha(record.get(field), field)
    expected = sha256_obj({k: v for k, v in record.items() if k != field})
    if supplied != expected:
        raise WriterRecoveryV2Error(code)
    return supplied


def _verify_safety(record: Mapping[str, Any], field: str) -> None:
    safety = record.get("safety") if isinstance(record, Mapping) else None
    if not isinstance(safety, Mapping):
        raise WriterRecoveryV2Error(f"{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise WriterRecoveryV2Error(f"unsafe_{field}:{key}")


def _verify_effects(record: Mapping[str, Any], expected: Mapping[str, bool], field: str) -> None:
    effects = record.get("effects") if isinstance(record, Mapping) else None
    if not isinstance(effects, Mapping) or set(effects) != set(expected):
        raise WriterRecoveryV2Error(f"{field}_effect_keys_mismatch")
    if any(effects.get(key) is not False for key in expected):
        raise WriterRecoveryV2Error(f"{field}_effect_boundary_breached")


def _verify_r8_closure(closure: Mapping[str, Any], expected_sha256: str) -> str:
    if not isinstance(closure, Mapping) or closure.get("schema") != R8_CLOSURE_SCHEMA:
        raise WriterRecoveryV2Error("r8_closure_schema_mismatch")
    digest = _verify_hash(
        closure, "writer_fencing_recovery_closure_sha256", "r8_closure_hash_mismatch"
    )
    if digest != _sha(expected_sha256, "expected_r8_closure_sha256"):
        raise WriterRecoveryV2Error("r8_closure_external_digest_mismatch")
    _verify_safety(closure, "r8_closure")
    _verify_effects(closure, R8_EFFECTS, "r8_closure")
    if closure.get("status") != "WRITER_FENCING_RECOVERY_BOUND_SHADOW_ONLY":
        raise WriterRecoveryV2Error("r8_closure_status_invalid")
    if closure.get("decision") != "HOLD" or closure.get("action") != "WAIT":
        raise WriterRecoveryV2Error("r8_closure_gate_widening_forbidden")
    if closure.get("live_writer_backend_proven") is not False or closure.get("durable_commit_proven") is not False:
        raise WriterRecoveryV2Error("r8_closure_durability_overclaim")
    if closure.get("human_gate_write_performed") is not False or closure.get("current_truth_promotion_allowed") is not False:
        raise WriterRecoveryV2Error("r8_closure_write_or_truth_breached")
    if closure.get("semantic_acceptance") != "NOT_PERFORMED" or closure.get("apply_allowed") is not False:
        raise WriterRecoveryV2Error("r8_closure_acceptance_or_apply_breached")
    if closure.get("execution_authority") != "NONE" or closure.get("can_execute") is not False:
        raise WriterRecoveryV2Error("r8_closure_authority_breached")
    return digest


def _verify_recovery_v2(receipt: Mapping[str, Any], expected_sha256: str) -> str:
    if not isinstance(receipt, Mapping) or receipt.get("schema") != RECOVERY_V2_SCHEMA:
        raise WriterRecoveryV2Error("recovery_v2_schema_mismatch")
    digest = _verify_hash(
        receipt, "recovery_verification_sha256", "recovery_v2_hash_mismatch"
    )
    if digest != _sha(expected_sha256, "expected_recovery_v2_sha256"):
        raise WriterRecoveryV2Error("recovery_v2_external_digest_mismatch")
    _verify_safety(receipt, "recovery_v2")
    _verify_effects(receipt, CONTROL_R8_EFFECTS, "recovery_v2")
    if receipt.get("protocol_status") != "FENCING_AND_CRASH_RECOVERY_HARDENED_SHADOW_ONLY":
        raise WriterRecoveryV2Error("recovery_v2_status_invalid")
    if receipt.get("paired_receipt_identity_verified") is not True:
        raise WriterRecoveryV2Error("recovery_v2_paired_identity_missing")
    if receipt.get("authority_root_anchor_consumed") is not True:
        raise WriterRecoveryV2Error("recovery_v2_authority_anchor_missing")
    if receipt.get("cross_plane_anchor_scope") != "CONTROL_CENTER_WRITER_LEASE_RECEIPT_INDEX":
        raise WriterRecoveryV2Error("recovery_v2_anchor_scope_invalid")
    if receipt.get("recovery_status") not in ALLOWED_RECOVERY:
        raise WriterRecoveryV2Error("recovery_v2_outcome_invalid")
    if receipt.get("live_writer_backend_proven") is not False or receipt.get("durable_commit_proven") is not False:
        raise WriterRecoveryV2Error("recovery_v2_durability_overclaim")
    if receipt.get("human_gate_write_performed") is not False or receipt.get("current_truth_promotion_allowed") is not False:
        raise WriterRecoveryV2Error("recovery_v2_write_or_truth_breached")
    if receipt.get("apply_allowed") is not False or receipt.get("execution_authority") != "NONE" or receipt.get("can_execute") is not False:
        raise WriterRecoveryV2Error("recovery_v2_apply_or_authority_breached")
    return digest


def _verify_anchor(
    anchor: Mapping[str, Any],
    *,
    expected_anchor_sha256: str,
    expected_authority_root_sha256: str,
) -> str:
    if not isinstance(anchor, Mapping) or anchor.get("schema") != AUTHORITY_ANCHOR_SCHEMA:
        raise WriterRecoveryV2Error("authority_anchor_schema_mismatch")
    digest = _verify_hash(anchor, "authority_anchor_sha256", "authority_anchor_hash_mismatch")
    if digest != _sha(expected_anchor_sha256, "expected_authority_anchor_sha256"):
        raise WriterRecoveryV2Error("authority_anchor_external_digest_mismatch")
    if anchor.get("authority_root_sha256") != _sha(
        expected_authority_root_sha256, "expected_authority_root_sha256"
    ):
        raise WriterRecoveryV2Error("authority_anchor_root_mismatch")
    _verify_safety(anchor, "authority_anchor")
    _verify_effects(anchor, CONTROL_R8_EFFECTS, "authority_anchor")
    if anchor.get("anchor_scope") != "WRITER_LEASE_AND_RECEIPT_INDEX_ONLY":
        raise WriterRecoveryV2Error("authority_anchor_scope_invalid")
    if anchor.get("retained_reference_required") is not True:
        raise WriterRecoveryV2Error("authority_anchor_retention_guard_missing")
    if anchor.get("current_truth_promotion_allowed") is not False or anchor.get("apply_allowed") is not False:
        raise WriterRecoveryV2Error("authority_anchor_truth_or_apply_breached")
    if anchor.get("execution_authority") != "NONE":
        raise WriterRecoveryV2Error("authority_anchor_authority_breached")
    return digest


def build_writer_fencing_recovery_closure_v2(
    r8_closure_v1: Mapping[str, Any],
    recovery_verification_v2: Mapping[str, Any],
    authority_anchor: Mapping[str, Any],
    *,
    expected_r8_closure_sha256: str,
    expected_recovery_verification_v2_sha256: str,
    expected_authority_anchor_sha256: str,
    expected_authority_root_sha256: str,
    generated_at: str,
) -> dict[str, Any]:
    r8_sha = _verify_r8_closure(r8_closure_v1, expected_r8_closure_sha256)
    recovery_sha = _verify_recovery_v2(
        recovery_verification_v2, expected_recovery_verification_v2_sha256
    )
    anchor_sha = _verify_anchor(
        authority_anchor,
        expected_anchor_sha256=expected_authority_anchor_sha256,
        expected_authority_root_sha256=expected_authority_root_sha256,
    )
    if recovery_verification_v2.get("authority_anchor_sha256") != anchor_sha:
        raise WriterRecoveryV2Error("r8_1_recovery_anchor_binding_mismatch")
    if recovery_verification_v2.get("authority_root_sha256") != authority_anchor.get("authority_root_sha256"):
        raise WriterRecoveryV2Error("r8_1_recovery_root_binding_mismatch")
    if authority_anchor.get("writer_lease_sha256") != recovery_verification_v2.get("current_writer_lease_sha256"):
        raise WriterRecoveryV2Error("r8_1_anchor_writer_lease_mismatch")
    if authority_anchor.get("legacy_receipt_index_sha256") != recovery_verification_v2.get("legacy_current_receipt_index_sha256"):
        raise WriterRecoveryV2Error("r8_1_anchor_legacy_index_mismatch")
    if authority_anchor.get("paired_receipt_index_sha256") != recovery_verification_v2.get("paired_receipt_index_sha256"):
        raise WriterRecoveryV2Error("r8_1_anchor_paired_index_mismatch")

    for field in ("case_id", "case_sha256", "challenge_id"):
        if recovery_verification_v2.get(field) != r8_closure_v1.get(field):
            raise WriterRecoveryV2Error(f"r8_1_cross_plane_{field}_mismatch")
    if recovery_verification_v2.get("current_writer_lease_sha256") != r8_closure_v1.get("writer_lease_sha256"):
        raise WriterRecoveryV2Error("r8_1_cross_plane_writer_lease_mismatch")
    if recovery_verification_v2.get("legacy_current_receipt_index_sha256") != r8_closure_v1.get("current_receipt_index_sha256"):
        raise WriterRecoveryV2Error("r8_1_cross_plane_legacy_index_mismatch")
    if recovery_verification_v2.get("receipt_candidate_sha256") != r8_closure_v1.get("receipt_candidate_sha256"):
        raise WriterRecoveryV2Error("r8_1_cross_plane_receipt_candidate_mismatch")
    if recovery_verification_v2.get("recovery_status") != r8_closure_v1.get("recovery_status"):
        raise WriterRecoveryV2Error("r8_1_cross_plane_recovery_status_mismatch")

    body = {
        "schema": R8_1_CLOSURE_SCHEMA,
        "prior_writer_fencing_recovery_closure_sha256": r8_sha,
        "recovery_verification_v2_sha256": recovery_sha,
        "authority_anchor_sha256": anchor_sha,
        "authority_root_sha256": _sha(
            expected_authority_root_sha256, "expected_authority_root_sha256"
        ),
        "case_id": r8_closure_v1["case_id"],
        "case_sha256": r8_closure_v1["case_sha256"],
        "challenge_id": r8_closure_v1["challenge_id"],
        "writer_lease_sha256": r8_closure_v1["writer_lease_sha256"],
        "legacy_receipt_index_sha256": r8_closure_v1["current_receipt_index_sha256"],
        "paired_receipt_index_sha256": recovery_verification_v2["paired_receipt_index_sha256"],
        "receipt_candidate_sha256": r8_closure_v1["receipt_candidate_sha256"],
        "recovery_status": r8_closure_v1["recovery_status"],
        "recovery_action": r8_closure_v1["recovery_action"],
        "paired_receipt_identity_verified": True,
        "authority_root_anchor_consumed": True,
        "cross_plane_anchor_verified": True,
        "status": "WRITER_FENCING_RECOVERY_HARDENED_SHADOW_ONLY",
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
        "effects": dict(R8_EFFECTS),
        "safety": dict(SHADOW_SAFETY),
        "generated_at": _iso(generated_at, "generated_at"),
    }
    body["writer_fencing_recovery_closure_sha256"] = sha256_obj(body)
    return body

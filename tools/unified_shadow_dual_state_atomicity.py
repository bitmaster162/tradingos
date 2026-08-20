#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj

R8_1_CLOSURE_SCHEMA = "bitevo.shadow_writer_fencing_recovery_closure.v2"
R9_ATOMICITY_SCHEMA = "control_center.shadow_human_gate_dual_state_atomicity_verification.v1"
R9_CLOSURE_SCHEMA = "bitevo.shadow_dual_state_atomicity_closure.v1"

_CONTROL_EFFECTS = {
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

_TRADING_EFFECTS = {
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
        raise ShadowIntegrationError(f"dual_state_{field}_required")
    return value.strip()

def _sha(value: Any, field: str) -> str:
    text = _text(value, field).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ShadowIntegrationError(f"dual_state_{field}_must_be_sha256")
    return text

def _iso(value: Any, field: str) -> str:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise ShadowIntegrationError(f"dual_state_{field}_must_be_iso8601") from exc
    if parsed.tzinfo is None:
        raise ShadowIntegrationError(f"dual_state_{field}_timezone_required")
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
        raise ShadowIntegrationError(f"dual_state_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"dual_state_unsafe_{field}:{key}")

def _verify_effects(value: Any, expected: Mapping[str, bool], code: str) -> None:
    if not isinstance(value, Mapping) or set(value) != set(expected):
        raise ShadowIntegrationError(code)
    if any(value.get(key) is not False for key in expected):
        raise ShadowIntegrationError(code)

def _verify_r8_1(closure: Mapping[str, Any], expected_sha256: str) -> str:
    if not isinstance(closure, Mapping) or closure.get("schema") != R8_1_CLOSURE_SCHEMA:
        raise ShadowIntegrationError("dual_state_r8_1_schema_mismatch")
    digest = _verify_hash(closure, "writer_fencing_recovery_closure_sha256", "dual_state_r8_1_hash_mismatch")
    if digest != _sha(expected_sha256, "expected_r8_1_closure_sha256"):
        raise ShadowIntegrationError("dual_state_r8_1_external_digest_mismatch")
    _verify_safety(closure, "r8_1")
    _verify_effects(closure.get("effects"), _TRADING_EFFECTS, "dual_state_r8_1_effect_boundary_breached")
    if closure.get("status") != "WRITER_FENCING_RECOVERY_HARDENED_SHADOW_ONLY":
        raise ShadowIntegrationError("dual_state_r8_1_status_invalid")
    if closure.get("paired_receipt_identity_verified") is not True:
        raise ShadowIntegrationError("dual_state_r8_1_pair_guard_missing")
    if closure.get("authority_root_anchor_consumed") is not True or closure.get("cross_plane_anchor_verified") is not True:
        raise ShadowIntegrationError("dual_state_r8_1_anchor_guard_missing")
    if closure.get("decision") != "HOLD" or closure.get("action") != "WAIT":
        raise ShadowIntegrationError("dual_state_r8_1_gate_widening_forbidden")
    if closure.get("durable_commit_proven") is not False or closure.get("live_writer_backend_proven") is not False:
        raise ShadowIntegrationError("dual_state_r8_1_durability_overclaim")
    if closure.get("human_gate_write_performed") is not False or closure.get("current_truth_promotion_allowed") is not False:
        raise ShadowIntegrationError("dual_state_r8_1_write_or_truth_breached")
    if closure.get("semantic_acceptance") != "NOT_PERFORMED" or closure.get("apply_allowed") is not False:
        raise ShadowIntegrationError("dual_state_r8_1_acceptance_or_apply_breached")
    if closure.get("execution_authority") != "NONE" or closure.get("can_execute") is not False:
        raise ShadowIntegrationError("dual_state_r8_1_authority_breached")
    return digest

def _verify_atomicity(receipt: Mapping[str, Any], expected_sha256: str) -> str:
    if not isinstance(receipt, Mapping) or receipt.get("schema") != R9_ATOMICITY_SCHEMA:
        raise ShadowIntegrationError("dual_state_atomicity_schema_mismatch")
    digest = _verify_hash(receipt, "atomicity_verification_sha256", "dual_state_atomicity_hash_mismatch")
    if digest != _sha(expected_sha256, "expected_atomicity_verification_sha256"):
        raise ShadowIntegrationError("dual_state_atomicity_external_digest_mismatch")
    _verify_safety(receipt, "atomicity")
    _verify_effects(receipt.get("effects"), _CONTROL_EFFECTS, "dual_state_atomicity_effect_boundary_breached")
    if receipt.get("protocol_status") != "DUAL_STATE_ATOMICITY_VERIFIED_SHADOW_ONLY":
        raise ShadowIntegrationError("dual_state_atomicity_status_invalid")
    if receipt.get("dual_state_atomicity_model") != "ONE_TRANSACTION_TWO_LOGICAL_RECORDS":
        raise ShadowIntegrationError("dual_state_atomicity_model_invalid")
    if receipt.get("split_state_rejected") is not True:
        raise ShadowIntegrationError("dual_state_split_state_guard_missing")
    if receipt.get("lease_epoch_lineage_verified") is not True or receipt.get("aba_guard_verified") is not True:
        raise ShadowIntegrationError("dual_state_lease_lineage_guard_missing")
    if receipt.get("durability_status") != "PROTOCOL_VERIFIED_NO_DURABLE_BACKEND":
        raise ShadowIntegrationError("dual_state_durability_status_invalid")
    if receipt.get("write_performed") is not False or receipt.get("durable_commit_proven") is not False or receipt.get("live_backend_observed") is not False:
        raise ShadowIntegrationError("dual_state_durable_write_overclaim")
    if receipt.get("current_truth_promotion_allowed") is not False or receipt.get("apply_allowed") is not False:
        raise ShadowIntegrationError("dual_state_truth_or_apply_breached")
    if receipt.get("execution_authority") != "NONE" or receipt.get("can_execute") is not False:
        raise ShadowIntegrationError("dual_state_authority_breached")
    return digest

def build_dual_state_atomicity_closure(r8_1_closure: Mapping[str, Any], atomicity_verification: Mapping[str, Any], *, expected_r8_1_closure_sha256: str, expected_atomicity_verification_sha256: str, expected_authority_root_sha256: str, generated_at: str) -> dict[str, Any]:
    r8_sha = _verify_r8_1(r8_1_closure, expected_r8_1_closure_sha256)
    atomicity_sha = _verify_atomicity(atomicity_verification, expected_atomicity_verification_sha256)
    root = _sha(expected_authority_root_sha256, "expected_authority_root_sha256")
    if r8_1_closure.get("authority_root_sha256") != root:
        raise ShadowIntegrationError("dual_state_r8_1_authority_root_mismatch")
    if atomicity_verification.get("authority_root_sha256") != root:
        raise ShadowIntegrationError("dual_state_atomicity_authority_root_mismatch")
    for field in ("case_id", "case_sha256", "challenge_id"):
        if atomicity_verification.get(field) != r8_1_closure.get(field):
            raise ShadowIntegrationError(f"dual_state_cross_plane_{field}_mismatch")
    if atomicity_verification.get("current_writer_lease_sha256") != r8_1_closure.get("writer_lease_sha256"):
        raise ShadowIntegrationError("dual_state_writer_lease_mismatch")
    if atomicity_verification.get("prior_paired_receipt_index_sha256") != r8_1_closure.get("paired_receipt_index_sha256"):
        raise ShadowIntegrationError("dual_state_prior_receipt_index_mismatch")
    body = {
        "schema": R9_CLOSURE_SCHEMA,
        "r8_1_closure_sha256": r8_sha,
        "atomicity_verification_sha256": atomicity_sha,
        "authority_root_sha256": root,
        "case_id": r8_1_closure["case_id"],
        "case_sha256": r8_1_closure["case_sha256"],
        "challenge_id": r8_1_closure["challenge_id"],
        "writer_lease_sha256": r8_1_closure["writer_lease_sha256"],
        "prior_paired_receipt_index_sha256": r8_1_closure["paired_receipt_index_sha256"],
        "next_paired_receipt_index_sha256": atomicity_verification["next_paired_receipt_index_sha256"],
        "lease_lineage_sha256": atomicity_verification["lease_lineage_sha256"],
        "commit_id": atomicity_verification["commit_id"],
        "idempotency_key_sha256": atomicity_verification["idempotency_key_sha256"],
        "observed_pair_state": atomicity_verification["observed_pair_state"],
        "dual_state_atomicity_verified": True,
        "split_state_rejected": True,
        "lease_epoch_lineage_verified": True,
        "aba_guard_verified": True,
        "authority_root_retained": True,
        "status": "DUAL_STATE_ATOMICITY_BOUND_SHADOW_ONLY",
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
        "effects": dict(_TRADING_EFFECTS),
        "safety": dict(SHADOW_SAFETY),
        "generated_at": _iso(generated_at, "generated_at"),
    }
    body["dual_state_atomicity_closure_sha256"] = sha256_obj(body)
    return body

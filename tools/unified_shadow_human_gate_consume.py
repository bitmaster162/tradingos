#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj

ASYMMETRIC_REVEAL_CLOSURE_SCHEMA_V2 = "bitevo.shadow_asymmetric_reveal_closure.v2"
ATOMIC_CONSUME_VERIFICATION_SCHEMA = "control_center.shadow_human_gate_atomic_consume_verification.v1"
HUMAN_GATE_CONSUME_CLOSURE_SCHEMA = "bitevo.shadow_human_gate_consume_closure.v1"

_R6_EFFECTS = {
    "human_gate_write": False,
    "credential_registry_write": False,
    "nonce_registry_write": False,
    "registry_write": False,
    "ledger_write": False,
    "return_index_write": False,
    "current_truth_apply": False,
    "runtime_activation": False,
    "executor_dispatch": False,
    "signal": False,
    "order": False,
    "capital_effect": False,
}

_CONTROL_R7_EFFECTS = {
    "human_gate_write": False,
    "credential_registry_write": False,
    "nonce_registry_write": False,
    "current_truth_apply": False,
    "decision_ledger_write": False,
    "command_queue_write": False,
    "runtime_activation": False,
    "executor_dispatch": False,
    "signal": False,
    "order": False,
    "capital_effect": False,
}

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


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShadowIntegrationError(f"human_gate_consume_{field}_required")
    return value.strip()


def _sha(value: Any, field: str) -> str:
    text = _text(value, field).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ShadowIntegrationError(f"human_gate_consume_{field}_must_be_sha256")
    return text


def _iso(value: Any, field: str) -> str:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise ShadowIntegrationError(f"human_gate_consume_{field}_must_be_iso8601") from exc
    if parsed.tzinfo is None:
        raise ShadowIntegrationError(f"human_gate_consume_{field}_timezone_required")
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
        raise ShadowIntegrationError(f"human_gate_consume_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"human_gate_consume_unsafe_{field}:{key}")


def _verify_false_map(value: Any, expected: Mapping[str, bool], code: str) -> None:
    if not isinstance(value, Mapping) or set(value) != set(expected):
        raise ShadowIntegrationError(code)
    if any(value.get(key) is not False for key in expected):
        raise ShadowIntegrationError(code)


def _verify_r6_closure(closure: Mapping[str, Any], expected_sha256: str) -> str:
    if not isinstance(closure, Mapping) or closure.get("schema") != ASYMMETRIC_REVEAL_CLOSURE_SCHEMA_V2:
        raise ShadowIntegrationError("human_gate_consume_wrong_r6_closure_schema")
    closure_sha = _verify_hash(closure, "asymmetric_reveal_closure_sha256", "human_gate_consume_r6_closure_hash_mismatch")
    if closure_sha != _sha(expected_sha256, "expected_r6_closure_sha256"):
        raise ShadowIntegrationError("human_gate_consume_r6_closure_external_digest_mismatch")
    _verify_safety(closure, "r6_closure")
    _verify_false_map(closure.get("effects"), _R6_EFFECTS, "human_gate_consume_r6_effect_boundary_breached")
    if closure.get("authentication_status") != "ASYMMETRIC_CUSTODY_VERIFIED_SHADOW_ONLY":
        raise ShadowIntegrationError("human_gate_consume_r6_authentication_status_invalid")
    if closure.get("trust_upgrade") != "INDEPENDENT_ASSERTION_AND_APPROVAL_DIGESTS_BOUND":
        raise ShadowIntegrationError("human_gate_consume_r6_trust_upgrade_invalid")
    if closure.get("external_assertion_digest_consumed") is not True:
        raise ShadowIntegrationError("human_gate_consume_r6_assertion_digest_guard_missing")
    if closure.get("local_signature_math_verified") is not False or closure.get("physical_human_presence_proven") is not False:
        raise ShadowIntegrationError("human_gate_consume_r6_authenticity_overclaim")
    if closure.get("human_gate_write_performed") is not False or closure.get("current_truth_promotion_allowed") is not False:
        raise ShadowIntegrationError("human_gate_consume_r6_write_or_truth_breached")
    if closure.get("semantic_acceptance") != "NOT_PERFORMED" or closure.get("apply_allowed") is not False:
        raise ShadowIntegrationError("human_gate_consume_r6_acceptance_or_apply_breached")
    if closure.get("execution_authority") != "NONE" or closure.get("can_execute") is not False:
        raise ShadowIntegrationError("human_gate_consume_r6_authority_breached")
    return closure_sha


def _verify_atomic(receipt: Mapping[str, Any], expected_sha256: str) -> str:
    if not isinstance(receipt, Mapping) or receipt.get("schema") != ATOMIC_CONSUME_VERIFICATION_SCHEMA:
        raise ShadowIntegrationError("human_gate_consume_wrong_atomic_schema")
    receipt_sha = _verify_hash(receipt, "atomic_consume_verification_sha256", "human_gate_consume_atomic_hash_mismatch")
    if receipt_sha != _sha(expected_sha256, "expected_atomic_consume_sha256"):
        raise ShadowIntegrationError("human_gate_consume_atomic_external_digest_mismatch")
    _verify_safety(receipt, "atomic")
    _verify_false_map(receipt.get("effects"), _CONTROL_R7_EFFECTS, "human_gate_consume_atomic_effect_boundary_breached")
    if receipt.get("toctou_guard_model") != "COMPARE_AND_SWAP_PRECONDITION":
        raise ShadowIntegrationError("human_gate_consume_atomic_cas_guard_missing")
    if receipt.get("atomicity_status") != "PROTOCOL_VERIFIED_NO_DURABLE_COMMIT":
        raise ShadowIntegrationError("human_gate_consume_atomic_status_invalid")
    if receipt.get("single_use_status") != "CANDIDATE_ONLY_NOT_DURABLY_ENFORCED":
        raise ShadowIntegrationError("human_gate_consume_atomic_single_use_status_invalid")
    if receipt.get("commit_performed") is not False or receipt.get("human_gate_write_performed") is not False:
        raise ShadowIntegrationError("human_gate_consume_atomic_commit_overclaim")
    if receipt.get("current_truth_promotion_allowed") is not False or receipt.get("apply_allowed") is not False:
        raise ShadowIntegrationError("human_gate_consume_atomic_truth_or_apply_breached")
    if receipt.get("execution_authority") != "NONE" or receipt.get("can_execute") is not False:
        raise ShadowIntegrationError("human_gate_consume_atomic_authority_breached")
    if receipt.get("cas_generation_to") != receipt.get("cas_generation_from") + 1:
        raise ShadowIntegrationError("human_gate_consume_atomic_generation_transition_invalid")
    return receipt_sha


def build_human_gate_consume_closure(
    asymmetric_reveal_closure_v2: Mapping[str, Any],
    atomic_consume_verification: Mapping[str, Any],
    *,
    expected_asymmetric_reveal_closure_sha256: str,
    expected_atomic_consume_verification_sha256: str,
    generated_at: str,
) -> dict[str, Any]:
    r6_sha = _verify_r6_closure(asymmetric_reveal_closure_v2, expected_asymmetric_reveal_closure_sha256)
    atomic_sha = _verify_atomic(atomic_consume_verification, expected_atomic_consume_verification_sha256)
    if atomic_consume_verification.get("case_id") != asymmetric_reveal_closure_v2.get("case_id"):
        raise ShadowIntegrationError("human_gate_consume_case_mismatch")
    if atomic_consume_verification.get("case_sha256") != asymmetric_reveal_closure_v2.get("case_sha256"):
        raise ShadowIntegrationError("human_gate_consume_case_sha_mismatch")
    if atomic_consume_verification.get("challenge_id") != asymmetric_reveal_closure_v2.get("challenge_id"):
        raise ShadowIntegrationError("human_gate_consume_challenge_mismatch")
    if atomic_consume_verification.get("approval_verification_sha256") != asymmetric_reveal_closure_v2.get("asymmetric_approval_verification_sha256"):
        raise ShadowIntegrationError("human_gate_consume_approval_binding_mismatch")
    generated = _iso(generated_at, "generated_at")
    body = {
        "schema": HUMAN_GATE_CONSUME_CLOSURE_SCHEMA,
        "case_id": asymmetric_reveal_closure_v2["case_id"],
        "case_sha256": asymmetric_reveal_closure_v2["case_sha256"],
        "challenge_id": asymmetric_reveal_closure_v2["challenge_id"],
        "asymmetric_reveal_closure_sha256": r6_sha,
        "asymmetric_approval_verification_sha256": asymmetric_reveal_closure_v2["asymmetric_approval_verification_sha256"],
        "atomic_consume_verification_sha256": atomic_sha,
        "prior_human_gate_state_sha256": atomic_consume_verification["prior_state_sha256"],
        "next_human_gate_state_candidate_sha256": atomic_consume_verification["next_state_candidate_sha256"],
        "cas_generation_from": atomic_consume_verification["cas_generation_from"],
        "cas_generation_to": atomic_consume_verification["cas_generation_to"],
        "toctou_guard_model": "COMPARE_AND_SWAP_PRECONDITION",
        "single_use_protocol": "BOUND_BUT_NOT_DURABLY_COMMITTED",
        "status": "HUMAN_GATE_CONSUME_BOUND_SHADOW_ONLY",
        "durable_commit_proven": False,
        "human_gate_write_performed": False,
        "current_truth_promotion_allowed": False,
        "semantic_acceptance": "NOT_PERFORMED",
        "apply_allowed": False,
        "execution_authority": "NONE",
        "can_execute": False,
        "decision": "HOLD",
        "action": "WAIT",
        "effects": dict(_R7_EFFECTS),
        "safety": dict(SHADOW_SAFETY),
        "generated_at": generated,
    }
    body["human_gate_consume_closure_sha256"] = sha256_obj(body)
    return body

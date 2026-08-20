#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj, validate_trade_case
from tools.unified_shadow_domain_history_closure import DOMAIN_HISTORY_CLOSURE_SCHEMA
from tools.unified_shadow_domain_subjects import HUMAN_REVEAL_SCHEMA, SUBJECT_MANIFEST_SCHEMA

APPROVAL_VERIFICATION_SCHEMA = "control_center.shadow_human_approval_verification.v1"
APPROVAL_REGISTRY_SCHEMA = "control_center.shadow_human_approval_registry_snapshot.v1"
AUTHENTICATED_REVEAL_CLOSURE_SCHEMA = "bitevo.shadow_authenticated_reveal_closure.v1"

_CONTROL_EFFECTS = {
    "human_gate_write": False,
    "current_truth_apply": False,
    "decision_ledger_write": False,
    "command_queue_write": False,
    "runtime_activation": False,
    "executor_dispatch": False,
    "signal": False,
    "order": False,
    "capital_effect": False,
}

_DOMAIN_EFFECTS = {
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

_R5_EFFECTS = {
    "human_gate_write": False,
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


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShadowIntegrationError(f"human_custody_{field}_required")
    return value.strip()


def _sha(value: Any, field: str) -> str:
    text = _text(value, field).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ShadowIntegrationError(f"human_custody_{field}_must_be_sha256")
    return text


def _iso(value: Any, field: str) -> tuple[str, float]:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise ShadowIntegrationError(f"human_custody_{field}_must_be_iso8601") from exc
    if parsed.tzinfo is None:
        raise ShadowIntegrationError(f"human_custody_{field}_timezone_required")
    return text, parsed.timestamp()


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
        raise ShadowIntegrationError(f"human_custody_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"human_custody_unsafe_{field}:{key}")


def _verify_false_map(value: Any, expected: Mapping[str, bool], code: str) -> None:
    if not isinstance(value, Mapping) or set(value) != set(expected):
        raise ShadowIntegrationError(code)
    if any(value.get(key) is not False for key in expected):
        raise ShadowIntegrationError(code)


def derive_reveal_intent_sha256(
    *,
    case_id: str,
    case_sha256: str,
    packet_sha256: str,
    twin_prediction_id: str,
    actual_choice: str,
    responded_at: str,
) -> str:
    response_text, _ = _iso(responded_at, "responded_at")
    return sha256_obj(
        {
            "case_id": _text(case_id, "case_id"),
            "case_sha256": _sha(case_sha256, "case_sha256"),
            "packet_sha256": _sha(packet_sha256, "packet_sha256"),
            "twin_prediction_id": _sha(twin_prediction_id, "twin_prediction_id"),
            "actual_choice": _text(actual_choice, "actual_choice").upper(),
            "responded_at": response_text,
        }
    )


def _verify_reveal(case: Mapping[str, Any], reveal: Mapping[str, Any]) -> str:
    if not isinstance(reveal, Mapping) or reveal.get("schema") != HUMAN_REVEAL_SCHEMA:
        raise ShadowIntegrationError("human_custody_wrong_reveal_schema")
    reveal_sha = _verify_hash(reveal, "reveal_sha256", "human_custody_reveal_hash_mismatch")
    _verify_safety(reveal, "reveal")
    if reveal.get("case_id") != case["case_id"] or reveal.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("human_custody_reveal_case_mismatch")
    if reveal.get("human_decision_status") != "REVEALED":
        raise ShadowIntegrationError("human_custody_reveal_status_invalid")
    if reveal.get("write_performed") is not False or reveal.get("apply_allowed") is not False:
        raise ShadowIntegrationError("human_custody_reveal_effect_boundary_breached")
    if reveal.get("execution_authority") != "NONE" or reveal.get("can_execute") is not False:
        raise ShadowIntegrationError("human_custody_reveal_authority_breached")
    if reveal.get("actual_choice") not in case["options"]:
        raise ShadowIntegrationError("human_custody_reveal_choice_invalid")
    _sha(reveal.get("packet_sha256"), "reveal.packet_sha256")
    _sha(reveal.get("twin_prediction_id"), "reveal.twin_prediction_id")
    _iso(reveal.get("decided_at"), "reveal.decided_at")
    return reveal_sha


def _verify_manifest(case: Mapping[str, Any], manifest: Mapping[str, Any], reveal_sha: str) -> str:
    if not isinstance(manifest, Mapping) or manifest.get("schema") != SUBJECT_MANIFEST_SCHEMA:
        raise ShadowIntegrationError("human_custody_wrong_manifest_schema")
    manifest_sha = _verify_hash(manifest, "subject_manifest_sha256", "human_custody_manifest_hash_mismatch")
    _verify_safety(manifest, "manifest")
    _verify_false_map(manifest.get("effects"), _DOMAIN_EFFECTS, "human_custody_manifest_effect_boundary_breached")
    if manifest.get("case_id") != case["case_id"] or manifest.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("human_custody_manifest_case_mismatch")
    if manifest.get("subject_binding_complete") is not True or manifest.get("semantic_acceptance") != "NOT_PERFORMED":
        raise ShadowIntegrationError("human_custody_manifest_status_invalid")
    if manifest.get("execution_authority") != "NONE":
        raise ShadowIntegrationError("human_custody_manifest_authority_breached")
    subjects = manifest.get("subjects")
    if not isinstance(subjects, (list, tuple)):
        raise ShadowIntegrationError("human_custody_manifest_subjects_missing")
    reveal_rows = [row for row in subjects if isinstance(row, Mapping) and row.get("event_type") == "HUMAN_REVEAL"]
    if len(reveal_rows) != 1 or reveal_rows[0].get("subject_sha256") != reveal_sha:
        raise ShadowIntegrationError("human_custody_manifest_reveal_binding_mismatch")
    return manifest_sha


def _verify_domain_closure(case: Mapping[str, Any], closure: Mapping[str, Any], manifest_sha: str) -> str:
    if not isinstance(closure, Mapping) or closure.get("schema") != DOMAIN_HISTORY_CLOSURE_SCHEMA:
        raise ShadowIntegrationError("human_custody_wrong_domain_closure_schema")
    closure_sha = _verify_hash(closure, "domain_history_closure_sha256", "human_custody_domain_closure_hash_mismatch")
    _verify_safety(closure, "domain_closure")
    _verify_false_map(closure.get("effects"), _DOMAIN_EFFECTS, "human_custody_domain_closure_effect_boundary_breached")
    if closure.get("case_id") != case["case_id"] or closure.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("human_custody_domain_closure_case_mismatch")
    if closure.get("subject_manifest_sha256") != manifest_sha:
        raise ShadowIntegrationError("human_custody_domain_closure_manifest_mismatch")
    if closure.get("status") != "DOMAIN_HISTORY_CLOSED_SHADOW_ONLY":
        raise ShadowIntegrationError("human_custody_domain_closure_status_invalid")
    if closure.get("subject_binding_complete") is not True or closure.get("admission_binding_complete") is not True:
        raise ShadowIntegrationError("human_custody_domain_closure_binding_incomplete")
    if closure.get("history_write_performed") is not False or closure.get("semantic_acceptance") != "NOT_PERFORMED":
        raise ShadowIntegrationError("human_custody_domain_closure_effect_or_acceptance_breached")
    if closure.get("execution_authority") != "NONE":
        raise ShadowIntegrationError("human_custody_domain_closure_authority_breached")
    return closure_sha


def _verify_registry_candidate(approval: Mapping[str, Any]) -> str:
    registry = approval.get("next_registry_candidate")
    if not isinstance(registry, Mapping) or registry.get("schema") != APPROVAL_REGISTRY_SCHEMA:
        raise ShadowIntegrationError("human_custody_registry_candidate_schema_mismatch")
    registry_sha = _verify_hash(registry, "registry_sha256", "human_custody_registry_candidate_hash_mismatch")
    if approval.get("next_registry_candidate_sha256") != registry_sha:
        raise ShadowIntegrationError("human_custody_registry_candidate_binding_mismatch")
    _verify_safety(registry, "registry_candidate")
    _verify_false_map(registry.get("effects"), _CONTROL_EFFECTS, "human_custody_registry_candidate_effect_boundary_breached")
    if registry.get("write_allowed") is not False or registry.get("apply_allowed") is not False:
        raise ShadowIntegrationError("human_custody_registry_candidate_write_breached")
    if registry.get("execution_authority") != "NONE":
        raise ShadowIntegrationError("human_custody_registry_candidate_authority_breached")
    entries = registry.get("entries")
    if not isinstance(entries, (list, tuple)) or registry.get("entry_count") != len(entries):
        raise ShadowIntegrationError("human_custody_registry_candidate_count_mismatch")
    matches = [entry for entry in entries if isinstance(entry, Mapping) and entry.get("challenge_id") == approval.get("challenge_id")]
    if len(matches) != 1:
        raise ShadowIntegrationError("human_custody_registry_candidate_challenge_missing")
    if matches[0].get("challenge_sha256") != approval.get("challenge_sha256") or matches[0].get("attestation_sha256") != approval.get("attestation_sha256"):
        raise ShadowIntegrationError("human_custody_registry_candidate_subject_mismatch")
    return registry_sha


def _verify_approval(
    case: Mapping[str, Any],
    reveal: Mapping[str, Any],
    approval: Mapping[str, Any],
    *,
    expected_approval_verification_sha256: str,
    expected_human_subject_id: str,
    expected_custody_provider_id: str,
    expected_verifier_id: str,
    expected_verifier_key_id: str,
) -> str:
    if not isinstance(approval, Mapping) or approval.get("schema") != APPROVAL_VERIFICATION_SCHEMA:
        raise ShadowIntegrationError("human_custody_wrong_approval_schema")
    approval_sha = _verify_hash(approval, "approval_verification_sha256", "human_custody_approval_hash_mismatch")
    if approval_sha != _sha(expected_approval_verification_sha256, "expected_approval_verification_sha256"):
        raise ShadowIntegrationError("human_custody_approval_external_digest_mismatch")
    _verify_safety(approval, "approval")
    _verify_false_map(approval.get("effects"), _CONTROL_EFFECTS, "human_custody_approval_effect_boundary_breached")
    if approval.get("case_id") != case["case_id"] or approval.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("human_custody_approval_case_mismatch")
    if approval.get("packet_sha256") != reveal["packet_sha256"] or approval.get("twin_prediction_id") != reveal["twin_prediction_id"]:
        raise ShadowIntegrationError("human_custody_approval_upstream_binding_mismatch")
    if approval.get("actual_choice") != reveal["actual_choice"] or approval.get("responded_at") != reveal["decided_at"]:
        raise ShadowIntegrationError("human_custody_approval_reveal_mismatch")
    if approval.get("human_subject_id") != _text(expected_human_subject_id, "expected_human_subject_id"):
        raise ShadowIntegrationError("human_custody_human_subject_mismatch")
    if approval.get("custody_provider_id") != _text(expected_custody_provider_id, "expected_custody_provider_id"):
        raise ShadowIntegrationError("human_custody_provider_mismatch")
    if approval.get("verifier_id") != _text(expected_verifier_id, "expected_verifier_id"):
        raise ShadowIntegrationError("human_custody_verifier_mismatch")
    if approval.get("verifier_key_id") != _text(expected_verifier_key_id, "expected_verifier_key_id"):
        raise ShadowIntegrationError("human_custody_verifier_key_mismatch")
    if approval.get("custody_mac_verified") is not True or approval.get("challenge_window_verified") is not True:
        raise ShadowIntegrationError("human_custody_cryptographic_or_window_verification_missing")
    if approval.get("challenge_unused_in_expected_registry") is not True:
        raise ShadowIntegrationError("human_custody_challenge_replay_guard_missing")
    if approval.get("single_use_status") != "ADMITTABLE_UNUSED_CHALLENGE_SHADOW_ONLY":
        raise ShadowIntegrationError("human_custody_single_use_status_invalid")
    if approval.get("approval_scope") != "HUMAN_REVEAL_ONLY":
        raise ShadowIntegrationError("human_custody_approval_scope_invalid")
    if approval.get("status") != "HUMAN_CUSTODY_APPROVAL_VERIFIED_SHADOW_ONLY":
        raise ShadowIntegrationError("human_custody_approval_status_invalid")
    if approval.get("human_identity_scope") != "CUSTODY_PROVIDER_SUBJECT_ASSERTION_ONLY":
        raise ShadowIntegrationError("human_custody_identity_scope_overclaim")
    if approval.get("cryptographic_property") != "HMAC_SHA256_VERIFIER_KEY_POSSESSION":
        raise ShadowIntegrationError("human_custody_cryptographic_property_invalid")
    if approval.get("physical_human_presence_proven") is not False:
        raise ShadowIntegrationError("human_custody_physical_presence_overclaim")
    if approval.get("registry_write_performed") is not False or approval.get("apply_allowed") is not False:
        raise ShadowIntegrationError("human_custody_approval_write_breached")
    if approval.get("execution_authority") != "NONE" or approval.get("can_execute") is not False:
        raise ShadowIntegrationError("human_custody_approval_authority_breached")
    _sha(approval.get("challenge_id"), "approval.challenge_id")
    _sha(approval.get("challenge_sha256"), "approval.challenge_sha256")
    _sha(approval.get("attestation_sha256"), "approval.attestation_sha256")
    _sha(approval.get("prior_registry_sha256"), "approval.prior_registry_sha256")
    _iso(approval.get("responded_at"), "approval.responded_at")
    _iso(approval.get("verified_at"), "approval.verified_at")
    expected_intent = derive_reveal_intent_sha256(
        case_id=case["case_id"],
        case_sha256=case["case_sha256"],
        packet_sha256=reveal["packet_sha256"],
        twin_prediction_id=reveal["twin_prediction_id"],
        actual_choice=reveal["actual_choice"],
        responded_at=reveal["decided_at"],
    )
    if approval.get("approved_reveal_intent_sha256") != expected_intent:
        raise ShadowIntegrationError("human_custody_reveal_intent_mismatch")
    _verify_registry_candidate(approval)
    return approval_sha


def build_authenticated_reveal_closure(
    trade_case: Mapping[str, Any],
    human_reveal_receipt: Mapping[str, Any],
    subject_manifest: Mapping[str, Any],
    domain_history_closure: Mapping[str, Any],
    human_approval_verification: Mapping[str, Any],
    *,
    expected_approval_verification_sha256: str,
    expected_human_subject_id: str,
    expected_custody_provider_id: str,
    expected_verifier_id: str,
    expected_verifier_key_id: str,
    generated_at: str,
) -> dict[str, Any]:
    """Bind R4 reveal/history to a Control Center custody approval without creating execution authority."""
    case = validate_trade_case(trade_case)
    reveal_sha = _verify_reveal(case, human_reveal_receipt)
    manifest_sha = _verify_manifest(case, subject_manifest, reveal_sha)
    domain_closure_sha = _verify_domain_closure(case, domain_history_closure, manifest_sha)
    approval_sha = _verify_approval(
        case,
        human_reveal_receipt,
        human_approval_verification,
        expected_approval_verification_sha256=expected_approval_verification_sha256,
        expected_human_subject_id=expected_human_subject_id,
        expected_custody_provider_id=expected_custody_provider_id,
        expected_verifier_id=expected_verifier_id,
        expected_verifier_key_id=expected_verifier_key_id,
    )
    generated_text, _ = _iso(generated_at, "generated_at")

    body = {
        "schema": AUTHENTICATED_REVEAL_CLOSURE_SCHEMA,
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "reveal_sha256": reveal_sha,
        "subject_manifest_sha256": manifest_sha,
        "domain_history_closure_sha256": domain_closure_sha,
        "approval_verification_sha256": approval_sha,
        "challenge_id": human_approval_verification["challenge_id"],
        "human_subject_id": human_approval_verification["human_subject_id"],
        "session_id": human_approval_verification["session_id"],
        "device_id": human_approval_verification["device_id"],
        "custody_provider_id": human_approval_verification["custody_provider_id"],
        "verifier_id": human_approval_verification["verifier_id"],
        "verifier_key_id": human_approval_verification["verifier_key_id"],
        "actual_choice": human_reveal_receipt["actual_choice"],
        "decided_at": human_reveal_receipt["decided_at"],
        "approved_reveal_intent_sha256": human_approval_verification["approved_reveal_intent_sha256"],
        "authentication_status": "TRUSTED_CUSTODY_ATTESTED_SHADOW_ONLY",
        "human_identity_scope": "CUSTODY_PROVIDER_SUBJECT_ASSERTION_ONLY",
        "cryptographic_property": "HMAC_SHA256_VERIFIER_KEY_POSSESSION",
        "physical_human_presence_proven": False,
        "single_use_registry_candidate_verified": True,
        "current_truth_promotion_allowed": False,
        "history_write_performed": False,
        "human_gate_write_performed": False,
        "semantic_acceptance": "NOT_PERFORMED",
        "apply_allowed": False,
        "execution_authority": "NONE",
        "can_execute": False,
        "effects": dict(_R5_EFFECTS),
        "safety": dict(SHADOW_SAFETY),
        "generated_at": generated_text,
    }
    body["authenticated_reveal_closure_sha256"] = sha256_obj(body)
    return body

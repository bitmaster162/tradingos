#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj, validate_trade_case
from tools.unified_shadow_domain_subjects import (
    DOMAIN_HISTORY_VERIFICATION_SCHEMA,
    SUBJECT_MANIFEST_SCHEMA,
)
from tools.unified_shadow_history_replay import HISTORY_VERIFICATION_SCHEMA

ADMISSION_SCHEMA = "continuityos.shadow_replay_admission_candidate.v1"
DOMAIN_HISTORY_CLOSURE_SCHEMA = "bitevo.shadow_domain_history_closure.v1"

_NO_EFFECTS = {
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
        raise ShadowIntegrationError(f"domain_history_{field}_required")
    return value.strip()


def _sha(value: Any, field: str) -> str:
    text = _text(value, field).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ShadowIntegrationError(f"domain_history_{field}_must_be_sha256")
    return text


def _verify_hash(record: Mapping[str, Any], field: str, code: str) -> str:
    if not isinstance(record, Mapping):
        raise ShadowIntegrationError(code)
    supplied = _sha(record.get(field), field)
    expected = sha256_obj({k: v for k, v in record.items() if k != field})
    if supplied != expected:
        raise ShadowIntegrationError(code)
    return supplied


def _safe(record: Mapping[str, Any], field: str) -> None:
    safety = record.get("safety") if isinstance(record, Mapping) else None
    if not isinstance(safety, Mapping):
        raise ShadowIntegrationError(f"domain_history_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"domain_history_unsafe_{field}:{key}")


def _no_effects(record: Mapping[str, Any], field: str) -> None:
    effects = record.get("effects") if isinstance(record, Mapping) else None
    if not isinstance(effects, Mapping) or set(effects) != set(_NO_EFFECTS):
        raise ShadowIntegrationError(f"domain_history_{field}_effect_keys_mismatch")
    if any(value is not False for value in effects.values()):
        raise ShadowIntegrationError(f"domain_history_{field}_effect_boundary_breached")


def build_domain_history_closure(
    trade_case: Mapping[str, Any],
    admission_candidate: Mapping[str, Any],
    history_verification: Mapping[str, Any],
    subject_manifest: Mapping[str, Any],
    domain_history_verification: Mapping[str, Any],
    *,
    generated_at: str,
) -> dict[str, Any]:
    """Bind the generic ContinuityOS admission to the R4 domain-subject membrane without applying history."""
    case = validate_trade_case(trade_case)

    if not isinstance(history_verification, Mapping) or history_verification.get("schema") != HISTORY_VERIFICATION_SCHEMA:
        raise ShadowIntegrationError("domain_history_wrong_history_schema")
    history_sha = _verify_hash(history_verification, "history_verification_sha256", "domain_history_history_hash_mismatch")
    _safe(history_verification, "history")
    _no_effects(history_verification, "history")
    if history_verification.get("case_id") != case["case_id"] or history_verification.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("domain_history_history_case_mismatch")
    if history_verification.get("status") != "HISTORY_CHAIN_VERIFIED_SHADOW_ONLY":
        raise ShadowIntegrationError("domain_history_history_status_invalid")

    if not isinstance(subject_manifest, Mapping) or subject_manifest.get("schema") != SUBJECT_MANIFEST_SCHEMA:
        raise ShadowIntegrationError("domain_history_wrong_manifest_schema")
    manifest_sha = _verify_hash(subject_manifest, "subject_manifest_sha256", "domain_history_manifest_hash_mismatch")
    _safe(subject_manifest, "manifest")
    _no_effects(subject_manifest, "manifest")
    if subject_manifest.get("case_id") != case["case_id"] or subject_manifest.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("domain_history_manifest_case_mismatch")

    if not isinstance(domain_history_verification, Mapping) or domain_history_verification.get("schema") != DOMAIN_HISTORY_VERIFICATION_SCHEMA:
        raise ShadowIntegrationError("domain_history_wrong_domain_verification_schema")
    domain_sha = _verify_hash(
        domain_history_verification,
        "domain_history_verification_sha256",
        "domain_history_domain_verification_hash_mismatch",
    )
    _safe(domain_history_verification, "domain_verification")
    _no_effects(domain_history_verification, "domain_verification")
    if domain_history_verification.get("case_id") != case["case_id"] or domain_history_verification.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("domain_history_domain_verification_case_mismatch")
    if domain_history_verification.get("history_verification_sha256") != history_sha:
        raise ShadowIntegrationError("domain_history_domain_history_binding_mismatch")
    if domain_history_verification.get("subject_manifest_sha256") != manifest_sha:
        raise ShadowIntegrationError("domain_history_domain_manifest_binding_mismatch")
    if domain_history_verification.get("status") != "DOMAIN_SUBJECTS_BOUND_SHADOW_ONLY" or domain_history_verification.get("subject_binding_complete") is not True:
        raise ShadowIntegrationError("domain_history_domain_verification_status_invalid")
    if domain_history_verification.get("history_write_performed") is not False or domain_history_verification.get("semantic_acceptance") != "NOT_PERFORMED":
        raise ShadowIntegrationError("domain_history_domain_effect_or_acceptance_breached")
    if domain_history_verification.get("execution_authority") != "NONE":
        raise ShadowIntegrationError("domain_history_domain_authority_breached")

    if not isinstance(admission_candidate, Mapping) or admission_candidate.get("schema") != ADMISSION_SCHEMA:
        raise ShadowIntegrationError("domain_history_wrong_admission_schema")
    admission_sha = _verify_hash(
        admission_candidate,
        "admission_candidate_sha256",
        "domain_history_admission_hash_mismatch",
    )
    _safe(admission_candidate, "admission")
    if history_verification.get("admission_candidate_sha256") != admission_sha:
        raise ShadowIntegrationError("domain_history_admission_history_binding_mismatch")
    if admission_candidate.get("case_id") != case["case_id"] or admission_candidate.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("domain_history_admission_case_mismatch")
    if admission_candidate.get("case_binding_sha256") != history_verification.get("case_binding_sha256"):
        raise ShadowIntegrationError("domain_history_admission_case_binding_mismatch")
    if admission_candidate.get("status") != "ADMITTABLE_NEW_CASE_SHADOW_ONLY":
        raise ShadowIntegrationError("domain_history_admission_status_invalid")
    if admission_candidate.get("registry_write_performed") is not False or admission_candidate.get("apply_allowed") is not False:
        raise ShadowIntegrationError("domain_history_admission_effect_boundary_breached")
    if admission_candidate.get("execution_authority") != "NONE":
        raise ShadowIntegrationError("domain_history_admission_authority_breached")

    subjects = subject_manifest.get("subjects")
    if not isinstance(subjects, (list, tuple)) or not subjects:
        raise ShadowIntegrationError("domain_history_manifest_subjects_missing")
    first = subjects[0]
    if not isinstance(first, Mapping) or first.get("event_type") != "CASE_QUALIFIED":
        raise ShadowIntegrationError("domain_history_case_qualified_subject_missing")
    replay_input_sha = _sha(first.get("subject_sha256"), "case_qualified.subject_sha256")
    if admission_candidate.get("replay_input_sha256") != replay_input_sha:
        raise ShadowIntegrationError("domain_history_admission_replay_input_mismatch")

    body = {
        "schema": DOMAIN_HISTORY_CLOSURE_SCHEMA,
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "case_binding_sha256": history_verification["case_binding_sha256"],
        "admission_candidate_sha256": admission_sha,
        "history_verification_sha256": history_sha,
        "subject_manifest_sha256": manifest_sha,
        "domain_history_verification_sha256": domain_sha,
        "case_qualified_replay_input_sha256": replay_input_sha,
        "subject_binding_complete": True,
        "admission_binding_complete": True,
        "status": "DOMAIN_HISTORY_CLOSED_SHADOW_ONLY",
        "history_write_performed": False,
        "semantic_acceptance": "NOT_PERFORMED",
        "effects": dict(_NO_EFFECTS),
        "execution_authority": "NONE",
        "safety": dict(SHADOW_SAFETY),
        "generated_at": _text(generated_at, "generated_at"),
    }
    body["domain_history_closure_sha256"] = sha256_obj(body)
    return body

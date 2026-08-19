#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping, Sequence

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj, validate_trade_case

ADMISSION_SCHEMA = "continuityos.shadow_replay_admission_candidate.v1"
APPEND_SCHEMA = "continuityos.shadow_case_append_candidate.v1"
LEDGER_SCHEMA = "continuityos.shadow_case_ledger_snapshot.v1"
EVENT_SCHEMA = "continuityos.shadow_case_event.v1"
RETURN_DEDUP_SCHEMA = "control_return_broker.shadow_return_dedup_candidate.v1"
HISTORY_VERIFICATION_SCHEMA = "bitevo.shadow_history_replay_verification.v1"

_EVENT_ORDER = {
    "CASE_QUALIFIED": 10,
    "TWIN_COMMITTED": 20,
    "DECISION_PACKET": 30,
    "HUMAN_REVEAL": 40,
    "OUTCOME_RECEIPT": 50,
    "RETURN_INTAKE": 60,
}
_REQUIRED_COMPLETE_HISTORY = tuple(_EVENT_ORDER)

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
        raise ShadowIntegrationError(f"history_{field}_required")
    return value.strip()


def _sha(value: Any, field: str) -> str:
    text = _text(value, field).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ShadowIntegrationError(f"history_{field}_must_be_sha256")
    return text


def _safe(record: Mapping[str, Any], field: str) -> None:
    safety = record.get("safety") if isinstance(record, Mapping) else None
    if not isinstance(safety, Mapping):
        raise ShadowIntegrationError(f"history_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"history_unsafe_{field}:{key}")


def _verify_hash(record: Mapping[str, Any], field: str, code: str) -> str:
    if not isinstance(record, Mapping):
        raise ShadowIntegrationError(code)
    supplied = _sha(record.get(field), field)
    expected = sha256_obj({k: v for k, v in record.items() if k != field})
    if supplied != expected:
        raise ShadowIntegrationError(code)
    return supplied


def _verify_admission(case: Mapping[str, Any], admission: Mapping[str, Any]) -> tuple[str, str]:
    if not isinstance(admission, Mapping) or admission.get("schema") != ADMISSION_SCHEMA:
        raise ShadowIntegrationError("history_wrong_admission_schema")
    _safe(admission, "admission")
    admission_sha = _verify_hash(admission, "admission_candidate_sha256", "history_admission_hash_mismatch")
    if admission.get("case_id") != case["case_id"] or admission.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("history_admission_case_mismatch")
    if admission.get("status") != "ADMITTABLE_NEW_CASE_SHADOW_ONLY":
        raise ShadowIntegrationError("history_admission_status_invalid")
    if admission.get("registry_write_performed") is not False or admission.get("apply_allowed") is not False:
        raise ShadowIntegrationError("history_admission_effect_boundary_breached")
    if admission.get("execution_authority") != "NONE":
        raise ShadowIntegrationError("history_admission_authority_breached")
    binding = _sha(admission.get("case_binding_sha256"), "admission.case_binding_sha256")
    _sha(admission.get("replay_input_sha256"), "admission.replay_input_sha256")
    _text(admission.get("ledger_id"), "admission.ledger_id")
    return admission_sha, binding


def _verify_return_dedup(
    receipt: Mapping[str, Any],
    *,
    expected_transaction_sha256: str,
    expected_intake_sha256: str,
) -> str:
    if not isinstance(receipt, Mapping) or receipt.get("schema") != RETURN_DEDUP_SCHEMA:
        raise ShadowIntegrationError("history_wrong_return_dedup_schema")
    _safe(receipt, "return_dedup")
    receipt_sha = _verify_hash(receipt, "dedup_candidate_sha256", "history_return_dedup_hash_mismatch")
    if receipt.get("source_transaction_sha256") != _sha(expected_transaction_sha256, "expected_transaction_sha256"):
        raise ShadowIntegrationError("history_return_transaction_mismatch")
    if receipt.get("shadow_intake_sha256") != _sha(expected_intake_sha256, "expected_intake_sha256"):
        raise ShadowIntegrationError("history_return_intake_mismatch")
    if receipt.get("status") != "UNIQUE_RETURN_CANDIDATE_SHADOW_ONLY":
        raise ShadowIntegrationError("history_return_dedup_status_invalid")
    if receipt.get("index_write_performed") is not False or receipt.get("apply_allowed") is not False:
        raise ShadowIntegrationError("history_return_dedup_effect_boundary_breached")
    if receipt.get("semantic_acceptance") != "NOT_PERFORMED":
        raise ShadowIntegrationError("history_return_semantic_acceptance_overclaim")
    if receipt.get("execution_authority") != "NONE":
        raise ShadowIntegrationError("history_return_dedup_authority_breached")
    return receipt_sha


def build_history_replay_verification(
    trade_case: Mapping[str, Any],
    admission_candidate: Mapping[str, Any],
    append_candidates: Sequence[Mapping[str, Any]],
    return_dedup_candidate: Mapping[str, Any],
    *,
    expected_initial_ledger_sha256: str,
    expected_initial_head_event_sha256: str,
    expected_final_ledger_sha256: str,
    expected_final_head_event_sha256: str,
    expected_transaction_sha256: str,
    generated_at: str,
) -> dict[str, Any]:
    """Verify one complete P0 history across ContinuityOS and Return Broker without applying it."""
    case = validate_trade_case(trade_case)
    admission_sha, case_binding = _verify_admission(case, admission_candidate)
    if isinstance(append_candidates, (str, bytes)) or not isinstance(append_candidates, Sequence):
        raise ShadowIntegrationError("history_append_candidates_invalid")
    if not append_candidates:
        raise ShadowIntegrationError("history_append_candidates_empty")

    current_ledger_sha = _sha(expected_initial_ledger_sha256, "expected_initial_ledger_sha256")
    current_head = _sha(expected_initial_head_event_sha256, "expected_initial_head_event_sha256")
    ledger_id = _text(admission_candidate.get("ledger_id"), "admission.ledger_id")
    seen_types: list[str] = []
    seen_idempotency: set[str] = set()
    seen_event_hashes: set[str] = set()
    last_order = 0
    final_ledger: Mapping[str, Any] | None = None
    append_hashes: list[str] = []
    return_intake_sha: str | None = None

    for offset, candidate in enumerate(append_candidates, start=1):
        if not isinstance(candidate, Mapping) or candidate.get("schema") != APPEND_SCHEMA:
            raise ShadowIntegrationError("history_wrong_append_schema")
        _safe(candidate, f"append_{offset}")
        append_sha = _verify_hash(candidate, "append_candidate_sha256", "history_append_hash_mismatch")
        append_hashes.append(append_sha)
        if candidate.get("prior_ledger_sha256") != current_ledger_sha:
            raise ShadowIntegrationError("history_prior_ledger_fork_detected")
        if candidate.get("prior_head_event_sha256") != current_head:
            raise ShadowIntegrationError("history_prior_head_fork_detected")
        if candidate.get("ledger_write_performed") is not False or candidate.get("apply_allowed") is not False:
            raise ShadowIntegrationError("history_append_effect_boundary_breached")
        if candidate.get("execution_authority") != "NONE":
            raise ShadowIntegrationError("history_append_authority_breached")

        event = candidate.get("event")
        if not isinstance(event, Mapping) or event.get("schema") != EVENT_SCHEMA:
            raise ShadowIntegrationError("history_wrong_event_schema")
        _safe(event, f"event_{offset}")
        event_sha = _verify_hash(event, "event_sha256", "history_event_hash_mismatch")
        if event_sha in seen_event_hashes:
            raise ShadowIntegrationError("history_duplicate_event_hash")
        seen_event_hashes.add(event_sha)
        if event.get("ledger_id") != ledger_id or event.get("case_id") != case["case_id"]:
            raise ShadowIntegrationError("history_event_identity_mismatch")
        if event.get("case_sha256") != case["case_sha256"] or event.get("case_binding_sha256") != case_binding:
            raise ShadowIntegrationError("history_event_case_binding_mismatch")
        if event.get("sequence") != offset:
            raise ShadowIntegrationError("history_event_sequence_mismatch")
        if event.get("previous_event_sha256") != current_head:
            raise ShadowIntegrationError("history_event_previous_head_mismatch")
        event_type = _text(event.get("event_type"), "event_type")
        if event_type not in _EVENT_ORDER:
            raise ShadowIntegrationError("history_event_type_unsupported")
        order = _EVENT_ORDER[event_type]
        if order <= last_order:
            raise ShadowIntegrationError("history_event_order_regression")
        if event_type in seen_types:
            if event_type == "HUMAN_REVEAL":
                raise ShadowIntegrationError("history_one_case_one_reveal_violation")
            if event_type == "RETURN_INTAKE":
                raise ShadowIntegrationError("history_duplicate_return_event")
            raise ShadowIntegrationError("history_duplicate_event_type")
        idem = _text(event.get("idempotency_key"), "idempotency_key")
        if idem in seen_idempotency:
            raise ShadowIntegrationError("history_duplicate_idempotency_key")
        seen_idempotency.add(idem)
        if event.get("write_allowed") is not False or event.get("apply_allowed") is not False:
            raise ShadowIntegrationError("history_event_effect_boundary_breached")
        if event_type == "RETURN_INTAKE":
            return_intake_sha = _sha(event.get("subject_sha256"), "return_intake.subject_sha256")
        else:
            _sha(event.get("subject_sha256"), "event.subject_sha256")

        next_ledger = candidate.get("next_ledger_candidate")
        if not isinstance(next_ledger, Mapping) or next_ledger.get("schema") != LEDGER_SCHEMA:
            raise ShadowIntegrationError("history_wrong_next_ledger_schema")
        _safe(next_ledger, f"next_ledger_{offset}")
        next_sha = _verify_hash(next_ledger, "ledger_sha256", "history_next_ledger_hash_mismatch")
        if next_ledger.get("ledger_id") != ledger_id or next_ledger.get("case_id") != case["case_id"]:
            raise ShadowIntegrationError("history_next_ledger_identity_mismatch")
        if next_ledger.get("case_sha256") != case["case_sha256"] or next_ledger.get("case_binding_sha256") != case_binding:
            raise ShadowIntegrationError("history_next_ledger_case_binding_mismatch")
        if next_ledger.get("event_count") != offset or next_ledger.get("head_event_sha256") != event_sha:
            raise ShadowIntegrationError("history_next_ledger_head_mismatch")
        events = next_ledger.get("events")
        if not isinstance(events, (list, tuple)) or len(events) != offset:
            raise ShadowIntegrationError("history_next_ledger_event_count_mismatch")
        if events[-1].get("event_sha256") != event_sha:
            raise ShadowIntegrationError("history_next_ledger_last_event_mismatch")
        if final_ledger is None:
            if tuple(events[:-1]) != ():
                raise ShadowIntegrationError("history_initial_ledger_prefix_not_empty")
        elif tuple(events[:-1]) != tuple(final_ledger.get("events") or ()):
            raise ShadowIntegrationError("history_ledger_prefix_rewrite_detected")
        expected_reveals = sum(1 for item in [*seen_types, event_type] if item == "HUMAN_REVEAL")
        expected_outcomes = sum(1 for item in [*seen_types, event_type] if item == "OUTCOME_RECEIPT")
        expected_returns = sum(1 for item in [*seen_types, event_type] if item == "RETURN_INTAKE")
        if next_ledger.get("human_reveal_count") != expected_reveals:
            raise ShadowIntegrationError("history_next_ledger_reveal_count_mismatch")
        if next_ledger.get("outcome_count") != expected_outcomes:
            raise ShadowIntegrationError("history_next_ledger_outcome_count_mismatch")
        if next_ledger.get("return_intake_count") != expected_returns:
            raise ShadowIntegrationError("history_next_ledger_return_count_mismatch")
        if next_ledger.get("write_allowed") is not False or next_ledger.get("apply_allowed") is not False:
            raise ShadowIntegrationError("history_next_ledger_effect_boundary_breached")

        current_ledger_sha = next_sha
        current_head = event_sha
        final_ledger = next_ledger
        seen_types.append(event_type)
        last_order = order

    if tuple(seen_types) != _REQUIRED_COMPLETE_HISTORY:
        raise ShadowIntegrationError("history_incomplete_or_reordered")
    if current_ledger_sha != _sha(expected_final_ledger_sha256, "expected_final_ledger_sha256"):
        raise ShadowIntegrationError("history_final_ledger_external_mismatch")
    if current_head != _sha(expected_final_head_event_sha256, "expected_final_head_event_sha256"):
        raise ShadowIntegrationError("history_final_head_external_mismatch")
    if return_intake_sha is None:
        raise ShadowIntegrationError("history_return_intake_missing")
    return_dedup_sha = _verify_return_dedup(
        return_dedup_candidate,
        expected_transaction_sha256=expected_transaction_sha256,
        expected_intake_sha256=return_intake_sha,
    )

    body = {
        "schema": HISTORY_VERIFICATION_SCHEMA,
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "case_binding_sha256": case_binding,
        "admission_candidate_sha256": admission_sha,
        "append_candidate_sha256s": tuple(append_hashes),
        "final_ledger_sha256": current_ledger_sha,
        "final_head_event_sha256": current_head,
        "return_dedup_candidate_sha256": return_dedup_sha,
        "event_types": tuple(seen_types),
        "human_reveal_count": final_ledger.get("human_reveal_count") if final_ledger else None,
        "return_intake_count": final_ledger.get("return_intake_count") if final_ledger else None,
        "status": "HISTORY_CHAIN_VERIFIED_SHADOW_ONLY",
        "history_write_performed": False,
        "semantic_acceptance": "NOT_PERFORMED",
        "effects": dict(_NO_EFFECTS),
        "execution_authority": "NONE",
        "safety": dict(SHADOW_SAFETY),
        "generated_at": _text(generated_at, "generated_at"),
    }
    if body["human_reveal_count"] != 1:
        raise ShadowIntegrationError("history_one_case_one_reveal_count_invalid")
    if body["return_intake_count"] != 1:
        raise ShadowIntegrationError("history_return_intake_count_invalid")
    body["history_verification_sha256"] = sha256_obj(body)
    return body

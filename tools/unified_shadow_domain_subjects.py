#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from tools.tradingos_shadow_integration import (
    DECISION_PACKET_SCHEMA,
    OUTCOME_RECEIPT_SCHEMA,
    SCT_PREDICTION_SCHEMA,
    SHADOW_SAFETY,
    ShadowIntegrationError,
    sha256_obj,
    validate_trade_case,
)
from tools.unified_shadow_history_replay import HISTORY_VERIFICATION_SCHEMA
from tools.unified_shadow_temporal_anchor import TRUSTED_REPLAY_INPUT_SCHEMA

HUMAN_REVEAL_SCHEMA = "tradingos.shadow_human_reveal_receipt.v1"
SUBJECT_MANIFEST_SCHEMA = "tradingos.shadow_domain_subject_manifest.v1"
DOMAIN_HISTORY_VERIFICATION_SCHEMA = "bitevo.shadow_domain_history_verification.v1"
RETURN_INTAKE_SCHEMA = "control_return_broker.shadow_intake_receipt.v1"

_EVENT_ORDER = (
    "CASE_QUALIFIED",
    "TWIN_COMMITTED",
    "DECISION_PACKET",
    "HUMAN_REVEAL",
    "OUTCOME_RECEIPT",
    "RETURN_INTAKE",
)

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

_REPLAY_NO_EFFECTS = {
    "current_truth_apply": False,
    "continuity_write": False,
    "return_write": False,
    "archive_write": False,
    "runtime_activation": False,
    "model_call": False,
    "exchange_call": False,
    "signal": False,
    "order": False,
    "capital_effect": False,
}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShadowIntegrationError(f"domain_subject_{field}_required")
    return value.strip()


def _sha(value: Any, field: str) -> str:
    text = _text(value, field).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ShadowIntegrationError(f"domain_subject_{field}_must_be_sha256")
    return text


def _iso_epoch(value: Any, field: str) -> tuple[str, float]:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise ShadowIntegrationError(f"domain_subject_{field}_must_be_iso8601") from exc
    if parsed.tzinfo is None:
        raise ShadowIntegrationError(f"domain_subject_{field}_timezone_required")
    return text, parsed.timestamp()


def _verify_safety(record: Mapping[str, Any], field: str) -> None:
    safety = record.get("safety") if isinstance(record, Mapping) else None
    if not isinstance(safety, Mapping):
        raise ShadowIntegrationError(f"domain_subject_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"domain_subject_unsafe_{field}:{key}")


def _verify_hash(record: Mapping[str, Any], hash_field: str, code: str) -> str:
    if not isinstance(record, Mapping):
        raise ShadowIntegrationError(code)
    supplied = _sha(record.get(hash_field), hash_field)
    expected = sha256_obj({k: v for k, v in record.items() if k != hash_field})
    if supplied != expected:
        raise ShadowIntegrationError(code)
    return supplied


def _verify_false_map(value: Any, expected_keys: set[str], code: str) -> None:
    if not isinstance(value, Mapping) or set(value) != expected_keys:
        raise ShadowIntegrationError(code)
    if any(item is not False for item in value.values()):
        raise ShadowIntegrationError(code)


def _verify_trusted_replay_input(case: Mapping[str, Any], replay_input: Mapping[str, Any]) -> str:
    if not isinstance(replay_input, Mapping) or replay_input.get("schema") != TRUSTED_REPLAY_INPUT_SCHEMA:
        raise ShadowIntegrationError("domain_subject_wrong_replay_input_schema")
    replay_sha = _verify_hash(replay_input, "replay_input_sha256", "domain_subject_replay_input_hash_mismatch")
    if replay_input.get("case_id") != case["case_id"] or replay_input.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("domain_subject_replay_input_case_mismatch")
    if replay_input.get("trade_case") != case:
        raise ShadowIntegrationError("domain_subject_replay_input_trade_case_mismatch")
    if replay_input.get("replay_mode") != "OFFLINE_TRUSTED_REPLAY_ONLY":
        raise ShadowIntegrationError("domain_subject_replay_mode_invalid")
    if replay_input.get("external_expected_reference_consumed") is not True:
        raise ShadowIntegrationError("domain_subject_external_reference_not_consumed")
    if replay_input.get("source_authenticity_created_here") is not False:
        raise ShadowIntegrationError("domain_subject_replay_authenticity_overclaim")
    if replay_input.get("execution_authority") != "NONE" or replay_input.get("can_execute") is not False:
        raise ShadowIntegrationError("domain_subject_replay_authority_breached")
    _verify_safety(replay_input, "replay_input")
    _verify_false_map(replay_input.get("effects"), set(_REPLAY_NO_EFFECTS), "domain_subject_replay_effect_boundary_breached")
    qualification = replay_input.get("qualification")
    if not isinstance(qualification, Mapping):
        raise ShadowIntegrationError("domain_subject_replay_qualification_missing")
    qualification_sha = _verify_hash(
        qualification,
        "qualification_sha256",
        "domain_subject_replay_qualification_hash_mismatch",
    )
    if replay_input.get("qualification_sha256") != qualification_sha:
        raise ShadowIntegrationError("domain_subject_replay_qualification_binding_mismatch")
    return replay_sha


def _verify_twin(case: Mapping[str, Any], twin: Mapping[str, Any]) -> str:
    if not isinstance(twin, Mapping) or twin.get("schema") != SCT_PREDICTION_SCHEMA:
        raise ShadowIntegrationError("domain_subject_wrong_twin_schema")
    if twin.get("case_id") != case["case_id"] or twin.get("arm") != "sct":
        raise ShadowIntegrationError("domain_subject_twin_case_or_arm_mismatch")
    if tuple(twin.get("options") or ()) != tuple(case["options"]):
        raise ShadowIntegrationError("domain_subject_twin_options_mismatch")
    if twin.get("execution_authority") != "NONE" or twin.get("can_execute") is not False:
        raise ShadowIntegrationError("domain_subject_twin_authority_breached")
    base_keys = (
        "schema",
        "case_id",
        "arm",
        "options",
        "option_probabilities",
        "predicted_choice",
        "confidence",
        "reasons",
        "change_conditions",
        "would_escalate",
        "committed_at",
        "execution_authority",
        "can_execute",
    )
    if any(key not in twin for key in base_keys):
        raise ShadowIntegrationError("domain_subject_twin_hash_basis_incomplete")
    basis = {key: twin[key] for key in base_keys}
    expected = sha256_obj(basis)
    if twin.get("prediction_id") != expected:
        raise ShadowIntegrationError("domain_subject_twin_prediction_hash_mismatch")
    committed_at = twin.get("committed_at")
    if isinstance(committed_at, bool) or not isinstance(committed_at, (int, float)):
        raise ShadowIntegrationError("domain_subject_twin_committed_at_invalid")
    _, frozen_epoch = _iso_epoch(case["frozen_at"], "case.frozen_at")
    if float(committed_at) + 1e-6 < frozen_epoch:
        raise ShadowIntegrationError("domain_subject_twin_precedes_case_freeze")
    return str(twin["prediction_id"])


def _verify_packet(case: Mapping[str, Any], packet: Mapping[str, Any], twin_sha: str) -> str:
    if not isinstance(packet, Mapping) or packet.get("schema") != DECISION_PACKET_SCHEMA:
        raise ShadowIntegrationError("domain_subject_wrong_packet_schema")
    packet_sha = _verify_hash(packet, "packet_sha256", "domain_subject_packet_hash_mismatch")
    _verify_safety(packet, "packet")
    if packet.get("case_id") != case["case_id"] or packet.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("domain_subject_packet_case_mismatch")
    if tuple(packet.get("options") or ()) != tuple(case["options"]):
        raise ShadowIntegrationError("domain_subject_packet_options_mismatch")
    nested_twin = packet.get("twin")
    if not isinstance(nested_twin, Mapping) or nested_twin.get("prediction_id") != twin_sha:
        raise ShadowIntegrationError("domain_subject_packet_twin_binding_mismatch")
    if packet.get("human_decision_status") != "UNREVEALED":
        raise ShadowIntegrationError("domain_subject_packet_must_be_unrevealed")
    return packet_sha


def build_human_reveal_receipt(
    trade_case: Mapping[str, Any],
    decision_packet: Mapping[str, Any],
    *,
    actual_choice: str,
    decided_at: str,
) -> dict[str, Any]:
    case = validate_trade_case(trade_case)
    packet_twin = decision_packet.get("twin") if isinstance(decision_packet, Mapping) else None
    if not isinstance(packet_twin, Mapping):
        raise ShadowIntegrationError("domain_subject_packet_twin_missing")
    twin_sha = _sha(packet_twin.get("prediction_id"), "packet.twin.prediction_id")
    packet_sha = _verify_packet(case, decision_packet, twin_sha)
    choice = _text(actual_choice, "actual_choice").upper()
    if choice not in case["options"]:
        raise ShadowIntegrationError("domain_subject_reveal_choice_outside_options")
    decided_text, decided_epoch = _iso_epoch(decided_at, "decided_at")
    _, frozen_epoch = _iso_epoch(case["frozen_at"], "case.frozen_at")
    committed_at = packet_twin.get("committed_at")
    if isinstance(committed_at, bool) or not isinstance(committed_at, (int, float)):
        raise ShadowIntegrationError("domain_subject_packet_twin_committed_at_invalid")
    if decided_epoch + 1e-6 < frozen_epoch or decided_epoch + 1e-6 < float(committed_at):
        raise ShadowIntegrationError("domain_subject_reveal_precedes_freeze_or_twin_commit")
    body = {
        "schema": HUMAN_REVEAL_SCHEMA,
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "packet_sha256": packet_sha,
        "twin_prediction_id": twin_sha,
        "actual_choice": choice,
        "decided_at": decided_text,
        "human_decision_status": "REVEALED",
        "write_performed": False,
        "apply_allowed": False,
        "execution_authority": "NONE",
        "can_execute": False,
        "safety": dict(SHADOW_SAFETY),
    }
    body["reveal_sha256"] = sha256_obj(body)
    return body


def _verify_reveal(
    case: Mapping[str, Any],
    packet: Mapping[str, Any],
    twin_sha: str,
    reveal: Mapping[str, Any],
) -> str:
    if not isinstance(reveal, Mapping) or reveal.get("schema") != HUMAN_REVEAL_SCHEMA:
        raise ShadowIntegrationError("domain_subject_wrong_reveal_schema")
    reveal_sha = _verify_hash(reveal, "reveal_sha256", "domain_subject_reveal_hash_mismatch")
    _verify_safety(reveal, "reveal")
    if reveal.get("case_id") != case["case_id"] or reveal.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("domain_subject_reveal_case_mismatch")
    if reveal.get("packet_sha256") != packet["packet_sha256"] or reveal.get("twin_prediction_id") != twin_sha:
        raise ShadowIntegrationError("domain_subject_reveal_upstream_binding_mismatch")
    if reveal.get("actual_choice") not in case["options"] or reveal.get("human_decision_status") != "REVEALED":
        raise ShadowIntegrationError("domain_subject_reveal_choice_or_status_invalid")
    if reveal.get("write_performed") is not False or reveal.get("apply_allowed") is not False:
        raise ShadowIntegrationError("domain_subject_reveal_effect_boundary_breached")
    if reveal.get("execution_authority") != "NONE" or reveal.get("can_execute") is not False:
        raise ShadowIntegrationError("domain_subject_reveal_authority_breached")
    _, decided_epoch = _iso_epoch(reveal.get("decided_at"), "reveal.decided_at")
    _, frozen_epoch = _iso_epoch(case["frozen_at"], "case.frozen_at")
    if decided_epoch + 1e-6 < frozen_epoch or decided_epoch + 1e-6 < float(packet["twin"]["committed_at"]):
        raise ShadowIntegrationError("domain_subject_reveal_temporal_violation")
    return reveal_sha


def _verify_outcome(
    case: Mapping[str, Any],
    packet: Mapping[str, Any],
    reveal: Mapping[str, Any],
    outcome: Mapping[str, Any],
) -> str:
    if not isinstance(outcome, Mapping) or outcome.get("schema") != OUTCOME_RECEIPT_SCHEMA:
        raise ShadowIntegrationError("domain_subject_wrong_outcome_schema")
    outcome_sha = _verify_hash(outcome, "receipt_sha256", "domain_subject_outcome_hash_mismatch")
    _verify_safety(outcome, "outcome")
    if outcome.get("case_id") != case["case_id"] or outcome.get("packet_sha256") != packet["packet_sha256"]:
        raise ShadowIntegrationError("domain_subject_outcome_case_or_packet_mismatch")
    if outcome.get("actual_choice") != reveal["actual_choice"]:
        raise ShadowIntegrationError("domain_subject_outcome_reveal_choice_mismatch")
    if outcome.get("decided_at") != reveal["decided_at"]:
        raise ShadowIntegrationError("domain_subject_outcome_reveal_time_mismatch")
    _iso_epoch(outcome.get("decided_at"), "outcome.decided_at")
    return outcome_sha


def _verify_return_intake(intake: Mapping[str, Any], expected_transaction_sha256: str) -> str:
    if not isinstance(intake, Mapping) or intake.get("schema") != RETURN_INTAKE_SCHEMA:
        raise ShadowIntegrationError("domain_subject_wrong_return_intake_schema")
    intake_sha = _verify_hash(intake, "shadow_intake_sha256", "domain_subject_return_intake_hash_mismatch")
    _verify_safety(intake, "return_intake")
    if intake.get("source_transaction_sha256") != _sha(expected_transaction_sha256, "expected_transaction_sha256"):
        raise ShadowIntegrationError("domain_subject_return_transaction_mismatch")
    if intake.get("physical_status") != "VERIFIED_READ_ONLY":
        raise ShadowIntegrationError("domain_subject_return_physical_status_invalid")
    physical = intake.get("physical_verification")
    if not isinstance(physical, Mapping) or physical.get("passed") is not True:
        raise ShadowIntegrationError("domain_subject_return_physical_pass_missing")
    transport = intake.get("transport")
    if not isinstance(transport, Mapping) or any(value is not False for value in transport.values()):
        raise ShadowIntegrationError("domain_subject_return_transport_effect_breached")
    if intake.get("semantic_acceptance") != "NOT_PERFORMED" or intake.get("content_acceptance_claimed") is not False:
        raise ShadowIntegrationError("domain_subject_return_semantic_acceptance_overclaim")
    authority = intake.get("authority")
    if not isinstance(authority, Mapping) or authority.get("execution_authority") != "NONE" or authority.get("apply_authorized") is not False:
        raise ShadowIntegrationError("domain_subject_return_authority_breached")
    return intake_sha


def build_domain_subject_manifest(
    trade_case: Mapping[str, Any],
    trusted_replay_input: Mapping[str, Any],
    twin_prediction: Mapping[str, Any],
    decision_packet: Mapping[str, Any],
    human_reveal_receipt: Mapping[str, Any],
    outcome_receipt: Mapping[str, Any],
    return_intake_receipt: Mapping[str, Any],
    *,
    expected_transaction_sha256: str,
) -> dict[str, Any]:
    case = validate_trade_case(trade_case)
    replay_sha = _verify_trusted_replay_input(case, trusted_replay_input)
    twin_sha = _verify_twin(case, twin_prediction)
    packet_sha = _verify_packet(case, decision_packet, twin_sha)
    reveal_sha = _verify_reveal(case, decision_packet, twin_sha, human_reveal_receipt)
    outcome_sha = _verify_outcome(case, decision_packet, human_reveal_receipt, outcome_receipt)
    intake_sha = _verify_return_intake(return_intake_receipt, expected_transaction_sha256)

    subjects = (
        {"event_type": "CASE_QUALIFIED", "subject_sha256": replay_sha, "subject_schema": TRUSTED_REPLAY_INPUT_SCHEMA},
        {"event_type": "TWIN_COMMITTED", "subject_sha256": twin_sha, "subject_schema": SCT_PREDICTION_SCHEMA},
        {"event_type": "DECISION_PACKET", "subject_sha256": packet_sha, "subject_schema": DECISION_PACKET_SCHEMA},
        {"event_type": "HUMAN_REVEAL", "subject_sha256": reveal_sha, "subject_schema": HUMAN_REVEAL_SCHEMA},
        {"event_type": "OUTCOME_RECEIPT", "subject_sha256": outcome_sha, "subject_schema": OUTCOME_RECEIPT_SCHEMA},
        {"event_type": "RETURN_INTAKE", "subject_sha256": intake_sha, "subject_schema": RETURN_INTAKE_SCHEMA},
    )
    body = {
        "schema": SUBJECT_MANIFEST_SCHEMA,
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "expected_transaction_sha256": _sha(expected_transaction_sha256, "expected_transaction_sha256"),
        "event_order": _EVENT_ORDER,
        "subjects": subjects,
        "subject_binding_complete": True,
        "semantic_acceptance": "NOT_PERFORMED",
        "effects": dict(_NO_EFFECTS),
        "execution_authority": "NONE",
        "safety": dict(SHADOW_SAFETY),
    }
    body["subject_manifest_sha256"] = sha256_obj(body)
    return body


def build_domain_history_verification(
    trade_case: Mapping[str, Any],
    history_verification: Mapping[str, Any],
    append_candidates: Sequence[Mapping[str, Any]],
    subject_manifest: Mapping[str, Any],
    *,
    expected_history_verification_sha256: str,
    generated_at: str,
) -> dict[str, Any]:
    case = validate_trade_case(trade_case)
    if not isinstance(subject_manifest, Mapping) or subject_manifest.get("schema") != SUBJECT_MANIFEST_SCHEMA:
        raise ShadowIntegrationError("domain_subject_wrong_manifest_schema")
    manifest_sha = _verify_hash(subject_manifest, "subject_manifest_sha256", "domain_subject_manifest_hash_mismatch")
    _verify_safety(subject_manifest, "manifest")
    _verify_false_map(subject_manifest.get("effects"), set(_NO_EFFECTS), "domain_subject_manifest_effect_boundary_breached")
    if subject_manifest.get("case_id") != case["case_id"] or subject_manifest.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("domain_subject_manifest_case_mismatch")
    if tuple(subject_manifest.get("event_order") or ()) != _EVENT_ORDER or subject_manifest.get("subject_binding_complete") is not True:
        raise ShadowIntegrationError("domain_subject_manifest_incomplete")
    if subject_manifest.get("semantic_acceptance") != "NOT_PERFORMED" or subject_manifest.get("execution_authority") != "NONE":
        raise ShadowIntegrationError("domain_subject_manifest_authority_or_acceptance_overclaim")

    if not isinstance(history_verification, Mapping) or history_verification.get("schema") != HISTORY_VERIFICATION_SCHEMA:
        raise ShadowIntegrationError("domain_subject_wrong_history_schema")
    history_sha = _verify_hash(
        history_verification,
        "history_verification_sha256",
        "domain_subject_history_hash_mismatch",
    )
    if history_sha != _sha(expected_history_verification_sha256, "expected_history_verification_sha256"):
        raise ShadowIntegrationError("domain_subject_history_external_digest_mismatch")
    _verify_safety(history_verification, "history")
    _verify_false_map(history_verification.get("effects"), set(_NO_EFFECTS), "domain_subject_history_effect_boundary_breached")
    if history_verification.get("case_id") != case["case_id"] or history_verification.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("domain_subject_history_case_mismatch")
    if history_verification.get("status") != "HISTORY_CHAIN_VERIFIED_SHADOW_ONLY":
        raise ShadowIntegrationError("domain_subject_history_status_invalid")
    if history_verification.get("history_write_performed") is not False or history_verification.get("semantic_acceptance") != "NOT_PERFORMED":
        raise ShadowIntegrationError("domain_subject_history_effect_or_acceptance_breached")
    if history_verification.get("execution_authority") != "NONE":
        raise ShadowIntegrationError("domain_subject_history_authority_breached")
    if tuple(history_verification.get("event_types") or ()) != _EVENT_ORDER:
        raise ShadowIntegrationError("domain_subject_history_event_order_mismatch")

    if isinstance(append_candidates, (str, bytes)) or not isinstance(append_candidates, Sequence):
        raise ShadowIntegrationError("domain_subject_append_candidates_invalid")
    if len(append_candidates) != len(_EVENT_ORDER):
        raise ShadowIntegrationError("domain_subject_append_candidate_count_mismatch")
    expected_append_hashes = tuple(history_verification.get("append_candidate_sha256s") or ())
    if len(expected_append_hashes) != len(_EVENT_ORDER):
        raise ShadowIntegrationError("domain_subject_history_append_hash_count_mismatch")

    subjects = tuple(subject_manifest.get("subjects") or ())
    if len(subjects) != len(_EVENT_ORDER):
        raise ShadowIntegrationError("domain_subject_manifest_subject_count_mismatch")
    bound_rows: list[dict[str, str]] = []
    for index, (event_type, candidate, expected_subject, expected_append_sha) in enumerate(
        zip(_EVENT_ORDER, append_candidates, subjects, expected_append_hashes),
        start=1,
    ):
        if not isinstance(candidate, Mapping):
            raise ShadowIntegrationError("domain_subject_append_candidate_invalid")
        candidate_sha = _verify_hash(candidate, "append_candidate_sha256", "domain_subject_append_candidate_hash_mismatch")
        if candidate_sha != expected_append_sha:
            raise ShadowIntegrationError("domain_subject_append_candidate_history_binding_mismatch")
        event = candidate.get("event")
        if not isinstance(event, Mapping):
            raise ShadowIntegrationError("domain_subject_event_missing")
        _verify_hash(event, "event_sha256", "domain_subject_event_hash_mismatch")
        if event.get("sequence") != index or event.get("event_type") != event_type:
            raise ShadowIntegrationError("domain_subject_event_sequence_or_type_mismatch")
        if not isinstance(expected_subject, Mapping) or expected_subject.get("event_type") != event_type:
            raise ShadowIntegrationError("domain_subject_manifest_event_type_mismatch")
        expected_subject_sha = _sha(expected_subject.get("subject_sha256"), f"manifest.{event_type}.subject_sha256")
        if event.get("subject_sha256") != expected_subject_sha:
            raise ShadowIntegrationError(f"domain_subject_event_subject_mismatch:{event_type}")
        bound_rows.append(
            {
                "event_type": event_type,
                "subject_sha256": expected_subject_sha,
                "subject_schema": _text(expected_subject.get("subject_schema"), f"manifest.{event_type}.subject_schema"),
            }
        )

    body = {
        "schema": DOMAIN_HISTORY_VERIFICATION_SCHEMA,
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "history_verification_sha256": history_sha,
        "subject_manifest_sha256": manifest_sha,
        "bound_subjects": tuple(bound_rows),
        "subject_binding_complete": True,
        "status": "DOMAIN_SUBJECTS_BOUND_SHADOW_ONLY",
        "history_write_performed": False,
        "semantic_acceptance": "NOT_PERFORMED",
        "effects": dict(_NO_EFFECTS),
        "execution_authority": "NONE",
        "safety": dict(SHADOW_SAFETY),
        "generated_at": _text(generated_at, "generated_at"),
    }
    body["domain_history_verification_sha256"] = sha256_obj(body)
    return body

"""TradingOS R82 deterministic human-review attestation contract."""
from __future__ import annotations

import hashlib
from typing import Any

from tools import tradingos_shadow_evaluation_common as r81c
from tools.tradingos_human_review_common import *
from tools.tradingos_human_review_common import _ID24_RE, _SHA64_RE

R81_REPORT_KEYS = {
    "schema",
    "report_id",
    "frozen_set_declaration_id",
    "frozen_set_declaration_sha256",
    "memory_policy_sha256",
    "shadow_policy_sha256",
    "record_count",
    "claim_count",
    "counts_by_outcome",
    "counts_by_claim_kind",
    "integrity",
    "report_mode",
    "shadow_only",
    "memory_write_authority",
    "auto_learning_allowed",
    "live_decision_feedback_allowed",
    "live_decision_use_allowed",
    "model_selection_use_allowed",
    "execution_authority",
    "can_trade",
    "capital_permission",
    "confers_authority",
}
R81_INTEGRITY_KEYS = {
    "all_records_valid",
    "duplicate_record_ids_absent",
    "frozen_set_exact",
    "mixed_memory_policy_absent",
    "record_payloads_bound",
}
REVIEW_INPUT_KEYS = {"disposition", "reason_codes"}
ATTESTATION_KEYS = {
    "schema",
    "attestation_id",
    "shadow_report_id",
    "shadow_report_sha256",
    "shadow_policy_sha256",
    "frozen_set_declaration_id",
    "review_policy_sha256",
    "review_origin",
    "disposition",
    "reason_codes",
    "shadow_only",
    "human_review_only",
    "report_consumption_authority",
    "memory_write_authority",
    "policy_update_allowed",
    "live_decision_feedback_allowed",
    "live_decision_use_allowed",
    "model_selection_use_allowed",
    "execution_authority",
    "can_trade",
    "capital_permission",
    "confers_authority",
}


def _r81_report_identity_payload(report: dict[str, Any]) -> dict[str, Any]:
    return {key: report[key] for key in R81_REPORT_KEYS if key != "report_id"}


def _expected_r81_report_id(report: dict[str, Any]) -> str:
    return hashlib.sha256(
        f"{r81c.REPORT_SCHEMA}:{r81c.VERSION}:".encode("utf-8")
        + r81c.stable_json_bytes(_r81_report_identity_payload(report))
    ).hexdigest()[:24]


def validate_r81_report_for_review(report: Any) -> None:
    if not isinstance(report, dict) or set(report) != R81_REPORT_KEYS:
        raise ValueError("R81 report key set mismatch")
    if report.get("schema") != r81c.REPORT_SCHEMA:
        raise ValueError("unsupported R81 report schema")
    rid = report.get("report_id")
    if not isinstance(rid, str) or _ID24_RE.fullmatch(rid) is None:
        raise ValueError("R81 report_id invalid")
    for field in (
        "frozen_set_declaration_sha256",
        "memory_policy_sha256",
        "shadow_policy_sha256",
    ):
        value = report.get(field)
        if not isinstance(value, str) or _SHA64_RE.fullmatch(value) is None:
            raise ValueError(f"R81 {field} invalid")
    did = report.get("frozen_set_declaration_id")
    if not isinstance(did, str) or _ID24_RE.fullmatch(did) is None:
        raise ValueError("R81 frozen_set_declaration_id invalid")
    for field in ("record_count", "claim_count"):
        value = report.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"R81 {field} invalid")
    outcomes = report.get("counts_by_outcome")
    if not isinstance(outcomes, dict) or set(outcomes) != set(r81c.OUTCOMES):
        raise ValueError("R81 outcome counts key set mismatch")
    kinds = report.get("counts_by_claim_kind")
    if not isinstance(kinds, dict) or set(kinds) != set(r81c.CLAIM_KINDS):
        raise ValueError("R81 claim-kind counts key set mismatch")
    for counts in (outcomes, kinds):
        for value in counts.values():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("R81 count invalid")
    if sum(outcomes.values()) != report["claim_count"]:
        raise ValueError("R81 outcome claim_count mismatch")
    if sum(kinds.values()) != report["claim_count"]:
        raise ValueError("R81 claim-kind claim_count mismatch")
    integrity = report.get("integrity")
    if not isinstance(integrity, dict) or set(integrity) != R81_INTEGRITY_KEYS:
        raise ValueError("R81 integrity key set mismatch")
    if any(value is not True for value in integrity.values()):
        raise ValueError("R81 integrity must remain all true")
    if report.get("report_mode") != "COUNT_AND_INTEGRITY_ONLY":
        raise ValueError("R81 report mode drift")
    ceiling = {
        "shadow_only": True,
        "memory_write_authority": "NONE",
        "auto_learning_allowed": False,
        "live_decision_feedback_allowed": False,
        "live_decision_use_allowed": False,
        "model_selection_use_allowed": False,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }
    for key, expected in ceiling.items():
        if report.get(key) != expected:
            raise ValueError(f"unsafe R81 report: {key}")
    if report["report_id"] != _expected_r81_report_id(report):
        raise ValueError("R81 report_id binding mismatch")


def _validate_review_input(review_input: Any) -> tuple[str, list[str]]:
    if not isinstance(review_input, dict) or set(review_input) != REVIEW_INPUT_KEYS:
        raise ValueError("review input key set mismatch")
    disposition = review_input.get("disposition")
    if disposition not in DISPOSITIONS:
        raise ValueError("unsupported review disposition")
    reasons = review_input.get("reason_codes")
    if not isinstance(reasons, list) or not 1 <= len(reasons) <= 2:
        raise ValueError("reason_codes length invalid")
    if any(not isinstance(code, str) or code not in REASON_CODES for code in reasons):
        raise ValueError("unsupported review reason code")
    if len(reasons) != len(set(reasons)):
        raise ValueError("duplicate review reason code")
    canonical = sorted(reasons, key=REASON_ORDER.__getitem__)
    if reasons != canonical:
        raise ValueError("reason_codes must be canonical order")
    allowed_by_disposition = {
        "ACKNOWLEDGED": {"INTEGRITY_CONFIRMED", "COUNT_REVIEWED"},
        "DISPUTED": {"SOURCE_BINDING_CONCERN", "POLICY_BINDING_CONCERN"},
        "FOLLOWUP_REQUIRED": {
            "SOURCE_BINDING_CONCERN",
            "POLICY_BINDING_CONCERN",
            "INSUFFICIENT_CONTEXT",
        },
    }
    if any(code not in allowed_by_disposition[disposition] for code in reasons):
        raise ValueError("reason code incompatible with disposition")
    if disposition == "FOLLOWUP_REQUIRED" and "INSUFFICIENT_CONTEXT" not in reasons:
        raise ValueError("followup requires INSUFFICIENT_CONTEXT")
    return disposition, reasons


def _attestation_identity_payload(attestation: dict[str, Any]) -> dict[str, Any]:
    return {key: attestation[key] for key in ATTESTATION_KEYS if key != "attestation_id"}


def _expected_attestation_id(attestation: dict[str, Any]) -> str:
    return hashlib.sha256(
        f"{ATTESTATION_SCHEMA}:{VERSION}:".encode("utf-8")
        + stable_json_bytes(_attestation_identity_payload(attestation))
    ).hexdigest()[:24]


def build_human_review_attestation(
    report: dict[str, Any],
    review_input: dict[str, Any],
    review_policy: dict[str, Any],
) -> dict[str, Any]:
    validate_r81_report_for_review(report)
    validate_review_policy(review_policy)
    disposition, reasons = _validate_review_input(review_input)
    attestation = {
        "schema": ATTESTATION_SCHEMA,
        "shadow_report_id": report["report_id"],
        "shadow_report_sha256": stable_sha256(report),
        "shadow_policy_sha256": report["shadow_policy_sha256"],
        "frozen_set_declaration_id": report["frozen_set_declaration_id"],
        "review_policy_sha256": stable_sha256(review_policy),
        "review_origin": "UNVERIFIED_HUMAN_INPUT",
        "disposition": disposition,
        "reason_codes": reasons,
        "shadow_only": True,
        "human_review_only": True,
        "report_consumption_authority": "NONE",
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
    attestation["attestation_id"] = _expected_attestation_id(attestation)
    validate_human_review_attestation(attestation, report, review_policy)
    return attestation


def validate_human_review_attestation(
    attestation: Any,
    report: dict[str, Any],
    review_policy: dict[str, Any],
) -> None:
    validate_r81_report_for_review(report)
    validate_review_policy(review_policy)
    if not isinstance(attestation, dict) or set(attestation) != ATTESTATION_KEYS:
        raise ValueError("attestation key set mismatch")
    if attestation.get("schema") != ATTESTATION_SCHEMA:
        raise ValueError("unsupported attestation schema")
    aid = attestation.get("attestation_id")
    if not isinstance(aid, str) or _ID24_RE.fullmatch(aid) is None:
        raise ValueError("attestation_id invalid")
    if attestation.get("shadow_report_id") != report["report_id"]:
        raise ValueError("attestation report id mismatch")
    if attestation.get("shadow_report_sha256") != stable_sha256(report):
        raise ValueError("attestation report sha mismatch")
    if attestation.get("shadow_policy_sha256") != report["shadow_policy_sha256"]:
        raise ValueError("attestation shadow policy mismatch")
    if attestation.get("frozen_set_declaration_id") != report["frozen_set_declaration_id"]:
        raise ValueError("attestation declaration mismatch")
    if attestation.get("review_policy_sha256") != stable_sha256(review_policy):
        raise ValueError("attestation review policy mismatch")
    if attestation.get("review_origin") != "UNVERIFIED_HUMAN_INPUT":
        raise ValueError("review origin drift")
    _validate_review_input(
        {"disposition": attestation.get("disposition"), "reason_codes": attestation.get("reason_codes")}
    )
    ceiling = {
        "shadow_only": True,
        "human_review_only": True,
        "report_consumption_authority": "NONE",
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
    for key, expected in ceiling.items():
        if attestation.get(key) != expected:
            raise ValueError(f"unsafe attestation: {key}")
    if attestation["attestation_id"] != _expected_attestation_id(attestation):
        raise ValueError("attestation_id binding mismatch")

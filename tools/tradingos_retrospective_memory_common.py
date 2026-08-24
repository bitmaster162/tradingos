"""Shared deterministic helpers for TradingOS R80 retrospective memory."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

RECORD_SCHEMA = "tradingos.retrospective_record.v1"
SUMMARY_SCHEMA = "tradingos.retrospective_count_summary.v1"
POLICY_ID = "TRADINGOS_RETROSPECTIVE_MEMORY_POLICY_V1"
VERSION = "1.0.0"

_ID24_RE = re.compile(r"^[0-9a-f]{24}$")
_SHA64_RE = re.compile(r"^[0-9a-f]{64}$")

OUTCOMES = ("SUPPORTED", "CONTRADICTED", "UNRESOLVED", "NOT_EVALUABLE")
RATIONALE_CODES = (
    "EVIDENCE_MATCH",
    "EVIDENCE_CONFLICT",
    "INSUFFICIENT_EVIDENCE",
    "NOT_APPLICABLE",
)
CLAIM_KINDS = (
    "THESIS",
    "COUNTERTHESIS",
    "BLIND_SPOT",
    "PREMORTEM",
    "SCENARIO_READ",
    "INVALIDATION_READ",
    "OPERATOR_QUESTION",
)

OUTPUT_PERMISSIONS = {
    "execution_authority": "NONE",
    "signals_allowed": False,
    "orders_allowed": False,
    "can_trade": False,
    "capital_permission": "DENY",
    "confers_authority": False,
}

POLICY_KEYS = {
    "schema_version",
    "policy_id",
    "mode",
    "allowed_outcomes",
    "require_all_response_claims",
    "allow_extra_claim_ids",
    "external_sources_allowed",
    "persistence_in_core_allowed",
    "pnl_fields_allowed",
    "trading_performance_use_allowed",
    "probability_outputs_allowed",
    "rate_outputs_allowed",
    "auto_learning_allowed",
    "weight_update_allowed",
    "prompt_update_allowed",
    "model_selection_update_allowed",
    "policy_update_allowed",
    "live_decision_feedback_allowed",
    "calibration_mode",
    "memory_write_authority",
    "output_permissions",
}


def stable_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("value is not canonically serializable") from exc


def stable_sha256(value: Any) -> str:
    return hashlib.sha256(stable_json_bytes(value)).hexdigest()


def validate_memory_policy(policy: Any) -> None:
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        raise ValueError("memory policy key set mismatch")
    if policy.get("schema_version") != 1 or policy.get("policy_id") != POLICY_ID:
        raise ValueError("unsupported memory policy")
    if policy.get("mode") != "OFFLINE_RETROSPECTIVE_ONLY":
        raise ValueError("memory mode must remain offline retrospective")
    if policy.get("allowed_outcomes") != list(OUTCOMES):
        raise ValueError("outcome set mismatch")
    if policy.get("require_all_response_claims") is not True:
        raise ValueError("all response claims must be evaluated")
    if policy.get("allow_extra_claim_ids") is not False:
        raise ValueError("extra retrospective claim ids forbidden")
    for field in (
        "external_sources_allowed",
        "persistence_in_core_allowed",
        "pnl_fields_allowed",
        "trading_performance_use_allowed",
        "probability_outputs_allowed",
        "rate_outputs_allowed",
        "auto_learning_allowed",
        "weight_update_allowed",
        "prompt_update_allowed",
        "model_selection_update_allowed",
        "policy_update_allowed",
        "live_decision_feedback_allowed",
    ):
        if policy.get(field) is not False:
            raise ValueError(f"unsafe memory policy: {field}")
    if policy.get("calibration_mode") != "COUNT_ONLY":
        raise ValueError("R80 calibration must remain COUNT_ONLY")
    if policy.get("memory_write_authority") != "NONE":
        raise ValueError("memory write authority must remain NONE")
    if policy.get("output_permissions") != OUTPUT_PERMISSIONS:
        raise ValueError("unsafe memory output permissions")

"""Shared deterministic helpers for TradingOS R81 frozen-record shadow evaluation."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

DECLARATION_SCHEMA = "tradingos.shadow_frozen_set.v1"
REPORT_SCHEMA = "tradingos.shadow_evaluation_report.v1"
POLICY_ID = "TRADINGOS_SHADOW_EVALUATION_POLICY_V1"
VERSION = "1.0.0"

_ID24_RE = re.compile(r"^[0-9a-f]{24}$")
_SHA64_RE = re.compile(r"^[0-9a-f]{64}$")

OUTCOMES = ("SUPPORTED", "CONTRADICTED", "UNRESOLVED", "NOT_EVALUABLE")
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
    "input_record_schema",
    "require_frozen_set_declaration",
    "require_all_declared_records",
    "allow_subset_evaluation",
    "allow_duplicate_record_ids",
    "mixed_memory_policy_allowed",
    "external_sources_allowed",
    "persistence_in_core_allowed",
    "pnl_fields_allowed",
    "price_return_fields_allowed",
    "probability_outputs_allowed",
    "rate_outputs_allowed",
    "confidence_outputs_allowed",
    "model_ranking_allowed",
    "provider_ranking_allowed",
    "auto_learning_allowed",
    "weight_update_allowed",
    "prompt_update_allowed",
    "model_selection_update_allowed",
    "policy_update_allowed",
    "live_decision_feedback_allowed",
    "live_decision_use_allowed",
    "model_selection_use_allowed",
    "shadow_only",
    "report_mode",
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


def validate_shadow_policy(policy: Any) -> None:
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        raise ValueError("shadow policy key set mismatch")
    if policy.get("schema_version") != 1 or policy.get("policy_id") != POLICY_ID:
        raise ValueError("unsupported shadow policy")
    if policy.get("mode") != "OFFLINE_FROZEN_RECORD_SHADOW_ONLY":
        raise ValueError("shadow mode drift")
    if policy.get("input_record_schema") != "tradingos.retrospective_record.v1":
        raise ValueError("input record schema drift")
    required_true = (
        "require_frozen_set_declaration",
        "require_all_declared_records",
        "shadow_only",
    )
    for field in required_true:
        if policy.get(field) is not True:
            raise ValueError(f"required shadow guard disabled: {field}")
    required_false = (
        "allow_subset_evaluation",
        "allow_duplicate_record_ids",
        "mixed_memory_policy_allowed",
        "external_sources_allowed",
        "persistence_in_core_allowed",
        "pnl_fields_allowed",
        "price_return_fields_allowed",
        "probability_outputs_allowed",
        "rate_outputs_allowed",
        "confidence_outputs_allowed",
        "model_ranking_allowed",
        "provider_ranking_allowed",
        "auto_learning_allowed",
        "weight_update_allowed",
        "prompt_update_allowed",
        "model_selection_update_allowed",
        "policy_update_allowed",
        "live_decision_feedback_allowed",
        "live_decision_use_allowed",
        "model_selection_use_allowed",
    )
    for field in required_false:
        if policy.get(field) is not False:
            raise ValueError(f"unsafe shadow policy: {field}")
    if policy.get("report_mode") != "COUNT_AND_INTEGRITY_ONLY":
        raise ValueError("shadow report mode drift")
    if policy.get("memory_write_authority") != "NONE":
        raise ValueError("memory write authority must remain NONE")
    if policy.get("output_permissions") != OUTPUT_PERMISSIONS:
        raise ValueError("unsafe shadow output permissions")

"""Shared deterministic helpers for TradingOS R82 human-review attestation."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

ATTESTATION_SCHEMA = "tradingos.human_review_attestation.v1"
POLICY_ID = "TRADINGOS_HUMAN_REVIEW_POLICY_V1"
VERSION = "1.0.0"

_ID24_RE = re.compile(r"^[0-9a-f]{24}$")
_SHA64_RE = re.compile(r"^[0-9a-f]{64}$")

DISPOSITIONS = ("ACKNOWLEDGED", "DISPUTED", "FOLLOWUP_REQUIRED")
REASON_CODES = (
    "INTEGRITY_CONFIRMED",
    "COUNT_REVIEWED",
    "SOURCE_BINDING_CONCERN",
    "POLICY_BINDING_CONCERN",
    "INSUFFICIENT_CONTEXT",
)
REASON_ORDER = {code: i for i, code in enumerate(REASON_CODES)}

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
    "input_report_schema",
    "require_exact_report_binding",
    "allow_free_text",
    "recommendations_allowed",
    "probability_outputs_allowed",
    "rate_outputs_allowed",
    "confidence_outputs_allowed",
    "pnl_fields_allowed",
    "price_return_fields_allowed",
    "model_ranking_allowed",
    "provider_ranking_allowed",
    "reviewer_identity_storage_allowed",
    "external_sources_allowed",
    "persistence_in_core_allowed",
    "policy_update_allowed",
    "live_decision_feedback_allowed",
    "live_decision_use_allowed",
    "model_selection_use_allowed",
    "human_review_only",
    "shadow_only",
    "report_consumption_authority",
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


def validate_review_policy(policy: Any) -> None:
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        raise ValueError("review policy key set mismatch")
    if policy.get("schema_version") != 1 or policy.get("policy_id") != POLICY_ID:
        raise ValueError("unsupported review policy")
    if policy.get("mode") != "OFFLINE_HUMAN_REVIEW_ATTESTATION_ONLY":
        raise ValueError("review mode drift")
    if policy.get("input_report_schema") != "tradingos.shadow_evaluation_report.v1":
        raise ValueError("input report schema drift")
    for field in ("require_exact_report_binding", "human_review_only", "shadow_only"):
        if policy.get(field) is not True:
            raise ValueError(f"required review guard disabled: {field}")
    for field in (
        "allow_free_text",
        "recommendations_allowed",
        "probability_outputs_allowed",
        "rate_outputs_allowed",
        "confidence_outputs_allowed",
        "pnl_fields_allowed",
        "price_return_fields_allowed",
        "model_ranking_allowed",
        "provider_ranking_allowed",
        "reviewer_identity_storage_allowed",
        "external_sources_allowed",
        "persistence_in_core_allowed",
        "policy_update_allowed",
        "live_decision_feedback_allowed",
        "live_decision_use_allowed",
        "model_selection_use_allowed",
    ):
        if policy.get(field) is not False:
            raise ValueError(f"unsafe review policy: {field}")
    if policy.get("report_consumption_authority") != "NONE":
        raise ValueError("report consumption authority must remain NONE")
    if policy.get("memory_write_authority") != "NONE":
        raise ValueError("memory write authority must remain NONE")
    if policy.get("output_permissions") != OUTPUT_PERMISSIONS:
        raise ValueError("unsafe review output permissions")

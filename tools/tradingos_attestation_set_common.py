"""Shared deterministic helpers for TradingOS R83 frozen attestation evidence sets."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

EVIDENCE_SET_SCHEMA = "tradingos.attestation_evidence_set.v1"
POLICY_ID = "TRADINGOS_ATTESTATION_EVIDENCE_SET_POLICY_V1"
VERSION = "1.0.0"

_ID24_RE = re.compile(r"^[0-9a-f]{24}$")
_SHA64_RE = re.compile(r"^[0-9a-f]{64}$")

OUTPUT_PERMISSIONS = {
    "execution_authority": "NONE",
    "signals_allowed": False,
    "orders_allowed": False,
    "can_trade": False,
    "capital_permission": "DENY",
    "confers_authority": False,
}

POLICY_KEYS = {
    "schema_version", "policy_id", "mode", "input_attestation_schema",
    "require_full_r82_validation", "require_homogeneous_review_policy",
    "min_items", "max_items", "allow_same_shadow_report_multiple_attestations",
    "reviewer_identity_inference_allowed", "distinct_reviewer_count_allowed",
    "consensus_inference_allowed", "disposition_aggregation_allowed",
    "reason_aggregation_allowed", "approval_state_allowed", "recommendations_allowed",
    "probability_outputs_allowed", "rate_outputs_allowed", "confidence_outputs_allowed",
    "pnl_fields_allowed", "price_return_fields_allowed", "model_ranking_allowed",
    "provider_ranking_allowed", "external_sources_allowed", "persistence_in_core_allowed",
    "policy_update_allowed", "live_decision_feedback_allowed", "live_decision_use_allowed",
    "model_selection_use_allowed", "human_review_only", "shadow_only",
    "attestation_set_consumption_authority", "memory_write_authority", "output_permissions",
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


def validate_evidence_set_policy(policy: Any) -> None:
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        raise ValueError("evidence-set policy key set mismatch")
    if policy.get("schema_version") != 1 or policy.get("policy_id") != POLICY_ID:
        raise ValueError("unsupported evidence-set policy")
    if policy.get("mode") != "OFFLINE_FROZEN_ATTESTATION_EVIDENCE_SET_ONLY":
        raise ValueError("evidence-set mode drift")
    if policy.get("input_attestation_schema") != "tradingos.human_review_attestation.v1":
        raise ValueError("input attestation schema drift")
    for field in (
        "require_full_r82_validation", "require_homogeneous_review_policy",
        "allow_same_shadow_report_multiple_attestations", "human_review_only", "shadow_only",
    ):
        if policy.get(field) is not True:
            raise ValueError(f"required evidence-set guard disabled: {field}")
    if policy.get("min_items") != 1 or policy.get("max_items") != 64:
        raise ValueError("evidence-set bounds drift")
    for field in (
        "reviewer_identity_inference_allowed", "distinct_reviewer_count_allowed",
        "consensus_inference_allowed", "disposition_aggregation_allowed",
        "reason_aggregation_allowed", "approval_state_allowed", "recommendations_allowed",
        "probability_outputs_allowed", "rate_outputs_allowed", "confidence_outputs_allowed",
        "pnl_fields_allowed", "price_return_fields_allowed", "model_ranking_allowed",
        "provider_ranking_allowed", "external_sources_allowed", "persistence_in_core_allowed",
        "policy_update_allowed", "live_decision_feedback_allowed", "live_decision_use_allowed",
        "model_selection_use_allowed",
    ):
        if policy.get(field) is not False:
            raise ValueError(f"unsafe evidence-set policy: {field}")
    if policy.get("attestation_set_consumption_authority") != "NONE":
        raise ValueError("attestation-set consumption authority must remain NONE")
    if policy.get("memory_write_authority") != "NONE":
        raise ValueError("memory write authority must remain NONE")
    if policy.get("output_permissions") != OUTPUT_PERMISSIONS:
        raise ValueError("unsafe evidence-set output permissions")

#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import (
    DECISION_PACKET_SCHEMA,
    SHADOW_SAFETY,
    TRADE_CASE_SCHEMA,
    ShadowIntegrationError,
    sha256_obj,
    validate_trade_case,
)
from tools.unified_shadow_control_plane import CONTROL_PLANE_SCHEMA
from tools.unified_shadow_federation import FEDERATION_SCHEMA
from tools.unified_shadow_router import ROUTE_SCHEMA

TRANSACTION_SCHEMA = "bitevo.unified_shadow_transaction.v2"


def _verify_hash(record: Mapping[str, Any], field: str, code: str) -> str:
    if not isinstance(record, Mapping):
        raise ShadowIntegrationError(code)
    expected = sha256_obj({k: v for k, v in record.items() if k != field})
    if record.get(field) != expected:
        raise ShadowIntegrationError(code)
    return str(record[field])


def _verify_safety(record: Mapping[str, Any], field: str) -> None:
    safety = record.get("safety") if isinstance(record, Mapping) else None
    if not isinstance(safety, Mapping):
        raise ShadowIntegrationError(f"{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"unsafe_{field}:{key}")


def build_unified_shadow_transaction(
    trade_case: Mapping[str, Any],
    decision_packet: Mapping[str, Any],
    federation_receipt: Mapping[str, Any],
    route_plan: Mapping[str, Any],
    control_plane_receipt: Mapping[str, Any],
    *,
    frozen_at: str,
) -> dict[str, Any]:
    """Bind P0 data, routing and control outputs into one immutable no-effect transaction."""
    case = validate_trade_case(trade_case)
    if case.get("schema") != TRADE_CASE_SCHEMA:
        raise ShadowIntegrationError("transaction_wrong_trade_case_schema")

    if not isinstance(decision_packet, Mapping) or decision_packet.get("schema") != DECISION_PACKET_SCHEMA:
        raise ShadowIntegrationError("transaction_wrong_decision_packet_schema")
    if decision_packet.get("case_id") != case["case_id"] or decision_packet.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("transaction_decision_packet_case_mismatch")
    _verify_safety(decision_packet, "transaction_packet")
    packet_sha = _verify_hash(decision_packet, "packet_sha256", "transaction_packet_hash_mismatch")

    if not isinstance(federation_receipt, Mapping) or federation_receipt.get("schema") != FEDERATION_SCHEMA:
        raise ShadowIntegrationError("transaction_wrong_federation_schema")
    if federation_receipt.get("case_id") != case["case_id"]:
        raise ShadowIntegrationError("transaction_federation_case_mismatch")
    if federation_receipt.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("transaction_federation_case_hash_mismatch")
    if federation_receipt.get("decision_packet_sha256") != packet_sha:
        raise ShadowIntegrationError("transaction_federation_packet_hash_mismatch")
    _verify_safety(federation_receipt, "transaction_federation")
    federation_sha = _verify_hash(
        federation_receipt,
        "federation_sha256",
        "transaction_federation_hash_mismatch",
    )

    if not isinstance(route_plan, Mapping) or route_plan.get("schema") != ROUTE_SCHEMA:
        raise ShadowIntegrationError("transaction_wrong_route_schema")
    if route_plan.get("case_id") != case["case_id"] or route_plan.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("transaction_route_case_mismatch")
    _verify_safety(route_plan, "transaction_route")
    route_sha = _verify_hash(route_plan, "route_sha256", "transaction_route_hash_mismatch")
    if route_plan.get("registered_node_count") != federation_receipt.get("registry_count"):
        raise ShadowIntegrationError("transaction_route_registry_count_mismatch")
    if route_plan.get("executor_boundary", {}).get("enabled") is not False:
        raise ShadowIntegrationError("transaction_executor_must_be_disabled")

    if not isinstance(control_plane_receipt, Mapping) or control_plane_receipt.get("schema") != CONTROL_PLANE_SCHEMA:
        raise ShadowIntegrationError("transaction_wrong_control_plane_schema")
    if control_plane_receipt.get("case_id") != case["case_id"]:
        raise ShadowIntegrationError("transaction_control_plane_case_mismatch")
    if control_plane_receipt.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("transaction_control_plane_case_hash_mismatch")
    if control_plane_receipt.get("decision_packet_sha256") != packet_sha:
        raise ShadowIntegrationError("transaction_control_plane_packet_hash_mismatch")
    _verify_safety(control_plane_receipt, "transaction_control_plane")
    control_sha = _verify_hash(
        control_plane_receipt,
        "control_plane_sha256",
        "transaction_control_plane_hash_mismatch",
    )
    if control_plane_receipt.get("executor_boundary", {}).get("enabled") is not False:
        raise ShadowIntegrationError("transaction_control_plane_executor_must_be_disabled")
    projection = control_plane_receipt.get("control_center_projection", {})
    continuity = control_plane_receipt.get("continuity_and_return", {})
    if projection.get("apply") is not False or projection.get("current_truth_mutation") is not False:
        raise ShadowIntegrationError("transaction_control_plane_apply_forbidden")
    for key in ("event_append", "checkpoint_write", "replay_write", "return_packet_write", "archive_write", "runtime_activation"):
        if continuity.get(key) is not False:
            raise ShadowIntegrationError(f"transaction_continuity_write_forbidden:{key}")

    control_gate = control_plane_receipt.get("control_gate")
    if control_gate not in {"PASS_SHADOW", "HOLD"}:
        raise ShadowIntegrationError("transaction_invalid_control_gate")
    control_action = control_plane_receipt.get("control_plane_action")
    if control_gate == "HOLD" and control_action != "WAIT":
        raise ShadowIntegrationError("transaction_hold_must_force_wait")

    body = {
        "schema": TRANSACTION_SCHEMA,
        "frozen_at": str(frozen_at),
        "case_id": case["case_id"],
        "trade_case_sha256": case["case_sha256"],
        "decision_packet_sha256": packet_sha,
        "federation_sha256": federation_sha,
        "route_sha256": route_sha,
        "control_plane_sha256": control_sha,
        "registered_node_count": federation_receipt["registry_count"],
        "system_recommendation": decision_packet["system_recommendation"],
        "control_gate": control_gate,
        "control_plane_action": control_action,
        "hanri_freshness": control_plane_receipt["hanri"]["freshness"],
        "hanri_attention_required": control_plane_receipt["hanri"]["attention_required"],
        "twin_prediction_status": decision_packet["twin"]["prediction_status"],
        "divergence": decision_packet["divergence"],
        "effect_boundary": {
            "executor_enabled": False,
            "current_truth_apply": False,
            "continuity_write": False,
            "runtime_registration": False,
            "external_model_call": False,
            "exchange_call": False,
            "signal": False,
            "order": False,
            "credential_mutation": False,
            "merge": False,
            "deploy": False,
        },
        "semantics": {
            "one_transaction_one_case": True,
            "route_federation_and_control_are_hash_bound": True,
            "prediction_is_not_permission": True,
            "federation_accounting_is_not_runtime_invocation": True,
            "shadow_projection_is_not_current_truth": True,
            "stale_control_evidence_can_block_without_mutation": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["transaction_sha256"] = sha256_obj(body)
    return body

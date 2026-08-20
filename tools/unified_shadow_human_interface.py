#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj

BASE_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v9"
INTERFACE_LEDGER_SCHEMA = "bitevo.shadow_human_interface_ledger.v1"
INTERFACE_RECEIPT_SCHEMA = "bitevo.shadow_human_interface_receipt.v1"

INTERFACE_NODES = (
    "portfolio:unified-dashboard",
    "portfolio:work-cockpit",
    "entity:universe_hub",
)

INTERFACE_SPECS: dict[str, dict[str, Any]] = {
    "portfolio:unified-dashboard": {
        "role": "CONTROL_CENTER_HANRI_SNAPSHOT_DASHBOARD",
        "current_posture": "CONTRACT_READY_LIVE_ADAPTERS_PENDING",
        "proof_fields": (
            "source_identity_verified",
            "snapshot_contract_verified",
            "freshness_rendering_verified",
            "unknown_degraded_rendering_verified",
            "current_bytes_verified",
        ),
    },
    "portfolio:work-cockpit": {
        "role": "OPERATOR_DECISION_QUEUE_DRAFT_AND_FOLLOWUP_COCKPIT",
        "current_posture": "ACTIVE_DELTA_MODE_WORKING_INTERNAL",
        "proof_fields": (
            "source_identity_verified",
            "decision_queue_contract_verified",
            "evidence_linkage_verified",
            "draft_only_external_message_boundary_verified",
            "human_review_gate_verified",
        ),
    },
    "entity:universe_hub": {
        "role": "FEDERATED_SYSTEM_NAVIGATION_AND_OBSERVATION_HUB",
        "current_posture": "REFERENCE_ARCHITECTURE_READ_ONLY_DEGRADED",
        "proof_fields": (
            "source_identity_verified",
            "system_manifest_verified",
            "route_resolver_verified",
            "snapshot_provenance_verified",
            "no_fake_metrics_verified",
            "read_only_action_boundary_verified",
        ),
    },
}


def _verify_safety(value: Mapping[str, Any], field: str) -> None:
    safety = value.get("safety") if isinstance(value, Mapping) else None
    if not isinstance(safety, Mapping):
        raise ShadowIntegrationError(f"human_interface_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"human_interface_unsafe_{field}:{key}")


def _verify_base(base: Mapping[str, Any]) -> tuple[str, str, str, str]:
    if not isinstance(base, Mapping) or base.get("schema") != BASE_CLOSURE_SCHEMA:
        raise ShadowIntegrationError("human_interface_wrong_base_schema")
    if base.get("registered_node_count") != 63 or base.get("status") != "P0_SHADOW_CLOSED_NO_EFFECT":
        raise ShadowIntegrationError("human_interface_base_state_mismatch")
    _verify_safety(base, "base")
    effects = base.get("effect_summary")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("human_interface_base_effect_boundary_breached")
    gate = str(base.get("effective_gate"))
    action = str(base.get("effective_action"))
    if gate not in {"PASS_SHADOW", "HOLD"} or (gate == "HOLD" and action != "WAIT"):
        raise ShadowIntegrationError("human_interface_base_decision_invalid")
    expected = sha256_obj({k: v for k, v in base.items() if k != "closure_sha256"})
    if base.get("closure_sha256") != expected:
        raise ShadowIntegrationError("human_interface_base_hash_mismatch")
    return str(base["closure_sha256"]), str(base.get("transaction_sha256")), gate, action


def build_default_interface_evidence() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for node_id, spec in INTERFACE_SPECS.items():
        row = {
            "node_id": node_id,
            "evidence_class": "BOUNDED_INTERFACE_POSTURE_ONLY",
            "current_posture": spec["current_posture"],
            "external_message_sent": False,
            "current_truth_written": False,
            "approval_granted": False,
            "runtime_action_performed": False,
            "trade_action_performed": False,
        }
        for field in spec["proof_fields"]:
            row.setdefault(field, False)
        result[node_id] = row
    return result


def _build_receipt(node_id: str, evidence: Mapping[str, Any], *, base_sha: str, tx_sha: str) -> dict[str, Any]:
    if not isinstance(evidence, Mapping):
        raise ShadowIntegrationError("human_interface_evidence_must_be_mapping")
    spec = INTERFACE_SPECS[node_id]
    row = dict(evidence)
    for key in (
        "external_message_sent",
        "current_truth_written",
        "approval_granted",
        "runtime_action_performed",
        "trade_action_performed",
    ):
        if row.get(key) is not False:
            raise ShadowIntegrationError(f"human_interface_effect_boundary_breached:{node_id}:{key}")
    required = tuple(spec["proof_fields"])
    missing = tuple(field for field in required if row.get(field) is not True)
    proof_complete = not missing

    body = {
        "schema": INTERFACE_RECEIPT_SCHEMA,
        "node_id": node_id,
        "role": spec["role"],
        "source_closure_sha256": base_sha,
        "source_transaction_sha256": tx_sha,
        "current_posture": str(row.get("current_posture", spec["current_posture"])),
        "evidence_class": str(row.get("evidence_class", "UNKNOWN")),
        "required_proof_fields": required,
        "missing_proof_fields": missing,
        "proof_complete": proof_complete,
        "typed_contract_bound": True,
        "presentation_only": True,
        "source_of_truth": False,
        "decision_vote": False,
        "gate_effect": "NONE",
        "may_widen_gate": False,
        "may_grant_approval": False,
        "may_send_external_message": False,
        "may_execute_action": False,
        "execution_authority": "NONE",
        "effects": {
            "current_truth_write": False,
            "approval": False,
            "external_message": False,
            "runtime_action": False,
            "signal": False,
            "order": False,
            "capital_effect": False,
        },
        "semantics": {
            "rendered_status_is_not_source_truth": True,
            "navigation_is_not_authority": True,
            "draft_is_not_send": True,
            "operator_review_is_required_for_external_message": True,
            "unknown_or_degraded_must_not_render_operational": True,
            "interface_does_not_call_production_effect_directly": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["interface_receipt_sha256"] = sha256_obj(body)
    return body


def build_shadow_human_interface_ledger(
    base_closure: Mapping[str, Any],
    evidence_bundle: Mapping[str, Mapping[str, Any]],
    *,
    generated_at: str,
) -> dict[str, Any]:
    base_sha, tx_sha, gate, action = _verify_base(base_closure)
    if not isinstance(evidence_bundle, Mapping) or set(evidence_bundle) != set(INTERFACE_NODES):
        raise ShadowIntegrationError("human_interface_coverage_mismatch")
    receipts = tuple(
        _build_receipt(node_id, evidence_bundle[node_id], base_sha=base_sha, tx_sha=tx_sha)
        for node_id in INTERFACE_NODES
    )
    body = {
        "schema": INTERFACE_LEDGER_SCHEMA,
        "generated_at": str(generated_at),
        "source_closure_sha256": base_sha,
        "source_transaction_sha256": tx_sha,
        "case_id": base_closure.get("case_id"),
        "base_gate": gate,
        "base_action": action,
        "interface_node_count": len(receipts),
        "all_interface_nodes_typed": True,
        "proof_complete_nodes": tuple(row["node_id"] for row in receipts if row["proof_complete"] is True),
        "receipts": receipts,
        "plane_rules": {
            "presentation_is_not_truth": True,
            "navigation_is_not_authority": True,
            "draft_is_not_send": True,
            "human_review_required_for_external_messages": True,
            "no_gate_change": True,
            "no_direct_effect_calls": True,
            "degraded_unknown_fail_closed_in_ui": True,
        },
        "effects": {
            "current_truth_write": False,
            "approval": False,
            "external_message": False,
            "runtime_action": False,
            "signal": False,
            "order": False,
            "capital_effect": False,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["interface_ledger_sha256"] = sha256_obj(body)
    return body

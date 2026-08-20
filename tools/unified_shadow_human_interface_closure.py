#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_human_interface import INTERFACE_LEDGER_SCHEMA, INTERFACE_NODES

BASE_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v9"
FINAL_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v10"


def _verify_safety(value: Mapping[str, Any], field: str) -> None:
    safety = value.get("safety") if isinstance(value, Mapping) else None
    if not isinstance(safety, Mapping):
        raise ShadowIntegrationError(f"human_interface_closure_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"human_interface_closure_unsafe_{field}:{key}")


def _hash(value: Mapping[str, Any], field: str, code: str) -> str:
    expected = sha256_obj({k: v for k, v in value.items() if k != field})
    if value.get(field) != expected:
        raise ShadowIntegrationError(code)
    return str(value[field])


def _verify_base(base: Mapping[str, Any]) -> tuple[str, str, str, str]:
    if not isinstance(base, Mapping) or base.get("schema") != BASE_CLOSURE_SCHEMA:
        raise ShadowIntegrationError("human_interface_closure_wrong_base_schema")
    if base.get("registered_node_count") != 63 or base.get("status") != "P0_SHADOW_CLOSED_NO_EFFECT":
        raise ShadowIntegrationError("human_interface_closure_base_state_mismatch")
    _verify_safety(base, "base")
    effects = base.get("effect_summary")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("human_interface_closure_base_effect_boundary_breached")
    gate = str(base.get("effective_gate"))
    action = str(base.get("effective_action"))
    if gate not in {"PASS_SHADOW", "HOLD"} or (gate == "HOLD" and action != "WAIT"):
        raise ShadowIntegrationError("human_interface_closure_base_decision_invalid")
    return _hash(base, "closure_sha256", "human_interface_closure_base_hash_mismatch"), str(base.get("transaction_sha256")), gate, action


def _verify_ledger(ledger: Mapping[str, Any], *, base_sha: str, tx_sha: str, gate: str, action: str) -> str:
    if not isinstance(ledger, Mapping) or ledger.get("schema") != INTERFACE_LEDGER_SCHEMA:
        raise ShadowIntegrationError("human_interface_closure_wrong_ledger_schema")
    if ledger.get("source_closure_sha256") != base_sha or ledger.get("source_transaction_sha256") != tx_sha:
        raise ShadowIntegrationError("human_interface_closure_binding_mismatch")
    if ledger.get("base_gate") != gate or ledger.get("base_action") != action:
        raise ShadowIntegrationError("human_interface_closure_decision_mismatch")
    if ledger.get("interface_node_count") != len(INTERFACE_NODES) or ledger.get("all_interface_nodes_typed") is not True:
        raise ShadowIntegrationError("human_interface_closure_coverage_mismatch")
    _verify_safety(ledger, "ledger")
    effects = ledger.get("effects")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("human_interface_closure_ledger_effect_boundary_breached")

    rules = ledger.get("plane_rules") or {}
    for key in (
        "presentation_is_not_truth",
        "navigation_is_not_authority",
        "draft_is_not_send",
        "human_review_required_for_external_messages",
        "no_gate_change",
        "no_direct_effect_calls",
        "degraded_unknown_fail_closed_in_ui",
    ):
        if rules.get(key) is not True:
            raise ShadowIntegrationError(f"human_interface_closure_rule_missing:{key}")

    receipts = ledger.get("receipts")
    if not isinstance(receipts, (list, tuple)) or len(receipts) != len(INTERFACE_NODES):
        raise ShadowIntegrationError("human_interface_closure_receipt_count_mismatch")
    if tuple(row.get("node_id") for row in receipts if isinstance(row, Mapping)) != INTERFACE_NODES:
        raise ShadowIntegrationError("human_interface_closure_receipt_identity_mismatch")
    for row in receipts:
        if not isinstance(row, Mapping):
            raise ShadowIntegrationError("human_interface_closure_invalid_receipt")
        _verify_safety(row, "receipt")
        _hash(row, "interface_receipt_sha256", "human_interface_closure_receipt_hash_mismatch")
        if row.get("typed_contract_bound") is not True or row.get("presentation_only") is not True:
            raise ShadowIntegrationError("human_interface_closure_nonpresentation_receipt")
        if row.get("source_of_truth") is not False or row.get("decision_vote") is not False or row.get("gate_effect") != "NONE":
            raise ShadowIntegrationError("human_interface_closure_decision_or_truth_influence")
        if row.get("may_widen_gate") is not False or row.get("may_grant_approval") is not False:
            raise ShadowIntegrationError("human_interface_closure_authority_detected")
        if row.get("may_send_external_message") is not False or row.get("may_execute_action") is not False:
            raise ShadowIntegrationError("human_interface_closure_effect_capability_detected")
        if row.get("execution_authority") != "NONE":
            raise ShadowIntegrationError("human_interface_closure_execution_authority_detected")
        row_effects = row.get("effects")
        if not isinstance(row_effects, Mapping) or any(value is not False for value in row_effects.values()):
            raise ShadowIntegrationError("human_interface_closure_receipt_effect_boundary_breached")
    return _hash(ledger, "interface_ledger_sha256", "human_interface_closure_ledger_hash_mismatch")


def build_unified_shadow_human_interface_closure(
    base_closure: Mapping[str, Any],
    interface_ledger: Mapping[str, Any],
    *,
    closed_at: str,
) -> dict[str, Any]:
    base_sha, tx_sha, gate, action = _verify_base(base_closure)
    ledger_sha = _verify_ledger(interface_ledger, base_sha=base_sha, tx_sha=tx_sha, gate=gate, action=action)
    body = {
        "schema": FINAL_CLOSURE_SCHEMA,
        "closed_at": str(closed_at),
        "case_id": base_closure.get("case_id"),
        "transaction_sha256": tx_sha,
        "base_closure_sha256": base_sha,
        "interface_ledger_sha256": ledger_sha,
        "registered_node_count": 63,
        "typed_interface_node_count": len(INTERFACE_NODES),
        "effective_gate": gate,
        "effective_action": action,
        "status": "P0_SHADOW_CLOSED_NO_EFFECT",
        "planes": {
            **dict(base_closure.get("planes") or {}),
            "human_interface_plane": "BOUND_3_OF_3_PRESENTATION_ONLY_NO_EFFECT",
        },
        "effect_summary": {
            **dict(base_closure.get("effect_summary") or {}),
            "interface_current_truth_write": False,
            "interface_approval": False,
            "interface_external_message": False,
            "interface_runtime_action": False,
            "interface_vote": False,
        },
        "interface_status": {
            "typed_nodes": len(INTERFACE_NODES),
            "proof_complete_nodes": tuple(interface_ledger.get("proof_complete_nodes") or ()),
            "source_of_truth": False,
            "approval_authority": False,
            "external_message_authority": False,
            "effect_authority": False,
        },
        "semantics": {
            "universe_hub_is_federated_presentation_not_truth": True,
            "work_cockpit_drafts_require_human_review_before_send": True,
            "dashboard_status_must_follow_snapshot_freshness": True,
            "unknown_degraded_never_becomes_operational_by_rendering": True,
            "interface_navigation_never_changes_authority": True,
            "base_gate_and_action_are_preserved_exactly": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["closure_sha256"] = sha256_obj(body)
    return body

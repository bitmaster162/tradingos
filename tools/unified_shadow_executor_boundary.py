#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj

BASE_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v12"
EXECUTOR_RECEIPT_SCHEMA = "bitevo.shadow_executor_boundary_receipt.v1"
FINAL_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v13"
EXECUTOR_NODE = "entity:executor_network"

PROOF_FIELDS = (
    "task_capsule_contract_verified",
    "capability_grant_contract_verified",
    "input_manifest_verified",
    "result_envelope_verified",
    "evidence_manifest_verified",
    "completion_status_receipt_verified",
    "acceptance_decision_externalized_verified",
    "trusted_effect_class_derivation_verified",
    "operation_specific_handler_verified",
    "trusted_approval_registry_verified",
    "active_writer_quiesce_protocol_verified",
    "vendor_fallback_budget_explicit_verified",
)

FORBIDDEN_OBSERVED_FIELDS = (
    "executor_enabled",
    "dispatch_performed",
    "arbitrary_command_allowed",
    "caller_chosen_effect_class_allowed",
    "self_accepted",
    "self_merged",
    "self_deployed",
    "authority_increased",
    "approval_self_issued",
    "runtime_effect_performed",
    "external_message_sent",
    "signal_emitted",
    "order_emitted",
    "capital_effect",
)


def _verify_safety(value: Mapping[str, Any], field: str) -> None:
    safety = value.get("safety") if isinstance(value, Mapping) else None
    if not isinstance(safety, Mapping):
        raise ShadowIntegrationError(f"executor_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"executor_unsafe_{field}:{key}")


def _verify_base(base: Mapping[str, Any]) -> tuple[str, str, str, str]:
    if not isinstance(base, Mapping) or base.get("schema") != BASE_CLOSURE_SCHEMA:
        raise ShadowIntegrationError("executor_wrong_base_schema")
    if base.get("registered_node_count") != 63 or base.get("status") != "P0_SHADOW_CLOSED_NO_EFFECT":
        raise ShadowIntegrationError("executor_base_state_mismatch")
    _verify_safety(base, "base")
    if any(value is not False for value in (base.get("effect_summary") or {}).values()):
        raise ShadowIntegrationError("executor_base_effect_boundary_breached")
    gate, action = str(base.get("effective_gate")), str(base.get("effective_action"))
    if gate not in {"PASS_SHADOW", "HOLD"} or (gate == "HOLD" and action != "WAIT"):
        raise ShadowIntegrationError("executor_base_decision_invalid")
    expected = sha256_obj({k: v for k, v in base.items() if k != "closure_sha256"})
    if base.get("closure_sha256") != expected:
        raise ShadowIntegrationError("executor_base_hash_mismatch")
    return str(base["closure_sha256"]), str(base.get("transaction_sha256")), gate, action


def build_default_executor_evidence() -> dict[str, Any]:
    row: dict[str, Any] = {
        "node_id": EXECUTOR_NODE,
        "evidence_class": "BOUNDED_EXECUTOR_CONTRACT_POSTURE_ONLY",
        "current_posture": "DISABLED_P0_TYPED_EFFECT_MEMBRANE_ONLY",
    }
    for field in PROOF_FIELDS:
        row[field] = False
    for field in FORBIDDEN_OBSERVED_FIELDS:
        row[field] = False
    return row


def build_shadow_executor_boundary_receipt(
    base_closure: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    generated_at: str,
) -> dict[str, Any]:
    base_sha, tx_sha, gate, action = _verify_base(base_closure)
    if not isinstance(evidence, Mapping) or evidence.get("node_id") != EXECUTOR_NODE:
        raise ShadowIntegrationError("executor_wrong_node")
    row = dict(evidence)
    for field in FORBIDDEN_OBSERVED_FIELDS:
        if row.get(field) is not False:
            raise ShadowIntegrationError(f"executor_effect_or_bypass_observed:{field}")
    missing = tuple(field for field in PROOF_FIELDS if row.get(field) is not True)
    body = {
        "schema": EXECUTOR_RECEIPT_SCHEMA,
        "generated_at": str(generated_at),
        "node_id": EXECUTOR_NODE,
        "source_closure_sha256": base_sha,
        "source_transaction_sha256": tx_sha,
        "base_gate": gate,
        "base_action": action,
        "current_posture": str(row.get("current_posture", "DISABLED_P0_TYPED_EFFECT_MEMBRANE_ONLY")),
        "evidence_class": str(row.get("evidence_class", "UNKNOWN")),
        "required_proof_fields": PROOF_FIELDS,
        "missing_proof_fields": missing,
        "proof_complete": not missing,
        "typed_contract_bound": True,
        "executor_enabled": False,
        "dispatch_enabled": False,
        "arbitrary_command_allowed": False,
        "caller_chosen_effect_class_allowed": False,
        "trusted_effect_class_derivation_required": True,
        "operation_specific_handler_required": True,
        "trusted_approval_registry_required": True,
        "active_writer_lease_required": True,
        "may_self_accept": False,
        "may_self_merge": False,
        "may_self_deploy": False,
        "may_change_authority": False,
        "may_self_issue_approval": False,
        "decision_vote": False,
        "gate_effect": "NONE",
        "may_widen_gate": False,
        "execution_authority": "NONE",
        "effects": {
            "dispatch": False,
            "runtime_effect": False,
            "external_message": False,
            "merge": False,
            "deploy": False,
            "current_truth_write": False,
            "signal": False,
            "order": False,
            "capital_effect": False,
        },
        "semantics": {
            "vendor_session_is_not_durable_project_state": True,
            "result_envelope_is_not_acceptance": True,
            "completion_claim_is_not_acceptance": True,
            "capability_grant_is_scoped_not_standing_authority": True,
            "caller_cannot_label_arbitrary_command_as_read_local": True,
            "effect_class_is_derived_by_trusted_broker": True,
            "acceptance_decision_is_external_to_executor": True,
            "hidden_chain_of_thought_export_not_required": True,
            "proof_complete_does_not_enable_executor_in_p0": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["executor_receipt_sha256"] = sha256_obj(body)
    return body


def _verify_receipt(receipt: Mapping[str, Any], *, base_sha: str, tx_sha: str, gate: str, action: str) -> str:
    if not isinstance(receipt, Mapping) or receipt.get("schema") != EXECUTOR_RECEIPT_SCHEMA or receipt.get("node_id") != EXECUTOR_NODE:
        raise ShadowIntegrationError("executor_closure_wrong_receipt")
    if receipt.get("source_closure_sha256") != base_sha or receipt.get("source_transaction_sha256") != tx_sha:
        raise ShadowIntegrationError("executor_closure_binding_mismatch")
    if receipt.get("base_gate") != gate or receipt.get("base_action") != action:
        raise ShadowIntegrationError("executor_closure_decision_mismatch")
    _verify_safety(receipt, "receipt")
    expected = sha256_obj({k: v for k, v in receipt.items() if k != "executor_receipt_sha256"})
    if receipt.get("executor_receipt_sha256") != expected:
        raise ShadowIntegrationError("executor_closure_receipt_hash_mismatch")
    required_false = (
        "executor_enabled", "dispatch_enabled", "arbitrary_command_allowed",
        "caller_chosen_effect_class_allowed", "may_self_accept", "may_self_merge",
        "may_self_deploy", "may_change_authority", "may_self_issue_approval",
        "decision_vote", "may_widen_gate",
    )
    if any(receipt.get(field) is not False for field in required_false):
        raise ShadowIntegrationError("executor_closure_disabled_invariant_breached")
    for field in ("trusted_effect_class_derivation_required", "operation_specific_handler_required", "trusted_approval_registry_required", "active_writer_lease_required"):
        if receipt.get(field) is not True:
            raise ShadowIntegrationError(f"executor_closure_required_control_missing:{field}")
    if receipt.get("gate_effect") != "NONE" or receipt.get("execution_authority") != "NONE":
        raise ShadowIntegrationError("executor_closure_authority_detected")
    if any(value is not False for value in (receipt.get("effects") or {}).values()):
        raise ShadowIntegrationError("executor_closure_effect_boundary_breached")
    return str(receipt["executor_receipt_sha256"])


def build_unified_shadow_executor_closure(
    base_closure: Mapping[str, Any],
    executor_receipt: Mapping[str, Any],
    *,
    closed_at: str,
) -> dict[str, Any]:
    base_sha, tx_sha, gate, action = _verify_base(base_closure)
    receipt_sha = _verify_receipt(executor_receipt, base_sha=base_sha, tx_sha=tx_sha, gate=gate, action=action)
    body = {
        "schema": FINAL_CLOSURE_SCHEMA,
        "closed_at": str(closed_at),
        "case_id": base_closure.get("case_id"),
        "transaction_sha256": tx_sha,
        "base_closure_sha256": base_sha,
        "executor_receipt_sha256": receipt_sha,
        "registered_node_count": 63,
        "typed_executor_node_count": 1,
        "effective_gate": gate,
        "effective_action": action,
        "status": "P0_SHADOW_CLOSED_NO_EFFECT",
        "planes": {
            **dict(base_closure.get("planes") or {}),
            "executor_boundary": "BOUND_1_OF_1_DISABLED_TYPED_EFFECT_MEMBRANE",
        },
        "effect_summary": {
            **dict(base_closure.get("effect_summary") or {}),
            "executor_dispatch": False,
            "executor_runtime_effect": False,
            "executor_external_message": False,
            "executor_merge": False,
            "executor_deploy": False,
            "executor_current_truth_write": False,
            "executor_vote": False,
        },
        "executor_status": {
            "typed_nodes": 1,
            "executor_enabled": False,
            "dispatch_enabled": False,
            "proof_complete": executor_receipt.get("proof_complete") is True,
            "trusted_effect_derivation_required": True,
            "arbitrary_command_allowed": False,
            "self_accept_merge_deploy": False,
            "execution_authority": False,
        },
        "semantics": {
            "effect_membrane_exists_but_is_disabled": True,
            "typed_operation_precedes_effect_class": True,
            "approval_resolution_is_external_and_trusted": True,
            "executor_never_becomes_authority_owner": True,
            "base_gate_and_action_are_preserved_exactly": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["closure_sha256"] = sha256_obj(body)
    return body

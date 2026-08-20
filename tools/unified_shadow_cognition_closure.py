#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_cognition_plane import COGNITION_LEDGER_SCHEMA, COGNITION_NODES

BASE_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v8"
FINAL_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v9"


def _verify_safety(value: Mapping[str, Any], field: str) -> None:
    safety = value.get("safety") if isinstance(value, Mapping) else None
    if not isinstance(safety, Mapping):
        raise ShadowIntegrationError(f"cognition_closure_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"cognition_closure_unsafe_{field}:{key}")


def _verify_hash(value: Mapping[str, Any], field: str, code: str) -> str:
    expected = sha256_obj({k: v for k, v in value.items() if k != field})
    if value.get(field) != expected:
        raise ShadowIntegrationError(code)
    return str(value[field])


def _verify_base(base: Mapping[str, Any]) -> tuple[str, str, str, str]:
    if not isinstance(base, Mapping) or base.get("schema") != BASE_CLOSURE_SCHEMA:
        raise ShadowIntegrationError("cognition_closure_wrong_base_schema")
    if base.get("registered_node_count") != 63:
        raise ShadowIntegrationError("cognition_closure_registry_count_mismatch")
    if base.get("status") != "P0_SHADOW_CLOSED_NO_EFFECT":
        raise ShadowIntegrationError("cognition_closure_base_status_mismatch")
    _verify_safety(base, "base")
    effects = base.get("effect_summary")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("cognition_closure_base_effect_boundary_breached")
    gate = str(base.get("effective_gate"))
    action = str(base.get("effective_action"))
    if gate not in {"PASS_SHADOW", "HOLD"}:
        raise ShadowIntegrationError("cognition_closure_base_gate_invalid")
    if gate == "HOLD" and action != "WAIT":
        raise ShadowIntegrationError("cognition_closure_base_hold_must_wait")
    return (
        _verify_hash(base, "closure_sha256", "cognition_closure_base_hash_mismatch"),
        str(base.get("transaction_sha256")),
        gate,
        action,
    )


def _verify_ledger(
    ledger: Mapping[str, Any],
    *,
    base_sha: str,
    transaction_sha: str,
    gate: str,
    action: str,
) -> str:
    if not isinstance(ledger, Mapping) or ledger.get("schema") != COGNITION_LEDGER_SCHEMA:
        raise ShadowIntegrationError("cognition_closure_wrong_ledger_schema")
    if ledger.get("source_closure_sha256") != base_sha:
        raise ShadowIntegrationError("cognition_closure_ledger_base_mismatch")
    if ledger.get("source_transaction_sha256") != transaction_sha:
        raise ShadowIntegrationError("cognition_closure_ledger_transaction_mismatch")
    if ledger.get("base_gate") != gate or ledger.get("base_action") != action:
        raise ShadowIntegrationError("cognition_closure_ledger_decision_mismatch")
    if ledger.get("cognition_node_count") != len(COGNITION_NODES) or ledger.get("all_cognition_nodes_typed") is not True:
        raise ShadowIntegrationError("cognition_closure_ledger_coverage_mismatch")
    _verify_safety(ledger, "ledger")

    plane_rules = ledger.get("plane_rules")
    if not isinstance(plane_rules, Mapping):
        raise ShadowIntegrationError("cognition_closure_plane_rules_missing")
    for key in (
        "proposal_only",
        "no_case_influence_in_p0",
        "no_majority_vote",
        "no_gate_change",
        "no_current_truth_authority",
        "no_memory_write_authority",
        "no_effect_authority",
        "human_approval_remains_external",
        "source_runtime_deployment_claims_remain_separate",
    ):
        if plane_rules.get(key) is not True:
            raise ShadowIntegrationError(f"cognition_closure_rule_missing:{key}")

    effects = ledger.get("effects")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("cognition_closure_ledger_effect_boundary_breached")

    receipts = ledger.get("receipts")
    if not isinstance(receipts, (tuple, list)) or len(receipts) != len(COGNITION_NODES):
        raise ShadowIntegrationError("cognition_closure_receipt_count_mismatch")
    ids = tuple(row.get("node_id") for row in receipts if isinstance(row, Mapping))
    if ids != COGNITION_NODES:
        raise ShadowIntegrationError("cognition_closure_receipt_identity_mismatch")

    proof_complete = []
    for row in receipts:
        if not isinstance(row, Mapping):
            raise ShadowIntegrationError("cognition_closure_invalid_receipt")
        _verify_safety(row, "receipt")
        _verify_hash(row, "cognition_receipt_sha256", "cognition_closure_receipt_hash_mismatch")
        if row.get("typed_contract_bound") is not True or row.get("proposal_only") is not True:
            raise ShadowIntegrationError("cognition_closure_nonproposal_receipt")
        if row.get("case_influence_enabled") is not False:
            raise ShadowIntegrationError("cognition_closure_case_influence_detected")
        if row.get("decision_vote") is not False or row.get("gate_effect") != "NONE":
            raise ShadowIntegrationError("cognition_closure_decision_influence_detected")
        if row.get("may_widen_gate") is not False:
            raise ShadowIntegrationError("cognition_closure_gate_widening_detected")
        if row.get("current_truth_authority") != "NONE":
            raise ShadowIntegrationError("cognition_closure_current_truth_authority_detected")
        if row.get("memory_authority") != "NONE":
            raise ShadowIntegrationError("cognition_closure_memory_authority_detected")
        if row.get("execution_authority") != "NONE":
            raise ShadowIntegrationError("cognition_closure_execution_authority_detected")
        if row.get("external_runtime_invoked") is not False:
            raise ShadowIntegrationError("cognition_closure_runtime_invocation_detected")
        row_effects = row.get("effects")
        if not isinstance(row_effects, Mapping) or any(value is not False for value in row_effects.values()):
            raise ShadowIntegrationError("cognition_closure_receipt_effect_boundary_breached")
        if row.get("proof_complete") is True:
            proof_complete.append(row.get("node_id"))

    if tuple(proof_complete) != tuple(ledger.get("proof_complete_nodes") or ()):
        raise ShadowIntegrationError("cognition_closure_proof_index_mismatch")
    return _verify_hash(ledger, "cognition_ledger_sha256", "cognition_closure_ledger_hash_mismatch")


def build_unified_shadow_cognition_closure(
    base_closure: Mapping[str, Any],
    cognition_ledger: Mapping[str, Any],
    *,
    closed_at: str,
) -> dict[str, Any]:
    """Bind all cognition-side roles while preserving the exact v8 decision and no-effect boundary."""
    base_sha, transaction_sha, gate, action = _verify_base(base_closure)
    ledger_sha = _verify_ledger(
        cognition_ledger,
        base_sha=base_sha,
        transaction_sha=transaction_sha,
        gate=gate,
        action=action,
    )

    body = {
        "schema": FINAL_CLOSURE_SCHEMA,
        "closed_at": str(closed_at),
        "case_id": base_closure.get("case_id"),
        "transaction_sha256": transaction_sha,
        "base_closure_sha256": base_sha,
        "cognition_ledger_sha256": ledger_sha,
        "registered_node_count": 63,
        "typed_cognition_node_count": len(COGNITION_NODES),
        "effective_gate": gate,
        "effective_action": action,
        "status": "P0_SHADOW_CLOSED_NO_EFFECT",
        "planes": {
            **dict(base_closure.get("planes") or {}),
            "cognition_proposal_plane": "BOUND_11_OF_11_PROPOSAL_ONLY_NO_CASE_INFLUENCE",
        },
        "effect_summary": {
            **dict(base_closure.get("effect_summary") or {}),
            "cognition_runtime": False,
            "cognition_model_call": False,
            "cognition_tool_call": False,
            "cognition_memory_write": False,
            "cognition_current_truth_write": False,
            "cognition_human_approval": False,
            "cognition_canary": False,
            "cognition_deploy": False,
            "cognition_vote": False,
        },
        "cognition_status": {
            "typed_nodes": len(COGNITION_NODES),
            "proof_complete_nodes": tuple(cognition_ledger.get("proof_complete_nodes") or ()),
            "case_influence_enabled": False,
            "gate_change_allowed": False,
            "current_truth_authority_granted": False,
            "memory_write_authority_granted": False,
            "execution_authority_granted": False,
        },
        "semantics": {
            "all_cognition_side_nodes_have_typed_boundary": True,
            "cognition_is_proposal_not_authority": True,
            "proof_complete_does_not_enable_case_influence_in_p0": True,
            "runtime_tool_or_model_harness_is_not_governance": True,
            "hypothesis_state_is_not_commitment_authority": True,
            "identity_memory_policy_is_not_current_truth": True,
            "human_coevolution_requires_external_evaluator_and_human_approval": True,
            "historical_sdk_is_not_active_core_specification": True,
            "legacy_or_product_candidate_is_not_runtime": True,
            "base_gate_and_action_are_preserved_exactly": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["closure_sha256"] = sha256_obj(body)
    return body

#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj

BASE_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v5"
CAPABILITY_LEDGER_SCHEMA = "bitevo.shadow_capability_influence_ledger.v1"
CAPABILITY_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v6"

_EXPECTED_NODE_COUNT = 63


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
        raise ShadowIntegrationError(f"capability_closure_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"capability_closure_unsafe_{field}:{key}")


def _verify_base(closure: Mapping[str, Any]) -> tuple[str, str, str, str]:
    if not isinstance(closure, Mapping) or closure.get("schema") != BASE_CLOSURE_SCHEMA:
        raise ShadowIntegrationError("capability_closure_wrong_base_schema")
    if closure.get("registered_node_count") != _EXPECTED_NODE_COUNT:
        raise ShadowIntegrationError("capability_closure_registry_count_mismatch")
    if closure.get("status") != "P0_SHADOW_CLOSED_NO_EFFECT":
        raise ShadowIntegrationError("capability_closure_base_status_mismatch")
    _verify_safety(closure, "base")
    effects = closure.get("effect_summary")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("capability_closure_base_effect_boundary_breached")
    gate = closure.get("effective_gate")
    action = closure.get("effective_action")
    if gate not in {"PASS_SHADOW", "HOLD"}:
        raise ShadowIntegrationError("capability_closure_effective_gate_invalid")
    if gate == "HOLD" and action != "WAIT":
        raise ShadowIntegrationError("capability_closure_hold_must_wait")
    base_sha = _verify_hash(closure, "closure_sha256", "capability_closure_base_hash_mismatch")
    return base_sha, str(closure.get("transaction_sha256")), str(gate), str(action)


def _verify_ledger(ledger: Mapping[str, Any], base_sha: str, transaction_sha: str, gate: str, action: str) -> str:
    if not isinstance(ledger, Mapping) or ledger.get("schema") != CAPABILITY_LEDGER_SCHEMA:
        raise ShadowIntegrationError("capability_closure_wrong_ledger_schema")
    if ledger.get("source_closure_sha256") != base_sha:
        raise ShadowIntegrationError("capability_closure_ledger_base_mismatch")
    if ledger.get("source_transaction_sha256") != transaction_sha:
        raise ShadowIntegrationError("capability_closure_ledger_transaction_mismatch")
    if ledger.get("registered_node_count") != _EXPECTED_NODE_COUNT or ledger.get("all_nodes_assigned_exactly_once") is not True:
        raise ShadowIntegrationError("capability_closure_ledger_coverage_mismatch")
    if ledger.get("effective_gate") != gate or ledger.get("effective_action") != action:
        raise ShadowIntegrationError("capability_closure_ledger_decision_mismatch")
    _verify_safety(ledger, "ledger")
    effects = ledger.get("effects")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("capability_closure_ledger_effect_boundary_breached")

    rows = ledger.get("nodes")
    if not isinstance(rows, (tuple, list)) or len(rows) != _EXPECTED_NODE_COUNT:
        raise ShadowIntegrationError("capability_closure_node_rows_mismatch")
    ids = [row.get("node_id") for row in rows if isinstance(row, Mapping)]
    if len(ids) != _EXPECTED_NODE_COUNT or len(set(ids)) != _EXPECTED_NODE_COUNT:
        raise ShadowIntegrationError("capability_closure_node_identity_mismatch")
    for row in rows:
        if not isinstance(row, Mapping):
            raise ShadowIntegrationError("capability_closure_node_invalid")
        if row.get("may_widen_gate") is not False:
            raise ShadowIntegrationError("capability_closure_gate_widening_detected")
        if row.get("trading_vote") is not False:
            raise ShadowIntegrationError("capability_closure_trading_vote_detected")
        if row.get("effect_authority") != "NONE":
            raise ShadowIntegrationError("capability_closure_effect_authority_detected")
        if row.get("external_runtime_invoked") is not False:
            raise ShadowIntegrationError("capability_closure_runtime_invocation_detected")
        if row.get("source_identity_proven_by_ledger") is not False or row.get("runtime_proven_by_ledger") is not False:
            raise ShadowIntegrationError("capability_closure_source_runtime_overclaim")

    rules = ledger.get("bus_rules") or {}
    required_rules = (
        "decision_is_not_majority_vote",
        "typed_contract_required_for_influence",
        "accounted_only_node_cannot_change_decision",
        "research_side_plane_cannot_vote",
        "product_service_plane_cannot_vote",
        "trading_advisory_is_not_execution_authority",
        "evidence_gate_can_only_narrow_when_contract_bound",
        "no_node_can_widen_existing_hold",
        "executor_is_separate_disabled_boundary",
    )
    for key in required_rules:
        if rules.get(key) is not True:
            raise ShadowIntegrationError(f"capability_closure_rule_missing:{key}")
    return _verify_hash(ledger, "capability_ledger_sha256", "capability_closure_ledger_hash_mismatch")


def build_unified_shadow_capability_closure(
    base_closure: Mapping[str, Any],
    capability_ledger: Mapping[str, Any],
    *,
    closed_at: str,
) -> dict[str, Any]:
    """Seal the P0 closure with an exact 63-node influence partition and no additional authority."""
    base_sha, transaction_sha, gate, action = _verify_base(base_closure)
    ledger_sha = _verify_ledger(capability_ledger, base_sha, transaction_sha, gate, action)

    body = {
        "schema": CAPABILITY_CLOSURE_SCHEMA,
        "closed_at": str(closed_at),
        "case_id": base_closure.get("case_id"),
        "transaction_sha256": transaction_sha,
        "base_closure_sha256": base_sha,
        "capability_ledger_sha256": ledger_sha,
        "registered_node_count": _EXPECTED_NODE_COUNT,
        "effective_gate": gate,
        "effective_action": action,
        "status": "P0_SHADOW_CLOSED_NO_EFFECT",
        "planes": {
            **dict(base_closure.get("planes") or {}),
            "capability_influence_bus": "BOUND_63_OF_63_NO_EFFECT_AUTHORITY",
        },
        "effect_summary": {
            **dict(base_closure.get("effect_summary") or {}),
            "capability_bus_runtime": False,
            "capability_vote": False,
            "capability_effect_authorization": False,
        },
        "capability_status": {
            "nodes_partitioned": 63,
            "majority_vote": False,
            "runtime_invoked": False,
            "effect_authority_granted": False,
            "gate_widening_allowed": False,
        },
        "semantics": {
            "all_systems_accounted_does_not_mean_all_systems_vote": True,
            "typed_influence_is_distinct_from_registry_membership": True,
            "advisory_capability_is_not_permission": True,
            "commercial_surface_is_not_decision_authority": True,
            "executor_is_not_a_voting_agent": True,
            "capability_ledger_cannot_change_effective_gate": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["closure_sha256"] = sha256_obj(body)
    return body

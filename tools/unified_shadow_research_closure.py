#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj

BASE_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v3"
RESEARCH_PLANE_SCHEMA = "bitevo.shadow_research_simulation_receipt.v1"
RESEARCH_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v4"

_EXPECTED_NODE_COUNT = 63
_EXPECTED_ARENA_HEAD = "f070fe0587a4222b993b7e8fc9b8f2726ca414d9"


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
        raise ShadowIntegrationError(f"research_closure_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"research_closure_unsafe_{field}:{key}")


def _verify_base_closure(closure: Mapping[str, Any]) -> tuple[str, str, str]:
    if not isinstance(closure, Mapping) or closure.get("schema") != BASE_CLOSURE_SCHEMA:
        raise ShadowIntegrationError("research_closure_wrong_base_schema")
    if closure.get("registered_node_count") != _EXPECTED_NODE_COUNT:
        raise ShadowIntegrationError("research_closure_registry_count_mismatch")
    if closure.get("status") != "P0_SHADOW_CLOSED_NO_EFFECT":
        raise ShadowIntegrationError("research_closure_base_status_mismatch")
    _verify_safety(closure, "base")
    effects = closure.get("effect_summary")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("research_closure_base_effect_boundary_breached")
    if closure.get("planes", {}).get("executor") != "DISABLED":
        raise ShadowIntegrationError("research_closure_executor_must_remain_disabled")
    gate = closure.get("effective_gate")
    action = closure.get("effective_action")
    if gate not in {"PASS_SHADOW", "HOLD"}:
        raise ShadowIntegrationError("research_closure_effective_gate_invalid")
    if gate == "HOLD" and action != "WAIT":
        raise ShadowIntegrationError("research_closure_hold_must_wait")
    closure_sha = _verify_hash(closure, "closure_sha256", "research_closure_base_hash_mismatch")
    return closure_sha, str(gate), str(action)


def _verify_research(receipt: Mapping[str, Any], transaction_sha: str) -> str:
    if not isinstance(receipt, Mapping) or receipt.get("schema") != RESEARCH_PLANE_SCHEMA:
        raise ShadowIntegrationError("research_closure_wrong_research_schema")
    if receipt.get("source_transaction_sha256") != transaction_sha:
        raise ShadowIntegrationError("research_closure_transaction_mismatch")
    _verify_safety(receipt, "research")
    if receipt.get("decision_dependency") != "NON_BLOCKING_SIDE_PLANE":
        raise ShadowIntegrationError("research_closure_dependency_widened")
    if receipt.get("trading_voter") is not False or receipt.get("can_change_decision") is not False:
        raise ShadowIntegrationError("research_closure_voting_authority_breached")
    effects = receipt.get("effects")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("research_closure_research_effect_boundary_breached")

    surfaces = receipt.get("surfaces") or {}
    maworld = surfaces.get("maworld") or {}
    pandora = surfaces.get("pandora") or {}
    arena = surfaces.get("sovereign_arena") or {}
    if maworld.get("source_identity_bound") is not False or maworld.get("runtime_invoked") is not False:
        raise ShadowIntegrationError("research_closure_maworld_overclaim")
    if pandora.get("source_identity_bound") is not False or pandora.get("runtime_invoked") is not False:
        raise ShadowIntegrationError("research_closure_pandora_overclaim")
    if arena.get("head_sha") != _EXPECTED_ARENA_HEAD:
        raise ShadowIntegrationError("research_closure_arena_source_mismatch")
    if arena.get("source_identity_bound") is not True:
        raise ShadowIntegrationError("research_closure_arena_source_unbound")
    if arena.get("deployment_proven") is not False or arena.get("runtime_proven") is not False:
        raise ShadowIntegrationError("research_closure_arena_runtime_overclaim")
    if arena.get("runtime_invoked") is not False or arena.get("trading_execution_surface") is not False:
        raise ShadowIntegrationError("research_closure_arena_effect_role_widened")

    contract = receipt.get("research_contract") or {}
    required_true = ("provenance_required", "replay_status_required", "all_trial_denominator_required", "no_signal_service", "no_live_trading", "publication_is_not_authority")
    for key in required_true:
        if contract.get(key) is not True:
            raise ShadowIntegrationError(f"research_closure_contract_missing:{key}")
    return _verify_hash(receipt, "research_plane_sha256", "research_closure_research_hash_mismatch")


def build_unified_shadow_research_closure(
    base_closure: Mapping[str, Any],
    research_receipt: Mapping[str, Any],
    *,
    closed_at: str,
) -> dict[str, Any]:
    """Extend the no-effect closure with an optional, non-voting research/simulation plane."""
    closure_sha, effective_gate, effective_action = _verify_base_closure(base_closure)
    transaction_sha = str(base_closure.get("transaction_sha256"))
    research_sha = _verify_research(research_receipt, transaction_sha)

    body = {
        "schema": RESEARCH_CLOSURE_SCHEMA,
        "closed_at": str(closed_at),
        "case_id": base_closure.get("case_id"),
        "transaction_sha256": transaction_sha,
        "base_closure_sha256": closure_sha,
        "research_plane_sha256": research_sha,
        "registered_node_count": base_closure["registered_node_count"],
        "effective_gate": effective_gate,
        "effective_action": effective_action,
        "status": "P0_SHADOW_CLOSED_NO_EFFECT",
        "planes": {
            **dict(base_closure.get("planes") or {}),
            "research_simulation": "BOUND_NON_BLOCKING_NO_RUNTIME",
        },
        "effect_summary": {
            **dict(base_closure.get("effect_summary") or {}),
            "experiment_launch": False,
            "research_publication": False,
            "simulation_runtime": False,
        },
        "research_surface_status": {
            "maworld": "ROLE_BOUND_SOURCE_RUNTIME_UNBOUND",
            "pandora": "ROLE_BOUND_SOURCE_RUNTIME_UNBOUND",
            "sovereign_arena": "SOURCE_IDENTITY_BOUND_DEPLOY_RUNTIME_UNPROVEN",
        },
        "semantics": {
            "research_side_plane_does_not_vote": True,
            "research_side_plane_cannot_widen_effective_gate": True,
            "unbound_optional_research_surface_does_not_block_core_decision": True,
            "unbound_does_not_mean_trusted": True,
            "source_identity_is_not_deployment": True,
            "simulation_result_is_not_execution_permission": True,
            "publication_is_not_authority": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["closure_sha256"] = sha256_obj(body)
    return body

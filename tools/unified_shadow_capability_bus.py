#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_federation import SYSTEM_IDS, system_registry

BASE_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v5"
CAPABILITY_LEDGER_SCHEMA = "bitevo.shadow_capability_influence_ledger.v1"

DECISION_BOUND = {
    "portfolio:control-canter",
    "portfolio:hanri",
    "portfolio:tradingos",
    "portfolio:visionassist",
    "entity:core_v6_3",
    "entity:sct",
    "entity:triaxis",
}
EVIDENCE_GATE = {
    "portfolio:anti-amnesia-gate",
    "portfolio:return-plane-v2",
    "portfolio:continuityos",
    "portfolio:archiveos-core",
    "portfolio:archive-tooling",
    "portfolio:state-authority-plane",
    "portfolio:knowledge-lab",
    "entity:return_broker",
    "entity:knowledge_foundry",
    "entity:durable_memory_kernel",
    "entity:archive_to_core_engine",
    "entity:typed_operational_memory",
    "entity:system_universe_registry",
}
INTERFACE_READ_ONLY = {
    "portfolio:unified-dashboard",
    "portfolio:work-cockpit",
    "entity:universe_hub",
}
COGNITION_SIDE = {
    "portfolio:bitevo-runtime",
    "portfolio:reflex-layer",
    "portfolio:openclaw",
    "portfolio:arbiter-content-engine",
    "portfolio:dtaap",
    "portfolio:sovereign-agent-core",
    "portfolio:gpts-core-sdk",
    "entity:lifeos",
    "entity:mind",
    "entity:pfi_brain_fabric",
    "entity:human_coevolution_layer",
}
RESEARCH_SIDE = {
    "portfolio:sovereign-arena",
    "portfolio:maworld",
    "portfolio:fable-observer",
    "entity:pandora_spatial_runtime",
    "entity:sim_os_pandora_predecessor",
    "entity:forge_foundry",
}
TRADING_ADVISORY = {
    "portfolio:sovereign-api-core-bot",
    "portfolio:arb-radar",
    "portfolio:grid-os",
    "portfolio:delist-drs",
    "portfolio:edge-research-lab",
    "portfolio:claude-bitunix",
    "portfolio:btcusdt-binance-bot",
    "portfolio:confluence-trading-bot",
    "portfolio:max-bitevo-pack",
}
PRODUCT_SERVICE = {
    "portfolio:bitevo-ai-portal",
    "portfolio:crypto-guides",
    "portfolio:inner-circle",
    "portfolio:operator-decision-sprint",
    "portfolio:ai-agent-reliability-audit",
    "portfolio:ai-client-hunter",
    "portfolio:blockchain-forensics-osint",
    "entity:physical_ai_cosmos",
}
NONTRADING_PARKED = {
    "portfolio:parasite-killer",
    "portfolio:parasite-hunter-game",
    "portfolio:amora",
    "portfolio:amora-token",
    "portfolio:rtf-starcoin",
}
EXECUTOR_DISABLED = {"entity:executor_network"}

INFLUENCE_CLASSES = {
    "DECISION_BOUND_NON_EXECUTING": DECISION_BOUND,
    "EVIDENCE_GATE_NON_VOTING": EVIDENCE_GATE,
    "INTERFACE_READ_ONLY": INTERFACE_READ_ONLY,
    "COGNITION_SIDE_ACCOUNTED": COGNITION_SIDE,
    "RESEARCH_SIDE_NON_VOTING": RESEARCH_SIDE,
    "TRADING_ADVISORY_ACCOUNTED": TRADING_ADVISORY,
    "PRODUCT_SERVICE_ACCOUNTED": PRODUCT_SERVICE,
    "NONTRADING_OR_PARKED": NONTRADING_PARKED,
    "EXECUTOR_DISABLED": EXECUTOR_DISABLED,
}

# A contract binding means the P0 composition has an explicit typed boundary for the role.
# It is deliberately weaker than source identity, deployment or runtime proof.
P0_CONTRACT_BOUND = {
    "portfolio:control-canter",
    "portfolio:anti-amnesia-gate",
    "portfolio:continuityos",
    "portfolio:archiveos-core",
    "portfolio:archive-tooling",
    "portfolio:state-authority-plane",
    "portfolio:hanri",
    "portfolio:tradingos",
    "portfolio:sovereign-arena",
    "portfolio:maworld",
    "portfolio:visionassist",
    "entity:core_v6_3",
    "entity:return_broker",
    "entity:knowledge_foundry",
    "entity:durable_memory_kernel",
    "entity:pandora_spatial_runtime",
    "entity:sct",
    "entity:triaxis",
}


def _assert_influence_partition() -> None:
    assigned = [node for nodes in INFLUENCE_CLASSES.values() for node in nodes]
    if len(assigned) != len(set(assigned)):
        raise RuntimeError("capability_influence_duplicate_node")
    missing = sorted(set(SYSTEM_IDS) - set(assigned))
    unknown = sorted(set(assigned) - set(SYSTEM_IDS))
    if missing or unknown:
        raise RuntimeError(
            "capability_influence_coverage_mismatch:missing=" + ",".join(missing) + ";unknown=" + ",".join(unknown)
        )


_assert_influence_partition()


def _verify_base_closure(closure: Mapping[str, Any]) -> tuple[str, str, str, str]:
    if not isinstance(closure, Mapping) or closure.get("schema") != BASE_CLOSURE_SCHEMA:
        raise ShadowIntegrationError("capability_bus_wrong_base_schema")
    if closure.get("registered_node_count") != len(SYSTEM_IDS):
        raise ShadowIntegrationError("capability_bus_registry_count_mismatch")
    safety = closure.get("safety")
    if not isinstance(safety, Mapping):
        raise ShadowIntegrationError("capability_bus_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"capability_bus_unsafe_base:{key}")
    effects = closure.get("effect_summary")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("capability_bus_base_effect_boundary_breached")
    gate = closure.get("effective_gate")
    action = closure.get("effective_action")
    if gate not in {"PASS_SHADOW", "HOLD"}:
        raise ShadowIntegrationError("capability_bus_effective_gate_invalid")
    if gate == "HOLD" and action != "WAIT":
        raise ShadowIntegrationError("capability_bus_hold_must_wait")
    closure_sha = sha256_obj({k: v for k, v in closure.items() if k != "closure_sha256"})
    if closure.get("closure_sha256") != closure_sha:
        raise ShadowIntegrationError("capability_bus_base_hash_mismatch")
    return str(closure["closure_sha256"]), str(closure.get("transaction_sha256")), str(gate), str(action)


def _class_for(node_id: str) -> str:
    for name, members in INFLUENCE_CLASSES.items():
        if node_id in members:
            return name
    raise ShadowIntegrationError("capability_bus_unclassified_node")


def build_shadow_capability_influence_ledger(
    base_closure: Mapping[str, Any],
    *,
    generated_at: str,
) -> dict[str, Any]:
    """Assign every System Universe node exactly one influence class without granting effects."""
    closure_sha, transaction_sha, gate, action = _verify_base_closure(base_closure)
    registry = {str(row["node_id"]): row for row in system_registry()}
    if set(registry) != set(SYSTEM_IDS):
        raise ShadowIntegrationError("capability_bus_registry_identity_mismatch")

    rows = []
    for node_id in SYSTEM_IDS:
        influence_class = _class_for(node_id)
        row = registry[node_id]
        may_influence_typed_decision = influence_class == "DECISION_BOUND_NON_EXECUTING"
        may_narrow_gate = influence_class in {"DECISION_BOUND_NON_EXECUTING", "EVIDENCE_GATE_NON_VOTING"}
        if influence_class in {"INTERFACE_READ_ONLY", "COGNITION_SIDE_ACCOUNTED", "RESEARCH_SIDE_NON_VOTING", "TRADING_ADVISORY_ACCOUNTED", "PRODUCT_SERVICE_ACCOUNTED", "NONTRADING_OR_PARKED"}:
            may_narrow_gate = False
        if influence_class == "EXECUTOR_DISABLED":
            may_narrow_gate = False

        rows.append(
            {
                "node_id": node_id,
                "display_name": str(row["display_name"]),
                "registry_participation": str(row["default_participation"]),
                "influence_class": influence_class,
                "p0_contract_bound": node_id in P0_CONTRACT_BOUND,
                "source_identity_proven_by_ledger": False,
                "runtime_proven_by_ledger": False,
                "external_runtime_invoked": False,
                "may_influence_typed_decision": may_influence_typed_decision,
                "may_narrow_gate_if_typed_contract_allows": may_narrow_gate,
                "may_widen_gate": False,
                "trading_vote": False,
                "effect_authority": "NONE",
            }
        )

    class_counts = {
        name: sum(1 for row in rows if row["influence_class"] == name)
        for name in INFLUENCE_CLASSES
    }
    body = {
        "schema": CAPABILITY_LEDGER_SCHEMA,
        "generated_at": str(generated_at),
        "source_closure_sha256": closure_sha,
        "source_transaction_sha256": transaction_sha,
        "case_id": base_closure.get("case_id"),
        "effective_gate": gate,
        "effective_action": action,
        "registered_node_count": len(rows),
        "all_nodes_assigned_exactly_once": True,
        "registry_sha256": sha256_obj(system_registry()),
        "class_counts": class_counts,
        "nodes": tuple(rows),
        "bus_rules": {
            "decision_is_not_majority_vote": True,
            "typed_contract_required_for_influence": True,
            "accounted_only_node_cannot_change_decision": True,
            "research_side_plane_cannot_vote": True,
            "product_service_plane_cannot_vote": True,
            "trading_advisory_is_not_execution_authority": True,
            "evidence_gate_can_only_narrow_when_contract_bound": True,
            "no_node_can_widen_existing_hold": True,
            "executor_is_separate_disabled_boundary": True,
        },
        "effects": {
            "runtime_invocation": False,
            "tool_call": False,
            "current_truth_apply": False,
            "knowledge_write": False,
            "memory_write": False,
            "external_message": False,
            "signal": False,
            "order": False,
            "capital_effect": False,
        },
        "semantics": {
            "registry_membership_is_not_influence": True,
            "active_shadow_is_not_runtime": True,
            "contract_binding_is_not_source_or_runtime_proof": True,
            "advisory_capability_is_not_permission": True,
            "commercial_relevance_is_not_decision_authority": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["capability_ledger_sha256"] = sha256_obj(body)
    return body

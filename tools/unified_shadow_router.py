#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_federation import SYSTEM_IDS, system_registry

ROUTE_SCHEMA = "bitevo.unified_shadow_route.v1"

PLANE_ORDER = (
    "AUTHORITY_AND_INTERFACE",
    "EVIDENCE_AND_CONTINUITY",
    "COGNITION_AND_PERSONALIZATION",
    "PERCEPTION_SIMULATION_AND_AUDIT",
    "TRADING_INTELLIGENCE",
    "PRODUCT_AND_SERVICE_CAPABILITIES",
    "NON_TRADING_OR_PARKED",
    "EXECUTOR_BOUNDARY",
)

PLANE_MEMBERS: dict[str, tuple[str, ...]] = {
    "AUTHORITY_AND_INTERFACE": (
        "portfolio:control-canter",
        "portfolio:anti-amnesia-gate",
        "portfolio:state-authority-plane",
        "portfolio:unified-dashboard",
        "portfolio:work-cockpit",
        "portfolio:hanri",
        "entity:universe_hub",
        "entity:core_v6_3",
        "entity:system_universe_registry",
    ),
    "EVIDENCE_AND_CONTINUITY": (
        "portfolio:return-plane-v2",
        "portfolio:continuityos",
        "portfolio:archiveos-core",
        "portfolio:archive-tooling",
        "portfolio:knowledge-lab",
        "entity:return_broker",
        "entity:knowledge_foundry",
        "entity:durable_memory_kernel",
        "entity:archive_to_core_engine",
        "entity:typed_operational_memory",
    ),
    "COGNITION_AND_PERSONALIZATION": (
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
        "entity:sct",
        "entity:human_coevolution_layer",
    ),
    "PERCEPTION_SIMULATION_AND_AUDIT": (
        "portfolio:sovereign-arena",
        "portfolio:maworld",
        "portfolio:visionassist",
        "portfolio:fable-observer",
        "entity:pandora_spatial_runtime",
        "entity:sim_os_pandora_predecessor",
        "entity:forge_foundry",
        "entity:triaxis",
    ),
    "TRADING_INTELLIGENCE": (
        "portfolio:tradingos",
        "portfolio:sovereign-api-core-bot",
        "portfolio:arb-radar",
        "portfolio:grid-os",
        "portfolio:delist-drs",
        "portfolio:edge-research-lab",
        "portfolio:claude-bitunix",
        "portfolio:btcusdt-binance-bot",
        "portfolio:confluence-trading-bot",
        "portfolio:max-bitevo-pack",
    ),
    "PRODUCT_AND_SERVICE_CAPABILITIES": (
        "portfolio:bitevo-ai-portal",
        "portfolio:crypto-guides",
        "portfolio:inner-circle",
        "portfolio:operator-decision-sprint",
        "portfolio:ai-agent-reliability-audit",
        "portfolio:ai-client-hunter",
        "portfolio:blockchain-forensics-osint",
        "entity:physical_ai_cosmos",
    ),
    "NON_TRADING_OR_PARKED": (
        "portfolio:parasite-killer",
        "portfolio:parasite-hunter-game",
        "portfolio:amora",
        "portfolio:amora-token",
        "portfolio:rtf-starcoin",
    ),
    "EXECUTOR_BOUNDARY": (
        "entity:executor_network",
    ),
}


def _assert_plane_coverage() -> None:
    assigned = tuple(node for plane in PLANE_ORDER for node in PLANE_MEMBERS[plane])
    if len(assigned) != len(set(assigned)):
        raise RuntimeError("unified_shadow_router_duplicate_node")
    missing = sorted(set(SYSTEM_IDS) - set(assigned))
    unknown = sorted(set(assigned) - set(SYSTEM_IDS))
    if missing or unknown:
        raise RuntimeError(
            "unified_shadow_router_coverage_mismatch:missing=" + ",".join(missing) + ";unknown=" + ",".join(unknown)
        )


_assert_plane_coverage()


def _registry_by_id() -> dict[str, Mapping[str, Any]]:
    return {str(row["node_id"]): row for row in system_registry()}


def build_trade_case_route(*, case_id: str, case_sha256: str) -> dict[str, Any]:
    """Build a deterministic no-effect route plan across the entire 63-node System Universe.

    The plan distinguishes accounting from invocation. Only nodes whose registry participation is
    ACTIVE_SHADOW enter `active_nodes`; all others remain visible in `accounted_noninvoked_nodes`.
    No external runtime is called by this function.
    """
    registry = _registry_by_id()
    if set(registry) != set(SYSTEM_IDS):
        raise ShadowIntegrationError("route_registry_coverage_mismatch")

    planes = []
    all_active = []
    all_noninvoked = []
    for plane in PLANE_ORDER:
        nodes = []
        for node_id in PLANE_MEMBERS[plane]:
            row = registry[node_id]
            participation = str(row["default_participation"])
            invoked = participation == "ACTIVE_SHADOW"
            node = {
                "node_id": node_id,
                "display_name": str(row["display_name"]),
                "participation": participation,
                "invoked_in_offline_composition": invoked,
                "external_runtime_called": False,
                "execution_authority": "NONE",
            }
            nodes.append(node)
            (all_active if invoked else all_noninvoked).append(node_id)
        planes.append({"plane": plane, "nodes": tuple(nodes)})

    body = {
        "schema": ROUTE_SCHEMA,
        "case_id": str(case_id),
        "case_sha256": str(case_sha256),
        "plane_order": PLANE_ORDER,
        "planes": tuple(planes),
        "registered_node_count": len(SYSTEM_IDS),
        "all_nodes_assigned_exactly_once": True,
        "active_nodes": tuple(all_active),
        "accounted_noninvoked_nodes": tuple(all_noninvoked),
        "executor_boundary": {
            "node_id": "entity:executor_network",
            "enabled": False,
            "reason": "P0_SHADOW_NO_EFFECT",
        },
        "semantics": {
            "accounted_is_not_invoked": True,
            "offline_composition_is_not_external_runtime": True,
            "inactive_case_capability_is_not_deleted_from_system": True,
            "route_plan_creates_no_permission": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["route_sha256"] = sha256_obj(body)
    return body

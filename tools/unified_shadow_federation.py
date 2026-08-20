#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from tools.tradingos_shadow_integration import (
    DECISION_PACKET_SCHEMA,
    SHADOW_SAFETY,
    ShadowIntegrationError,
    sha256_obj,
    validate_trade_case,
)

FEDERATION_SCHEMA = "bitevo.unified_shadow_federation.v2"
CONTRIBUTION_SCHEMA = "bitevo.system_contribution.v2"
PORTFOLIO_SOURCE_CLASS = "PROJECT_GATE_REGISTRY_V2_NON_AUTHORITY_PLANNING_CANDIDATE"
EXTENDED_SOURCE_CLASS = "SYSTEM_UNIVERSE_EXTENDED_ENTITY_REGISTER_CANDIDATE"

PARTICIPATION_STATES = {
    "ACTIVE_SHADOW",
    "REGISTERED_ONLY",
    "NOT_APPLICABLE",
    "UNRESOLVED_FAMILY",
    "PARKED",
}


@dataclass(frozen=True)
class PortfolioLine:
    action_order: int
    project_id: str
    project_name: str
    phase: str
    gate_class: str
    participation: str

    @property
    def node_id(self) -> str:
        return f"portfolio:{self.project_id}"


@dataclass(frozen=True)
class ExtendedEntity:
    entity_id: str
    display_name: str
    entity_type: str
    role: str
    participation: str

    @property
    def node_id(self) -> str:
        return f"entity:{self.entity_id}"


# Exact 44-line candidate planning register, preserved as a separate project-management view.
# Presence here is NOT a claim that any line is live, canonical, deployed, paid or authoritative.
PORTFOLIO_44: tuple[PortfolioLine, ...] = (
    PortfolioLine(1, "control-canter", "Control canter", "PHASE_0_CONTROL_AND_CONTINUITY", "AUTHORITY_AND_STATE", "ACTIVE_SHADOW"),
    PortfolioLine(2, "anti-amnesia-gate", "ANTI_AMNESIA_GATE v1", "PHASE_0_CONTROL_AND_CONTINUITY", "BYTE_GIT_RUNTIME", "ACTIVE_SHADOW"),
    PortfolioLine(3, "return-plane-v2", "Return Plane V2", "PHASE_0_CONTROL_AND_CONTINUITY", "BYTE_GIT_RUNTIME", "ACTIVE_SHADOW"),
    PortfolioLine(4, "continuityos", "ContinuityOS", "PHASE_0_CONTROL_AND_CONTINUITY", "BYTE_GIT_RUNTIME", "ACTIVE_SHADOW"),
    PortfolioLine(5, "archiveos-core", "ArchiveOS Core", "PHASE_0_CONTROL_AND_CONTINUITY", "AUTHORITY_AND_STATE", "ACTIVE_SHADOW"),
    PortfolioLine(6, "archive-tooling", "Archive Tooling", "PHASE_0_CONTROL_AND_CONTINUITY", "BYTE_GIT_RUNTIME", "ACTIVE_SHADOW"),
    PortfolioLine(7, "state-authority-plane", "State Authority Plane", "PHASE_0_CONTROL_AND_CONTINUITY", "AUTHORITY_AND_STATE", "ACTIVE_SHADOW"),
    PortfolioLine(8, "unified-dashboard", "HANRI / Control Center Unified Dashboard", "PHASE_0_CONTROL_AND_CONTINUITY", "BYTE_GIT_RUNTIME", "ACTIVE_SHADOW"),
    PortfolioLine(9, "knowledge-lab", "Knowledge Lab", "PHASE_0_CONTROL_AND_CONTINUITY", "BYTE_GIT_RUNTIME", "ACTIVE_SHADOW"),
    PortfolioLine(10, "work-cockpit", "Work Cockpit / Operator CRM", "PHASE_0_CONTROL_AND_CONTINUITY", "AUTHORITY_AND_STATE", "ACTIVE_SHADOW"),
    PortfolioLine(11, "hanri", "HANRI", "PHASE_0_CONTROL_AND_CONTINUITY", "AUTHORITY_AND_STATE", "ACTIVE_SHADOW"),
    PortfolioLine(12, "bitevo-runtime", "BitEvo Runtime / Cognitive Orchestrator", "PHASE_0_CONTROL_AND_CONTINUITY", "BYTE_GIT_RUNTIME", "ACTIVE_SHADOW"),
    PortfolioLine(13, "reflex-layer", "Reflex Layer", "PHASE_0_CONTROL_AND_CONTINUITY", "BYTE_GIT_RUNTIME", "REGISTERED_ONLY"),
    PortfolioLine(14, "tradingos", "TradingOS", "PHASE_1_CURRENT_PRODUCT_RECOVERY", "LOCKED_EMPIRICAL", "ACTIVE_SHADOW"),
    PortfolioLine(15, "parasite-killer", "Parasite-Killer / OKX NFT Bot", "PHASE_1_CURRENT_PRODUCT_RECOVERY", "BYTE_GIT_RUNTIME", "NOT_APPLICABLE"),
    PortfolioLine(16, "sovereign-api-core-bot", "Sovereign API / Sovereign-Core Bot", "PHASE_1_CURRENT_PRODUCT_RECOVERY", "BYTE_GIT_RUNTIME", "ACTIVE_SHADOW"),
    PortfolioLine(17, "arb-radar", "Arb Radar", "PHASE_1_CURRENT_PRODUCT_RECOVERY", "BYTE_GIT_RUNTIME", "ACTIVE_SHADOW"),
    PortfolioLine(18, "sovereign-arena", "Sovereign Arena", "PHASE_1_CURRENT_PRODUCT_RECOVERY", "BYTE_GIT_RUNTIME", "ACTIVE_SHADOW"),
    PortfolioLine(19, "grid-os", "Grid OS / Grid Mirror", "PHASE_1_CURRENT_PRODUCT_RECOVERY", "BYTE_GIT_RUNTIME", "ACTIVE_SHADOW"),
    PortfolioLine(20, "delist-drs", "Delist EWS / Low-Cap Scanner + DRS", "PHASE_1_CURRENT_PRODUCT_RECOVERY", "BYTE_GIT_RUNTIME", "ACTIVE_SHADOW"),
    PortfolioLine(21, "maworld", "MAWorld", "PHASE_1_CURRENT_PRODUCT_RECOVERY", "BYTE_GIT_RUNTIME", "ACTIVE_SHADOW"),
    PortfolioLine(22, "bitevo-ai-portal", "BitEvo AI Portal", "PHASE_1_CURRENT_PRODUCT_RECOVERY", "BYTE_GIT_RUNTIME", "REGISTERED_ONLY"),
    PortfolioLine(23, "crypto-guides", "Crypto Guides / Knowledge Portal", "PHASE_1_CURRENT_PRODUCT_RECOVERY", "FOCUSED_EXTERNAL_RESEARCH", "REGISTERED_ONLY"),
    PortfolioLine(24, "inner-circle", "Inner Circle / VIP Bot", "PHASE_1_CURRENT_PRODUCT_RECOVERY", "CUSTOMER_PAYMENT_DELIVERY", "REGISTERED_ONLY"),
    PortfolioLine(25, "openclaw", "OpenClaw", "PHASE_1_CURRENT_PRODUCT_RECOVERY", "BYTE_GIT_RUNTIME", "REGISTERED_ONLY"),
    PortfolioLine(26, "arbiter-content-engine", "Arbiter Content Engine / LLM Aggregator", "PHASE_1_CURRENT_PRODUCT_RECOVERY", "BYTE_GIT_RUNTIME", "REGISTERED_ONLY"),
    PortfolioLine(27, "dtaap", "DTaaP — Digital Twin as Platform", "PHASE_1_CURRENT_PRODUCT_RECOVERY", "BYTE_GIT_RUNTIME", "REGISTERED_ONLY"),
    PortfolioLine(28, "sovereign-agent-core", "Sovereign Agent Core", "PHASE_1_CURRENT_PRODUCT_RECOVERY", "BYTE_GIT_RUNTIME", "REGISTERED_ONLY"),
    PortfolioLine(29, "gpts-core-sdk", "GPT-S:CORE SDK", "PHASE_1_CURRENT_PRODUCT_RECOVERY", "BYTE_GIT_RUNTIME", "REGISTERED_ONLY"),
    PortfolioLine(30, "edge-research-lab", "Trading Edge Research / BTC Pressure Lab", "PHASE_2_EMPIRICAL_COMMERCIAL_AND_LEGACY", "LOCKED_EMPIRICAL", "ACTIVE_SHADOW"),
    PortfolioLine(31, "visionassist", "VisionAssist", "PHASE_2_EMPIRICAL_COMMERCIAL_AND_LEGACY", "LOCKED_EMPIRICAL", "ACTIVE_SHADOW"),
    PortfolioLine(32, "claude-bitunix", "Claude Bitunix Evidence Lane", "PHASE_2_EMPIRICAL_COMMERCIAL_AND_LEGACY", "LOCKED_EMPIRICAL", "REGISTERED_ONLY"),
    PortfolioLine(33, "parasite-hunter-game", "Parasite Hunter / AXIOM Game", "PHASE_2_EMPIRICAL_COMMERCIAL_AND_LEGACY", "LOCKED_EMPIRICAL", "NOT_APPLICABLE"),
    PortfolioLine(34, "operator-decision-sprint", "7-Day Operator Decision Sprint", "PHASE_2_EMPIRICAL_COMMERCIAL_AND_LEGACY", "CUSTOMER_PAYMENT_DELIVERY", "REGISTERED_ONLY"),
    PortfolioLine(35, "ai-agent-reliability-audit", "AI-Agent Reliability Audit", "PHASE_2_EMPIRICAL_COMMERCIAL_AND_LEGACY", "CUSTOMER_PAYMENT_DELIVERY", "REGISTERED_ONLY"),
    PortfolioLine(36, "ai-client-hunter", "AI Client Hunter Machine", "PHASE_2_EMPIRICAL_COMMERCIAL_AND_LEGACY", "CUSTOMER_PAYMENT_DELIVERY", "NOT_APPLICABLE"),
    PortfolioLine(37, "blockchain-forensics-osint", "Blockchain Forensics / OSINT Dossiers", "PHASE_2_EMPIRICAL_COMMERCIAL_AND_LEGACY", "CASE_CHAIN_OF_CUSTODY", "NOT_APPLICABLE"),
    PortfolioLine(38, "btcusdt-binance-bot", "BTCUSDT Binance Futures Bot", "PHASE_2_EMPIRICAL_COMMERCIAL_AND_LEGACY", "BYTE_GIT_RUNTIME", "REGISTERED_ONLY"),
    PortfolioLine(39, "confluence-trading-bot", "Confluence Trading Bot", "PHASE_2_EMPIRICAL_COMMERCIAL_AND_LEGACY", "BYTE_GIT_RUNTIME", "REGISTERED_ONLY"),
    PortfolioLine(40, "max-bitevo-pack", "MAX+BitEvo v7 / BitEvo Trading Tools", "PHASE_2_EMPIRICAL_COMMERCIAL_AND_LEGACY", "BYTE_GIT_RUNTIME", "REGISTERED_ONLY"),
    PortfolioLine(41, "fable-observer", "Fable 5 Observer", "PHASE_2_EMPIRICAL_COMMERCIAL_AND_LEGACY", "DEPENDENCY_GATED", "REGISTERED_ONLY"),
    PortfolioLine(42, "amora", "Amora", "PHASE_3_HOLD_OR_ARCHIVE", "HOLD_ARCHIVE", "PARKED"),
    PortfolioLine(43, "amora-token", "$AMORA Token", "PHASE_3_HOLD_OR_ARCHIVE", "HOLD_ARCHIVE", "PARKED"),
    PortfolioLine(44, "rtf-starcoin", "RTF / StarCoin", "PHASE_3_HOLD_OR_ARCHIVE", "HOLD_ARCHIVE", "PARKED"),
)

# Logical entities/programs that are not safely reducible to the 44 project-management lines.
# Namespacing prevents alias laundering (e.g. Return Plane != silently Return Broker).
EXTENDED_ENTITIES: tuple[ExtendedEntity, ...] = (
    ExtendedEntity("universe_hub", "BitEvo Universe Hub", "PUBLIC_SURFACE/INTERFACE", "federated operator shell and projection surface", "ACTIVE_SHADOW"),
    ExtendedEntity("core_v6_3", "GPT-S CORE v6.3", "NORMATIVE_POLICY", "action/risk/fail-closed policy and invariant layer", "ACTIVE_SHADOW"),
    ExtendedEntity("return_broker", "Return Broker", "ENTITY", "transport, custody, deduplication and delivery receipts", "ACTIVE_SHADOW"),
    ExtendedEntity("lifeos", "LifeOS", "ENTITY", "functional continuity and agent-habitat program", "ACTIVE_SHADOW"),
    ExtendedEntity("pandora_spatial_runtime", "Pandora Spatial Runtime", "ENTITY", "simulation/world-runtime program", "REGISTERED_ONLY"),
    ExtendedEntity("sim_os_pandora_predecessor", "Sim-OS / Pandora predecessor", "HISTORICAL_PREDECESSOR", "historical simulation lineage", "REGISTERED_ONLY"),
    ExtendedEntity("forge_foundry", "Forge / Foundry family", "FAMILY", "creation/research family with unresolved entity boundaries", "UNRESOLVED_FAMILY"),
    ExtendedEntity("mind", "MIND", "ENTITY", "falsifiable cognition primitives and error monitoring", "ACTIVE_SHADOW"),
    ExtendedEntity("pfi_brain_fabric", "PFI / Brain / Fabric", "FAMILY", "personal intelligence/research transport/core candidates", "ACTIVE_SHADOW"),
    ExtendedEntity("knowledge_foundry", "Knowledge Foundry", "ENTITY", "source-to-claim-to-contradiction-to-decision evidence graph", "REGISTERED_ONLY"),
    ExtendedEntity("executor_network", "Executor Network", "ENTITY", "bounded executor capability boundary; effects disabled in P0", "ACTIVE_SHADOW"),
    ExtendedEntity("physical_ai_cosmos", "Physical AI / Cosmos", "ENTITY", "future simulation/world-model/robotics integration", "REGISTERED_ONLY"),
    ExtendedEntity("durable_memory_kernel", "Durable Memory Kernel", "ENTITY", "policy-governed durable memory transactions/readback candidate", "REGISTERED_ONLY"),
    ExtendedEntity("system_universe_registry", "R2 System Universe Registry", "ENTITY", "typed identity/relation/supersession registry", "ACTIVE_SHADOW"),
    ExtendedEntity("sct", "Sovereign Cognitive Twin", "ENTITY", "Person/Decision Twin and prospective prediction", "ACTIVE_SHADOW"),
    ExtendedEntity("triaxis", "TRIAXIS", "ENTITY", "independent adversarial epistemic audit", "ACTIVE_SHADOW"),
    ExtendedEntity("archive_to_core_engine", "Archive-to-Core Engine", "COMPONENT", "replayable archive distillation into governed knowledge", "ACTIVE_SHADOW"),
    ExtendedEntity("typed_operational_memory", "Typed Operational Memory", "COMPONENT", "working/episodic/semantic/procedural/outcome/eval memory separation", "ACTIVE_SHADOW"),
    ExtendedEntity("human_coevolution_layer", "Human Coevolution Layer", "COMPONENT", "mirror/coevolution/twin integration program", "ACTIVE_SHADOW"),
)

PORTFOLIO_NODE_IDS = tuple(line.node_id for line in PORTFOLIO_44)
EXTENDED_NODE_IDS = tuple(entity.node_id for entity in EXTENDED_ENTITIES)
SYSTEM_IDS = PORTFOLIO_NODE_IDS + EXTENDED_NODE_IDS

PORTFOLIO_BY_NODE = {line.node_id: line for line in PORTFOLIO_44}
EXTENDED_BY_NODE = {entity.node_id: entity for entity in EXTENDED_ENTITIES}

ACTIVE_TRADING_SPINE = tuple(
    [line.node_id for line in PORTFOLIO_44 if line.participation == "ACTIVE_SHADOW"]
    + [entity.node_id for entity in EXTENDED_ENTITIES if entity.participation == "ACTIVE_SHADOW"]
)


def _assert_static_registry() -> None:
    orders = tuple(line.action_order for line in PORTFOLIO_44)
    if orders != tuple(range(1, 45)):
        raise RuntimeError("portfolio_44_action_order_invariant")
    if len({line.project_id for line in PORTFOLIO_44}) != 44:
        raise RuntimeError("portfolio_44_unique_project_id_invariant")
    if len(SYSTEM_IDS) != len(set(SYSTEM_IDS)):
        raise RuntimeError("federation_node_id_collision")


_assert_static_registry()


def system_registry() -> tuple[dict[str, Any], ...]:
    portfolio_rows = tuple(
        {
            "node_id": line.node_id,
            "registry_view": "CANONICAL_PORTFOLIO_44_CANDIDATE",
            "source_class": PORTFOLIO_SOURCE_CLASS,
            "action_order": line.action_order,
            "project_id": line.project_id,
            "display_name": line.project_name,
            "phase": line.phase,
            "gate_class": line.gate_class,
            "default_participation": line.participation,
        }
        for line in PORTFOLIO_44
    )
    extended_rows = tuple(
        {
            "node_id": entity.node_id,
            "registry_view": "EXTENDED_SYSTEM_UNIVERSE_CANDIDATE",
            "source_class": EXTENDED_SOURCE_CLASS,
            "entity_id": entity.entity_id,
            "display_name": entity.display_name,
            "entity_type": entity.entity_type,
            "role": entity.role,
            "default_participation": entity.participation,
        }
        for entity in EXTENDED_ENTITIES
    )
    return portfolio_rows + extended_rows


def _default_participation(node_id: str) -> str:
    if node_id in PORTFOLIO_BY_NODE:
        return PORTFOLIO_BY_NODE[node_id].participation
    if node_id in EXTENDED_BY_NODE:
        return EXTENDED_BY_NODE[node_id].participation
    raise ShadowIntegrationError("unknown_federation_system")


def _display_name(node_id: str) -> str:
    if node_id in PORTFOLIO_BY_NODE:
        return PORTFOLIO_BY_NODE[node_id].project_name
    if node_id in EXTENDED_BY_NODE:
        return EXTENDED_BY_NODE[node_id].display_name
    raise ShadowIntegrationError("unknown_federation_system")


def _safe_contribution_safety(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise ShadowIntegrationError("federation_contribution_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if value.get(key) != expected or type(value.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"unsafe_federation_contribution:{key}")


def build_system_contribution(
    *,
    system_id: str,
    case_id: str,
    participation: str,
    summary: str,
    evidence_refs: Sequence[str] = (),
    truth_class: str = "FIXTURE_ONLY",
) -> dict[str, Any]:
    if system_id not in SYSTEM_IDS:
        raise ShadowIntegrationError("unknown_federation_system")
    if participation not in PARTICIPATION_STATES:
        raise ShadowIntegrationError("invalid_federation_participation")
    if not isinstance(summary, str) or not summary.strip():
        raise ShadowIntegrationError("federation_summary_required")
    refs = tuple(str(ref).strip() for ref in evidence_refs if str(ref).strip())
    body = {
        "schema": CONTRIBUTION_SCHEMA,
        "system_id": system_id,
        "case_id": str(case_id).strip(),
        "participation": participation,
        "summary": summary.strip(),
        "evidence_refs": refs,
        "truth_class": str(truth_class).strip() or "UNKNOWN",
        "safety": dict(SHADOW_SAFETY),
    }
    body["contribution_sha256"] = sha256_obj(body)
    return body


def build_default_shadow_contributions(
    *,
    case_id: str,
    case_sha256: str,
    packet_sha256: str,
) -> tuple[dict[str, Any], ...]:
    """Account for the exact 44-line portfolio plus extended universe in an offline fixture.

    ACTIVE_SHADOW means the node has a role in the composition proof. It does NOT mean an
    external runtime was invoked. All rows remain FIXTURE_ONLY until independently replaced
    by source/runtime receipts.
    """
    refs = (f"trade_case:{case_sha256}", f"decision_packet:{packet_sha256}")
    rows = []
    for node_id in SYSTEM_IDS:
        participation = _default_participation(node_id)
        rows.append(
            build_system_contribution(
                system_id=node_id,
                case_id=case_id,
                participation=participation,
                summary=f"P0 offline federation accounting: {_display_name(node_id)}",
                evidence_refs=refs if participation == "ACTIVE_SHADOW" else (),
                truth_class="FIXTURE_ONLY",
            )
        )
    return tuple(rows)


def _validate_contribution(row: Mapping[str, Any], case_id: str) -> dict[str, Any]:
    if not isinstance(row, Mapping) or row.get("schema") != CONTRIBUTION_SCHEMA:
        raise ShadowIntegrationError("wrong_federation_contribution_schema")
    system_id = row.get("system_id")
    if system_id not in SYSTEM_IDS:
        raise ShadowIntegrationError("unknown_federation_system")
    if row.get("case_id") != case_id:
        raise ShadowIntegrationError("federation_case_mismatch")
    if row.get("participation") not in PARTICIPATION_STATES:
        raise ShadowIntegrationError("invalid_federation_participation")
    _safe_contribution_safety(row.get("safety", {}))
    expected = sha256_obj({k: v for k, v in row.items() if k != "contribution_sha256"})
    if row.get("contribution_sha256") != expected:
        raise ShadowIntegrationError("federation_contribution_hash_mismatch")
    return dict(row)


def build_unified_shadow_receipt(
    trade_case: Mapping[str, Any],
    decision_packet: Mapping[str, Any],
    contributions: Sequence[Mapping[str, Any]],
    *,
    generated_at: str,
) -> dict[str, Any]:
    case = validate_trade_case(trade_case)
    if not isinstance(decision_packet, Mapping) or decision_packet.get("schema") != DECISION_PACKET_SCHEMA:
        raise ShadowIntegrationError("wrong_decision_packet_schema")
    if decision_packet.get("case_id") != case["case_id"]:
        raise ShadowIntegrationError("decision_packet_case_mismatch")
    if decision_packet.get("case_sha256") != case["case_sha256"]:
        raise ShadowIntegrationError("decision_packet_case_hash_mismatch")
    packet_safety = decision_packet.get("safety")
    if not isinstance(packet_safety, Mapping):
        raise ShadowIntegrationError("decision_packet_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if packet_safety.get(key) != expected or type(packet_safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"unsafe_decision_packet:{key}")

    validated_rows = [_validate_contribution(row, case["case_id"]) for row in contributions]
    ids = [row["system_id"] for row in validated_rows]
    if len(ids) != len(set(ids)):
        raise ShadowIntegrationError("duplicate_federation_system_contribution")
    if set(ids) != set(SYSTEM_IDS):
        missing = sorted(set(SYSTEM_IDS) - set(ids))
        unknown = sorted(set(ids) - set(SYSTEM_IDS))
        raise ShadowIntegrationError(
            "federation_registry_coverage_mismatch:missing=" + ",".join(missing) + ";unknown=" + ",".join(unknown)
        )

    by_id = {row["system_id"]: row for row in validated_rows}
    wrong_active = [
        system_id for system_id in ACTIVE_TRADING_SPINE
        if by_id[system_id]["participation"] != "ACTIVE_SHADOW"
    ]
    if wrong_active:
        raise ShadowIntegrationError("federation_active_spine_missing:" + ",".join(sorted(wrong_active)))

    active = tuple(node_id for node_id in SYSTEM_IDS if by_id[node_id]["participation"] == "ACTIVE_SHADOW")
    registered_only = tuple(node_id for node_id in SYSTEM_IDS if by_id[node_id]["participation"] == "REGISTERED_ONLY")
    not_applicable = tuple(node_id for node_id in SYSTEM_IDS if by_id[node_id]["participation"] == "NOT_APPLICABLE")
    parked = tuple(node_id for node_id in SYSTEM_IDS if by_id[node_id]["participation"] == "PARKED")
    unresolved = tuple(node_id for node_id in SYSTEM_IDS if by_id[node_id]["participation"] == "UNRESOLVED_FAMILY")

    body = {
        "schema": FEDERATION_SCHEMA,
        "generated_at": str(generated_at).strip(),
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "decision_packet_sha256": decision_packet.get("packet_sha256"),
        "portfolio_44_count": len(PORTFOLIO_44),
        "extended_entity_count": len(EXTENDED_ENTITIES),
        "registry_count": len(SYSTEM_IDS),
        "portfolio_44_exact_action_order": tuple(line.action_order for line in PORTFOLIO_44),
        "registry_sha256": sha256_obj(system_registry()),
        "all_registered_systems_accounted_for": True,
        "active_trading_spine": active,
        "registered_only": registered_only,
        "not_applicable_to_this_trade_case": not_applicable,
        "parked": parked,
        "unresolved_families": unresolved,
        "contribution_hashes": {
            node_id: by_id[node_id]["contribution_sha256"] for node_id in SYSTEM_IDS
        },
        "semantic_boundaries": {
            "portfolio_44_is_planning_candidate_not_authority": True,
            "extended_universe_is_separate_from_portfolio_view": True,
            "alias_is_not_entity_identity": True,
            "registered_does_not_mean_live": True,
            "registered_does_not_mean_current_authority": True,
            "active_shadow_does_not_mean_external_runtime_called": True,
            "not_applicable_does_not_mean_architecturally_unimportant": True,
            "prediction_does_not_create_permission": True,
            "one_federation_does_not_collapse_subsystem_ownership": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["federation_sha256"] = sha256_obj(body)
    return body

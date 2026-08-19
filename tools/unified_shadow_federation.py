#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from tools.tradingos_shadow_integration import (
    DECISION_PACKET_SCHEMA,
    SHADOW_SAFETY,
    TRADE_CASE_SCHEMA,
    ShadowIntegrationError,
    sha256_obj,
    validate_trade_case,
)

FEDERATION_SCHEMA = "bitevo.unified_shadow_federation.v1"
CONTRIBUTION_SCHEMA = "bitevo.system_contribution.v1"

PARTICIPATION_STATES = {
    "ACTIVE_SHADOW",
    "REGISTERED_ONLY",
    "NOT_APPLICABLE",
    "UNRESOLVED_FAMILY",
    "PARKED",
}


@dataclass(frozen=True)
class SystemSpec:
    system_id: str
    display_name: str
    domain: str
    role: str
    default_participation: str


# Evidence-bounded system universe. A registry entry does not claim that a runtime is live,
# canonical, deployed, or currently healthy. It only fixes identity and intended role for
# the P0 composition proof.
SYSTEM_SPECS: tuple[SystemSpec, ...] = (
    SystemSpec("universe_hub", "BitEvo Universe Hub", "interface", "federated operator shell and projection surface", "ACTIVE_SHADOW"),
    SystemSpec("control_center", "Control Center", "governance", "accepted intent, current-truth projection, approvals and effect authority", "ACTIVE_SHADOW"),
    SystemSpec("hanri", "HANRI", "governance", "freshness, contradiction, attention and bounded recommendation layer", "ACTIVE_SHADOW"),
    SystemSpec("anti_amnesia_gate", "ANTI_AMNESIA_GATE", "governance", "deterministic admission and closure gate", "ACTIVE_SHADOW"),
    SystemSpec("work_cockpit", "Work Cockpit", "interface", "operator case surface", "ACTIVE_SHADOW"),
    SystemSpec("unified_dashboard", "Unified Dashboard", "interface", "read-only portfolio and case projection", "ACTIVE_SHADOW"),
    SystemSpec("core_v6_3", "GPT-S CORE v6.3", "policy", "normative constraints, action classes and fail-closed policy", "ACTIVE_SHADOW"),
    SystemSpec("continuityos", "ContinuityOS", "continuity", "append-only events, checkpoints, lineage, handoff and replay", "ACTIVE_SHADOW"),
    SystemSpec("archiveos", "ArchiveOS", "evidence", "raw historical source and evidence vault", "ACTIVE_SHADOW"),
    SystemSpec("archive_tooling", "Archive Tooling", "evidence", "archive processing and exact-byte support tooling", "ACTIVE_SHADOW"),
    SystemSpec("return_broker", "Return Broker", "transport", "transport, custody, deduplication and delivery receipts", "ACTIVE_SHADOW"),
    SystemSpec("knowledge_lab", "Knowledge Lab", "knowledge", "research derivatives and evidence-linked knowledge", "ACTIVE_SHADOW"),
    SystemSpec("lifeos", "LifeOS", "continuity", "functional continuity and agent-habitat program", "ACTIVE_SHADOW"),
    SystemSpec("bitevo_runtime", "BitEvo Runtime", "runtime-adapter", "thin cross-runtime intent and receipt adapter", "ACTIVE_SHADOW"),
    SystemSpec("maworld", "MAWorld", "experiment", "isolated reproducible multi-agent and blind-protocol chamber", "ACTIVE_SHADOW"),
    SystemSpec("mind", "MIND", "cognition", "cognitive program/context provider", "ACTIVE_SHADOW"),
    SystemSpec("pfi_brain_fabric", "PFI / Brain / Fabric", "cognition", "personal intelligence and brain/fabric context", "ACTIVE_SHADOW"),
    SystemSpec("executor_network", "Executor Network", "execution", "executor capability boundary; disabled for P0 effects", "ACTIVE_SHADOW"),
    SystemSpec("pandora", "Pandora", "simulation", "visual programmable/runtime and simulation candidate", "ACTIVE_SHADOW"),
    SystemSpec("sim_os_pandora_predecessor", "Sim-OS / Pandora predecessor", "simulation", "historical simulation lineage", "REGISTERED_ONLY"),
    SystemSpec("visionassist", "VisionAssist", "perception", "visual-semantic market observation and decision-audit evidence", "ACTIVE_SHADOW"),
    SystemSpec("forge_foundry", "Forge / Foundry family", "creation", "unresolved creation/research family", "UNRESOLVED_FAMILY"),
    SystemSpec("knowledge_foundry", "Knowledge Foundry", "knowledge", "research/content synthesis line", "REGISTERED_ONLY"),
    SystemSpec("fable_observer", "Fable Observer", "review", "sealed advisory reviewer without authority", "ACTIVE_SHADOW"),
    SystemSpec("sct", "Sovereign Cognitive Twin", "human-model", "Person/Decision Twin and prospective prediction", "ACTIVE_SHADOW"),
    SystemSpec("triaxis", "TRIAXIS", "audit", "independent adversarial epistemic audit", "ACTIVE_SHADOW"),
    SystemSpec("sovereign_arena", "Sovereign Arena", "research", "decision/research arena and comparative evaluation surface", "ACTIVE_SHADOW"),
    SystemSpec("tradingos", "TradingOS", "trading", "market reasoning, thesis, risk and decision packet orchestration", "ACTIVE_SHADOW"),
    SystemSpec("edge_research_lab", "Edge Research Lab", "trading-research", "edge research contribution", "ACTIVE_SHADOW"),
    SystemSpec("grid_os", "Grid OS", "trading-research", "grid-strategy family research contribution", "ACTIVE_SHADOW"),
    SystemSpec("arb_radar", "Arb Radar", "trading-research", "arbitrage/radar evidence contribution", "ACTIVE_SHADOW"),
    SystemSpec("delist_drs", "Delist DRS", "trading-research", "delist/low-cap risk intelligence contribution", "ACTIVE_SHADOW"),
    SystemSpec("parasite_killer", "Parasite-Killer", "marketplace-research", "marketplace/NFT strategy lineage", "NOT_APPLICABLE"),
    SystemSpec("okx_nft_bot", "OKX NFT Bot", "marketplace-runtime", "bounded marketplace execution line", "NOT_APPLICABLE"),
    SystemSpec("sovereign_api", "Sovereign API", "interface", "bounded API contract surface", "ACTIVE_SHADOW"),
    SystemSpec("physical_ai_cosmos", "Physical AI / Cosmos", "future-research", "future physical-AI program", "REGISTERED_ONLY"),
    SystemSpec("ai_agent_reliability_audit", "AI-Agent Reliability Audit", "commercial", "commercial reliability assessment surface", "REGISTERED_ONLY"),
    SystemSpec("operator_decision_sprint", "7-Day Operator Decision Sprint", "commercial", "operator decision improvement service", "REGISTERED_ONLY"),
    SystemSpec("blockchain_forensics", "Blockchain Forensics / OSINT", "commercial", "forensics and investigation service", "NOT_APPLICABLE"),
    SystemSpec("inner_circle_vip", "Inner Circle / VIP", "commercial", "subscriber decision-support surface", "REGISTERED_ONLY"),
    SystemSpec("bitevo_portal", "BitEvo Portal", "commercial-interface", "public/commercial portal", "REGISTERED_ONLY"),
    SystemSpec("ai_skill_lab", "AI Skill Lab", "commercial-product", "AI education/product line", "NOT_APPLICABLE"),
    SystemSpec("crypto_guides", "Crypto Guides", "commercial-product", "crypto education/guides product", "REGISTERED_ONLY"),
    SystemSpec("amora", "Amora", "parked", "parked/historical product line", "PARKED"),
    SystemSpec("amora_token", "$AMORA", "parked", "parked/historical token line", "PARKED"),
    SystemSpec("dtaap", "DTaaP", "parked", "parked/historical line", "PARKED"),
    SystemSpec("rtf_starcoin", "RTF / StarCoin", "parked", "parked/historical line", "PARKED"),
    SystemSpec("axiom_game", "AXIOM Game", "parked", "bounded creative/game line", "PARKED"),
    SystemSpec("legacy_bots_toolkits", "Legacy bots/toolkits", "historical", "historical implementation/tooling family", "PARKED"),
)

SYSTEM_IDS = tuple(spec.system_id for spec in SYSTEM_SPECS)
SYSTEM_BY_ID = {spec.system_id: spec for spec in SYSTEM_SPECS}

ACTIVE_TRADING_SPINE = tuple(
    spec.system_id for spec in SYSTEM_SPECS if spec.default_participation == "ACTIVE_SHADOW"
)


def system_registry() -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "system_id": spec.system_id,
            "display_name": spec.display_name,
            "domain": spec.domain,
            "role": spec.role,
            "default_participation": spec.default_participation,
        }
        for spec in SYSTEM_SPECS
    )


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
    if system_id not in SYSTEM_BY_ID:
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
    """Build explicit P0 fixture contributions for every registered system.

    This helper is for offline composition tests only. It does not claim live runtime use.
    """
    refs = (f"trade_case:{case_sha256}", f"decision_packet:{packet_sha256}")
    rows = []
    for spec in SYSTEM_SPECS:
        rows.append(
            build_system_contribution(
                system_id=spec.system_id,
                case_id=case_id,
                participation=spec.default_participation,
                summary=f"P0 offline federation role: {spec.role}",
                evidence_refs=refs if spec.default_participation == "ACTIVE_SHADOW" else (),
                truth_class="FIXTURE_ONLY",
            )
        )
    return tuple(rows)


def _validate_contribution(row: Mapping[str, Any], case_id: str) -> dict[str, Any]:
    if not isinstance(row, Mapping) or row.get("schema") != CONTRIBUTION_SCHEMA:
        raise ShadowIntegrationError("wrong_federation_contribution_schema")
    system_id = row.get("system_id")
    if system_id not in SYSTEM_BY_ID:
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

    active = tuple(system_id for system_id in SYSTEM_IDS if by_id[system_id]["participation"] == "ACTIVE_SHADOW")
    registered_only = tuple(system_id for system_id in SYSTEM_IDS if by_id[system_id]["participation"] == "REGISTERED_ONLY")
    not_applicable = tuple(system_id for system_id in SYSTEM_IDS if by_id[system_id]["participation"] == "NOT_APPLICABLE")
    parked = tuple(system_id for system_id in SYSTEM_IDS if by_id[system_id]["participation"] == "PARKED")
    unresolved = tuple(system_id for system_id in SYSTEM_IDS if by_id[system_id]["participation"] == "UNRESOLVED_FAMILY")

    body = {
        "schema": FEDERATION_SCHEMA,
        "generated_at": str(generated_at).strip(),
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "decision_packet_sha256": decision_packet.get("packet_sha256"),
        "registry_count": len(SYSTEM_IDS),
        "registry_sha256": sha256_obj(system_registry()),
        "all_registered_systems_accounted_for": True,
        "active_trading_spine": active,
        "registered_only": registered_only,
        "not_applicable_to_this_trade_case": not_applicable,
        "parked": parked,
        "unresolved_families": unresolved,
        "contribution_hashes": {
            system_id: by_id[system_id]["contribution_sha256"] for system_id in SYSTEM_IDS
        },
        "semantic_boundaries": {
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

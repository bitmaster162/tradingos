#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj

BASE_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v8"
COGNITION_RECEIPT_SCHEMA = "bitevo.shadow_cognition_proposal_receipt.v1"
COGNITION_LEDGER_SCHEMA = "bitevo.shadow_cognition_proposal_ledger.v1"

COGNITION_NODES = (
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
)

COGNITION_SPECS: dict[str, dict[str, Any]] = {
    "portfolio:bitevo-runtime": {
        "role": "THIN_COGNITIVE_ORCHESTRATION_ADAPTER_CANDIDATE",
        "current_posture": "ACTIVE_INTERNAL_STATUS_RECAPTURE",
        "proof_fields": (
            "source_identity_verified",
            "live_api_receipt_verified",
            "deployment_receipt_verified",
            "integration_receipt_verified",
            "budget_retry_timeout_semantics_verified",
            "tool_security_boundary_verified",
        ),
        "forbidden_ownership": ("current_truth", "durable_memory", "effect_authority"),
    },
    "portfolio:reflex-layer": {
        "role": "MONITORING_AND_BOUNDED_RESPONSE_PROPOSAL_PROFILE",
        "current_posture": "RESEARCH_ONLY_RUNTIME_RECAPTURE_REQUIRED",
        "proof_fields": (
            "source_identity_verified",
            "live_runner_verified",
            "observation_provenance_verified",
            "deterministic_rule_profile_verified",
            "receipt_lineage_verified",
            "kill_boundary_verified",
        ),
        "forbidden_ownership": ("effect_execution", "trade_authority", "current_truth"),
    },
    "portfolio:openclaw": {
        "role": "AGENT_HARNESS_TOOL_ADAPTER_CANDIDATE",
        "current_posture": "UNKNOWN_CURRENT_RUNTIME_RECAPTURE_REQUIRED",
        "proof_fields": (
            "source_or_vendor_identity_verified",
            "installed_version_verified",
            "runtime_config_verified",
            "sandbox_and_tool_permissions_verified",
            "secret_boundary_verified",
            "maintenance_and_failure_receipt_verified",
        ),
        "forbidden_ownership": ("governance", "canonical_memory", "effect_authority"),
    },
    "portfolio:arbiter-content-engine": {
        "role": "MULTI_MODEL_SYNTHESIS_AND_CHALLENGE_PROPOSAL_CANDIDATE",
        "current_posture": "RECOVERED_CONCEPT_IMPLEMENTATION_UNVERIFIED",
        "proof_fields": (
            "source_identity_verified",
            "single_model_baseline_verified",
            "blind_benchmark_verified",
            "independence_semantics_verified",
            "cost_latency_accounting_verified",
            "source_provenance_verified",
        ),
        "forbidden_ownership": ("final_truth", "majority_vote_authority", "effect_authority"),
    },
    "portfolio:dtaap": {
        "role": "DIGITAL_TWIN_PRODUCT_WRAPPER_CANDIDATE",
        "current_posture": "CANDIDATE_RECAPTURE_REQUIRED",
        "proof_fields": (
            "source_identity_verified",
            "sct_boundary_verified",
            "current_tests_verified",
            "deployment_verified",
            "one_external_user_verified",
            "one_measurable_twin_use_case_verified",
        ),
        "forbidden_ownership": ("sct_person_identity", "execution_authority", "current_truth"),
    },
    "portfolio:sovereign-agent-core": {
        "role": "AGENT_TRUST_PATTERN_LIBRARY_MERGE_CONCEPTS_ONLY",
        "current_posture": "NO_SEPARATE_PRODUCT_REPOSITORY",
        "proof_fields": (
            "historical_bytes_verified",
            "active_core_boundary_verified",
            "control_center_overlap_verified",
            "continuityos_overlap_verified",
            "pre_execution_enforcement_gap_recorded",
            "action_receipt_gap_recorded",
        ),
        "forbidden_ownership": ("second_authority_root", "separate_product_authority", "effect_execution"),
    },
    "portfolio:gpts-core-sdk": {
        "role": "HISTORICAL_CORE_SDK_COMPONENT_EVIDENCE",
        "current_posture": "HISTORICAL_V5_9_NOT_ACTIVE_CORE_V6_3",
        "proof_fields": (
            "historical_snapshot_verified",
            "active_core_manifest_verified",
            "version_boundary_verified",
            "licensing_authority_verified",
            "component_reuse_map_verified",
        ),
        "forbidden_ownership": ("active_core_normative_authority", "release_authority", "effect_execution"),
    },
    "entity:lifeos": {
        "role": "IDENTITY_AND_PERSONAL_MEMORY_POLICY_CANDIDATE",
        "current_posture": "ROLE_BOUND_SOURCE_RUNTIME_UNBOUND",
        "proof_fields": (
            "source_identity_verified",
            "identity_lifecycle_contract_verified",
            "private_persistent_scope_policy_verified",
            "memory_admission_boundary_verified",
            "consent_and_revocation_verified",
        ),
        "forbidden_ownership": ("frontier_ingestion", "operational_truth", "effect_authority"),
    },
    "entity:mind": {
        "role": "HYPOTHESIS_AND_COGNITIVE_STATE_CANDIDATE",
        "current_posture": "ROLE_BOUND_SOURCE_RUNTIME_UNBOUND",
        "proof_fields": (
            "source_identity_verified",
            "hypothesis_state_schema_verified",
            "evidence_linkage_verified",
            "commitment_boundary_verified",
            "update_and_rollback_semantics_verified",
        ),
        "forbidden_ownership": ("source_custody", "accepted_truth", "commitment_authority", "effects"),
    },
    "entity:pfi_brain_fabric": {
        "role": "EVIDENCE_LINKED_FRONTIER_INTELLIGENCE_FAMILY_CANDIDATE",
        "current_posture": "UNKNOWN_UNPROVEN_RELATIONS",
        "proof_fields": (
            "source_manifest_verified",
            "pfi_brain_fabric_relation_map_verified",
            "claim_provenance_schema_verified",
            "scope_authz_policy_verified",
            "archive_continuity_lifeos_mind_interfaces_verified",
            "source_to_claim_to_session_to_rebuild_verified",
        ),
        "forbidden_ownership": (
            "exact_source_bytes",
            "accepted_truth",
            "identity_lifecycle",
            "personal_memory_policy",
            "canonical_event_history",
            "semantic_acceptance",
            "effect_authority",
        ),
    },
    "entity:human_coevolution_layer": {
        "role": "HUMAN_AGENT_ENVIRONMENT_UPDATE_PROPOSAL_PROTOCOL",
        "current_posture": "CONTRACT_BOUND_NO_AUTONOMOUS_SELF_DEVELOPMENT",
        "proof_fields": (
            "typed_update_schema_verified",
            "external_evaluator_verified",
            "human_approval_gate_verified",
            "canary_boundary_verified",
            "rollback_boundary_verified",
            "deployment_separation_verified",
        ),
        "forbidden_ownership": ("self_approval", "autonomous_self_development", "deployment_authority", "effects"),
    },
}


def _verify_safety(value: Mapping[str, Any], field: str) -> None:
    safety = value.get("safety") if isinstance(value, Mapping) else None
    if not isinstance(safety, Mapping):
        raise ShadowIntegrationError(f"cognition_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"cognition_unsafe_{field}:{key}")


def _verify_base(base: Mapping[str, Any]) -> tuple[str, str, str, str]:
    if not isinstance(base, Mapping) or base.get("schema") != BASE_CLOSURE_SCHEMA:
        raise ShadowIntegrationError("cognition_wrong_base_schema")
    if base.get("registered_node_count") != 63:
        raise ShadowIntegrationError("cognition_registry_count_mismatch")
    if base.get("status") != "P0_SHADOW_CLOSED_NO_EFFECT":
        raise ShadowIntegrationError("cognition_base_status_mismatch")
    _verify_safety(base, "base")
    effects = base.get("effect_summary")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("cognition_base_effect_boundary_breached")
    gate = str(base.get("effective_gate"))
    action = str(base.get("effective_action"))
    if gate not in {"PASS_SHADOW", "HOLD"}:
        raise ShadowIntegrationError("cognition_base_gate_invalid")
    if gate == "HOLD" and action != "WAIT":
        raise ShadowIntegrationError("cognition_base_hold_must_wait")
    expected = sha256_obj({k: v for k, v in base.items() if k != "closure_sha256"})
    if base.get("closure_sha256") != expected:
        raise ShadowIntegrationError("cognition_base_hash_mismatch")
    return str(base["closure_sha256"]), str(base.get("transaction_sha256")), gate, action


def build_default_cognition_evidence() -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for node_id, spec in COGNITION_SPECS.items():
        row = {
            "node_id": node_id,
            "evidence_class": "BOUNDED_ROLE_AND_POSTURE_ONLY",
            "current_posture": spec["current_posture"],
            "source_identity_verified": False,
            "runtime_verified": False,
            "external_runtime_invoked": False,
            "model_call_performed": False,
            "tool_call_performed": False,
            "memory_write_performed": False,
            "current_truth_write_performed": False,
            "effect_performed": False,
        }
        for field in spec["proof_fields"]:
            row.setdefault(field, False)
        evidence[node_id] = row
    return evidence


def _normalize(node_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
    if node_id not in COGNITION_SPECS:
        raise ShadowIntegrationError("cognition_unknown_node")
    if not isinstance(value, Mapping):
        raise ShadowIntegrationError("cognition_evidence_must_be_mapping")
    row = dict(value)
    row["node_id"] = node_id
    for forbidden in (
        "external_runtime_invoked",
        "model_call_performed",
        "tool_call_performed",
        "memory_write_performed",
        "current_truth_write_performed",
        "effect_performed",
    ):
        if row.get(forbidden) is not False:
            raise ShadowIntegrationError(f"cognition_effect_boundary_breached:{node_id}:{forbidden}")
    if row.get("runtime_verified") is True and row.get("source_identity_verified") is not True:
        raise ShadowIntegrationError(f"cognition_runtime_without_source_identity:{node_id}")
    return row


def _build_receipt(node_id: str, value: Mapping[str, Any], *, base_sha: str, transaction_sha: str) -> dict[str, Any]:
    spec = COGNITION_SPECS[node_id]
    row = _normalize(node_id, value)
    proof_fields = tuple(spec["proof_fields"])
    missing = tuple(field for field in proof_fields if row.get(field) is not True)
    proof_complete = not missing

    body = {
        "schema": COGNITION_RECEIPT_SCHEMA,
        "node_id": node_id,
        "role": spec["role"],
        "source_closure_sha256": base_sha,
        "source_transaction_sha256": transaction_sha,
        "evidence_class": str(row.get("evidence_class", "UNKNOWN")),
        "current_posture": str(row.get("current_posture", spec["current_posture"])),
        "required_proof_fields": proof_fields,
        "missing_proof_fields": missing,
        "proof_complete": proof_complete,
        "typed_contract_bound": True,
        "proposal_only": True,
        "case_influence_enabled": False,
        "decision_vote": False,
        "gate_effect": "NONE",
        "may_widen_gate": False,
        "current_truth_authority": "NONE",
        "memory_authority": "NONE",
        "execution_authority": "NONE",
        "forbidden_ownership": tuple(spec["forbidden_ownership"]),
        "source_identity_proven_by_receipt": proof_complete and row.get("source_identity_verified") is True,
        "runtime_proven_by_receipt": proof_complete and row.get("runtime_verified") is True,
        "external_runtime_invoked": False,
        "effects": {
            "model_call": False,
            "tool_call": False,
            "memory_write": False,
            "current_truth_write": False,
            "runtime_activation": False,
            "human_approval": False,
            "canary": False,
            "deploy": False,
            "signal": False,
            "order": False,
            "capital_effect": False,
        },
        "semantics": {
            "cognitive_proposal_is_not_truth": True,
            "cognitive_proposal_is_not_permission": True,
            "proof_complete_does_not_enable_case_influence_in_p0": True,
            "source_identity_is_distinct_from_runtime": True,
            "memory_proposal_is_distinct_from_memory_write": True,
            "human_approval_is_distinct_from_agent_proposal": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["cognition_receipt_sha256"] = sha256_obj(body)
    return body


def build_shadow_cognition_proposal_ledger(
    base_closure: Mapping[str, Any],
    evidence_bundle: Mapping[str, Mapping[str, Any]],
    *,
    generated_at: str,
) -> dict[str, Any]:
    base_sha, transaction_sha, gate, action = _verify_base(base_closure)
    if not isinstance(evidence_bundle, Mapping):
        raise ShadowIntegrationError("cognition_evidence_bundle_missing")
    if set(evidence_bundle) != set(COGNITION_NODES):
        missing = sorted(set(COGNITION_NODES) - set(evidence_bundle))
        unknown = sorted(set(evidence_bundle) - set(COGNITION_NODES))
        raise ShadowIntegrationError(
            "cognition_coverage_mismatch:missing=" + ",".join(missing) + ";unknown=" + ",".join(unknown)
        )

    receipts = tuple(
        _build_receipt(node_id, evidence_bundle[node_id], base_sha=base_sha, transaction_sha=transaction_sha)
        for node_id in COGNITION_NODES
    )
    proof_complete_nodes = tuple(row["node_id"] for row in receipts if row["proof_complete"] is True)

    body = {
        "schema": COGNITION_LEDGER_SCHEMA,
        "generated_at": str(generated_at),
        "source_closure_sha256": base_sha,
        "source_transaction_sha256": transaction_sha,
        "case_id": base_closure.get("case_id"),
        "base_gate": gate,
        "base_action": action,
        "cognition_node_count": len(receipts),
        "all_cognition_nodes_typed": True,
        "proof_complete_nodes": proof_complete_nodes,
        "receipts": receipts,
        "plane_rules": {
            "proposal_only": True,
            "no_case_influence_in_p0": True,
            "no_majority_vote": True,
            "no_gate_change": True,
            "no_current_truth_authority": True,
            "no_memory_write_authority": True,
            "no_effect_authority": True,
            "human_approval_remains_external": True,
            "source_runtime_deployment_claims_remain_separate": True,
        },
        "effects": {
            "runtime_invocation": False,
            "model_call": False,
            "tool_call": False,
            "memory_write": False,
            "current_truth_write": False,
            "human_approval": False,
            "canary": False,
            "deploy": False,
            "signal": False,
            "order": False,
            "capital_effect": False,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["cognition_ledger_sha256"] = sha256_obj(body)
    return body

#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj

BASE_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v4"
KNOWLEDGE_MEMORY_SCHEMA = "bitevo.shadow_knowledge_memory_candidate.v1"
KNOWLEDGE_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v5"

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
        raise ShadowIntegrationError(f"knowledge_closure_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"knowledge_closure_unsafe_{field}:{key}")


def _verify_base(closure: Mapping[str, Any]) -> tuple[str, str, str, str]:
    if not isinstance(closure, Mapping) or closure.get("schema") != BASE_CLOSURE_SCHEMA:
        raise ShadowIntegrationError("knowledge_closure_wrong_base_schema")
    if closure.get("registered_node_count") != _EXPECTED_NODE_COUNT:
        raise ShadowIntegrationError("knowledge_closure_registry_count_mismatch")
    if closure.get("status") != "P0_SHADOW_CLOSED_NO_EFFECT":
        raise ShadowIntegrationError("knowledge_closure_base_status_mismatch")
    _verify_safety(closure, "base")
    effects = closure.get("effect_summary")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("knowledge_closure_base_effect_boundary_breached")
    gate = closure.get("effective_gate")
    action = closure.get("effective_action")
    if gate not in {"PASS_SHADOW", "HOLD"}:
        raise ShadowIntegrationError("knowledge_closure_effective_gate_invalid")
    if gate == "HOLD" and action != "WAIT":
        raise ShadowIntegrationError("knowledge_closure_hold_must_wait")
    transaction_sha = str(closure.get("transaction_sha256"))
    closure_sha = _verify_hash(closure, "closure_sha256", "knowledge_closure_base_hash_mismatch")
    return closure_sha, transaction_sha, str(gate), str(action)


def _verify_candidate(candidate: Mapping[str, Any], transaction_sha: str) -> str:
    if not isinstance(candidate, Mapping) or candidate.get("schema") != KNOWLEDGE_MEMORY_SCHEMA:
        raise ShadowIntegrationError("knowledge_closure_wrong_candidate_schema")
    if candidate.get("source_transaction_sha256") != transaction_sha:
        raise ShadowIntegrationError("knowledge_closure_transaction_mismatch")
    _verify_safety(candidate, "candidate")
    if candidate.get("decision_dependency") != "NON_VOTING_EVIDENCE_DERIVATIVE":
        raise ShadowIntegrationError("knowledge_closure_dependency_widened")
    if candidate.get("can_change_decision") is not False:
        raise ShadowIntegrationError("knowledge_closure_decision_authority_breached")

    foundry = candidate.get("knowledge_foundry") or {}
    memory = candidate.get("durable_memory") or {}
    if foundry.get("source_identity_bound") is not False or foundry.get("runtime_bound") is not False:
        raise ShadowIntegrationError("knowledge_closure_foundry_source_runtime_overclaim")
    if foundry.get("claim_admission_performed") is not False or foundry.get("semantic_acceptance_authority") is not False:
        raise ShadowIntegrationError("knowledge_closure_foundry_admission_overclaim")
    if memory.get("source_identity_bound") is not False or memory.get("runtime_bound") is not False:
        raise ShadowIntegrationError("knowledge_closure_memory_source_runtime_overclaim")
    if memory.get("write_performed") is not False or memory.get("permission_source") is not False:
        raise ShadowIntegrationError("knowledge_closure_memory_authority_overclaim")

    admission = candidate.get("admission") or {}
    if admission.get("status") != "NOT_PERFORMED":
        raise ShadowIntegrationError("knowledge_closure_admission_overclaim")
    if admission.get("admitted_claim_count") != 0 or admission.get("rejected_claim_count") != 0:
        raise ShadowIntegrationError("knowledge_closure_admission_count_nonzero")

    claims = candidate.get("claim_candidates")
    if not isinstance(claims, (tuple, list)) or not claims:
        raise ShadowIntegrationError("knowledge_closure_claim_candidates_missing")
    for row in claims:
        if not isinstance(row, Mapping):
            raise ShadowIntegrationError("knowledge_closure_claim_candidate_invalid")
        if row.get("admission_status") != "UNADMITTED" or row.get("can_be_current_truth") is not False:
            raise ShadowIntegrationError("knowledge_closure_claim_candidate_promoted")

    proposal = candidate.get("memory_proposal") or {}
    for key in ("write_allowed", "auto_merge_allowed", "private_memory_write", "shared_memory_write", "project_canon_write", "current_truth_write"):
        if proposal.get(key) is not False:
            raise ShadowIntegrationError(f"knowledge_closure_memory_proposal_write_breached:{key}")
    _verify_hash(proposal, "proposal_sha256", "knowledge_closure_memory_proposal_hash_mismatch")

    effects = candidate.get("effects")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("knowledge_closure_candidate_effect_boundary_breached")
    return _verify_hash(candidate, "knowledge_memory_sha256", "knowledge_closure_candidate_hash_mismatch")


def build_unified_shadow_knowledge_closure(
    base_closure: Mapping[str, Any],
    knowledge_memory_candidate: Mapping[str, Any],
    *,
    closed_at: str,
) -> dict[str, Any]:
    """Extend P0 closure with an unadmitted knowledge/memory proposal plane without writes or decision authority."""
    base_sha, transaction_sha, effective_gate, effective_action = _verify_base(base_closure)
    knowledge_sha = _verify_candidate(knowledge_memory_candidate, transaction_sha)

    body = {
        "schema": KNOWLEDGE_CLOSURE_SCHEMA,
        "closed_at": str(closed_at),
        "case_id": base_closure.get("case_id"),
        "transaction_sha256": transaction_sha,
        "base_closure_sha256": base_sha,
        "knowledge_memory_sha256": knowledge_sha,
        "registered_node_count": base_closure["registered_node_count"],
        "effective_gate": effective_gate,
        "effective_action": effective_action,
        "status": "P0_SHADOW_CLOSED_NO_EFFECT",
        "planes": {
            **dict(base_closure.get("planes") or {}),
            "knowledge_candidate": "BOUND_UNADMITTED_NO_WRITE",
            "durable_memory_candidate": "BOUND_PROPOSAL_ONLY_NO_WRITE",
        },
        "effect_summary": {
            **dict(base_closure.get("effect_summary") or {}),
            "claim_admission": False,
            "project_canon_write": False,
            "durable_memory_write": False,
        },
        "knowledge_status": {
            "claim_candidates": len(knowledge_memory_candidate.get("claim_candidates") or ()),
            "admitted_claims": 0,
            "memory_write": False,
            "source_runtime_bound": False,
        },
        "semantics": {
            "claim_candidate_is_not_admitted_claim": True,
            "knowledge_derivative_does_not_vote": True,
            "memory_proposal_is_not_memory_write": True,
            "durable_memory_is_not_current_truth": True,
            "memory_is_not_permission": True,
            "knowledge_plane_cannot_widen_effective_gate": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["closure_sha256"] = sha256_obj(body)
    return body

#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_transaction import TRANSACTION_SCHEMA

HANRI_RECEIPT_SCHEMA = "hanri.shadow-evidence-governor.receipt/v1"
KNOWLEDGE_MEMORY_SCHEMA = "bitevo.shadow_knowledge_memory_candidate.v1"

_EXPECTED_NODE_COUNT = 63
_EXPECTED_HANRI_HEAD = "ef5c504179de8ae8c16bd70c168b14b79bd2f466"


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
        raise ShadowIntegrationError(f"knowledge_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"knowledge_unsafe_{field}:{key}")


def _verify_transaction(transaction: Mapping[str, Any]) -> str:
    if not isinstance(transaction, Mapping) or transaction.get("schema") != TRANSACTION_SCHEMA:
        raise ShadowIntegrationError("knowledge_wrong_transaction_schema")
    if transaction.get("registered_node_count") != _EXPECTED_NODE_COUNT:
        raise ShadowIntegrationError("knowledge_registry_count_mismatch")
    _verify_safety(transaction, "transaction")
    effects = transaction.get("effect_boundary")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("knowledge_transaction_effect_boundary_breached")
    tx_sha = _verify_hash(transaction, "transaction_sha256", "knowledge_transaction_hash_mismatch")
    gate = transaction.get("control_gate")
    action = transaction.get("control_plane_action")
    if gate == "HOLD" and action != "WAIT":
        raise ShadowIntegrationError("knowledge_hold_must_wait")
    return tx_sha


def _verify_hanri(receipt: Mapping[str, Any], tx_sha: str) -> str:
    if not isinstance(receipt, Mapping) or receipt.get("schema") != HANRI_RECEIPT_SCHEMA:
        raise ShadowIntegrationError("knowledge_wrong_hanri_schema")
    if receipt.get("source_transaction_sha256") != tx_sha:
        raise ShadowIntegrationError("knowledge_hanri_transaction_mismatch")
    _verify_safety(receipt, "hanri")
    source = receipt.get("hanri_source") or {}
    if source.get("head_sha") != _EXPECTED_HANRI_HEAD:
        raise ShadowIntegrationError("knowledge_hanri_source_mismatch")
    if source.get("authority_root") is not False or source.get("can_promote_self") is not False:
        raise ShadowIntegrationError("knowledge_hanri_authority_overclaim")
    effects = receipt.get("effects")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("knowledge_hanri_effect_boundary_breached")
    km = receipt.get("knowledge_memory") or {}
    if km.get("claim_admission") != "NOT_PERFORMED":
        raise ShadowIntegrationError("knowledge_upstream_admission_already_claimed")
    for key in ("durable_memory_write", "project_canon_write", "current_truth_write"):
        if km.get(key) is not False:
            raise ShadowIntegrationError(f"knowledge_upstream_write_breached:{key}")
    return _verify_hash(receipt, "hanri_receipt_sha256", "knowledge_hanri_hash_mismatch")


def build_shadow_knowledge_memory_candidate(
    transaction: Mapping[str, Any],
    hanri_receipt: Mapping[str, Any],
    *,
    generated_at: str,
) -> dict[str, Any]:
    """Create replayable claim/memory candidates without admission, canon write or durable memory write."""
    tx_sha = _verify_transaction(transaction)
    hanri_sha = _verify_hanri(hanri_receipt, tx_sha)
    governor = hanri_receipt.get("governor") or {}
    archive = hanri_receipt.get("archiveos") or {}

    candidates = [
        {
            "claim_id": "claim:p0-effective-gate",
            "statement": f"P0 effective evidence-governor gate is {governor.get('gate')}",
            "truth_class": "SOURCE_BOUND_DERIVATIVE",
            "evidence_refs": (f"transaction:{tx_sha}", f"hanri:{hanri_sha}"),
            "admission_status": "UNADMITTED",
            "can_be_current_truth": False,
        },
        {
            "claim_id": "claim:p0-archiveos-freshness",
            "statement": f"ArchiveOS freshness state in the bound HANRI receipt is {archive.get('freshness')}",
            "truth_class": "SOURCE_BOUND_DERIVATIVE",
            "evidence_refs": (f"hanri:{hanri_sha}",),
            "admission_status": "UNADMITTED",
            "can_be_current_truth": False,
        },
    ]
    if archive.get("status") == "BLOCKED_REVERIFY":
        candidates.append(
            {
                "claim_id": "claim:p0-archiveos-proof-gap",
                "statement": "ArchiveOS current immutable-source-set integrity remains unproven in the bound qualification",
                "truth_class": "SOURCE_BOUND_DERIVATIVE",
                "evidence_refs": (f"hanri:{hanri_sha}",),
                "admission_status": "UNADMITTED",
                "can_be_current_truth": False,
            }
        )

    memory_proposal = {
        "proposal_id": f"memory:{transaction.get('case_id')}:p0-shadow",
        "memory_class": "EVIDENCE_STATE_CANDIDATE",
        "source_transaction_sha256": tx_sha,
        "source_hanri_receipt_sha256": hanri_sha,
        "candidate_claim_ids": tuple(row["claim_id"] for row in candidates),
        "write_allowed": False,
        "auto_merge_allowed": False,
        "private_memory_write": False,
        "shared_memory_write": False,
        "project_canon_write": False,
        "current_truth_write": False,
    }
    memory_proposal["proposal_sha256"] = sha256_obj(memory_proposal)

    body = {
        "schema": KNOWLEDGE_MEMORY_SCHEMA,
        "generated_at": str(generated_at),
        "case_id": transaction.get("case_id"),
        "source_transaction_sha256": tx_sha,
        "source_hanri_receipt_sha256": hanri_sha,
        "knowledge_foundry": {
            "role": "SOURCE_TO_CLAIM_TO_CONTRADICTION_TO_DECISION_GRAPH",
            "source_identity_bound": False,
            "runtime_bound": False,
            "claim_admission_performed": False,
            "semantic_acceptance_authority": False,
            "claim_ceiling": "ROLE_BOUND_SOURCE_RUNTIME_UNBOUND",
        },
        "durable_memory": {
            "role": "POLICY_GOVERNED_DURABLE_MEMORY_TRANSACTION_CANDIDATE",
            "source_identity_bound": False,
            "runtime_bound": False,
            "write_performed": False,
            "permission_source": False,
            "claim_ceiling": "ROLE_BOUND_SOURCE_RUNTIME_UNBOUND",
        },
        "claim_candidates": tuple(candidates),
        "contradiction_candidates": (),
        "memory_proposal": memory_proposal,
        "admission": {
            "status": "NOT_PERFORMED",
            "admitted_claim_count": 0,
            "rejected_claim_count": 0,
            "human_or_authority_review_required": True,
        },
        "decision_dependency": "NON_VOTING_EVIDENCE_DERIVATIVE",
        "can_change_decision": False,
        "effects": {
            "knowledge_write": False,
            "memory_write": False,
            "project_canon_write": False,
            "current_truth_apply": False,
            "runtime_invocation": False,
            "external_message": False,
            "signal": False,
            "order": False,
            "capital_effect": False,
        },
        "semantics": {
            "derived_claim_is_not_admitted_claim": True,
            "archive_custody_is_not_claim_acceptance": True,
            "memory_proposal_is_not_memory_write": True,
            "durable_memory_is_not_current_truth": True,
            "memory_is_not_permission": True,
            "unbound_knowledge_runtime_is_not_invoked": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["knowledge_memory_sha256"] = sha256_obj(body)
    return body

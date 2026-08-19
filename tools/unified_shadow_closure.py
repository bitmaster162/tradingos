#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_transaction import TRANSACTION_SCHEMA

CONTINUITY_RECEIPT_SCHEMA = "continuityos.shadow_continuity_receipt.v1"
RETURN_INTAKE_SCHEMA = "control_return_broker.shadow_intake_receipt.v1"
CONTROL_PROJECTION_SCHEMA = "control_center.unified_shadow_projection.v1"
CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v2"

_EXPECTED_NODE_COUNT = 63
_EXPECTED_CONTINUITY_HEAD = "9dfb9e5b847a27113ca7c709a0adee900e3ff63f"


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
        raise ShadowIntegrationError(f"closure_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"closure_unsafe_{field}:{key}")


def _verify_transaction(transaction: Mapping[str, Any]) -> str:
    if not isinstance(transaction, Mapping) or transaction.get("schema") != TRANSACTION_SCHEMA:
        raise ShadowIntegrationError("closure_wrong_transaction_schema")
    if transaction.get("registered_node_count") != _EXPECTED_NODE_COUNT:
        raise ShadowIntegrationError("closure_transaction_registry_count_mismatch")
    _verify_safety(transaction, "transaction")
    effects = transaction.get("effect_boundary")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("closure_transaction_effect_boundary_breached")
    if transaction.get("control_gate") == "HOLD" and transaction.get("control_plane_action") != "WAIT":
        raise ShadowIntegrationError("closure_transaction_hold_must_wait")
    return _verify_hash(transaction, "transaction_sha256", "closure_transaction_hash_mismatch")


def _verify_continuity(receipt: Mapping[str, Any], tx_sha: str, control_gate: str) -> str:
    if not isinstance(receipt, Mapping) or receipt.get("schema") != CONTINUITY_RECEIPT_SCHEMA:
        raise ShadowIntegrationError("closure_wrong_continuity_schema")
    if receipt.get("source_transaction_sha256") != tx_sha:
        raise ShadowIntegrationError("closure_continuity_transaction_mismatch")
    _verify_safety(receipt, "continuity")
    modern = receipt.get("modern_source", {})
    if modern.get("head_sha") != _EXPECTED_CONTINUITY_HEAD:
        raise ShadowIntegrationError("closure_continuity_modern_source_mismatch")
    if modern.get("claim_ceiling") != "MODERN_GITHUB_SOURCE_ONLY":
        raise ShadowIntegrationError("closure_continuity_claim_ceiling_mismatch")
    if modern.get("proves_live_runtime") is not False or modern.get("proves_current_host_state") is not False:
        raise ShadowIntegrationError("closure_continuity_source_authority_overclaim")
    historical = receipt.get("historical_lineage", {})
    if historical.get("live_host_state") != "UNVERIFIED":
        raise ShadowIntegrationError("closure_live_host_state_overclaim")
    writes = receipt.get("writes")
    if not isinstance(writes, Mapping) or any(value is not False for value in writes.values()):
        raise ShadowIntegrationError("closure_continuity_write_breached")
    authority = receipt.get("authority", {})
    if authority.get("execution_authority") != "NONE" or authority.get("apply_authorized") is not False:
        raise ShadowIntegrationError("closure_continuity_authority_breached")
    return_candidate = receipt.get("return_candidate", {})
    if return_candidate.get("semantic_acceptance") != "NOT_PERFORMED":
        raise ShadowIntegrationError("closure_return_semantic_acceptance_overclaim")
    if return_candidate.get("write_allowed") is not False:
        raise ShadowIntegrationError("closure_return_candidate_write_breached")
    expected_disposition = "HOLD_SHADOW_NO_WRITE" if control_gate == "HOLD" else "READY_FOR_READ_ONLY_REVIEW"
    if receipt.get("disposition") != expected_disposition:
        raise ShadowIntegrationError("closure_continuity_disposition_mismatch")
    return _verify_hash(receipt, "continuity_receipt_sha256", "closure_continuity_hash_mismatch")


def _verify_return_intake(receipt: Mapping[str, Any], tx_sha: str, continuity_sha: str) -> str:
    if not isinstance(receipt, Mapping) or receipt.get("schema") != RETURN_INTAKE_SCHEMA:
        raise ShadowIntegrationError("closure_wrong_return_intake_schema")
    if receipt.get("source_transaction_sha256") != tx_sha:
        raise ShadowIntegrationError("closure_return_intake_transaction_mismatch")
    if receipt.get("continuity_receipt_sha256") != continuity_sha:
        raise ShadowIntegrationError("closure_return_intake_continuity_mismatch")
    _verify_safety(receipt, "return_intake")
    if receipt.get("physical_status") != "VERIFIED_READ_ONLY":
        raise ShadowIntegrationError("closure_return_intake_not_physically_verified")
    physical = receipt.get("physical_verification")
    if not isinstance(physical, Mapping) or physical.get("passed") is not True:
        raise ShadowIntegrationError("closure_return_intake_physical_pass_missing")
    transport = receipt.get("transport")
    if not isinstance(transport, Mapping) or any(value is not False for value in transport.values()):
        raise ShadowIntegrationError("closure_return_transport_mutation_breached")
    if receipt.get("semantic_acceptance") != "NOT_PERFORMED" or receipt.get("content_acceptance_claimed") is not False:
        raise ShadowIntegrationError("closure_return_semantic_acceptance_overclaim")
    if receipt.get("source_bytes_unchanged") is not True:
        raise ShadowIntegrationError("closure_return_source_bytes_not_proven_unchanged")
    authority = receipt.get("authority", {})
    if authority.get("execution_authority") != "NONE" or authority.get("apply_authorized") is not False:
        raise ShadowIntegrationError("closure_return_authority_breached")
    return _verify_hash(receipt, "shadow_intake_sha256", "closure_return_intake_hash_mismatch")


def _verify_control_projection(
    projection: Mapping[str, Any],
    tx_sha: str,
    *,
    control_gate: str,
    control_action: str,
) -> str:
    if not isinstance(projection, Mapping) or projection.get("schema") != CONTROL_PROJECTION_SCHEMA:
        raise ShadowIntegrationError("closure_wrong_control_projection_schema")
    if projection.get("source_transaction_sha256") != tx_sha:
        raise ShadowIntegrationError("closure_control_projection_transaction_mismatch")
    _verify_safety(projection, "control_projection")
    if projection.get("projection_kind") != "NON_AUTHORITY_SHADOW_PROJECTION":
        raise ShadowIntegrationError("closure_control_projection_authority_overclaim")
    if projection.get("apply") is not False:
        raise ShadowIntegrationError("closure_control_projection_apply_forbidden")
    mutations = projection.get("mutations")
    if not isinstance(mutations, Mapping) or any(value is not False for value in mutations.values()):
        raise ShadowIntegrationError("closure_control_projection_mutation_breached")
    if projection.get("effect_candidates_created") != 0 or projection.get("executions_authorized") != 0:
        raise ShadowIntegrationError("closure_control_projection_effect_count_breached")
    view = projection.get("decision_view", {})
    if view.get("control_gate") != control_gate or view.get("control_plane_action") != control_action:
        raise ShadowIntegrationError("closure_control_projection_decision_mismatch")
    expected_disposition = "HOLD_NO_APPLY" if control_gate == "HOLD" else "SHADOW_REVIEW_ONLY"
    if view.get("disposition") != expected_disposition:
        raise ShadowIntegrationError("closure_control_projection_disposition_mismatch")
    return _verify_hash(projection, "projection_sha256", "closure_control_projection_hash_mismatch")


def build_unified_shadow_closure(
    transaction: Mapping[str, Any],
    continuity_receipt: Mapping[str, Any],
    return_intake_receipt: Mapping[str, Any],
    control_projection: Mapping[str, Any],
    *,
    closed_at: str,
) -> dict[str, Any]:
    """Close one P0 shadow decision across composition, continuity, transport and authority planes."""
    tx_sha = _verify_transaction(transaction)
    gate = str(transaction.get("control_gate"))
    action = str(transaction.get("control_plane_action"))
    continuity_sha = _verify_continuity(continuity_receipt, tx_sha, gate)
    return_sha = _verify_return_intake(return_intake_receipt, tx_sha, continuity_sha)
    projection_sha = _verify_control_projection(
        control_projection,
        tx_sha,
        control_gate=gate,
        control_action=action,
    )

    body = {
        "schema": CLOSURE_SCHEMA,
        "closed_at": str(closed_at),
        "case_id": transaction.get("case_id"),
        "transaction_sha256": tx_sha,
        "continuity_receipt_sha256": continuity_sha,
        "return_intake_sha256": return_sha,
        "control_projection_sha256": projection_sha,
        "registered_node_count": transaction["registered_node_count"],
        "control_gate": gate,
        "control_plane_action": action,
        "status": "P0_SHADOW_CLOSED_NO_EFFECT",
        "planes": {
            "composition": "BOUND",
            "continuity": "BOUND_READ_ONLY",
            "return_transport": "BOUND_READ_ONLY_PHYSICAL",
            "authority_projection": "BOUND_NON_AUTHORITY",
            "executor": "DISABLED",
        },
        "effect_summary": {
            "merge": False,
            "deploy": False,
            "runtime_activation": False,
            "current_truth_apply": False,
            "memory_or_checkpoint_write": False,
            "return_or_archive_write": False,
            "external_model_call": False,
            "exchange_call": False,
            "signal": False,
            "order": False,
            "capital_effect": False,
        },
        "semantics": {
            "closure_is_evidence_not_authority": True,
            "all_bound_planes_reference_same_transaction": True,
            "continuity_candidate_is_not_canonical_state": True,
            "return_physical_pass_is_not_semantic_acceptance": True,
            "control_projection_is_not_current_truth": True,
            "registered_system_is_not_invoked_runtime": True,
            "executor_remains_separate_and_disabled": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["closure_sha256"] = sha256_obj(body)
    return body

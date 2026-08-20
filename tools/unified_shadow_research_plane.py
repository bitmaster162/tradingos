#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_transaction import TRANSACTION_SCHEMA

RESEARCH_PLANE_SCHEMA = "bitevo.shadow_research_simulation_receipt.v1"
_EXPECTED_ARENA_REPO = "bitmaster162/sovereign-arena-site"
_EXPECTED_ARENA_BRANCH = "main"
_EXPECTED_ARENA_HEAD = "f070fe0587a4222b993b7e8fc9b8f2726ca414d9"
_EXPECTED_NODE_COUNT = 63


def _hex(value: Any, length: int, field: str) -> str:
    if not isinstance(value, str):
        raise ShadowIntegrationError(f"research_{field}_must_be_hex{length}")
    text = value.lower()
    if len(text) != length or any(ch not in "0123456789abcdef" for ch in text):
        raise ShadowIntegrationError(f"research_{field}_must_be_hex{length}")
    return text


def _verify_safety(record: Mapping[str, Any], field: str) -> None:
    safety = record.get("safety") if isinstance(record, Mapping) else None
    if not isinstance(safety, Mapping):
        raise ShadowIntegrationError(f"research_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"research_unsafe_{field}:{key}")


def _verify_transaction(transaction: Mapping[str, Any]) -> str:
    if not isinstance(transaction, Mapping) or transaction.get("schema") != TRANSACTION_SCHEMA:
        raise ShadowIntegrationError("research_wrong_transaction_schema")
    if transaction.get("registered_node_count") != _EXPECTED_NODE_COUNT:
        raise ShadowIntegrationError("research_registry_count_mismatch")
    _verify_safety(transaction, "transaction")
    effects = transaction.get("effect_boundary")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("research_transaction_effect_boundary_breached")
    tx_sha = _hex(transaction.get("transaction_sha256"), 64, "transaction_sha256")
    expected = sha256_obj({k: v for k, v in transaction.items() if k != "transaction_sha256"})
    if tx_sha != expected:
        raise ShadowIntegrationError("research_transaction_hash_mismatch")
    return tx_sha


def _arena_ref(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ShadowIntegrationError("research_arena_ref_must_be_object")
    repo = value.get("repo")
    branch = value.get("branch")
    head = _hex(value.get("head_sha"), 40, "arena_head")
    if repo != _EXPECTED_ARENA_REPO:
        raise ShadowIntegrationError("research_arena_repo_mismatch")
    if branch != _EXPECTED_ARENA_BRANCH:
        raise ShadowIntegrationError("research_arena_branch_mismatch")
    if head != _EXPECTED_ARENA_HEAD:
        raise ShadowIntegrationError("research_arena_head_mismatch")
    return {
        "repo": repo,
        "branch": branch,
        "head_sha": head,
        "source_identity_bound": True,
        "deployment_proven": False,
        "runtime_proven": False,
    }


def build_shadow_research_simulation_receipt(
    transaction: Mapping[str, Any],
    *,
    sovereign_arena_ref: Mapping[str, Any],
    maworld_source_bound: bool,
    maworld_runtime_bound: bool,
    pandora_source_bound: bool,
    pandora_runtime_bound: bool,
    generated_at: str,
) -> dict[str, Any]:
    """Account for research/simulation surfaces without making them voting or runtime dependencies."""
    tx_sha = _verify_transaction(transaction)
    arena = _arena_ref(sovereign_arena_ref)

    for field, value in {
        "maworld_source_bound": maworld_source_bound,
        "maworld_runtime_bound": maworld_runtime_bound,
        "pandora_source_bound": pandora_source_bound,
        "pandora_runtime_bound": pandora_runtime_bound,
    }.items():
        if not isinstance(value, bool):
            raise ShadowIntegrationError(f"research_{field}_must_be_bool")

    # P0 evidence does not currently bind MAWorld or Pandora code/runtime identity.
    # A caller cannot silently upgrade those dimensions without a new evidence-bound schema revision.
    if maworld_source_bound or maworld_runtime_bound:
        raise ShadowIntegrationError("research_maworld_current_source_or_runtime_not_bound")
    if pandora_source_bound or pandora_runtime_bound:
        raise ShadowIntegrationError("research_pandora_current_source_or_runtime_not_bound")

    body = {
        "schema": RESEARCH_PLANE_SCHEMA,
        "generated_at": str(generated_at),
        "source_transaction_sha256": tx_sha,
        "case_id": transaction.get("case_id"),
        "decision_dependency": "NON_BLOCKING_SIDE_PLANE",
        "trading_voter": False,
        "can_change_decision": False,
        "surfaces": {
            "maworld": {
                "role": "ISOLATED_REPRODUCIBLE_EXPERIMENT_CHAMBER_CANDIDATE",
                "source_status": "SOURCE_UNBOUND",
                "runtime_status": "RUNTIME_UNBOUND",
                "source_identity_bound": False,
                "runtime_invoked": False,
                "claim_ceiling": "ROLE_AND_RESEARCH_HYPOTHESIS_ONLY",
            },
            "pandora": {
                "role": "VISUAL_PROGRAMMABLE_RUNTIME_AND_SIMULATION_CANDIDATE",
                "source_status": "SOURCE_UNBOUND",
                "runtime_status": "RUNTIME_UNBOUND",
                "source_identity_bound": False,
                "runtime_invoked": False,
                "claim_ceiling": "ROLE_AND_RESEARCH_HYPOTHESIS_ONLY",
            },
            "sovereign_arena": {
                **arena,
                "role": "RESEARCH_EVIDENCE_PRODUCT_SURFACE",
                "runtime_invoked": False,
                "trading_execution_surface": False,
                "claim_ceiling": "SOURCE_IDENTITY_ONLY",
            },
        },
        "research_contract": {
            "preserve_failed_stopped_degraded_experiments": "DESIGN_REQUIREMENT_ONLY",
            "provenance_required": True,
            "replay_status_required": True,
            "all_trial_denominator_required": True,
            "no_signal_service": True,
            "no_live_trading": True,
            "publication_is_not_authority": True,
        },
        "effects": {
            "runtime_invocation": False,
            "experiment_launch": False,
            "artifact_publication": False,
            "deployment": False,
            "external_message": False,
            "current_truth_apply": False,
            "signal": False,
            "order": False,
            "capital_effect": False,
        },
        "semantics": {
            "source_identity_is_not_deployment": True,
            "research_surface_is_not_decision_authority": True,
            "simulation_result_is_not_execution_permission": True,
            "unbound_optional_surface_does_not_block_core_decision": True,
            "non_blocking_does_not_mean_trusted": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["research_plane_sha256"] = sha256_obj(body)
    return body

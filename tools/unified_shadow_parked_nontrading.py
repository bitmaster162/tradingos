#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj

BASE_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v11"
PARKED_LEDGER_SCHEMA = "bitevo.shadow_parked_nontrading_ledger.v1"
PARKED_RECEIPT_SCHEMA = "bitevo.shadow_parked_nontrading_receipt.v1"
FINAL_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v12"

PARKED_NODES = (
    "portfolio:parasite-killer",
    "portfolio:parasite-hunter-game",
    "portfolio:amora",
    "portfolio:amora-token",
    "portfolio:rtf-starcoin",
)

PARKED_SPECS: dict[str, dict[str, Any]] = {
    "portfolio:parasite-killer": {
        "role": "CONTAINED_NFT_MARKET_INTELLIGENCE_TOOL",
        "posture": "RISK_HOLD_CONTAINMENT_READ_ONLY_ONLY",
        "proof_fields": ("source_identity_verified", "containment_verified", "fresh_health_verified", "read_only_scan_verified"),
    },
    "portfolio:parasite-hunter-game": {
        "role": "SEPARATE_GAME_PRODUCT",
        "posture": "BOUNDED_PILOT_PHYSICAL_TEST_GAPS",
        "proof_fields": ("source_identity_verified", "separate_scope_verified", "gpu_mobile_test_verified", "human_balance_test_verified"),
    },
    "portfolio:amora": {
        "role": "PARKED_AI_COMPANION_PRODUCT",
        "posture": "PARKED_NO_REVIVAL_AUTHORIZED",
        "proof_fields": ("source_identity_verified", "explicit_revival_gate_verified"),
    },
    "portfolio:amora-token": {
        "role": "DEPENDENT_HISTORICAL_TOKEN_CONCEPT",
        "posture": "HOLD_WITH_PARENT_NO_INDEPENDENT_LAUNCH",
        "proof_fields": ("source_identity_verified", "parent_product_revival_verified", "independent_launch_forbidden_verified"),
    },
    "portfolio:rtf-starcoin": {
        "role": "ARCHIVED_HISTORICAL_PRODUCT_LINE",
        "posture": "ARCHIVE_PRESERVE_ONLY",
        "proof_fields": ("archive_identity_verified", "preservation_manifest_verified"),
    },
}

_FORBIDDEN_EFFECT_FIELDS = (
    "runtime_activated",
    "wallet_accessed",
    "signing_performed",
    "order_emitted",
    "token_launched",
    "external_message_sent",
    "deployment_performed",
    "current_truth_written",
    "capital_effect",
)


def _verify_safety(value: Mapping[str, Any], field: str) -> None:
    safety = value.get("safety") if isinstance(value, Mapping) else None
    if not isinstance(safety, Mapping):
        raise ShadowIntegrationError(f"parked_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"parked_unsafe_{field}:{key}")


def _verify_base(base: Mapping[str, Any]) -> tuple[str, str, str, str]:
    if not isinstance(base, Mapping) or base.get("schema") != BASE_CLOSURE_SCHEMA:
        raise ShadowIntegrationError("parked_wrong_base_schema")
    if base.get("registered_node_count") != 63 or base.get("status") != "P0_SHADOW_CLOSED_NO_EFFECT":
        raise ShadowIntegrationError("parked_base_state_mismatch")
    _verify_safety(base, "base")
    if any(value is not False for value in (base.get("effect_summary") or {}).values()):
        raise ShadowIntegrationError("parked_base_effect_boundary_breached")
    gate, action = str(base.get("effective_gate")), str(base.get("effective_action"))
    if gate not in {"PASS_SHADOW", "HOLD"} or (gate == "HOLD" and action != "WAIT"):
        raise ShadowIntegrationError("parked_base_decision_invalid")
    expected = sha256_obj({k: v for k, v in base.items() if k != "closure_sha256"})
    if base.get("closure_sha256") != expected:
        raise ShadowIntegrationError("parked_base_hash_mismatch")
    return str(base["closure_sha256"]), str(base.get("transaction_sha256")), gate, action


def build_default_parked_nontrading_evidence() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for node_id, spec in PARKED_SPECS.items():
        row = {
            "node_id": node_id,
            "evidence_class": "BOUNDED_PARKED_POSTURE_ONLY",
            "current_posture": spec["posture"],
        }
        for field in spec["proof_fields"]:
            row[field] = False
        for field in _FORBIDDEN_EFFECT_FIELDS:
            row[field] = False
        result[node_id] = row
    return result


def _build_receipt(node_id: str, evidence: Mapping[str, Any], *, base_sha: str, tx_sha: str) -> dict[str, Any]:
    if node_id not in PARKED_SPECS or not isinstance(evidence, Mapping):
        raise ShadowIntegrationError("parked_invalid_evidence")
    spec = PARKED_SPECS[node_id]
    row = dict(evidence)
    for field in _FORBIDDEN_EFFECT_FIELDS:
        if row.get(field) is not False:
            raise ShadowIntegrationError(f"parked_effect_boundary_breached:{node_id}:{field}")
    required = tuple(spec["proof_fields"])
    missing = tuple(field for field in required if row.get(field) is not True)
    body = {
        "schema": PARKED_RECEIPT_SCHEMA,
        "node_id": node_id,
        "role": spec["role"],
        "source_closure_sha256": base_sha,
        "source_transaction_sha256": tx_sha,
        "current_posture": str(row.get("current_posture", spec["posture"])),
        "evidence_class": str(row.get("evidence_class", "UNKNOWN")),
        "required_proof_fields": required,
        "missing_proof_fields": missing,
        "proof_complete": not missing,
        "typed_contract_bound": True,
        "revival_authority": False,
        "case_influence_enabled": False,
        "decision_vote": False,
        "gate_effect": "NONE",
        "may_widen_gate": False,
        "execution_authority": "NONE",
        "scope_mixing_allowed": False,
        "effects": {
            "runtime_activation": False,
            "wallet_access": False,
            "signing": False,
            "order": False,
            "token_launch": False,
            "external_message": False,
            "deployment": False,
            "current_truth_write": False,
            "capital_effect": False,
        },
        "semantics": {
            "parasite_killer_is_not_parasite_hunter_game": True,
            "parked_status_is_not_revival_permission": True,
            "historical_artifact_is_not_current_runtime": True,
            "archive_preservation_is_not_product_activation": True,
            "proof_complete_does_not_reactivate_project": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["parked_receipt_sha256"] = sha256_obj(body)
    return body


def build_shadow_parked_nontrading_ledger(
    base_closure: Mapping[str, Any],
    evidence_bundle: Mapping[str, Mapping[str, Any]],
    *,
    generated_at: str,
) -> dict[str, Any]:
    base_sha, tx_sha, gate, action = _verify_base(base_closure)
    if not isinstance(evidence_bundle, Mapping) or set(evidence_bundle) != set(PARKED_NODES):
        raise ShadowIntegrationError("parked_coverage_mismatch")
    receipts = tuple(
        _build_receipt(node_id, evidence_bundle[node_id], base_sha=base_sha, tx_sha=tx_sha)
        for node_id in PARKED_NODES
    )
    body = {
        "schema": PARKED_LEDGER_SCHEMA,
        "generated_at": str(generated_at),
        "source_closure_sha256": base_sha,
        "source_transaction_sha256": tx_sha,
        "case_id": base_closure.get("case_id"),
        "base_gate": gate,
        "base_action": action,
        "parked_node_count": len(receipts),
        "all_parked_nodes_typed": True,
        "proof_complete_nodes": tuple(row["node_id"] for row in receipts if row["proof_complete"] is True),
        "receipts": receipts,
        "plane_rules": {
            "no_automatic_revival": True,
            "no_scope_mixing": True,
            "no_wallet_signing_or_orders": True,
            "no_token_launch": True,
            "no_external_messages": True,
            "no_gate_change": True,
        },
        "effects": {
            "runtime_activation": False,
            "wallet_access": False,
            "signing": False,
            "order": False,
            "token_launch": False,
            "external_message": False,
            "deployment": False,
            "current_truth_write": False,
            "capital_effect": False,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["parked_ledger_sha256"] = sha256_obj(body)
    return body


def _verify_ledger(ledger: Mapping[str, Any], *, base_sha: str, tx_sha: str, gate: str, action: str) -> str:
    if not isinstance(ledger, Mapping) or ledger.get("schema") != PARKED_LEDGER_SCHEMA:
        raise ShadowIntegrationError("parked_closure_wrong_ledger_schema")
    if ledger.get("source_closure_sha256") != base_sha or ledger.get("source_transaction_sha256") != tx_sha:
        raise ShadowIntegrationError("parked_closure_binding_mismatch")
    if ledger.get("base_gate") != gate or ledger.get("base_action") != action:
        raise ShadowIntegrationError("parked_closure_decision_mismatch")
    if ledger.get("parked_node_count") != len(PARKED_NODES) or ledger.get("all_parked_nodes_typed") is not True:
        raise ShadowIntegrationError("parked_closure_coverage_mismatch")
    _verify_safety(ledger, "ledger")
    if any(value is not False for value in (ledger.get("effects") or {}).values()):
        raise ShadowIntegrationError("parked_closure_effect_boundary_breached")
    receipts = ledger.get("receipts")
    if not isinstance(receipts, (list, tuple)) or tuple(row.get("node_id") for row in receipts) != PARKED_NODES:
        raise ShadowIntegrationError("parked_closure_receipt_identity_mismatch")
    for row in receipts:
        _verify_safety(row, "receipt")
        expected = sha256_obj({k: v for k, v in row.items() if k != "parked_receipt_sha256"})
        if row.get("parked_receipt_sha256") != expected:
            raise ShadowIntegrationError("parked_closure_receipt_hash_mismatch")
        if row.get("typed_contract_bound") is not True or row.get("revival_authority") is not False:
            raise ShadowIntegrationError("parked_closure_revival_authority_detected")
        if row.get("case_influence_enabled") is not False or row.get("decision_vote") is not False or row.get("gate_effect") != "NONE":
            raise ShadowIntegrationError("parked_closure_influence_detected")
        if row.get("may_widen_gate") is not False or row.get("execution_authority") != "NONE" or row.get("scope_mixing_allowed") is not False:
            raise ShadowIntegrationError("parked_closure_authority_or_scope_mix")
        if any(value is not False for value in (row.get("effects") or {}).values()):
            raise ShadowIntegrationError("parked_closure_receipt_effect_boundary_breached")
    expected = sha256_obj({k: v for k, v in ledger.items() if k != "parked_ledger_sha256"})
    if ledger.get("parked_ledger_sha256") != expected:
        raise ShadowIntegrationError("parked_closure_ledger_hash_mismatch")
    return str(ledger["parked_ledger_sha256"])


def build_unified_shadow_parked_nontrading_closure(
    base_closure: Mapping[str, Any],
    parked_ledger: Mapping[str, Any],
    *,
    closed_at: str,
) -> dict[str, Any]:
    base_sha, tx_sha, gate, action = _verify_base(base_closure)
    ledger_sha = _verify_ledger(parked_ledger, base_sha=base_sha, tx_sha=tx_sha, gate=gate, action=action)
    body = {
        "schema": FINAL_CLOSURE_SCHEMA,
        "closed_at": str(closed_at),
        "case_id": base_closure.get("case_id"),
        "transaction_sha256": tx_sha,
        "base_closure_sha256": base_sha,
        "parked_ledger_sha256": ledger_sha,
        "registered_node_count": 63,
        "typed_parked_node_count": len(PARKED_NODES),
        "effective_gate": gate,
        "effective_action": action,
        "status": "P0_SHADOW_CLOSED_NO_EFFECT",
        "planes": {
            **dict(base_closure.get("planes") or {}),
            "parked_nontrading_plane": "BOUND_5_OF_5_NO_REVIVAL_OR_EFFECT_AUTHORITY",
        },
        "effect_summary": {
            **dict(base_closure.get("effect_summary") or {}),
            "parked_runtime_activation": False,
            "parked_wallet_access": False,
            "parked_signing": False,
            "parked_order": False,
            "parked_token_launch": False,
            "parked_external_message": False,
            "parked_deployment": False,
            "parked_vote": False,
        },
        "parked_status": {
            "typed_nodes": len(PARKED_NODES),
            "proof_complete_nodes": tuple(parked_ledger.get("proof_complete_nodes") or ()),
            "revival_authority": False,
            "scope_mixing_allowed": False,
            "effect_authority": False,
        },
        "semantics": {
            "containment_is_not_execution": True,
            "game_and_nft_bot_remain_separate": True,
            "parked_parent_blocks_dependent_token_launch": True,
            "archive_only_remains_archive_only": True,
            "base_gate_and_action_are_preserved_exactly": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["closure_sha256"] = sha256_obj(body)
    return body

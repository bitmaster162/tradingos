#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj

BASE_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v10"
PRODUCT_LEDGER_SCHEMA = "bitevo.shadow_product_service_ledger.v1"
PRODUCT_RECEIPT_SCHEMA = "bitevo.shadow_product_service_receipt.v1"
FINAL_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v11"

PRODUCT_NODES = (
    "portfolio:bitevo-ai-portal",
    "portfolio:crypto-guides",
    "portfolio:inner-circle",
    "portfolio:operator-decision-sprint",
    "portfolio:ai-agent-reliability-audit",
    "portfolio:ai-client-hunter",
    "portfolio:blockchain-forensics-osint",
    "entity:physical_ai_cosmos",
)

PRODUCT_SPECS: dict[str, dict[str, Any]] = {
    "portfolio:bitevo-ai-portal": {
        "role": "PUBLIC_LEAD_AND_PRODUCT_SURFACE",
        "posture": "PUBLIC_SURFACE_BINDING_AND_CONVERSION_PROOF_REQUIRED",
        "proof_fields": ("source_identity_verified", "public_surface_binding_verified", "telemetry_provenance_verified", "conversion_path_verified"),
    },
    "portfolio:crypto-guides": {
        "role": "PUBLIC_KNOWLEDGE_AND_ACQUISITION_SURFACE",
        "posture": "EDITORIAL_FRESHNESS_AND_CONVERSION_PROOF_REQUIRED",
        "proof_fields": ("source_identity_verified", "content_freshness_verified", "factual_quality_verified", "cta_binding_verified", "conversion_analytics_verified"),
    },
    "portfolio:inner-circle": {
        "role": "SUBSCRIPTION_PRODUCT_CANDIDATE",
        "posture": "CURRENT_CHANNEL_BOT_PAYMENT_STATE_UNPROVEN",
        "proof_fields": ("source_identity_verified", "channel_or_bot_current_state_verified", "payment_path_verified", "first_subscriber_verified"),
    },
    "portfolio:operator-decision-sprint": {
        "role": "MANUAL_OPERATOR_DECISION_SERVICE",
        "posture": "DEFINED_SERVICE_NO_PAYMENT_PROOF",
        "proof_fields": ("service_definition_verified", "payment_receipt_verified", "delivery_receipt_verified", "acceptance_or_rejection_receipt_verified", "human_outreach_gate_verified"),
    },
    "portfolio:ai-agent-reliability-audit": {
        "role": "MANUAL_AI_RELIABILITY_AUDIT_SERVICE",
        "posture": "PUBLIC_OFFER_NO_EXTERNAL_CUSTOMER_PROOF",
        "proof_fields": ("offer_binding_verified", "external_customer_verified", "delivery_receipt_verified", "buyer_acceptance_verified"),
    },
    "portfolio:ai-client-hunter": {
        "role": "HUMAN_REVIEWED_LOCAL_LEAD_DISCOVERY_PILOT",
        "posture": "HOLD_MANUAL_VALIDATION_ONLY",
        "proof_fields": ("manual_pilot_scope_verified", "lawful_or_consent_basis_verified", "channel_safe_outreach_verified", "buyer_evidence_verified", "human_review_gate_verified"),
    },
    "portfolio:blockchain-forensics-osint": {
        "role": "CASE_SCOPED_FORENSICS_AND_OSINT_SERVICE",
        "posture": "SEPARATE_CONFIDENTIAL_CASE_SCOPES_REQUIRED",
        "proof_fields": ("case_scope_verified", "provenance_verified", "lawful_handling_verified", "redaction_boundary_verified", "confidential_public_separation_verified"),
    },
    "entity:physical_ai_cosmos": {
        "role": "FUTURE_PHYSICAL_AI_INTEGRATION_CANDIDATE",
        "posture": "REGISTERED_ONLY_SOURCE_RUNTIME_UNBOUND",
        "proof_fields": ("source_identity_verified", "bounded_use_case_verified", "simulation_evidence_verified", "safety_boundary_verified", "runtime_identity_verified"),
    },
}

_FORBIDDEN_EFFECT_FIELDS = (
    "external_message_sent",
    "payment_mutated",
    "entitlement_mutated",
    "deployment_performed",
    "current_truth_written",
    "trade_action_performed",
    "capital_effect",
)


def _verify_safety(value: Mapping[str, Any], field: str) -> None:
    safety = value.get("safety") if isinstance(value, Mapping) else None
    if not isinstance(safety, Mapping):
        raise ShadowIntegrationError(f"product_service_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"product_service_unsafe_{field}:{key}")


def _verify_base(base: Mapping[str, Any]) -> tuple[str, str, str, str]:
    if not isinstance(base, Mapping) or base.get("schema") != BASE_CLOSURE_SCHEMA:
        raise ShadowIntegrationError("product_service_wrong_base_schema")
    if base.get("registered_node_count") != 63 or base.get("status") != "P0_SHADOW_CLOSED_NO_EFFECT":
        raise ShadowIntegrationError("product_service_base_state_mismatch")
    _verify_safety(base, "base")
    effects = base.get("effect_summary")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("product_service_base_effect_boundary_breached")
    gate = str(base.get("effective_gate"))
    action = str(base.get("effective_action"))
    if gate not in {"PASS_SHADOW", "HOLD"} or (gate == "HOLD" and action != "WAIT"):
        raise ShadowIntegrationError("product_service_base_decision_invalid")
    expected = sha256_obj({k: v for k, v in base.items() if k != "closure_sha256"})
    if base.get("closure_sha256") != expected:
        raise ShadowIntegrationError("product_service_base_hash_mismatch")
    return str(base["closure_sha256"]), str(base.get("transaction_sha256")), gate, action


def build_default_product_service_evidence() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for node_id, spec in PRODUCT_SPECS.items():
        row = {
            "node_id": node_id,
            "evidence_class": "BOUNDED_PRODUCT_POSTURE_ONLY",
            "current_posture": spec["posture"],
        }
        for field in spec["proof_fields"]:
            row[field] = False
        for field in _FORBIDDEN_EFFECT_FIELDS:
            row[field] = False
        result[node_id] = row
    return result


def _build_receipt(node_id: str, evidence: Mapping[str, Any], *, base_sha: str, tx_sha: str) -> dict[str, Any]:
    if node_id not in PRODUCT_SPECS or not isinstance(evidence, Mapping):
        raise ShadowIntegrationError("product_service_invalid_evidence")
    spec = PRODUCT_SPECS[node_id]
    row = dict(evidence)
    for field in _FORBIDDEN_EFFECT_FIELDS:
        if row.get(field) is not False:
            raise ShadowIntegrationError(f"product_service_effect_boundary_breached:{node_id}:{field}")
    required = tuple(spec["proof_fields"])
    missing = tuple(field for field in required if row.get(field) is not True)
    body = {
        "schema": PRODUCT_RECEIPT_SCHEMA,
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
        "case_influence_enabled": False,
        "decision_vote": False,
        "gate_effect": "NONE",
        "may_widen_gate": False,
        "current_truth_authority": "NONE",
        "payment_authority": "NONE",
        "entitlement_authority": "NONE",
        "external_message_authority": "NONE",
        "execution_authority": "NONE",
        "effects": {
            "external_message": False,
            "payment_mutation": False,
            "entitlement_mutation": False,
            "deployment": False,
            "current_truth_write": False,
            "signal": False,
            "order": False,
            "capital_effect": False,
        },
        "semantics": {
            "public_url_is_not_live_backend_proof": True,
            "offer_is_not_customer_proof": True,
            "customer_is_not_payment_proof": True,
            "payment_is_not_authority": True,
            "deployment_is_not_product_validation": True,
            "commercial_relevance_is_not_trade_influence": True,
            "proof_complete_does_not_grant_case_influence": True,
            "confidential_case_data_must_not_become_public_product_evidence": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["product_receipt_sha256"] = sha256_obj(body)
    return body


def build_shadow_product_service_ledger(
    base_closure: Mapping[str, Any],
    evidence_bundle: Mapping[str, Mapping[str, Any]],
    *,
    generated_at: str,
) -> dict[str, Any]:
    base_sha, tx_sha, gate, action = _verify_base(base_closure)
    if not isinstance(evidence_bundle, Mapping) or set(evidence_bundle) != set(PRODUCT_NODES):
        raise ShadowIntegrationError("product_service_coverage_mismatch")
    receipts = tuple(
        _build_receipt(node_id, evidence_bundle[node_id], base_sha=base_sha, tx_sha=tx_sha)
        for node_id in PRODUCT_NODES
    )
    body = {
        "schema": PRODUCT_LEDGER_SCHEMA,
        "generated_at": str(generated_at),
        "source_closure_sha256": base_sha,
        "source_transaction_sha256": tx_sha,
        "case_id": base_closure.get("case_id"),
        "base_gate": gate,
        "base_action": action,
        "product_node_count": len(receipts),
        "all_product_nodes_typed": True,
        "proof_complete_nodes": tuple(row["node_id"] for row in receipts if row["proof_complete"] is True),
        "receipts": receipts,
        "plane_rules": {
            "product_plane_cannot_change_decision": True,
            "proof_completion_does_not_grant_authority": True,
            "human_review_required_before_external_message": True,
            "payment_and_entitlement_mutation_disabled": True,
            "deployment_disabled": True,
            "confidential_public_separation_required": True,
        },
        "effects": {
            "external_message": False,
            "payment_mutation": False,
            "entitlement_mutation": False,
            "deployment": False,
            "current_truth_write": False,
            "signal": False,
            "order": False,
            "capital_effect": False,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["product_ledger_sha256"] = sha256_obj(body)
    return body


def _verify_ledger(ledger: Mapping[str, Any], *, base_sha: str, tx_sha: str, gate: str, action: str) -> str:
    if not isinstance(ledger, Mapping) or ledger.get("schema") != PRODUCT_LEDGER_SCHEMA:
        raise ShadowIntegrationError("product_service_closure_wrong_ledger_schema")
    if ledger.get("source_closure_sha256") != base_sha or ledger.get("source_transaction_sha256") != tx_sha:
        raise ShadowIntegrationError("product_service_closure_binding_mismatch")
    if ledger.get("base_gate") != gate or ledger.get("base_action") != action:
        raise ShadowIntegrationError("product_service_closure_decision_mismatch")
    if ledger.get("product_node_count") != len(PRODUCT_NODES) or ledger.get("all_product_nodes_typed") is not True:
        raise ShadowIntegrationError("product_service_closure_coverage_mismatch")
    _verify_safety(ledger, "ledger")
    if any(value is not False for value in (ledger.get("effects") or {}).values()):
        raise ShadowIntegrationError("product_service_closure_effect_boundary_breached")
    receipts = ledger.get("receipts")
    if not isinstance(receipts, (list, tuple)) or tuple(row.get("node_id") for row in receipts) != PRODUCT_NODES:
        raise ShadowIntegrationError("product_service_closure_receipt_identity_mismatch")
    for row in receipts:
        _verify_safety(row, "receipt")
        expected = sha256_obj({k: v for k, v in row.items() if k != "product_receipt_sha256"})
        if row.get("product_receipt_sha256") != expected:
            raise ShadowIntegrationError("product_service_closure_receipt_hash_mismatch")
        if row.get("typed_contract_bound") is not True or row.get("case_influence_enabled") is not False:
            raise ShadowIntegrationError("product_service_closure_influence_detected")
        if row.get("decision_vote") is not False or row.get("gate_effect") != "NONE" or row.get("may_widen_gate") is not False:
            raise ShadowIntegrationError("product_service_closure_vote_or_gate_change")
        for field in ("current_truth_authority", "payment_authority", "entitlement_authority", "external_message_authority", "execution_authority"):
            if row.get(field) != "NONE":
                raise ShadowIntegrationError(f"product_service_closure_authority_detected:{field}")
        if any(value is not False for value in (row.get("effects") or {}).values()):
            raise ShadowIntegrationError("product_service_closure_receipt_effect_boundary_breached")
    expected = sha256_obj({k: v for k, v in ledger.items() if k != "product_ledger_sha256"})
    if ledger.get("product_ledger_sha256") != expected:
        raise ShadowIntegrationError("product_service_closure_ledger_hash_mismatch")
    return str(ledger["product_ledger_sha256"])


def build_unified_shadow_product_service_closure(
    base_closure: Mapping[str, Any],
    product_ledger: Mapping[str, Any],
    *,
    closed_at: str,
) -> dict[str, Any]:
    base_sha, tx_sha, gate, action = _verify_base(base_closure)
    ledger_sha = _verify_ledger(product_ledger, base_sha=base_sha, tx_sha=tx_sha, gate=gate, action=action)
    body = {
        "schema": FINAL_CLOSURE_SCHEMA,
        "closed_at": str(closed_at),
        "case_id": base_closure.get("case_id"),
        "transaction_sha256": tx_sha,
        "base_closure_sha256": base_sha,
        "product_ledger_sha256": ledger_sha,
        "registered_node_count": 63,
        "typed_product_node_count": len(PRODUCT_NODES),
        "effective_gate": gate,
        "effective_action": action,
        "status": "P0_SHADOW_CLOSED_NO_EFFECT",
        "planes": {
            **dict(base_closure.get("planes") or {}),
            "product_service_plane": "BOUND_8_OF_8_ACCOUNTED_NO_DECISION_OR_EFFECT_AUTHORITY",
        },
        "effect_summary": {
            **dict(base_closure.get("effect_summary") or {}),
            "product_external_message": False,
            "product_payment_mutation": False,
            "product_entitlement_mutation": False,
            "product_deployment": False,
            "product_current_truth_write": False,
            "product_vote": False,
        },
        "product_status": {
            "typed_nodes": len(PRODUCT_NODES),
            "proof_complete_nodes": tuple(product_ledger.get("proof_complete_nodes") or ()),
            "case_influence_enabled": False,
            "payment_authority": False,
            "external_message_authority": False,
            "effect_authority": False,
        },
        "semantics": {
            "commercial_surface_is_not_current_truth": True,
            "customer_payment_delivery_are_separate_claims": True,
            "product_proof_does_not_change_frozen_trade_case": True,
            "base_gate_and_action_are_preserved_exactly": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["closure_sha256"] = sha256_obj(body)
    return body

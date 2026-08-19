#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj

BASE_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v6"
ADVISORY_LEDGER_SCHEMA = "bitevo.shadow_trading_advisory_ledger.v1"
ADVISORY_RECEIPT_SCHEMA = "bitevo.shadow_trading_advisory_receipt.v1"

ADVISORY_NODES = (
    "portfolio:edge-research-lab",
    "portfolio:arb-radar",
    "portfolio:grid-os",
    "portfolio:delist-drs",
    "portfolio:sovereign-api-core-bot",
)

ADVISORY_SPECS: dict[str, dict[str, Any]] = {
    "portfolio:edge-research-lab": {
        "role": "PREREGISTERED_HYPOTHESIS_DISCOVERY_AND_FALSIFICATION",
        "current_posture": "RETURN_RESOLUTION_REQUIRED",
        "required_bool_fields": (
            "source_identity_verified",
            "preregistered_hypothesis_receipt_verified",
            "strict_return_verified",
            "independent_replay_verified",
        ),
        "allowed_findings": {"NO_OBJECTION", "RISK_FLAG", "STALE_OR_UNKNOWN"},
        "allowed_outcomes": {"KEEP", "KILL", "INSUFFICIENT_DATA", "UNRESOLVED"},
    },
    "portfolio:arb-radar": {
        "role": "READ_ONLY_ARBITRAGE_FUNDING_CARRY_EVIDENCE",
        "current_posture": "MEASUREMENT_REPAIR_REQUIRED",
        "required_bool_fields": (
            "source_identity_verified",
            "measurement_semantics_verified",
            "cost_model_verified",
            "entry_exit_semantics_verified",
            "bounded_paper_comparison_verified",
            "freshness_verified",
        ),
        "allowed_findings": {"NO_OBJECTION", "RISK_FLAG", "STALE_OR_UNKNOWN"},
        "allowed_outcomes": {"KEEP", "REPAIR", "HOLD", "UNRESOLVED"},
    },
    "portfolio:grid-os": {
        "role": "PAPER_ONLY_GRID_POLICY_AND_EVIDENCE",
        "current_posture": "CURRENT_IMPLEMENTATION_UNVERIFIED",
        "required_bool_fields": (
            "source_identity_verified",
            "paper_only_boundary_verified",
            "policy_schema_verified",
            "stop_inventory_policy_verified",
            "pnl_evidence_ledger_verified",
            "replay_verified",
        ),
        "allowed_findings": {"NO_OBJECTION", "RISK_FLAG", "STALE_OR_UNKNOWN"},
        "allowed_outcomes": {"KEEP", "REVISE_POLICY", "HOLD", "UNRESOLVED"},
    },
    "portfolio:delist-drs": {
        "role": "EXPLAINABLE_CONTINUITY_RISK_MONITORING",
        "current_posture": "EXISTING_SURFACE_FRESHNESS_UNVERIFIED",
        "required_bool_fields": (
            "source_identity_verified",
            "endpoint_verified",
            "watchlist_freshness_verified",
            "reason_codes_verified",
            "timestamp_provenance_verified",
            "event_taxonomy_verified",
        ),
        "allowed_findings": {"NO_OBJECTION", "RISK_FLAG", "STALE_OR_UNKNOWN"},
        "allowed_outcomes": {"KEEP_EXISTING", "REPAIR", "MERGE", "KILL", "UNRESOLVED"},
    },
    "portfolio:sovereign-api-core-bot": {
        "role": "READ_ONLY_API_FACADE_STATUS_PROVENANCE_EXPORT",
        "current_posture": "CURRENT_STATE_RECAPTURE_REQUIRED",
        "required_bool_fields": (
            "source_identity_verified",
            "status_health_verified",
            "auth_boundary_verified",
            "null_stale_degraded_semantics_verified",
            "integration_receipt_verified",
            "runtime_lineage_verified",
        ),
        "allowed_findings": {"NO_OBJECTION", "RISK_FLAG", "STALE_OR_UNKNOWN"},
        "allowed_outcomes": {"KEEP_INTERNAL_API", "MERGE", "REPLACE", "HOLD", "ARCHIVE", "KILL", "UNRESOLVED"},
    },
}


def _verify_safety(value: Mapping[str, Any], field: str) -> None:
    safety = value.get("safety") if isinstance(value, Mapping) else None
    if not isinstance(safety, Mapping):
        raise ShadowIntegrationError(f"trading_advisory_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"trading_advisory_unsafe_{field}:{key}")


def _verify_base_closure(closure: Mapping[str, Any]) -> tuple[str, str, str, str]:
    if not isinstance(closure, Mapping) or closure.get("schema") != BASE_CLOSURE_SCHEMA:
        raise ShadowIntegrationError("trading_advisory_wrong_base_schema")
    if closure.get("registered_node_count") != 63:
        raise ShadowIntegrationError("trading_advisory_registry_count_mismatch")
    if closure.get("status") != "P0_SHADOW_CLOSED_NO_EFFECT":
        raise ShadowIntegrationError("trading_advisory_base_status_mismatch")
    _verify_safety(closure, "base")
    effects = closure.get("effect_summary")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("trading_advisory_base_effect_boundary_breached")
    gate = str(closure.get("effective_gate"))
    action = str(closure.get("effective_action"))
    if gate not in {"PASS_SHADOW", "HOLD"}:
        raise ShadowIntegrationError("trading_advisory_base_gate_invalid")
    if gate == "HOLD" and action != "WAIT":
        raise ShadowIntegrationError("trading_advisory_base_hold_must_wait")
    expected_sha = sha256_obj({k: v for k, v in closure.items() if k != "closure_sha256"})
    if closure.get("closure_sha256") != expected_sha:
        raise ShadowIntegrationError("trading_advisory_base_hash_mismatch")
    return str(closure["closure_sha256"]), str(closure.get("transaction_sha256")), gate, action


def build_default_trading_advisory_evidence() -> dict[str, dict[str, Any]]:
    """Return the bounded current P0 posture without pretending current source/runtime proof exists."""
    defaults: dict[str, dict[str, Any]] = {}
    for node_id, spec in ADVISORY_SPECS.items():
        row = {
            "node_id": node_id,
            "evidence_class": "BOUNDED_INTERNAL_POSTURE_ONLY",
            "current_posture": spec["current_posture"],
            "finding": "STALE_OR_UNKNOWN",
            "outcome": "UNRESOLVED",
            "source_identity_verified": False,
            "runtime_verified": False,
            "effectful_surface_enabled": False,
            "trade_signal_emitted": False,
            "order_emitted": False,
            "capital_effect": False,
        }
        for field in spec["required_bool_fields"]:
            row.setdefault(field, False)
        defaults[node_id] = row
    return defaults


def _normalize_evidence(node_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
    if node_id not in ADVISORY_SPECS:
        raise ShadowIntegrationError("trading_advisory_unknown_node")
    if not isinstance(value, Mapping):
        raise ShadowIntegrationError("trading_advisory_evidence_must_be_mapping")
    spec = ADVISORY_SPECS[node_id]
    finding = str(value.get("finding", "STALE_OR_UNKNOWN"))
    outcome = str(value.get("outcome", "UNRESOLVED"))
    if finding not in spec["allowed_findings"]:
        raise ShadowIntegrationError(f"trading_advisory_invalid_finding:{node_id}")
    if outcome not in spec["allowed_outcomes"]:
        raise ShadowIntegrationError(f"trading_advisory_invalid_outcome:{node_id}")

    normalized = dict(value)
    normalized["node_id"] = node_id
    normalized["finding"] = finding
    normalized["outcome"] = outcome
    for forbidden in ("effectful_surface_enabled", "trade_signal_emitted", "order_emitted", "capital_effect"):
        if normalized.get(forbidden) is not False:
            raise ShadowIntegrationError(f"trading_advisory_effect_boundary_breached:{node_id}:{forbidden}")
    if normalized.get("runtime_verified") is True and normalized.get("source_identity_verified") is not True:
        raise ShadowIntegrationError(f"trading_advisory_runtime_without_source_identity:{node_id}")
    return normalized


def _build_receipt(
    *,
    node_id: str,
    evidence: Mapping[str, Any],
    transaction_sha: str,
    base_closure_sha: str,
) -> dict[str, Any]:
    spec = ADVISORY_SPECS[node_id]
    normalized = _normalize_evidence(node_id, evidence)
    missing = tuple(field for field in spec["required_bool_fields"] if normalized.get(field) is not True)
    admitted = not missing

    gate_effect = "NONE"
    if admitted and normalized["finding"] == "RISK_FLAG":
        gate_effect = "NARROW_TO_HOLD"
    if admitted and node_id == "portfolio:edge-research-lab" and normalized["outcome"] in {"KILL", "INSUFFICIENT_DATA"}:
        gate_effect = "NARROW_TO_HOLD"

    body = {
        "schema": ADVISORY_RECEIPT_SCHEMA,
        "node_id": node_id,
        "role": spec["role"],
        "source_transaction_sha256": transaction_sha,
        "source_closure_sha256": base_closure_sha,
        "evidence_class": str(normalized.get("evidence_class", "UNKNOWN")),
        "current_posture": str(normalized.get("current_posture", spec["current_posture"])),
        "finding": normalized["finding"],
        "outcome": normalized["outcome"],
        "required_proof_fields": tuple(spec["required_bool_fields"]),
        "missing_proof_fields": missing,
        "typed_contract_bound": True,
        "admitted_for_narrowing": admitted,
        "gate_effect": gate_effect,
        "may_widen_gate": False,
        "trading_vote": False,
        "execution_authority": "NONE",
        "source_identity_proven_by_contract": admitted and normalized.get("source_identity_verified") is True,
        "runtime_proven_by_contract": admitted and normalized.get("runtime_verified") is True,
        "external_runtime_invoked": False,
        "effects": {
            "tool_call": False,
            "runtime_activation": False,
            "signal": False,
            "order": False,
            "capital_effect": False,
            "current_truth_apply": False,
        },
        "semantics": {
            "typed_contract_is_not_execution_permission": True,
            "admission_can_only_narrow": True,
            "missing_proof_means_no_influence": True,
            "no_objection_does_not_authorize_trade": True,
            "advisory_output_is_not_majority_vote": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["advisory_receipt_sha256"] = sha256_obj(body)
    return body


def build_shadow_trading_advisory_ledger(
    base_closure: Mapping[str, Any],
    evidence_bundle: Mapping[str, Mapping[str, Any]],
    *,
    generated_at: str,
) -> dict[str, Any]:
    base_sha, transaction_sha, base_gate, base_action = _verify_base_closure(base_closure)
    if not isinstance(evidence_bundle, Mapping):
        raise ShadowIntegrationError("trading_advisory_evidence_bundle_missing")
    if set(evidence_bundle) != set(ADVISORY_NODES):
        missing = sorted(set(ADVISORY_NODES) - set(evidence_bundle))
        unknown = sorted(set(evidence_bundle) - set(ADVISORY_NODES))
        raise ShadowIntegrationError(
            "trading_advisory_coverage_mismatch:missing=" + ",".join(missing) + ";unknown=" + ",".join(unknown)
        )

    receipts = tuple(
        _build_receipt(
            node_id=node_id,
            evidence=evidence_bundle[node_id],
            transaction_sha=transaction_sha,
            base_closure_sha=base_sha,
        )
        for node_id in ADVISORY_NODES
    )
    narrowing = tuple(row["node_id"] for row in receipts if row["gate_effect"] == "NARROW_TO_HOLD")
    admitted = tuple(row["node_id"] for row in receipts if row["admitted_for_narrowing"] is True)
    not_admitted = tuple(row["node_id"] for row in receipts if row["admitted_for_narrowing"] is False)

    body = {
        "schema": ADVISORY_LEDGER_SCHEMA,
        "generated_at": str(generated_at),
        "source_closure_sha256": base_sha,
        "source_transaction_sha256": transaction_sha,
        "case_id": base_closure.get("case_id"),
        "base_gate": base_gate,
        "base_action": base_action,
        "advisory_node_count": len(receipts),
        "all_advisory_nodes_typed": True,
        "admitted_nodes": admitted,
        "not_admitted_nodes": not_admitted,
        "narrowing_nodes": narrowing,
        "receipts": receipts,
        "bus_rules": {
            "typed_receipt_required_for_admission": True,
            "missing_proof_fails_closed_to_no_influence": True,
            "advisory_may_only_narrow": True,
            "advisory_cannot_turn_hold_into_pass": True,
            "advisory_cannot_emit_signal_or_order": True,
            "source_identity_and_runtime_are_separate": True,
        },
        "effects": {
            "runtime_invocation": False,
            "tool_call": False,
            "signal": False,
            "order": False,
            "capital_effect": False,
            "current_truth_apply": False,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["advisory_ledger_sha256"] = sha256_obj(body)
    return body

#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Mapping

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, sha256_obj
from tools.unified_shadow_trading_advisory import ADVISORY_LEDGER_SCHEMA, ADVISORY_NODES

BASE_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v6"
FINAL_CLOSURE_SCHEMA = "bitevo.unified_shadow_closure.v8"


def _verify_safety(value: Mapping[str, Any], field: str) -> None:
    safety = value.get("safety") if isinstance(value, Mapping) else None
    if not isinstance(safety, Mapping):
        raise ShadowIntegrationError(f"advisory_closure_{field}_safety_missing")
    for key, expected in SHADOW_SAFETY.items():
        if safety.get(key) != expected or type(safety.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"advisory_closure_unsafe_{field}:{key}")


def _verify_hash(value: Mapping[str, Any], field: str, code: str) -> str:
    expected = sha256_obj({k: v for k, v in value.items() if k != field})
    if value.get(field) != expected:
        raise ShadowIntegrationError(code)
    return str(value[field])


def _verify_base(base: Mapping[str, Any]) -> tuple[str, str, str, str]:
    if not isinstance(base, Mapping) or base.get("schema") != BASE_CLOSURE_SCHEMA:
        raise ShadowIntegrationError("advisory_closure_wrong_base_schema")
    if base.get("registered_node_count") != 63:
        raise ShadowIntegrationError("advisory_closure_registry_count_mismatch")
    if base.get("status") != "P0_SHADOW_CLOSED_NO_EFFECT":
        raise ShadowIntegrationError("advisory_closure_base_status_mismatch")
    _verify_safety(base, "base")
    effects = base.get("effect_summary")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("advisory_closure_base_effect_boundary_breached")
    gate = str(base.get("effective_gate"))
    action = str(base.get("effective_action"))
    if gate not in {"PASS_SHADOW", "HOLD"}:
        raise ShadowIntegrationError("advisory_closure_base_gate_invalid")
    if gate == "HOLD" and action != "WAIT":
        raise ShadowIntegrationError("advisory_closure_hold_must_wait")
    return (
        _verify_hash(base, "closure_sha256", "advisory_closure_base_hash_mismatch"),
        str(base.get("transaction_sha256")),
        gate,
        action,
    )


def _verify_ledger(
    ledger: Mapping[str, Any],
    *,
    base_sha: str,
    transaction_sha: str,
    base_gate: str,
    base_action: str,
) -> str:
    if not isinstance(ledger, Mapping) or ledger.get("schema") != ADVISORY_LEDGER_SCHEMA:
        raise ShadowIntegrationError("advisory_closure_wrong_ledger_schema")
    if ledger.get("source_closure_sha256") != base_sha:
        raise ShadowIntegrationError("advisory_closure_ledger_base_mismatch")
    if ledger.get("source_transaction_sha256") != transaction_sha:
        raise ShadowIntegrationError("advisory_closure_ledger_transaction_mismatch")
    if ledger.get("base_gate") != base_gate or ledger.get("base_action") != base_action:
        raise ShadowIntegrationError("advisory_closure_ledger_decision_mismatch")
    if ledger.get("advisory_node_count") != len(ADVISORY_NODES):
        raise ShadowIntegrationError("advisory_closure_ledger_coverage_mismatch")
    if ledger.get("all_trading_advisory_nodes_typed") is not True:
        raise ShadowIntegrationError("advisory_closure_all_nodes_not_typed")
    _verify_safety(ledger, "ledger")
    effects = ledger.get("effects")
    if not isinstance(effects, Mapping) or any(value is not False for value in effects.values()):
        raise ShadowIntegrationError("advisory_closure_ledger_effect_boundary_breached")

    rules = ledger.get("bus_rules") or {}
    for key in (
        "typed_receipt_required_for_admission",
        "case_relevance_required_for_influence",
        "pre_freeze_evidence_required_for_influence",
        "missing_proof_fails_closed_to_no_influence",
        "advisory_may_only_narrow",
        "advisory_cannot_turn_hold_into_pass",
        "advisory_cannot_emit_signal_or_order",
        "source_identity_and_runtime_are_separate",
        "legacy_presence_is_not_current_case_relevance",
    ):
        if rules.get(key) is not True:
            raise ShadowIntegrationError(f"advisory_closure_rule_missing:{key}")

    receipts = ledger.get("receipts")
    if not isinstance(receipts, (tuple, list)) or len(receipts) != len(ADVISORY_NODES):
        raise ShadowIntegrationError("advisory_closure_receipt_count_mismatch")
    ids = tuple(row.get("node_id") for row in receipts if isinstance(row, Mapping))
    if ids != ADVISORY_NODES:
        raise ShadowIntegrationError("advisory_closure_receipt_identity_mismatch")

    narrowing = []
    for row in receipts:
        if not isinstance(row, Mapping):
            raise ShadowIntegrationError("advisory_closure_invalid_receipt")
        _verify_safety(row, "receipt")
        _verify_hash(row, "advisory_receipt_sha256", "advisory_closure_receipt_hash_mismatch")
        if row.get("typed_contract_bound") is not True:
            raise ShadowIntegrationError("advisory_closure_untyped_receipt")
        if row.get("may_widen_gate") is not False:
            raise ShadowIntegrationError("advisory_closure_gate_widening_detected")
        if row.get("trading_vote") is not False:
            raise ShadowIntegrationError("advisory_closure_trading_vote_detected")
        if row.get("execution_authority") != "NONE":
            raise ShadowIntegrationError("advisory_closure_execution_authority_detected")
        if row.get("external_runtime_invoked") is not False:
            raise ShadowIntegrationError("advisory_closure_runtime_invocation_detected")
        row_effects = row.get("effects")
        if not isinstance(row_effects, Mapping) or any(value is not False for value in row_effects.values()):
            raise ShadowIntegrationError("advisory_closure_receipt_effect_boundary_breached")
        gate_effect = row.get("gate_effect")
        if gate_effect not in {"NONE", "NARROW_TO_HOLD"}:
            raise ShadowIntegrationError("advisory_closure_invalid_gate_effect")
        if gate_effect == "NARROW_TO_HOLD":
            if row.get("admitted_for_narrowing") is not True:
                raise ShadowIntegrationError("advisory_closure_unadmitted_narrowing")
            if row.get("case_relevance_verified") is not True:
                raise ShadowIntegrationError("advisory_closure_irrelevant_narrowing")
            if row.get("pre_freeze_evidence_verified") is not True:
                raise ShadowIntegrationError("advisory_closure_post_freeze_narrowing")
            narrowing.append(row.get("node_id"))

    if tuple(narrowing) != tuple(ledger.get("narrowing_nodes") or ()):
        raise ShadowIntegrationError("advisory_closure_narrowing_index_mismatch")
    return _verify_hash(ledger, "advisory_ledger_sha256", "advisory_closure_ledger_hash_mismatch")


def build_unified_shadow_advisory_closure(
    base_closure: Mapping[str, Any],
    advisory_ledger: Mapping[str, Any],
    *,
    closed_at: str,
) -> dict[str, Any]:
    """Seal all trading-advisory contracts with relevance/pre-freeze gates and no effect authority."""
    base_sha, transaction_sha, base_gate, base_action = _verify_base(base_closure)
    ledger_sha = _verify_ledger(
        advisory_ledger,
        base_sha=base_sha,
        transaction_sha=transaction_sha,
        base_gate=base_gate,
        base_action=base_action,
    )
    narrowing_nodes = tuple(advisory_ledger.get("narrowing_nodes") or ())
    effective_gate = "HOLD" if base_gate == "HOLD" or narrowing_nodes else "PASS_SHADOW"
    effective_action = "WAIT" if effective_gate == "HOLD" else base_action

    body = {
        "schema": FINAL_CLOSURE_SCHEMA,
        "closed_at": str(closed_at),
        "case_id": base_closure.get("case_id"),
        "transaction_sha256": transaction_sha,
        "base_closure_sha256": base_sha,
        "advisory_ledger_sha256": ledger_sha,
        "registered_node_count": 63,
        "typed_advisory_node_count": len(ADVISORY_NODES),
        "effective_gate": effective_gate,
        "effective_action": effective_action,
        "status": "P0_SHADOW_CLOSED_NO_EFFECT",
        "planes": {
            **dict(base_closure.get("planes") or {}),
            "trading_advisory_contracts": "BOUND_9_OF_9_RELEVANCE_GATED_NARROW_ONLY",
        },
        "effect_summary": {
            **dict(base_closure.get("effect_summary") or {}),
            "advisory_runtime": False,
            "advisory_vote": False,
            "advisory_signal": False,
            "advisory_order": False,
            "advisory_capital_effect": False,
        },
        "advisory_status": {
            "typed_nodes": len(ADVISORY_NODES),
            "admitted_nodes": len(tuple(advisory_ledger.get("admitted_nodes") or ())),
            "not_admitted_nodes": len(tuple(advisory_ledger.get("not_admitted_nodes") or ())),
            "narrowing_nodes": narrowing_nodes,
            "case_relevance_required": True,
            "pre_freeze_evidence_required": True,
            "gate_widening_allowed": False,
            "execution_authority_granted": False,
        },
        "semantics": {
            "all_trading_advisory_nodes_have_typed_boundary": True,
            "typed_contract_is_weaker_than_current_source_runtime_proof": True,
            "missing_proof_means_no_influence": True,
            "unrelated_system_cannot_influence_frozen_case": True,
            "post_freeze_evidence_cannot_influence_frozen_case": True,
            "advisory_can_only_preserve_or_narrow_gate": True,
            "advisory_no_objection_is_not_trade_permission": True,
            "advisory_risk_flag_is_not_order_instruction": True,
            "hold_cannot_be_widened_by_advisory": True,
        },
        "safety": dict(SHADOW_SAFETY),
    }
    body["closure_sha256"] = sha256_obj(body)
    return body

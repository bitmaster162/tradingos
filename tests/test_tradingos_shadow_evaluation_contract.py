from __future__ import annotations

import copy
import pytest

from r81_shadow_evaluation_fixtures import m, memory_policy, shadow_policy, records, declaration, report


def test_shadow_policy_accepts_exact_v1():
    p = shadow_policy()
    m.validate_shadow_policy(p)
    assert p["mode"] == "OFFLINE_FROZEN_RECORD_SHADOW_ONLY"
    assert p["shadow_only"] is True
    assert p["memory_write_authority"] == "NONE"
    assert p["output_permissions"]["execution_authority"] == "NONE"
    assert p["output_permissions"]["can_trade"] is False
    assert p["output_permissions"]["capital_permission"] == "DENY"


@pytest.mark.parametrize("field", [
    "require_frozen_set_declaration",
    "require_all_declared_records",
    "shadow_only",
])
def test_policy_rejects_required_guard_disable(field):
    p = shadow_policy(); p[field] = False
    with pytest.raises(ValueError): m.validate_shadow_policy(p)


@pytest.mark.parametrize("field", [
    "allow_subset_evaluation", "allow_duplicate_record_ids", "mixed_memory_policy_allowed",
    "external_sources_allowed", "persistence_in_core_allowed", "pnl_fields_allowed",
    "price_return_fields_allowed", "probability_outputs_allowed", "rate_outputs_allowed",
    "confidence_outputs_allowed", "model_ranking_allowed", "provider_ranking_allowed",
    "auto_learning_allowed", "weight_update_allowed", "prompt_update_allowed",
    "model_selection_update_allowed", "policy_update_allowed", "live_decision_feedback_allowed",
    "live_decision_use_allowed", "model_selection_use_allowed",
])
def test_policy_rejects_unsafe_boolean_widening(field):
    p = shadow_policy(); p[field] = True
    with pytest.raises(ValueError): m.validate_shadow_policy(p)


@pytest.mark.parametrize("field,value", [
    ("mode", "LIVE"),
    ("input_record_schema", "other"),
    ("report_mode", "RATE"),
    ("memory_write_authority", "WRITE"),
])
def test_policy_rejects_contract_drift(field, value):
    p = shadow_policy(); p[field] = value
    with pytest.raises(ValueError): m.validate_shadow_policy(p)


def test_policy_rejects_extra_key():
    p = shadow_policy(); p["probability_calibration"] = True
    with pytest.raises(ValueError): m.validate_shadow_policy(p)


def test_policy_rejects_output_permission_widening():
    p = shadow_policy(); p["output_permissions"]["orders_allowed"] = True
    with pytest.raises(ValueError): m.validate_shadow_policy(p)


def test_build_declaration_positive():
    rs = records(); dec = declaration(rs)
    assert dec["record_count"] == 4
    assert len(dec["records"]) == 4
    assert dec["records"] == sorted(dec["records"], key=lambda row: row["record_id"])
    assert dec["shadow_only"] is True
    assert dec["execution_authority"] == "NONE"
    assert dec["can_trade"] is False


def test_declaration_is_input_order_independent():
    rs = records()
    assert declaration(rs) == declaration(list(reversed(rs)))


def test_declaration_empty_set_allowed():
    dec = declaration([])
    assert dec["record_count"] == 0
    assert dec["records"] == []


def test_build_report_positive():
    rep = report()
    assert rep["record_count"] == 4
    assert rep["claim_count"] == 4
    assert rep["counts_by_outcome"] == {
        "SUPPORTED": 1, "CONTRADICTED": 1, "UNRESOLVED": 1, "NOT_EVALUABLE": 1
    }
    assert sum(rep["counts_by_claim_kind"].values()) == 4
    assert rep["report_mode"] == "COUNT_AND_INTEGRITY_ONLY"
    assert all(rep["integrity"].values())
    assert rep["shadow_only"] is True
    assert rep["can_trade"] is False
    assert rep["capital_permission"] == "DENY"


def test_report_is_input_order_independent():
    rs = records(); dec = declaration(rs)
    assert m.build_shadow_report(rs, dec, memory_policy(), shadow_policy()) == m.build_shadow_report(
        list(reversed(rs)), dec, memory_policy(), shadow_policy()
    )


def test_empty_report_allowed():
    dec = declaration([])
    rep = m.build_shadow_report([], dec, memory_policy(), shadow_policy())
    assert rep["record_count"] == 0
    assert rep["claim_count"] == 0
    assert sum(rep["counts_by_outcome"].values()) == 0


@pytest.mark.parametrize("field,value", [
    ("shadow_only", False),
    ("memory_write_authority", "WRITE"),
    ("live_decision_use_allowed", True),
    ("model_selection_use_allowed", True),
    ("execution_authority", "TRADE"),
    ("can_trade", True),
    ("capital_permission", "ALLOW"),
    ("confers_authority", True),
])
def test_declaration_rejects_authority_widening(field, value):
    dec = declaration(); dec[field] = value
    with pytest.raises(ValueError): m.validate_frozen_set_declaration(dec, memory_policy(), shadow_policy())


@pytest.mark.parametrize("field,value", [
    ("shadow_only", False),
    ("memory_write_authority", "WRITE"),
    ("auto_learning_allowed", True),
    ("live_decision_feedback_allowed", True),
    ("live_decision_use_allowed", True),
    ("model_selection_use_allowed", True),
    ("execution_authority", "TRADE"),
    ("can_trade", True),
    ("capital_permission", "ALLOW"),
    ("confers_authority", True),
])
def test_report_rejects_authority_widening(field, value):
    dec = declaration(); rep = report(source_declaration=dec); rep[field] = value
    with pytest.raises(ValueError): m.validate_shadow_report(rep, dec, memory_policy(), shadow_policy())


def test_report_has_no_probability_rate_confidence_ranking_or_economic_keys():
    rep = report()
    forbidden = {"probability", "rate", "percentage", "confidence", "model_ranking", "provider_ranking", "pnl", "price", "return"}
    assert forbidden.isdisjoint(rep)

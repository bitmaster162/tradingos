from __future__ import annotations

import copy
import pytest

from r80_retrospective_fixtures import m, cal, memory_policy, chain, record


def test_memory_policy_accepts_exact_v1():
    m.validate_memory_policy(memory_policy())


@pytest.mark.parametrize("field", [
    "external_sources_allowed",
    "persistence_in_core_allowed",
    "pnl_fields_allowed",
    "trading_performance_use_allowed",
    "probability_outputs_allowed",
    "rate_outputs_allowed",
    "auto_learning_allowed",
    "weight_update_allowed",
    "prompt_update_allowed",
    "model_selection_update_allowed",
    "policy_update_allowed",
    "live_decision_feedback_allowed",
])
def test_policy_rejects_guard_widening(field):
    p = memory_policy()
    p[field] = True
    with pytest.raises(ValueError):
        m.validate_memory_policy(p)


@pytest.mark.parametrize("field,value", [
    ("mode", "LIVE"),
    ("allowed_outcomes", ["SUPPORTED"]),
    ("require_all_response_claims", False),
    ("allow_extra_claim_ids", True),
    ("calibration_mode", "RATE"),
    ("memory_write_authority", "WRITE"),
])
def test_policy_rejects_contract_drift(field, value):
    p = memory_policy()
    p[field] = value
    with pytest.raises(ValueError):
        m.validate_memory_policy(p)


def test_policy_rejects_output_permission_widening():
    p = memory_policy()
    p["output_permissions"]["can_trade"] = True
    with pytest.raises(ValueError):
        m.validate_memory_policy(p)


def test_build_record_positive():
    r = record()
    assert r["schema"] == m.RECORD_SCHEMA
    assert r["claim_outcomes"][0]["outcome"] == "SUPPORTED"
    assert r["memory_write_authority"] == "NONE"
    assert r["auto_learning_allowed"] is False
    assert r["can_trade"] is False
    assert r["capital_permission"] == "DENY"


@pytest.mark.parametrize("outcome", m.OUTCOMES)
def test_all_categorical_outcomes_allowed(outcome):
    brief, rp, req, prompt, tp, env, receipt, response, annotation = chain()
    annotation["claim_outcomes"][0]["outcome"] = outcome
    r = m.build_retrospective_record(
        request=req,prompt=prompt,r78_policy=rp,source_brief=brief,
        transport_policy=tp,envelope=env,transport_receipt=receipt,
        response=response,annotation=annotation,memory_policy=memory_policy()
    )
    assert r["claim_outcomes"][0]["outcome"] == outcome


@pytest.mark.parametrize("code", m.RATIONALE_CODES)
def test_all_rationale_codes_allowed(code):
    brief, rp, req, prompt, tp, env, receipt, response, annotation = chain()
    annotation["claim_outcomes"][0]["rationale_code"] = code
    r = m.build_retrospective_record(
        request=req,prompt=prompt,r78_policy=rp,source_brief=brief,
        transport_policy=tp,envelope=env,transport_receipt=receipt,
        response=response,annotation=annotation,memory_policy=memory_policy()
    )
    assert r["claim_outcomes"][0]["rationale_code"] == code


def test_count_summary_positive():
    r = record()
    s = cal.build_count_summary([r], memory_policy())
    assert s["record_count"] == 1
    assert s["claim_count"] == 1
    assert len(s["summary_id"]) == 24
    assert s["total_outcomes"]["SUPPORTED"] == 1
    assert s["predictive_probability"] is None
    assert s["calibration_mode"] == "COUNT_ONLY"


def test_count_summary_empty_allowed():
    s = cal.build_count_summary([], memory_policy())
    assert s["record_count"] == 0
    assert s["claim_count"] == 0
    assert all(v == 0 for v in s["total_outcomes"].values())


def test_duplicate_records_rejected():
    r = record()
    with pytest.raises(ValueError):
        cal.build_count_summary([r, copy.deepcopy(r)], memory_policy())


def test_record_validation_rejects_memory_policy_mismatch():
    r = record()
    p = memory_policy()
    p["policy_id"] = "OTHER"
    with pytest.raises(ValueError):
        m.validate_retrospective_record(r, p)


@pytest.mark.parametrize("field,value", [
    ("memory_write_authority","WRITE"),
    ("auto_learning_allowed",True),
    ("live_decision_feedback_allowed",True),
    ("execution_authority","LIVE"),
    ("can_trade",True),
    ("capital_permission","ALLOW"),
    ("confers_authority",True),
])
def test_record_rejects_authority_widening(field, value):
    r = record()
    r[field] = value
    with pytest.raises(ValueError):
        m.validate_retrospective_record(r, memory_policy())


@pytest.mark.parametrize("field,value", [
    ("auto_learning_allowed",True),
    ("live_decision_feedback_allowed",True),
    ("execution_authority","LIVE"),
    ("can_trade",True),
    ("capital_permission","ALLOW"),
    ("confers_authority",True),
])
def test_summary_rejects_authority_widening(field, value):
    s = cal.build_count_summary([record()], memory_policy())
    s[field] = value
    with pytest.raises(ValueError):
        cal.validate_count_summary(s, memory_policy())


def test_summary_rejects_predictive_probability():
    s = cal.build_count_summary([record()], memory_policy())
    s["predictive_probability"] = 0.5
    with pytest.raises(ValueError):
        cal.validate_count_summary(s, memory_policy())


def test_summary_rejects_rate_mode():
    s = cal.build_count_summary([record()], memory_policy())
    s["calibration_mode"] = "RATE"
    with pytest.raises(ValueError):
        cal.validate_count_summary(s, memory_policy())

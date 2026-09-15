from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R86_PATH = ROOT / "tools" / "r6_registration_adapter.py"
R85_TEST_PATH = ROOT / "tests" / "test_r6_admission_evaluator.py"

spec = importlib.util.spec_from_file_location("r86", R86_PATH)
assert spec and spec.loader
r86 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = r86
spec.loader.exec_module(r86)

helper_spec = importlib.util.spec_from_file_location("r85_test_helpers", R85_TEST_PATH)
assert helper_spec and helper_spec.loader
helpers = importlib.util.module_from_spec(helper_spec)
sys.modules[helper_spec.name] = helpers
helper_spec.loader.exec_module(helpers)


def input_payload() -> dict:
    r85_input = helpers.payload()
    evaluation = r86.r85.evaluate(r85_input)
    return {
        "schema": r86.INPUT_SCHEMA,
        "r85_input": r85_input,
        "r85_evaluation": evaluation,
        "ledger_state": {
            "existing_event_ids": [],
            "resolved_calibration_n": 0,
            "resolved_holdout_n": 0,
            "holdout_freeze": "NOT_STARTED",
        },
        "registration_contract": {
            "timeframe": "15m",
            "regime_shadow": "BALANCE",
            "horizon_end_ms": helpers.NOW + 3_600_000,
        },
    }


def test_happy_path_builds_candidate_without_write_authority():
    out = r86.build_candidate(input_payload())
    assert out["result"] == "REGISTRATION_CANDIDATE_READY"
    assert out["registration_candidate"] is True
    assert out["sample_phase"] == "CALIBRATION"
    assert out["ledger_write_authority"] is False
    assert out["can_trade"] is False
    assert out["capital_permission"] == "DENY"
    row = out["row_values"]
    assert row["Trial_Eligibility"] == r86.TRIAL
    assert row["Resolver_State"] == "ENTRY_CONFIRMED"
    assert row["CURRENT_Entry_State"] == "ENTRY_CONFIRMED"
    assert row["CURRENT_Outcome_R"] == ""
    assert row["R5_Outcome_R"] == ""

def test_event_id_is_deterministic():
    p = input_payload()
    a = r86.build_candidate(copy.deepcopy(p))
    b = r86.build_candidate(copy.deepcopy(p))
    assert a["event_id"] == b["event_id"]
    assert a["semantic_sha256"] == b["semantic_sha256"]


def test_duplicate_event_id_is_no_write():
    p = input_payload()
    first = r86.build_candidate(copy.deepcopy(p))
    p["ledger_state"]["existing_event_ids"] = [first["event_id"]]
    out = r86.build_candidate(p)
    assert out["reason"] == "DUPLICATE_EVENT_ID_NO_WRITE"
    assert out["registration_candidate"] is False


def test_r85_fail_closed_cannot_register():
    p = input_payload()
    p["r85_input"]["decision_bundle"]["ctha"]["material_contradiction"] = True
    p["r85_evaluation"] = r86.r85.evaluate(p["r85_input"])
    out = r86.build_candidate(p)
    assert out["reason"] == "R85_NOT_TRIAL_ELIGIBLE"


def test_supplied_evaluation_must_match_recomputation():
    p = input_payload()
    p["r85_evaluation"]["a_grade_candidate"] = True
    out = r86.build_candidate(p)
    assert out["reason"] == "R85_EVALUATION_MISMATCH"

def test_missing_or_past_horizon_fails_closed():
    p = input_payload()
    p["registration_contract"].pop("horizon_end_ms")
    assert r86.build_candidate(p)["reason"] == "HORIZON_NOT_PREDECLARED_AFTER_DECISION"
    p = input_payload()
    p["registration_contract"]["horizon_end_ms"] = helpers.NOW
    assert r86.build_candidate(p)["reason"] == "HORIZON_NOT_PREDECLARED_AFTER_DECISION"


def test_calibration_stops_at_30_until_freeze():
    p = input_payload()
    p["ledger_state"]["resolved_calibration_n"] = 30
    out = r86.build_candidate(p)
    assert out["reason"] == "R6_HOLDOUT_FREEZE_REQUIRED"


def test_holdout_starts_only_after_freeze_pass():
    p = input_payload()
    p["ledger_state"]["resolved_calibration_n"] = 30
    p["ledger_state"]["holdout_freeze"] = "PASS"
    out = r86.build_candidate(p)
    assert out["registration_candidate"] is True
    assert out["sample_phase"] == "HOLDOUT"


def test_no_new_registration_after_holdout_30():
    p = input_payload()
    p["ledger_state"]["resolved_calibration_n"] = 30
    p["ledger_state"]["resolved_holdout_n"] = 30
    p["ledger_state"]["holdout_freeze"] = "PASS"
    assert r86.build_candidate(p)["reason"] == "R6_SAMPLE_COMPLETE_NO_NEW_REGISTRATION"

def test_registration_requires_explicit_timeframe_and_regime():
    p = input_payload()
    p["registration_contract"]["timeframe"] = ""
    assert r86.build_candidate(p)["reason"] == "TIMEFRAME_MISSING"
    p = input_payload()
    p["registration_contract"]["regime_shadow"] = ""
    assert r86.build_candidate(p)["reason"] == "REGIME_SHADOW_MISSING"


def test_r5_is_never_inferred_from_current():
    out = r86.build_candidate(input_payload())
    row = out["row_values"]
    assert row["R5_Decision"] == "PENDING_INDEPENDENT"
    assert "INDEPENDENT_R5_EVALUATION_REQUIRED" in row["R5_Trigger_Spec"]
    assert row["R5_Entry_State"] == "PENDING_INDEPENDENT"


def test_current_trigger_is_bound_to_r85_quote_and_reaction():
    p = input_payload()
    out = r86.build_candidate(p)
    trigger = r86.json.loads(out["row_values"]["CURRENT_Trigger_Spec"])
    quote = p["r85_input"]["r84_evidence"]["quote"]
    assert trigger["mode"] == "ENTRY_AT_R85_DECISION_QUOTE"
    assert trigger["reaction_id"] == "RX1"
    assert trigger["quote_provenance_sha256"] == quote["provenance_sha256"]
    assert trigger["quote_decision_time_ms"] == p["r85_evaluation"]["decision_time_ms"]


def test_candidate_has_no_terminal_or_outcome_claim():
    row = r86.build_candidate(input_payload())["row_values"]
    for key in ("Outcome_R", "R5_Outcome_R", "CURRENT_Outcome_R", "Terminal_Event", "Resolved_At_UTC"):
        assert row[key] == ""

def test_event_id_does_not_change_when_only_horizon_changes():
    p1 = input_payload()
    p2 = copy.deepcopy(p1)
    p2["registration_contract"]["horizon_end_ms"] += 7_200_000
    a = r86.build_candidate(p1)
    b = r86.build_candidate(p2)
    assert a["event_id"] == b["event_id"]
    assert a["registration_row_sha256"] != b["registration_row_sha256"]


def test_reaction_change_changes_event_id():
    p = input_payload()
    first = r86.build_candidate(copy.deepcopy(p))
    p["r85_input"]["decision_bundle"]["p"]["reaction_id"] = "RX2"
    p["r85_input"]["decision_bundle"]["r"]["reaction_id"] = "RX2"
    for event in p["r85_input"]["decision_bundle"]["r"]["events"]:
        event["reaction_id"] = "RX2"
    p["r85_input"]["decision_bundle"]["c"][0]["reaction_id"] = "RX2"
    p["r85_evaluation"] = r86.r85.evaluate(p["r85_input"])
    second = r86.build_candidate(p)
    assert first["event_id"] != second["event_id"]


def test_sheet_plan_uses_numeric_trade_cells_and_formula_templates():
    out = r86.build_candidate(input_payload())
    row = out["row_values"]
    for key in ("Nearest_Target", "Invalidation", "RR_Net", "CURRENT_Entry_Price", "CURRENT_Stop", "CURRENT_Target"):
        assert isinstance(row[key], float)
    formulas = out["sheet_write_plan"]["formula_templates"]
    assert "{row}" in formulas["Net_Expectancy_R"]
    assert "{row}" in formulas["Delta_Utility_R"]

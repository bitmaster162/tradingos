from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


r88 = load("r88", ROOT / "tools" / "r6_candidate_compiler.py")
base = load("r85_fixture", ROOT / "tests" / "test_r6_admission_evaluator.py")


def source_item(item: dict, tf: str = "15m") -> dict:
    out = copy.deepcopy(item)
    out["source_timeframe"] = tf
    out["provenance"] = f"test:{item.get('type', 'level')}"
    return out

def full_input() -> dict:
    p = base.payload()
    evidence = copy.deepcopy(p["r84_evidence"])
    bundle = p["decision_bundle"]
    ctha = copy.deepcopy(bundle["ctha"])
    ctha["bias"] = bundle["p"]["bias"]
    ctha["r84_evidence_sha256"] = r88.sha256_json(evidence)
    events = [source_item(x) for x in bundle["r"]["events"]]
    confluence = [source_item(x) for x in bundle["c"]]
    invalidation = {"price": bundle["invalidation"]["price"],
                    "known_at_ms": bundle["invalidation"]["known_at_ms"],
                    "source_timeframe": "15m", "provenance": "test:invalidation"}
    targets = [{"price": x["price"], "valid": x["valid"], "known_at_ms": x["known_at_ms"],
                "source_timeframe": "15m", "provenance": f"test:target:{x['price']}"}
               for x in bundle["targets"]]
    return {"schema": r88.INPUT_SCHEMA, "r84_evidence": evidence, "ctha_assessment": ctha,
            "structural_event_evidence": events, "confluence_evidence": confluence,
            "structural_levels": {"invalidation": invalidation, "targets": targets,
                                  "liquidity_objective": bundle["p"]["liquidity_objective"]},
            "cost_model": copy.deepcopy(bundle["cost_model"]),
            "risk_state": copy.deepcopy(bundle["risk"]),
            "expectancy": copy.deepcopy(bundle["expectancy"])}


def compile(p: dict | None = None) -> dict:
    return r88.compile_candidate(p or full_input())

def test_complete_explicit_evidence_compiles_r85_pass():
    out = compile()
    assert out["result"] == "CANDIDATE_COMPILED_R85_PASS"
    assert out["r85_evaluation"]["trial_eligibility"] == "R6_SHADOW_TRIAL_ELIGIBLE"
    assert out["ledger_write_authority"] is False
    assert out["can_trade"] is False and out["capital_permission"] == "DENY"
    assert out["r85_input"]["decision_bundle"]["chosen_target"] == "105"


def test_r84_alone_never_invents_candidate():
    p = full_input()
    for key in ["structural_event_evidence", "confluence_evidence", "structural_levels"]:
        p.pop(key)
    out = compile(p)
    assert out["result"] == "NO_CANDIDATE_FAIL_CLOSED"
    assert out["reason"] == "EVIDENCE_COMPILATION_FAILED"


def test_missing_displacement_fails_r85():
    p = full_input()
    p["structural_event_evidence"] = [x for x in p["structural_event_evidence"] if x["type"] != "DISPLACEMENT"]
    out = compile(p)
    assert out["reason"] == "R85_NOT_TRIAL_ELIGIBLE"
    assert out["details"]["evaluation"]["gates"]["r"]["status"] == "FAIL"


def test_missing_mss_fails_r85():
    p = full_input()
    p["structural_event_evidence"] = [x for x in p["structural_event_evidence"] if x["type"] != "MSS"]
    out = compile(p)
    assert out["reason"] == "R85_NOT_TRIAL_ELIGIBLE"

def test_event_timestamps_must_encode_chronology():
    p = full_input()
    p["structural_event_evidence"][1]["time_ms"] = 905_000
    out = compile(p)
    assert out["reason"] == "R85_NOT_TRIAL_ELIGIBLE"
    assert out["details"]["evaluation"]["gates"]["r"]["status"] == "FAIL"


def test_future_event_fails_before_r85():
    p = full_input()
    p["structural_event_evidence"][-1]["time_ms"] = base.NOW + 1
    out = compile(p)
    assert out["reason"] == "EVIDENCE_COMPILATION_FAILED"
    assert "time_invalid" in out["details"]["error"]


def test_reaction_ids_must_be_single_chain():
    p = full_input()
    p["structural_event_evidence"][-1]["reaction_id"] = "OTHER"
    out = compile(p)
    assert "event_reaction_id_invalid" in out["details"]["error"]


def test_ctha_must_be_bound_to_exact_r84_evidence():
    p = full_input()
    p["ctha_assessment"]["r84_evidence_sha256"] = "0" * 64
    out = compile(p)
    assert "ctha_evidence_binding_mismatch" in out["details"]["error"]


def test_ctha_contradiction_cannot_compile():
    p = full_input()
    p["ctha_assessment"]["material_contradiction"] = True
    out = compile(p)
    assert "ctha_not_explicit_pass" in out["details"]["error"]

def test_confluence_requires_same_reaction_and_provenance():
    p = full_input()
    p["confluence_evidence"][0]["reaction_id"] = "OTHER"
    out = compile(p)
    assert "thesis_link_invalid" in out["details"]["error"]
    p = full_input()
    p["confluence_evidence"][0].pop("provenance")
    out = compile(p)
    assert "provenance_missing" in out["details"]["error"]


def test_target_without_provenance_fails_closed():
    p = full_input()
    p["structural_levels"]["targets"][0].pop("provenance")
    out = compile(p)
    assert "target_0_provenance_missing" in out["details"]["error"]


def test_nearest_valid_target_is_selected_automatically():
    p = full_input()
    p["structural_levels"]["targets"].append({"price": "102", "valid": True,
        "known_at_ms": 958_000, "source_timeframe": "15m", "provenance": "test:nearer"})
    out = compile(p)
    assert out["r85_input"]["decision_bundle"]["chosen_target"] == "102"


def test_risk_state_is_required():
    p = full_input()
    p.pop("risk_state")
    out = compile(p)
    assert "risk_state_missing" in out["details"]["error"]

def test_tampered_quote_is_rejected_before_compile():
    p = full_input()
    p["r84_evidence"]["quote"]["ask"] = "101"
    out = compile(p)
    assert out["reason"] == "R84_EVIDENCE_INVALID"


def test_unsupported_event_label_is_not_promoted():
    p = full_input()
    p["structural_event_evidence"][1]["type"] = "BIG_GREEN_CANDLE"
    out = compile(p)
    assert "type_invalid" in out["details"]["error"]


def test_wrong_side_invalidation_fails_compilation():
    p = full_input()
    p["structural_levels"]["invalidation"]["price"] = "101"
    out = compile(p)
    assert "invalidation_invalid" in out["details"]["error"]


def test_unknown_operational_risk_remains_fail_closed():
    p = full_input()
    p["risk_state"]["operational_risk_state"] = "UNKNOWN"
    out = compile(p)
    assert out["reason"] == "R85_NOT_TRIAL_ELIGIBLE"
    assert out["details"]["evaluation"]["gates"]["risk"]["status"] == "UNKNOWN"


def test_default_expectancy_is_research_pending_only():
    p = full_input()
    p.pop("expectancy")
    out = compile(p)
    assert out["candidate_compiled"] is True
    assert out["r85_evaluation"]["a_grade_candidate"] is False

def test_r88_pass_feeds_r87_registration_candidate_without_write():
    compiled = compile()
    r87reg = load("r87reg_from_r88", ROOT / "tools" / "r6_registration_adapter.py")
    reg_input = {
        "schema": r87reg.INPUT_SCHEMA,
        "r85_input": compiled["r85_input"],
        "r85_evaluation": compiled["r85_evaluation"],
        "ledger_state": {"existing_event_ids": [], "resolved_calibration_n": 0,
                         "resolved_holdout_n": 0, "holdout_freeze": "NOT_STARTED"},
        "registration_contract": r87reg.r87.build_contract(base.NOW, "BALANCE"),
    }
    out = r87reg.build_candidate(reg_input)
    assert out["result"] == "REGISTRATION_CANDIDATE_READY"
    assert out["sample_phase"] == "CALIBRATION"
    assert out["ledger_write_authority"] is False
    assert out["row_values"]["CURRENT_Outcome_R"] == ""

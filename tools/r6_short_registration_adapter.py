#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = ROOT / "tools" / "r6_registration_adapter.py"
EVAL_PATH = ROOT / "tools" / "r6_directional_admission_evaluator.py"

def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

base = _load("r92_base_r86", BASE_PATH)
evaluator = _load("r92_reg_eval", EVAL_PATH)

INPUT_SCHEMA = "tradingos.r92_short_registration_input.v1"
OUTPUT_SCHEMA = "tradingos.r92_short_registration_candidate.v1"
ADAPTER_VERSION = "R92_SHORT_REGISTRATION_ADAPTER_V1_20260916"
TRIAL = "R6_SHADOW_TRIAL_ELIGIBLE"


def fail(reason: str, **details: Any) -> dict[str, Any]:
    return {"schema": OUTPUT_SCHEMA, "result": "NOT_REGISTERABLE_FAIL_CLOSED", "reason": reason,
            "details": details, "registration_candidate": False, "ledger_write_authority": False,
            "can_trade": False, "capital_permission": "DENY"}

def _semantic(r92_input: dict[str, Any], evaluation: dict[str, Any]) -> dict[str, Any]:
    bundle = r92_input["decision_bundle"]
    return {"symbol": evaluation["symbol"], "setup_family": evaluation["setup_family"],
            "direction": "SHORT", "reaction_id": bundle["r"]["reaction_id"],
            "r_ordered": evaluation["gates"]["r"]["evidence"]["ordered"],
            "invalidation": evaluation["gates"]["invalidation"]["evidence"]["price"],
            "nearest_target": evaluation["gates"]["target"]["evidence"]["price"],
            "chosen_target": str(bundle["chosen_target"])}


def _trigger(r92_input: dict[str, Any], evaluation: dict[str, Any]) -> dict[str, Any]:
    quote = r92_input["r84_evidence"]["quote"]
    econ = evaluation["gates"]["economics"]["evidence"]
    return {"schema": "tradingos.r92_short_current_trigger.v1",
            "mode": "ENTRY_SHORT_AT_R92_DECISION_QUOTE", "direction": "SHORT",
            "decision_time_ms": evaluation["decision_time_ms"],
            "reaction_id": r92_input["decision_bundle"]["r"]["reaction_id"],
            "r_ordered": evaluation["gates"]["r"]["evidence"]["ordered"],
            "quote_source": quote["source"], "quote_event_time_ms": quote["event_time_ms"],
            "quote_decision_time_ms": quote["decision_time_ms"],
            "quote_provenance_sha256": quote["provenance_sha256"], "raw_bid": quote["bid"],
            "modeled_entry": econ["entry"],
            "structural_invalidation": evaluation["gates"]["invalidation"]["evidence"]["price"],
            "nearest_target": evaluation["gates"]["target"]["evidence"]["price"],
            "net_rr": econ["net_rr"]}


def build_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != INPUT_SCHEMA:
        return fail("INPUT_SCHEMA_INVALID")
    r92_input = payload.get("r92_input")
    supplied = payload.get("r92_evaluation")
    ledger = payload.get("ledger_state")
    reg = payload.get("registration_contract")
    if not all(isinstance(x, dict) for x in (r92_input, supplied, ledger, reg)):
        return fail("INPUT_COMPONENT_MISSING")
    try:
        recomputed = evaluator.evaluate(r92_input)
    except Exception as exc:
        return fail("R92_REEVALUATION_ERROR", error=str(exc))
    if base.stable_json(recomputed) != base.stable_json(supplied):
        return fail("R92_EVALUATION_MISMATCH")
    if supplied.get("trial_eligibility") != TRIAL or supplied.get("calibration_registration_candidate") is not True:
        return fail("R92_NOT_TRIAL_ELIGIBLE")
    if supplied.get("direction") != "SHORT":
        return fail("R92_DIRECTION_INVALID")
    try:
        phase, phase_block = base._sample_phase(ledger)
    except ValueError as exc:
        return fail("LEDGER_STATE_INVALID", error=str(exc))
    if phase_block is not None:
        return fail(phase_block)
    decision_ms = supplied.get("decision_time_ms")
    horizon_ms = reg.get("horizon_end_ms")
    if type(decision_ms) is not int or decision_ms <= 0:
        return fail("DECISION_TIME_INVALID")
    if type(horizon_ms) is not int or horizon_ms <= decision_ms:
        return fail("HORIZON_NOT_PREDECLARED_AFTER_DECISION")
    timeframe = reg.get("timeframe")
    regime = reg.get("regime_shadow")
    if not isinstance(timeframe, str) or not timeframe or not isinstance(regime, str) or not regime:
        return fail("REGISTRATION_CONTRACT_INCOMPLETE")
    semantic = _semantic(r92_input, supplied)
    event_id, semantic_sha = base._event_id(semantic)
    existing = ledger.get("existing_event_ids")
    if not isinstance(existing, list) or any(not isinstance(x, str) for x in existing):
        return fail("EXISTING_EVENT_IDS_INVALID")
    if event_id in set(existing):
        return fail("DUPLICATE_EVENT_ID_NO_WRITE", event_id=event_id)
    trigger = _trigger(r92_input, supplied)
    econ = supplied["gates"]["economics"]["evidence"]
    inv = supplied["gates"]["invalidation"]["evidence"]["price"]
    target = supplied["gates"]["target"]["evidence"]["price"]
    quote = r92_input["r84_evidence"]["quote"]
    provenance = (f"R84+R92_SHORT_VERIFIED; quote_provenance_sha256={quote['provenance_sha256']}; "
                  f"r92_evaluation_sha256={base.sha256_json(supplied)}; semantic_sha256={semantic_sha}")
    row = {"Observed_at_BKK": base.observed_bkk(decision_ms), "Event_ID": event_id,
           "Symbol": supplied["symbol"].removesuffix("USDT"), "Setup_Family": supplied["setup_family"],
           "Timeframe": timeframe, "Regime_Shadow": regime,
           "R5_Status": "PENDING_INDEPENDENT_R5_EVALUATION", "CURRENT_Grade": "RESEARCH_CALIBRATION",
           "P": "PASS", "R": "PASS", "C": "PASS",
           "CTHA_Alignment": "PASS_NO_MATERIAL_CONTRADICTION",
           "Nearest_Target": base.sheet_number(target), "Invalidation": base.sheet_number(inv),
           "RR_Net": base.sheet_number(econ["net_rr"]),
           "Cost_Stress": "PREDECLARED_GATE_V5_COST_MODEL; STRESS_NOT_YET_RESOLVED",
           "OOS_or_Forward": "FORWARD_PROSPECTIVE",
           "Multiple_Testing_Control": "PREDECLARED_BEFORE_OUTCOME; LONG_SHORT_FAMILY_DECLARED_BEFORE_N1",
           "Provenance_State": provenance, "Operational_Risk": "PASS_AT_REGISTRATION",
           "Outcome_R": "", "Final_Label": "TRIAL_ELIGIBLE_REGISTERED",
           "Notes": f"adapter={ADAPTER_VERSION}; direction=SHORT; semantic_sha256={semantic_sha}; paper-only",
           "R5_Decision": "PENDING_INDEPENDENT", "R5_Outcome_R": "",
           "CURRENT_Decision": "ENTER_SHORT_AT_R92_DECISION_QUOTE", "CURRENT_Outcome_R": "",
           "Trial_Eligibility": TRIAL, "Sample_Phase": phase,
           "Resolver_Version": "R6_SHADOW_RESOLVER_V1_20260916", "Resolver_State": "ENTRY_CONFIRMED",
           "Registered_At_UTC": base.iso_utc(decision_ms), "Horizon_End_UTC": base.iso_utc(horizon_ms),
           "R5_Trigger_Spec": base.stable_json(base._r5_trigger_spec()),
           "CURRENT_Trigger_Spec": base.stable_json(trigger),
           "R5_Entry_State": "PENDING_INDEPENDENT", "CURRENT_Entry_State": "ENTRY_CONFIRMED",
           "CURRENT_Entry_At_UTC": base.iso_utc(decision_ms),
           "CURRENT_Entry_Price": base.sheet_number(econ["entry"]),
           "CURRENT_Stop": base.sheet_number(inv), "CURRENT_Target": base.sheet_number(target),
           "Terminal_Event": "", "Resolved_At_UTC": "", "Resolution_Evidence": "",
           "Sequence_Proof": "", "Last_Resolver_Run_UTC": ""}
    return {"schema": OUTPUT_SCHEMA, "result": "SHORT_REGISTRATION_CANDIDATE_READY",
            "adapter_version": ADAPTER_VERSION, "event_id": event_id,
            "semantic_sha256": semantic_sha, "sample_phase": phase,
            "registration_candidate": True, "registration_row_sha256": base.sha256_json(row),
            "row_values": row,
            "sheet_write_plan": {"spreadsheet_id": "11jGKFpdCmfC9cCMfhnEyeM9LHnVOgzfRIiQfClog46I",
                                 "sheet_name": "ShadowScorecard",
                                 "mutation": "APPEND_FIRST_EMPTY_ROW_AFTER_FRESH_DEDUPE",
                                 "event_id_column": "B",
                                 "post_write_readback": "EXACT_ROW_AND_R6_PROMOTION_GATE"},
            "ledger_write_authority": False, "can_trade": False, "capital_permission": "DENY"}


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Build one R92 SHORT registration candidate")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = build_candidate(json.loads(args.input.read_text(encoding="utf-8")))
        text = base.stable_json(result)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if result.get("registration_candidate") else 3
    except Exception as exc:
        print(base.stable_json({"schema": OUTPUT_SCHEMA, "result": "ERROR", "error": str(exc),
                               "registration_candidate": False, "ledger_write_authority": False,
                               "can_trade": False, "capital_permission": "DENY"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

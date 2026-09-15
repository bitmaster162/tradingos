#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
R85_PATH = ROOT / "tools" / "r6_admission_evaluator.py"
SPEC = importlib.util.spec_from_file_location("r85_eval", R85_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load R85 evaluator")
r85 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = r85
SPEC.loader.exec_module(r85)

INPUT_SCHEMA = "tradingos.r86_registration_input.v1"
OUTPUT_SCHEMA = "tradingos.r86_registration_candidate.v1"
ADAPTER_VERSION = "R86_REGISTRATION_ADAPTER_V1_20260916"
RESOLVER_VERSION = "R6_SHADOW_RESOLVER_V1_20260916"
TRIAL = "R6_SHADOW_TRIAL_ELIGIBLE"
BKK = timezone(timedelta(hours=7))

def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()

def sheet_number(value: Any) -> float:
    return float(Decimal(str(value)))


def iso_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def observed_bkk(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, BKK).isoformat()


def fail(reason: str, **details: Any) -> dict[str, Any]:
    return {
        "schema": OUTPUT_SCHEMA,
        "result": "NOT_REGISTERABLE_FAIL_CLOSED",
        "reason": reason,
        "details": details,
        "registration_candidate": False,
        "ledger_write_authority": False,
        "can_trade": False,
        "capital_permission": "DENY",
    }


def _require_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name}_invalid")
    return value

def _sample_phase(ledger: dict[str, Any]) -> tuple[str | None, str | None]:
    cal_n = _require_int(ledger.get("resolved_calibration_n"), "resolved_calibration_n")
    hold_n = _require_int(ledger.get("resolved_holdout_n", 0), "resolved_holdout_n")
    freeze = ledger.get("holdout_freeze")
    if cal_n < 30:
        return "CALIBRATION", None
    if freeze != "PASS":
        return None, "R6_HOLDOUT_FREEZE_REQUIRED"
    if hold_n < 30:
        return "HOLDOUT", None
    return None, "R6_SAMPLE_COMPLETE_NO_NEW_REGISTRATION"


def _reaction_id(r85_input: dict[str, Any]) -> str:
    bundle = r85_input.get("decision_bundle")
    if not isinstance(bundle, dict):
        raise ValueError("decision_bundle_missing")
    r = bundle.get("r")
    if not isinstance(r, dict) or not isinstance(r.get("reaction_id"), str) or not r["reaction_id"]:
        raise ValueError("reaction_id_missing")
    return r["reaction_id"]


def _semantic_identity(r85_input: dict[str, Any], evaluation: dict[str, Any]) -> dict[str, Any]:
    bundle = r85_input["decision_bundle"]
    r_gate = evaluation["gates"]["r"]
    ordered = r_gate.get("evidence", {}).get("ordered")
    return {
        "symbol": evaluation["symbol"],
        "setup_family": evaluation["setup_family"],
        "direction": evaluation["direction"],
        "reaction_id": _reaction_id(r85_input),
        "r_ordered": ordered,
        "invalidation": evaluation["gates"]["invalidation"]["evidence"]["price"],
        "nearest_target": evaluation["gates"]["target"]["evidence"]["price"],
        "chosen_target": str(bundle.get("chosen_target")),
    }


def _event_id(semantic: dict[str, Any]) -> tuple[str, str]:
    digest = sha256_json(semantic)
    symbol = semantic["symbol"].removesuffix("USDT")
    return f"R6_{symbol}_{digest[:16].upper()}", digest

def _current_trigger_spec(r85_input: dict[str, Any], evaluation: dict[str, Any]) -> dict[str, Any]:
    quote = r85_input["r84_evidence"]["quote"]
    econ = evaluation["gates"]["economics"]["evidence"]
    return {
        "schema": "tradingos.r86_current_trigger.v1",
        "mode": "ENTRY_AT_R85_DECISION_QUOTE",
        "decision_time_ms": evaluation["decision_time_ms"],
        "reaction_id": _reaction_id(r85_input),
        "r_ordered": evaluation["gates"]["r"]["evidence"]["ordered"],
        "quote_source": quote["source"],
        "quote_event_time_ms": quote["event_time_ms"],
        "quote_decision_time_ms": quote["decision_time_ms"],
        "quote_provenance_sha256": quote["provenance_sha256"],
        "raw_ask": quote["ask"],
        "modeled_entry": econ["entry"],
        "structural_invalidation": evaluation["gates"]["invalidation"]["evidence"]["price"],
        "nearest_target": evaluation["gates"]["target"]["evidence"]["price"],
        "net_rr": econ["net_rr"],
    }


def _r5_trigger_spec() -> dict[str, Any]:
    return {
        "mode": "INDEPENDENT_R5_EVALUATION_REQUIRED",
        "contract": "MULTI_ASSET_PRC_PAPER_R5_20260907",
        "inherit_current": False,
    }

def build_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != INPUT_SCHEMA:
        return fail("INPUT_SCHEMA_INVALID")
    r85_input = payload.get("r85_input")
    supplied = payload.get("r85_evaluation")
    ledger = payload.get("ledger_state")
    reg = payload.get("registration_contract")
    if not all(isinstance(x, dict) for x in (r85_input, supplied, ledger, reg)):
        return fail("INPUT_COMPONENT_MISSING")
    try:
        recomputed = r85.evaluate(r85_input)
    except Exception as exc:
        return fail("R85_REEVALUATION_ERROR", error=str(exc))
    if stable_json(recomputed) != stable_json(supplied):
        return fail("R85_EVALUATION_MISMATCH")
    if supplied.get("trial_eligibility") != TRIAL or supplied.get("calibration_registration_candidate") is not True:
        return fail("R85_NOT_TRIAL_ELIGIBLE")
    if supplied.get("ledger_write_authority") is not False:
        return fail("R85_WRITE_BOUNDARY_INVALID")
    if supplied.get("can_trade") is not False or supplied.get("capital_permission") != "DENY":
        return fail("R85_AUTHORITY_BOUNDARY_INVALID")
    try:
        phase, phase_block = _sample_phase(ledger)
    except ValueError as exc:
        return fail("LEDGER_STATE_INVALID", error=str(exc))
    if phase_block is not None:
        return fail(phase_block)
    decision_ms = supplied.get("decision_time_ms")
    if type(decision_ms) is not int or decision_ms <= 0:
        return fail("DECISION_TIME_INVALID")
    horizon_ms = reg.get("horizon_end_ms")
    if type(horizon_ms) is not int or horizon_ms <= decision_ms:
        return fail("HORIZON_NOT_PREDECLARED_AFTER_DECISION")
    timeframe = reg.get("timeframe")
    regime = reg.get("regime_shadow")
    if not isinstance(timeframe, str) or not timeframe:
        return fail("TIMEFRAME_MISSING")
    if not isinstance(regime, str) or not regime:
        return fail("REGIME_SHADOW_MISSING")
    semantic = _semantic_identity(r85_input, supplied)
    event_id, semantic_sha = _event_id(semantic)
    existing_ids = ledger.get("existing_event_ids")
    if not isinstance(existing_ids, list) or any(not isinstance(x, str) for x in existing_ids):
        return fail("EXISTING_EVENT_IDS_INVALID")
    if event_id in set(existing_ids):
        return fail("DUPLICATE_EVENT_ID_NO_WRITE", event_id=event_id)
    trigger = _current_trigger_spec(r85_input, supplied)
    r5_trigger = _r5_trigger_spec()
    econ = supplied["gates"]["economics"]["evidence"]
    inv = supplied["gates"]["invalidation"]["evidence"]["price"]
    target = supplied["gates"]["target"]["evidence"]["price"]
    quote = r85_input["r84_evidence"]["quote"]
    current_grade = "A_CANDIDATE" if supplied.get("a_grade_candidate") else "RESEARCH_CALIBRATION"
    provenance = (
        f"R84+R85_VERIFIED; quote_provenance_sha256={quote['provenance_sha256']}; "
        f"r85_evaluation_sha256={sha256_json(supplied)}; r86_semantic_sha256={semantic_sha}"
    )
    notes = (
        f"adapter={ADAPTER_VERSION}; semantic_sha256={semantic_sha}; "
        "entry_mode=ENTRY_AT_R85_DECISION_QUOTE; R5 evaluated independently; "
        "candidate only until sole resolver sheet write + exact readback"
    )
    row = {
        "Observed_at_BKK": observed_bkk(decision_ms),
        "Event_ID": event_id,
        "Symbol": supplied["symbol"].removesuffix("USDT"),
        "Setup_Family": supplied["setup_family"],
        "Timeframe": timeframe,
        "Regime_Shadow": regime,
        "R5_Status": "PENDING_INDEPENDENT_R5_EVALUATION",
        "CURRENT_Grade": current_grade,
        "P": "PASS",
        "R": "PASS",
        "C": "PASS",
        "CTHA_Alignment": "PASS_NO_MATERIAL_CONTRADICTION",
        "Nearest_Target": sheet_number(target),
        "Invalidation": sheet_number(inv),
        "RR_Net": sheet_number(econ["net_rr"]),
        "Cost_Stress": "PREDECLARED_COST_MODEL; STRESS_NOT_YET_RESOLVED",
        "OOS_or_Forward": "FORWARD_PROSPECTIVE",
        "Multiple_Testing_Control": "PREDECLARED_BEFORE_OUTCOME",
        "Provenance_State": provenance,
        "Operational_Risk": "PASS_AT_REGISTRATION",
        "Outcome_R": "",
        "Final_Label": "TRIAL_ELIGIBLE_REGISTERED",
        "Notes": notes,
        "R5_Decision": "PENDING_INDEPENDENT",
        "R5_Outcome_R": "",
        "CURRENT_Decision": "ENTER_LONG_AT_R85_DECISION_QUOTE",
        "CURRENT_Outcome_R": "",
        "Trial_Eligibility": TRIAL,
        "Sample_Phase": phase,
        "Resolver_Version": RESOLVER_VERSION,
        "Resolver_State": "ENTRY_CONFIRMED",
        "Registered_At_UTC": iso_utc(decision_ms),
        "Horizon_End_UTC": iso_utc(horizon_ms),
        "R5_Trigger_Spec": stable_json(r5_trigger),
        "CURRENT_Trigger_Spec": stable_json(trigger),
        "R5_Entry_State": "PENDING_INDEPENDENT",
        "CURRENT_Entry_State": "ENTRY_CONFIRMED",
        "CURRENT_Entry_At_UTC": iso_utc(decision_ms),
        "CURRENT_Entry_Price": sheet_number(econ["entry"]),
        "CURRENT_Stop": sheet_number(inv),
        "CURRENT_Target": sheet_number(target),
        "Terminal_Event": "",
        "Resolved_At_UTC": "",
        "Resolution_Evidence": "",
        "Sequence_Proof": "",
        "Last_Resolver_Run_UTC": "",
    }
    return {
        "schema": OUTPUT_SCHEMA,
        "result": "REGISTRATION_CANDIDATE_READY",
        "adapter_version": ADAPTER_VERSION,
        "event_id": event_id,
        "semantic_sha256": semantic_sha,
        "sample_phase": phase,
        "registration_candidate": True,
        "registration_row_sha256": sha256_json(row),
        "row_values": row,
        "sheet_write_plan": {
            "spreadsheet_id": "11jGKFpdCmfC9cCMfhnEyeM9LHnVOgzfRIiQfClog46I",
            "sheet_name": "ShadowScorecard",
            "mutation": "APPEND_FIRST_EMPTY_ROW_AFTER_FRESH_DEDUPE",
            "event_id_column": "B",
            "post_write_readback": "EXACT_ROW_AND_R6_PROMOTION_GATE",
            "formula_templates": {
                "Net_Expectancy_R": '=IF(AND(ISNUMBER(P{row});ISNUMBER(R{row});ISNUMBER(S{row}));P{row}*R{row}-(1-P{row})*S{row};"")',
                "Delta_Utility_R": '=IF(AND(ISNUMBER(AJ{row});ISNUMBER(AL{row}));AL{row}-AJ{row};"")',
            },
        },
        "preconditions": {
            "event_id_absent_on_fresh_read": True,
            "same_semantic_setup_absent": True,
            "sole_resolver_writer_required": True,
            "exact_post_write_readback_required": True,
        },
        "ledger_write_authority": False,
        "can_trade": False,
        "capital_permission": "DENY",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build one frozen R6 registration candidate")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        result = build_candidate(payload)
        text = stable_json(result)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if result.get("registration_candidate") else 3
    except Exception as exc:
        print(stable_json({
            "schema": OUTPUT_SCHEMA, "result": "ERROR", "error": str(exc),
            "registration_candidate": False, "ledger_write_authority": False,
            "can_trade": False, "capital_permission": "DENY"
        }))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

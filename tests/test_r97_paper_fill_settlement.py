from __future__ import annotations

import copy, importlib.util, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("r97",ROOT/"tools"/"r6_paper_fill_settlement.py")
assert SPEC and SPEC.loader
m=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(m)

def runtime(risk="0.5"):
    return {"risk_state":{"proposed_risk_pct":risk},"cost_model":{"entry_fee_rate":"0.0005","exit_fee_rate":"0.0005","entry_slippage_bps":"2","exit_slippage_bps":"2"}}

def cand(direction="LONG", qty="1", impact="1"):
    decision=f"ENTER_{direction}_AT_R97_TEST"
    trigger={"settlement_spec":{"entry_fee_rate":"0.0005","exit_fee_rate":"0.0005","entry_slippage_bps":"2","exit_slippage_bps":"2","initial_planned_risk_per_unit":"2"}}
    row={"Symbol":"BTC","CURRENT_Decision":decision,"CURRENT_Entry_Price":100.0,"CURRENT_Stop":98.0 if direction=="LONG" else 102.0,"CURRENT_Target":103.0 if direction=="LONG" else 97.0,"CURRENT_Trigger_Spec":json.dumps(trigger),"Operational_Risk":"PASS","Provenance_State":"P","Notes":"N","Registered_At_UTC":"old","CURRENT_Entry_At_UTC":"old","RR_Net":1.0}
    cont={"status":"PASS","frozen_quantity":qty,"fresh_entry_depth_vwap":"100.01" if direction=="LONG" else "99.99","fresh_entry_impact_bps":impact,"fresh_best_ask":"100.00","fresh_best_bid":"100.00","fresh_decision_time_ms":1800000000000,"fresh_quote_provenance_sha256":"q","fresh_book_provenance_sha256":"b"}
    return {"event_id":"E1","registration_candidate":True,"ledger_write_authority":False,"decision_continuity_gate_pass":True,"decision_continuity":cont,"row_values":row,"can_trade":False,"capital_permission":"DENY"}

def test_long_pass_settles_to_frozen_total_slippage_budget():
    out=m.settle_candidate(cand("LONG"),runtime()); assert out["paper_fill_settlement_gate_pass"] is True
    row=out["row_values"]; assert abs(row["CURRENT_Entry_Price"]-100.02)<1e-9
    assert row["CURRENT_Decision"]=="ENTER_LONG_AT_R97_SETTLED_FILL" and row["Registered_At_UTC"]==row["CURRENT_Entry_At_UTC"]
    spec=json.loads(row["CURRENT_Trigger_Spec"])["settlement_spec"]
    assert spec["entry_depth_vwap"]=="100.01" and spec["entry_residual_slippage_bps"]=="1"

def test_short_pass_mirrors_adverse_fill():
    out=m.settle_candidate(cand("SHORT"),runtime()); assert out["paper_fill_settlement_gate_pass"] is True
    assert abs(out["row_values"]["CURRENT_Entry_Price"]-99.98)<1e-9

def test_event_id_and_quantity_are_frozen():
    c=cand(); out=m.settle_candidate(c,runtime()); assert out["event_id"]=="E1"
    assert out["paper_fill_settlement"]["quantity"]=="1"

def test_depth_impact_above_budget_fails():
    out=m.settle_candidate(cand(impact="2.1"),runtime()); assert out["status"]=="FAIL_CLOSED"

def test_missing_r96_pass_fails():
    c=cand(); c["decision_continuity_gate_pass"]=False
    assert m.settle_candidate(c,runtime())["status"]=="FAIL_CLOSED"

def test_settled_risk_budget_exceeded_fails_closed():
    out=m.settle_candidate(cand("LONG",qty="100"),runtime("0.5"))
    assert out["status"]=="FAIL_CLOSED" and out["reason"]=="SETTLED_RISK_BUDGET_EXCEEDED"

def test_geometry_breach_fails():
    c=cand("LONG"); c["row_values"]["CURRENT_Target"]=100.01
    assert m.settle_candidate(c,runtime())["status"]=="FAIL_CLOSED"

def test_slippage_contract_mismatch_fails():
    r=runtime(); r["cost_model"]["entry_slippage_bps"]="3"
    assert m.settle_candidate(cand(),r)["status"]=="FAIL_CLOSED"

def test_trigger_contains_r97_provenance_and_new_risk_basis():
    out=m.settle_candidate(cand(),runtime()); trg=json.loads(out["row_values"]["CURRENT_Trigger_Spec"])
    ev=trg["r97_paper_fill_settlement"]
    assert ev["provenance_sha256"] and float(ev["unit_risk"])>0 and float(ev["net_rr"])>0
    assert trg["settlement_spec"]["initial_planned_risk_per_unit"]==ev["unit_risk"]

def test_no_write_authority_or_live_permission():
    out=m.settle_candidate(cand(),runtime())
    assert out["ledger_write_authority"] is False and out["can_trade"] is False and out["capital_permission"]=="DENY"

from __future__ import annotations
import asyncio, importlib.util, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("r97s",ROOT/"tools"/"r6_paper_fill_settlement_sweep.py")
assert SPEC and SPEC.loader
m=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(m)

def state():
    return {"ledger_state":{"existing_event_ids":[]},"risk_state":{"proposed_risk_pct":"0.5","aggregate_open_risk_pct":"0","entries_today":0},"cost_model":{"entry_fee_rate":"0.0005","exit_fee_rate":"0.0005","entry_slippage_bps":"2","exit_slippage_bps":"2"}}

def candidate():
    row={"Symbol":"BTC","CURRENT_Decision":"ENTER_LONG_AT_R85_DECISION_QUOTE","CURRENT_Stop":98.0,"CURRENT_Target":103.0,"CURRENT_Trigger_Spec":json.dumps({"settlement_spec":{"entry_fee_rate":"0.0005","exit_fee_rate":"0.0005","entry_slippage_bps":"2","exit_slippage_bps":"2","initial_planned_risk_per_unit":"2"}}),"Operational_Risk":"PASS","Provenance_State":"P","Notes":"N"}
    return {"event_id":"E1","registration_candidate":True,"ledger_write_authority":False,"row_values":row,"execution_capacity":{"status":"PASS","quantity":"1","quote_provenance_sha256":"oldq"},"liquidity_persistence_gate_pass":True,"capacity_gate_pass":True,"can_trade":False,"capital_permission":"DENY"}

def setup(monkeypatch, has_candidate=True, settlement_pass=True):
    async def capture(symbol, refs):
        q={"symbol":symbol,"provenance_sha256":"oldq","decision_time_ms":1000,"event_time_ms":950}
        return {"symbol":symbol},{"provenance_sha256":"book"},{"provenance_sha256":"rules"},{"x":1},{"states":[{"quote":q}]}
    monkeypatch.setattr(m.r95,"_capture_symbol",capture)
    monkeypatch.setattr(m.r95.r94.r93.r92.base,"_state",lambda s:s)
    monkeypatch.setattr(m.r95,"process_snapshot",lambda *a,**k:{"results":[{"symbol":"BTCUSDT"}],"registration_candidates":[candidate()] if has_candidate else []})
    async def fresh(symbol,rules): return {"symbol":symbol},{"provenance_sha256":"fb"},rules,{"x":1}
    monkeypatch.setattr(m,"_fresh_continuity_state",fresh)
    cont={"status":"PASS","contract_id":"R96_DECISION_CONTINUITY_V1_20260916","continuity_gap_ms":500,"adverse_drift_bps":"1","fresh_decision_time_ms":1500,"fresh_event_time_ms":1450,"frozen_quantity":"1","fresh_notional_usdt":"100","fresh_entry_depth_vwap":"100.01","fresh_exit_depth_vwap_proxy":"99.99","fresh_entry_impact_bps":"1","fresh_exit_impact_bps":"1","fresh_best_ask":"100","fresh_best_bid":"99.99","fresh_quote_provenance_sha256":"fq","fresh_book_provenance_sha256":"fb","reference_provenance_sha256":"ref","provenance_sha256":"r96p"}
    monkeypatch.setattr(m.cont,"evaluate_continuity",lambda *a,**k:cont)
    def settlement(c,r):
        if not settlement_pass: return {"status":"FAIL_CLOSED","reason":"SETTLED_RISK_BUDGET_EXCEEDED"}
        o=dict(c); o["paper_fill_settlement_gate_pass"]=True; o["paper_fill_settlement"]={"status":"PASS"}; return o
    monkeypatch.setattr(m.settle,"settle_candidate",settlement)

def test_reserves_only_after_r97_pass(monkeypatch):
    setup(monkeypatch); calls=[]
    monkeypatch.setattr(m.r95.r94,"_reserve_external",lambda *a:calls.append(a))
    out=asyncio.run(m.run_sweep(["BTCUSDT"],state()))
    assert out["registration_candidate_count"]==1 and len(calls)==1
    assert out["paper_fill_settlement_pass_count"]==1

def test_r97_fail_does_not_reserve(monkeypatch):
    setup(monkeypatch,settlement_pass=False); calls=[]
    monkeypatch.setattr(m.r95.r94,"_reserve_external",lambda *a:calls.append(a))
    out=asyncio.run(m.run_sweep(["BTCUSDT"],state()))
    assert out["registration_candidate_count"]==0 and calls==[]
    assert out["results"][0]["status"]=="PAPER_FILL_SETTLEMENT_FAIL_CLOSED"

def test_no_upstream_candidate_skips_r96_and_r97(monkeypatch):
    setup(monkeypatch,has_candidate=False); calls=[]
    async def bad(*a,**k): calls.append(1); raise AssertionError
    monkeypatch.setattr(m,"_fresh_continuity_state",bad)
    out=asyncio.run(m.run_sweep(["BTCUSDT"],state()))
    assert out["registration_candidate_count"]==0 and calls==[]

def test_live_permissions_remain_denied(monkeypatch):
    setup(monkeypatch); monkeypatch.setattr(m.r95.r94,"_reserve_external",lambda *a:None)
    out=asyncio.run(m.run_sweep(["BTCUSDT"],state()))
    assert out["ledger_write_authority"] is False and out["can_trade"] is False and out["capital_permission"]=="DENY"

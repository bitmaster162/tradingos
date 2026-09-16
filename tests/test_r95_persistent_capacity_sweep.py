from __future__ import annotations
import importlib.util, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("r95_sweep_test",ROOT/"tools"/"r6_persistent_capacity_sweep.py")
assert SPEC and SPEC.loader
m=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(m)

def candidate(event_id="E1"):
    row={"Symbol":"BTC","CURRENT_Decision":"ENTER_LONG_AT_R85_DECISION_QUOTE",
         "CURRENT_Trigger_Spec":json.dumps({"quote_provenance_sha256":"q"}),
         "CURRENT_Stop":99.0,"CURRENT_Target":103.0,"RR_Net":2.0,
         "Provenance_State":"p","Operational_Risk":"PASS","Notes":"n"}
    return {"event_id":event_id,"registration_candidate":True,"ledger_write_authority":False,
            "row_values":row,"registration_row_sha256":"old","can_trade":False,"capital_permission":"DENY"}

def cap_pass():
    return {"status":"PASS","quantity":"1","quote_provenance_sha256":"q",
            "capacity_book_provenance_sha256":"b","contract_id":m.r94.cap.CONTRACT_ID,
            "research_equity_usdt":"10000","gross_notional_usdt":"100",
            "actual_planned_risk_usdt":"5","actual_planned_risk_pct":"0.05",
            "entry_impact_bps":"0.1","exit_impact_bps":"0.1","rounded_stop":"99.00",
            "rounded_target":"103.00","net_rr_after_venue_rounding":"2.1",
            "symbol_rules_provenance_sha256":"r","provenance_sha256":"cp"}

def persistence_pass():
    return {"status":"PASS","contract_id":m.persist.CONTRACT_ID,"state_count":3,"event_span_ms":200,
            "max_entry_impact_bps":"0.1","max_exit_impact_bps":"0.1",
            "sequence_provenance_sha256":"seq","provenance_sha256":"pp"}

def setup(monkeypatch,persist_result):
    monkeypatch.setattr(m.r94.r93.r92.base,"_state",lambda state:state)
    monkeypatch.setattr(m.r94.r93,"process_snapshot",lambda snap,state,refs:{
        "results":[{"symbol":"BTCUSDT","market_integrity":{"status":"PASS"},"registration_candidate":True}],
        "registration_candidates":[candidate()]})
    monkeypatch.setattr(m.r94.cap,"evaluate_capacity",lambda *a,**k:cap_pass())
    monkeypatch.setattr(m.persist,"evaluate_persistence",lambda *a,**k:persist_result)
    snapshot={"schema":m.r94.r93.r92.base.r84.SCHEMA,"requested_symbols":["BTCUSDT"],
              "evidence":[{"symbol":"BTCUSDT","quote":{"symbol":"BTCUSDT"}}]}
    state={"ledger_state":{"existing_event_ids":[]},
           "risk_state":{"proposed_risk_pct":"0.5","aggregate_open_risk_pct":"0","entries_today":0}}
    books={"BTCUSDT":{"provenance_sha256":"b"}}; rules={"BTCUSDT":{"provenance_sha256":"r"}}
    refs={"BTCUSDT":{"x":1}}; seqs={"BTCUSDT":{"provenance_sha256":"seq"}}
    return snapshot,state,books,rules,refs,seqs

def test_failed_persistence_does_not_reserve(monkeypatch):
    args=setup(monkeypatch,{"status":"FAIL_CLOSED","reason":"PERSISTENCE"}); calls=[]
    monkeypatch.setattr(m.r94,"_reserve_external",lambda *a:calls.append(a))
    out=m.process_snapshot(*args)
    assert out["registration_candidate_count"]==0
    assert out["results"][0]["status"]=="LIQUIDITY_PERSISTENCE_FAIL_CLOSED"
    assert calls==[]


def test_persistence_pass_reserves_and_attaches(monkeypatch):
    args=setup(monkeypatch,persistence_pass()); calls=[]
    monkeypatch.setattr(m.r94,"_reserve_external",lambda *a:calls.append(a))
    out=m.process_snapshot(*args)
    assert out["registration_candidate_count"]==1 and calls
    c=out["registration_candidates"][0]
    assert c["liquidity_persistence_gate_pass"] is True
    assert "r95_liquidity_persistence" in json.loads(c["row_values"]["CURRENT_Trigger_Spec"])


def test_capacity_failure_prevents_persistence(monkeypatch):
    args=setup(monkeypatch,persistence_pass()); calls=[]
    monkeypatch.setattr(m.r94.cap,"evaluate_capacity",lambda *a,**k:{"status":"FAIL_CLOSED","reason":"CAP"})
    monkeypatch.setattr(m.persist,"evaluate_persistence",lambda *a,**k:calls.append(a))
    out=m.process_snapshot(*args)
    assert out["registration_candidate_count"]==0
    assert out["results"][0]["status"]=="EXECUTION_CAPACITY_FAIL_CLOSED"
    assert calls==[]


def test_run_sweep_isolates_symbol_capture_failure(monkeypatch):
    import asyncio
    async def fake_capture(symbols):
        snap={"schema":m.r94.r93.r92.base.r84.SCHEMA,"requested_symbols":symbols,
              "evidence":[{"symbol":"BTCUSDT","quote":{"symbol":"BTCUSDT"}}]}
        return snap,{"BTCUSDT":{}},{"BTCUSDT":{}},{"BTCUSDT":{}},{"BTCUSDT":{}},{"ETHUSDT":"SpotQuoteReject:persistence_span_too_long"}
    monkeypatch.setattr(m,"capture_snapshot",fake_capture)
    monkeypatch.setattr(m,"process_snapshot",lambda *a,**k:{
        "result":"SWEEP_COMPLETE","requested_symbols":["BTCUSDT","ETHUSDT"],"symbol_count":1,
        "results":[{"symbol":"BTCUSDT","status":"NO_CANDIDATE_FAIL_CLOSED"}],
        "registration_candidates":[],"registration_candidate_count":0})
    out=asyncio.run(m.run_sweep(["BTCUSDT","ETHUSDT"],{}))
    assert out["result"]=="SWEEP_COMPLETE" and out["symbol_count"]==2
    assert out["persistence_capture_fail_count"]==1
    assert out["results"][1]["symbol"]=="ETHUSDT"
    assert out["results"][1]["status"]=="R95_PERSISTENCE_CAPTURE_FAIL_CLOSED"

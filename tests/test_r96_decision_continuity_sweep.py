from __future__ import annotations

import asyncio, importlib.util, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("r96_sweep_test",ROOT/"tools"/"r6_decision_continuity_sweep.py")
assert SPEC and SPEC.loader
m=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(m)


def state():
    return {"ledger_state":{"existing_event_ids":[]},
            "risk_state":{"proposed_risk_pct":"0.5","aggregate_open_risk_pct":"0","entries_today":0}}


def candidate(event_id="E1"):
    row={"Symbol":"BTC","CURRENT_Decision":"ENTER_LONG_AT_R85_DECISION_QUOTE","CURRENT_Stop":98.0,
         "CURRENT_Target":103.0,"CURRENT_Trigger_Spec":json.dumps({}),"Operational_Risk":"PASS","Notes":"n"}
    return {"event_id":event_id,"registration_candidate":True,"ledger_write_authority":False,
            "capacity_gate_pass":True,"liquidity_persistence_gate_pass":True,"row_values":row,
            "execution_capacity":{"status":"PASS","quantity":"1","quote_provenance_sha256":"oldq"},
            "can_trade":False,"capital_permission":"DENY"}

def continuity_pass():
    return {"status":"PASS","contract_id":m.cont.CONTRACT_ID,"continuity_gap_ms":1000,
            "adverse_drift_bps":"0.5","frozen_quantity":"1","fresh_notional_usdt":"100",
            "fresh_entry_impact_bps":"0.1","fresh_exit_impact_bps":"0.1",
            "fresh_quote_provenance_sha256":"fq","fresh_book_provenance_sha256":"fb",
            "reference_provenance_sha256":"fr","provenance_sha256":"p"}


def setup(monkeypatch, upstream_candidate=True, continuity=None):
    async def capture(symbol, refs):
        q={"symbol":symbol,"provenance_sha256":"oldq","decision_time_ms":1000,"event_time_ms":950}
        seq={"states":[{"quote":q}]}
        return {"symbol":symbol}, {"provenance_sha256":"b"}, {"provenance_sha256":"r"}, {"x":1}, seq
    monkeypatch.setattr(m.r95,"_capture_symbol",capture)
    monkeypatch.setattr(m.r95.r94.r93.r92.base,"_state",lambda s:s)
    monkeypatch.setattr(m.r95,"process_snapshot",lambda *a,**k:{"results":[{"symbol":"BTCUSDT"}],
        "registration_candidates":[candidate()] if upstream_candidate else []})
    async def fresh(symbol, rules):
        return {"symbol":symbol},{"provenance_sha256":"fb"},{"provenance_sha256":"r"},{"x":1}
    monkeypatch.setattr(m,"_fresh_continuity_state",fresh)
    monkeypatch.setattr(m.cont,"evaluate_continuity",lambda *a,**k: continuity or continuity_pass())

def test_pass_reserves_only_after_r96(monkeypatch):
    setup(monkeypatch); calls=[]
    monkeypatch.setattr(m.r95.r94,"_reserve_external",lambda *a:calls.append(a))
    out=asyncio.run(m.run_sweep(["BTCUSDT"],state()))
    assert out["registration_candidate_count"]==1 and len(calls)==1
    assert out["registration_candidates"][0]["decision_continuity_gate_pass"] is True


def test_continuity_fail_does_not_reserve(monkeypatch):
    setup(monkeypatch,continuity={"status":"FAIL_CLOSED","reason":"ADVERSE_DRIFT_EXCEEDS_MODEL"}); calls=[]
    monkeypatch.setattr(m.r95.r94,"_reserve_external",lambda *a:calls.append(a))
    out=asyncio.run(m.run_sweep(["BTCUSDT"],state()))
    assert out["registration_candidate_count"]==0 and calls==[]
    assert out["results"][0]["status"]=="DECISION_CONTINUITY_FAIL_CLOSED"


def test_no_r95_candidate_skips_fresh_capture(monkeypatch):
    setup(monkeypatch,upstream_candidate=False); calls=[]
    async def should_not_run(symbol, rules): calls.append(symbol); raise AssertionError
    monkeypatch.setattr(m,"_fresh_continuity_state",should_not_run)
    out=asyncio.run(m.run_sweep(["BTCUSDT"],state()))
    assert out["registration_candidate_count"]==0 and calls==[]


def test_symbol_capture_failure_is_isolated(monkeypatch):
    async def capture(symbol, refs):
        if symbol=="BTCUSDT": raise RuntimeError("boom")
        q={"symbol":symbol,"provenance_sha256":"oldq","decision_time_ms":1000,"event_time_ms":950}
        return {"symbol":symbol},{"provenance_sha256":"b"},{"provenance_sha256":"r"},{"x":1},{"states":[{"quote":q}]}
    monkeypatch.setattr(m.r95,"_capture_symbol",capture)
    monkeypatch.setattr(m.r95.r94.r93.r92.base,"_state",lambda s:s)
    monkeypatch.setattr(m.r95,"process_snapshot",lambda *a,**k:{"results":[{"symbol":"ETHUSDT"}],"registration_candidates":[]})
    out=asyncio.run(m.run_sweep(["BTCUSDT","ETHUSDT"],state()))
    assert out["symbol_count"]==2 and out["registration_candidate_count"]==0
    assert out["results"][0]["status"]=="R95_PERSISTENCE_CAPTURE_FAIL_CLOSED"

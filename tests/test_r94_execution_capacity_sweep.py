from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("r94_sweep_test", ROOT / "tools" / "r6_execution_capacity_sweep.py")
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def fake_candidate(event_id="E1"):
    row = {"Symbol":"BTC","CURRENT_Decision":"ENTER_LONG_AT_R85_DECISION_QUOTE",
           "CURRENT_Trigger_Spec":json.dumps({"quote_provenance_sha256":"q"}),
           "CURRENT_Stop":99.0,"CURRENT_Target":103.0,"RR_Net":2.0,
           "Provenance_State":"p","Operational_Risk":"PASS","Notes":"n"}
    return {"event_id":event_id,"registration_candidate":True,"ledger_write_authority":False,
            "row_values":row,"registration_row_sha256":"old","can_trade":False,
            "capital_permission":"DENY"}


def cap_pass():
    out = {"schema":m.cap.SCHEMA,"contract_id":m.cap.CONTRACT_ID,"status":"PASS",
           "provenance_sha256":"cp","research_equity_usdt":"10000","quantity":"1",
           "gross_notional_usdt":"100","actual_planned_risk_usdt":"5",
           "actual_planned_risk_pct":"0.05","entry_impact_bps":"0.1","exit_impact_bps":"0.1",
           "rounded_stop":"99.00","rounded_target":"103.00","net_rr_after_venue_rounding":"2.1",
           "capacity_book_provenance_sha256":"b","symbol_rules_provenance_sha256":"r"}
    return out
def setup_common(monkeypatch, capacity_result):
    monkeypatch.setattr(m.r93.r92.base, "_state", lambda state: state)
    monkeypatch.setattr(m.r93, "process_snapshot", lambda snap, state, refs: {
        "results":[{"symbol":"BTCUSDT","status":"REGISTRATION_CANDIDATE_READY",
                    "market_integrity":{"status":"PASS"},"registration_candidate":True}],
        "registration_candidates":[fake_candidate()]})
    monkeypatch.setattr(m.cap, "evaluate_capacity", lambda *args, **kwargs: capacity_result)
    snapshot = {"schema":m.r93.r92.base.r84.SCHEMA,"requested_symbols":["BTCUSDT"],
                "evidence":[{"symbol":"BTCUSDT","quote":{"symbol":"BTCUSDT"}}]}
    state = {"ledger_state":{"existing_event_ids":[]},
             "risk_state":{"proposed_risk_pct":"0.5","aggregate_open_risk_pct":"0","entries_today":0}}
    books = {"BTCUSDT":{"provenance_sha256":"b"}}
    rules = {"BTCUSDT":{"provenance_sha256":"r"}}
    refs = {"BTCUSDT":{"x":1}}
    return snapshot,state,books,rules,refs


def test_failed_capacity_does_not_reserve(monkeypatch):
    snapshot,state,books,rules,refs = setup_common(monkeypatch, {"status":"FAIL_CLOSED","reason":"DEPTH"})
    calls=[]
    monkeypatch.setattr(m, "_reserve_external", lambda *args: calls.append(args))
    out=m.process_snapshot(snapshot,state,books,rules,refs)
    assert out["registration_candidate_count"]==0
    assert out["results"][0]["status"]=="EXECUTION_CAPACITY_FAIL_CLOSED"
    assert calls==[]


def test_passed_capacity_reserves_and_attaches(monkeypatch):
    snapshot,state,books,rules,refs = setup_common(monkeypatch, cap_pass())
    calls=[]
    monkeypatch.setattr(m, "_reserve_external", lambda *args: calls.append(args))
    out=m.process_snapshot(snapshot,state,books,rules,refs)
    assert out["registration_candidate_count"]==1
    assert calls and calls[0][2]=="E1"
    c=out["registration_candidates"][0]
    assert c["capacity_gate_pass"] is True
    assert c["row_values"]["CURRENT_Stop"]==99.0
    assert "r94_execution_capacity" in json.loads(c["row_values"]["CURRENT_Trigger_Spec"])


def test_capacity_mode_acquires_binance_depth_before_bybit(monkeypatch):
    import asyncio
    order = []
    monkeypatch.setattr(m.cap, "fetch_symbol_rules", lambda symbol: {"provenance_sha256":"r"})
    async def fake_capture(symbol):
        order.append("BINANCE_DEPTH")
        return ({"symbol":symbol,"decision_time_ms":1000,"provenance_sha256":"q"},
                {"provenance_sha256":"b"})
    monkeypatch.setattr(m.cap, "capture_quote_and_capacity", fake_capture)
    def fake_bybit(symbol):
        order.append("BYBIT_L1")
        return {"received_at_ms":1100}
    monkeypatch.setattr(m.r93.gate, "fetch_bybit_orderbook", fake_bybit)
    monkeypatch.setattr(m.r93.gate, "evaluate", lambda *args, **kwargs: {"status":"PASS","details":{}})
    monkeypatch.setattr(m, "_build_evidence", lambda symbol, refs, quote: {"symbol":symbol,"quote":quote})
    refs = {"BTCUSDT":{}, "ETHUSDT":{}}
    asyncio.run(m._capture_symbol("BTCUSDT", refs))
    assert order == ["BINANCE_DEPTH", "BYBIT_L1"]

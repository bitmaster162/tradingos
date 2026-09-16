from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("r96_gate_test",ROOT/"tools"/"r6_decision_continuity_gate.py")
assert SPEC and SPEC.loader
m=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(m)
T=1_800_000_000_000


def quote(decision_ms, update_id, bid="99.99", ask="100.00"):
    event=decision_ms-50
    q={"schema":m.cap.r82.SCHEMA,"version":"1.0.0","source":"binance_spot_diff_depth_local_book",
       "symbol":"BTCUSDT","stream":"btcusdt@depth@100ms","event_time_ms":event,
       "received_at_ms":decision_ms-10,"age_ms":40,"snapshot_update_id":10,"snapshot_sha256":"s",
       "first_update_id":11,"final_update_id":update_id,"bid":bid,"bid_qty":"200","ask":ask,"ask_qty":"200",
       "raw_event_sha256":"e","decision_time_ms":decision_ms,"decision_age_ms":50,"events_applied":update_id-10,
       "restart_count":0,"can_trade":False,"capital_permission":"DENY"}
    q["provenance_sha256"]=m.cap.r82.stable_sha256({k:v for k,v in q.items() if k!="provenance_sha256"})
    return q

def book(q, bid_qty="200", ask_qty="200", bad_ask=None, bad_bid=None):
    bids=[[q["bid"],bid_qty]] if bad_bid is None else [[q["bid"],"1"],[bad_bid,"200"]]
    asks=[[q["ask"],ask_qty]] if bad_ask is None else [[q["ask"],"1"],[bad_ask,"200"]]
    b={"schema":m.cap.BOOK_SCHEMA,"symbol":"BTCUSDT","event_time_ms":q["event_time_ms"],
       "decision_time_ms":q["decision_time_ms"],"snapshot_update_id":10,"final_update_id":q["final_update_id"],
       "quote_provenance_sha256":q["provenance_sha256"],"bid_levels":bids,"ask_levels":asks,
       "captured_bid_notional":"20000","captured_ask_notional":"20000","max_capture_notional_usdt":"20000",
       "restart_count":0,"can_trade":False,"capital_permission":"DENY"}
    b["provenance_sha256"]=m.cap.sha256_json(b); return b


def rules():
    r={"schema":m.cap.FILTER_SCHEMA,"symbol":"BTCUSDT","status":"TRADING","tick_size":"0.01",
       "qty_step":"0.001","min_qty":"0.001","max_qty":"1000","min_notional":"5","max_notional":None,
       "base_asset_precision":8,"source":"binance_spot_exchangeInfo","received_at_ms":T,"raw_sha256":"raw"}
    r["provenance_sha256"]=m.cap.sha256_json(r); return r


def ref(q, bid="99.99", ask="100.00"):
    ts=q["decision_time_ms"]
    return {"venue":"BYBIT_SPOT","source":"bybit_v5_spot_orderbook_l1","symbol":"BTCUSDT",
            "bid":bid,"ask":ask,"provider_book_time_ms":ts-20,"provider_cross_sequence_time_ms":ts-22,
            "provider_response_time_ms":ts-5,"request_started_at_ms":ts-30,"received_at_ms":ts+20,
            "integrity_final_time_ms":ts+20,"update_id":1,"sequence":2,"raw_sha256":"c"*64}

def candidate(old_q, direction="LONG", quantity="10"):
    decision="ENTER_LONG_AT_R85_DECISION_QUOTE" if direction=="LONG" else "ENTER_SHORT_AT_R92_DECISION_QUOTE"
    row={"Symbol":"BTC","CURRENT_Decision":decision,"CURRENT_Stop":98.0,"CURRENT_Target":103.0,
         "CURRENT_Trigger_Spec":"{}","Operational_Risk":"PASS","Notes":"n"}
    return {"registration_candidate":True,"ledger_write_authority":False,"capacity_gate_pass":True,
            "liquidity_persistence_gate_pass":True,"row_values":row,
            "execution_capacity":{"status":"PASS","quantity":quantity,
                                  "quote_provenance_sha256":old_q["provenance_sha256"]},
            "can_trade":False,"capital_permission":"DENY"}


def test_long_continuity_pass():
    old=quote(T,12); fresh=quote(T+1000,13)
    out=m.evaluate_continuity(candidate(old),old,fresh,book(fresh),rules(),ref(fresh))
    assert out["status"]=="PASS" and out["continuity_gap_ms"]==1000


def test_short_continuity_pass():
    old=quote(T,12); fresh=quote(T+1000,13,bid="99.98",ask="99.99")
    c=candidate(old,"SHORT"); c["row_values"]["CURRENT_Stop"]=102.0; c["row_values"]["CURRENT_Target"]=97.0
    out=m.evaluate_continuity(c,old,fresh,book(fresh),rules(),ref(fresh,bid="99.98",ask="99.99"))
    assert out["status"]=="PASS" and out["direction"]=="SHORT"


def test_gap_over_2s_fails():
    old=quote(T,12); fresh=quote(T+2001,13)
    out=m.evaluate_continuity(candidate(old),old,fresh,book(fresh),rules(),ref(fresh))
    assert out["reason"]=="CONTINUITY_GAP_OUT_OF_RANGE"


def test_non_strict_event_time_fails():
    old=quote(T,12); fresh=quote(T+1000,13); fresh["event_time_ms"]=old["event_time_ms"]
    fresh["provenance_sha256"]=m.cap.r82.stable_sha256({k:v for k,v in fresh.items() if k!="provenance_sha256"})
    out=m.evaluate_continuity(candidate(old),old,fresh,book(fresh),rules(),ref(fresh))
    assert out["reason"]=="EVENT_TIME_NOT_STRICT"

def test_long_adverse_drift_over_2bps_fails():
    old=quote(T,12); fresh=quote(T+1000,13,bid="100.02",ask="100.03")
    out=m.evaluate_continuity(candidate(old),old,fresh,book(fresh),rules(),ref(fresh,bid="100.02",ask="100.03"))
    assert out["reason"]=="ADVERSE_DRIFT_EXCEEDS_MODEL"


def test_short_adverse_drift_over_2bps_fails():
    old=quote(T,12); fresh=quote(T+1000,13,bid="99.96",ask="99.97")
    c=candidate(old,"SHORT"); c["row_values"]["CURRENT_Stop"]=102.0; c["row_values"]["CURRENT_Target"]=97.0
    out=m.evaluate_continuity(c,old,fresh,book(fresh),rules(),ref(fresh,bid="99.96",ask="99.97"))
    assert out["reason"]=="ADVERSE_DRIFT_EXCEEDS_MODEL"


def test_favorable_long_drift_is_allowed():
    old=quote(T,12); fresh=quote(T+1000,13,bid="99.89",ask="99.90")
    out=m.evaluate_continuity(candidate(old),old,fresh,book(fresh),rules(),ref(fresh,bid="99.89",ask="99.90"))
    assert out["status"]=="PASS" and out["adverse_drift_bps"]=="0"


def test_fresh_capacity_impact_fails():
    old=quote(T,12); fresh=quote(T+1000,13)
    out=m.evaluate_continuity(candidate(old),old,fresh,book(fresh,bad_ask="100.10"),rules(),ref(fresh))
    assert out["reason"]=="FRESH_CAPACITY_IMPACT_EXCEEDS_MODEL"


def test_fresh_integrity_fails():
    old=quote(T,12); fresh=quote(T+1000,13)
    out=m.evaluate_continuity(candidate(old),old,fresh,book(fresh),rules(),ref(fresh,bid="100.40",ask="100.41"))
    assert out["reason"]=="FRESH_MARKET_INTEGRITY_FAIL"

def test_frozen_quantity_step_mismatch_fails():
    old=quote(T,12); fresh=quote(T+1000,13)
    out=m.evaluate_continuity(candidate(old,quantity="10.0005"),old,fresh,book(fresh),rules(),ref(fresh))
    assert out["reason"]=="FRESH_CAPACITY_UNPROVEN"
    assert "venue_step" in out["details"]["error"]


def test_missing_r95_pass_fails():
    old=quote(T,12); fresh=quote(T+1000,13); c=candidate(old); c["liquidity_persistence_gate_pass"]=False
    out=m.evaluate_continuity(c,old,fresh,book(fresh),rules(),ref(fresh))
    assert out["reason"]=="CONTINUITY_INPUT_INVALID"


def test_fresh_price_outside_geometry_fails():
    old=quote(T,12); fresh=quote(T+1000,13,bid="102.99",ask="103.00")
    out=m.evaluate_continuity(candidate(old),old,fresh,book(fresh),rules(),ref(fresh,bid="102.99",ask="103.00"))
    assert out["reason"] in {"ADVERSE_DRIFT_EXCEEDS_MODEL","FRESH_PRICE_OUTSIDE_FROZEN_GEOMETRY"}


def test_contract_thresholds_are_inherited_not_loosened():
    assert m.MAX_CONTINUITY_GAP_MS==2000
    assert m.MAX_ADVERSE_DRIFT_BPS==m.Decimal("2")
    assert m.cap.MAX_IMPACT_BPS==m.Decimal("2")

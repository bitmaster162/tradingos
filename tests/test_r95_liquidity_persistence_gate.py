from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("r95_gate_test", ROOT / "tools" / "r6_liquidity_persistence_gate.py")
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def quote(event_ms: int, update_id: int, *, bid="99.99", ask="100"):
    q = {"schema": m.cap.r82.SCHEMA, "version": "1.0.0", "source": "binance_spot_diff_depth_local_book",
         "symbol": "BTCUSDT", "stream": "btcusdt@depth@100ms", "event_time_ms": event_ms,
         "received_at_ms": event_ms + 20, "age_ms": 20, "snapshot_update_id": 10,
         "snapshot_sha256": "s", "first_update_id": 11, "final_update_id": update_id,
         "bid": bid, "bid_qty": "200", "ask": ask, "ask_qty": "200",
         "raw_event_sha256": "e", "decision_time_ms": event_ms + 50,
         "decision_age_ms": 50, "events_applied": update_id - 10, "restart_count": 0,
         "can_trade": False, "capital_permission": "DENY"}
    q["provenance_sha256"] = m.cap.r82.stable_sha256({k:v for k,v in q.items() if k != "provenance_sha256"})
    return q


def book(q, *, bid_qty="200", ask_qty="200", bad_ask=None, bad_bid=None):
    bids = [[q["bid"], bid_qty]] if bad_bid is None else [[q["bid"], "1"], [bad_bid, "200"]]
    asks = [[q["ask"], ask_qty]] if bad_ask is None else [[q["ask"], "1"], [bad_ask, "200"]]
    b = {"schema": m.cap.BOOK_SCHEMA, "symbol": q["symbol"], "event_time_ms": q["event_time_ms"],
         "decision_time_ms": q["decision_time_ms"], "snapshot_update_id": q["snapshot_update_id"],
         "final_update_id": q["final_update_id"], "quote_provenance_sha256": q["provenance_sha256"],
         "bid_levels": bids, "ask_levels": asks, "captured_bid_notional": "20000",
         "captured_ask_notional": "20000", "max_capture_notional_usdt": "20000",
         "restart_count": 0, "can_trade": False, "capital_permission": "DENY"}
    b["provenance_sha256"] = m.cap.sha256_json(b)
    return b


def sequence(times=(1000,1100,1200), *, bad_index=None, bad_ask=None, bad_bid=None):
    states=[]
    for i,t in enumerate(times):
        q=quote(t,12+i)
        b=book(q, bad_ask=bad_ask if i==bad_index else None, bad_bid=bad_bid if i==bad_index else None)
        states.append({"index":i,"quote":q,"book":b})
    s={"schema":m.SEQUENCE_SCHEMA,"contract_id":m.CONTRACT_ID,"symbol":"BTCUSDT",
       "required_states":m.REQUIRED_STATES,"event_span_ms":times[-1]-times[0],"states":states,
       "restart_count":0,"can_trade":False,"capital_permission":"DENY"}
    s["provenance_sha256"]=m.cap.sha256_json(s)
    return s


def candidate(seq, direction="LONG"):
    final=seq["states"][-1]["quote"]
    decision="ENTER_LONG_AT_R85_DECISION_QUOTE" if direction=="LONG" else "ENTER_SHORT_AT_R92_DECISION_QUOTE"
    row={"Symbol":"BTC","CURRENT_Decision":decision,
         "CURRENT_Trigger_Spec":json.dumps({"quote_provenance_sha256":final["provenance_sha256"]})}
    return {"registration_candidate":True,"ledger_write_authority":False,"row_values":row,
            "can_trade":False,"capital_permission":"DENY"}


def capacity(seq, quantity="10"):
    final=seq["states"][-1]
    return {"status":"PASS","quantity":quantity,
            "quote_provenance_sha256":final["quote"]["provenance_sha256"],
            "capacity_book_provenance_sha256":final["book"]["provenance_sha256"]}


def test_long_persistence_pass():
    s=sequence(); out=m.evaluate_persistence(candidate(s),capacity(s),s)
    assert out["status"]=="PASS"
    assert out["state_count"]==3
    assert out["event_span_ms"]==200


def test_short_persistence_pass():
    s=sequence(); out=m.evaluate_persistence(candidate(s,"SHORT"),capacity(s),s)
    assert out["status"]=="PASS"
    assert out["direction"]=="SHORT"


def test_state_count_too_small_fails():
    s=sequence((1000,1200)); out=m.evaluate_persistence(candidate(s),capacity(s),s)
    assert out["reason"]=="PERSISTENCE_INPUT_INVALID"
    assert "state_count" in out["details"]["error"]


def test_span_too_short_fails():
    s=sequence((1000,1050,1100)); out=m.evaluate_persistence(candidate(s),capacity(s),s)
    assert "span_too_short" in out["details"]["error"]


def test_gap_too_large_fails():
    s=sequence((1000,1600,1800)); out=m.evaluate_persistence(candidate(s),capacity(s),s)
    assert "gap_too_large" in out["details"]["error"]


def test_total_span_too_long_fails():
    s=sequence((1000,1400,1800,2200)); out=m.evaluate_persistence(candidate(s),capacity(s),s)
    assert "span_too_long" in out["details"]["error"]


def test_sequence_provenance_tamper_fails():
    s=sequence(); s["event_span_ms"]=201
    out=m.evaluate_persistence(candidate(s),capacity(s),s)
    assert "provenance_mismatch" in out["details"]["error"]


def test_update_id_not_strict_fails():
    s=sequence(); q=s["states"][1]["quote"]; q["final_update_id"]=12
    q["provenance_sha256"]=m.cap.r82.stable_sha256({k:v for k,v in q.items() if k!="provenance_sha256"})
    b=s["states"][1]["book"]; b["final_update_id"]=12; b["quote_provenance_sha256"]=q["provenance_sha256"]
    b["provenance_sha256"]=m.cap.sha256_json({k:v for k,v in b.items() if k!="provenance_sha256"})
    s["provenance_sha256"]=m.cap.sha256_json({k:v for k,v in s.items() if k!="provenance_sha256"})
    out=m.evaluate_persistence(candidate(s),capacity(s),s)
    assert "update_id_not_strict" in out["details"]["error"]


def test_final_quote_binding_fails():
    s=sequence(); c=candidate(s); c["row_values"]["CURRENT_Trigger_Spec"]=json.dumps({"quote_provenance_sha256":"bad"})
    out=m.evaluate_persistence(c,capacity(s),s)
    assert out["reason"]=="FINAL_QUOTE_BINDING_MISMATCH"


def test_final_book_binding_fails():
    s=sequence(); cp=capacity(s); cp["capacity_book_provenance_sha256"]="bad"
    out=m.evaluate_persistence(candidate(s),cp,s)
    assert out["reason"]=="R94_FINAL_BOOK_BINDING_MISMATCH"


def test_r94_capacity_must_pass():
    s=sequence(); out=m.evaluate_persistence(candidate(s),{"status":"FAIL_CLOSED"},s)
    assert "r94_capacity_not_pass" in out["details"]["error"]


def test_persistent_entry_impact_failure_is_indexed():
    s=sequence(bad_index=1,bad_ask="100.10")
    out=m.evaluate_persistence(candidate(s),capacity(s),s)
    assert out["reason"]=="PERSISTENT_ENTRY_IMPACT_EXCEEDS_MODEL"
    assert out["details"]["index"]==1


def test_persistent_exit_impact_failure_is_indexed():
    s=sequence(bad_index=1,bad_bid="99.89")
    out=m.evaluate_persistence(candidate(s),capacity(s),s)
    assert out["reason"]=="PERSISTENT_EXIT_IMPACT_EXCEEDS_MODEL"
    assert out["details"]["index"]==1


def test_short_maps_sell_to_entry_and_buy_to_exit():
    s=sequence(bad_index=1,bad_ask="100.10")
    out=m.evaluate_persistence(candidate(s,"SHORT"),capacity(s),s)
    assert out["reason"]=="PERSISTENT_EXIT_IMPACT_EXCEEDS_MODEL"
    assert out["details"]["index"]==1


def venue_rules():
    r={"schema":m.cap.FILTER_SCHEMA,"symbol":"BTCUSDT","status":"TRADING",
       "tick_size":"0.01","qty_step":"0.001","min_qty":"0.001","max_qty":"1000",
       "min_notional":"5","max_notional":None,"base_asset_precision":8,
       "source":"binance_spot_exchangeInfo","received_at_ms":1150,"raw_sha256":"raw"}
    r["provenance_sha256"]=m.cap.sha256_json(r)
    return r


def test_full_equity_persistence_benchmark_pass():
    out=m.benchmark_full_equity_persistence(sequence(),venue_rules())
    assert out["status"]=="PASS" and out["state_count"]==3


def test_full_equity_persistence_benchmark_rejects_one_bad_state():
    s=sequence(bad_index=1,bad_ask="100.10")
    out=m.benchmark_full_equity_persistence(s,venue_rules())
    assert out["reason"]=="PERSISTENCE_BENCHMARK_IMPACT_EXCEEDS_MODEL"
    assert out["details"]["index"]==1

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("r94_cap_test", ROOT / "tools" / "r6_execution_capacity_gate.py")
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def quote(symbol="BTCUSDT", bid="99.99", ask="100"):
    q = {"schema": m.r82.SCHEMA, "version": "1.0.0", "source": "binance_spot_diff_depth_local_book",
         "symbol": symbol, "stream": f"{symbol.lower()}@depth@100ms", "event_time_ms": 1000,
         "received_at_ms": 1100, "age_ms": 100, "snapshot_update_id": 10, "snapshot_sha256": "s",
         "first_update_id": 11, "final_update_id": 12, "bid": bid, "bid_qty": "200",
         "ask": ask, "ask_qty": "200", "raw_event_sha256": "e", "decision_time_ms": 1200,
         "decision_age_ms": 200, "events_applied": 1, "restart_count": 0,
         "can_trade": False, "capital_permission": "DENY"}
    q["provenance_sha256"] = m.r82.stable_sha256({k:v for k,v in q.items() if k != "provenance_sha256"})
    return q
def book(q, bids=None, asks=None):
    b = {"schema": m.BOOK_SCHEMA, "symbol": q["symbol"], "event_time_ms": q["event_time_ms"],
         "decision_time_ms": q["decision_time_ms"], "snapshot_update_id": q["snapshot_update_id"],
         "final_update_id": q["final_update_id"], "quote_provenance_sha256": q["provenance_sha256"],
         "bid_levels": bids or [[q["bid"], "200"]], "ask_levels": asks or [[q["ask"], "200"]],
         "captured_bid_notional": "20000", "captured_ask_notional": "20000",
         "max_capture_notional_usdt": "20000", "restart_count": 0,
         "can_trade": False, "capital_permission": "DENY"}
    b["provenance_sha256"] = m.sha256_json(b)
    return b


def rules(symbol="BTCUSDT", **overrides):
    r = {"schema": m.FILTER_SCHEMA, "symbol": symbol, "status": "TRADING", "tick_size": "0.01",
         "qty_step": "0.001", "min_qty": "0.001", "max_qty": "1000", "min_notional": "5",
         "max_notional": None, "base_asset_precision": 8, "source": "binance_spot_exchangeInfo",
         "received_at_ms": 1150, "raw_sha256": "raw"}
    r.update(overrides)
    r["provenance_sha256"] = m.sha256_json(r)
    return r


def runtime(slip="2", risk="0.5"):
    return {"cost_model": {"entry_fee_rate": "0.0005", "exit_fee_rate": "0.0005",
                            "entry_slippage_bps": slip, "exit_slippage_bps": slip},
            "risk_state": {"proposed_risk_pct": risk}}
def candidate(q, direction="LONG", stop=None, target=None):
    if direction == "LONG":
        entry = 100.02
        stop = 99.0 if stop is None else stop
        target = 103.0 if target is None else target
        decision = "ENTER_LONG_AT_R85_DECISION_QUOTE"
        trigger = {"quote_provenance_sha256": q["provenance_sha256"], "raw_ask": q["ask"]}
    else:
        entry = 99.970002
        stop = 101.0 if stop is None else stop
        target = 97.0 if target is None else target
        decision = "ENTER_SHORT_AT_R92_DECISION_QUOTE"
        trigger = {"quote_provenance_sha256": q["provenance_sha256"], "raw_bid": q["bid"]}
    row = {"Symbol": q["symbol"].removesuffix("USDT"), "CURRENT_Decision": decision,
           "CURRENT_Entry_Price": entry, "CURRENT_Stop": stop, "CURRENT_Target": target,
           "CURRENT_Trigger_Spec": json.dumps(trigger), "Provenance_State": "p",
           "Operational_Risk": "PASS", "Notes": "n", "RR_Net": 2.0}
    return {"registration_candidate": True, "ledger_write_authority": False, "row_values": row,
            "can_trade": False, "capital_permission": "DENY"}


def test_long_capacity_pass():
    q = quote(); out = m.evaluate_capacity(candidate(q), runtime(), q, book(q), rules())
    assert out["status"] == "PASS"
    assert float(out["gross_notional_usdt"]) <= 10000
    assert float(out["entry_impact_bps"]) <= 2


def test_short_capacity_pass():
    q = quote(); out = m.evaluate_capacity(candidate(q, "SHORT"), runtime(), q, book(q), rules())
    assert out["status"] == "PASS"
    assert out["short_capacity_semantics"].startswith("PAPER_")
def test_insufficient_depth_fails():
    q = quote(); b = book(q, bids=[[q["bid"], "0.01"]], asks=[[q["ask"], "0.01"]])
    out = m.evaluate_capacity(candidate(q), runtime(), q, b, rules())
    assert out["status"] == "FAIL_CLOSED"
    assert out["reason"] == "DEPTH_CAPACITY_UNPROVEN"


def test_entry_impact_over_two_bps_fails():
    q = quote(); b = book(q, asks=[["100", "1"], ["100.10", "200"]])
    out = m.evaluate_capacity(candidate(q), runtime(), q, b, rules())
    assert out["reason"] == "ENTRY_DEPTH_IMPACT_EXCEEDS_MODEL"


def test_exit_impact_over_two_bps_fails():
    q = quote(); b = book(q, bids=[["99.99", "1"], ["99.89", "200"]])
    out = m.evaluate_capacity(candidate(q), runtime(), q, b, rules())
    assert out["reason"] == "EXIT_DEPTH_IMPACT_EXCEEDS_MODEL"


def test_quote_binding_tamper_fails():
    q = quote(); c = candidate(q); c["row_values"]["CURRENT_Trigger_Spec"] = json.dumps({"quote_provenance_sha256":"bad"})
    out = m.evaluate_capacity(c, runtime(), q, book(q), rules())
    assert out["reason"] == "CANDIDATE_QUOTE_BINDING_MISMATCH"


def test_book_provenance_tamper_fails():
    q = quote(); b = book(q); b["bid_levels"][0][1] = "999"
    out = m.evaluate_capacity(candidate(q), runtime(), q, b, rules())
    assert out["reason"] == "CAPACITY_INPUT_INVALID"
def test_r91_slippage_contract_mismatch_fails():
    q = quote(); out = m.evaluate_capacity(candidate(q), runtime(slip="3"), q, book(q), rules())
    assert out["reason"] == "R91_SLIPPAGE_CONTRACT_MISMATCH"


def test_min_notional_fails_closed():
    q = quote(); r = rules(min_notional="9000")
    out = m.evaluate_capacity(candidate(q), runtime(), q, book(q), r)
    assert out["reason"] == "NOTIONAL_BELOW_VENUE_MIN"


def test_one_x_gross_cap_is_enforced():
    q = quote(); c = candidate(q, stop=99.95, target=103)
    out = m.evaluate_capacity(c, runtime(), q, book(q), rules())
    assert out["status"] == "PASS"
    assert float(out["gross_equity_multiple"]) <= 1.0


def test_adverse_tick_rounding_long():
    q = quote(); c = candidate(q, stop=99.006, target=103.009)
    out = m.evaluate_capacity(c, runtime(), q, book(q), rules())
    assert out["status"] == "PASS"
    assert out["rounded_stop"] == "99.00"
    assert out["rounded_target"] == "103.00"


def test_adverse_tick_rounding_short():
    q = quote(); c = candidate(q, "SHORT", stop=101.001, target=97.001)
    out = m.evaluate_capacity(c, runtime(), q, book(q), rules())
    assert out["status"] == "PASS"
    assert out["rounded_stop"] == "101.01"
    assert out["rounded_target"] == "97.01"
def test_exchange_info_parser_uses_conservative_quantity_step(monkeypatch):
    raw = {"symbols":[{"symbol":"BTCUSDT","status":"TRADING","quoteAsset":"USDT",
        "isSpotTradingAllowed":True,"baseAssetPrecision":8,"filters":[
        {"filterType":"PRICE_FILTER","tickSize":"0.01"},
        {"filterType":"LOT_SIZE","minQty":"0.00001","maxQty":"9000","stepSize":"0.00001"},
        {"filterType":"MARKET_LOT_SIZE","minQty":"0","maxQty":"145","stepSize":"0"},
        {"filterType":"NOTIONAL","minNotional":"5","applyMinToMarket":True,
         "maxNotional":"9000000","applyMaxToMarket":False}]}]}
    monkeypatch.setattr(m, "_fetch_json", lambda url: raw)
    out = m.fetch_symbol_rules("BTCUSDT")
    assert out["qty_step"] == "0.00001"
    assert out["min_notional"] == "5"
    assert out["max_notional"] is None


def test_rule_provenance_tamper_fails():
    q = quote(); r = rules(); r["min_notional"] = "99"
    out = m.evaluate_capacity(candidate(q), runtime(), q, book(q), r)
    assert out["reason"] == "CAPACITY_INPUT_INVALID"


def test_full_equity_benchmark_passes_on_deep_book():
    q = quote()
    out = m.benchmark_full_equity_capacity(q, book(q), rules())
    assert out["status"] == "PASS"
    assert out["benchmark_notional_usdt"] == "10000"
    assert float(out["buy_impact_bps"]) <= 2
    assert float(out["sell_impact_bps"]) <= 2


def test_full_equity_benchmark_rejects_thin_book():
    q = quote()
    b = book(q, bids=[["99.99", "1"], ["99.80", "200"]], asks=[["100", "1"], ["100.20", "200"]])
    out = m.benchmark_full_equity_capacity(q, b, rules())
    assert out["status"] == "FAIL_CLOSED"
    assert out["reason"] == "BENCHMARK_IMPACT_EXCEEDS_MODEL"


def test_full_equity_benchmark_cannot_exceed_one_x():
    q = quote()
    out = m.benchmark_full_equity_capacity(q, book(q), rules(), notional_usdt=m.Decimal("10001"))
    assert out["reason"] == "BENCHMARK_NOTIONAL_OUT_OF_RANGE"

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tools" / "r6_market_integrity_gate.py"
SPEC = importlib.util.spec_from_file_location("r93", PATH)
assert SPEC and SPEC.loader
r93 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = r93
SPEC.loader.exec_module(r93)

T = 1_800_000_000_000


def quote(bid="100.00", ask="100.01", event_offset=200):
    q = {"schema": "tradingos.binance_spot_event_time_quote.v1", "version": "1.0.0",
         "source": "binance_spot_diff_depth_local_book", "symbol": "BTCUSDT",
         "stream": "btcusdt@depth@100ms", "event_time_ms": T-event_offset,
         "received_at_ms": T-100, "age_ms": 100, "snapshot_update_id": 10,
         "snapshot_sha256": "a"*64, "first_update_id": 11, "final_update_id": 11,
         "bid": bid, "bid_qty": "1", "ask": ask, "ask_qty": "1",
         "raw_event_sha256": "b"*64, "decision_time_ms": T-50,
         "decision_age_ms": event_offset-50, "events_applied": 1,
         "can_trade": False, "capital_permission": "DENY", "restart_count": 0}
    q["provenance_sha256"] = r93.r82.stable_sha256(q)
    return q
def ref(bid="100.00", ask="100.01", ts=T-100, symbol="BTCUSDT"):
    return {"venue": "BYBIT_SPOT", "source": "bybit_v5_spot_orderbook_l1",
            "symbol": symbol, "bid": bid, "ask": ask,
            "provider_book_time_ms": ts, "provider_cross_sequence_time_ms": ts-2,
            "provider_response_time_ms": ts+10, "request_started_at_ms": ts-20,
            "received_at_ms": ts+20, "update_id": 1, "sequence": 2,
            "raw_sha256": "c"*64}


def test_pass():
    out = r93.evaluate(quote(), ref(), final_time_ms=T)
    assert out["status"] == "PASS"
    assert float(out["mid_divergence_bps"]) < 1


def test_tampered_binance_quote_fails():
    q = quote(); q["ask"] = "100.02"
    out = r93.evaluate(q, ref(), final_time_ms=T)
    assert out["status"] == "FAIL_CLOSED"


def test_reference_symbol_mismatch_fails():
    out = r93.evaluate(quote(), ref(symbol="ETHUSDT"), final_time_ms=T)
    assert out["status"] == "FAIL_CLOSED"


def test_reference_stale_fails():
    out = r93.evaluate(quote(), ref(ts=T-3000), final_time_ms=T)
    assert out["status"] == "FAIL_CLOSED"
def test_reference_future_skew_fails():
    out = r93.evaluate(quote(), ref(ts=T+1000), final_time_ms=T)
    assert out["status"] == "FAIL_CLOSED"


def test_reference_spread_fails():
    out = r93.evaluate(quote(), ref(bid="99.90", ask="100.10"), final_time_ms=T)
    assert out["reason"] == "REFERENCE_SPREAD_TOO_WIDE"


def test_binance_spread_fails():
    q = quote(bid="99.90", ask="100.10")
    out = r93.evaluate(q, ref(), final_time_ms=T)
    assert out["reason"] == "BINANCE_SPREAD_TOO_WIDE"


def test_cross_venue_divergence_fails():
    out = r93.evaluate(quote(), ref(bid="100.30", ask="100.31"), final_time_ms=T)
    assert out["reason"] == "CROSS_VENUE_DIVERGENCE_TOO_WIDE"


def test_crossed_reference_fails():
    out = r93.evaluate(quote(), ref(bid="100.02", ask="100.01"), final_time_ms=T)
    assert out["status"] == "FAIL_CLOSED"


def test_contract_thresholds_are_frozen():
    assert r93.MAX_BINANCE_SPREAD_BPS == r93.Decimal("10")
    assert r93.MAX_REFERENCE_SPREAD_BPS == r93.Decimal("12")
    assert r93.MAX_MID_DIVERGENCE_BPS == r93.Decimal("20")

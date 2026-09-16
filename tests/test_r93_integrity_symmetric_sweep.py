from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tools" / "r6_integrity_symmetric_sweep.py"
SPEC = importlib.util.spec_from_file_location("r93s", PATH)
assert SPEC and SPEC.loader
r93s = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = r93s
SPEC.loader.exec_module(r93s)

T = 1_800_000_000_000


def quote():
    q = {"schema":"tradingos.binance_spot_event_time_quote.v1","version":"1.0.0",
         "source":"binance_spot_diff_depth_local_book","symbol":"BTCUSDT",
         "stream":"btcusdt@depth@100ms","event_time_ms":T-200,"received_at_ms":T-100,
         "age_ms":100,"snapshot_update_id":10,"snapshot_sha256":"a"*64,
         "first_update_id":11,"final_update_id":11,"bid":"100.00","bid_qty":"1",
         "ask":"100.01","ask_qty":"1","raw_event_sha256":"b"*64,
         "decision_time_ms":T-50,"decision_age_ms":150,"events_applied":1,
         "can_trade":False,"capital_permission":"DENY","restart_count":0}
    q["provenance_sha256"] = r93s.gate.r82.stable_sha256(q)
    return q
def reference(bid="100.00", ask="100.01"):
    return {"venue":"BYBIT_SPOT","source":"bybit_v5_spot_orderbook_l1","symbol":"BTCUSDT",
            "bid":bid,"ask":ask,"provider_book_time_ms":T-100,
            "provider_cross_sequence_time_ms":T-102,"provider_response_time_ms":T-80,
            "request_started_at_ms":T-150,"received_at_ms":T,"update_id":1,
            "sequence":2,"raw_sha256":"c"*64}


def snapshot():
    return {"schema": r93s.r92.base.r84.SCHEMA, "requested_symbols":["BTCUSDT"],
            "evidence":[{"schema":r93s.r92.base.r84.SYMBOL_SCHEMA,"symbol":"BTCUSDT",
                         "quote":quote()}]}


def test_integrity_failure_blocks_downstream(monkeypatch):
    called = {"v": False}
    def fake(*args, **kwargs):
        called["v"] = True
        return {"results":[],"registration_candidates":[]}
    monkeypatch.setattr(r93s.r92, "process_snapshot", fake)
    out = r93s.process_snapshot(snapshot(), {}, {"BTCUSDT":reference("99.0","101.0")})
    assert out["integrity_fail_count"] == 1
    assert out["results"][0]["status"] == "MARKET_INTEGRITY_FAIL_CLOSED"
    assert called["v"] is True
def test_integrity_pass_enriches_evidence(monkeypatch):
    seen = {}
    def fake(snap, state, fetch_m15=None):
        seen["evidence"] = snap["evidence"]
        return {"results":[{"symbol":"BTCUSDT","status":"NO_CANDIDATE_FAIL_CLOSED",
                            "long_candidate":False,"short_candidate":False,
                            "registration_candidate":False}],
                "long_candidate_count":0,"short_candidate_count":0,
                "registration_candidate_count":0,"registration_candidates":[]}
    monkeypatch.setattr(r93s.r92, "process_snapshot", fake)
    out = r93s.process_snapshot(snapshot(), {}, {"BTCUSDT":reference()})
    assert out["integrity_pass_count"] == 1
    assert seen["evidence"][0]["market_integrity"]["status"] == "PASS"
    assert out["results"][0]["market_integrity"]["status"] == "PASS"


def test_missing_reference_fails_closed(monkeypatch):
    monkeypatch.setattr(r93s.r92, "process_snapshot",
                        lambda *a, **k: {"results":[],"registration_candidates":[]})
    out = r93s.process_snapshot(snapshot(), {}, {"BTCUSDT":None})
    assert out["integrity_fail_count"] == 1
    assert out["registration_candidate_count"] == 0
    assert out["ledger_write_authority"] is False

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tools" / "r6_all16_admission_probe.py"
SPEC = importlib.util.spec_from_file_location("r84_probe_test", PATH)
assert SPEC is not None and SPEC.loader is not None
r84 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = r84
SPEC.loader.exec_module(r84)

EXPECTED = (
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT", "TRXUSDT",
    "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "SUIUSDT", "LTCUSDT",
    "BCHUSDT", "AAVEUSDT", "NEARUSDT", "UNIUSDT",
)


def tf_state(open_: float, close: float) -> dict:
    return {"open": open_, "close": close}


def ctha(mult: float) -> dict:
    return {tf: tf_state(100.0, 100.0 * mult) for tf in r84.r83.TF_LIMITS}
def test_canonical_universe_exact() -> None:
    assert r84.ALL16 == EXPECTED
    assert len(set(r84.ALL16)) == 16


def test_relative_strength_has_btc_and_eth_axes() -> None:
    out = r84.relative_state(tf_state(100, 103), tf_state(100, 101), tf_state(100, 102))
    assert round(out["target_bar_return_pct"], 8) == 3.0
    assert round(out["target_minus_btc_pct"], 8) == 2.0
    assert round(out["target_minus_eth_pct"], 8) == 1.0
    assert round(out["eth_minus_btc_pct"], 8) == 1.0


def test_noncanonical_symbol_rejected_before_capture(monkeypatch) -> None:
    monkeypatch.setattr(r84, "fetch_ctha", lambda _: ctha(1.0))
    try:
        asyncio.run(r84.run_probe(["FOOUSDT"]))
    except ValueError as exc:
        assert "non-canonical" in str(exc)
    else:
        raise AssertionError("non-canonical symbol was accepted")
def test_symbol_capture_binds_own_quote_and_references(monkeypatch) -> None:
    refs = {"BTCUSDT": ctha(1.01), "ETHUSDT": ctha(1.02), "SOLUSDT": ctha(1.03)}

    async def fake_quote(**kwargs):
        return {
            "schema": "tradingos.binance_spot_event_time_quote.v1",
            "symbol": kwargs["symbol"],
            "event_time_ms": 123,
            "age_ms": 10,
            "bid": "99",
            "bid_qty": "1",
            "ask": "100",
            "ask_qty": "1",
            "can_trade": False,
            "capital_permission": "DENY",
        }

    monkeypatch.setattr(r84.r83.quote_mod, "capture_quote_with_restarts", fake_quote)
    out = asyncio.run(r84.capture_symbol("SOLUSDT", refs))
    assert out["schema"] == r84.SYMBOL_SCHEMA
    assert out["symbol"] == "SOLUSDT"
    assert out["quote"]["symbol"] == "SOLUSDT"
    rel = out["relative_strength"]["1h"]
    assert round(rel["target_minus_btc_pct"], 8) == 2.0
    assert round(rel["target_minus_eth_pct"], 8) == 1.0
    assert out["admission_authority"] == "EVIDENCE_ONLY_FROZEN_CURRENT_RULES_APPLY"
    assert out["can_trade"] is False
    assert out["capital_permission"] == "DENY"
def test_quote_symbol_mismatch_fails_closed(monkeypatch) -> None:
    refs = {"BTCUSDT": ctha(1.0), "ETHUSDT": ctha(1.0)}

    async def wrong_quote(**kwargs):
        return {
            "schema": "tradingos.binance_spot_event_time_quote.v1",
            "symbol": "ETHUSDT",
        }

    monkeypatch.setattr(r84.r83.quote_mod, "capture_quote_with_restarts", wrong_quote)
    try:
        asyncio.run(r84.capture_symbol("BTCUSDT", refs))
    except RuntimeError as exc:
        assert "symbol mismatch" in str(exc)
    else:
        raise AssertionError("mismatched quote was accepted")


def test_multi_symbol_probe_reuses_btc_eth_reference(monkeypatch) -> None:
    fetched = []

    def fake_ctha(symbol):
        fetched.append(symbol)
        return ctha({"BTCUSDT": 1.01, "ETHUSDT": 1.02, "BNBUSDT": 1.03}[symbol])

    async def fake_quote(**kwargs):
        return {
            "schema": "tradingos.binance_spot_event_time_quote.v1",
            "symbol": kwargs["symbol"],
            "event_time_ms": 123,
        }

    monkeypatch.setattr(r84, "fetch_ctha", fake_ctha)
    monkeypatch.setattr(r84.r83.quote_mod, "capture_quote_with_restarts", fake_quote)
    out = asyncio.run(r84.run_probe(["BTCUSDT", "ETHUSDT", "BNBUSDT"]))
    assert out["schema"] == r84.SCHEMA
    assert out["requested_symbols"] == ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
    assert [x["symbol"] for x in out["evidence"]] == out["requested_symbols"]
    assert fetched.count("BTCUSDT") == 1
    assert fetched.count("ETHUSDT") == 1
    assert fetched.count("BNBUSDT") == 1
    assert out["scored_admission_decision"] == "NOT_COMPUTED_BY_PROBE"
def test_partial_monthly_history_is_explicit_unknown_not_crash() -> None:
    rows = []
    for i in range(60):
        close = 100.0 + i * 0.1
        rows.append([i * 1000, "100", str(close + 1), str(close - 1), str(close), "10", i * 1000 + 999])
    out = r84.summarize_tf_safe(rows)
    assert out["history_bars"] == 60
    assert out["ema25"] is not None
    assert out["ema99"] is None
    assert out["ema_state"] == "UNKNOWN_INSUFFICIENT_HISTORY"
    assert out["indicator_completeness"] == "PARTIAL_INSUFFICIENT_EMA99"
def test_all16_probe_shape_under_mocked_sources(monkeypatch) -> None:
    monkeypatch.setattr(r84, "fetch_ctha", lambda _: ctha(1.0))

    async def fake_quote(**kwargs):
        return {
            "schema": "tradingos.binance_spot_event_time_quote.v1",
            "symbol": kwargs["symbol"],
            "event_time_ms": 123,
        }

    monkeypatch.setattr(r84.r83.quote_mod, "capture_quote_with_restarts", fake_quote)
    out = asyncio.run(r84.run_probe(list(r84.ALL16)))
    assert out["requested_symbols"] == list(r84.ALL16)
    assert len(out["evidence"]) == 16
    assert {x["symbol"] for x in out["evidence"]} == set(r84.ALL16)
    assert all(x["can_trade"] is False for x in out["evidence"])

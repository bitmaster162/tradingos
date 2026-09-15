from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tools" / "binance_spot_event_time_quote_collector.py"
spec = importlib.util.spec_from_file_location("spot_event_quote", PATH)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = m
spec.loader.exec_module(m)

BASE_E = 1_800_000_000_000


def snapshot(update_id: int = 100) -> dict:
    return {
        "lastUpdateId": update_id,
        "bids": [["100.00", "2.0"], ["99.00", "3.0"]],
        "asks": [["101.00", "4.0"], ["102.00", "5.0"]],
    }


def raw_event(*, U: int, u: int, E: int = BASE_E, bids=None, asks=None) -> str:
    data = {
        "e": "depthUpdate", "E": E, "s": "BTCUSDT", "U": U, "u": u,
        "b": bids if bids is not None else [], "a": asks if asks is not None else [],
    }
    return json.dumps({"stream": "btcusdt@depth@100ms", "data": data}, separators=(",", ":"))

def parse(raw: str, *, received_at_ms: int = BASE_E + 10, max_age_ms: int = 2000):
    return m.parse_depth_event(raw, symbol="BTCUSDT", received_at_ms=received_at_ms, max_age_ms=max_age_ms)


def test_snapshot_bridge_produces_event_time_bound_quote() -> None:
    book = m.SpotDepthBook(symbol="BTCUSDT", snapshot=snapshot())
    event = parse(raw_event(U=101, u=103, bids=[["100.50", "1.25"]]))
    quote = book.apply(event)
    assert quote is not None
    assert quote["event_time_ms"] == BASE_E
    assert quote["snapshot_update_id"] == 100
    assert quote["first_update_id"] == 101 and quote["final_update_id"] == 103
    assert quote["bid"] == "100.50" and quote["ask"] == "101.00"
    assert quote["snapshot_sha256"] == m.stable_sha256(snapshot())
    assert quote["provenance_sha256"] == m.stable_sha256(
        {k: v for k, v in quote.items() if k != "provenance_sha256"}
    )


def test_overlapping_contiguous_event_is_accepted() -> None:
    book = m.SpotDepthBook(symbol="BTCUSDT", snapshot=snapshot())
    assert book.apply(parse(raw_event(U=101, u=103))) is not None
    quote = book.apply(parse(raw_event(U=103, u=105, E=BASE_E + 1, asks=[["100.75", "2"]]), received_at_ms=BASE_E + 11))
    assert quote is not None
    assert quote["final_update_id"] == 105
    assert quote["ask"] == "100.75"

def test_gap_fails_closed() -> None:
    book = m.SpotDepthBook(symbol="BTCUSDT", snapshot=snapshot())
    book.apply(parse(raw_event(U=101, u=103)))
    with pytest.raises(m.SpotQuoteReject, match="depth_sequence_gap"):
        book.apply(parse(raw_event(U=105, u=106, E=BASE_E + 1), received_at_ms=BASE_E + 11))


def test_best_level_delete_reveals_next_level() -> None:
    book = m.SpotDepthBook(symbol="BTCUSDT", snapshot=snapshot())
    quote = book.apply(parse(raw_event(U=101, u=101, bids=[["100.00", "0"]])))
    assert quote is not None
    assert quote["bid"] == "99.00"


def test_stale_and_future_event_times_rejected() -> None:
    raw = raw_event(U=101, u=101)
    with pytest.raises(m.SpotQuoteReject, match="stale_event_time_E"):
        parse(raw, received_at_ms=BASE_E + 2001, max_age_ms=2000)
    with pytest.raises(m.SpotQuoteReject, match="future_event_time_E"):
        parse(raw, received_at_ms=BASE_E - 1)


def test_bookticker_without_E_cannot_enter_depth_quote_path() -> None:
    raw = json.dumps({"stream": "btcusdt@bookTicker", "data": {"u": 1, "s": "BTCUSDT", "b": "100", "B": "1", "a": "101", "A": "1"}})
    with pytest.raises(m.SpotQuoteReject):
        parse(raw)


def test_decision_validation_rejects_stale_and_tampered_quote() -> None:
    book = m.SpotDepthBook(symbol="BTCUSDT", snapshot=snapshot())
    quote = book.apply(parse(raw_event(U=101, u=101)))
    assert quote is not None
    assert m.validate_quote_at_decision(quote, decision_time_ms=BASE_E + 100, max_age_ms=2000) == 100
    with pytest.raises(m.SpotQuoteReject, match="quote_stale_at_decision"):
        m.validate_quote_at_decision(quote, decision_time_ms=BASE_E + 2001, max_age_ms=2000)
    tampered = dict(quote)
    tampered["bid"] = "100.25"
    with pytest.raises(m.SpotQuoteReject, match="quote_provenance_mismatch"):
        m.validate_quote_at_decision(tampered, decision_time_ms=BASE_E + 100, max_age_ms=2000)


def test_crossed_book_after_update_fails_closed() -> None:
    book = m.SpotDepthBook(symbol="BTCUSDT", snapshot=snapshot())
    event = parse(raw_event(U=101, u=101, bids=[["101.50", "1"]]))
    with pytest.raises(m.SpotQuoteReject, match="depth_book_crossed_or_locked"):
        book.apply(event)


def test_snapshot_url_is_fixed_public_endpoint() -> None:
    url = m.snapshot_url("BTCUSDT", 1000)
    assert url == "https://data-api.binance.vision/api/v3/depth?symbol=BTCUSDT&limit=1000"
    m.validate_snapshot_url(url)
    for bad in [
        "http://data-api.binance.vision/api/v3/depth?symbol=BTCUSDT&limit=1000",
        "https://evil.example/api/v3/depth?symbol=BTCUSDT&limit=1000",
        "https://data-api.binance.vision/api/v3/order?symbol=BTCUSDT&limit=1000",
    ]:
        with pytest.raises(ValueError):
            m.validate_snapshot_url(bad)


def test_source_has_no_trading_or_credentials_capability() -> None:
    text = PATH.read_text(encoding="utf-8").lower()
    assert "create_order" not in text and "place_order" not in text
    assert "x-mbx-apikey" not in text and "api_secret" not in text
    assert 'quote["can_trade"] = false' in text
    assert 'quote["capital_permission"] = "deny"' in text

def test_bounded_restart_recovers_from_sequence_gap() -> None:
    calls = {"n": 0}
    async def fake_capture(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise m.SpotQuoteReject("depth_sequence_gap")
        book = m.SpotDepthBook(symbol="BTCUSDT", snapshot=snapshot())
        return book.apply(parse(raw_event(U=101, u=101)))
    import asyncio
    quote = asyncio.run(m.capture_quote_with_restarts(max_restarts=1, capture_fn=fake_capture))
    assert calls["n"] == 2
    assert quote["restart_count"] == 1
    assert quote["provenance_sha256"] == m.stable_sha256(
        {k: v for k, v in quote.items() if k != "provenance_sha256"}
    )


def test_nonrestartable_rejection_stays_fail_closed() -> None:
    calls = {"n": 0}
    async def fake_capture(**kwargs):
        calls["n"] += 1
        raise m.SpotQuoteReject("event_identity_mismatch")
    import asyncio
    with pytest.raises(m.SpotQuoteReject, match="event_identity_mismatch"):
        asyncio.run(m.capture_quote_with_restarts(max_restarts=2, capture_fn=fake_capture))
    assert calls["n"] == 1
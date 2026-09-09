import hashlib
import json

import pytest

from btcusdt_bot.collector.futures_book_ticker_gate import (
    FuturesBookTickerGate,
    FuturesBookTickerGateReject,
)


BASE = {
    "e": "bookTicker",
    "u": 400900217,
    "E": 1_700_000_000_000,
    "T": 1_700_000_000_001,
    "s": "BTCUSDT",
    "b": "65000.00",
    "B": "1.50",
    "a": "65000.50",
    "A": "2.00",
}


def _gate(*, max_age_ms: int = 4000, max_raw_frame_bytes: int = 65_536) -> FuturesBookTickerGate:
    return FuturesBookTickerGate(
        expected_symbol="BTCUSDT",
        expected_stream="btcusdt@bookTicker",
        max_age_ms=max_age_ms,
        max_raw_frame_bytes=max_raw_frame_bytes,
    )


def _raw(*, updates=None, stream="btcusdt@bookTicker") -> str:
    payload = dict(BASE)
    if updates:
        payload.update(updates)
    return json.dumps({"stream": stream, "data": payload}, separators=(",", ":"))


def _assert_rejected(raw, reason: str, *, received_at_ms=1_700_000_000_100, gate=None) -> None:
    with pytest.raises(FuturesBookTickerGateReject, match=reason):
        (gate or _gate()).admit(raw, received_at_ms=received_at_ms)


def test_valid_combined_frame_is_admitted_with_e_only_freshness() -> None:
    raw = _raw()
    admitted = _gate().admit(raw, received_at_ms=1_700_000_000_100)
    assert admitted.event_time_ms == BASE["E"]
    assert admitted.received_at_ms == 1_700_000_000_100
    assert admitted.age_ms == 100
    assert admitted.freshness_source == "E"
    assert admitted.raw_sha256 == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert admitted.payload["T"] == BASE["T"]
    assert admitted.payload["b"] == "65000.00"
    assert admitted.payload["a"] == "65000.50"


def test_direct_frame_without_combined_wrapper_is_rejected() -> None:
    raw = json.dumps(BASE, separators=(",", ":"))
    _assert_rejected(raw, "combined_frame_required", received_at_ms=1_700_000_000_010)


def test_state_payload_carries_admission_metadata_without_raw_frame() -> None:
    admitted = _gate().admit(_raw(), received_at_ms=1_700_000_000_100)
    payload = admitted.state_payload()
    assert payload["_quote_gate"]["admitted"] is True
    assert payload["_quote_gate"]["freshness_source"] == "E"
    assert payload["_quote_gate"]["age_ms"] == 100
    assert "raw_frame" not in payload


def test_missing_e_is_rejected_even_when_t_exists() -> None:
    payload = dict(BASE)
    payload.pop("E")
    _assert_rejected(json.dumps({"stream": "btcusdt@bookTicker", "data": payload}), "invalid_event_time_E")


def test_future_e_is_rejected() -> None:
    _assert_rejected(_raw(), "future_event_time_E", received_at_ms=BASE["E"] - 1)


def test_stale_e_is_rejected() -> None:
    _assert_rejected(_raw(), "stale_event_time_E", received_at_ms=BASE["E"] + 4001)


def test_age_at_exact_limit_is_admitted() -> None:
    admitted = _gate().admit(_raw(), received_at_ms=BASE["E"] + 4000)
    assert admitted.age_ms == 4000


def test_wrong_stream_is_rejected() -> None:
    _assert_rejected(_raw(stream="ethusdt@bookTicker"), "unexpected_stream")


def test_wrong_event_type_is_rejected() -> None:
    _assert_rejected(_raw(updates={"e": "aggTrade"}), "unexpected_event_type")


def test_wrong_symbol_is_rejected() -> None:
    _assert_rejected(_raw(updates={"s": "ETHUSDT"}), "unexpected_symbol")


def test_crossed_book_is_rejected() -> None:
    _assert_rejected(_raw(updates={"b": "65001", "a": "65000"}), "crossed_book_ask_below_bid")


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("b", "0", "invalid_bid_price_b"),
        ("b", "NaN", "invalid_bid_price_b"),
        ("B", "-1", "invalid_bid_qty_B"),
        ("B", "Infinity", "invalid_bid_qty_B"),
        ("a", "0", "invalid_ask_price_a"),
        ("a", "abc", "invalid_ask_price_a"),
        ("A", "0", "invalid_ask_qty_A"),
        ("A", None, "invalid_ask_qty_A"),
    ],
)
def test_invalid_quote_numbers_are_rejected(field, value, reason) -> None:
    _assert_rejected(_raw(updates={field: value}), reason)


def test_invalid_t_is_rejected_but_t_is_never_freshness_fallback() -> None:
    _assert_rejected(_raw(updates={"T": "1700000000001"}), "invalid_transaction_time_T")


def test_invalid_update_id_is_rejected() -> None:
    _assert_rejected(_raw(updates={"u": -1}), "invalid_update_id_u")


def test_duplicate_json_keys_are_rejected() -> None:
    raw = '{"stream":"btcusdt@bookTicker","data":{"e":"bookTicker","E":1700000000000,"E":1700000000001,"s":"BTCUSDT","b":"1","B":"1","a":"2","A":"1"}}'
    _assert_rejected(raw, "duplicate_json_key:E")


def test_non_standard_json_constant_is_rejected() -> None:
    raw = '{"stream":"btcusdt@bookTicker","data":{"e":"bookTicker","E":1700000000000,"s":"BTCUSDT","b":NaN,"B":"1","a":"2","A":"1"}}'
    _assert_rejected(raw, "non_standard_json_constant:NaN")


def test_non_object_root_is_rejected() -> None:
    _assert_rejected("[]", "raw_frame_not_json_object")


def test_invalid_utf8_bytes_are_rejected() -> None:
    _assert_rejected(b"\xff", "raw_frame_not_utf8")


def test_dict_input_is_rejected_to_preserve_raw_frame_provenance() -> None:
    _assert_rejected({"stream": "btcusdt@bookTicker", "data": BASE}, "raw_frame_must_be_str_or_bytes")


def test_oversized_raw_frame_is_rejected() -> None:
    raw = _raw() + (" " * 10)
    _assert_rejected(raw, "raw_frame_too_large", gate=_gate(max_raw_frame_bytes=len(raw.encode("utf-8")) - 1))


def test_invalid_received_at_ms_is_rejected() -> None:
    _assert_rejected(_raw(), "invalid_received_at_ms", received_at_ms=0)


def test_constructor_rejects_negative_age_limit() -> None:
    with pytest.raises(ValueError, match="max_age_ms"):
        _gate(max_age_ms=-1)

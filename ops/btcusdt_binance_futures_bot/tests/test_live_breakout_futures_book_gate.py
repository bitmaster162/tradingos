import json
from decimal import Decimal
from types import SimpleNamespace

from btcusdt_bot.collector.futures_book_ticker_gate import FuturesBookTickerGate
from btcusdt_bot.live_breakout import LiveBreakoutRunner
from btcusdt_bot.state.store import StateStore


EVENT_TIME = 1_700_000_000_000


def _runner(*, max_staleness_ms=4000, max_spread_bps=Decimal("50")) -> LiveBreakoutRunner:
    runner = LiveBreakoutRunner.__new__(LiveBreakoutRunner)
    runner.config = SimpleNamespace(symbol="BTCUSDT", stale_data_limit_ms=4000)
    runner.live_config = SimpleNamespace(
        max_book_ticker_staleness_ms=max_staleness_ms,
        max_book_spread_bps=max_spread_bps,
    )
    runner.store = StateStore()
    runner.status = SimpleNamespace(
        last_book_bid="",
        last_book_ask="",
        last_book_spread_bps="",
        last_book_age_ms=0,
    )
    return runner


def _admitted(*, event_time_ms=EVENT_TIME, received_at_ms=None, bid="65000", ask="65000.5") -> dict:
    if received_at_ms is None:
        received_at_ms = event_time_ms + 100
    payload = {
        "e": "bookTicker",
        "E": event_time_ms,
        "T": event_time_ms + 1,
        "u": 123,
        "s": "BTCUSDT",
        "b": bid,
        "B": "1.5",
        "a": ask,
        "A": "2.0",
    }
    raw = json.dumps({"stream": "btcusdt@bookTicker", "data": payload}, separators=(",", ":"))
    gate = FuturesBookTickerGate(
        expected_symbol="BTCUSDT",
        expected_stream="btcusdt@bookTicker",
        max_age_ms=4000,
    )
    return gate.admit(raw, received_at_ms=received_at_ms).state_payload()


def test_valid_admitted_book_is_available_to_gate_status_and_fill_snapshot() -> None:
    runner = _runner()
    runner.store.patch_book_ticker(_admitted())
    assert runner._book_gate_reason(event_time_ms=EVENT_TIME + 200) == ""
    runner._apply_book_status(event_time_ms=EVENT_TIME + 200)
    assert runner.status.last_book_age_ms == 200
    assert runner.status.last_book_bid == "65000"
    assert runner.status.last_book_ask == "65000.5"
    snapshot = runner._current_book_snapshot()
    assert snapshot is not None
    assert snapshot.event_time_ms == EVENT_TIME
    assert snapshot.bid_price == Decimal("65000")
    assert snapshot.ask_price == Decimal("65000.5")


def test_unadmitted_book_is_rejected_and_not_exposed_to_fill_model() -> None:
    runner = _runner()
    runner.store.patch_book_ticker({
        "e": "bookTicker", "E": EVENT_TIME, "T": EVENT_TIME + 1, "s": "BTCUSDT",
        "b": "65000", "B": "1", "a": "65000.5", "A": "1",
    })
    assert runner._book_gate_reason(event_time_ms=EVENT_TIME + 100) == "unadmitted_book_ticker"
    assert runner._current_book_snapshot() is None


def test_missing_e_is_invalid_even_when_t_is_present() -> None:
    runner = _runner()
    payload = _admitted()
    payload.pop("E")
    assert payload["T"] > 0
    runner.store.patch_book_ticker(payload)
    assert runner._book_gate_reason(event_time_ms=EVENT_TIME + 100) == "invalid_book_ticker"
    assert runner._current_book_snapshot() is None


def test_future_book_is_fail_closed_and_status_keeps_negative_age() -> None:
    runner = _runner()
    runner.store.patch_book_ticker(_admitted())
    assert runner._book_gate_reason(event_time_ms=EVENT_TIME - 1) == "future_book_ticker"
    runner._apply_book_status(event_time_ms=EVENT_TIME - 1)
    assert runner.status.last_book_age_ms == -1


def test_stale_book_is_fail_closed() -> None:
    runner = _runner(max_staleness_ms=4000)
    runner.store.patch_book_ticker(_admitted())
    assert runner._book_gate_reason(event_time_ms=EVENT_TIME + 4001) == "stale_book_ticker"


def test_receipt_age_metadata_mismatch_is_invalid() -> None:
    runner = _runner()
    payload = _admitted()
    payload["_quote_gate"]["age_ms"] += 1
    runner.store.patch_book_ticker(payload)
    assert runner._book_gate_reason(event_time_ms=EVENT_TIME + 200) == "invalid_book_ticker"


def test_bad_raw_hash_metadata_is_invalid() -> None:
    runner = _runner()
    payload = _admitted()
    payload["_quote_gate"]["raw_sha256"] = "x" * 64
    runner.store.patch_book_ticker(payload)
    assert runner._book_gate_reason(event_time_ms=EVENT_TIME + 200) == "invalid_book_ticker"


def test_crossed_book_after_store_mutation_is_invalid() -> None:
    runner = _runner()
    payload = _admitted()
    payload["b"] = "66000"
    runner.store.patch_book_ticker(payload)
    assert runner._book_gate_reason(event_time_ms=EVENT_TIME + 200) == "invalid_book_ticker"


def test_spread_gate_uses_only_valid_admitted_book() -> None:
    runner = _runner(max_spread_bps=Decimal("50"))
    runner.store.patch_book_ticker(_admitted(bid="100", ask="101"))
    assert runner._book_gate_reason(event_time_ms=EVENT_TIME + 200) == "book_spread_too_wide"


def test_receipt_older_than_bot_stale_limit_is_invalid_even_if_metadata_is_self_consistent() -> None:
    runner = _runner()
    payload = _admitted()
    payload["_quote_gate"]["received_at_ms"] = EVENT_TIME + 5000
    payload["_quote_gate"]["age_ms"] = 5000
    runner.store.patch_book_ticker(payload)
    assert runner._book_gate_reason(event_time_ms=EVENT_TIME + 200) == "invalid_book_ticker"


def test_no_book_remains_optional_and_does_not_invent_a_quote() -> None:
    runner = _runner()
    assert runner._book_gate_reason(event_time_ms=EVENT_TIME) == ""
    assert runner._current_book_snapshot() is None


def test_semantically_valid_price_mutation_breaks_quote_binding() -> None:
    runner = _runner()
    payload = _admitted()
    payload["a"] = "65000.6"
    runner.store.patch_book_ticker(payload)
    assert runner._book_gate_reason(event_time_ms=EVENT_TIME + 200) == "invalid_book_ticker"
    assert runner._current_book_snapshot() is None


def test_quote_binding_digest_mutation_is_rejected() -> None:
    runner = _runner()
    payload = _admitted()
    payload["_quote_gate"]["quote_binding_sha256"] = "0" * 64
    runner.store.patch_book_ticker(payload)
    assert runner._book_gate_reason(event_time_ms=EVENT_TIME + 200) == "invalid_book_ticker"
    assert runner._current_book_snapshot() is None

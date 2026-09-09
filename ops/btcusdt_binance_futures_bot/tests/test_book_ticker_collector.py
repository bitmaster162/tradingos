import json

from btcusdt_bot.collector.book_ticker import BookTickerCollector
from btcusdt_bot.config import BotConfig
from btcusdt_bot.state.store import StateStore
from btcusdt_bot.storage.jsonl import JSONLWriter


def _build_config(tmp_path) -> BotConfig:
    return BotConfig(
        env="demo",
        symbol="BTCUSDT",
        rest_base_url="https://demo-fapi.binance.com",
        ws_public_base_url="wss://fstream.binancefuture.com",
        ws_market_base_url="wss://fstream.binancefuture.com",
        ws_private_base_url="wss://fstream.binancefuture.com",
        api_key="",
        api_secret="",
        recv_window_ms=5000,
        timeout_s=10.0,
        position_mode="ONE_WAY",
        margin_mode="ISOLATED",
        max_leverage=3,
        max_position_notional_usdt=500.0,
        max_daily_loss_usdt=50.0,
        max_normal_open_orders=8,
        max_algo_open_orders=20,
        stale_data_limit_ms=4000,
        countdown_cancel_ms=120000,
        heartbeat_interval_ms=30000,
        user_stream_keepalive_ms=1800000,
        reconnect_initial_backoff_ms=1000,
        reconnect_max_backoff_ms=30000,
        kline_intervals=("1m",),
        private_events=("ORDER_TRADE_UPDATE",),
        enable_contract_info_stream=True,
        enable_force_order_stream=False,
        enable_countdown_heartbeat=False,
        state_flush_every_events=1,
        data_dir=tmp_path,
        log_level="INFO",
    )


def _raw(*, event_time_ms=1_700_000_000_000, updates=None) -> str:
    payload = {
        "e": "bookTicker",
        "E": event_time_ms,
        "T": event_time_ms + 1,
        "u": 123,
        "s": "BTCUSDT",
        "b": "65000.0",
        "B": "1.5",
        "a": "65000.5",
        "A": "2.0",
    }
    if updates:
        payload.update(updates)
    return json.dumps({"stream": "btcusdt@bookTicker", "data": payload}, separators=(",", ":"))


def test_book_ticker_collector_admits_updates_store_and_writes_jsonl(tmp_path) -> None:
    config = _build_config(tmp_path)
    store = StateStore()

    with JSONLWriter(tmp_path) as writer:
        collector = BookTickerCollector(config, writer=writer, store=store)
        admitted = collector.handle_raw_message(_raw(), received_at_ms=1_700_000_000_100)

        assert admitted is True
        assert collector.status.messages_received == 1
        assert collector.status.messages_rejected == 0
        assert collector.status.last_event_time_ms == 1_700_000_000_000
        assert collector.status.last_quote_age_ms == 100
        assert collector.status.freshness_source == "E"
        assert store.state.latest_book_ticker["b"] == "65000.0"
        assert store.state.latest_book_ticker["a"] == "65000.5"
        assert store.state.latest_book_ticker["_quote_gate"]["freshness_source"] == "E"
        assert store.state.latest_book_ticker["_quote_gate"]["age_ms"] == 100
        assert collector.status.last_written_path.endswith("btcusdt_bookTicker.jsonl")


def test_collector_rejects_missing_e_without_store_or_write(tmp_path) -> None:
    config = _build_config(tmp_path)
    store = StateStore()
    raw_obj = json.loads(_raw())
    raw_obj["data"].pop("E")

    with JSONLWriter(tmp_path) as writer:
        collector = BookTickerCollector(config, writer=writer, store=store)
        admitted = collector.handle_raw_message(json.dumps(raw_obj), received_at_ms=1_700_000_000_100)

        assert admitted is False
        assert collector.status.messages_received == 0
        assert collector.status.messages_rejected == 1
        assert collector.status.last_rejection_reason == "invalid_event_time_E"
        assert store.state.latest_book_ticker == {}
        assert collector.status.last_written_path == ""


def test_collector_rejects_future_event_without_masking_age(tmp_path) -> None:
    config = _build_config(tmp_path)
    store = StateStore()

    with JSONLWriter(tmp_path) as writer:
        collector = BookTickerCollector(config, writer=writer, store=store)
        admitted = collector.handle_raw_message(_raw(), received_at_ms=1_699_999_999_999)

        assert admitted is False
        assert collector.status.messages_received == 0
        assert collector.status.messages_rejected == 1
        assert collector.status.last_rejection_reason == "future_event_time_E"
        assert store.state.latest_book_ticker == {}


def test_collector_uses_configured_staleness_limit(tmp_path) -> None:
    config = _build_config(tmp_path)
    with JSONLWriter(tmp_path) as writer:
        collector = BookTickerCollector(config, writer=writer)
        assert collector.handle_raw_message(_raw(), received_at_ms=1_700_000_004_000) is True
        assert collector.handle_raw_message(_raw(), received_at_ms=1_700_000_004_001) is False
        assert collector.status.messages_received == 1
        assert collector.status.messages_rejected == 1
        assert collector.status.last_rejection_reason == "stale_event_time_E"


def test_manifest_declares_e_only_fail_closed_quote_gate(tmp_path) -> None:
    config = _build_config(tmp_path)
    with JSONLWriter(tmp_path) as writer:
        manifest = BookTickerCollector(config, writer=writer).manifest()
    gate = manifest["quote_gate"]
    assert gate["kind"] == "binance_usdm_futures_book_ticker"
    assert gate["freshness_source"] == "E"
    assert gate["max_age_ms"] == 4000
    assert gate["fallback_timestamp_allowed"] is False

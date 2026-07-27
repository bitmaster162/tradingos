from btcusdt_bot.collector.book_ticker import BookTickerCollector
from btcusdt_bot.config import BotConfig
from btcusdt_bot.state.store import StateStore
from btcusdt_bot.storage.jsonl import JSONLWriter
from btcusdt_bot.ws.messages import decode_ws_message


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


def test_book_ticker_collector_updates_store_and_writes_jsonl(tmp_path) -> None:
    config = _build_config(tmp_path)
    store = StateStore()

    with JSONLWriter(tmp_path) as writer:
        collector = BookTickerCollector(config, writer=writer, store=store)
        message = decode_ws_message(
            {
                "stream": "btcusdt@bookTicker",
                "data": {
                    "e": "bookTicker",
                    "E": 1700000000000,
                    "T": 1700000000000,
                    "s": "BTCUSDT",
                    "b": "65000.0",
                    "B": "1.5",
                    "a": "65000.5",
                    "A": "2.0",
                },
            }
        )
        collector.handle_message(message)

        assert collector.status.messages_received == 1
        assert store.state.latest_book_ticker["b"] == "65000.0"
        assert store.state.latest_book_ticker["a"] == "65000.5"
        assert collector.status.last_written_path.endswith("btcusdt_bookTicker.jsonl")

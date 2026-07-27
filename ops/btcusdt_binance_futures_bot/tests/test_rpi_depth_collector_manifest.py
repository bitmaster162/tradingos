from btcusdt_bot.collector.depth_book import RPIDepthBookCollector
from btcusdt_bot.config import BotConfig
from btcusdt_bot.state.store import StateStore
from btcusdt_bot.storage.jsonl import JSONLWriter


class _DummyClient:
    pass



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



def test_rpi_depth_collector_manifest_exposes_rpi_stream(tmp_path) -> None:
    config = _build_config(tmp_path)
    with JSONLWriter(tmp_path) as writer:
        collector = RPIDepthBookCollector(config, client=_DummyClient(), writer=writer, store=StateStore(), depth_levels=20)
        manifest = collector.manifest()

    assert manifest["routing"] == "public"
    assert manifest["streams"] == ["btcusdt@rpiDepth@500ms"]
    assert "RPI" in manifest["notes"][0]

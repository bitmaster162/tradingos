from decimal import Decimal

from btcusdt_bot.config import BotConfig
from btcusdt_bot.live_breakout import LiveBreakoutConfig, LiveBreakoutRunner
from btcusdt_bot.state.store import StateStore
from btcusdt_bot.storage.jsonl import JSONLWriter


class _DummyGateway:
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



def test_live_breakout_prefers_rpi_depth_when_available(tmp_path) -> None:
    config = _build_config(tmp_path)
    store = StateStore()
    store.patch_depth_snapshot(
        {
            "e": "localDepthSnapshot",
            "E": 1_700_000_000_000,
            "T": 1_700_000_000_000,
            "s": "BTCUSDT",
            "u": 100,
            "levels": 2,
            "imbalance": "-0.40",
            "bids": [["100.0", "1.0"]],
            "asks": [["100.5", "4.0"]],
        }
    )
    store.patch_rpi_depth_snapshot(
        {
            "e": "localRpiDepthSnapshot",
            "E": 1_700_000_000_050,
            "T": 1_700_000_000_050,
            "s": "BTCUSDT",
            "u": 110,
            "levels": 2,
            "imbalance": "0.30",
            "bids": [["100.0", "3.0"]],
            "asks": [["100.5", "1.0"]],
        }
    )

    with JSONLWriter(tmp_path) as writer:
        runner = LiveBreakoutRunner(
            config,
            client=object(),
            store=store,
            writer=writer,
            gateway=_DummyGateway(),
            live_config=LiveBreakoutConfig(
                with_private_consumer=False,
                with_reconcile_daemon=False,
                with_book_ticker_collector=False,
                with_depth_book_collector=False,
                with_rpi_depth_book_collector=False,
                with_crowding_collector=False,
                use_rpi_depth_if_available=True,
                send_orders=False,
            ),
        )

        depth = runner._current_depth_snapshot()
        assert depth is not None
        assert depth.best_bid_price == Decimal("100.0")
        assert depth.bids[0].qty == Decimal("3.0")
        assert depth.asks[0].qty == Decimal("1.0")

        runner._apply_depth_status(event_time_ms=1_700_000_000_100)
        assert runner.status.last_depth_source == "rpi"
        assert runner.status.last_rpi_depth_levels == 2
        assert runner.status.last_rpi_depth_age_ms == 50

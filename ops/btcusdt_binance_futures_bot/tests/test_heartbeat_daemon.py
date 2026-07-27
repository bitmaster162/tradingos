import asyncio

from btcusdt_bot.config import BotConfig
from btcusdt_bot.execution.gateway import GatewayResult
from btcusdt_bot.heartbeat_daemon import CountdownHeartbeatDaemon
from btcusdt_bot.storage.jsonl import JSONLWriter


class FakeGateway:
    def __init__(self) -> None:
        self.calls = []

    def refresh_countdown(self, *, dry_run=True):
        self.calls.append(dry_run)
        return GatewayResult(
            payload={"symbol": "BTCUSDT", "countdownTime": 120000},
            validation=None,
            sent=not dry_run,
        )


def _build_config(tmp_path):
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
        enable_countdown_heartbeat=True,
        state_flush_every_events=1,
        data_dir=tmp_path,
        log_level="INFO",
    )


def test_countdown_heartbeat_daemon_dry_run_writes_status(tmp_path) -> None:
    config = _build_config(tmp_path)
    gateway = FakeGateway()
    with JSONLWriter(tmp_path) as writer:
        daemon = CountdownHeartbeatDaemon(config, gateway=gateway, writer=writer)
        status = asyncio.run(daemon.run(interval_seconds=0.01, max_iterations=2, send=False))

    assert status.iterations == 2
    assert status.sent == 0
    assert gateway.calls == [True, True]
    assert (tmp_path / "heartbeat" / "latest.json").exists()


def test_countdown_heartbeat_daemon_send_path_counts_sent_iterations(tmp_path) -> None:
    config = _build_config(tmp_path)
    gateway = FakeGateway()
    with JSONLWriter(tmp_path) as writer:
        daemon = CountdownHeartbeatDaemon(config, gateway=gateway, writer=writer)
        status = asyncio.run(daemon.run(interval_seconds=0.01, max_iterations=1, send=True))

    assert status.iterations == 1
    assert status.sent == 1
    assert gateway.calls == [False]

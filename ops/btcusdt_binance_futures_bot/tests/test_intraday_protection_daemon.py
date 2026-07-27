import asyncio

from btcusdt_bot.config import BotConfig
from btcusdt_bot.domain.models import APICallResult
from btcusdt_bot.intraday_protection_daemon import IntradayProtectionDaemon, IntradayProtectionDaemonConfig
from btcusdt_bot.monitoring.intraday_protection import IntradayProtectionThresholds
from btcusdt_bot.storage.jsonl import JSONLWriter


class FakeClient:
    def api_trading_status(self, symbol: str) -> APICallResult:
        return APICallResult(
            data={
                "indicators": {
                    symbol: [
                        {
                            "isLocked": False,
                            "plannedRecoverTime": 0,
                            "indicator": "UFR",
                            "value": "0.92",
                            "triggerValue": "0.995",
                        }
                    ]
                }
            },
            headers={},
        )

    def adl_quantile(self, symbol: str) -> APICallResult:
        return APICallResult(
            data=[{"symbol": symbol, "adlQuantile": {"BOTH": 3}}],
            headers={},
        )


def _build_config(tmp_path):
    return BotConfig(
        env="demo",
        symbol="BTCUSDT",
        rest_base_url="https://demo-fapi.binance.com",
        ws_public_base_url="wss://fstream.binancefuture.com",
        ws_market_base_url="wss://fstream.binancefuture.com",
        ws_private_base_url="wss://fstream.binancefuture.com",
        api_key="k",
        api_secret="s",
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


def test_intraday_protection_daemon_writes_guard_and_report(tmp_path) -> None:
    config = _build_config(tmp_path)
    daemon_config = IntradayProtectionDaemonConfig(
        thresholds=IntradayProtectionThresholds(),
        include_adl=True,
        position_mode="ONE_WAY",
    )
    with JSONLWriter(tmp_path) as writer:
        daemon = IntradayProtectionDaemon(
            config,
            client=FakeClient(),
            writer=writer,
            daemon_config=daemon_config,
        )
        status = asyncio.run(daemon.run(interval_seconds=0.01, max_iterations=1))

    assert status.iterations == 1
    assert status.decisions_written == 1
    assert status.last_action == "reduce_size"
    assert (tmp_path / "live" / "guards" / "latest_intraday_protection.json").exists()

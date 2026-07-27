import asyncio
import json
from decimal import Decimal

from btcusdt_bot.config import BotConfig
from btcusdt_bot.monitoring.pnl_protection import PnLProtectionThresholds
from btcusdt_bot.pnl_protection_daemon import PnLProtectionDaemon, PnLProtectionDaemonConfig
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
        enable_contract_info_stream=False,
        enable_force_order_stream=False,
        enable_countdown_heartbeat=False,
        state_flush_every_events=1,
        data_dir=tmp_path,
        log_level="INFO",
    )


def test_pnl_protection_daemon_uses_bootstrap_anchor_and_writes_guard(tmp_path) -> None:
    config = _build_config(tmp_path)
    bootstrap_state = {
        "account": {
            "balances": {"USDT": "1000"},
            "positions": {},
            "last_event_time_ms": 1_700_000_000_000,
        },
        "last_bootstrap_at_ms": 1_700_000_000_000,
    }
    runtime_state = {
        "account": {
            "balances": {"USDT": "985"},
            "positions": {"BTCUSDT/BOTH": {"amount": "0.01", "unrealized_pnl": "0"}},
            "last_event_time_ms": 1_700_000_100_000,
        }
    }
    bootstrap_path = tmp_path / "live" / "bootstrap_state.json"
    bootstrap_path.parent.mkdir(parents=True, exist_ok=True)
    bootstrap_path.write_text(json.dumps(bootstrap_state), encoding="utf-8")
    runtime_path = tmp_path / "private" / "state" / "latest.json"
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text(json.dumps(runtime_state), encoding="utf-8")

    daemon_config = PnLProtectionDaemonConfig(
        runtime_state_path=runtime_path,
        bootstrap_state_path=bootstrap_path,
        anchor_path=tmp_path / "live" / "guards" / "latest_pnl_anchor.json",
        thresholds=PnLProtectionThresholds(
            max_session_loss_fraction_reduce=Decimal("0.010"),
            max_session_loss_fraction_observe=Decimal("0.020"),
        ),
    )

    with JSONLWriter(tmp_path) as writer:
        daemon = PnLProtectionDaemon(config, writer=writer, daemon_config=daemon_config)
        status = asyncio.run(daemon.run(interval_seconds=0.01, max_iterations=1))

    guard_payload = json.loads((tmp_path / "live" / "guards" / "latest_pnl_protection.json").read_text(encoding="utf-8"))
    anchor_payload = json.loads((tmp_path / "live" / "guards" / "latest_pnl_anchor.json").read_text(encoding="utf-8"))

    assert status.decisions_written == 1
    assert status.reduce_size_decisions == 1
    assert guard_payload["action"] == "reduce_size"
    assert guard_payload["session_loss_usdt"] == "15"
    assert anchor_payload["baseline_equity_usdt"] == "1000"
    assert anchor_payload["latest_equity_usdt"] == "985"

import asyncio
import json
from decimal import Decimal

from btcusdt_bot.combined_protection_daemon import CombinedProtectionDaemon, CombinedProtectionDaemonConfig
from btcusdt_bot.config import BotConfig
from btcusdt_bot.monitoring.combined_protection import CombinedProtectionThresholds
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


def test_combined_protection_daemon_writes_unified_guard(tmp_path) -> None:
    config = _build_config(tmp_path)
    guards_dir = tmp_path / "live" / "guards"
    guards_dir.mkdir(parents=True, exist_ok=True)
    (guards_dir / "latest_execution_drift.json").write_text(
        json.dumps({"action": "reduce_size", "size_multiplier": "0.75", "score": "1"}),
        encoding="utf-8",
    )
    (guards_dir / "latest_pnl_protection.json").write_text(
        json.dumps({"action": "reduce_size", "size_multiplier": "0.50", "score": "1"}),
        encoding="utf-8",
    )

    daemon_config = CombinedProtectionDaemonConfig(
        execution_drift_guard_path=guards_dir / "latest_execution_drift.json",
        intraday_protection_guard_path=guards_dir / "latest_intraday_protection.json",
        pnl_protection_guard_path=guards_dir / "latest_pnl_protection.json",
        trade_reconciliation_guard_path=guards_dir / "latest_trade_reconciliation.json",
        state_path=guards_dir / "latest_combined_protection_state.json",
        thresholds=CombinedProtectionThresholds(observe_cooldown_seconds=60),
    )

    with JSONLWriter(tmp_path) as writer:
        daemon = CombinedProtectionDaemon(config, writer=writer, daemon_config=daemon_config)
        status = asyncio.run(daemon.run(interval_seconds=0.01, max_iterations=1))

    payload = json.loads((guards_dir / "latest_combined_protection.json").read_text(encoding="utf-8"))
    state_payload = json.loads((guards_dir / "latest_combined_protection_state.json").read_text(encoding="utf-8"))

    assert status.decisions_written == 1
    assert status.observe_only_decisions == 1
    assert payload["action"] == "observe_only"
    assert payload["co_degrade_triggered"] is True
    assert payload["source_actions"]["execution_drift"] == "reduce_size"
    assert payload["source_actions"]["pnl_protection"] == "reduce_size"
    assert Decimal(state_payload["last_size_multiplier"]) == Decimal("0")

import asyncio
import json
from pathlib import Path

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



def test_combined_protection_daemon_reads_economics_regime_guard(tmp_path) -> None:
    config = _build_config(tmp_path)
    live_guards = tmp_path / "live" / "guards"
    live_guards.mkdir(parents=True, exist_ok=True)
    (live_guards / "execution.json").write_text(json.dumps({"action": "reduce_size", "size_multiplier": "0.8"}), encoding="utf-8")
    (live_guards / "economics.json").write_text(json.dumps({"action": "reduce_size", "size_multiplier": "0.6"}), encoding="utf-8")

    daemon_config = CombinedProtectionDaemonConfig(
        execution_drift_guard_path=live_guards / "execution.json",
        intraday_protection_guard_path=live_guards / "missing_intraday.json",
        pnl_protection_guard_path=live_guards / "missing_pnl.json",
        trade_reconciliation_guard_path=live_guards / "missing_trade_recon.json",
        session_truth_guard_path=live_guards / "missing_session_truth.json",
        session_truth_trend_guard_path=live_guards / "missing_session_truth_trend.json",
        economics_regime_guard_path=live_guards / "economics.json",
        state_path=Path("live/guards/state.json"),
        thresholds=CombinedProtectionThresholds(),
    )

    with JSONLWriter(tmp_path) as writer:
        daemon = CombinedProtectionDaemon(config, writer=writer, daemon_config=daemon_config)
        status = asyncio.run(daemon.run(interval_seconds=0.01, max_iterations=1))

    payload = json.loads((tmp_path / "live" / "guards" / "latest_combined_protection.json").read_text(encoding="utf-8"))
    assert status.decisions_written == 1
    assert payload["action"] == "observe_only"
    assert payload["source_actions"]["economics_regime"] == "reduce_size"

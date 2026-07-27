import asyncio
import json
from pathlib import Path

from btcusdt_bot.config import BotConfig
from btcusdt_bot.economics_regime_daemon import EconomicsRegimeDaemon, EconomicsRegimeDaemonConfig
from btcusdt_bot.monitoring.economics_regime import EconomicsRegimeThresholds
from btcusdt_bot.storage.jsonl import JSONLWriter



def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")



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



def test_economics_regime_daemon_writes_guard_and_dashboard(tmp_path) -> None:
    for date, pnl, bps in [
        ("2026-04-05", "3.0", "0.30"),
        ("2026-04-06", "-4.0", "-0.40"),
        ("2026-04-07", "-7.0", "-0.70"),
    ]:
        _append_jsonl(
            tmp_path / "reports" / date / "btcusdt_session_truth_report.jsonl",
            {"report": {
                "active_bucket_count": 4,
                "exchange_trade_count": 10,
                "exchange_order_count": 8,
                "exchange_quote_qty_usdt": "10000",
                "net_realized_pnl_usdt": pnl,
                "net_realized_bps": bps,
                "maker_ratio": "0.20",
                "commission_bps": "7.0",
                "funding_bps": "-0.6",
                "negative_bucket_ratio": "0.60",
                "recent_bucket_net_realized_bps": bps,
                "recent_two_bucket_net_realized_bps": bps,
                "cumulative_drawdown_usdt": "0",
            }},
        )

    config = _build_config(tmp_path)
    daemon_config = EconomicsRegimeDaemonConfig(
        lookback_days=3,
        end_date="2026-04-07",
        thresholds=EconomicsRegimeThresholds(),
    )
    with JSONLWriter(tmp_path) as writer:
        daemon = EconomicsRegimeDaemon(config, writer=writer, daemon_config=daemon_config)
        status = asyncio.run(daemon.run(interval_seconds=0.01, max_iterations=1))

    guard = json.loads((tmp_path / "live" / "guards" / "latest_economics_regime.json").read_text(encoding="utf-8"))
    dashboard = json.loads((tmp_path / "live" / "reports" / "latest_economics_dashboard.json").read_text(encoding="utf-8"))

    assert status.decisions_written == 1
    assert guard["action"] in {"reduce_size", "observe_only"}
    assert guard["active_day_count"] == 3
    assert dashboard["dashboard"]["available_day_count"] == 3

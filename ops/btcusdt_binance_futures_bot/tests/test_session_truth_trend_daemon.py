import asyncio
import json

from btcusdt_bot.config import BotConfig
from btcusdt_bot.monitoring.session_truth_trend import SessionTruthTrendThresholds
from btcusdt_bot.session_truth_trend_daemon import SessionTruthTrendDaemon, SessionTruthTrendDaemonConfig
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



def test_session_truth_trend_daemon_reads_report_and_writes_guard(tmp_path) -> None:
    config = _build_config(tmp_path)
    report_path = tmp_path / "live" / "reports" / "latest_session_truth_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps({
            "report": {
                "compared_at_ms": 1700000000000,
                "lookback_start_ms": 1699996400000,
                "lookback_end_ms": 1700000000000,
                "bucket_ms": 3600000,
                "bucket_count": 4,
                "active_bucket_count": 4,
                "negative_bucket_count": 3,
                "negative_bucket_ratio": "0.75",
                "trailing_negative_bucket_streak": 3,
                "recent_bucket_net_realized_bps": "-4.0",
                "recent_two_bucket_net_realized_bps": "-3.5",
                "recent_bucket_maker_ratio": "0.10",
                "worst_bucket_net_realized_bps": "-9.0",
                "cumulative_drawdown_usdt": "16.0",
                "buckets": [],
            }
        }),
        encoding="utf-8",
    )

    daemon_config = SessionTruthTrendDaemonConfig(
        report_path=report_path,
        thresholds=SessionTruthTrendThresholds(),
    )
    with JSONLWriter(tmp_path) as writer:
        daemon = SessionTruthTrendDaemon(config, writer=writer, daemon_config=daemon_config)
        status = asyncio.run(daemon.run(interval_seconds=0.01, max_iterations=1))

    payload = json.loads((tmp_path / "live" / "guards" / "latest_session_truth_trend.json").read_text(encoding="utf-8"))
    assert status.decisions_written == 1
    assert payload["action"] == "observe_only"
    assert payload["negative_bucket_ratio"] == "0.75"

import asyncio
import json
from types import SimpleNamespace

from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.storage.jsonl import JSONLWriter
from btcusdt_bot.session_truth_daemon import SessionTruthDaemon, SessionTruthDaemonConfig
from btcusdt_bot.monitoring.session_truth import SessionTruthThresholds


class FakeClient:
    def __init__(self) -> None:
        self.user_trade_calls = []
        self.income_calls = []

    def user_trades(self, symbol, *, start_time=None, end_time=None, limit=None):
        self.user_trade_calls.append((symbol, start_time, end_time, limit))
        return SimpleNamespace(data=[
            {"id": 101, "orderId": 11, "quoteQty": "2000", "realizedPnl": "3.0", "commission": "0.5", "maker": True, "time": start_time},
            {"id": 102, "orderId": 12, "quoteQty": "2000", "realizedPnl": "2.0", "commission": "0.5", "maker": True, "time": end_time},
            {"id": 103, "orderId": 13, "quoteQty": "2000", "realizedPnl": "1.0", "commission": "0.5", "maker": False, "time": end_time},
        ])

    def income_history(self, *, symbol=None, start_time=None, end_time=None, page=None, limit=None):
        self.income_calls.append((symbol, start_time, end_time, page, limit))
        return SimpleNamespace(data=[
            {"incomeType": "FUNDING_FEE", "income": "-0.2", "time": 1700000000500, "tranId": "fund-1"},
        ])



def _build_config(tmp_path) -> BotConfig:
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
        enable_contract_info_stream=False,
        enable_force_order_stream=False,
        enable_countdown_heartbeat=False,
        state_flush_every_events=1,
        data_dir=tmp_path,
        log_level="INFO",
    )



def test_session_truth_daemon_writes_guard_and_uses_session_window(tmp_path) -> None:
    config = _build_config(tmp_path)
    session_state_path = tmp_path / "live" / "status" / "latest.json"
    session_state_path.parent.mkdir(parents=True, exist_ok=True)
    session_started_at_ms = now_ms() - 10_000
    session_state_path.write_text(json.dumps({"session_started_at_ms": session_started_at_ms}), encoding="utf-8")

    daemon_config = SessionTruthDaemonConfig(
        lookback_ms=60 * 60 * 1000,
        thresholds=SessionTruthThresholds(min_exchange_trade_count=3, min_quote_qty_usdt=0),
        session_state_path=session_state_path,
        authoritative_archive_root=None,
        prefer_authoritative_archive=False,
    )
    client = FakeClient()

    with JSONLWriter(tmp_path) as writer:
        daemon = SessionTruthDaemon(config, client=client, writer=writer, daemon_config=daemon_config)
        status = asyncio.run(daemon.run(interval_seconds=0.01, max_iterations=1))

    payload = json.loads((tmp_path / "live" / "guards" / "latest_session_truth.json").read_text(encoding="utf-8"))
    latest_report = json.loads((tmp_path / "live" / "reports" / "latest_session_truth.json").read_text(encoding="utf-8"))
    assert status.decisions_written == 1
    assert payload["action"] == "trade"
    assert payload["window_mode"] == "session"
    assert payload["session_started_at_ms"] == session_started_at_ms
    assert latest_report["decision"]["exchange_trade_count"] == 3
    assert latest_report["source"]["source_mode"] == "live_only"
    assert client.user_trade_calls[0][1] == session_started_at_ms

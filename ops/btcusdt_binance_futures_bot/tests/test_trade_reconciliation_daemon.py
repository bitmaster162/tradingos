import asyncio
import json
from types import SimpleNamespace

from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.monitoring.trade_reconciliation import TradeReconciliationThresholds
from btcusdt_bot.storage.jsonl import JSONLWriter
from btcusdt_bot.trade_reconciliation_daemon import TradeReconciliationDaemon, TradeReconciliationDaemonConfig


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


class FakeClient:
    def __init__(self) -> None:
        self.user_trade_calls = []
        self.income_calls = []

    def user_trades(self, symbol, *, start_time=None, end_time=None, limit=None):
        self.user_trade_calls.append((symbol, start_time, end_time, limit))
        return SimpleNamespace(data=[{"id": 101, "orderId": 11, "quoteQty": "125.0", "realizedPnl": "1.25", "commission": "0.05"}])

    def income_history(self, *, symbol=None, start_time=None, end_time=None, page=None, limit=None):
        self.income_calls.append((symbol, start_time, end_time, page, limit))
        return SimpleNamespace(data=[
            {"incomeType": "REALIZED_PNL", "income": "1.25", "tradeId": "101"},
            {"incomeType": "COMMISSION", "income": "-0.05", "tradeId": "101"},
        ])


class PartitioningClient:
    def __init__(self) -> None:
        self.calls = []

    def user_trades(self, symbol, *, start_time=None, end_time=None, limit=None):
        self.calls.append((start_time, end_time, limit))
        span = int(end_time or 0) - int(start_time or 0)
        if span > 1_000:
            return SimpleNamespace(data=[{"id": idx, "orderId": idx, "time": start_time + idx} for idx in range(1, 1001)])
        base = int(start_time or 0)
        return SimpleNamespace(data=[
            {"id": base + 1, "orderId": base + 1, "time": start_time, "quoteQty": "1", "realizedPnl": "0", "commission": "0"},
            {"id": base + 2, "orderId": base + 2, "time": end_time, "quoteQty": "1", "realizedPnl": "0", "commission": "0"},
        ])

    def income_history(self, *, symbol=None, start_time=None, end_time=None, page=None, limit=None):
        return SimpleNamespace(data=[])



def test_trade_reconciliation_daemon_writes_guard_and_uses_session_window(tmp_path) -> None:
    config = _build_config(tmp_path)
    trade_time_ms = now_ms() - 5_000
    runtime_path = tmp_path / "private" / "state" / "latest.json"
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text(
        json.dumps({
            "trade_fills": {
                "101": {
                    "symbol": "BTCUSDT",
                    "trade_id": 101,
                    "order_id": 11,
                    "trade_time_ms": trade_time_ms,
                    "quote_qty": "125.0",
                    "realized_pnl": "1.25",
                    "commission": "0.05",
                }
            }
        }),
        encoding="utf-8",
    )
    session_state_path = tmp_path / "live" / "status" / "latest.json"
    session_state_path.parent.mkdir(parents=True, exist_ok=True)
    session_state_path.write_text(json.dumps({"session_started_at_ms": trade_time_ms - 10_000}), encoding="utf-8")

    daemon_config = TradeReconciliationDaemonConfig(
        runtime_state_path=runtime_path,
        lookback_ms=60 * 60 * 1000,
        thresholds=TradeReconciliationThresholds(),
        session_state_path=session_state_path,
    )

    client = FakeClient()
    with JSONLWriter(tmp_path) as writer:
        daemon = TradeReconciliationDaemon(
            config,
            client=client,
            writer=writer,
            daemon_config=daemon_config,
        )
        status = asyncio.run(daemon.run(interval_seconds=0.01, max_iterations=1))

    payload = json.loads((tmp_path / "live" / "guards" / "latest_trade_reconciliation.json").read_text(encoding="utf-8"))
    assert status.decisions_written == 1
    assert payload["action"] == "trade"
    assert payload["matched_trade_count"] == 1
    assert payload["window_mode"] == "session"
    assert payload["session_started_at_ms"] == trade_time_ms - 10_000
    assert client.user_trade_calls[0][1] == trade_time_ms - 10_000



def test_trade_reconciliation_daemon_partitions_user_trade_requests_when_range_hits_limit(tmp_path) -> None:
    config = _build_config(tmp_path)
    runtime_path = tmp_path / "private" / "state" / "latest.json"
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text(json.dumps({"trade_fills": {}}), encoding="utf-8")
    daemon_config = TradeReconciliationDaemonConfig(
        runtime_state_path=runtime_path,
        lookback_ms=60 * 60 * 1000,
        thresholds=TradeReconciliationThresholds(),
    )
    client = PartitioningClient()

    with JSONLWriter(tmp_path) as writer:
        daemon = TradeReconciliationDaemon(
            config,
            client=client,
            writer=writer,
            daemon_config=daemon_config,
        )
        rows = daemon._fetch_user_trades_partitioned(0, 2_000)

    assert len(client.calls) > 1
    assert len(rows) == 4

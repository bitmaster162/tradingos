import asyncio
import json
from types import SimpleNamespace

from btcusdt_bot.authoritative.archive import INCOME_HISTORY_DATASET, USER_TRADES_DATASET, AuthoritativeArchive
from btcusdt_bot.config import BotConfig
from btcusdt_bot.monitoring.trade_reconciliation import TradeReconciliationThresholds
from btcusdt_bot.storage.jsonl import JSONLWriter
from btcusdt_bot.trade_reconciliation_daemon import TradeReconciliationDaemon, TradeReconciliationDaemonConfig


class GapFillClient:
    def __init__(self, gap_trade_time_ms: int) -> None:
        self.gap_trade_time_ms = gap_trade_time_ms
        self.user_trade_calls = []
        self.income_calls = []

    def user_trades(self, symbol, *, start_time=None, end_time=None, limit=None):
        self.user_trade_calls.append((start_time, end_time, limit))
        return SimpleNamespace(data=[
            {"id": 202, "orderId": 22, "symbol": symbol, "time": self.gap_trade_time_ms, "price": "65000", "qty": "0.001", "quoteQty": "65", "realizedPnl": "0.20", "commission": "0.02"},
        ])

    def income_history(self, *, symbol=None, income_type=None, start_time=None, end_time=None, page=None, limit=None):
        self.income_calls.append((start_time, end_time, page, limit))
        if page == 1:
            return SimpleNamespace(data=[
                {"incomeType": "REALIZED_PNL", "income": "0.20", "tradeId": "202", "tranId": "9901", "symbol": symbol, "time": self.gap_trade_time_ms},
                {"incomeType": "COMMISSION", "income": "-0.02", "tradeId": "202", "tranId": "9902", "symbol": symbol, "time": self.gap_trade_time_ms},
            ])
        return SimpleNamespace(data=[])


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


def test_trade_reconciliation_daemon_blends_archive_with_live_gap_fill(tmp_path) -> None:
    config = _build_config(tmp_path)
    now_ms = 1_700_000_100_000
    archive_start_ms = now_ms - 5_000
    archive_end_ms = now_ms - 3_001
    gap_trade_time_ms = now_ms - 1_000
    archive = AuthoritativeArchive(tmp_path, symbol="BTCUSDT")
    archive.upsert_rows(
        USER_TRADES_DATASET,
        [{"id": 201, "orderId": 21, "symbol": "BTCUSDT", "time": archive_start_ms + 100, "price": "64000", "qty": "0.001", "quoteQty": "64", "realizedPnl": "0.10", "commission": "0.01"}],
        coverage_intervals=[(archive_start_ms, archive_end_ms)],
        updated_at_ms=1,
    )
    archive.upsert_rows(
        INCOME_HISTORY_DATASET,
        [
            {"incomeType": "REALIZED_PNL", "income": "0.10", "tradeId": "201", "tranId": "9801", "symbol": "BTCUSDT", "time": archive_start_ms + 100},
            {"incomeType": "COMMISSION", "income": "-0.01", "tradeId": "201", "tranId": "9802", "symbol": "BTCUSDT", "time": archive_start_ms + 100},
        ],
        coverage_intervals=[(archive_start_ms, archive_end_ms)],
        updated_at_ms=1,
    )

    runtime_path = tmp_path / "private" / "state" / "latest.json"
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text(json.dumps({
        "trade_fills": {
            "201": {"symbol": "BTCUSDT", "trade_id": 201, "order_id": 21, "trade_time_ms": archive_start_ms + 100, "quote_qty": "64", "realized_pnl": "0.10", "commission": "0.01"},
            "202": {"symbol": "BTCUSDT", "trade_id": 202, "order_id": 22, "trade_time_ms": gap_trade_time_ms, "quote_qty": "65", "realized_pnl": "0.20", "commission": "0.02"},
        }
    }), encoding="utf-8")
    session_state_path = tmp_path / "live" / "status" / "latest.json"
    session_state_path.parent.mkdir(parents=True, exist_ok=True)
    session_state_path.write_text(json.dumps({"session_started_at_ms": archive_start_ms}), encoding="utf-8")

    client = GapFillClient(gap_trade_time_ms=gap_trade_time_ms)
    daemon_config = TradeReconciliationDaemonConfig(
        runtime_state_path=runtime_path,
        lookback_ms=10_000,
        thresholds=TradeReconciliationThresholds(),
        session_state_path=session_state_path,
        authoritative_archive_root=tmp_path,
        prefer_authoritative_archive=True,
        hydrate_archive_gaps=True,
    )

    with JSONLWriter(tmp_path) as writer:
        daemon = TradeReconciliationDaemon(config, client=client, writer=writer, daemon_config=daemon_config)
        status = asyncio.run(daemon.run(interval_seconds=0.01, max_iterations=1))

    assert status.last_source_mode == "archive_blended"
    assert status.archived_user_trade_count == 1
    assert status.live_user_trade_count == 1
    assert status.archive_gap_count >= 1
    hydrated = archive.load_rows_for_range(USER_TRADES_DATASET, start_ms=archive_start_ms, end_ms=now_ms)
    assert {int(row["id"]) for row in hydrated.rows} == {201, 202}

import json
from types import SimpleNamespace

from btcusdt_bot.authoritative.backfill import AuthoritativeHistoryBackfillConfig, AuthoritativeHistoryBackfiller
from btcusdt_bot.config import BotConfig
from btcusdt_bot.storage.jsonl import JSONLWriter


class BackfillClient:
    def __init__(self):
        self.income_calls = 0

    def user_trades(self, symbol, *, start_time=None, end_time=None, limit=None):
        return SimpleNamespace(data=[
            {"id": 101, "orderId": 11, "symbol": symbol, "time": start_time + 100, "price": "60000", "qty": "0.001", "quoteQty": "60", "realizedPnl": "0.10", "commission": "0.01"},
        ])

    def income_history(self, *, symbol=None, income_type=None, start_time=None, end_time=None, page=None, limit=None):
        self.income_calls += 1
        if page == 1:
            return SimpleNamespace(data=[
                {"incomeType": "REALIZED_PNL", "income": "0.10", "tradeId": "101", "tranId": "9001", "symbol": symbol, "time": start_time + 100},
                {"incomeType": "COMMISSION", "income": "-0.01", "tradeId": "101", "tranId": "9002", "symbol": symbol, "time": start_time + 100},
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


def test_authoritative_backfiller_writes_archive_manifest_and_report(tmp_path) -> None:
    config = _build_config(tmp_path)
    client = BackfillClient()
    with JSONLWriter(tmp_path) as writer:
        backfiller = AuthoritativeHistoryBackfiller(
            config,
            client=client,
            writer=writer,
            backfill_config=AuthoritativeHistoryBackfillConfig(
                archive_root=tmp_path,
                start_ms=1_700_000_000_000,
                end_ms=1_700_000_000_999,
            ),
        )
        result = backfiller.run_once()

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert result.user_trade_row_count == 1
    assert result.income_row_count == 2
    assert manifest["symbol"] == "BTCUSDT"
    assert (tmp_path / "authoritative" / "latest_backfill_status.json").exists()
    assert list((tmp_path / "reports").glob("**/*authoritative_backfill.jsonl"))


def test_authoritative_backfiller_can_limit_private_surface_to_user_trades(tmp_path) -> None:
    config = _build_config(tmp_path)
    client = BackfillClient()
    with JSONLWriter(tmp_path) as writer:
        backfiller = AuthoritativeHistoryBackfiller(
            config,
            client=client,
            writer=writer,
            backfill_config=AuthoritativeHistoryBackfillConfig(
                archive_root=tmp_path,
                start_ms=1_700_000_000_000,
                end_ms=1_700_000_000_999,
                include_income_history=False,
            ),
        )
        result = backfiller.run_once()

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert result.user_trade_row_count == 1
    assert result.income_row_count == 0
    assert result.income_history_requested is False
    assert result.income_requests == 0
    assert client.income_calls == 0
    assert manifest["datasets"]["income_history"] == {}

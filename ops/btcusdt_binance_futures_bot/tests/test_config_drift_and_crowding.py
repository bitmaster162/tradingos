import asyncio
from decimal import Decimal

from btcusdt_bot.collector.crowding import CrowdingCollector
from btcusdt_bot.config import BotConfig
from btcusdt_bot.domain.models import APICallResult
from btcusdt_bot.reconcile_daemon import diff_runtime_states
from btcusdt_bot.reconcile_healer import TargetedReconcileHealer
from btcusdt_bot.state.store import RuntimeState, StateStore
from btcusdt_bot.storage.jsonl import JSONLWriter


class FakeNoQueryClient:
    def query_order(self, symbol, *, order_id=None, client_order_id=None):  # pragma: no cover - should not be called
        raise AssertionError("query_order should not be called")

    def query_algo_order(self, *, algo_id=None, client_algo_id=None):  # pragma: no cover - should not be called
        raise AssertionError("query_algo_order should not be called")


class FakeCrowdingClient:
    def open_interest(self, symbol):
        return APICallResult(data={"symbol": symbol, "openInterest": "12345.67", "time": 1700000000000}, headers={})

    def open_interest_hist(self, symbol, *, period, limit=30, start_time=None, end_time=None):
        return APICallResult(data=[{"symbol": symbol, "sumOpenInterest": "12300", "timestamp": 1700000000000}], headers={})

    def global_long_short_account_ratio(self, symbol, *, period, limit=30, start_time=None, end_time=None):
        return APICallResult(data=[{"symbol": symbol, "longShortRatio": "1.1", "timestamp": 1700000000000}], headers={})

    def top_long_short_account_ratio(self, symbol, *, period, limit=30, start_time=None, end_time=None):
        return APICallResult(data=[{"symbol": symbol, "longShortRatio": "1.2", "timestamp": 1700000000000}], headers={})

    def top_long_short_position_ratio(self, symbol, *, period, limit=30, start_time=None, end_time=None):
        return APICallResult(data=[{"symbol": symbol, "longShortRatio": "1.3", "timestamp": 1700000000000}], headers={})

    def taker_buy_sell_ratio(self, symbol, *, period, limit=30, start_time=None, end_time=None):
        return APICallResult(data=[{"buySellRatio": "1.4", "timestamp": 1700000000000}], headers={})



def _build_config(tmp_path) -> BotConfig:
    return BotConfig(
        env="demo",
        symbol="BTCUSDT",
        rest_base_url="https://demo-fapi.binance.com",
        ws_public_base_url="wss://fstream.binancefuture.com",
        ws_market_base_url="wss://fstream.binancefuture.com",
        ws_private_base_url="wss://fstream.binancefuture.com",
        api_key="demo-key",
        api_secret="demo-secret",
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



def test_diff_runtime_states_detects_config_drift() -> None:
    local_state = RuntimeState(
        symbol_config={"symbol": "BTCUSDT", "marginType": "ISOLATED"},
        leverage_brackets={"symbol": "BTCUSDT", "brackets": [{"bracket": 1, "initialLeverage": 50}]},
        commission_rate={"symbol": "BTCUSDT", "makerCommissionRate": "0.0002"},
    )
    exchange_state = RuntimeState(
        symbol_config={"symbol": "BTCUSDT", "marginType": "CROSSED"},
        leverage_brackets={"symbol": "BTCUSDT", "brackets": [{"bracket": 1, "initialLeverage": 25}]},
        commission_rate={"symbol": "BTCUSDT", "makerCommissionRate": "0.0001"},
    )

    report = diff_runtime_states(local_state, exchange_state, symbol="BTCUSDT")

    assert sorted(mismatch.kind for mismatch in report.config_mismatches) == [
        "commission_rate",
        "leverage_brackets",
        "symbol_config",
    ]



def test_targeted_reconcile_healer_patches_config_drift(tmp_path) -> None:
    config = _build_config(tmp_path)
    store = StateStore()
    store.patch_symbol_config({"symbol": "BTCUSDT", "marginType": "ISOLATED"})
    store.patch_leverage_brackets({"symbol": "BTCUSDT", "brackets": [{"bracket": 1, "initialLeverage": 50}]})
    store.patch_commission_rate({"symbol": "BTCUSDT", "makerCommissionRate": "0.0002"})

    exchange_state = RuntimeState(
        symbol_config={"symbol": "BTCUSDT", "marginType": "CROSSED"},
        leverage_brackets={"symbol": "BTCUSDT", "brackets": [{"bracket": 1, "initialLeverage": 25}]},
        commission_rate={"symbol": "BTCUSDT", "makerCommissionRate": "0.0001"},
    )

    report = diff_runtime_states(store.snapshot(), exchange_state, symbol="BTCUSDT")
    healer = TargetedReconcileHealer(config, client=FakeNoQueryClient(), store=store)
    result = healer.heal(report, exchange_state)

    assert result.applied is True
    assert store.state.symbol_config["marginType"] == "CROSSED"
    assert store.state.leverage_brackets["brackets"][0]["initialLeverage"] == 25
    assert store.state.commission_rate["makerCommissionRate"] == "0.0001"
    patched_keys = {action.key for action in result.actions if action.kind == "config"}
    assert patched_keys == {"symbol_config", "leverage_brackets", "commission_rate"}



def test_crowding_collector_fetches_and_writes_snapshot(tmp_path) -> None:
    config = _build_config(tmp_path)
    store = StateStore()
    with JSONLWriter(tmp_path) as writer:
        collector = CrowdingCollector(config, client=FakeCrowdingClient(), writer=writer, store=store)
        snapshot = collector.fetch_snapshot(period="5m")
        assert snapshot["symbol"] == "BTCUSDT"
        assert snapshot["global_long_short_account_ratio"]["longShortRatio"] == "1.1"
        assert snapshot["taker_buy_sell_ratio"]["buySellRatio"] == "1.4"

        status = asyncio.run(collector.run(period="5m", interval_seconds=0.01, max_iterations=1))
        assert status.snapshots_written == 1
        assert status.last_snapshot_path.endswith("btcusdt_5m.jsonl")
        assert store.state.latest_crowding_snapshot["symbol"] == "BTCUSDT"

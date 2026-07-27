from decimal import Decimal

from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.rest_client import BinanceAPIError
from btcusdt_bot.domain.enums import PositionSide
from btcusdt_bot.domain.models import APICallResult, PositionSnapshot
from btcusdt_bot.reconcile_daemon import diff_runtime_states
from btcusdt_bot.reconcile_healer import TargetedReconcileHealer
from btcusdt_bot.state.store import RuntimeState, StateStore


class FakeQueryClient:
    def query_order(self, symbol, *, order_id=None, client_order_id=None):
        return APICallResult(
            data={
                "symbol": symbol,
                "orderId": 77,
                "clientOrderId": client_order_id,
                "side": "BUY",
                "positionSide": "BOTH",
                "type": "LIMIT",
                "status": "FILLED",
                "timeInForce": "GTX",
                "origQty": "0.001",
                "executedQty": "0.001",
                "price": "65000",
                "avgPrice": "64999.5",
                "updateTime": 123456,
            },
            headers={},
        )

    def query_algo_order(self, *, algo_id=None, client_algo_id=None):
        raise AssertionError("query_algo_order should not be called in this test")


class FakeAlgoMissingClient:
    def query_order(self, symbol, *, order_id=None, client_order_id=None):
        raise AssertionError("query_order should not be called in this test")

    def query_algo_order(self, *, algo_id=None, client_algo_id=None):
        raise BinanceAPIError(400, -2013, "Unknown clientAlgoId", {"msg": "Unknown clientAlgoId"})



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



def test_targeted_reconcile_healer_hydrates_missing_order_and_patches_account_nodes(tmp_path) -> None:
    config = _build_config(tmp_path)
    store = StateStore()
    store.upsert_normal_order_from_rest(
        {
            "symbol": "BTCUSDT",
            "orderId": 1,
            "clientOrderId": "ENT-1",
            "side": "BUY",
            "positionSide": "BOTH",
            "type": "LIMIT",
            "status": "NEW",
            "timeInForce": "GTX",
            "origQty": "0.001",
            "executedQty": "0",
            "price": "65000",
            "avgPrice": "0",
            "updateTime": 1000,
        }
    )
    store.patch_balance("USDT", Decimal("1000"))
    store.patch_position(
        PositionSnapshot(
            symbol="BTCUSDT",
            position_side=PositionSide.BOTH,
            amount=Decimal("0.001"),
            entry_price=Decimal("65000"),
            break_even_price=Decimal("65000"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            margin_type="isolated",
            isolated_wallet=Decimal("50"),
        )
    )

    exchange_state = RuntimeState()
    exchange_state.account.balances["USDT"] = Decimal("995")
    report = diff_runtime_states(store.snapshot(), exchange_state, symbol="BTCUSDT")

    healer = TargetedReconcileHealer(config, client=FakeQueryClient(), store=store)
    result = healer.heal(report, exchange_state)

    assert result.applied is True
    assert store.state.normal_orders["ENT-1"].status == "FILLED"
    assert store.current_position_qty("BTCUSDT") == 0
    assert store.state.account.balances["USDT"] == Decimal("995")
    outcomes = {action.outcome for action in result.actions}
    assert "hydrated_from_query" in outcomes
    assert "cleared_to_zero" in outcomes
    assert "patched_from_exchange_snapshot" in outcomes



def test_targeted_reconcile_healer_marks_missing_algo_terminal_after_not_found(tmp_path) -> None:
    config = _build_config(tmp_path)
    store = StateStore()
    store.upsert_algo_order_from_rest(
        {
            "symbol": "BTCUSDT",
            "algoId": 5,
            "clientAlgoId": "TP-1",
            "side": "SELL",
            "positionSide": "BOTH",
            "orderType": "TAKE_PROFIT_MARKET",
            "algoStatus": "NEW",
            "quantity": "0.001",
            "actualQuantity": "0",
            "triggerPrice": "70000",
            "price": "0",
            "workingType": "CONTRACT_PRICE",
            "updateTime": 2000,
        }
    )
    exchange_state = RuntimeState()
    report = diff_runtime_states(store.snapshot(), exchange_state, symbol="BTCUSDT")

    healer = TargetedReconcileHealer(config, client=FakeAlgoMissingClient(), store=store)
    result = healer.heal(report, exchange_state)

    assert result.applied is True
    assert store.state.algo_orders["TP-1"].status == "CANCELED"
    assert any(action.outcome == "marked_terminal_after_not_found" for action in result.actions)

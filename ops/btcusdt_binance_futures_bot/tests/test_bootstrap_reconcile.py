from decimal import Decimal

from btcusdt_bot.bootstrap.reconcile import BootstrapSynchronizer
from btcusdt_bot.config import BotConfig
from btcusdt_bot.domain.models import APICallResult
from btcusdt_bot.state.store import StateStore


class FakeClient:
    def exchange_info(self) -> APICallResult:
        return APICallResult(
            data={
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "status": "TRADING",
                        "triggerProtect": "0.15",
                        "marketTakeBound": "0.30",
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                            {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
                            {"filterType": "MARKET_LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
                            {"filterType": "MIN_NOTIONAL", "notional": "5"},
                            {"filterType": "PERCENT_PRICE", "multiplierUp": "1.15", "multiplierDown": "0.85"},
                            {"filterType": "MAX_NUM_ORDERS", "limit": 200},
                        ],
                    }
                ]
            },
            headers={},
        )

    def symbol_config(self, symbol: str) -> APICallResult:
        return APICallResult(data={"symbol": symbol, "marginType": "ISOLATED"}, headers={})

    def account_v3(self) -> APICallResult:
        return APICallResult(data={"assets": [{"asset": "USDT", "walletBalance": "1000"}]}, headers={})

    def position_risk_v3(self, symbol: str) -> APICallResult:
        return APICallResult(
            data=[
                {
                    "symbol": symbol,
                    "positionSide": "BOTH",
                    "positionAmt": "0.002",
                    "entryPrice": "65000",
                    "breakEvenPrice": "65010",
                    "realizedPnl": "1.2",
                    "unRealizedProfit": "0.4",
                    "marginType": "isolated",
                    "isolatedWallet": "50",
                }
            ],
            headers={},
        )

    def leverage_brackets(self, symbol: str) -> APICallResult:
        return APICallResult(data=[{"symbol": symbol, "brackets": []}], headers={})

    def api_trading_status(self, symbol: str) -> APICallResult:
        return APICallResult(
            data={"indicators": {symbol: {"isLocked": True, "plannedRecoverTime": 1234567890}}},
            headers={},
        )

    def commission_rate(self, symbol: str) -> APICallResult:
        return APICallResult(
            data={"symbol": symbol, "makerCommissionRate": "0.0002", "takerCommissionRate": "0.0005"},
            headers={},
        )

    def open_orders(self, symbol: str) -> APICallResult:
        return APICallResult(
            data=[
                {
                    "symbol": symbol,
                    "side": "BUY",
                    "positionSide": "BOTH",
                    "type": "LIMIT",
                    "status": "NEW",
                    "timeInForce": "GTX",
                    "orderId": 1,
                    "clientOrderId": "ENT-1",
                    "origQty": "0.002",
                    "executedQty": "0",
                    "price": "65000",
                    "avgPrice": "0",
                    "reduceOnly": False,
                    "closePosition": False,
                    "updateTime": 1000,
                }
            ],
            headers={"X-MBX-ORDER-COUNT-10S": "1"},
        )

    def open_algo_orders(self, symbol: str) -> APICallResult:
        return APICallResult(
            data=[
                {
                    "symbol": symbol,
                    "side": "SELL",
                    "positionSide": "BOTH",
                    "orderType": "STOP_MARKET",
                    "algoStatus": "NEW",
                    "timeInForce": "GTC",
                    "algoId": 2,
                    "clientAlgoId": "STP-1",
                    "quantity": "0.002",
                    "actualPrice": "0",
                    "triggerPrice": "64000",
                    "price": "0",
                    "reduceOnly": True,
                    "closePosition": False,
                    "workingType": "CONTRACT_PRICE",
                    "updateTime": 1001,
                }
            ],
            headers={"X-MBX-ORDER-COUNT-1M": "2"},
        )


def test_bootstrap_sync_rehydrates_runtime_state(tmp_path) -> None:
    config = BotConfig(
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
        enable_contract_info_stream=True,
        enable_force_order_stream=False,
        enable_countdown_heartbeat=False,
        state_flush_every_events=1,
        data_dir=tmp_path,
        log_level="INFO",
    )
    store = StateStore()
    synchronizer = BootstrapSynchronizer(config, client=FakeClient(), store=store)

    result, raw = synchronizer.sync()

    assert result.open_normal_orders == 1
    assert result.open_algo_orders == 1
    assert result.quantitative_lock is True
    assert result.cooling_off is True
    assert result.symbol_filters["tick_size"] == Decimal("0.1")
    assert raw["commission_rate"]["makerCommissionRate"] == "0.0002"
    assert store.state.account.balances["USDT"] == Decimal("1000")
    assert "ENT-1" in store.state.normal_orders
    assert "STP-1" in store.state.algo_orders
    assert store.state.order_rate_limit_headers["X-MBX-ORDER-COUNT-10S"] == "1"
    assert store.state.last_bootstrap_summary["open_normal_orders"] == 1
    assert raw["contract_info"]["cs"] == "TRADING"
    assert store.state.latest_contract_info["cs"] == "TRADING"

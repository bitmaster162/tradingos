from decimal import Decimal

from btcusdt_bot.connectors.rest_client import BinanceAPIError
from btcusdt_bot.domain.enums import AlgoStatus, OrderStatus
from btcusdt_bot.domain.models import APICallResult
from btcusdt_bot.execution.query_resolver import QueryResolver
from btcusdt_bot.state.store import StateStore


class FakeClient:
    def query_order(self, symbol, *, order_id=None, client_order_id=None):
        return APICallResult(
            data={
                "symbol": symbol,
                "orderId": order_id or 11,
                "clientOrderId": client_order_id or "ENT-1",
                "side": "BUY",
                "positionSide": "BOTH",
                "type": "LIMIT",
                "status": "NEW",
                "timeInForce": "GTX",
                "origQty": "0.002",
                "executedQty": "0",
                "price": "65000",
                "avgPrice": "0",
                "updateTime": 123456,
            },
            headers={"X-MBX-ORDER-COUNT-10S": "1"},
        )

    def query_algo_order(self, *, algo_id=None, client_algo_id=None):
        return APICallResult(
            data={
                "algoId": algo_id or 22,
                "clientAlgoId": client_algo_id or "ALGO-1",
                "symbol": "BTCUSDT",
                "side": "SELL",
                "positionSide": "BOTH",
                "orderType": "STOP_MARKET",
                "algoStatus": "TRIGGERED",
                "timeInForce": "GTC",
                "quantity": "0.002",
                "actualQuantity": "0.001",
                "triggerPrice": "64000",
                "price": "0",
                "actualPrice": "0",
                "updateTime": 123999,
            },
            headers={"X-MBX-ORDER-COUNT-1M": "5"},
        )


class UnknownClient:
    def query_order(self, symbol, *, order_id=None, client_order_id=None):
        raise BinanceAPIError(503, None, "Unknown error, please check your request or try again later.")

    def query_algo_order(self, *, algo_id=None, client_algo_id=None):
        raise BinanceAPIError(400, -2013, "Unknown order sent.")


def test_query_normal_hydrates_store_from_rest_row() -> None:
    store = StateStore()
    resolver = QueryResolver(client=FakeClient(), store=store)

    result = resolver.query_normal(symbol="BTCUSDT", client_order_id="ENT-1")

    assert result.found is True
    assert result.updated_store is True
    assert store.state.normal_orders["ENT-1"].status == OrderStatus.NEW
    assert store.state.normal_orders["ENT-1"].price == Decimal("65000")
    assert store.state.order_rate_limit_headers["X-MBX-ORDER-COUNT-10S"] == "1"


def test_query_algo_hydrates_store_from_rest_row() -> None:
    store = StateStore()
    resolver = QueryResolver(client=FakeClient(), store=store)

    result = resolver.query_algo(symbol="BTCUSDT", client_algo_id="ALGO-1")

    assert result.found is True
    assert result.updated_store is True
    assert store.state.algo_orders["ALGO-1"].status == AlgoStatus.TRIGGERED
    assert store.state.algo_orders["ALGO-1"].executed_qty == Decimal("0.001")
    assert store.state.order_rate_limit_headers["X-MBX-ORDER-COUNT-1M"] == "5"


def test_query_resolver_classifies_unknown_and_not_found_errors() -> None:
    resolver = QueryResolver(client=UnknownClient(), store=StateStore())

    normal = resolver.query_normal(symbol="BTCUSDT", client_order_id="ENT-404")
    algo = resolver.query_algo(symbol="BTCUSDT", client_algo_id="ALGO-404")

    assert normal.found is False
    assert normal.execution_unknown is True
    assert algo.found is False
    assert algo.not_found is True

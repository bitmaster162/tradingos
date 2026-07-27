from decimal import Decimal

from btcusdt_bot.domain.enums import OrderType, PositionSide, Side, TimeInForce
from btcusdt_bot.domain.models import AlgoOrderProposal, OrderProposal
from btcusdt_bot.execution.payloads import build_algo_order_payload, build_normal_order_payload


def test_build_normal_limit_payload_for_gtx_entry() -> None:
    proposal = OrderProposal(
        symbol="BTCUSDT",
        side=Side.BUY,
        position_side=PositionSide.BOTH,
        order_type=OrderType.LIMIT,
        tif=TimeInForce.GTX,
        qty=Decimal("0.001"),
        price=Decimal("65000.1"),
        client_id="ENT-1",
    )
    payload = build_normal_order_payload(proposal, include_position_side=False)

    assert payload == {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "LIMIT",
        "newOrderRespType": "ACK",
        "timeInForce": "GTX",
        "quantity": "0.001",
        "price": "65000.1",
        "newClientOrderId": "ENT-1",
    }


def test_build_algo_close_position_payload_omits_quantity_and_reduce_only() -> None:
    proposal = AlgoOrderProposal(
        symbol="BTCUSDT",
        side=Side.SELL,
        position_side=PositionSide.BOTH,
        order_type=OrderType.STOP_MARKET,
        qty=None,
        trigger_price=Decimal("64000"),
        close_position=True,
        reduce_only=True,
        price_protect=True,
        client_algo_id="STP-1",
    )
    payload = build_algo_order_payload(proposal, include_position_side=False)

    assert payload["algoType"] == "CONDITIONAL"
    assert payload["type"] == "STOP_MARKET"
    assert payload["triggerPrice"] == "64000"
    assert payload["closePosition"] == "true"
    assert payload["priceProtect"] == "TRUE"
    assert "quantity" not in payload
    assert "reduceOnly" not in payload

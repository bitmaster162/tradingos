from __future__ import annotations

from decimal import Decimal
from typing import Any

from btcusdt_bot.domain.enums import OrderType, PositionSide
from btcusdt_bot.domain.models import AlgoOrderProposal, OrderProposal


NORMAL_TESTABLE_ORDER_TYPES = {OrderType.LIMIT, OrderType.MARKET}
ALGO_ORDER_TYPES = {
    OrderType.STOP,
    OrderType.STOP_MARKET,
    OrderType.TAKE_PROFIT,
    OrderType.TAKE_PROFIT_MARKET,
    OrderType.TRAILING_STOP_MARKET,
}


def _fmt(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f") if value != 0 else "0"


def _bool_str(value: bool) -> str:
    return "true" if value else "false"


def build_normal_order_payload(
    proposal: OrderProposal,
    *,
    include_position_side: bool = False,
    new_order_resp_type: str = "ACK",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "symbol": proposal.symbol,
        "side": proposal.side.value,
        "type": proposal.order_type.value,
        "newOrderRespType": new_order_resp_type,
    }

    if include_position_side or proposal.position_side != PositionSide.BOTH:
        payload["positionSide"] = proposal.position_side.value

    if proposal.order_type == OrderType.LIMIT:
        payload["timeInForce"] = proposal.tif.value
        payload["quantity"] = _fmt(proposal.qty)
        payload["price"] = _fmt(proposal.price)
    elif proposal.order_type == OrderType.MARKET:
        payload["quantity"] = _fmt(proposal.qty)
    else:
        raise ValueError(f"Unsupported normal order type for v1 gateway: {proposal.order_type}")

    if proposal.client_id:
        payload["newClientOrderId"] = proposal.client_id
    if proposal.reduce_only:
        payload["reduceOnly"] = _bool_str(True)
    if proposal.close_position:
        payload["closePosition"] = _bool_str(True)
    return payload


def build_algo_order_payload(
    proposal: AlgoOrderProposal,
    *,
    include_position_side: bool = False,
    new_order_resp_type: str = "ACK",
) -> dict[str, Any]:
    if proposal.order_type not in ALGO_ORDER_TYPES:
        raise ValueError(f"Unsupported algo order type: {proposal.order_type}")

    payload: dict[str, Any] = {
        "algoType": "CONDITIONAL",
        "symbol": proposal.symbol,
        "side": proposal.side.value,
        "type": proposal.order_type.value,
        "timeInForce": proposal.tif.value,
        "workingType": proposal.working_type.value,
        "newOrderRespType": new_order_resp_type,
    }

    if include_position_side or proposal.position_side != PositionSide.BOTH:
        payload["positionSide"] = proposal.position_side.value

    if proposal.client_algo_id:
        payload["clientAlgoId"] = proposal.client_algo_id

    if proposal.close_position:
        payload["closePosition"] = _bool_str(True)
    else:
        if proposal.qty is None:
            raise ValueError("quantity is required when close_position is false")
        payload["quantity"] = _fmt(proposal.qty)

    if proposal.reduce_only and not proposal.close_position:
        payload["reduceOnly"] = _bool_str(True)

    if proposal.price is not None:
        payload["price"] = _fmt(proposal.price)

    if proposal.order_type == OrderType.TRAILING_STOP_MARKET:
        if proposal.activate_price is not None:
            payload["activatePrice"] = _fmt(proposal.activate_price)
        if proposal.callback_rate is None:
            raise ValueError("callback_rate is required for TRAILING_STOP_MARKET")
        payload["callbackRate"] = _fmt(proposal.callback_rate)
    else:
        payload["triggerPrice"] = _fmt(proposal.trigger_price)
        if proposal.price_protect:
            payload["priceProtect"] = "TRUE"

    return payload

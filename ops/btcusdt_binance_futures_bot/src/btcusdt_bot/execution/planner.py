from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

from btcusdt_bot.domain.enums import OrderType, PositionSide, Side, TimeInForce, WorkingType
from btcusdt_bot.domain.models import AlgoOrderProposal, OrderProposal


@dataclass(slots=True)
class PlannerConfig:
    passive_offset_bps: Decimal = Decimal("2")
    stop_atr_multiple: Decimal = Decimal("1.0")
    take_profit_atr_multiple: Decimal = Decimal("1.5")
    working_type: WorkingType = WorkingType.CONTRACT_PRICE


class ExecutionPlanner:
    def __init__(self, config: PlannerConfig | None = None):
        self.config = config or PlannerConfig()

    def entry_order(
        self,
        *,
        symbol: str,
        side: Side,
        qty: Decimal,
        mark_price: Decimal,
        position_side: PositionSide = PositionSide.BOTH,
    ) -> OrderProposal:
        offset = mark_price * self.config.passive_offset_bps / Decimal("10000")
        price = mark_price - offset if side == Side.BUY else mark_price + offset
        client_id = f"ENT-{int(time.time() * 1000)}"

        return OrderProposal(
            symbol=symbol,
            side=side,
            position_side=position_side,
            order_type=OrderType.LIMIT,
            tif=TimeInForce.GTX,
            qty=qty,
            price=price,
            reduce_only=False,
            close_position=False,
            working_type=self.config.working_type,
            client_id=client_id,
        )

    def bracket_exits(
        self,
        *,
        symbol: str,
        entry_side: Side,
        qty: Decimal,
        entry_price: Decimal,
        atr: Decimal,
        position_side: PositionSide = PositionSide.BOTH,
    ) -> tuple[AlgoOrderProposal, AlgoOrderProposal]:
        exit_side = Side.SELL if entry_side == Side.BUY else Side.BUY
        stop_distance = atr * self.config.stop_atr_multiple
        take_distance = atr * self.config.take_profit_atr_multiple

        if entry_side == Side.BUY:
            stop_price = entry_price - stop_distance
            take_profit_price = entry_price + take_distance
        else:
            stop_price = entry_price + stop_distance
            take_profit_price = entry_price - take_distance

        ts = int(time.time() * 1000)
        stop_algo = AlgoOrderProposal(
            symbol=symbol,
            side=exit_side,
            position_side=position_side,
            order_type=OrderType.STOP_MARKET,
            qty=qty,
            trigger_price=stop_price,
            price=None,
            tif=TimeInForce.GTC,
            reduce_only=True,
            close_position=False,
            working_type=self.config.working_type,
            client_algo_id=f"STP-{ts}",
        )
        tp_algo = AlgoOrderProposal(
            symbol=symbol,
            side=exit_side,
            position_side=position_side,
            order_type=OrderType.TAKE_PROFIT_MARKET,
            qty=qty,
            trigger_price=take_profit_price,
            price=None,
            tif=TimeInForce.GTC,
            reduce_only=True,
            close_position=False,
            working_type=self.config.working_type,
            client_algo_id=f"TP-{ts}",
        )
        return stop_algo, tp_algo

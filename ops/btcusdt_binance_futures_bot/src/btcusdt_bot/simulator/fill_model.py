from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from btcusdt_bot.domain.enums import Side


@dataclass(slots=True)
class PassiveFillScenario:
    side: Side
    limit_price: Decimal
    qty: Decimal
    best_bid: Decimal
    best_ask: Decimal
    next_trade_price: Decimal
    adverse_move_bps: Decimal = Decimal("0")


@dataclass(slots=True)
class FillOutcome:
    filled_qty: Decimal
    avg_price: Decimal | None
    maker: bool
    rejected_as_taker: bool
    missed_trade: bool
    notes: list[str]


class MakerFirstFillModel:
    def simulate(self, scenario: PassiveFillScenario) -> FillOutcome:
        notes: list[str] = []

        if scenario.side == Side.BUY and scenario.limit_price >= scenario.best_ask:
            return FillOutcome(
                filled_qty=Decimal("0"),
                avg_price=None,
                maker=True,
                rejected_as_taker=True,
                missed_trade=False,
                notes=["gtx_would_reject_crossing_buy"],
            )

        if scenario.side == Side.SELL and scenario.limit_price <= scenario.best_bid:
            return FillOutcome(
                filled_qty=Decimal("0"),
                avg_price=None,
                maker=True,
                rejected_as_taker=True,
                missed_trade=False,
                notes=["gtx_would_reject_crossing_sell"],
            )

        if scenario.side == Side.BUY:
            filled = scenario.next_trade_price <= scenario.limit_price
        else:
            filled = scenario.next_trade_price >= scenario.limit_price

        if not filled:
            return FillOutcome(
                filled_qty=Decimal("0"),
                avg_price=None,
                maker=True,
                rejected_as_taker=False,
                missed_trade=True,
                notes=["passive_order_not_touched"],
            )

        notes.append("passive_fill_assumed_full")
        if scenario.adverse_move_bps > 0:
            notes.append(f"adverse_move_bps={scenario.adverse_move_bps}")

        return FillOutcome(
            filled_qty=scenario.qty,
            avg_price=scenario.limit_price,
            maker=True,
            rejected_as_taker=False,
            missed_trade=False,
            notes=notes,
        )

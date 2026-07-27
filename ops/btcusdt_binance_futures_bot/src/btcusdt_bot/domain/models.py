from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from btcusdt_bot.domain.enums import AlgoStatus, OrderStatus, OrderType, PositionSide, Side, TimeInForce, WorkingType


@dataclass(slots=True)
class SymbolFilters:
    symbol: str
    tick_size: Decimal
    step_size: Decimal
    market_step_size: Decimal
    min_qty: Decimal
    market_min_qty: Decimal
    min_notional: Decimal
    percent_price_up: Decimal | None = None
    percent_price_down: Decimal | None = None
    trigger_protect: Decimal | None = None
    market_take_bound: Decimal | None = None
    max_num_orders: int | None = None


@dataclass(slots=True)
class OrderRecord:
    symbol: str
    side: Side
    position_side: PositionSide
    order_type: OrderType
    status: OrderStatus
    tif: str
    order_id: int | None
    client_order_id: str
    qty: Decimal
    executed_qty: Decimal
    price: Decimal
    avg_price: Decimal
    reduce_only: bool
    close_position: bool
    update_time_ms: int


@dataclass(slots=True)
class AlgoOrderRecord:
    symbol: str
    side: Side
    position_side: PositionSide
    order_type: OrderType
    status: AlgoStatus
    tif: str
    algo_id: int | None
    client_algo_id: str
    qty: Decimal
    executed_qty: Decimal
    price: Decimal
    avg_price: Decimal
    trigger_price: Decimal
    reduce_only: bool
    close_position: bool
    working_type: WorkingType
    update_time_ms: int
    reject_reason: str = ""


@dataclass(slots=True)
class TradeFillRecord:
    symbol: str
    trade_id: int
    order_id: int | None
    client_order_id: str
    side: Side
    position_side: PositionSide
    qty: Decimal
    price: Decimal
    quote_qty: Decimal
    maker: bool
    reduce_only: bool
    commission: Decimal
    commission_asset: str
    realized_pnl: Decimal
    trade_time_ms: int
    event_time_ms: int


@dataclass(slots=True)
class PositionSnapshot:
    symbol: str
    position_side: PositionSide
    amount: Decimal
    entry_price: Decimal
    break_even_price: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    margin_type: str
    isolated_wallet: Decimal


@dataclass(slots=True)
class AccountSnapshot:
    balances: dict[str, Decimal] = field(default_factory=dict)
    positions: dict[tuple[str, PositionSide], PositionSnapshot] = field(default_factory=dict)
    last_event_time_ms: int = 0
    reason: str = ""


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    normalized_price: Decimal | None
    normalized_qty: Decimal | None
    notional: Decimal | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OrderProposal:
    symbol: str
    side: Side
    position_side: PositionSide
    order_type: OrderType
    tif: TimeInForce
    qty: Decimal
    price: Decimal | None = None
    reduce_only: bool = False
    close_position: bool = False
    working_type: WorkingType = WorkingType.CONTRACT_PRICE
    client_id: str = ""


@dataclass(slots=True)
class AlgoOrderProposal:
    symbol: str
    side: Side
    position_side: PositionSide
    order_type: OrderType
    qty: Decimal | None
    trigger_price: Decimal
    price: Decimal | None = None
    tif: TimeInForce = TimeInForce.GTC
    reduce_only: bool = True
    close_position: bool = False
    working_type: WorkingType = WorkingType.CONTRACT_PRICE
    client_algo_id: str = ""
    activate_price: Decimal | None = None
    callback_rate: Decimal | None = None
    price_protect: bool = False


@dataclass(slots=True)
class RiskDecision:
    allow_new_entry: bool
    allow_reduce_only: bool
    hard_reasons: list[str] = field(default_factory=list)
    soft_warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class APICallResult:
    data: Any
    headers: dict[str, str]

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.domain.enums import AlgoStatus, OrderStatus, OrderType, PositionSide, Side, WorkingType
from btcusdt_bot.domain.models import AccountSnapshot, AlgoOrderRecord, OrderRecord, PositionSnapshot, TradeFillRecord


def _d(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _position_side(value: object) -> PositionSide:
    raw = str(value or "BOTH").upper()
    try:
        return PositionSide(raw)
    except ValueError:
        return PositionSide.BOTH


TRADE_FILL_RETENTION_MS = 7 * 24 * 60 * 60 * 1000


def _order_record_from_rest(row: dict[str, Any]) -> tuple[str, OrderRecord] | None:
    client_id = str(row.get("clientOrderId") or row.get("origClientOrderId") or row.get("orderId") or "")
    if not client_id:
        return None
    record = OrderRecord(
        symbol=str(row.get("symbol", "")),
        side=Side(str(row.get("side", "BUY"))),
        position_side=_position_side(row.get("positionSide", "BOTH")),
        order_type=OrderType(str(row.get("type", row.get("origType", "LIMIT")))),
        status=OrderStatus(str(row.get("status", "NEW"))),
        tif=str(row.get("timeInForce", "")),
        order_id=int(row["orderId"]) if row.get("orderId") not in {None, "", "None"} else None,
        client_order_id=client_id,
        qty=_d(row.get("origQty", row.get("quantity", "0"))),
        executed_qty=_d(row.get("executedQty", row.get("cumQty", "0"))),
        price=_d(row.get("price", "0")),
        avg_price=_d(row.get("avgPrice", row.get("averagePrice", "0"))),
        reduce_only=_bool(row.get("reduceOnly", False)),
        close_position=_bool(row.get("closePosition", False)),
        update_time_ms=int(row.get("updateTime", row.get("time", 0))),
    )
    return client_id, record


def _algo_order_record_from_rest(row: dict[str, Any]) -> tuple[str, AlgoOrderRecord] | None:
    client_algo_id = str(row.get("clientAlgoId") or row.get("algoId") or "")
    if not client_algo_id:
        return None
    record = AlgoOrderRecord(
        symbol=str(row.get("symbol", "")),
        side=Side(str(row.get("side", "BUY"))),
        position_side=_position_side(row.get("positionSide", "BOTH")),
        order_type=OrderType(str(row.get("orderType", row.get("type", "STOP_MARKET")))),
        status=AlgoStatus(str(row.get("algoStatus", row.get("status", "NEW")))),
        tif=str(row.get("timeInForce", "")),
        algo_id=int(row["algoId"]) if row.get("algoId") not in {None, "", "None"} else None,
        client_algo_id=client_algo_id,
        qty=_d(row.get("quantity", row.get("origQty", "0"))),
        executed_qty=_d(row.get("actualQuantity", row.get("executedQty", "0"))),
        price=_d(row.get("price", row.get("tpPrice", row.get("slPrice", "0")))),
        avg_price=_d(row.get("actualPrice", row.get("avgPrice", "0"))),
        trigger_price=_d(
            row.get(
                "triggerPrice",
                row.get("tpTriggerPrice", row.get("slTriggerPrice", row.get("activatePrice", "0"))),
            )
        ),
        reduce_only=_bool(row.get("reduceOnly", False)),
        close_position=_bool(row.get("closePosition", False)),
        working_type=WorkingType(str(row.get("workingType", "CONTRACT_PRICE"))),
        update_time_ms=int(row.get("updateTime", row.get("createTime", 0))),
        reject_reason=str(row.get("rejectReason", row.get("rm", ""))),
    )
    return client_algo_id, record


@dataclass(slots=True)
class RuntimeState:
    normal_orders: dict[str, OrderRecord] = field(default_factory=dict)
    algo_orders: dict[str, AlgoOrderRecord] = field(default_factory=dict)
    account: AccountSnapshot = field(default_factory=AccountSnapshot)
    order_rate_limit_headers: dict[str, str] = field(default_factory=dict)
    last_private_event_type: str = ""
    last_account_config_update: dict[str, Any] = field(default_factory=dict)
    listen_key_expired_at_ms: int | None = None
    last_reconcile_at_ms: int | None = None
    last_reconcile_mismatch_count: int | None = None
    last_bootstrap_at_ms: int | None = None
    last_bootstrap_summary: dict[str, Any] = field(default_factory=dict)
    symbol_config: dict[str, Any] = field(default_factory=dict)
    leverage_brackets: dict[str, Any] = field(default_factory=dict)
    commission_rate: dict[str, Any] = field(default_factory=dict)
    latest_contract_info: dict[str, Any] = field(default_factory=dict)
    latest_crowding_snapshot: dict[str, Any] = field(default_factory=dict)
    latest_book_ticker: dict[str, Any] = field(default_factory=dict)
    latest_depth_snapshot: dict[str, Any] = field(default_factory=dict)
    latest_rpi_depth_snapshot: dict[str, Any] = field(default_factory=dict)
    trade_fills: dict[int, TradeFillRecord] = field(default_factory=dict)

    @property
    def open_normal_orders(self) -> int:
        return sum(
            1
            for order in self.normal_orders.values()
            if order.status in {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED}
        )

    @property
    def open_algo_orders(self) -> int:
        return sum(
            1
            for order in self.algo_orders.values()
            if order.status in {AlgoStatus.NEW, AlgoStatus.TRIGGERING, AlgoStatus.TRIGGERED}
        )


class StateStore:
    def __init__(self) -> None:
        self._state = RuntimeState()

    @property
    def state(self) -> RuntimeState:
        return self._state

    def snapshot(self) -> RuntimeState:
        return deepcopy(self._state)

    def replace_runtime_state(self, runtime_state: RuntimeState) -> None:
        self._state = deepcopy(runtime_state)

    def set_normal_order_record(self, client_order_id: str, record: OrderRecord) -> None:
        self._state.normal_orders[client_order_id] = deepcopy(record)

    def set_algo_order_record(self, client_algo_id: str, record: AlgoOrderRecord) -> None:
        self._state.algo_orders[client_algo_id] = deepcopy(record)

    def patch_symbol_config(self, snapshot: dict[str, Any]) -> None:
        self._state.symbol_config = deepcopy(snapshot)

    def patch_leverage_brackets(self, snapshot: dict[str, Any]) -> None:
        self._state.leverage_brackets = deepcopy(snapshot)

    def patch_commission_rate(self, snapshot: dict[str, Any]) -> None:
        self._state.commission_rate = deepcopy(snapshot)

    def patch_contract_info(self, snapshot: dict[str, Any]) -> None:
        current = deepcopy(self._state.latest_contract_info)
        current.update(deepcopy(snapshot))
        if "bks" in snapshot:
            current["bks"] = deepcopy(snapshot.get("bks") or [])
        self._state.latest_contract_info = current

    def patch_crowding_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._state.latest_crowding_snapshot = deepcopy(snapshot)

    def patch_book_ticker(self, snapshot: dict[str, Any]) -> None:
        self._state.latest_book_ticker = deepcopy(snapshot)

    def patch_depth_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._state.latest_depth_snapshot = deepcopy(snapshot)

    def patch_rpi_depth_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._state.latest_rpi_depth_snapshot = deepcopy(snapshot)

    def _prune_trade_fills(self, *, latest_event_time_ms: int) -> None:
        cutoff_ms = latest_event_time_ms - TRADE_FILL_RETENTION_MS
        self._state.trade_fills = {
            trade_id: record
            for trade_id, record in self._state.trade_fills.items()
            if record.trade_time_ms >= cutoff_ms
        }

    def _ingest_trade_fill_from_order_event(self, event: dict[str, object]) -> None:
        payload = event["o"]
        execution_type = str(payload.get("x", ""))
        last_fill_qty = _d(payload.get("l", "0"))
        trade_id_raw = payload.get("t")
        trade_id = int(trade_id_raw) if str(trade_id_raw) not in {"", "0", "None", "-1"} else 0
        if execution_type != "TRADE" and last_fill_qty <= 0:
            return
        if trade_id <= 0 or last_fill_qty <= 0:
            return
        last_fill_price = _d(payload.get("L", payload.get("ap", "0")))
        record = TradeFillRecord(
            symbol=str(payload.get("s", "")),
            trade_id=trade_id,
            order_id=int(payload["i"]) if str(payload.get("i", "")) not in {"", "None"} else None,
            client_order_id=str(payload.get("c", "")),
            side=Side(str(payload.get("S", "BUY"))),
            position_side=_position_side(payload.get("ps", "BOTH")),
            qty=last_fill_qty,
            price=last_fill_price,
            quote_qty=last_fill_price * last_fill_qty,
            maker=_bool(payload.get("m", False)),
            reduce_only=_bool(payload.get("R", False)),
            commission=_d(payload.get("n", "0")),
            commission_asset=str(payload.get("N", "")),
            realized_pnl=_d(payload.get("rp", "0")),
            trade_time_ms=int(payload.get("T", event.get("E", 0))),
            event_time_ms=int(event.get("E", payload.get("T", 0))),
        )
        self._state.trade_fills[trade_id] = record
        self._prune_trade_fills(latest_event_time_ms=record.event_time_ms)

    def patch_balance(self, asset: str, amount: Decimal) -> None:
        self._state.account.balances[asset] = amount

    def patch_position(self, snapshot: PositionSnapshot) -> None:
        key = (snapshot.symbol, snapshot.position_side)
        self._state.account.positions[key] = deepcopy(snapshot)

    def clear_position(self, symbol: str, position_side: PositionSide = PositionSide.BOTH) -> None:
        self._state.account.positions.pop((symbol, position_side), None)

    def mark_normal_order_terminal(
        self,
        client_order_id: str,
        *,
        status: OrderStatus = OrderStatus.EXPIRED,
        event_time_ms: int | None = None,
    ) -> None:
        record = self._state.normal_orders.get(client_order_id)
        if record is None:
            return
        record.status = status
        record.update_time_ms = int(event_time_ms or now_ms())
        self._state.account.last_event_time_ms = max(self._state.account.last_event_time_ms, record.update_time_ms)

    def mark_algo_order_terminal(
        self,
        client_algo_id: str,
        *,
        status: AlgoStatus = AlgoStatus.EXPIRED,
        event_time_ms: int | None = None,
    ) -> None:
        record = self._state.algo_orders.get(client_algo_id)
        if record is None:
            return
        record.status = status
        record.update_time_ms = int(event_time_ms or now_ms())
        self._state.account.last_event_time_ms = max(self._state.account.last_event_time_ms, record.update_time_ms)

    def current_position_qty(self, symbol: str, position_side: PositionSide = PositionSide.BOTH) -> Decimal:
        position = self._state.account.positions.get((symbol, position_side))
        return position.amount if position is not None else Decimal("0")

    def ingest_headers(self, headers: dict[str, str]) -> None:
        for key, value in headers.items():
            if key.upper().startswith("X-MBX-ORDER-COUNT-"):
                self._state.order_rate_limit_headers[key] = value

    def apply_order_trade_update(self, event: dict[str, object]) -> None:
        payload = event["o"]
        client_id = str(payload["c"])
        record = OrderRecord(
            symbol=str(payload["s"]),
            side=Side(str(payload["S"])),
            position_side=_position_side(payload.get("ps", "BOTH")),
            order_type=OrderType(str(payload["o"])),
            status=OrderStatus(str(payload["X"])),
            tif=str(payload.get("f", "")),
            order_id=int(payload["i"]) if str(payload.get("i", "")) not in {"", "None"} else None,
            client_order_id=client_id,
            qty=_d(payload.get("q", "0")),
            executed_qty=_d(payload.get("z", "0")),
            price=_d(payload.get("p", "0")),
            avg_price=_d(payload.get("ap", "0")),
            reduce_only=_bool(payload.get("R", False)),
            close_position=_bool(payload.get("cp", False)),
            update_time_ms=int(event["E"]),
        )
        self._state.normal_orders[client_id] = record
        self._ingest_trade_fill_from_order_event(event)
        self._state.account.last_event_time_ms = int(event["E"])
        self._state.last_private_event_type = "ORDER_TRADE_UPDATE"

    def apply_algo_update(self, event: dict[str, object]) -> None:
        payload = event["o"]
        client_algo_id = str(payload["caid"])
        record = AlgoOrderRecord(
            symbol=str(payload["s"]),
            side=Side(str(payload["S"])),
            position_side=_position_side(payload.get("ps", "BOTH")),
            order_type=OrderType(str(payload["o"])),
            status=AlgoStatus(str(payload["X"])),
            tif=str(payload.get("f", "")),
            algo_id=int(payload["aid"]) if str(payload.get("aid", "")) not in {"", "None"} else None,
            client_algo_id=client_algo_id,
            qty=_d(payload.get("q", "0")),
            executed_qty=_d(payload.get("aq", "0")),
            price=_d(payload.get("p", "0")),
            avg_price=_d(payload.get("ap", "0")),
            trigger_price=_d(payload.get("tp", payload.get("triggerPrice", "0"))),
            reduce_only=_bool(payload.get("R", payload.get("reduceOnly", False))),
            close_position=_bool(payload.get("cp", payload.get("closePosition", False))),
            working_type=WorkingType(str(payload.get("wt", payload.get("workingType", "CONTRACT_PRICE")))),
            update_time_ms=int(event["E"]),
            reject_reason=str(payload.get("rm", "")),
        )
        self._state.algo_orders[client_algo_id] = record
        self._state.account.last_event_time_ms = int(event["E"])
        self._state.last_private_event_type = "ALGO_UPDATE"

    def apply_account_update(self, event: dict[str, object]) -> None:
        payload = event["a"]
        balances = payload.get("B", [])
        positions = payload.get("P", [])

        for balance in balances:
            asset = str(balance["a"])
            self._state.account.balances[asset] = _d(balance["wb"])

        for pos in positions:
            key = (str(pos["s"]), _position_side(pos.get("ps", "BOTH")))
            self._state.account.positions[key] = PositionSnapshot(
                symbol=str(pos["s"]),
                position_side=_position_side(pos.get("ps", "BOTH")),
                amount=_d(pos.get("pa", "0")),
                entry_price=_d(pos.get("ep", "0")),
                break_even_price=_d(pos.get("bep", "0")),
                realized_pnl=_d(pos.get("cr", "0")),
                unrealized_pnl=_d(pos.get("up", "0")),
                margin_type=str(pos.get("mt", "")),
                isolated_wallet=_d(pos.get("iw", "0")),
            )

        self._state.account.reason = str(payload.get("m", ""))
        self._state.account.last_event_time_ms = int(event["E"])
        self._state.last_private_event_type = "ACCOUNT_UPDATE"

    def apply_account_config_update(self, event: dict[str, object]) -> None:
        self._state.last_account_config_update = deepcopy(event)
        self._state.account.last_event_time_ms = int(event.get("E", self._state.account.last_event_time_ms))
        self._state.last_private_event_type = "ACCOUNT_CONFIG_UPDATE"

    def mark_listen_key_expired(self, event: dict[str, object]) -> None:
        event_time_ms = int(event.get("E", 0))
        self._state.listen_key_expired_at_ms = event_time_ms
        self._state.account.last_event_time_ms = max(self._state.account.last_event_time_ms, event_time_ms)
        self._state.last_private_event_type = "listenKeyExpired"

    def mark_reconcile_result(self, *, checked_at_ms: int, mismatch_count: int) -> None:
        self._state.last_reconcile_at_ms = int(checked_at_ms)
        self._state.last_reconcile_mismatch_count = max(0, int(mismatch_count))

    def apply_account_v3_snapshot(self, snapshot: dict[str, Any]) -> None:
        balances = snapshot.get("assets") or snapshot.get("balances") or []
        for balance in balances:
            asset = str(balance.get("asset", balance.get("a", "")))
            if not asset:
                continue
            wallet_balance = balance.get("walletBalance", balance.get("wb", balance.get("balance", "0")))
            self._state.account.balances[asset] = _d(wallet_balance)

        positions = snapshot.get("positions", [])
        if positions:
            self.apply_position_risk_v3_snapshot(positions)

    def apply_position_risk_v3_snapshot(self, rows: Iterable[dict[str, Any]] | dict[str, Any]) -> None:
        iterable: Iterable[dict[str, Any]] = [rows] if isinstance(rows, dict) else rows
        for row in iterable:
            symbol = str(row.get("symbol", row.get("s", "")))
            if not symbol:
                continue
            position_side = _position_side(row.get("positionSide", row.get("ps", "BOTH")))
            key = (symbol, position_side)
            self._state.account.positions[key] = PositionSnapshot(
                symbol=symbol,
                position_side=position_side,
                amount=_d(row.get("positionAmt", row.get("pa", "0"))),
                entry_price=_d(row.get("entryPrice", row.get("ep", "0"))),
                break_even_price=_d(row.get("breakEvenPrice", row.get("bep", "0"))),
                realized_pnl=_d(row.get("realizedPnl", row.get("cr", "0"))),
                unrealized_pnl=_d(row.get("unRealizedProfit", row.get("up", "0"))),
                margin_type=str(row.get("marginType", row.get("mt", ""))),
                isolated_wallet=_d(row.get("isolatedWallet", row.get("iw", "0"))),
            )

    def upsert_normal_order_from_rest(self, row: dict[str, Any]) -> None:
        parsed = _order_record_from_rest(row)
        if parsed is None:
            return
        client_id, record = parsed
        self._state.normal_orders[client_id] = record

    def upsert_algo_order_from_rest(self, row: dict[str, Any]) -> None:
        parsed = _algo_order_record_from_rest(row)
        if parsed is None:
            return
        client_algo_id, record = parsed
        self._state.algo_orders[client_algo_id] = record

    def replace_normal_orders_from_rest(self, rows: Iterable[dict[str, Any]]) -> None:
        self._state.normal_orders = {}
        for row in rows:
            self.upsert_normal_order_from_rest(row)

    def replace_algo_orders_from_rest(self, rows: Iterable[dict[str, Any]]) -> None:
        self._state.algo_orders = {}
        for row in rows:
            self.upsert_algo_order_from_rest(row)

    def mark_bootstrap(self, *, event_time_ms: int, summary: dict[str, Any]) -> None:
        self._state.last_bootstrap_at_ms = event_time_ms
        self._state.last_bootstrap_summary = deepcopy(summary)
        self._state.account.last_event_time_ms = max(self._state.account.last_event_time_ms, event_time_ms)

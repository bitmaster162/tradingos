from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.rest_client import BinanceRESTClient
from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.domain.enums import AlgoStatus, OrderStatus, PositionSide
from btcusdt_bot.execution.query_resolver import QueryResolver
from btcusdt_bot.state.store import RuntimeState, StateStore

if TYPE_CHECKING:
    from btcusdt_bot.reconcile_daemon import ReconcileReport


@dataclass(slots=True)
class HealAction:
    kind: str
    key: str
    outcome: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class TargetedHealResult:
    checked_at_ms: int
    symbol: str
    applied: bool = False
    actions: list[HealAction] = field(default_factory=list)

    @property
    def action_count(self) -> int:
        return len(self.actions)


class TargetedReconcileHealer:
    def __init__(
        self,
        config: BotConfig,
        *,
        client: BinanceRESTClient,
        store: StateStore,
    ) -> None:
        self.config = config
        self.client = client
        self.store = store
        self.query_resolver = QueryResolver(client=client, store=store)

    def heal(self, report: ReconcileReport, exchange_state: RuntimeState) -> TargetedHealResult:
        result = TargetedHealResult(checked_at_ms=now_ms(), symbol=self.config.symbol)
        self._heal_normal_orders(report, exchange_state, result)
        self._heal_algo_orders(report, exchange_state, result)
        self._heal_positions(report, exchange_state, result)
        self._heal_balances(report, exchange_state, result)
        self._heal_config(report, exchange_state, result)
        result.applied = result.action_count > 0
        return result

    def _heal_normal_orders(
        self,
        report: ReconcileReport,
        exchange_state: RuntimeState,
        result: TargetedHealResult,
    ) -> None:
        for key in report.normal_only_exchange:
            record = exchange_state.normal_orders.get(key)
            if record is None:
                continue
            self.store.set_normal_order_record(key, record)
            result.actions.append(HealAction(kind="normal_order", key=key, outcome="copied_from_exchange_snapshot"))

        keys_to_query = {row.key for row in report.normal_mismatches}
        keys_to_query.update(report.normal_only_local)
        for key in sorted(keys_to_query):
            resolution = self.query_resolver.query_normal(symbol=self.config.symbol, client_order_id=key)
            if resolution.found:
                result.actions.append(
                    HealAction(
                        kind="normal_order",
                        key=key,
                        outcome="hydrated_from_query",
                        details={"requested_by": resolution.requested_by},
                    )
                )
                continue
            if resolution.not_found:
                status = _normal_missing_status(self.store.state.normal_orders.get(key))
                self.store.mark_normal_order_terminal(key, status=status, event_time_ms=result.checked_at_ms)
                result.actions.append(
                    HealAction(
                        kind="normal_order",
                        key=key,
                        outcome="marked_terminal_after_not_found",
                        details={"status": status},
                    )
                )
                continue
            result.actions.append(
                HealAction(kind="normal_order", key=key, outcome="query_error", details=resolution.error or {})
            )

    def _heal_algo_orders(
        self,
        report: ReconcileReport,
        exchange_state: RuntimeState,
        result: TargetedHealResult,
    ) -> None:
        for key in report.algo_only_exchange:
            record = exchange_state.algo_orders.get(key)
            if record is None:
                continue
            self.store.set_algo_order_record(key, record)
            result.actions.append(HealAction(kind="algo_order", key=key, outcome="copied_from_exchange_snapshot"))

        keys_to_query = {row.key for row in report.algo_mismatches}
        keys_to_query.update(report.algo_only_local)
        for key in sorted(keys_to_query):
            resolution = self.query_resolver.query_algo(symbol=self.config.symbol, client_algo_id=key)
            if resolution.found:
                result.actions.append(
                    HealAction(
                        kind="algo_order",
                        key=key,
                        outcome="hydrated_from_query",
                        details={"requested_by": resolution.requested_by},
                    )
                )
                continue
            if resolution.not_found:
                status = _algo_missing_status(self.store.state.algo_orders.get(key))
                self.store.mark_algo_order_terminal(key, status=status, event_time_ms=result.checked_at_ms)
                result.actions.append(
                    HealAction(
                        kind="algo_order",
                        key=key,
                        outcome="marked_terminal_after_not_found",
                        details={"status": status},
                    )
                )
                continue
            result.actions.append(HealAction(kind="algo_order", key=key, outcome="query_error", details=resolution.error or {}))

    def _heal_positions(
        self,
        report: ReconcileReport,
        exchange_state: RuntimeState,
        result: TargetedHealResult,
    ) -> None:
        seen_keys: set[tuple[str, PositionSide]] = set()
        for mismatch in report.position_mismatches:
            key = (mismatch.symbol, PositionSide(mismatch.position_side))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            exchange_position = exchange_state.account.positions.get(key)
            if exchange_position is None or exchange_position.amount == 0:
                self.store.clear_position(*key)
                outcome = "cleared_to_zero"
                details = {"exchange_amount": "0", "exchange_entry_price": "0"}
            else:
                self.store.patch_position(exchange_position)
                outcome = "patched_from_exchange_snapshot"
                details = {
                    "exchange_amount": str(exchange_position.amount),
                    "exchange_entry_price": str(exchange_position.entry_price),
                }
            result.actions.append(HealAction(kind="position", key=f"{key[0]}:{key[1]}", outcome=outcome, details=details))

    def _heal_balances(
        self,
        report: ReconcileReport,
        exchange_state: RuntimeState,
        result: TargetedHealResult,
    ) -> None:
        for mismatch in report.balance_mismatches:
            amount = exchange_state.account.balances.get(mismatch.asset, Decimal("0"))
            self.store.patch_balance(mismatch.asset, amount)
            result.actions.append(
                HealAction(
                    kind="balance",
                    key=mismatch.asset,
                    outcome="patched_from_exchange_snapshot",
                    details={"exchange_balance": str(amount)},
                )
            )

    def _heal_config(
        self,
        report: ReconcileReport,
        exchange_state: RuntimeState,
        result: TargetedHealResult,
    ) -> None:
        for mismatch in report.config_mismatches:
            if mismatch.kind == "symbol_config":
                self.store.patch_symbol_config(exchange_state.symbol_config)
                details = {"exchange_symbol": str(exchange_state.symbol_config.get("symbol", ""))}
            elif mismatch.kind == "leverage_brackets":
                self.store.patch_leverage_brackets(exchange_state.leverage_brackets)
                details = {"exchange_symbol": str(exchange_state.leverage_brackets.get("symbol", ""))}
            elif mismatch.kind == "commission_rate":
                self.store.patch_commission_rate(exchange_state.commission_rate)
                details = {"exchange_symbol": str(exchange_state.commission_rate.get("symbol", ""))}
            elif mismatch.kind == "contract_info":
                self.store.patch_contract_info(exchange_state.latest_contract_info)
                details = {
                    "exchange_symbol": str(exchange_state.latest_contract_info.get("s", "")),
                    "exchange_status": str(exchange_state.latest_contract_info.get("cs", "")),
                    "exchange_bracket_count": len(exchange_state.latest_contract_info.get("bks") or []),
                }
            else:
                continue
            result.actions.append(
                HealAction(
                    kind="config",
                    key=mismatch.kind,
                    outcome="patched_from_exchange_snapshot",
                    details=details,
                )
            )


def _normal_missing_status(record: object | None) -> OrderStatus:
    if record is None:
        return OrderStatus.EXPIRED
    executed_qty = getattr(record, "executed_qty", Decimal("0"))
    qty = getattr(record, "qty", Decimal("0"))
    if qty > 0 and executed_qty >= qty:
        return OrderStatus.FILLED
    if executed_qty > 0:
        return OrderStatus.EXPIRED
    return OrderStatus.CANCELED


def _algo_missing_status(record: object | None) -> AlgoStatus:
    if record is None:
        return AlgoStatus.EXPIRED
    executed_qty = getattr(record, "executed_qty", Decimal("0"))
    qty = getattr(record, "qty", Decimal("0"))
    if qty > 0 and executed_qty >= qty:
        return AlgoStatus.FINISHED
    if executed_qty > 0:
        return AlgoStatus.EXPIRED
    return AlgoStatus.CANCELED

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from btcusdt_bot.bootstrap.reconcile import BootstrapSynchronizer, BootstrapResult
from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.rest_client import BinanceRESTClient
from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.domain.enums import AlgoStatus, OrderStatus
from btcusdt_bot.reconcile_healer import TargetedReconcileHealer
from btcusdt_bot.state.store import RuntimeState, StateStore
from btcusdt_bot.storage.jsonl import JSONLWriter


OPEN_NORMAL_STATUSES = {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED}
OPEN_ALGO_STATUSES = {AlgoStatus.NEW, AlgoStatus.TRIGGERING, AlgoStatus.TRIGGERED}


@dataclass(slots=True)
class OrderMismatch:
    key: str
    local_status: str
    exchange_status: str
    local_executed_qty: Decimal
    exchange_executed_qty: Decimal


@dataclass(slots=True)
class PositionMismatch:
    symbol: str
    position_side: str
    local_amount: Decimal
    exchange_amount: Decimal
    local_entry_price: Decimal
    exchange_entry_price: Decimal


@dataclass(slots=True)
class BalanceMismatch:
    asset: str
    local_balance: Decimal
    exchange_balance: Decimal


@dataclass(slots=True)
class ConfigMismatch:
    kind: str
    local_digest: str
    exchange_digest: str


@dataclass(slots=True)
class ReconcileReport:
    checked_at_ms: int
    symbol: str
    local_open_normal_orders: int
    exchange_open_normal_orders: int
    local_open_algo_orders: int
    exchange_open_algo_orders: int
    normal_only_local: list[str] = field(default_factory=list)
    normal_only_exchange: list[str] = field(default_factory=list)
    normal_mismatches: list[OrderMismatch] = field(default_factory=list)
    algo_only_local: list[str] = field(default_factory=list)
    algo_only_exchange: list[str] = field(default_factory=list)
    algo_mismatches: list[OrderMismatch] = field(default_factory=list)
    position_mismatches: list[PositionMismatch] = field(default_factory=list)
    balance_mismatches: list[BalanceMismatch] = field(default_factory=list)
    config_mismatches: list[ConfigMismatch] = field(default_factory=list)
    quantitative_lock: bool = False
    cooling_off: bool = False
    local_last_private_event_type: str = ""
    local_listen_key_expired_at_ms: int | None = None
    healed: bool = False
    heal_mode: str = ""
    heal_actions_count: int = 0
    remaining_mismatch_count: int = 0

    @property
    def mismatch_count(self) -> int:
        return (
            len(self.normal_only_local)
            + len(self.normal_only_exchange)
            + len(self.normal_mismatches)
            + len(self.algo_only_local)
            + len(self.algo_only_exchange)
            + len(self.algo_mismatches)
            + len(self.position_mismatches)
            + len(self.balance_mismatches)
            + len(self.config_mismatches)
        )

    @property
    def has_divergence(self) -> bool:
        return self.mismatch_count > 0


@dataclass(slots=True)
class ReconcileStatus:
    iterations: int = 0
    mismatches_detected: int = 0
    heals_applied: int = 0
    targeted_heals_applied: int = 0
    full_replaces_applied: int = 0
    last_report_path: str = ""
    last_checked_at_ms: int = 0
    last_error: str = ""
    quantitative_lock: bool = False
    cooling_off: bool = False
    last_heal_mode: str = ""
    last_remaining_mismatches: int = 0


class ReconcileDaemon:
    def __init__(
        self,
        config: BotConfig,
        *,
        client: BinanceRESTClient,
        store: StateStore,
        writer: JSONLWriter,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.store = store
        self.writer = writer
        self.logger = logger or logging.getLogger("btcusdt_bot.reconcile")
        self.status = ReconcileStatus()

    async def run(
        self,
        *,
        interval_seconds: float,
        max_iterations: int | None = None,
        heal_on_divergence: bool = False,
        targeted_heal_on_divergence: bool = False,
    ) -> ReconcileStatus:
        while True:
            try:
                report = await asyncio.to_thread(
                    self.run_once,
                    heal_on_divergence=heal_on_divergence,
                    targeted_heal_on_divergence=targeted_heal_on_divergence,
                )
                self.status.iterations += 1
                self.status.last_checked_at_ms = report.checked_at_ms
                self.status.quantitative_lock = report.quantitative_lock
                self.status.cooling_off = report.cooling_off
                self.status.last_heal_mode = report.heal_mode
                self.status.last_remaining_mismatches = report.remaining_mismatch_count
                if report.has_divergence:
                    self.status.mismatches_detected += report.mismatch_count
                if report.healed:
                    self.status.heals_applied += 1
                    if "targeted" in report.heal_mode:
                        self.status.targeted_heals_applied += 1
                    if "full_replace" in report.heal_mode:
                        self.status.full_replaces_applied += 1
                self.status.last_report_path = str(
                    self.writer.append_record(
                        "reconcile",
                        f"{self.config.symbol.lower()}_report",
                        report,
                        event_time_ms=report.checked_at_ms,
                    )
                )
                self.writer.write_json("reconcile/latest_report.json", report)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.status.last_error = str(exc)
                self.logger.exception("reconcile daemon iteration failed")

            if max_iterations is not None and self.status.iterations >= max_iterations:
                return self.status
            await asyncio.sleep(max(0.1, interval_seconds))

    def run_once(
        self,
        *,
        heal_on_divergence: bool = False,
        targeted_heal_on_divergence: bool = False,
    ) -> ReconcileReport:
        local_snapshot = self.store.snapshot()
        exchange_snapshot, bootstrap_result = self._fresh_exchange_snapshot()
        report = diff_runtime_states(
            local_snapshot,
            exchange_snapshot,
            symbol=self.config.symbol,
            bootstrap_result=bootstrap_result,
        )
        report.remaining_mismatch_count = report.mismatch_count

        def finish() -> ReconcileReport:
            self.store.mark_reconcile_result(
                checked_at_ms=report.checked_at_ms,
                mismatch_count=report.remaining_mismatch_count,
            )
            return report

        needs_heal = report.has_divergence or local_snapshot.listen_key_expired_at_ms is not None
        if not needs_heal:
            return finish()

        if targeted_heal_on_divergence:
            healer = TargetedReconcileHealer(self.config, client=self.client, store=self.store)
            targeted = healer.heal(report, exchange_snapshot)
            if targeted.applied:
                report.healed = True
                report.heal_mode = "targeted"
                report.heal_actions_count = targeted.action_count
                after_targeted = diff_runtime_states(
                    self.store.snapshot(),
                    exchange_snapshot,
                    symbol=self.config.symbol,
                    bootstrap_result=bootstrap_result,
                )
                report.remaining_mismatch_count = after_targeted.mismatch_count
                if report.remaining_mismatch_count == 0 and local_snapshot.listen_key_expired_at_ms is None:
                    self.logger.warning(
                        "reconcile daemon applied targeted heal",
                        extra={
                            "symbol": self.config.symbol,
                            "mismatch_count": report.mismatch_count,
                            "heal_actions_count": report.heal_actions_count,
                        },
                    )
                    return finish()

        if heal_on_divergence:
            self.store.replace_runtime_state(exchange_snapshot)
            report.healed = True
            report.heal_mode = f"{report.heal_mode}+full_replace" if report.heal_mode else "full_replace"
            report.heal_actions_count += 1
            post_replace = diff_runtime_states(
                self.store.snapshot(),
                exchange_snapshot,
                symbol=self.config.symbol,
                bootstrap_result=bootstrap_result,
            )
            report.remaining_mismatch_count = post_replace.mismatch_count
            self.logger.warning(
                "reconcile daemon applied full runtime-state replace",
                extra={
                    "symbol": self.config.symbol,
                    "mismatch_count": report.mismatch_count,
                    "listen_key_expired_at_ms": local_snapshot.listen_key_expired_at_ms,
                },
            )
        return finish()

    def _fresh_exchange_snapshot(self) -> tuple[RuntimeState, BootstrapResult]:
        fresh_store = StateStore()
        synchronizer = BootstrapSynchronizer(self.config, client=self.client, store=fresh_store)
        bootstrap_result, _ = synchronizer.sync()
        return fresh_store.snapshot(), bootstrap_result


def diff_runtime_states(
    local_state: RuntimeState,
    exchange_state: RuntimeState,
    *,
    symbol: str,
    bootstrap_result: BootstrapResult | None = None,
) -> ReconcileReport:
    report = ReconcileReport(
        checked_at_ms=now_ms(),
        symbol=symbol,
        local_open_normal_orders=local_state.open_normal_orders,
        exchange_open_normal_orders=exchange_state.open_normal_orders,
        local_open_algo_orders=local_state.open_algo_orders,
        exchange_open_algo_orders=exchange_state.open_algo_orders,
        quantitative_lock=bootstrap_result.quantitative_lock if bootstrap_result is not None else False,
        cooling_off=bootstrap_result.cooling_off if bootstrap_result is not None else False,
        local_last_private_event_type=local_state.last_private_event_type,
        local_listen_key_expired_at_ms=local_state.listen_key_expired_at_ms,
    )

    local_open_normal = {
        key: value for key, value in local_state.normal_orders.items() if value.status in OPEN_NORMAL_STATUSES
    }
    exchange_open_normal = {
        key: value for key, value in exchange_state.normal_orders.items() if value.status in OPEN_NORMAL_STATUSES
    }
    local_open_algo = {
        key: value for key, value in local_state.algo_orders.items() if value.status in OPEN_ALGO_STATUSES
    }
    exchange_open_algo = {
        key: value for key, value in exchange_state.algo_orders.items() if value.status in OPEN_ALGO_STATUSES
    }

    report.normal_only_local = sorted(set(local_open_normal) - set(exchange_open_normal))
    report.normal_only_exchange = sorted(set(exchange_open_normal) - set(local_open_normal))
    for key in sorted(set(local_open_normal) & set(exchange_open_normal)):
        local = local_open_normal[key]
        exchange = exchange_open_normal[key]
        if local.status != exchange.status or local.executed_qty != exchange.executed_qty:
            report.normal_mismatches.append(
                OrderMismatch(
                    key=key,
                    local_status=local.status,
                    exchange_status=exchange.status,
                    local_executed_qty=local.executed_qty,
                    exchange_executed_qty=exchange.executed_qty,
                )
            )

    report.algo_only_local = sorted(set(local_open_algo) - set(exchange_open_algo))
    report.algo_only_exchange = sorted(set(exchange_open_algo) - set(local_open_algo))
    for key in sorted(set(local_open_algo) & set(exchange_open_algo)):
        local = local_open_algo[key]
        exchange = exchange_open_algo[key]
        if local.status != exchange.status or local.executed_qty != exchange.executed_qty:
            report.algo_mismatches.append(
                OrderMismatch(
                    key=key,
                    local_status=local.status,
                    exchange_status=exchange.status,
                    local_executed_qty=local.executed_qty,
                    exchange_executed_qty=exchange.executed_qty,
                )
            )

    local_positions = {
        key: value for key, value in local_state.account.positions.items() if value.symbol == symbol and value.amount != 0
    }
    exchange_positions = {
        key: value for key, value in exchange_state.account.positions.items() if value.symbol == symbol and value.amount != 0
    }
    for key in sorted(set(local_positions) | set(exchange_positions), key=lambda row: (row[0], row[1])):
        local = local_positions.get(key)
        exchange = exchange_positions.get(key)
        if local is None or exchange is None:
            report.position_mismatches.append(
                PositionMismatch(
                    symbol=key[0],
                    position_side=key[1],
                    local_amount=local.amount if local is not None else Decimal("0"),
                    exchange_amount=exchange.amount if exchange is not None else Decimal("0"),
                    local_entry_price=local.entry_price if local is not None else Decimal("0"),
                    exchange_entry_price=exchange.entry_price if exchange is not None else Decimal("0"),
                )
            )
            continue
        if local.amount != exchange.amount or local.entry_price != exchange.entry_price:
            report.position_mismatches.append(
                PositionMismatch(
                    symbol=key[0],
                    position_side=key[1],
                    local_amount=local.amount,
                    exchange_amount=exchange.amount,
                    local_entry_price=local.entry_price,
                    exchange_entry_price=exchange.entry_price,
                )
            )

    assets = sorted(set(local_state.account.balances) | set(exchange_state.account.balances))
    for asset in assets:
        local_balance = local_state.account.balances.get(asset, Decimal("0"))
        exchange_balance = exchange_state.account.balances.get(asset, Decimal("0"))
        if local_balance != exchange_balance:
            report.balance_mismatches.append(
                BalanceMismatch(
                    asset=asset,
                    local_balance=local_balance,
                    exchange_balance=exchange_balance,
                )
            )

    config_pairs = [
        ("symbol_config", local_state.symbol_config, exchange_state.symbol_config),
        ("leverage_brackets", local_state.leverage_brackets, exchange_state.leverage_brackets),
        ("commission_rate", local_state.commission_rate, exchange_state.commission_rate),
        ("contract_info", local_state.latest_contract_info, exchange_state.latest_contract_info),
    ]
    for kind, local_value, exchange_value in config_pairs:
        local_digest = _config_digest(local_value, kind=kind)
        exchange_digest = _config_digest(exchange_value, kind=kind)
        if local_digest != exchange_digest:
            report.config_mismatches.append(
                ConfigMismatch(
                    kind=kind,
                    local_digest=local_digest,
                    exchange_digest=exchange_digest,
                )
            )

    return report


def _config_digest(payload: Any, *, kind: str = "") -> str:
    if kind == "contract_info":
        payload = _contract_info_view(payload)
    return json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str)


def _contract_info_view(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return {
        "s": payload.get("s", ""),
        "cs": payload.get("cs", ""),
        "bks": payload.get("bks") or [],
    }

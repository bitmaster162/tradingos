from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.rest_client import BinanceRESTClient
from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.execution.validator import extract_symbol_filters
from btcusdt_bot.monitoring.intraday_protection import normalize_api_trading_status
from btcusdt_bot.state.store import StateStore


@dataclass(slots=True)
class BootstrapResult:
    synced_at_ms: int
    symbol: str
    balances: int
    positions: int
    open_normal_orders: int
    open_algo_orders: int
    quantitative_lock: bool
    cooling_off: bool
    symbol_filters: dict[str, Any]
    commission_rate: dict[str, Any]


class BootstrapSynchronizer:
    def __init__(self, config: BotConfig, *, client: BinanceRESTClient, store: StateStore) -> None:
        self.config = config
        self.client = client
        self.store = store

    def sync(self) -> tuple[BootstrapResult, dict[str, Any]]:
        exchange_info = self.client.exchange_info()
        symbol_config = self.client.symbol_config(self.config.symbol)
        account_v3 = self.client.account_v3()
        position_risk_v3 = self.client.position_risk_v3(self.config.symbol)
        leverage_brackets = self.client.leverage_brackets(self.config.symbol)
        api_trading_status = self.client.api_trading_status(self.config.symbol)
        commission_rate = self.client.commission_rate(self.config.symbol)
        open_orders = self.client.open_orders(self.config.symbol)
        open_algo_orders = self.client.open_algo_orders(self.config.symbol)

        exchange_symbol_row = _extract_exchange_symbol_row(exchange_info.data, self.config.symbol)
        normalized_symbol_config = _normalize_symbol_row(symbol_config.data, self.config.symbol)
        normalized_leverage_brackets = _normalize_symbol_row(leverage_brackets.data, self.config.symbol)
        normalized_commission_rate = _normalize_symbol_row(commission_rate.data, self.config.symbol)
        synthetic_contract_info = _build_synthetic_contract_info(exchange_symbol_row, normalized_leverage_brackets, self.config.symbol)

        self.store.ingest_headers(open_orders.headers)
        self.store.ingest_headers(open_algo_orders.headers)
        self.store.apply_account_v3_snapshot(account_v3.data)
        self.store.apply_position_risk_v3_snapshot(position_risk_v3.data)
        self.store.replace_normal_orders_from_rest(open_orders.data)
        self.store.replace_algo_orders_from_rest(open_algo_orders.data)
        self.store.patch_symbol_config(normalized_symbol_config)
        self.store.patch_leverage_brackets(normalized_leverage_brackets)
        self.store.patch_commission_rate(normalized_commission_rate)
        self.store.patch_contract_info(synthetic_contract_info)

        filters = extract_symbol_filters(exchange_info.data, self.config.symbol)
        quant_rules = normalize_api_trading_status(api_trading_status.data, self.config.symbol)
        synced_at_ms = now_ms()
        quantitative_lock = quant_rules.is_locked
        cooling_off = quantitative_lock or bool(quant_rules.planned_recover_time_ms)

        summary = {
            "synced_at_ms": synced_at_ms,
            "symbol": self.config.symbol,
            "quantitative_lock": quantitative_lock,
            "cooling_off": cooling_off,
            "open_normal_orders": self.store.state.open_normal_orders,
            "open_algo_orders": self.store.state.open_algo_orders,
            "balances": len(self.store.state.account.balances),
            "positions": len(self.store.state.account.positions),
        }
        self.store.mark_bootstrap(event_time_ms=synced_at_ms, summary=summary)

        raw_snapshot = {
            "exchange_info": exchange_info.data,
            "symbol_config": normalized_symbol_config,
            "account_v3": account_v3.data,
            "position_risk_v3": position_risk_v3.data,
            "leverage_brackets": normalized_leverage_brackets,
            "api_trading_status": api_trading_status.data,
            "commission_rate": normalized_commission_rate,
            "contract_info": synthetic_contract_info,
            "open_orders": open_orders.data,
            "open_algo_orders": open_algo_orders.data,
        }

        result = BootstrapResult(
            synced_at_ms=synced_at_ms,
            symbol=self.config.symbol,
            balances=len(self.store.state.account.balances),
            positions=len(self.store.state.account.positions),
            open_normal_orders=self.store.state.open_normal_orders,
            open_algo_orders=self.store.state.open_algo_orders,
            quantitative_lock=quantitative_lock,
            cooling_off=cooling_off,
            symbol_filters={
                "tick_size": filters.tick_size,
                "step_size": filters.step_size,
                "market_step_size": filters.market_step_size,
                "min_qty": filters.min_qty,
                "market_min_qty": filters.market_min_qty,
                "min_notional": filters.min_notional,
                "percent_price_up": filters.percent_price_up,
                "percent_price_down": filters.percent_price_down,
                "trigger_protect": filters.trigger_protect,
                "market_take_bound": filters.market_take_bound,
                "max_num_orders": filters.max_num_orders,
            },
            commission_rate=normalized_commission_rate,
        )
        return result, raw_snapshot



def _normalize_symbol_row(payload: Any, symbol: str) -> dict[str, Any]:
    if isinstance(payload, dict):
        if payload.get("symbol") == symbol:
            return dict(payload)
        if symbol in payload and isinstance(payload[symbol], dict):
            row = payload[symbol]
            return dict(row)
        data = payload.get("data")
        if isinstance(data, dict) and symbol in data and isinstance(data[symbol], dict):
            return dict(data[symbol])
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict) and row.get("symbol") == symbol:
                    return dict(row)
        if "symbol" in payload or not payload:
            return dict(payload)

    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict) and row.get("symbol") == symbol:
                return dict(row)
        for row in payload:
            if isinstance(row, dict):
                return dict(row)
    return {}


def _extract_exchange_symbol_row(payload: Any, symbol: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    symbols = payload.get("symbols")
    if isinstance(symbols, list):
        for row in symbols:
            if isinstance(row, dict) and row.get("symbol") == symbol:
                return dict(row)
    return {}


def _build_synthetic_contract_info(symbol_row: dict[str, Any], leverage_brackets: dict[str, Any], symbol: str) -> dict[str, Any]:
    status = str(symbol_row.get("status", symbol_row.get("contractStatus", "")))
    brackets = leverage_brackets.get("brackets") if isinstance(leverage_brackets, dict) else []
    if not isinstance(brackets, list):
        brackets = []
    return {
        "e": "contractInfo",
        "s": str(symbol_row.get("symbol", symbol)),
        "cs": status,
        "bks": brackets,
        "source": "bootstrap_synthetic",
    }

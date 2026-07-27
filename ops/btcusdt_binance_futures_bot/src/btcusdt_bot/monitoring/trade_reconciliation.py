from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
import json

_ZERO = Decimal("0")
_ONE = Decimal("1")


def _to_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _to_int(value: object) -> int:
    if value in {None, "", "None"}:
        return 0
    return int(value)


def load_json(path: str | Path) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid_json_payload")
    return payload


@dataclass(slots=True)
class TradeReconciliationThresholds:
    min_exchange_trade_count: int = 1
    max_missing_local_trade_ratio_reduce: Decimal = Decimal("0.10")
    max_missing_local_trade_ratio_observe: Decimal = Decimal("0.25")
    max_unmatched_local_trade_ratio_reduce: Decimal = Decimal("0.10")
    max_unmatched_local_trade_ratio_observe: Decimal = Decimal("0.25")
    max_missing_local_order_ratio_reduce: Decimal = Decimal("0.10")
    max_missing_local_order_ratio_observe: Decimal = Decimal("0.25")
    max_unmatched_local_order_ratio_reduce: Decimal = Decimal("0.10")
    max_unmatched_local_order_ratio_observe: Decimal = Decimal("0.25")
    max_realized_pnl_diff_usdt_reduce: Decimal = Decimal("1.00")
    max_realized_pnl_diff_usdt_observe: Decimal = Decimal("3.00")
    max_commission_abs_diff_usdt_reduce: Decimal = Decimal("0.25")
    max_commission_abs_diff_usdt_observe: Decimal = Decimal("1.00")
    max_quote_qty_abs_diff_usdt_reduce: Decimal = Decimal("25.00")
    max_quote_qty_abs_diff_usdt_observe: Decimal = Decimal("100.00")
    max_income_trade_realized_pnl_diff_usdt_reduce: Decimal = Decimal("1.00")
    max_income_trade_realized_pnl_diff_usdt_observe: Decimal = Decimal("3.00")
    max_income_trade_link_gap_ratio_reduce: Decimal = Decimal("0.10")
    max_income_trade_link_gap_ratio_observe: Decimal = Decimal("0.25")
    reduce_size_multiplier: Decimal = Decimal("0.60")


@dataclass(slots=True)
class TradeReconciliationDecision:
    action: str
    size_multiplier: Decimal = _ONE
    score: Decimal = _ZERO
    moderate_breaches: int = 0
    severe_breaches: int = 0
    reasons: list[str] = field(default_factory=list)
    compared_at_ms: int = 0
    lookback_start_ms: int = 0
    lookback_end_ms: int = 0
    window_mode: str = "lookback"
    session_started_at_ms: int = 0
    exchange_trade_count: int = 0
    local_trade_fill_count: int = 0
    matched_trade_count: int = 0
    missing_local_trade_count: int = 0
    unmatched_local_trade_count: int = 0
    missing_local_trade_ratio: Decimal = _ZERO
    unmatched_local_trade_ratio: Decimal = _ZERO
    exchange_order_count: int = 0
    local_order_count: int = 0
    matched_order_count: int = 0
    missing_local_order_count: int = 0
    unmatched_local_order_count: int = 0
    missing_local_order_ratio: Decimal = _ZERO
    unmatched_local_order_ratio: Decimal = _ZERO
    exchange_realized_pnl_usdt: Decimal = _ZERO
    exchange_income_realized_pnl_usdt: Decimal = _ZERO
    local_realized_pnl_usdt: Decimal = _ZERO
    realized_pnl_diff_usdt: Decimal = _ZERO
    exchange_commission_usdt: Decimal = _ZERO
    exchange_income_commission_usdt: Decimal = _ZERO
    local_commission_usdt: Decimal = _ZERO
    commission_abs_diff_usdt: Decimal = _ZERO
    exchange_quote_qty_usdt: Decimal = _ZERO
    local_quote_qty_usdt: Decimal = _ZERO
    quote_qty_abs_diff_usdt: Decimal = _ZERO
    exchange_funding_fee_usdt: Decimal = _ZERO
    income_trade_realized_pnl_diff_usdt: Decimal = _ZERO
    income_trade_linked_count: int = 0
    income_trade_unlinked_count: int = 0
    income_trade_link_gap_ratio: Decimal = _ZERO

    @property
    def observe_only(self) -> bool:
        return self.action == "observe_only"

    @property
    def reduce_size(self) -> bool:
        return self.action == "reduce_size"

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "TradeReconciliationDecision":
        return cls(
            action=str(payload.get("action", "trade") or "trade"),
            size_multiplier=_to_decimal(payload.get("size_multiplier")) or _ONE,
            score=_to_decimal(payload.get("score")) or _ZERO,
            moderate_breaches=int(payload.get("moderate_breaches", 0) or 0),
            severe_breaches=int(payload.get("severe_breaches", 0) or 0),
            reasons=[str(item) for item in payload.get("reasons", [])],
            compared_at_ms=int(payload.get("compared_at_ms", 0) or 0),
            lookback_start_ms=int(payload.get("lookback_start_ms", 0) or 0),
            lookback_end_ms=int(payload.get("lookback_end_ms", 0) or 0),
            window_mode=str(payload.get("window_mode", "lookback") or "lookback"),
            session_started_at_ms=int(payload.get("session_started_at_ms", 0) or 0),
            exchange_trade_count=int(payload.get("exchange_trade_count", 0) or 0),
            local_trade_fill_count=int(payload.get("local_trade_fill_count", 0) or 0),
            matched_trade_count=int(payload.get("matched_trade_count", 0) or 0),
            missing_local_trade_count=int(payload.get("missing_local_trade_count", 0) or 0),
            unmatched_local_trade_count=int(payload.get("unmatched_local_trade_count", 0) or 0),
            missing_local_trade_ratio=_to_decimal(payload.get("missing_local_trade_ratio")) or _ZERO,
            unmatched_local_trade_ratio=_to_decimal(payload.get("unmatched_local_trade_ratio")) or _ZERO,
            exchange_order_count=int(payload.get("exchange_order_count", 0) or 0),
            local_order_count=int(payload.get("local_order_count", 0) or 0),
            matched_order_count=int(payload.get("matched_order_count", 0) or 0),
            missing_local_order_count=int(payload.get("missing_local_order_count", 0) or 0),
            unmatched_local_order_count=int(payload.get("unmatched_local_order_count", 0) or 0),
            missing_local_order_ratio=_to_decimal(payload.get("missing_local_order_ratio")) or _ZERO,
            unmatched_local_order_ratio=_to_decimal(payload.get("unmatched_local_order_ratio")) or _ZERO,
            exchange_realized_pnl_usdt=_to_decimal(payload.get("exchange_realized_pnl_usdt")) or _ZERO,
            exchange_income_realized_pnl_usdt=_to_decimal(payload.get("exchange_income_realized_pnl_usdt")) or _ZERO,
            local_realized_pnl_usdt=_to_decimal(payload.get("local_realized_pnl_usdt")) or _ZERO,
            realized_pnl_diff_usdt=_to_decimal(payload.get("realized_pnl_diff_usdt")) or _ZERO,
            exchange_commission_usdt=_to_decimal(payload.get("exchange_commission_usdt")) or _ZERO,
            exchange_income_commission_usdt=_to_decimal(payload.get("exchange_income_commission_usdt")) or _ZERO,
            local_commission_usdt=_to_decimal(payload.get("local_commission_usdt")) or _ZERO,
            commission_abs_diff_usdt=_to_decimal(payload.get("commission_abs_diff_usdt")) or _ZERO,
            exchange_quote_qty_usdt=_to_decimal(payload.get("exchange_quote_qty_usdt")) or _ZERO,
            local_quote_qty_usdt=_to_decimal(payload.get("local_quote_qty_usdt")) or _ZERO,
            quote_qty_abs_diff_usdt=_to_decimal(payload.get("quote_qty_abs_diff_usdt")) or _ZERO,
            exchange_funding_fee_usdt=_to_decimal(payload.get("exchange_funding_fee_usdt")) or _ZERO,
            income_trade_realized_pnl_diff_usdt=_to_decimal(payload.get("income_trade_realized_pnl_diff_usdt")) or _ZERO,
            income_trade_linked_count=int(payload.get("income_trade_linked_count", 0) or 0),
            income_trade_unlinked_count=int(payload.get("income_trade_unlinked_count", 0) or 0),
            income_trade_link_gap_ratio=_to_decimal(payload.get("income_trade_link_gap_ratio")) or _ZERO,
        )


@dataclass(slots=True)
class TradeReconciliationStatus:
    iterations: int = 0
    decisions_written: int = 0
    reduce_size_decisions: int = 0
    observe_only_decisions: int = 0
    last_action: str = ""
    last_path: str = ""
    last_error: str = ""
    last_window_mode: str = ""
    last_source_mode: str = ""
    session_started_at_ms: int = 0
    exchange_trade_count: int = 0
    local_trade_fill_count: int = 0
    missing_local_order_ratio: Decimal | None = None
    realized_pnl_diff_usdt: Decimal | None = None
    commission_abs_diff_usdt: Decimal | None = None
    quote_qty_abs_diff_usdt: Decimal | None = None
    income_trade_link_gap_ratio: Decimal | None = None
    user_trade_archive_coverage_ratio: Decimal | None = None
    income_archive_coverage_ratio: Decimal | None = None
    archive_gap_count: int = 0
    archived_user_trade_count: int = 0
    live_user_trade_count: int = 0
    archived_income_count: int = 0
    live_income_count: int = 0


def _extract_local_trade_fills(
    runtime_state: dict[str, object],
    *,
    symbol: str,
    since_ms: int,
    until_ms: int,
) -> list[dict[str, object]]:
    raw = runtime_state.get("trade_fills")
    if isinstance(raw, dict):
        iterable = raw.values()
    elif isinstance(raw, list):
        iterable = raw
    else:
        iterable = []
    results: list[dict[str, object]] = []
    for item in iterable:
        if not isinstance(item, dict):
            continue
        if str(item.get("symbol", "")) != symbol:
            continue
        trade_time_ms = int(item.get("trade_time_ms", item.get("event_time_ms", 0)) or 0)
        if trade_time_ms < since_ms or trade_time_ms > until_ms:
            continue
        results.append(item)
    return results


def _sum_decimal(rows: list[dict[str, object]], key: str) -> Decimal:
    total = _ZERO
    for row in rows:
        total += _to_decimal(row.get(key)) or _ZERO
    return total


def _sum_abs_decimal(rows: list[dict[str, object]], key: str) -> Decimal:
    total = _ZERO
    for row in rows:
        total += abs(_to_decimal(row.get(key)) or _ZERO)
    return total


def _filter_income(rows: list[dict[str, object]], income_type: str) -> list[dict[str, object]]:
    return [row for row in rows if str(row.get("incomeType", "")) == income_type]


def _trade_id(row: dict[str, object]) -> int:
    return _to_int(row.get("id", row.get("tradeId", row.get("trade_id", 0))))


def _local_order_key(row: dict[str, object]) -> str | None:
    order_id = _to_int(row.get("order_id"))
    if order_id > 0:
        return f"oid:{order_id}"
    client_order_id = str(row.get("client_order_id", "")).strip()
    if client_order_id:
        return f"cid:{client_order_id}"
    trade_id = _trade_id(row)
    if trade_id > 0:
        return f"tid:{trade_id}"
    return None


def _exchange_order_key(row: dict[str, object]) -> str | None:
    order_id = _to_int(row.get("orderId"))
    if order_id > 0:
        return f"oid:{order_id}"
    trade_id = _trade_id(row)
    if trade_id > 0:
        return f"tid:{trade_id}"
    return None


def _unique_trade_ids(rows: list[dict[str, object]]) -> set[int]:
    return {trade_id for row in rows if (trade_id := _trade_id(row)) > 0}


def _unique_order_keys(rows: list[dict[str, object]], *, local: bool) -> set[str]:
    keys: set[str] = set()
    for row in rows:
        key = _local_order_key(row) if local else _exchange_order_key(row)
        if key is not None:
            keys.add(key)
    return keys


def _income_trade_ids(rows: list[dict[str, object]]) -> set[int]:
    relevant = [
        row
        for row in rows
        if str(row.get("incomeType", "")) in {"REALIZED_PNL", "COMMISSION"}
    ]
    return {
        trade_id
        for row in relevant
        if (trade_id := _to_int(row.get("tradeId"))) > 0
    }


def evaluate_trade_reconciliation(
    *,
    runtime_state: dict[str, object],
    symbol: str,
    exchange_user_trades: list[dict[str, object]],
    exchange_income_rows: list[dict[str, object]],
    lookback_start_ms: int,
    lookback_end_ms: int,
    thresholds: TradeReconciliationThresholds | None = None,
    compared_at_ms: int = 0,
    window_mode: str = "lookback",
    session_started_at_ms: int = 0,
) -> TradeReconciliationDecision:
    thresholds = thresholds or TradeReconciliationThresholds()
    reasons: list[str] = []
    moderate = 0
    severe = 0

    def flag(condition_reduce: bool, condition_observe: bool, reason_reduce: str, reason_observe: str) -> None:
        nonlocal moderate, severe
        if condition_observe:
            severe += 1
            reasons.append(reason_observe)
        elif condition_reduce:
            moderate += 1
            reasons.append(reason_reduce)

    local_fills = _extract_local_trade_fills(
        runtime_state,
        symbol=symbol,
        since_ms=lookback_start_ms,
        until_ms=lookback_end_ms,
    )
    local_trade_ids = _unique_trade_ids(local_fills)
    exchange_trade_ids = _unique_trade_ids(exchange_user_trades)
    local_order_keys = _unique_order_keys(local_fills, local=True)
    exchange_order_keys = _unique_order_keys(exchange_user_trades, local=False)
    income_link_trade_ids = _income_trade_ids(exchange_income_rows)

    missing_local_trade_count = len(exchange_trade_ids - local_trade_ids)
    unmatched_local_trade_count = len(local_trade_ids - exchange_trade_ids)
    matched_trade_count = len(exchange_trade_ids & local_trade_ids)
    exchange_trade_count = len(exchange_trade_ids)
    local_trade_fill_count = len(local_trade_ids)

    if exchange_trade_count >= thresholds.min_exchange_trade_count:
        missing_local_trade_ratio = Decimal(missing_local_trade_count) / Decimal(exchange_trade_count)
    else:
        missing_local_trade_ratio = _ZERO
    if local_trade_fill_count > 0:
        unmatched_local_trade_ratio = Decimal(unmatched_local_trade_count) / Decimal(local_trade_fill_count)
    else:
        unmatched_local_trade_ratio = _ZERO

    missing_local_order_count = len(exchange_order_keys - local_order_keys)
    unmatched_local_order_count = len(local_order_keys - exchange_order_keys)
    matched_order_count = len(exchange_order_keys & local_order_keys)
    exchange_order_count = len(exchange_order_keys)
    local_order_count = len(local_order_keys)

    if exchange_order_count > 0:
        missing_local_order_ratio = Decimal(missing_local_order_count) / Decimal(exchange_order_count)
    else:
        missing_local_order_ratio = _ZERO
    if local_order_count > 0:
        unmatched_local_order_ratio = Decimal(unmatched_local_order_count) / Decimal(local_order_count)
    else:
        unmatched_local_order_ratio = _ZERO

    local_realized_pnl_usdt = _sum_decimal(local_fills, "realized_pnl")
    exchange_realized_pnl_usdt = _sum_decimal(exchange_user_trades, "realizedPnl")
    exchange_income_realized_pnl_usdt = _sum_decimal(_filter_income(exchange_income_rows, "REALIZED_PNL"), "income")
    local_commission_usdt = _sum_abs_decimal(local_fills, "commission")
    exchange_commission_usdt = _sum_abs_decimal(exchange_user_trades, "commission")
    exchange_income_commission_usdt = _sum_abs_decimal(_filter_income(exchange_income_rows, "COMMISSION"), "income")
    local_quote_qty_usdt = _sum_decimal(local_fills, "quote_qty")
    exchange_quote_qty_usdt = _sum_decimal(exchange_user_trades, "quoteQty")
    exchange_funding_fee_usdt = _sum_decimal(_filter_income(exchange_income_rows, "FUNDING_FEE"), "income")

    realized_pnl_reference = exchange_realized_pnl_usdt
    if exchange_trade_count == 0 and exchange_income_realized_pnl_usdt != _ZERO:
        realized_pnl_reference = exchange_income_realized_pnl_usdt
    realized_pnl_diff_usdt = abs(local_realized_pnl_usdt - realized_pnl_reference)

    commission_reference = exchange_commission_usdt
    if exchange_trade_count == 0 and exchange_income_commission_usdt != _ZERO:
        commission_reference = exchange_income_commission_usdt
    commission_abs_diff_usdt = abs(local_commission_usdt - commission_reference)

    quote_qty_abs_diff_usdt = abs(local_quote_qty_usdt - exchange_quote_qty_usdt)
    income_trade_realized_pnl_diff_usdt = abs(exchange_realized_pnl_usdt - exchange_income_realized_pnl_usdt)
    income_trade_linked_count = len(income_link_trade_ids & exchange_trade_ids)
    income_trade_unlinked_count = len(income_link_trade_ids - exchange_trade_ids)
    if income_link_trade_ids:
        income_trade_link_gap_ratio = Decimal(income_trade_unlinked_count) / Decimal(len(income_link_trade_ids))
    else:
        income_trade_link_gap_ratio = _ZERO

    if exchange_trade_count >= thresholds.min_exchange_trade_count:
        flag(
            missing_local_trade_ratio > thresholds.max_missing_local_trade_ratio_reduce,
            missing_local_trade_ratio > thresholds.max_missing_local_trade_ratio_observe,
            "missing_local_trade_ratio_above_reduce_threshold",
            "missing_local_trade_ratio_above_observe_threshold",
        )
    if local_trade_fill_count > 0 or exchange_trade_count > 0:
        flag(
            unmatched_local_trade_ratio > thresholds.max_unmatched_local_trade_ratio_reduce,
            unmatched_local_trade_ratio > thresholds.max_unmatched_local_trade_ratio_observe,
            "unmatched_local_trade_ratio_above_reduce_threshold",
            "unmatched_local_trade_ratio_above_observe_threshold",
        )
    if exchange_order_count > 0:
        flag(
            missing_local_order_ratio > thresholds.max_missing_local_order_ratio_reduce,
            missing_local_order_ratio > thresholds.max_missing_local_order_ratio_observe,
            "missing_local_order_ratio_above_reduce_threshold",
            "missing_local_order_ratio_above_observe_threshold",
        )
    if local_order_count > 0 or exchange_order_count > 0:
        flag(
            unmatched_local_order_ratio > thresholds.max_unmatched_local_order_ratio_reduce,
            unmatched_local_order_ratio > thresholds.max_unmatched_local_order_ratio_observe,
            "unmatched_local_order_ratio_above_reduce_threshold",
            "unmatched_local_order_ratio_above_observe_threshold",
        )
    flag(
        realized_pnl_diff_usdt > thresholds.max_realized_pnl_diff_usdt_reduce,
        realized_pnl_diff_usdt > thresholds.max_realized_pnl_diff_usdt_observe,
        "realized_pnl_diff_above_reduce_threshold",
        "realized_pnl_diff_above_observe_threshold",
    )
    flag(
        commission_abs_diff_usdt > thresholds.max_commission_abs_diff_usdt_reduce,
        commission_abs_diff_usdt > thresholds.max_commission_abs_diff_usdt_observe,
        "commission_diff_above_reduce_threshold",
        "commission_diff_above_observe_threshold",
    )
    if exchange_quote_qty_usdt != _ZERO or local_quote_qty_usdt != _ZERO:
        flag(
            quote_qty_abs_diff_usdt > thresholds.max_quote_qty_abs_diff_usdt_reduce,
            quote_qty_abs_diff_usdt > thresholds.max_quote_qty_abs_diff_usdt_observe,
            "quote_qty_diff_above_reduce_threshold",
            "quote_qty_diff_above_observe_threshold",
        )
    if exchange_income_realized_pnl_usdt != _ZERO or exchange_realized_pnl_usdt != _ZERO:
        flag(
            income_trade_realized_pnl_diff_usdt > thresholds.max_income_trade_realized_pnl_diff_usdt_reduce,
            income_trade_realized_pnl_diff_usdt > thresholds.max_income_trade_realized_pnl_diff_usdt_observe,
            "exchange_income_trade_realized_pnl_diff_above_reduce_threshold",
            "exchange_income_trade_realized_pnl_diff_above_observe_threshold",
        )
    if income_link_trade_ids:
        flag(
            income_trade_link_gap_ratio > thresholds.max_income_trade_link_gap_ratio_reduce,
            income_trade_link_gap_ratio > thresholds.max_income_trade_link_gap_ratio_observe,
            "income_trade_link_gap_ratio_above_reduce_threshold",
            "income_trade_link_gap_ratio_above_observe_threshold",
        )

    if severe > 0:
        action = "observe_only"
        size_multiplier = _ZERO
    elif moderate > 0:
        action = "reduce_size"
        size_multiplier = thresholds.reduce_size_multiplier
    else:
        action = "trade"
        size_multiplier = _ONE

    score = Decimal(moderate) + Decimal(severe) * Decimal("2")
    return TradeReconciliationDecision(
        action=action,
        size_multiplier=size_multiplier,
        score=score,
        moderate_breaches=moderate,
        severe_breaches=severe,
        reasons=reasons,
        compared_at_ms=compared_at_ms,
        lookback_start_ms=lookback_start_ms,
        lookback_end_ms=lookback_end_ms,
        window_mode=window_mode,
        session_started_at_ms=session_started_at_ms,
        exchange_trade_count=exchange_trade_count,
        local_trade_fill_count=local_trade_fill_count,
        matched_trade_count=matched_trade_count,
        missing_local_trade_count=missing_local_trade_count,
        unmatched_local_trade_count=unmatched_local_trade_count,
        missing_local_trade_ratio=missing_local_trade_ratio,
        unmatched_local_trade_ratio=unmatched_local_trade_ratio,
        exchange_order_count=exchange_order_count,
        local_order_count=local_order_count,
        matched_order_count=matched_order_count,
        missing_local_order_count=missing_local_order_count,
        unmatched_local_order_count=unmatched_local_order_count,
        missing_local_order_ratio=missing_local_order_ratio,
        unmatched_local_order_ratio=unmatched_local_order_ratio,
        exchange_realized_pnl_usdt=exchange_realized_pnl_usdt,
        exchange_income_realized_pnl_usdt=exchange_income_realized_pnl_usdt,
        local_realized_pnl_usdt=local_realized_pnl_usdt,
        realized_pnl_diff_usdt=realized_pnl_diff_usdt,
        exchange_commission_usdt=exchange_commission_usdt,
        exchange_income_commission_usdt=exchange_income_commission_usdt,
        local_commission_usdt=local_commission_usdt,
        commission_abs_diff_usdt=commission_abs_diff_usdt,
        exchange_quote_qty_usdt=exchange_quote_qty_usdt,
        local_quote_qty_usdt=local_quote_qty_usdt,
        quote_qty_abs_diff_usdt=quote_qty_abs_diff_usdt,
        exchange_funding_fee_usdt=exchange_funding_fee_usdt,
        income_trade_realized_pnl_diff_usdt=income_trade_realized_pnl_diff_usdt,
        income_trade_linked_count=income_trade_linked_count,
        income_trade_unlinked_count=income_trade_unlinked_count,
        income_trade_link_gap_ratio=income_trade_link_gap_ratio,
    )

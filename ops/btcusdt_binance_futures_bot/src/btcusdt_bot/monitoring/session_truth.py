from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

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


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes", "y"}
    return bool(value)


@dataclass(slots=True)
class SessionTruthThresholds:
    min_exchange_trade_count: int = 3
    min_quote_qty_usdt: Decimal = Decimal("1000")
    max_negative_net_realized_pnl_usdt_reduce: Decimal = Decimal("2.50")
    max_negative_net_realized_pnl_usdt_observe: Decimal = Decimal("10.00")
    max_negative_net_realized_bps_reduce: Decimal = Decimal("1.00")
    max_negative_net_realized_bps_observe: Decimal = Decimal("4.00")
    max_negative_net_per_trade_usdt_reduce: Decimal = Decimal("0.25")
    max_negative_net_per_trade_usdt_observe: Decimal = Decimal("1.00")
    min_maker_ratio_reduce: Decimal = Decimal("0.40")
    min_maker_ratio_observe: Decimal = Decimal("0.20")
    max_commission_bps_reduce: Decimal = Decimal("6.00")
    max_commission_bps_observe: Decimal = Decimal("10.00")
    max_negative_funding_bps_reduce: Decimal = Decimal("0.50")
    max_negative_funding_bps_observe: Decimal = Decimal("2.00")
    reduce_size_multiplier: Decimal = Decimal("0.60")


@dataclass(slots=True)
class SessionTruthDecision:
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
    sample_ready: bool = False
    exchange_trade_count: int = 0
    exchange_order_count: int = 0
    maker_trade_count: int = 0
    taker_trade_count: int = 0
    maker_ratio: Decimal = _ZERO
    exchange_quote_qty_usdt: Decimal = _ZERO
    exchange_realized_pnl_usdt: Decimal = _ZERO
    exchange_commission_abs_usdt: Decimal = _ZERO
    exchange_funding_fee_usdt: Decimal = _ZERO
    exchange_other_income_usdt: Decimal = _ZERO
    net_realized_pnl_usdt: Decimal = _ZERO
    gross_realized_bps: Decimal = _ZERO
    net_realized_bps: Decimal = _ZERO
    commission_bps: Decimal = _ZERO
    funding_bps: Decimal = _ZERO
    net_per_trade_usdt: Decimal = _ZERO

    @property
    def observe_only(self) -> bool:
        return self.action == "observe_only"

    @property
    def reduce_size(self) -> bool:
        return self.action == "reduce_size"

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "SessionTruthDecision":
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
            sample_ready=bool(payload.get("sample_ready", False)),
            exchange_trade_count=int(payload.get("exchange_trade_count", 0) or 0),
            exchange_order_count=int(payload.get("exchange_order_count", 0) or 0),
            maker_trade_count=int(payload.get("maker_trade_count", 0) or 0),
            taker_trade_count=int(payload.get("taker_trade_count", 0) or 0),
            maker_ratio=_to_decimal(payload.get("maker_ratio")) or _ZERO,
            exchange_quote_qty_usdt=_to_decimal(payload.get("exchange_quote_qty_usdt")) or _ZERO,
            exchange_realized_pnl_usdt=_to_decimal(payload.get("exchange_realized_pnl_usdt")) or _ZERO,
            exchange_commission_abs_usdt=_to_decimal(payload.get("exchange_commission_abs_usdt")) or _ZERO,
            exchange_funding_fee_usdt=_to_decimal(payload.get("exchange_funding_fee_usdt")) or _ZERO,
            exchange_other_income_usdt=_to_decimal(payload.get("exchange_other_income_usdt")) or _ZERO,
            net_realized_pnl_usdt=_to_decimal(payload.get("net_realized_pnl_usdt")) or _ZERO,
            gross_realized_bps=_to_decimal(payload.get("gross_realized_bps")) or _ZERO,
            net_realized_bps=_to_decimal(payload.get("net_realized_bps")) or _ZERO,
            commission_bps=_to_decimal(payload.get("commission_bps")) or _ZERO,
            funding_bps=_to_decimal(payload.get("funding_bps")) or _ZERO,
            net_per_trade_usdt=_to_decimal(payload.get("net_per_trade_usdt")) or _ZERO,
        )


@dataclass(slots=True)
class SessionTruthStatus:
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
    maker_ratio: Decimal | None = None
    net_realized_pnl_usdt: Decimal | None = None
    net_realized_bps: Decimal | None = None
    commission_bps: Decimal | None = None
    funding_bps: Decimal | None = None
    user_trade_archive_coverage_ratio: Decimal | None = None
    income_archive_coverage_ratio: Decimal | None = None
    archive_gap_count: int = 0
    archived_user_trade_count: int = 0
    live_user_trade_count: int = 0
    archived_income_count: int = 0
    live_income_count: int = 0


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


def _income_other(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    excluded = {"REALIZED_PNL", "COMMISSION", "FUNDING_FEE"}
    return [row for row in rows if str(row.get("incomeType", "")) not in excluded]


def _unique_order_count(rows: list[dict[str, object]]) -> int:
    order_ids: set[int] = set()
    for row in rows:
        order_id = _to_int(row.get("orderId"))
        if order_id > 0:
            order_ids.add(order_id)
    return len(order_ids)


def _maker_trade_count(rows: list[dict[str, object]]) -> int:
    count = 0
    for row in rows:
        if _bool(row.get("maker", row.get("m", False))):
            count += 1
    return count


def evaluate_session_truth(
    *,
    exchange_user_trades: list[dict[str, object]],
    exchange_income_rows: list[dict[str, object]],
    lookback_start_ms: int,
    lookback_end_ms: int,
    thresholds: SessionTruthThresholds | None = None,
    compared_at_ms: int = 0,
    window_mode: str = "lookback",
    session_started_at_ms: int = 0,
) -> SessionTruthDecision:
    thresholds = thresholds or SessionTruthThresholds()
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

    exchange_trade_count = len(exchange_user_trades)
    exchange_order_count = _unique_order_count(exchange_user_trades)
    maker_trade_count = _maker_trade_count(exchange_user_trades)
    taker_trade_count = max(0, exchange_trade_count - maker_trade_count)
    maker_ratio = (Decimal(maker_trade_count) / Decimal(exchange_trade_count)) if exchange_trade_count > 0 else _ZERO

    exchange_quote_qty_usdt = _sum_decimal(exchange_user_trades, "quoteQty")
    exchange_realized_pnl_usdt = _sum_decimal(exchange_user_trades, "realizedPnl")
    exchange_commission_abs_usdt = _sum_abs_decimal(exchange_user_trades, "commission")
    exchange_funding_fee_usdt = _sum_decimal(_filter_income(exchange_income_rows, "FUNDING_FEE"), "income")
    exchange_other_income_usdt = _sum_decimal(_income_other(exchange_income_rows), "income")

    net_realized_pnl_usdt = exchange_realized_pnl_usdt + exchange_funding_fee_usdt - exchange_commission_abs_usdt
    if exchange_quote_qty_usdt > _ZERO:
        gross_realized_bps = (exchange_realized_pnl_usdt / exchange_quote_qty_usdt) * Decimal("10000")
        net_realized_bps = (net_realized_pnl_usdt / exchange_quote_qty_usdt) * Decimal("10000")
        commission_bps = (exchange_commission_abs_usdt / exchange_quote_qty_usdt) * Decimal("10000")
        funding_bps = (exchange_funding_fee_usdt / exchange_quote_qty_usdt) * Decimal("10000")
    else:
        gross_realized_bps = _ZERO
        net_realized_bps = _ZERO
        commission_bps = _ZERO
        funding_bps = _ZERO
    net_per_trade_usdt = (net_realized_pnl_usdt / Decimal(exchange_trade_count)) if exchange_trade_count > 0 else _ZERO

    sample_ready = (
        exchange_trade_count >= thresholds.min_exchange_trade_count
        and exchange_quote_qty_usdt >= thresholds.min_quote_qty_usdt
    )

    if sample_ready:
        flag(
            net_realized_pnl_usdt < -thresholds.max_negative_net_realized_pnl_usdt_reduce,
            net_realized_pnl_usdt < -thresholds.max_negative_net_realized_pnl_usdt_observe,
            "net_realized_pnl_below_reduce_threshold",
            "net_realized_pnl_below_observe_threshold",
        )
        flag(
            net_realized_bps < -thresholds.max_negative_net_realized_bps_reduce,
            net_realized_bps < -thresholds.max_negative_net_realized_bps_observe,
            "net_realized_bps_below_reduce_threshold",
            "net_realized_bps_below_observe_threshold",
        )
        flag(
            net_per_trade_usdt < -thresholds.max_negative_net_per_trade_usdt_reduce,
            net_per_trade_usdt < -thresholds.max_negative_net_per_trade_usdt_observe,
            "net_per_trade_below_reduce_threshold",
            "net_per_trade_below_observe_threshold",
        )
        flag(
            maker_ratio < thresholds.min_maker_ratio_reduce,
            maker_ratio < thresholds.min_maker_ratio_observe,
            "maker_ratio_below_reduce_threshold",
            "maker_ratio_below_observe_threshold",
        )
        flag(
            commission_bps > thresholds.max_commission_bps_reduce,
            commission_bps > thresholds.max_commission_bps_observe,
            "commission_bps_above_reduce_threshold",
            "commission_bps_above_observe_threshold",
        )
        flag(
            funding_bps < -thresholds.max_negative_funding_bps_reduce,
            funding_bps < -thresholds.max_negative_funding_bps_observe,
            "funding_bps_below_reduce_threshold",
            "funding_bps_below_observe_threshold",
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
    return SessionTruthDecision(
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
        sample_ready=sample_ready,
        exchange_trade_count=exchange_trade_count,
        exchange_order_count=exchange_order_count,
        maker_trade_count=maker_trade_count,
        taker_trade_count=taker_trade_count,
        maker_ratio=maker_ratio,
        exchange_quote_qty_usdt=exchange_quote_qty_usdt,
        exchange_realized_pnl_usdt=exchange_realized_pnl_usdt,
        exchange_commission_abs_usdt=exchange_commission_abs_usdt,
        exchange_funding_fee_usdt=exchange_funding_fee_usdt,
        exchange_other_income_usdt=exchange_other_income_usdt,
        net_realized_pnl_usdt=net_realized_pnl_usdt,
        gross_realized_bps=gross_realized_bps,
        net_realized_bps=net_realized_bps,
        commission_bps=commission_bps,
        funding_bps=funding_bps,
        net_per_trade_usdt=net_per_trade_usdt,
    )

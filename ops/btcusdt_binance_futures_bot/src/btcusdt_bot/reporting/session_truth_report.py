from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from btcusdt_bot.monitoring.session_truth import SessionTruthDecision, SessionTruthThresholds, evaluate_session_truth

_ZERO = Decimal("0")


@dataclass(slots=True)
class SessionTruthBucket:
    bucket_index: int
    bucket_start_ms: int
    bucket_end_ms: int
    active: bool = False
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

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "SessionTruthBucket":
        return cls(
            bucket_index=int(payload.get("bucket_index", 0) or 0),
            bucket_start_ms=int(payload.get("bucket_start_ms", 0) or 0),
            bucket_end_ms=int(payload.get("bucket_end_ms", 0) or 0),
            active=bool(payload.get("active", False)),
            exchange_trade_count=int(payload.get("exchange_trade_count", 0) or 0),
            exchange_order_count=int(payload.get("exchange_order_count", 0) or 0),
            maker_trade_count=int(payload.get("maker_trade_count", 0) or 0),
            taker_trade_count=int(payload.get("taker_trade_count", 0) or 0),
            maker_ratio=Decimal(str(payload.get("maker_ratio", "0") or "0")),
            exchange_quote_qty_usdt=Decimal(str(payload.get("exchange_quote_qty_usdt", "0") or "0")),
            exchange_realized_pnl_usdt=Decimal(str(payload.get("exchange_realized_pnl_usdt", "0") or "0")),
            exchange_commission_abs_usdt=Decimal(str(payload.get("exchange_commission_abs_usdt", "0") or "0")),
            exchange_funding_fee_usdt=Decimal(str(payload.get("exchange_funding_fee_usdt", "0") or "0")),
            exchange_other_income_usdt=Decimal(str(payload.get("exchange_other_income_usdt", "0") or "0")),
            net_realized_pnl_usdt=Decimal(str(payload.get("net_realized_pnl_usdt", "0") or "0")),
            gross_realized_bps=Decimal(str(payload.get("gross_realized_bps", "0") or "0")),
            net_realized_bps=Decimal(str(payload.get("net_realized_bps", "0") or "0")),
            commission_bps=Decimal(str(payload.get("commission_bps", "0") or "0")),
            funding_bps=Decimal(str(payload.get("funding_bps", "0") or "0")),
            net_per_trade_usdt=Decimal(str(payload.get("net_per_trade_usdt", "0") or "0")),
        )


@dataclass(slots=True)
class SessionTruthReport:
    compared_at_ms: int
    lookback_start_ms: int
    lookback_end_ms: int
    window_mode: str = "lookback"
    session_started_at_ms: int = 0
    bucket_ms: int = 60 * 60 * 1000
    bucket_count: int = 0
    active_bucket_count: int = 0
    negative_bucket_count: int = 0
    negative_bucket_ratio: Decimal = _ZERO
    trailing_negative_bucket_streak: int = 0
    worst_bucket_net_realized_pnl_usdt: Decimal = _ZERO
    worst_bucket_net_realized_bps: Decimal = _ZERO
    best_bucket_net_realized_pnl_usdt: Decimal = _ZERO
    best_bucket_net_realized_bps: Decimal = _ZERO
    recent_bucket_net_realized_pnl_usdt: Decimal = _ZERO
    recent_bucket_net_realized_bps: Decimal = _ZERO
    recent_bucket_maker_ratio: Decimal = _ZERO
    recent_two_bucket_net_realized_pnl_usdt: Decimal = _ZERO
    recent_two_bucket_net_realized_bps: Decimal = _ZERO
    cumulative_drawdown_usdt: Decimal = _ZERO
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
    buckets: list[SessionTruthBucket] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "SessionTruthReport":
        raw_buckets = payload.get("buckets", [])
        buckets: list[SessionTruthBucket] = []
        if isinstance(raw_buckets, list):
            for item in raw_buckets:
                if isinstance(item, dict):
                    buckets.append(SessionTruthBucket.from_payload(item))
        return cls(
            compared_at_ms=int(payload.get("compared_at_ms", 0) or 0),
            lookback_start_ms=int(payload.get("lookback_start_ms", 0) or 0),
            lookback_end_ms=int(payload.get("lookback_end_ms", 0) or 0),
            window_mode=str(payload.get("window_mode", "lookback") or "lookback"),
            session_started_at_ms=int(payload.get("session_started_at_ms", 0) or 0),
            bucket_ms=int(payload.get("bucket_ms", 60 * 60 * 1000) or (60 * 60 * 1000)),
            bucket_count=int(payload.get("bucket_count", 0) or 0),
            active_bucket_count=int(payload.get("active_bucket_count", 0) or 0),
            negative_bucket_count=int(payload.get("negative_bucket_count", 0) or 0),
            negative_bucket_ratio=Decimal(str(payload.get("negative_bucket_ratio", "0") or "0")),
            trailing_negative_bucket_streak=int(payload.get("trailing_negative_bucket_streak", 0) or 0),
            worst_bucket_net_realized_pnl_usdt=Decimal(str(payload.get("worst_bucket_net_realized_pnl_usdt", "0") or "0")),
            worst_bucket_net_realized_bps=Decimal(str(payload.get("worst_bucket_net_realized_bps", "0") or "0")),
            best_bucket_net_realized_pnl_usdt=Decimal(str(payload.get("best_bucket_net_realized_pnl_usdt", "0") or "0")),
            best_bucket_net_realized_bps=Decimal(str(payload.get("best_bucket_net_realized_bps", "0") or "0")),
            recent_bucket_net_realized_pnl_usdt=Decimal(str(payload.get("recent_bucket_net_realized_pnl_usdt", "0") or "0")),
            recent_bucket_net_realized_bps=Decimal(str(payload.get("recent_bucket_net_realized_bps", "0") or "0")),
            recent_bucket_maker_ratio=Decimal(str(payload.get("recent_bucket_maker_ratio", "0") or "0")),
            recent_two_bucket_net_realized_pnl_usdt=Decimal(str(payload.get("recent_two_bucket_net_realized_pnl_usdt", "0") or "0")),
            recent_two_bucket_net_realized_bps=Decimal(str(payload.get("recent_two_bucket_net_realized_bps", "0") or "0")),
            cumulative_drawdown_usdt=Decimal(str(payload.get("cumulative_drawdown_usdt", "0") or "0")),
            exchange_trade_count=int(payload.get("exchange_trade_count", 0) or 0),
            exchange_order_count=int(payload.get("exchange_order_count", 0) or 0),
            maker_trade_count=int(payload.get("maker_trade_count", 0) or 0),
            taker_trade_count=int(payload.get("taker_trade_count", 0) or 0),
            maker_ratio=Decimal(str(payload.get("maker_ratio", "0") or "0")),
            exchange_quote_qty_usdt=Decimal(str(payload.get("exchange_quote_qty_usdt", "0") or "0")),
            exchange_realized_pnl_usdt=Decimal(str(payload.get("exchange_realized_pnl_usdt", "0") or "0")),
            exchange_commission_abs_usdt=Decimal(str(payload.get("exchange_commission_abs_usdt", "0") or "0")),
            exchange_funding_fee_usdt=Decimal(str(payload.get("exchange_funding_fee_usdt", "0") or "0")),
            exchange_other_income_usdt=Decimal(str(payload.get("exchange_other_income_usdt", "0") or "0")),
            net_realized_pnl_usdt=Decimal(str(payload.get("net_realized_pnl_usdt", "0") or "0")),
            gross_realized_bps=Decimal(str(payload.get("gross_realized_bps", "0") or "0")),
            net_realized_bps=Decimal(str(payload.get("net_realized_bps", "0") or "0")),
            commission_bps=Decimal(str(payload.get("commission_bps", "0") or "0")),
            funding_bps=Decimal(str(payload.get("funding_bps", "0") or "0")),
            net_per_trade_usdt=Decimal(str(payload.get("net_per_trade_usdt", "0") or "0")),
            buckets=buckets,
        )


def _metrics_thresholds() -> SessionTruthThresholds:
    huge = Decimal("1000000000")
    return SessionTruthThresholds(
        min_exchange_trade_count=1,
        min_quote_qty_usdt=_ZERO,
        max_negative_net_realized_pnl_usdt_reduce=huge,
        max_negative_net_realized_pnl_usdt_observe=huge,
        max_negative_net_realized_bps_reduce=huge,
        max_negative_net_realized_bps_observe=huge,
        max_negative_net_per_trade_usdt_reduce=huge,
        max_negative_net_per_trade_usdt_observe=huge,
        min_maker_ratio_reduce=_ZERO,
        min_maker_ratio_observe=_ZERO,
        max_commission_bps_reduce=huge,
        max_commission_bps_observe=huge,
        max_negative_funding_bps_reduce=huge,
        max_negative_funding_bps_observe=huge,
        reduce_size_multiplier=Decimal("1"),
    )


def _bucket_index(timestamp_ms: int, *, start_ms: int, bucket_ms: int, bucket_count: int) -> int:
    index = max(0, (int(timestamp_ms) - start_ms) // bucket_ms)
    return min(bucket_count - 1, index)


def build_session_truth_report(
    *,
    exchange_user_trades: list[dict[str, object]],
    exchange_income_rows: list[dict[str, object]],
    lookback_start_ms: int,
    lookback_end_ms: int,
    bucket_ms: int,
    compared_at_ms: int = 0,
    window_mode: str = "lookback",
    session_started_at_ms: int = 0,
) -> SessionTruthReport:
    bucket_ms = max(1, int(bucket_ms))
    if lookback_end_ms < lookback_start_ms:
        total_bucket_count = 0
    else:
        total_bucket_count = ((lookback_end_ms - lookback_start_ms) // bucket_ms) + 1

    thresholds = _metrics_thresholds()
    overall: SessionTruthDecision = evaluate_session_truth(
        exchange_user_trades=exchange_user_trades,
        exchange_income_rows=exchange_income_rows,
        lookback_start_ms=lookback_start_ms,
        lookback_end_ms=lookback_end_ms,
        thresholds=thresholds,
        compared_at_ms=compared_at_ms,
        window_mode=window_mode,
        session_started_at_ms=session_started_at_ms,
    )

    trades_by_bucket: dict[int, list[dict[str, object]]] = {index: [] for index in range(total_bucket_count)}
    income_by_bucket: dict[int, list[dict[str, object]]] = {index: [] for index in range(total_bucket_count)}

    for row in exchange_user_trades:
        timestamp_ms = int(row.get("time", 0) or 0)
        if timestamp_ms < lookback_start_ms or timestamp_ms > lookback_end_ms or total_bucket_count <= 0:
            continue
        trades_by_bucket[_bucket_index(timestamp_ms, start_ms=lookback_start_ms, bucket_ms=bucket_ms, bucket_count=total_bucket_count)].append(row)

    for row in exchange_income_rows:
        timestamp_ms = int(row.get("time", 0) or 0)
        if timestamp_ms < lookback_start_ms or timestamp_ms > lookback_end_ms or total_bucket_count <= 0:
            continue
        income_by_bucket[_bucket_index(timestamp_ms, start_ms=lookback_start_ms, bucket_ms=bucket_ms, bucket_count=total_bucket_count)].append(row)

    buckets: list[SessionTruthBucket] = []
    active_buckets: list[SessionTruthBucket] = []
    cumulative = _ZERO
    peak = _ZERO
    cumulative_drawdown = _ZERO

    for index in range(total_bucket_count):
        bucket_start_ms = lookback_start_ms + (index * bucket_ms)
        bucket_end_ms = min(lookback_end_ms, bucket_start_ms + bucket_ms - 1)
        bucket_decision = evaluate_session_truth(
            exchange_user_trades=trades_by_bucket.get(index, []),
            exchange_income_rows=income_by_bucket.get(index, []),
            lookback_start_ms=bucket_start_ms,
            lookback_end_ms=bucket_end_ms,
            thresholds=thresholds,
            compared_at_ms=bucket_end_ms,
            window_mode="bucket",
            session_started_at_ms=session_started_at_ms,
        )
        active = (
            bucket_decision.exchange_trade_count > 0
            or bucket_decision.exchange_quote_qty_usdt > _ZERO
            or bucket_decision.exchange_funding_fee_usdt != _ZERO
            or bucket_decision.exchange_other_income_usdt != _ZERO
        )
        bucket = SessionTruthBucket(
            bucket_index=index,
            bucket_start_ms=bucket_start_ms,
            bucket_end_ms=bucket_end_ms,
            active=active,
            exchange_trade_count=bucket_decision.exchange_trade_count,
            exchange_order_count=bucket_decision.exchange_order_count,
            maker_trade_count=bucket_decision.maker_trade_count,
            taker_trade_count=bucket_decision.taker_trade_count,
            maker_ratio=bucket_decision.maker_ratio,
            exchange_quote_qty_usdt=bucket_decision.exchange_quote_qty_usdt,
            exchange_realized_pnl_usdt=bucket_decision.exchange_realized_pnl_usdt,
            exchange_commission_abs_usdt=bucket_decision.exchange_commission_abs_usdt,
            exchange_funding_fee_usdt=bucket_decision.exchange_funding_fee_usdt,
            exchange_other_income_usdt=bucket_decision.exchange_other_income_usdt,
            net_realized_pnl_usdt=bucket_decision.net_realized_pnl_usdt,
            gross_realized_bps=bucket_decision.gross_realized_bps,
            net_realized_bps=bucket_decision.net_realized_bps,
            commission_bps=bucket_decision.commission_bps,
            funding_bps=bucket_decision.funding_bps,
            net_per_trade_usdt=bucket_decision.net_per_trade_usdt,
        )
        buckets.append(bucket)
        if active:
            active_buckets.append(bucket)
        cumulative += bucket.net_realized_pnl_usdt
        if cumulative > peak:
            peak = cumulative
        drawdown = peak - cumulative
        if drawdown > cumulative_drawdown:
            cumulative_drawdown = drawdown

    negative_bucket_count = sum(1 for bucket in active_buckets if bucket.net_realized_pnl_usdt < _ZERO)
    negative_bucket_ratio = (
        Decimal(negative_bucket_count) / Decimal(len(active_buckets)) if active_buckets else _ZERO
    )

    trailing_negative_bucket_streak = 0
    for bucket in reversed(active_buckets):
        if bucket.net_realized_pnl_usdt < _ZERO:
            trailing_negative_bucket_streak += 1
        else:
            break

    if active_buckets:
        worst_bucket = min(active_buckets, key=lambda bucket: (bucket.net_realized_pnl_usdt, bucket.net_realized_bps))
        best_bucket = max(active_buckets, key=lambda bucket: (bucket.net_realized_pnl_usdt, bucket.net_realized_bps))
        recent_bucket = active_buckets[-1]
        recent_two = active_buckets[-2:] if len(active_buckets) >= 2 else active_buckets[-1:]
        recent_two_bucket_net_realized_pnl_usdt = sum((bucket.net_realized_pnl_usdt for bucket in recent_two), _ZERO)
        recent_two_quote_qty_usdt = sum((bucket.exchange_quote_qty_usdt for bucket in recent_two), _ZERO)
        recent_two_bucket_net_realized_bps = (
            (recent_two_bucket_net_realized_pnl_usdt / recent_two_quote_qty_usdt) * Decimal("10000")
            if recent_two_quote_qty_usdt > _ZERO
            else _ZERO
        )
    else:
        worst_bucket = SessionTruthBucket(bucket_index=0, bucket_start_ms=0, bucket_end_ms=0)
        best_bucket = SessionTruthBucket(bucket_index=0, bucket_start_ms=0, bucket_end_ms=0)
        recent_bucket = SessionTruthBucket(bucket_index=0, bucket_start_ms=0, bucket_end_ms=0)
        recent_two_bucket_net_realized_pnl_usdt = _ZERO
        recent_two_bucket_net_realized_bps = _ZERO

    return SessionTruthReport(
        compared_at_ms=compared_at_ms,
        lookback_start_ms=lookback_start_ms,
        lookback_end_ms=lookback_end_ms,
        window_mode=window_mode,
        session_started_at_ms=session_started_at_ms,
        bucket_ms=bucket_ms,
        bucket_count=total_bucket_count,
        active_bucket_count=len(active_buckets),
        negative_bucket_count=negative_bucket_count,
        negative_bucket_ratio=negative_bucket_ratio,
        trailing_negative_bucket_streak=trailing_negative_bucket_streak,
        worst_bucket_net_realized_pnl_usdt=worst_bucket.net_realized_pnl_usdt,
        worst_bucket_net_realized_bps=worst_bucket.net_realized_bps,
        best_bucket_net_realized_pnl_usdt=best_bucket.net_realized_pnl_usdt,
        best_bucket_net_realized_bps=best_bucket.net_realized_bps,
        recent_bucket_net_realized_pnl_usdt=recent_bucket.net_realized_pnl_usdt,
        recent_bucket_net_realized_bps=recent_bucket.net_realized_bps,
        recent_bucket_maker_ratio=recent_bucket.maker_ratio,
        recent_two_bucket_net_realized_pnl_usdt=recent_two_bucket_net_realized_pnl_usdt,
        recent_two_bucket_net_realized_bps=recent_two_bucket_net_realized_bps,
        cumulative_drawdown_usdt=cumulative_drawdown,
        exchange_trade_count=overall.exchange_trade_count,
        exchange_order_count=overall.exchange_order_count,
        maker_trade_count=overall.maker_trade_count,
        taker_trade_count=overall.taker_trade_count,
        maker_ratio=overall.maker_ratio,
        exchange_quote_qty_usdt=overall.exchange_quote_qty_usdt,
        exchange_realized_pnl_usdt=overall.exchange_realized_pnl_usdt,
        exchange_commission_abs_usdt=overall.exchange_commission_abs_usdt,
        exchange_funding_fee_usdt=overall.exchange_funding_fee_usdt,
        exchange_other_income_usdt=overall.exchange_other_income_usdt,
        net_realized_pnl_usdt=overall.net_realized_pnl_usdt,
        gross_realized_bps=overall.gross_realized_bps,
        net_realized_bps=overall.net_realized_bps,
        commission_bps=overall.commission_bps,
        funding_bps=overall.funding_bps,
        net_per_trade_usdt=overall.net_per_trade_usdt,
        buckets=buckets,
    )

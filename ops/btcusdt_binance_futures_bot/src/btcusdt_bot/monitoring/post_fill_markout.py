from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Iterable

from btcusdt_bot.authoritative.archive import AuthoritativeArchive, USER_TRADES_DATASET, iter_buckets
from btcusdt_bot.connectors.signing import now_ms


BOOK_MID = "book_mid"
MARK_PRICE = "mark_price"
_VALID_REFERENCE_SOURCES = {BOOK_MID, MARK_PRICE}
_BPS = Decimal("10000")
_TWO = Decimal("2")
_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class PostFillMarkoutConfig:
    archive_root: Path
    market_root: Path
    symbol: str
    start_ms: int
    end_ms: int
    horizon_ms: int
    max_pre_fill_age_ms: int
    max_post_horizon_delay_ms: int
    reference_source: str = BOOK_MID

    def validate(self) -> None:
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms_before_start_ms")
        if self.horizon_ms <= 0:
            raise ValueError("horizon_ms_must_be_positive")
        if self.max_pre_fill_age_ms < 0:
            raise ValueError("max_pre_fill_age_ms_must_be_non_negative")
        if self.max_post_horizon_delay_ms < 0:
            raise ValueError("max_post_horizon_delay_ms_must_be_non_negative")
        if self.reference_source not in _VALID_REFERENCE_SOURCES:
            raise ValueError(f"unsupported_reference_source:{self.reference_source}")


@dataclass(frozen=True, slots=True)
class ReferenceSnapshot:
    event_time_ms: int
    received_at_ms: int | None
    price: Decimal
    source: str
    sequence: int


@dataclass(frozen=True, slots=True)
class NormalizedFill:
    symbol: str
    trade_id: int
    order_id: int | None
    trade_time_ms: int
    side: str
    maker: bool | None
    reduce_only: bool | None
    qty: Decimal
    quote_qty: Decimal
    price: Decimal


@dataclass(slots=True)
class PostFillMarkoutObservation:
    symbol: str
    trade_id: int
    order_id: int | None
    trade_time_ms: int
    side: str
    maker: bool | None
    reduce_only: bool | None
    qty: Decimal
    quote_qty: Decimal
    fill_price: Decimal
    reference_source: str
    metric_kind: str
    causality_mode: str
    horizon_ms: int
    target_time_ms: int
    pre_reference_event_time_ms: int
    pre_reference_received_at_ms: int | None
    pre_reference_age_ms: int
    post_reference_event_time_ms: int
    post_reference_received_at_ms: int | None
    post_reference_delay_ms: int
    pre_reference_price: Decimal
    post_reference_price: Decimal
    effective_spread_bps: Decimal | None
    realized_spread_bps: Decimal | None
    price_impact_bps: Decimal | None
    signed_markout_bps: Decimal
    markout_class: str


@dataclass(slots=True)
class PostFillMarkoutReport:
    schema_version: int
    generated_at_ms: int
    decision: str
    symbol: str
    start_ms: int
    end_ms: int
    horizon_ms: int
    max_pre_fill_age_ms: int
    max_post_horizon_delay_ms: int
    reference_source: str
    metric_kind: str
    causality_mode: str
    archive_source_mode: str
    archive_coverage_ratio: Decimal
    archive_gaps: list[tuple[int, int]]
    reference_files: list[str]
    reference_snapshot_count: int
    invalid_reference_record_count: int
    raw_fill_count: int
    valid_fill_count: int
    invalid_fill_count: int
    evaluated_fill_count: int
    missing_pre_reference_count: int
    stale_pre_reference_count: int
    missing_post_reference_count: int
    late_post_reference_count: int
    evaluation_coverage_ratio: Decimal
    evaluated_quote_qty: Decimal
    favorable_markout_count: int
    adverse_markout_count: int
    flat_markout_count: int
    unknown_liquidity_role_count: int
    quote_weighted_signed_markout_bps: Decimal | None
    quote_weighted_effective_spread_bps: Decimal | None
    quote_weighted_realized_spread_bps: Decimal | None
    quote_weighted_price_impact_bps: Decimal | None
    maker_quote_weighted_signed_markout_bps: Decimal | None
    taker_quote_weighted_signed_markout_bps: Decimal | None
    observations: list[PostFillMarkoutObservation] = field(default_factory=list)
    orders_allowed: bool = False
    can_trade: bool = False


@dataclass(slots=True)
class _ReferenceLoad:
    snapshots: list[ReferenceSnapshot]
    files: list[str]
    invalid_record_count: int


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "" or value == "None":
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _optional_bool(row: dict[str, object], *keys: str) -> bool | None:
    for key in keys:
        if key in row:
            return _bool(row[key])
    return None


def _int(value: object) -> int | None:
    if value is None or value == "" or value == "None":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_fill(row: dict[str, object], *, symbol: str) -> NormalizedFill | None:
    row_symbol = str(row.get("symbol", symbol)).upper()
    if row_symbol != symbol.upper():
        return None
    price = _decimal(row.get("price"))
    qty = _decimal(row.get("qty", row.get("quantity")))
    trade_time_ms = _int(row.get("time", row.get("trade_time_ms", 0)))
    if price is None or qty is None or price <= 0 or qty <= 0 or not trade_time_ms or trade_time_ms <= 0:
        return None

    side = str(row.get("side", "")).upper()
    if side not in {"BUY", "SELL"}:
        if "buyer" not in row:
            return None
        side = "BUY" if _bool(row.get("buyer")) else "SELL"

    quote_qty = _decimal(row.get("quoteQty", row.get("quote_qty")))
    if quote_qty is None or quote_qty <= 0:
        quote_qty = price * qty
    trade_id = _int(row.get("id", row.get("tradeId", row.get("trade_id", 0)))) or 0
    order_id_raw = row.get("orderId", row.get("order_id"))
    order_id = _int(order_id_raw)
    return NormalizedFill(
        symbol=row_symbol,
        trade_id=trade_id,
        order_id=order_id,
        trade_time_ms=trade_time_ms,
        side=side,
        maker=_optional_bool(row, "maker", "isMaker"),
        reduce_only=_optional_bool(row, "reduceOnly", "reduce_only"),
        qty=qty,
        quote_qty=quote_qty,
        price=price,
    )


def _iter_jsonl(path: Path) -> Iterable[dict[str, object]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                yield {}
                continue
            yield payload if isinstance(payload, dict) else {}


def _reference_path(config: PostFillMarkoutConfig, bucket: str) -> Path:
    symbol_key = config.symbol.lower()
    if config.reference_source == BOOK_MID:
        return config.market_root / "public" / bucket / f"{symbol_key}_bookTicker.jsonl"
    return config.market_root / "market" / bucket / f"{symbol_key}_markPrice_1s.jsonl"


def _snapshot_from_record(
    record: dict[str, object],
    *,
    config: PostFillMarkoutConfig,
    sequence: int,
) -> ReferenceSnapshot | None:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        payload = record
    symbol = str(payload.get("s", payload.get("symbol", config.symbol))).upper()
    if symbol != config.symbol.upper():
        return None
    event_time_ms = _int(payload.get("E", payload.get("T", record.get("event_time_ms", 0))))
    received_at_raw = record.get("received_at_ms")
    received_at_ms = _int(received_at_raw)
    if event_time_ms is None or event_time_ms <= 0:
        return None

    if config.reference_source == BOOK_MID:
        bid = _decimal(payload.get("b", payload.get("bid")))
        ask = _decimal(payload.get("a", payload.get("ask")))
        if bid is None or ask is None or bid <= 0 or ask <= 0 or bid > ask:
            return None
        price = (bid + ask) / _TWO
    else:
        price = _decimal(payload.get("p", payload.get("markPrice", payload.get("price"))))
        if price is None or price <= 0:
            return None

    return ReferenceSnapshot(
        event_time_ms=event_time_ms,
        received_at_ms=received_at_ms,
        price=price,
        source=config.reference_source,
        sequence=sequence,
    )


def _reference_windows(config: PostFillMarkoutConfig, trade_times: Iterable[int]) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    for trade_time_ms in trade_times:
        windows.append((max(0, trade_time_ms - config.max_pre_fill_age_ms), trade_time_ms))
        target_time_ms = trade_time_ms + config.horizon_ms
        windows.append((target_time_ms, target_time_ms + config.max_post_horizon_delay_ms))
    windows.sort()
    merged: list[tuple[int, int]] = []
    for start_ms, end_ms in windows:
        if not merged or start_ms > merged[-1][1] + 1:
            merged.append((start_ms, end_ms))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end_ms))
    return merged


def _inside_reference_windows(
    event_time_ms: int,
    windows: list[tuple[int, int]],
    starts: list[int],
) -> bool:
    index = bisect_right(starts, event_time_ms) - 1
    return index >= 0 and event_time_ms <= windows[index][1]


def load_reference_snapshots(
    config: PostFillMarkoutConfig,
    *,
    trade_times: Iterable[int] | None = None,
) -> _ReferenceLoad:
    load_start_ms = max(0, config.start_ms - config.max_pre_fill_age_ms)
    load_end_ms = config.end_ms + config.horizon_ms + config.max_post_horizon_delay_ms
    snapshots: list[ReferenceSnapshot] = []
    files: list[str] = []
    invalid_record_count = 0
    sequence = 0
    windows = _reference_windows(config, trade_times or []) if trade_times is not None else None
    starts = [item[0] for item in windows] if windows is not None else []
    before_window: dict[int, ReferenceSnapshot] = {}
    after_window: dict[int, ReferenceSnapshot] = {}
    for bucket in iter_buckets(load_start_ms, load_end_ms):
        path = _reference_path(config, bucket)
        if not path.exists():
            continue
        files.append(str(path))
        if windows == []:
            continue
        for record in _iter_jsonl(path):
            snapshot = _snapshot_from_record(record, config=config, sequence=sequence)
            sequence += 1
            if snapshot is None:
                invalid_record_count += 1
                continue
            if windows is None or _inside_reference_windows(snapshot.event_time_ms, windows, starts):
                snapshots.append(snapshot)
                continue

            previous_index = bisect_right(starts, snapshot.event_time_ms) - 1
            next_index = previous_index + 1
            if previous_index >= 0 and snapshot.event_time_ms > windows[previous_index][1]:
                current = after_window.get(previous_index)
                if current is None or snapshot.event_time_ms < current.event_time_ms:
                    after_window[previous_index] = snapshot
            if next_index < len(windows) and snapshot.event_time_ms < windows[next_index][0]:
                current = before_window.get(next_index)
                if current is None or snapshot.event_time_ms > current.event_time_ms:
                    before_window[next_index] = snapshot
    snapshots.extend(before_window.values())
    snapshots.extend(after_window.values())
    snapshots = list({(item.event_time_ms, item.sequence): item for item in snapshots}.values())
    snapshots.sort(key=lambda item: (item.event_time_ms, item.sequence))
    return _ReferenceLoad(
        snapshots=snapshots,
        files=files,
        invalid_record_count=invalid_record_count,
    )


def _weighted_average(values: Iterable[tuple[Decimal | None, Decimal]]) -> Decimal | None:
    weighted_sum = _ZERO
    total_weight = _ZERO
    for value, weight in values:
        if value is None or weight <= 0:
            continue
        weighted_sum += value * weight
        total_weight += weight
    if total_weight <= 0:
        return None
    return weighted_sum / total_weight


def _build_observation(
    fill: NormalizedFill,
    *,
    pre: ReferenceSnapshot,
    post: ReferenceSnapshot,
    config: PostFillMarkoutConfig,
) -> PostFillMarkoutObservation:
    side_sign = Decimal("1") if fill.side == "BUY" else Decimal("-1")
    target_time_ms = fill.trade_time_ms + config.horizon_ms
    signed_markout_bps = side_sign * (post.price - fill.price) / fill.price * _BPS
    if config.reference_source == BOOK_MID:
        effective_spread_bps = side_sign * _TWO * (fill.price - pre.price) / pre.price * _BPS
        realized_spread_bps = side_sign * _TWO * (fill.price - post.price) / pre.price * _BPS
        price_impact_bps = side_sign * _TWO * (post.price - pre.price) / pre.price * _BPS
        metric_kind = "book_mid_spread"
    else:
        effective_spread_bps = None
        realized_spread_bps = None
        price_impact_bps = None
        metric_kind = "mark_price_proxy"
    if signed_markout_bps > 0:
        markout_class = "favorable"
    elif signed_markout_bps < 0:
        markout_class = "adverse"
    else:
        markout_class = "flat"
    return PostFillMarkoutObservation(
        symbol=fill.symbol,
        trade_id=fill.trade_id,
        order_id=fill.order_id,
        trade_time_ms=fill.trade_time_ms,
        side=fill.side,
        maker=fill.maker,
        reduce_only=fill.reduce_only,
        qty=fill.qty,
        quote_qty=fill.quote_qty,
        fill_price=fill.price,
        reference_source=config.reference_source,
        metric_kind=metric_kind,
        causality_mode="exchange_event_time",
        horizon_ms=config.horizon_ms,
        target_time_ms=target_time_ms,
        pre_reference_event_time_ms=pre.event_time_ms,
        pre_reference_received_at_ms=pre.received_at_ms,
        pre_reference_age_ms=fill.trade_time_ms - pre.event_time_ms,
        post_reference_event_time_ms=post.event_time_ms,
        post_reference_received_at_ms=post.received_at_ms,
        post_reference_delay_ms=post.event_time_ms - target_time_ms,
        pre_reference_price=pre.price,
        post_reference_price=post.price,
        effective_spread_bps=effective_spread_bps,
        realized_spread_bps=realized_spread_bps,
        price_impact_bps=price_impact_bps,
        signed_markout_bps=signed_markout_bps,
        markout_class=markout_class,
    )


def analyze_post_fill_markout(
    config: PostFillMarkoutConfig,
    *,
    generated_at_ms: int | None = None,
) -> PostFillMarkoutReport:
    config.validate()
    archive = AuthoritativeArchive(config.archive_root, symbol=config.symbol)
    archive_result = archive.load_rows_for_range(
        USER_TRADES_DATASET,
        start_ms=config.start_ms,
        end_ms=config.end_ms,
    )
    raw_fills = archive_result.rows
    fills: list[NormalizedFill] = []
    invalid_fill_count = 0
    for row in raw_fills:
        fill = _normalize_fill(row, symbol=config.symbol)
        if fill is None:
            invalid_fill_count += 1
            continue
        fills.append(fill)

    reference_load = load_reference_snapshots(
        config,
        trade_times=(fill.trade_time_ms for fill in fills),
    )
    snapshots = reference_load.snapshots
    event_times = [item.event_time_ms for item in snapshots]
    observations: list[PostFillMarkoutObservation] = []
    missing_pre_reference_count = 0
    stale_pre_reference_count = 0
    missing_post_reference_count = 0
    late_post_reference_count = 0
    for fill in fills:
        pre_index = bisect_right(event_times, fill.trade_time_ms) - 1
        if pre_index < 0:
            missing_pre_reference_count += 1
            continue
        pre = snapshots[pre_index]
        if fill.trade_time_ms - pre.event_time_ms > config.max_pre_fill_age_ms:
            stale_pre_reference_count += 1
            continue

        target_time_ms = fill.trade_time_ms + config.horizon_ms
        post_index = bisect_left(event_times, target_time_ms)
        if post_index >= len(snapshots):
            missing_post_reference_count += 1
            continue
        post = snapshots[post_index]
        if post.event_time_ms - target_time_ms > config.max_post_horizon_delay_ms:
            late_post_reference_count += 1
            continue
        observations.append(_build_observation(fill, pre=pre, post=post, config=config))

    evaluated_quote_qty = sum((item.quote_qty for item in observations), _ZERO)
    valid_fill_count = len(fills)
    evaluated_fill_count = len(observations)
    evaluation_coverage_ratio = (
        Decimal(evaluated_fill_count) / Decimal(valid_fill_count) if valid_fill_count > 0 else _ZERO
    )
    favorable_count = sum(item.markout_class == "favorable" for item in observations)
    adverse_count = sum(item.markout_class == "adverse" for item in observations)
    flat_count = sum(item.markout_class == "flat" for item in observations)
    unknown_liquidity_role_count = sum(item.maker is None for item in observations)

    if archive_result.coverage_ratio < Decimal("1"):
        decision = "partial_authoritative_coverage"
    elif not raw_fills:
        decision = "no_fills"
    elif not fills:
        decision = "no_valid_fills"
    elif not observations:
        decision = "insufficient_reference_coverage"
    elif evaluated_fill_count < valid_fill_count:
        decision = "partial_reference_coverage"
    elif config.reference_source == BOOK_MID:
        decision = "book_mid_markout_ready_research_only"
    else:
        decision = "mark_price_proxy_ready_research_only"

    return PostFillMarkoutReport(
        schema_version=1,
        generated_at_ms=generated_at_ms if generated_at_ms is not None else now_ms(),
        decision=decision,
        symbol=config.symbol.upper(),
        start_ms=config.start_ms,
        end_ms=config.end_ms,
        horizon_ms=config.horizon_ms,
        max_pre_fill_age_ms=config.max_pre_fill_age_ms,
        max_post_horizon_delay_ms=config.max_post_horizon_delay_ms,
        reference_source=config.reference_source,
        metric_kind="book_mid_spread" if config.reference_source == BOOK_MID else "mark_price_proxy",
        causality_mode="exchange_event_time",
        archive_source_mode=archive_result.source_mode,
        archive_coverage_ratio=archive_result.coverage_ratio,
        archive_gaps=archive_result.gaps,
        reference_files=reference_load.files,
        reference_snapshot_count=len(snapshots),
        invalid_reference_record_count=reference_load.invalid_record_count,
        raw_fill_count=len(raw_fills),
        valid_fill_count=valid_fill_count,
        invalid_fill_count=invalid_fill_count,
        evaluated_fill_count=evaluated_fill_count,
        missing_pre_reference_count=missing_pre_reference_count,
        stale_pre_reference_count=stale_pre_reference_count,
        missing_post_reference_count=missing_post_reference_count,
        late_post_reference_count=late_post_reference_count,
        evaluation_coverage_ratio=evaluation_coverage_ratio,
        evaluated_quote_qty=evaluated_quote_qty,
        favorable_markout_count=favorable_count,
        adverse_markout_count=adverse_count,
        flat_markout_count=flat_count,
        unknown_liquidity_role_count=unknown_liquidity_role_count,
        quote_weighted_signed_markout_bps=_weighted_average(
            (item.signed_markout_bps, item.quote_qty) for item in observations
        ),
        quote_weighted_effective_spread_bps=_weighted_average(
            (item.effective_spread_bps, item.quote_qty) for item in observations
        ),
        quote_weighted_realized_spread_bps=_weighted_average(
            (item.realized_spread_bps, item.quote_qty) for item in observations
        ),
        quote_weighted_price_impact_bps=_weighted_average(
            (item.price_impact_bps, item.quote_qty) for item in observations
        ),
        maker_quote_weighted_signed_markout_bps=_weighted_average(
            (item.signed_markout_bps, item.quote_qty) for item in observations if item.maker is True
        ),
        taker_quote_weighted_signed_markout_bps=_weighted_average(
            (item.signed_markout_bps, item.quote_qty) for item in observations if item.maker is False
        ),
        observations=observations,
    )

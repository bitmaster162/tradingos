from __future__ import annotations

import heapq
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator


@dataclass(slots=True)
class BacktestTick:
    event_time_ms: int
    price: Decimal
    funding_rate: Decimal
    next_funding_time_ms: int
    moving_average_price: Decimal | None = None


@dataclass(slots=True)
class BacktestEvent:
    event_time_ms: int
    stream: str
    event_type: str
    payload: dict[str, Any]
    price: Decimal | None = None
    qty: Decimal | None = None
    funding_rate: Decimal | None = None
    next_funding_time_ms: int = 0
    moving_average_price: Decimal | None = None
    buyer_is_market_maker: bool | None = None
    crowding_snapshot: dict[str, Any] | None = None
    bid_price: Decimal | None = None
    bid_qty: Decimal | None = None
    ask_price: Decimal | None = None
    ask_qty: Decimal | None = None


def iter_mark_price_ticks(
    data_dir: Path,
    *,
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> Iterator[BacktestTick]:
    for event in iter_market_events(
        data_dir,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        include_agg_trades=False,
        include_contract_info=False,
        include_book_ticker=False,
    ):
        if event.event_type != "markPriceUpdate" or event.price is None:
            continue
        yield BacktestTick(
            event_time_ms=event.event_time_ms,
            price=event.price,
            funding_rate=event.funding_rate or Decimal("0"),
            next_funding_time_ms=event.next_funding_time_ms,
            moving_average_price=event.moving_average_price,
        )


def iter_market_events(
    data_dir: Path,
    *,
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
    include_agg_trades: bool = True,
    include_contract_info: bool = False,
    include_crowding: bool = False,
    crowding_period: str = "5m",
    include_book_ticker: bool = True,
    include_local_depth: bool = False,
    local_depth_levels: int = 20,
    include_local_rpi_depth: bool = False,
    local_rpi_depth_levels: int | None = None,
) -> Iterator[BacktestEvent]:
    heap: list[tuple[int, int, BacktestEvent, Iterator[BacktestEvent]]] = []
    sequence = 0

    market_filenames = [f"{symbol.lower()}_markPrice_1s.jsonl"]
    if include_agg_trades:
        market_filenames.append(f"{symbol.lower()}_aggTrade.jsonl")
    if include_contract_info:
        market_filenames.append("contractInfo.jsonl")

    for path in _iter_day_stream_files(
        data_dir,
        namespace="market",
        filenames=market_filenames,
        start_date=start_date,
        end_date=end_date,
    ):
        sequence = _prime_event_file(path, heap=heap, sequence=sequence)

    if include_book_ticker:
        for path in _iter_day_stream_files(
            data_dir,
            namespace="public",
            filenames=[f"{symbol.lower()}@bookTicker.jsonl", f"{symbol.lower()}_bookTicker.jsonl"],
            start_date=start_date,
            end_date=end_date,
        ):
            sequence = _prime_event_file(path, heap=heap, sequence=sequence)

    if include_local_depth:
        for path in _iter_day_stream_files(
            data_dir,
            namespace="public",
            filenames=[f"{symbol.lower()}_localDepth{local_depth_levels}.jsonl"],
            start_date=start_date,
            end_date=end_date,
        ):
            sequence = _prime_event_file(path, heap=heap, sequence=sequence)

    if include_local_rpi_depth:
        resolved_rpi_levels = local_rpi_depth_levels or local_depth_levels
        for path in _iter_day_stream_files(
            data_dir,
            namespace="public",
            filenames=[f"{symbol.lower()}_localRpiDepth{resolved_rpi_levels}.jsonl"],
            start_date=start_date,
            end_date=end_date,
        ):
            sequence = _prime_event_file(path, heap=heap, sequence=sequence)

    if include_crowding:
        crowding_filename = f"{symbol.lower()}_{crowding_period}.jsonl"
        for path in _iter_day_stream_files(
            data_dir,
            namespace="crowding",
            filenames=[crowding_filename],
            start_date=start_date,
            end_date=end_date,
        ):
            sequence = _prime_event_file(path, heap=heap, sequence=sequence)

    while heap:
        _, _, event, generator = heapq.heappop(heap)
        yield event
        try:
            next_event = next(generator)
        except StopIteration:
            continue
        heapq.heappush(heap, (next_event.event_time_ms, sequence, next_event, generator))
        sequence += 1


def _prime_event_file(path: Path, *, heap: list[tuple[int, int, BacktestEvent, Iterator[BacktestEvent]]], sequence: int) -> int:
    generator = _iter_events_from_file(path)
    try:
        first = next(generator)
    except StopIteration:
        return sequence
    heapq.heappush(heap, (first.event_time_ms, sequence, first, generator))
    return sequence + 1


def _iter_day_stream_files(
    data_dir: Path,
    *,
    namespace: str,
    filenames: list[str],
    start_date: str | None,
    end_date: str | None,
) -> Iterator[Path]:
    root = Path(data_dir) / namespace
    if not root.exists():
        return
    for day_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        day = day_dir.name
        if start_date is not None and day < start_date:
            continue
        if end_date is not None and day > end_date:
            continue
        for filename in filenames:
            path = day_dir / filename
            if path.exists():
                yield path


def _iter_events_from_file(path: Path) -> Iterator[BacktestEvent]:
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            record = json.loads(raw_line)
            event = _record_to_event(record)
            if event is None:
                continue
            yield event


def _record_to_event(record: dict[str, Any]) -> BacktestEvent | None:
    if "snapshot_time_ms" in record and "open_interest" in record:
        event_time_ms = int(record.get("snapshot_time_ms", 0) or 0)
        return BacktestEvent(
            event_time_ms=event_time_ms,
            stream=f"crowding/{record.get('period', '')}",
            event_type="crowdingSnapshot",
            payload=record,
            crowding_snapshot=record,
        )

    payload = record.get("payload") or {}
    if not isinstance(payload, dict):
        return None
    stream = str(record.get("stream") or payload.get("e") or "unknown")
    event_type = str(payload.get("e", ""))
    event_time_ms = int(payload.get("E", payload.get("T", record.get("received_at_ms", 0))))

    if event_type == "markPriceUpdate":
        price = payload.get("p")
        if price is None:
            return None
        moving_average_price = payload.get("ap")
        return BacktestEvent(
            event_time_ms=event_time_ms,
            stream=stream,
            event_type=event_type,
            payload=payload,
            price=Decimal(str(price)),
            funding_rate=Decimal(str(payload.get("r", "0"))),
            next_funding_time_ms=int(payload.get("T", 0) or 0),
            moving_average_price=(
                Decimal(str(moving_average_price)) if moving_average_price not in {None, ""} else None
            ),
        )

    if event_type == "aggTrade":
        price = payload.get("p")
        qty = payload.get("nq", payload.get("q", "0"))
        if price is None:
            return None
        return BacktestEvent(
            event_time_ms=int(payload.get("T", event_time_ms)),
            stream=stream,
            event_type=event_type,
            payload=payload,
            price=Decimal(str(price)),
            qty=Decimal(str(qty)),
            buyer_is_market_maker=bool(payload.get("m", False)),
        )

    if event_type == "contractInfo":
        return BacktestEvent(
            event_time_ms=event_time_ms,
            stream=stream,
            event_type=event_type,
            payload=payload,
        )

    if event_type == "bookTicker":
        try:
            bid_price = Decimal(str(payload.get("b", "0")))
            ask_price = Decimal(str(payload.get("a", "0")))
        except Exception:  # noqa: BLE001
            return None
        return BacktestEvent(
            event_time_ms=event_time_ms,
            stream=stream,
            event_type=event_type,
            payload=payload,
            bid_price=bid_price,
            bid_qty=Decimal(str(payload.get("B", "0"))),
            ask_price=ask_price,
            ask_qty=Decimal(str(payload.get("A", "0"))),
        )

    if event_type in {"localDepthSnapshot", "localRpiDepthSnapshot"}:
        return BacktestEvent(
            event_time_ms=event_time_ms,
            stream=stream,
            event_type=event_type,
            payload=payload,
        )

    return None

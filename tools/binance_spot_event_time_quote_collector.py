#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

import websockets

SCHEMA = "tradingos.binance_spot_event_time_quote.v1"
VERSION = "1.0.0"
REST_SCHEME = "https"
REST_HOST = "data-api.binance.vision"
REST_PATH = "/api/v3/depth"
WS_URL_TEMPLATE = "wss://stream.binance.com:9443/stream?streams={stream}"
DEFAULT_LIMIT = 1000
DEFAULT_MAX_AGE_MS = 2000
DEFAULT_TIMEOUT_S = 10.0
MAX_TIMEOUT_S = 30.0
MAX_RESPONSE_BYTES = 4_000_000
MAX_RAW_FRAME_BYTES = 1_000_000
MAX_BUFFER_EVENTS = 20_000
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{5,20}$")

FetchJson = Callable[[str], Any]


class SpotQuoteReject(ValueError):
    """Fail-closed admission error for a synchronized spot depth quote."""


def now_ms() -> int:
    return int(time.time() * 1000)


def stable_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def validate_symbol(raw: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("symbol must be non-empty")
    symbol = raw.strip().upper()
    if not _SYMBOL_RE.fullmatch(symbol) or not symbol.endswith("USDT"):
        raise ValueError("symbol must be an uppercase-compatible USDT market")
    return symbol

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(req.full_url, code, "redirects are forbidden", headers, fp)


def snapshot_url(symbol: str, limit: int = DEFAULT_LIMIT) -> str:
    symbol = validate_symbol(symbol)
    if limit not in {100, 500, 1000, 5000}:
        raise ValueError("unsupported depth limit")
    query = urllib.parse.urlencode({"symbol": symbol, "limit": limit})
    return urllib.parse.urlunsplit((REST_SCHEME, REST_HOST, REST_PATH, query, ""))


def validate_snapshot_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != REST_SCHEME or parsed.hostname != REST_HOST or parsed.port is not None:
        raise ValueError("snapshot host/scheme not allowlisted")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("snapshot credentials forbidden")
    if parsed.path != REST_PATH or parsed.fragment:
        raise ValueError("snapshot path/fragment not allowlisted")
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    if len(pairs) != 2 or {key for key, _ in pairs} != {"symbol", "limit"}:
        raise ValueError("snapshot query contract violated")
    values = dict(pairs)
    expected = snapshot_url(values["symbol"], int(values["limit"]))
    if url != expected:
        raise ValueError("snapshot URL not canonical")

def default_fetch_json(url: str, *, timeout_s: float = 5.0) -> Any:
    validate_snapshot_url(url)
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
        raise ValueError("timeout must be numeric")
    timeout = float(timeout_s)
    if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_TIMEOUT_S:
        raise ValueError("timeout out of bounds")
    opener = urllib.request.build_opener(_NoRedirect())
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "TradingOS-SpotQuote/1.0", "Accept": "application/json"},
        method="GET",
    )
    with opener.open(req, timeout=timeout) as response:  # nosec B310 - fixed allowlisted HTTPS endpoint
        if getattr(response, "status", None) != 200:
            raise SpotQuoteReject(f"snapshot_http_status:{getattr(response, 'status', None)}")
        if response.geturl() != url:
            raise SpotQuoteReject("snapshot_url_drift")
        if response.headers.get_content_type() not in {"application/json", "text/json"}:
            raise SpotQuoteReject("snapshot_content_type_invalid")
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise SpotQuoteReject("snapshot_response_too_large")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpotQuoteReject("snapshot_invalid_json") from exc

def _decimal(value: Any, field: str, *, allow_zero: bool = False) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise SpotQuoteReject(f"invalid_{field}") from exc
    if not number.is_finite() or number < 0 or (number == 0 and not allow_zero):
        raise SpotQuoteReject(f"invalid_{field}")
    return number


def _int(value: Any, field: str, *, allow_zero: bool = False) -> int:
    if type(value) is not int:
        raise SpotQuoteReject(f"invalid_{field}")
    if value < 0 or (value == 0 and not allow_zero):
        raise SpotQuoteReject(f"invalid_{field}")
    return value


def _snapshot_levels(raw: Any, side: str) -> dict[Decimal, Decimal]:
    if not isinstance(raw, list) or not raw:
        raise SpotQuoteReject(f"snapshot_{side}_invalid")
    out: dict[Decimal, Decimal] = {}
    for index, row in enumerate(raw):
        if not isinstance(row, list) or len(row) != 2:
            raise SpotQuoteReject(f"snapshot_{side}_{index}_malformed")
        price = _decimal(row[0], f"snapshot_{side}_{index}_price")
        qty = _decimal(row[1], f"snapshot_{side}_{index}_qty")
        if price in out:
            raise SpotQuoteReject(f"snapshot_{side}_duplicate_price")
        out[price] = qty
    return out

def validate_snapshot(raw: Any, *, symbol: str):
    if not isinstance(raw, dict):
        raise SpotQuoteReject("snapshot_shape_invalid")
    if set(raw) != {"lastUpdateId", "bids", "asks"}:
        raise SpotQuoteReject("snapshot_shape_invalid")
    last_update_id = _int(raw.get("lastUpdateId"), "snapshot_lastUpdateId", allow_zero=True)
    bids = _snapshot_levels(raw.get("bids"), "bids")
    asks = _snapshot_levels(raw.get("asks"), "asks")
    if not bids or not asks:
        raise SpotQuoteReject("snapshot_empty_book")
    if max(bids) >= min(asks):
        raise SpotQuoteReject("snapshot_crossed_or_locked")
    validate_symbol(symbol)
    return last_update_id, bids, asks


def _diff_levels(raw: Any, side: str):
    if not isinstance(raw, list):
        raise SpotQuoteReject(f"event_{side}_invalid")
    out = []
    seen = set()
    for index, row in enumerate(raw):
        if not isinstance(row, list) or len(row) != 2:
            raise SpotQuoteReject(f"event_{side}_{index}_malformed")
        price = _decimal(row[0], f"event_{side}_{index}_price")
        qty = _decimal(row[1], f"event_{side}_{index}_qty", allow_zero=True)
        if price in seen:
            raise SpotQuoteReject(f"event_{side}_duplicate_price")
        seen.add(price)
        out.append((price, qty))
    return tuple(out)

@dataclass(frozen=True, slots=True)
class DepthEvent:
    stream: str
    symbol: str
    event_time_ms: int
    first_update_id: int
    final_update_id: int
    bids: tuple
    asks: tuple
    received_at_ms: int
    receive_age_ms: int
    raw_sha256: str


def parse_depth_event(raw_message: str | bytes, *, symbol: str, received_at_ms: int, max_age_ms: int) -> DepthEvent:
    symbol = validate_symbol(symbol)
    if max_age_ms <= 0:
        raise ValueError("max_age_ms must be positive")
    if isinstance(raw_message, bytes):
        raw_bytes = raw_message
        try:
            text = raw_message.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SpotQuoteReject("event_not_utf8") from exc
    elif isinstance(raw_message, str):
        text = raw_message
        raw_bytes = raw_message.encode("utf-8")
    else:
        raise SpotQuoteReject("event_raw_type_invalid")
    if len(raw_bytes) > MAX_RAW_FRAME_BYTES:
        raise SpotQuoteReject("event_frame_too_large")
    try:
        root = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SpotQuoteReject("event_invalid_json") from exc
    if not isinstance(root, dict) or set(root) != {"stream", "data"}:
        raise SpotQuoteReject("combined_stream_frame_required")
    stream = f"{symbol.lower()}@depth@100ms"
    if root.get("stream") != stream or not isinstance(root.get("data"), dict):
        raise SpotQuoteReject("event_stream_mismatch")
    data = root["data"]
    required = {"e", "E", "s", "U", "u", "b", "a"}
    if not required.issubset(data):
        raise SpotQuoteReject("event_required_fields_missing")
    if data.get("e") != "depthUpdate" or data.get("s") != symbol:
        raise SpotQuoteReject("event_identity_mismatch")
    event_time_ms = _int(data.get("E"), "event_time_E")
    first_update_id = _int(data.get("U"), "first_update_id_U", allow_zero=True)
    final_update_id = _int(data.get("u"), "final_update_id_u", allow_zero=True)
    if final_update_id < first_update_id:
        raise SpotQuoteReject("event_update_range_invalid")
    if type(received_at_ms) is not int or received_at_ms <= 0:
        raise SpotQuoteReject("received_at_ms_invalid")
    age_ms = received_at_ms - event_time_ms
    if age_ms < 0:
        raise SpotQuoteReject("future_event_time_E")
    if age_ms > max_age_ms:
        raise SpotQuoteReject("stale_event_time_E")
    return DepthEvent(
        stream=stream,
        symbol=symbol,
        event_time_ms=event_time_ms,
        first_update_id=first_update_id,
        final_update_id=final_update_id,
        bids=_diff_levels(data.get("b"), "bids"),
        asks=_diff_levels(data.get("a"), "asks"),
        received_at_ms=received_at_ms,
        receive_age_ms=age_ms,
        raw_sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )

class SpotDepthBook:
    def __init__(self, *, symbol: str, snapshot: Any) -> None:
        self.symbol = validate_symbol(symbol)
        self.update_id, self.bids, self.asks = validate_snapshot(snapshot, symbol=self.symbol)
        self.snapshot_update_id = self.update_id
        self.snapshot_sha256 = stable_sha256(snapshot)
        self.synced = False
        self.events_applied = 0

    @staticmethod
    def _apply_levels(book: dict[Decimal, Decimal], updates: tuple) -> None:
        for price, qty in updates:
            if qty == 0:
                book.pop(price, None)
            else:
                book[price] = qty

    def apply(self, event: DepthEvent) -> dict[str, Any] | None:
        if event.symbol != self.symbol:
            raise SpotQuoteReject("event_symbol_drift")
        if event.final_update_id <= self.update_id:
            return None
        expected = self.update_id + 1
        if not self.synced:
            if not (event.first_update_id <= expected <= event.final_update_id):
                raise SpotQuoteReject("snapshot_event_bridge_gap")
            self.synced = True
        elif event.first_update_id > expected:
            raise SpotQuoteReject("depth_sequence_gap")
        self._apply_levels(self.bids, event.bids)
        self._apply_levels(self.asks, event.asks)
        if not self.bids or not self.asks:
            raise SpotQuoteReject("depth_book_side_empty")
        best_bid = max(self.bids)
        best_ask = min(self.asks)
        if best_bid >= best_ask:
            raise SpotQuoteReject("depth_book_crossed_or_locked")
        self.update_id = event.final_update_id
        self.events_applied += 1
        quote = {
            "schema": SCHEMA,
            "version": VERSION,
            "source": "binance_spot_diff_depth_local_book",
            "symbol": self.symbol,
            "stream": event.stream,
            "event_time_ms": event.event_time_ms,
            "received_at_ms": event.received_at_ms,
            "age_ms": event.receive_age_ms,
            "snapshot_update_id": self.snapshot_update_id,
            "snapshot_sha256": self.snapshot_sha256,
            "first_update_id": event.first_update_id,
            "final_update_id": event.final_update_id,
            "bid": str(best_bid),
            "bid_qty": str(self.bids[best_bid]),
            "ask": str(best_ask),
            "ask_qty": str(self.asks[best_ask]),
            "raw_event_sha256": event.raw_sha256,
        }
        quote["provenance_sha256"] = stable_sha256(quote)
        return quote



def validate_quote_at_decision(quote: dict[str, Any], *, decision_time_ms: int, max_age_ms: int) -> int:
    if not isinstance(quote, dict) or quote.get("schema") != SCHEMA:
        raise SpotQuoteReject("quote_schema_invalid")
    if quote.get("source") != "binance_spot_diff_depth_local_book":
        raise SpotQuoteReject("quote_source_invalid")
    event_time_ms = _int(quote.get("event_time_ms"), "quote_event_time_ms")
    if type(decision_time_ms) is not int or decision_time_ms <= 0:
        raise SpotQuoteReject("decision_time_invalid")
    if max_age_ms <= 0:
        raise SpotQuoteReject("decision_max_age_invalid")
    age_ms = decision_time_ms - event_time_ms
    if age_ms < 0:
        raise SpotQuoteReject("quote_future_at_decision")
    if age_ms > max_age_ms:
        raise SpotQuoteReject("quote_stale_at_decision")
    for field in ("bid", "bid_qty", "ask", "ask_qty"):
        _decimal(quote.get(field), f"quote_{field}")
    if _decimal(quote["bid"], "quote_bid") >= _decimal(quote["ask"], "quote_ask"):
        raise SpotQuoteReject("quote_crossed_or_locked")
    supplied = quote.get("provenance_sha256")
    basis = {key: value for key, value in quote.items() if key != "provenance_sha256"}
    if supplied != stable_sha256(basis):
        raise SpotQuoteReject("quote_provenance_mismatch")
    return age_ms

async def capture_one_quote(
    *,
    symbol: str,
    max_age_ms: int = DEFAULT_MAX_AGE_MS,
    snapshot_limit: int = DEFAULT_LIMIT,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_events: int = 500,
) -> dict[str, Any]:
    symbol = validate_symbol(symbol)
    if max_age_ms <= 0 or max_events <= 0:
        raise ValueError("max_age_ms and max_events must be positive")
    if not math.isfinite(float(timeout_s)) or timeout_s <= 0 or timeout_s > MAX_TIMEOUT_S:
        raise ValueError("timeout_s out of bounds")
    stream = f"{symbol.lower()}@depth@100ms"
    ws_url = WS_URL_TEMPLATE.format(stream=stream)
    async with websockets.connect(
        ws_url,
        ping_interval=None,
        open_timeout=timeout_s,
        close_timeout=timeout_s,
        max_size=MAX_RAW_FRAME_BYTES,
        max_queue=4096,
    ) as websocket:
        snapshot = await asyncio.to_thread(
            default_fetch_json,
            snapshot_url(symbol, snapshot_limit),
            timeout_s=timeout_s,
        )
        book = SpotDepthBook(symbol=symbol, snapshot=snapshot)
        for _ in range(max_events):
            raw_message = await asyncio.wait_for(websocket.recv(), timeout=timeout_s)
            event = parse_depth_event(
                raw_message,
                symbol=symbol,
                received_at_ms=now_ms(),
                max_age_ms=max_age_ms,
            )
            quote = book.apply(event)
            if quote is None:
                continue
            decision_time_ms = now_ms()
            quote["decision_time_ms"] = decision_time_ms
            quote["decision_age_ms"] = decision_time_ms - quote["event_time_ms"]
            quote["events_applied"] = book.events_applied
            quote["can_trade"] = False
            quote["capital_permission"] = "DENY"
            quote["provenance_sha256"] = stable_sha256(
                {key: value for key, value in quote.items() if key != "provenance_sha256"}
            )
            validate_quote_at_decision(quote, decision_time_ms=decision_time_ms, max_age_ms=max_age_ms)
            return quote
    raise SpotQuoteReject("no_synchronized_quote_within_event_budget")

RESTARTABLE_SYNC_ERRORS = {
    "snapshot_event_bridge_gap",
    "depth_sequence_gap",
    "stale_event_time_E",
    "no_synchronized_quote_within_event_budget",
}


async def capture_quote_with_restarts(*, max_restarts: int = 2, capture_fn=None, **kwargs) -> dict[str, Any]:
    if type(max_restarts) is not int or max_restarts < 0 or max_restarts > 5:
        raise ValueError("max_restarts out of bounds")
    fn = capture_fn or capture_one_quote
    last_error: Exception | None = None
    for attempt in range(max_restarts + 1):
        try:
            quote = await fn(**kwargs)
            quote = dict(quote)
            quote["restart_count"] = attempt
            quote["provenance_sha256"] = stable_sha256(
                {key: value for key, value in quote.items() if key != "provenance_sha256"}
            )
            return quote
        except SpotQuoteReject as exc:
            last_error = exc
            if str(exc) not in RESTARTABLE_SYNC_ERRORS or attempt >= max_restarts:
                raise
        except (asyncio.TimeoutError, OSError) as exc:
            last_error = exc
            if attempt >= max_restarts:
                raise
    raise SpotQuoteReject(f"restart_budget_exhausted:{last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture one synchronized Binance Spot event-time top-of-book quote")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--max-age-ms", type=int, default=DEFAULT_MAX_AGE_MS)
    parser.add_argument("--snapshot-limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--max-events", type=int, default=500)
    parser.add_argument("--max-restarts", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        quote = asyncio.run(
            capture_quote_with_restarts(
                symbol=args.symbol,
                max_age_ms=args.max_age_ms,
                snapshot_limit=args.snapshot_limit,
                timeout_s=args.timeout_s,
                max_events=args.max_events,
                max_restarts=args.max_restarts,
            )
        )
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(quote, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(quote, ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False, "capital_permission": "DENY"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

GATE_SCHEMA = "tradingos.binance_usdm_futures_book_ticker_gate.v1"
STORE_GATE_KEY = "_quote_gate"
SOURCE_EVENT_TIME_FIELD = "E"
DEFAULT_MAX_RAW_FRAME_BYTES = 65_536


class FuturesBookTickerGateReject(ValueError):
    """Raised when a Futures bookTicker frame fails deterministic admission."""


def _stable_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _binding_sha256(payload: dict[str, Any], marker: dict[str, Any]) -> str:
    return _stable_sha256({"payload": payload, "gate": marker})


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FuturesBookTickerGateReject(f"duplicate_json_key:{key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise FuturesBookTickerGateReject(f"non_standard_json_constant:{value}")


def _require_int(value: Any, field: str, *, allow_zero: bool = False) -> int:
    if type(value) is not int:
        raise FuturesBookTickerGateReject(f"invalid_{field}")
    if value < 0 if allow_zero else value <= 0:
        raise FuturesBookTickerGateReject(f"invalid_{field}")
    return value


def _require_positive_decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise FuturesBookTickerGateReject(f"invalid_{field}") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise FuturesBookTickerGateReject(f"invalid_{field}")
    return parsed


@dataclass(frozen=True, slots=True)
class FuturesBookTickerAdmission:
    stream: str
    payload: dict[str, Any]
    event_time_ms: int
    received_at_ms: int
    age_ms: int
    raw_sha256: str
    raw_size_bytes: int
    max_age_ms: int
    freshness_source: str = SOURCE_EVENT_TIME_FIELD

    def state_payload(self) -> dict[str, Any]:
        payload = dict(self.payload)
        marker = {
            "admitted": True,
            "schema": GATE_SCHEMA,
            "freshness_source": self.freshness_source,
            "stream": self.stream,
            "event_time_ms": self.event_time_ms,
            "received_at_ms": self.received_at_ms,
            "age_ms": self.age_ms,
            "max_age_ms": self.max_age_ms,
            "raw_sha256": self.raw_sha256,
            "raw_size_bytes": self.raw_size_bytes,
        }
        marker["quote_binding_sha256"] = _binding_sha256(payload, marker)
        payload[STORE_GATE_KEY] = marker
        return payload


class FuturesBookTickerGate:
    def __init__(
        self,
        *,
        expected_symbol: str,
        expected_stream: str,
        max_age_ms: int,
        max_raw_frame_bytes: int = DEFAULT_MAX_RAW_FRAME_BYTES,
    ) -> None:
        if not expected_symbol:
            raise ValueError("expected_symbol is required")
        if not expected_stream:
            raise ValueError("expected_stream is required")
        if max_age_ms <= 0:
            raise ValueError("max_age_ms must be positive")
        if max_raw_frame_bytes <= 0:
            raise ValueError("max_raw_frame_bytes must be positive")
        self.expected_symbol = expected_symbol.upper()
        self.expected_stream = expected_stream
        self.max_age_ms = int(max_age_ms)
        self.max_raw_frame_bytes = int(max_raw_frame_bytes)

    def _decode_raw(self, raw_message: str | bytes) -> tuple[str, dict[str, Any], bytes]:
        if isinstance(raw_message, bytes):
            raw_bytes = raw_message
            try:
                text = raw_message.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise FuturesBookTickerGateReject("raw_frame_not_utf8") from exc
        elif isinstance(raw_message, str):
            text = raw_message
            raw_bytes = raw_message.encode("utf-8")
        else:
            raise FuturesBookTickerGateReject("raw_frame_must_be_str_or_bytes")
        if len(raw_bytes) > self.max_raw_frame_bytes:
            raise FuturesBookTickerGateReject("raw_frame_too_large")
        try:
            parsed = json.loads(
                text,
                object_pairs_hook=_reject_duplicate_pairs,
                parse_constant=_reject_constant,
            )
        except FuturesBookTickerGateReject:
            raise
        except json.JSONDecodeError as exc:
            raise FuturesBookTickerGateReject("raw_frame_invalid_json") from exc
        if not isinstance(parsed, dict):
            raise FuturesBookTickerGateReject("raw_frame_not_json_object")
        if set(parsed) != {"stream", "data"}:
            raise FuturesBookTickerGateReject("combined_frame_required")
        stream = parsed.get("stream")
        payload = parsed.get("data")
        if stream != self.expected_stream:
            raise FuturesBookTickerGateReject("unexpected_stream")
        if not isinstance(payload, dict):
            raise FuturesBookTickerGateReject("combined_frame_data_not_object")
        return stream, payload, raw_bytes

    def admit(
        self,
        raw_message: str | bytes,
        *,
        received_at_ms: int,
    ) -> FuturesBookTickerAdmission:
        stream, payload, raw_bytes = self._decode_raw(raw_message)
        if payload.get("e") != "bookTicker":
            raise FuturesBookTickerGateReject("unexpected_event_type")
        if payload.get("s") != self.expected_symbol:
            raise FuturesBookTickerGateReject("unexpected_symbol")
        update_id = _require_int(payload.get("u"), "update_id_u", allow_zero=True)
        event_time_ms = _require_int(payload.get("E"), "event_time_E")
        transaction_time_ms = _require_int(payload.get("T"), "transaction_time_T")
        if type(received_at_ms) is not int or received_at_ms <= 0:
            raise FuturesBookTickerGateReject("invalid_received_at_ms")
        age_ms = received_at_ms - event_time_ms
        if age_ms < 0:
            raise FuturesBookTickerGateReject("future_event_time_E")
        if age_ms > self.max_age_ms:
            raise FuturesBookTickerGateReject("stale_event_time_E")
        bid = _require_positive_decimal(payload.get("b"), "bid_price_b")
        bid_qty = _require_positive_decimal(payload.get("B"), "bid_qty_B")
        ask = _require_positive_decimal(payload.get("a"), "ask_price_a")
        ask_qty = _require_positive_decimal(payload.get("A"), "ask_qty_A")
        if ask < bid:
            raise FuturesBookTickerGateReject("crossed_book_ask_below_bid")
        _ = (bid_qty, ask_qty)
        sanitized = {
            "e": "bookTicker",
            "u": update_id,
            "E": event_time_ms,
            "T": transaction_time_ms,
            "s": self.expected_symbol,
            "b": str(payload.get("b")),
            "B": str(payload.get("B")),
            "a": str(payload.get("a")),
            "A": str(payload.get("A")),
        }
        return FuturesBookTickerAdmission(
            stream=stream,
            payload=sanitized,
            event_time_ms=event_time_ms,
            received_at_ms=received_at_ms,
            age_ms=age_ms,
            raw_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            raw_size_bytes=len(raw_bytes),
            max_age_ms=self.max_age_ms,
        )


def validate_gated_book_ticker(
    payload: dict[str, Any],
    *,
    expected_symbol: str,
    decision_time_ms: int,
    max_age_ms: int,
) -> int:
    if not isinstance(payload, dict):
        raise FuturesBookTickerGateReject("stored_book_ticker_not_object")
    marker = payload.get(STORE_GATE_KEY)
    if not isinstance(marker, dict):
        raise FuturesBookTickerGateReject("book_ticker_gate_marker_missing")
    expected_keys = {
        "admitted", "schema", "freshness_source", "stream", "event_time_ms",
        "received_at_ms", "age_ms", "max_age_ms", "raw_sha256", "raw_size_bytes",
        "quote_binding_sha256",
    }
    if set(marker) != expected_keys:
        raise FuturesBookTickerGateReject("book_ticker_gate_marker_shape_mismatch")
    if marker.get("admitted") is not True or marker.get("schema") != GATE_SCHEMA:
        raise FuturesBookTickerGateReject("book_ticker_gate_marker_contract_drift")
    if marker.get("freshness_source") != SOURCE_EVENT_TIME_FIELD:
        raise FuturesBookTickerGateReject("book_ticker_freshness_source_drift")
    symbol = expected_symbol.upper()
    expected_stream = f"{symbol.lower()}@bookTicker"
    if payload.get("e") != "bookTicker" or payload.get("s") != symbol:
        raise FuturesBookTickerGateReject("book_ticker_identity_mismatch")
    if marker.get("stream") != expected_stream:
        raise FuturesBookTickerGateReject("book_ticker_stream_mismatch")
    update_id = _require_int(payload.get("u"), "update_id_u", allow_zero=True)
    event_time_ms = _require_int(payload.get("E"), "event_time_E")
    _require_int(payload.get("T"), "transaction_time_T")
    _ = update_id
    bid = _require_positive_decimal(payload.get("b"), "bid_price_b")
    _require_positive_decimal(payload.get("B"), "bid_qty_B")
    ask = _require_positive_decimal(payload.get("a"), "ask_price_a")
    _require_positive_decimal(payload.get("A"), "ask_qty_A")
    if ask < bid:
        raise FuturesBookTickerGateReject("crossed_book_ask_below_bid")
    if marker.get("event_time_ms") != event_time_ms:
        raise FuturesBookTickerGateReject("book_ticker_event_time_binding_mismatch")
    received_at_ms = marker.get("received_at_ms")
    marker_max_age_ms = marker.get("max_age_ms")
    if type(received_at_ms) is not int or received_at_ms <= 0:
        raise FuturesBookTickerGateReject("book_ticker_received_at_invalid")
    if type(marker_max_age_ms) is not int or marker_max_age_ms <= 0:
        raise FuturesBookTickerGateReject("book_ticker_marker_max_age_invalid")
    receive_age_ms = received_at_ms - event_time_ms
    if receive_age_ms < 0 or receive_age_ms > marker_max_age_ms:
        raise FuturesBookTickerGateReject("book_ticker_receive_freshness_invalid")
    if marker.get("age_ms") != receive_age_ms:
        raise FuturesBookTickerGateReject("book_ticker_receive_age_mismatch")
    if not _valid_sha256(marker.get("raw_sha256")):
        raise FuturesBookTickerGateReject("book_ticker_raw_sha256_invalid")
    if type(marker.get("raw_size_bytes")) is not int or marker["raw_size_bytes"] <= 0:
        raise FuturesBookTickerGateReject("book_ticker_raw_size_invalid")
    quote_payload = {key: value for key, value in payload.items() if key != STORE_GATE_KEY}
    marker_basis = {key: value for key, value in marker.items() if key != "quote_binding_sha256"}
    if marker.get("quote_binding_sha256") != _binding_sha256(quote_payload, marker_basis):
        raise FuturesBookTickerGateReject("book_ticker_quote_binding_mismatch")
    if type(decision_time_ms) is not int or decision_time_ms <= 0:
        raise FuturesBookTickerGateReject("book_ticker_decision_time_invalid")
    if max_age_ms <= 0:
        raise FuturesBookTickerGateReject("book_ticker_max_age_invalid")
    decision_age_ms = decision_time_ms - event_time_ms
    if decision_age_ms < 0:
        raise FuturesBookTickerGateReject("future_event_time_E_at_decision")
    effective_max_age_ms = min(marker_max_age_ms, int(max_age_ms))
    if decision_age_ms > effective_max_age_ms:
        raise FuturesBookTickerGateReject("stale_event_time_E_at_decision")
    return decision_age_ms

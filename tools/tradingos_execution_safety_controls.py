"""Fail-closed execution controls derived from hash-bound Cowork evidence.

This module is a proposal-only safety layer. It has no network client, cannot
submit orders, and is not wired into any runtime. The controls make unsafe
states explicit so a future integrator cannot replace evidence with a boolean
or trust a ``reduce_only`` label without an observed position.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


class SafetyControlError(ValueError):
    """Base class for a fail-closed control rejection."""


class IdentityMismatch(SafetyControlError):
    """An immutable identity was reused with different content."""


class InvalidAdmissionEvidence(SafetyControlError):
    """Order state admission lacked validated venue evidence."""


class DeadManLatched(SafetyControlError):
    """A heartbeat cannot silently clear a latched dead-man condition."""


def _finite_decimal(name: str, value: Any) -> Decimal:
    if isinstance(value, bool):
        raise SafetyControlError(f"{name} must be a finite number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SafetyControlError(f"{name} must be a finite number") from exc
    if not number.is_finite():
        raise SafetyControlError(f"{name} must be finite")
    return number


def _positive_decimal(name: str, value: Any) -> Decimal:
    number = _finite_decimal(name, value)
    if number <= 0:
        raise SafetyControlError(f"{name} must be > 0")
    return number


def _positive_timestamp(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SafetyControlError(f"{name} must be a positive integer timestamp")
    return value


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def deterministic_client_id(intent_id: str, strategy_id: str, strategy_version: str) -> str:
    parts = (intent_id.strip(), strategy_id.strip(), strategy_version.strip())
    if not all(parts):
        raise SafetyControlError("intent and strategy identity fields are mandatory")
    digest = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"cx-{digest}"


@dataclass(frozen=True)
class ImmutableIntent:
    intent_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    side: str
    qty: str
    decision_ts_ms: int
    price: str | None = None
    reduce_only: bool = False
    position_id: str | None = None
    client_id: str = ""

    def __post_init__(self) -> None:
        intent_id = self.intent_id.strip()
        strategy_id = self.strategy_id.strip()
        strategy_version = self.strategy_version.strip()
        symbol = self.symbol.strip().upper()
        side = self.side.strip().upper()
        if not intent_id or not strategy_id or not strategy_version:
            raise SafetyControlError("immutable intent identity fields are mandatory")
        if not symbol:
            raise SafetyControlError("symbol is mandatory")
        if side not in {"BUY", "SELL"}:
            raise SafetyControlError("side must be BUY or SELL")
        _positive_decimal("qty", self.qty)
        if self.price is not None:
            _positive_decimal("price", self.price)
        _positive_timestamp("decision_ts_ms", self.decision_ts_ms)
        if not isinstance(self.reduce_only, bool):
            raise SafetyControlError("reduce_only must be boolean")

        expected_client_id = deterministic_client_id(intent_id, strategy_id, strategy_version)
        if self.client_id and self.client_id != expected_client_id:
            raise IdentityMismatch("client_id does not match immutable intent identity")

        object.__setattr__(self, "intent_id", intent_id)
        object.__setattr__(self, "strategy_id", strategy_id)
        object.__setattr__(self, "strategy_version", strategy_version)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "client_id", expected_client_id)

    @property
    def fingerprint(self) -> str:
        return canonical_sha256(
            {
                "client_id": self.client_id,
                "decision_ts_ms": self.decision_ts_ms,
                "intent_id": self.intent_id,
                "position_id": self.position_id,
                "price": self.price,
                "qty": str(self.qty),
                "reduce_only": self.reduce_only,
                "side": self.side,
                "strategy_id": self.strategy_id,
                "strategy_version": self.strategy_version,
                "symbol": self.symbol,
            }
        )


@dataclass(frozen=True)
class LedgerEntry:
    intent_fingerprint: str
    state: str


class IdempotencyLedger:
    """Suppress exact replays and reject identity reuse with changed content."""

    def __init__(self) -> None:
        self._entries: dict[str, LedgerEntry] = {}

    def register(self, intent: ImmutableIntent, state: str = "CREATED") -> bool:
        existing = self._entries.get(intent.client_id)
        if existing is not None:
            if existing.intent_fingerprint != intent.fingerprint:
                raise IdentityMismatch(
                    f"client_id {intent.client_id} is bound to another intent fingerprint"
                )
            return False
        self._entries[intent.client_id] = LedgerEntry(intent.fingerprint, state)
        return True

    def update(self, intent: ImmutableIntent, state: str) -> None:
        existing = self._entries.get(intent.client_id)
        if existing is None:
            raise IdentityMismatch(f"client_id {intent.client_id} is not registered")
        if existing.intent_fingerprint != intent.fingerprint:
            raise IdentityMismatch(
                f"client_id {intent.client_id} cannot change immutable intent content"
            )
        self._entries[intent.client_id] = LedgerEntry(intent.fingerprint, state)

    def snapshot(self) -> str:
        payload = {
            "schema": "tradingos-idempotency-ledger-v2",
            "entries": {
                client_id: {
                    "intent_fingerprint": entry.intent_fingerprint,
                    "state": entry.state,
                }
                for client_id, entry in sorted(self._entries.items())
            },
        }
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)

    @classmethod
    def load(cls, blob: str) -> "IdempotencyLedger":
        payload = json.loads(blob)
        if payload.get("schema") != "tradingos-idempotency-ledger-v2":
            raise IdentityMismatch("unsupported or missing idempotency ledger schema")
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            raise IdentityMismatch("idempotency ledger entries must be an object")
        ledger = cls()
        for client_id, raw in entries.items():
            if not isinstance(raw, dict):
                raise IdentityMismatch("invalid idempotency ledger entry")
            fingerprint = raw.get("intent_fingerprint")
            state = raw.get("state")
            if (
                not isinstance(client_id, str)
                or not isinstance(fingerprint, str)
                or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
                or not isinstance(state, str)
                or not state
            ):
                raise IdentityMismatch("invalid idempotency ledger identity")
            ledger._entries[client_id] = LedgerEntry(fingerprint, state)
        return ledger


_ADMISSION_TOKEN = object()
_ADMISSIBLE_STATES = {
    "ACKNOWLEDGED",
    "OPEN",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELED",
    "REJECTED",
}


@dataclass(frozen=True)
class ValidatedAdmissionEvidence:
    channel: str
    client_id: str
    order_id: str
    state: str
    observed_at_ms: int
    payload_sha256: str
    _token: object = field(repr=False, compare=False)


def _validated_admission_evidence(
    payload: Mapping[str, Any],
    *,
    expected_client_id: str,
    channel: str,
    observed_at_ms: int,
    require_found: bool,
) -> ValidatedAdmissionEvidence:
    if require_found and payload.get("found") is not True:
        raise InvalidAdmissionEvidence("order-detail response did not prove an order exists")
    client_id = payload.get("clientId")
    order_id = payload.get("orderId")
    state = str(payload.get("state", "")).upper()
    if client_id != expected_client_id:
        raise InvalidAdmissionEvidence("venue evidence clientId does not match the intent")
    if not isinstance(order_id, str) or not order_id.strip():
        raise InvalidAdmissionEvidence("venue evidence requires a non-empty orderId")
    if state not in _ADMISSIBLE_STATES:
        raise InvalidAdmissionEvidence("venue evidence contains an inadmissible order state")
    for key in ("filledQty", "avgPrice", "qty", "price"):
        value = payload.get(key)
        if value is not None:
            try:
                _finite_decimal(key, value)
            except SafetyControlError as exc:
                raise InvalidAdmissionEvidence(
                    f"venue evidence contains non-finite {key}"
                ) from exc
    timestamp = _positive_timestamp("observed_at_ms", observed_at_ms)
    try:
        payload_hash = canonical_sha256(dict(payload))
    except (TypeError, ValueError) as exc:
        raise InvalidAdmissionEvidence("venue evidence is not canonical JSON") from exc
    return ValidatedAdmissionEvidence(
        channel=channel,
        client_id=client_id,
        order_id=order_id.strip(),
        state=state,
        observed_at_ms=timestamp,
        payload_sha256=payload_hash,
        _token=_ADMISSION_TOKEN,
    )


def private_ws_evidence(
    payload: Mapping[str, Any], *, expected_client_id: str, observed_at_ms: int
) -> ValidatedAdmissionEvidence:
    return _validated_admission_evidence(
        payload,
        expected_client_id=expected_client_id,
        channel="private_ws",
        observed_at_ms=observed_at_ms,
        require_found=False,
    )


def order_detail_evidence(
    payload: Mapping[str, Any], *, expected_client_id: str, observed_at_ms: int
) -> ValidatedAdmissionEvidence:
    return _validated_admission_evidence(
        payload,
        expected_client_id=expected_client_id,
        channel="order_detail",
        observed_at_ms=observed_at_ms,
        require_found=True,
    )


def admit_order_state(
    *, expected_client_id: str, requested_state: str, evidence: object
) -> str:
    if (
        not isinstance(evidence, ValidatedAdmissionEvidence)
        or evidence._token is not _ADMISSION_TOKEN
    ):
        raise InvalidAdmissionEvidence(
            "state admission requires factory-validated private WS or order-detail evidence"
        )
    normalized_state = requested_state.upper()
    if evidence.client_id != expected_client_id:
        raise InvalidAdmissionEvidence("admission evidence belongs to another clientId")
    if evidence.state != normalized_state:
        raise InvalidAdmissionEvidence("requested state does not match venue evidence")
    if evidence.channel not in {"private_ws", "order_detail"}:
        raise InvalidAdmissionEvidence("untrusted admission evidence channel")
    return normalized_state


class DeadManSwitch:
    """Fail closed before the first heartbeat and latch after a timeout."""

    def __init__(self, timeout_ms: int = 5_000):
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0:
            raise SafetyControlError("timeout_ms must be a positive integer")
        self.timeout_ms = timeout_ms
        self._last_heartbeat_ms: int | None = None
        self._latched_reason: str | None = None
        self._last_reason = "first_heartbeat_missing"

    def heartbeat(self, now_ms: int) -> None:
        now = _positive_timestamp("now_ms", now_ms)
        if self._latched_reason is not None:
            raise DeadManLatched("dead-man is latched; explicit reset is required")
        if self._last_heartbeat_ms is not None and now < self._last_heartbeat_ms:
            self._latched_reason = "heartbeat_clock_regression"
            self._last_reason = self._latched_reason
            raise DeadManLatched(self._latched_reason)
        self._last_heartbeat_ms = now
        self._last_reason = ""

    def trip(self, reason: str) -> None:
        self._latched_reason = reason.strip() or "manual_trip"
        self._last_reason = self._latched_reason

    def reset_after_healthcheck(self, now_ms: int) -> None:
        now = _positive_timestamp("now_ms", now_ms)
        self._last_heartbeat_ms = now
        self._latched_reason = None
        self._last_reason = ""

    def is_tripped(self, now_ms: int) -> bool:
        now = _positive_timestamp("now_ms", now_ms)
        if self._latched_reason is not None:
            self._last_reason = self._latched_reason
            return True
        if self._last_heartbeat_ms is None:
            self._last_reason = "first_heartbeat_missing"
            return True
        if now < self._last_heartbeat_ms:
            self._latched_reason = "heartbeat_clock_regression"
            self._last_reason = self._latched_reason
            return True
        if now - self._last_heartbeat_ms > self.timeout_ms:
            self._latched_reason = "deadman_timeout"
            self._last_reason = self._latched_reason
            return True
        self._last_reason = ""
        return False

    @property
    def reason(self) -> str:
        return self._last_reason


@dataclass(frozen=True)
class ObservedPosition:
    symbol: str
    signed_qty: str
    observed_at_ms: int
    source: str
    position_id: str | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if not symbol:
            raise SafetyControlError("observed position symbol is mandatory")
        _finite_decimal("signed_qty", self.signed_qty)
        _positive_timestamp("observed_at_ms", self.observed_at_ms)
        if self.source not in {"private_ws", "order_detail", "reconciliation_snapshot"}:
            raise SafetyControlError("observed position source is not admissible")
        object.__setattr__(self, "symbol", symbol)


@dataclass(frozen=True)
class ReduceOnlyDecision:
    approved: bool
    reason: str
    observed_abs_qty: str


def authorize_reduce_only(
    intent: ImmutableIntent,
    observed_position: ObservedPosition | None,
    *,
    now_ms: int,
    max_position_age_ms: int = 5_000,
) -> ReduceOnlyDecision:
    try:
        now = _positive_timestamp("now_ms", now_ms)
    except SafetyControlError as exc:
        return ReduceOnlyDecision(False, str(exc), "0")
    if not intent.reduce_only:
        return ReduceOnlyDecision(False, "intent_is_not_reduce_only", "0")
    if observed_position is None:
        return ReduceOnlyDecision(False, "observed_position_missing", "0")
    if (
        isinstance(max_position_age_ms, bool)
        or not isinstance(max_position_age_ms, int)
        or max_position_age_ms <= 0
    ):
        return ReduceOnlyDecision(False, "invalid_position_age_limit", "0")
    if observed_position.symbol != intent.symbol:
        return ReduceOnlyDecision(False, "observed_position_symbol_mismatch", "0")
    age_ms = now - observed_position.observed_at_ms
    if age_ms < 0:
        return ReduceOnlyDecision(False, "observed_position_clock_regression", "0")
    if age_ms > max_position_age_ms:
        return ReduceOnlyDecision(False, "observed_position_stale", "0")
    position_qty = _finite_decimal("signed_qty", observed_position.signed_qty)
    observed_abs = abs(position_qty)
    if observed_abs == 0:
        return ReduceOnlyDecision(False, "no_observed_position_to_reduce", "0")
    if observed_position.position_id is not None:
        if intent.position_id != observed_position.position_id:
            return ReduceOnlyDecision(
                False, "observed_position_identity_mismatch", str(observed_abs)
            )
    expected_side = "SELL" if position_qty > 0 else "BUY"
    if intent.side != expected_side:
        return ReduceOnlyDecision(False, "order_side_would_increase_exposure", str(observed_abs))
    if _positive_decimal("qty", intent.qty) > observed_abs:
        return ReduceOnlyDecision(False, "reduce_qty_exceeds_observed_position", str(observed_abs))
    return ReduceOnlyDecision(True, "observed_position_reduce_only", str(observed_abs))

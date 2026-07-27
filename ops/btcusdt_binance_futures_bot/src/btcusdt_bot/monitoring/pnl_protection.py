from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
import json

_ZERO = Decimal("0")
_ONE = Decimal("1")


def _to_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _utc_date(ms_value: int) -> str:
    return datetime.fromtimestamp(ms_value / 1000, tz=UTC).strftime("%Y-%m-%d")


@dataclass(slots=True)
class SessionEquitySnapshot:
    symbol: str
    asset: str
    source: str
    event_time_ms: int
    wallet_balance_usdt: Decimal
    unrealized_pnl_usdt: Decimal
    position_qty: Decimal
    estimated_equity_usdt: Decimal


@dataclass(slots=True)
class PnLSessionAnchor:
    anchor_date: str
    started_at_ms: int
    baseline_equity_usdt: Decimal
    peak_equity_usdt: Decimal
    peak_equity_at_ms: int
    latest_equity_usdt: Decimal
    latest_event_time_ms: int
    asset: str = "USDT"
    symbol: str = ""
    source: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "PnLSessionAnchor":
        return cls(
            anchor_date=str(payload.get("anchor_date", "") or ""),
            started_at_ms=int(payload.get("started_at_ms", 0) or 0),
            baseline_equity_usdt=_to_decimal(payload.get("baseline_equity_usdt")) or _ZERO,
            peak_equity_usdt=_to_decimal(payload.get("peak_equity_usdt")) or _ZERO,
            peak_equity_at_ms=int(payload.get("peak_equity_at_ms", 0) or 0),
            latest_equity_usdt=_to_decimal(payload.get("latest_equity_usdt")) or _ZERO,
            latest_event_time_ms=int(payload.get("latest_event_time_ms", 0) or 0),
            asset=str(payload.get("asset", "USDT") or "USDT"),
            symbol=str(payload.get("symbol", "") or ""),
            source=str(payload.get("source", "") or ""),
        )


@dataclass(slots=True)
class PnLProtectionThresholds:
    max_session_loss_fraction_reduce: Decimal | None = Decimal("0.010")
    max_session_loss_fraction_observe: Decimal | None = Decimal("0.020")
    max_drawdown_fraction_reduce: Decimal | None = Decimal("0.008")
    max_drawdown_fraction_observe: Decimal | None = Decimal("0.015")
    max_unrealized_loss_fraction_reduce: Decimal | None = Decimal("0.006")
    max_unrealized_loss_fraction_observe: Decimal | None = Decimal("0.012")
    max_session_loss_usdt_reduce: Decimal | None = None
    max_session_loss_usdt_observe: Decimal | None = None
    max_drawdown_usdt_reduce: Decimal | None = None
    max_drawdown_usdt_observe: Decimal | None = None
    max_unrealized_loss_usdt_reduce: Decimal | None = None
    max_unrealized_loss_usdt_observe: Decimal | None = None


@dataclass(slots=True)
class PnLProtectionDecision:
    action: str
    size_multiplier: Decimal = _ONE
    score: Decimal = _ZERO
    moderate_breaches: int = 0
    severe_breaches: int = 0
    reasons: list[str] = field(default_factory=list)
    compared_at_ms: int = 0
    baseline_equity_usdt: Decimal = _ZERO
    peak_equity_usdt: Decimal = _ZERO
    current_equity_usdt: Decimal = _ZERO
    session_pnl_usdt: Decimal = _ZERO
    session_pnl_fraction: Decimal = _ZERO
    session_loss_usdt: Decimal = _ZERO
    session_loss_fraction: Decimal = _ZERO
    drawdown_usdt: Decimal = _ZERO
    drawdown_fraction: Decimal = _ZERO
    unrealized_pnl_usdt: Decimal = _ZERO
    unrealized_loss_usdt: Decimal = _ZERO
    unrealized_loss_fraction: Decimal = _ZERO
    position_qty: Decimal = _ZERO

    @property
    def observe_only(self) -> bool:
        return self.action == "observe_only"

    @property
    def reduce_size(self) -> bool:
        return self.action == "reduce_size"

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "PnLProtectionDecision":
        return cls(
            action=str(payload.get("action", "trade") or "trade"),
            size_multiplier=_to_decimal(payload.get("size_multiplier")) or _ONE,
            score=_to_decimal(payload.get("score")) or _ZERO,
            moderate_breaches=int(payload.get("moderate_breaches", 0) or 0),
            severe_breaches=int(payload.get("severe_breaches", 0) or 0),
            reasons=[str(item) for item in payload.get("reasons", [])],
            compared_at_ms=int(payload.get("compared_at_ms", 0) or 0),
            baseline_equity_usdt=_to_decimal(payload.get("baseline_equity_usdt")) or _ZERO,
            peak_equity_usdt=_to_decimal(payload.get("peak_equity_usdt")) or _ZERO,
            current_equity_usdt=_to_decimal(payload.get("current_equity_usdt")) or _ZERO,
            session_pnl_usdt=_to_decimal(payload.get("session_pnl_usdt")) or _ZERO,
            session_pnl_fraction=_to_decimal(payload.get("session_pnl_fraction")) or _ZERO,
            session_loss_usdt=_to_decimal(payload.get("session_loss_usdt")) or _ZERO,
            session_loss_fraction=_to_decimal(payload.get("session_loss_fraction")) or _ZERO,
            drawdown_usdt=_to_decimal(payload.get("drawdown_usdt")) or _ZERO,
            drawdown_fraction=_to_decimal(payload.get("drawdown_fraction")) or _ZERO,
            unrealized_pnl_usdt=_to_decimal(payload.get("unrealized_pnl_usdt")) or _ZERO,
            unrealized_loss_usdt=_to_decimal(payload.get("unrealized_loss_usdt")) or _ZERO,
            unrealized_loss_fraction=_to_decimal(payload.get("unrealized_loss_fraction")) or _ZERO,
            position_qty=_to_decimal(payload.get("position_qty")) or _ZERO,
        )


@dataclass(slots=True)
class PnLProtectionStatus:
    iterations: int = 0
    decisions_written: int = 0
    reduce_size_decisions: int = 0
    observe_only_decisions: int = 0
    last_action: str = ""
    last_path: str = ""
    last_error: str = ""
    baseline_equity_usdt: Decimal | None = None
    peak_equity_usdt: Decimal | None = None
    current_equity_usdt: Decimal | None = None
    session_loss_usdt: Decimal | None = None
    drawdown_usdt: Decimal | None = None
    unrealized_loss_usdt: Decimal | None = None


def _position_key(symbol: str, position_side: str) -> str:
    return f"{symbol}/{position_side}"


def _extract_state_event_time(payload: dict[str, object]) -> int:
    account = payload.get("account")
    if isinstance(account, dict):
        event_time = int(account.get("last_event_time_ms", 0) or 0)
        if event_time > 0:
            return event_time
    bootstrap_at = int(payload.get("last_bootstrap_at_ms", 0) or 0)
    if bootstrap_at > 0:
        return bootstrap_at
    summary = payload.get("last_bootstrap_summary")
    if isinstance(summary, dict):
        return int(summary.get("synced_at_ms", 0) or 0)
    return 0


def extract_session_equity_snapshot(
    payload: dict[str, object],
    *,
    symbol: str,
    asset: str = "USDT",
    position_side: str = "BOTH",
    source: str = "runtime_state",
) -> SessionEquitySnapshot:
    account = payload.get("account")
    balances: dict[str, object] = {}
    positions: dict[str, object] = {}
    if isinstance(account, dict):
        raw_balances = account.get("balances")
        if isinstance(raw_balances, dict):
            balances = raw_balances
        raw_positions = account.get("positions")
        if isinstance(raw_positions, dict):
            positions = raw_positions

    wallet_balance = _to_decimal(balances.get(asset)) or _ZERO
    position_payload = positions.get(_position_key(symbol, position_side)) or positions.get(_position_key(symbol, "BOTH"))
    unrealized_pnl = _ZERO
    position_qty = _ZERO
    if isinstance(position_payload, dict):
        unrealized_pnl = _to_decimal(
            position_payload.get("unrealized_pnl", position_payload.get("unRealizedProfit"))
        ) or _ZERO
        position_qty = _to_decimal(position_payload.get("amount", position_payload.get("positionAmt"))) or _ZERO

    event_time_ms = _extract_state_event_time(payload)
    estimated_equity = wallet_balance + unrealized_pnl
    return SessionEquitySnapshot(
        symbol=symbol,
        asset=asset,
        source=source,
        event_time_ms=event_time_ms,
        wallet_balance_usdt=wallet_balance,
        unrealized_pnl_usdt=unrealized_pnl,
        position_qty=position_qty,
        estimated_equity_usdt=estimated_equity,
    )


def load_json(path: str | Path) -> dict[str, object]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_runtime_state(path: str | Path) -> dict[str, object]:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError("runtime_state_invalid")
    return payload


def load_session_anchor(path: str | Path) -> PnLSessionAnchor:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError("pnl_anchor_invalid")
    return PnLSessionAnchor.from_payload(payload)


def seed_session_anchor(snapshot: SessionEquitySnapshot, *, started_at_ms: int | None = None) -> PnLSessionAnchor:
    seed_time_ms = started_at_ms if started_at_ms is not None else snapshot.event_time_ms
    seed_time_ms = seed_time_ms or 0
    anchor_date = _utc_date(seed_time_ms) if seed_time_ms > 0 else ""
    return PnLSessionAnchor(
        anchor_date=anchor_date,
        started_at_ms=seed_time_ms,
        baseline_equity_usdt=snapshot.estimated_equity_usdt,
        peak_equity_usdt=snapshot.estimated_equity_usdt,
        peak_equity_at_ms=seed_time_ms,
        latest_equity_usdt=snapshot.estimated_equity_usdt,
        latest_event_time_ms=snapshot.event_time_ms,
        asset=snapshot.asset,
        symbol=snapshot.symbol,
        source=snapshot.source,
    )


def update_session_anchor(
    anchor: PnLSessionAnchor,
    *,
    snapshot: SessionEquitySnapshot,
    reset_on_new_utc_day: bool = True,
) -> PnLSessionAnchor:
    anchor_date = anchor.anchor_date
    snapshot_date = _utc_date(snapshot.event_time_ms) if snapshot.event_time_ms > 0 else anchor_date
    if reset_on_new_utc_day and anchor_date and snapshot_date and snapshot_date != anchor_date:
        return seed_session_anchor(snapshot)

    peak_equity = anchor.peak_equity_usdt
    peak_equity_at_ms = anchor.peak_equity_at_ms
    if snapshot.estimated_equity_usdt > peak_equity:
        peak_equity = snapshot.estimated_equity_usdt
        peak_equity_at_ms = snapshot.event_time_ms or anchor.peak_equity_at_ms

    return PnLSessionAnchor(
        anchor_date=anchor_date or snapshot_date,
        started_at_ms=anchor.started_at_ms or snapshot.event_time_ms,
        baseline_equity_usdt=anchor.baseline_equity_usdt,
        peak_equity_usdt=peak_equity,
        peak_equity_at_ms=peak_equity_at_ms,
        latest_equity_usdt=snapshot.estimated_equity_usdt,
        latest_event_time_ms=snapshot.event_time_ms,
        asset=snapshot.asset,
        symbol=snapshot.symbol,
        source=anchor.source or snapshot.source,
    )


def evaluate_pnl_protection(
    *,
    snapshot: SessionEquitySnapshot,
    anchor: PnLSessionAnchor,
    thresholds: PnLProtectionThresholds | None = None,
    compared_at_ms: int = 0,
) -> PnLProtectionDecision:
    thresholds = thresholds or PnLProtectionThresholds()
    reasons: list[str] = []
    moderate = 0
    severe = 0

    baseline_equity = max(anchor.baseline_equity_usdt, _ZERO)
    peak_equity = max(anchor.peak_equity_usdt, baseline_equity)
    current_equity = snapshot.estimated_equity_usdt
    session_pnl = current_equity - baseline_equity
    session_pnl_fraction = session_pnl / baseline_equity if baseline_equity > 0 else _ZERO
    session_loss_usdt = max(_ZERO, baseline_equity - current_equity)
    session_loss_fraction = session_loss_usdt / baseline_equity if baseline_equity > 0 else _ZERO
    drawdown_usdt = max(_ZERO, peak_equity - current_equity)
    drawdown_fraction = drawdown_usdt / peak_equity if peak_equity > 0 else _ZERO
    unrealized_loss_usdt = max(_ZERO, -snapshot.unrealized_pnl_usdt)
    unrealized_loss_fraction = unrealized_loss_usdt / baseline_equity if baseline_equity > 0 else _ZERO

    def flag(moderate_trigger: bool, severe_trigger: bool, moderate_reason: str, severe_reason: str) -> None:
        nonlocal moderate, severe
        if severe_trigger:
            severe += 1
            reasons.append(severe_reason)
        elif moderate_trigger:
            moderate += 1
            reasons.append(moderate_reason)

    if thresholds.max_session_loss_fraction_reduce is not None or thresholds.max_session_loss_fraction_observe is not None:
        flag(
            thresholds.max_session_loss_fraction_reduce is not None
            and session_loss_fraction > thresholds.max_session_loss_fraction_reduce,
            thresholds.max_session_loss_fraction_observe is not None
            and session_loss_fraction > thresholds.max_session_loss_fraction_observe,
            "session_loss_fraction_above_reduce_threshold",
            "session_loss_fraction_above_observe_threshold",
        )
    if thresholds.max_session_loss_usdt_reduce is not None or thresholds.max_session_loss_usdt_observe is not None:
        flag(
            thresholds.max_session_loss_usdt_reduce is not None
            and session_loss_usdt > thresholds.max_session_loss_usdt_reduce,
            thresholds.max_session_loss_usdt_observe is not None
            and session_loss_usdt > thresholds.max_session_loss_usdt_observe,
            "session_loss_usdt_above_reduce_threshold",
            "session_loss_usdt_above_observe_threshold",
        )
    if thresholds.max_drawdown_fraction_reduce is not None or thresholds.max_drawdown_fraction_observe is not None:
        flag(
            thresholds.max_drawdown_fraction_reduce is not None
            and drawdown_fraction > thresholds.max_drawdown_fraction_reduce,
            thresholds.max_drawdown_fraction_observe is not None
            and drawdown_fraction > thresholds.max_drawdown_fraction_observe,
            "drawdown_fraction_above_reduce_threshold",
            "drawdown_fraction_above_observe_threshold",
        )
    if thresholds.max_drawdown_usdt_reduce is not None or thresholds.max_drawdown_usdt_observe is not None:
        flag(
            thresholds.max_drawdown_usdt_reduce is not None
            and drawdown_usdt > thresholds.max_drawdown_usdt_reduce,
            thresholds.max_drawdown_usdt_observe is not None
            and drawdown_usdt > thresholds.max_drawdown_usdt_observe,
            "drawdown_usdt_above_reduce_threshold",
            "drawdown_usdt_above_observe_threshold",
        )
    if thresholds.max_unrealized_loss_fraction_reduce is not None or thresholds.max_unrealized_loss_fraction_observe is not None:
        flag(
            thresholds.max_unrealized_loss_fraction_reduce is not None
            and unrealized_loss_fraction > thresholds.max_unrealized_loss_fraction_reduce,
            thresholds.max_unrealized_loss_fraction_observe is not None
            and unrealized_loss_fraction > thresholds.max_unrealized_loss_fraction_observe,
            "unrealized_loss_fraction_above_reduce_threshold",
            "unrealized_loss_fraction_above_observe_threshold",
        )
    if thresholds.max_unrealized_loss_usdt_reduce is not None or thresholds.max_unrealized_loss_usdt_observe is not None:
        flag(
            thresholds.max_unrealized_loss_usdt_reduce is not None
            and unrealized_loss_usdt > thresholds.max_unrealized_loss_usdt_reduce,
            thresholds.max_unrealized_loss_usdt_observe is not None
            and unrealized_loss_usdt > thresholds.max_unrealized_loss_usdt_observe,
            "unrealized_loss_usdt_above_reduce_threshold",
            "unrealized_loss_usdt_above_observe_threshold",
        )

    if severe > 0 or moderate >= 3:
        action = "observe_only"
        size_multiplier = _ZERO
    elif moderate == 2:
        action = "reduce_size"
        size_multiplier = Decimal("0.50")
    elif moderate == 1:
        action = "reduce_size"
        size_multiplier = Decimal("0.75")
    else:
        action = "trade"
        size_multiplier = _ONE

    score = Decimal(moderate) + Decimal(severe) * Decimal("2")
    return PnLProtectionDecision(
        action=action,
        size_multiplier=size_multiplier,
        score=score,
        moderate_breaches=moderate,
        severe_breaches=severe,
        reasons=reasons,
        compared_at_ms=compared_at_ms,
        baseline_equity_usdt=baseline_equity,
        peak_equity_usdt=peak_equity,
        current_equity_usdt=current_equity,
        session_pnl_usdt=session_pnl,
        session_pnl_fraction=session_pnl_fraction,
        session_loss_usdt=session_loss_usdt,
        session_loss_fraction=session_loss_fraction,
        drawdown_usdt=drawdown_usdt,
        drawdown_fraction=drawdown_fraction,
        unrealized_pnl_usdt=snapshot.unrealized_pnl_usdt,
        unrealized_loss_usdt=unrealized_loss_usdt,
        unrealized_loss_fraction=unrealized_loss_fraction,
        position_qty=snapshot.position_qty,
    )

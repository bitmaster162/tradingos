from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
import json

from btcusdt_bot.monitoring.execution_drift import ExecutionDriftDecision
from btcusdt_bot.monitoring.intraday_protection import IntradayProtectionDecision
from btcusdt_bot.monitoring.pnl_protection import PnLProtectionDecision
from btcusdt_bot.monitoring.trade_reconciliation import TradeReconciliationDecision
from btcusdt_bot.monitoring.session_truth import SessionTruthDecision
from btcusdt_bot.monitoring.session_truth_trend import SessionTruthTrendDecision
from btcusdt_bot.monitoring.economics_regime import EconomicsRegimeDecision

_ZERO = Decimal("0")
_ONE = Decimal("1")
_ACTION_RANK = {"": 0, "trade": 1, "reduce_size": 2, "observe_only": 3}


def _to_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


@dataclass(slots=True)
class CombinedProtectionThresholds:
    observe_cooldown_seconds: int = 180
    min_trade_confirmations_to_relax_reduce: int = 2
    min_trade_confirmations_to_relax_observe: int = 3
    min_reduce_confirmations_to_relax_observe: int = 2
    enable_execution_pnl_observe_synergy: bool = True
    enable_execution_trade_reconciliation_observe_synergy: bool = True
    enable_execution_session_truth_observe_synergy: bool = True
    enable_execution_session_truth_trend_observe_synergy: bool = True
    enable_execution_economics_regime_observe_synergy: bool = True
    multisource_reduce_size_multiplier: Decimal = Decimal("0.50")


@dataclass(slots=True)
class CombinedProtectionDecision:
    action: str
    size_multiplier: Decimal = _ONE
    score: Decimal = _ZERO
    reasons: list[str] = field(default_factory=list)
    compared_at_ms: int = 0
    source_actions: dict[str, str] = field(default_factory=dict)
    source_size_multipliers: dict[str, Decimal] = field(default_factory=dict)
    cooldown_until_ms: int = 0
    co_degrade_triggered: bool = False
    hysteresis_applied: bool = False
    consecutive_trade_signals: int = 0
    consecutive_reduce_signals: int = 0

    @property
    def observe_only(self) -> bool:
        return self.action == "observe_only"

    @property
    def reduce_size(self) -> bool:
        return self.action == "reduce_size"

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "CombinedProtectionDecision":
        raw_source_actions = payload.get("source_actions")
        raw_source_size_multipliers = payload.get("source_size_multipliers")
        source_actions: dict[str, str] = {}
        if isinstance(raw_source_actions, dict):
            source_actions = {str(key): str(value) for key, value in raw_source_actions.items()}
        source_size_multipliers: dict[str, Decimal] = {}
        if isinstance(raw_source_size_multipliers, dict):
            for key, value in raw_source_size_multipliers.items():
                parsed = _to_decimal(value)
                if parsed is not None:
                    source_size_multipliers[str(key)] = parsed
        return cls(
            action=str(payload.get("action", "trade") or "trade"),
            size_multiplier=_to_decimal(payload.get("size_multiplier")) or _ONE,
            score=_to_decimal(payload.get("score")) or _ZERO,
            reasons=[str(item) for item in payload.get("reasons", [])],
            compared_at_ms=int(payload.get("compared_at_ms", 0) or 0),
            source_actions=source_actions,
            source_size_multipliers=source_size_multipliers,
            cooldown_until_ms=int(payload.get("cooldown_until_ms", 0) or 0),
            co_degrade_triggered=bool(payload.get("co_degrade_triggered", False)),
            hysteresis_applied=bool(payload.get("hysteresis_applied", False)),
            consecutive_trade_signals=int(payload.get("consecutive_trade_signals", 0) or 0),
            consecutive_reduce_signals=int(payload.get("consecutive_reduce_signals", 0) or 0),
        )


@dataclass(slots=True)
class CombinedProtectionState:
    last_action: str = "trade"
    last_size_multiplier: Decimal = _ONE
    cooldown_until_ms: int = 0
    consecutive_trade_signals: int = 0
    consecutive_reduce_signals: int = 0
    last_compared_at_ms: int = 0

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "CombinedProtectionState":
        return cls(
            last_action=str(payload.get("last_action", "trade") or "trade"),
            last_size_multiplier=_to_decimal(payload.get("last_size_multiplier")) or _ONE,
            cooldown_until_ms=int(payload.get("cooldown_until_ms", 0) or 0),
            consecutive_trade_signals=int(payload.get("consecutive_trade_signals", 0) or 0),
            consecutive_reduce_signals=int(payload.get("consecutive_reduce_signals", 0) or 0),
            last_compared_at_ms=int(payload.get("last_compared_at_ms", 0) or 0),
        )


@dataclass(slots=True)
class CombinedProtectionStatus:
    iterations: int = 0
    decisions_written: int = 0
    reduce_size_decisions: int = 0
    observe_only_decisions: int = 0
    last_action: str = ""
    last_path: str = ""
    last_error: str = ""
    last_cooldown_until_ms: int = 0


def _prefer_stronger_action(current: str, candidate: str) -> str:
    return candidate if _ACTION_RANK.get(candidate, 0) > _ACTION_RANK.get(current, 0) else current


def _source_multiplier(*decisions: object) -> Decimal:
    multipliers: list[Decimal] = []
    for decision in decisions:
        if decision is None:
            continue
        value = getattr(decision, "size_multiplier", None)
        if isinstance(value, Decimal):
            multipliers.append(value)
        elif value not in {None, "", "None"}:
            multipliers.append(Decimal(str(value)))
    return min(multipliers, default=_ONE)


def evaluate_combined_protection(
    *,
    execution_drift: ExecutionDriftDecision | None = None,
    intraday_protection: IntradayProtectionDecision | None = None,
    pnl_protection: PnLProtectionDecision | None = None,
    trade_reconciliation: TradeReconciliationDecision | None = None,
    session_truth: SessionTruthDecision | None = None,
    session_truth_trend: SessionTruthTrendDecision | None = None,
    economics_regime: EconomicsRegimeDecision | None = None,
    previous_state: CombinedProtectionState | None = None,
    thresholds: CombinedProtectionThresholds | None = None,
    compared_at_ms: int = 0,
) -> tuple[CombinedProtectionDecision, CombinedProtectionState]:
    thresholds = thresholds or CombinedProtectionThresholds()
    previous_state = previous_state or CombinedProtectionState()
    reasons: list[str] = []
    source_actions: dict[str, str] = {}
    source_size_multipliers: dict[str, Decimal] = {}
    score = _ZERO

    decisions = {
        "execution_drift": execution_drift,
        "intraday_protection": intraday_protection,
        "pnl_protection": pnl_protection,
        "trade_reconciliation": trade_reconciliation,
        "session_truth": session_truth,
        "session_truth_trend": session_truth_trend,
        "economics_regime": economics_regime,
    }

    reduce_sources: list[str] = []
    observe_sources: list[str] = []
    for source_name, decision in decisions.items():
        if decision is None:
            continue
        source_actions[source_name] = decision.action
        source_size_multipliers[source_name] = decision.size_multiplier
        score += getattr(decision, "score", _ZERO) or _ZERO
        for reason in getattr(decision, "reasons", []):
            reasons.append(f"{source_name}:{reason}")
        if decision.action == "observe_only":
            observe_sources.append(source_name)
        elif decision.action == "reduce_size":
            reduce_sources.append(source_name)

    proposed_action = "trade"
    proposed_multiplier = _ONE
    co_degrade_triggered = False

    if observe_sources:
        proposed_action = "observe_only"
        proposed_multiplier = _ZERO
    else:
        if (
            thresholds.enable_execution_pnl_observe_synergy
            and execution_drift is not None
            and pnl_protection is not None
            and execution_drift.action != "trade"
            and pnl_protection.action != "trade"
        ):
            proposed_action = "observe_only"
            proposed_multiplier = _ZERO
            co_degrade_triggered = True
            reasons.append("execution_pnl_co_degrade")
            score += Decimal("2")
        elif (
            thresholds.enable_execution_trade_reconciliation_observe_synergy
            and execution_drift is not None
            and trade_reconciliation is not None
            and execution_drift.action != "trade"
            and trade_reconciliation.action != "trade"
        ):
            proposed_action = "observe_only"
            proposed_multiplier = _ZERO
            co_degrade_triggered = True
            reasons.append("execution_trade_reconciliation_co_degrade")
            score += Decimal("2")
        elif (
            thresholds.enable_execution_session_truth_observe_synergy
            and execution_drift is not None
            and session_truth is not None
            and execution_drift.action != "trade"
            and session_truth.action != "trade"
        ):
            proposed_action = "observe_only"
            proposed_multiplier = _ZERO
            co_degrade_triggered = True
            reasons.append("execution_session_truth_co_degrade")
            score += Decimal("2")
        elif (
            thresholds.enable_execution_session_truth_trend_observe_synergy
            and execution_drift is not None
            and session_truth_trend is not None
            and execution_drift.action != "trade"
            and session_truth_trend.action != "trade"
        ):
            proposed_action = "observe_only"
            proposed_multiplier = _ZERO
            co_degrade_triggered = True
            reasons.append("execution_session_truth_trend_co_degrade")
            score += Decimal("2")
        elif (
            thresholds.enable_execution_economics_regime_observe_synergy
            and execution_drift is not None
            and economics_regime is not None
            and execution_drift.action != "trade"
            and economics_regime.action != "trade"
        ):
            proposed_action = "observe_only"
            proposed_multiplier = _ZERO
            co_degrade_triggered = True
            reasons.append("execution_economics_regime_co_degrade")
            score += Decimal("2")
        elif len(reduce_sources) >= 2:
            proposed_action = "reduce_size"
            proposed_multiplier = min(
                _source_multiplier(execution_drift, intraday_protection, pnl_protection, trade_reconciliation, session_truth, session_truth_trend, economics_regime),
                thresholds.multisource_reduce_size_multiplier,
            )
            reasons.append("multi_source_reduce")
            score += Decimal("1")
        elif len(reduce_sources) == 1:
            proposed_action = "reduce_size"
            proposed_multiplier = _source_multiplier(execution_drift, intraday_protection, pnl_protection, trade_reconciliation, session_truth, session_truth_trend, economics_regime)
        else:
            proposed_action = "trade"
            proposed_multiplier = _ONE

    proposed_trade_signals = previous_state.consecutive_trade_signals + 1 if proposed_action == "trade" else 0
    proposed_reduce_signals = previous_state.consecutive_reduce_signals + 1 if proposed_action == "reduce_size" else 0

    final_action = proposed_action
    final_multiplier = proposed_multiplier
    cooldown_until_ms = previous_state.cooldown_until_ms if previous_state.last_action == "observe_only" else 0
    hysteresis_applied = False

    if previous_state.last_action == "observe_only" and proposed_action != "observe_only":
        if compared_at_ms < previous_state.cooldown_until_ms:
            final_action = "observe_only"
            final_multiplier = _ZERO
            cooldown_until_ms = previous_state.cooldown_until_ms
            reasons.append("observe_cooldown_active")
            hysteresis_applied = True
        elif proposed_action == "trade" and proposed_trade_signals < thresholds.min_trade_confirmations_to_relax_observe:
            final_action = "observe_only"
            final_multiplier = _ZERO
            reasons.append("observe_hysteresis_wait_trade")
            hysteresis_applied = True
        elif proposed_action == "reduce_size" and proposed_reduce_signals < thresholds.min_reduce_confirmations_to_relax_observe:
            final_action = "observe_only"
            final_multiplier = _ZERO
            reasons.append("observe_hysteresis_wait_reduce")
            hysteresis_applied = True
    elif previous_state.last_action == "reduce_size" and proposed_action == "trade":
        if proposed_trade_signals < thresholds.min_trade_confirmations_to_relax_reduce:
            final_action = "reduce_size"
            fallback_multiplier = previous_state.last_size_multiplier if _ZERO < previous_state.last_size_multiplier < _ONE else Decimal("0.75")
            final_multiplier = fallback_multiplier
            reasons.append("reduce_hysteresis_wait_trade")
            hysteresis_applied = True

    if final_action == "observe_only":
        if proposed_action == "observe_only":
            cooldown_until_ms = max(previous_state.cooldown_until_ms, compared_at_ms + thresholds.observe_cooldown_seconds * 1000)
        elif cooldown_until_ms < compared_at_ms:
            cooldown_until_ms = compared_at_ms
    else:
        cooldown_until_ms = 0

    if hysteresis_applied:
        score += Decimal("1")

    decision = CombinedProtectionDecision(
        action=final_action,
        size_multiplier=final_multiplier,
        score=score,
        reasons=reasons,
        compared_at_ms=compared_at_ms,
        source_actions=source_actions,
        source_size_multipliers=source_size_multipliers,
        cooldown_until_ms=cooldown_until_ms,
        co_degrade_triggered=co_degrade_triggered,
        hysteresis_applied=hysteresis_applied,
        consecutive_trade_signals=proposed_trade_signals,
        consecutive_reduce_signals=proposed_reduce_signals,
    )
    state = CombinedProtectionState(
        last_action=final_action,
        last_size_multiplier=final_multiplier,
        cooldown_until_ms=cooldown_until_ms,
        consecutive_trade_signals=proposed_trade_signals,
        consecutive_reduce_signals=proposed_reduce_signals,
        last_compared_at_ms=compared_at_ms,
    )
    return decision, state


def load_json(path: str | Path) -> dict[str, object]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_combined_protection_state(path: str | Path) -> CombinedProtectionState:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError("combined_protection_state_invalid")
    return CombinedProtectionState.from_payload(payload)

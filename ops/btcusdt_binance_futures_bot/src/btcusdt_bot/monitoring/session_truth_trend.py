from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from btcusdt_bot.reporting.session_truth_report import SessionTruthReport

_ZERO = Decimal("0")
_ONE = Decimal("1")


def _to_decimal(value: object) -> Decimal | None:
    if value in {None, "", "None"}:
        return None
    return Decimal(str(value))


@dataclass(slots=True)
class SessionTruthTrendThresholds:
    min_active_bucket_count: int = 3
    max_negative_bucket_ratio_reduce: Decimal = Decimal("0.50")
    max_negative_bucket_ratio_observe: Decimal = Decimal("0.75")
    consecutive_negative_buckets_reduce: int = 2
    consecutive_negative_buckets_observe: int = 3
    max_negative_recent_bucket_net_realized_bps_reduce: Decimal = Decimal("1.00")
    max_negative_recent_bucket_net_realized_bps_observe: Decimal = Decimal("3.00")
    max_negative_recent_two_bucket_net_realized_bps_reduce: Decimal = Decimal("0.75")
    max_negative_recent_two_bucket_net_realized_bps_observe: Decimal = Decimal("2.50")
    min_recent_bucket_maker_ratio_reduce: Decimal = Decimal("0.35")
    min_recent_bucket_maker_ratio_observe: Decimal = Decimal("0.15")
    max_negative_worst_bucket_net_realized_bps_reduce: Decimal = Decimal("3.00")
    max_negative_worst_bucket_net_realized_bps_observe: Decimal = Decimal("8.00")
    max_cumulative_drawdown_usdt_reduce: Decimal = Decimal("5.00")
    max_cumulative_drawdown_usdt_observe: Decimal = Decimal("15.00")
    reduce_size_multiplier: Decimal = Decimal("0.60")


@dataclass(slots=True)
class SessionTruthTrendDecision:
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
    bucket_ms: int = 0
    bucket_count: int = 0
    active_bucket_count: int = 0
    negative_bucket_count: int = 0
    negative_bucket_ratio: Decimal = _ZERO
    trailing_negative_bucket_streak: int = 0
    recent_bucket_net_realized_bps: Decimal = _ZERO
    recent_two_bucket_net_realized_bps: Decimal = _ZERO
    recent_bucket_maker_ratio: Decimal = _ZERO
    worst_bucket_net_realized_bps: Decimal = _ZERO
    cumulative_drawdown_usdt: Decimal = _ZERO
    sample_ready: bool = False

    @property
    def observe_only(self) -> bool:
        return self.action == "observe_only"

    @property
    def reduce_size(self) -> bool:
        return self.action == "reduce_size"

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "SessionTruthTrendDecision":
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
            bucket_ms=int(payload.get("bucket_ms", 0) or 0),
            bucket_count=int(payload.get("bucket_count", 0) or 0),
            active_bucket_count=int(payload.get("active_bucket_count", 0) or 0),
            negative_bucket_count=int(payload.get("negative_bucket_count", 0) or 0),
            negative_bucket_ratio=_to_decimal(payload.get("negative_bucket_ratio")) or _ZERO,
            trailing_negative_bucket_streak=int(payload.get("trailing_negative_bucket_streak", 0) or 0),
            recent_bucket_net_realized_bps=_to_decimal(payload.get("recent_bucket_net_realized_bps")) or _ZERO,
            recent_two_bucket_net_realized_bps=_to_decimal(payload.get("recent_two_bucket_net_realized_bps")) or _ZERO,
            recent_bucket_maker_ratio=_to_decimal(payload.get("recent_bucket_maker_ratio")) or _ZERO,
            worst_bucket_net_realized_bps=_to_decimal(payload.get("worst_bucket_net_realized_bps")) or _ZERO,
            cumulative_drawdown_usdt=_to_decimal(payload.get("cumulative_drawdown_usdt")) or _ZERO,
            sample_ready=bool(payload.get("sample_ready", False)),
        )


@dataclass(slots=True)
class SessionTruthTrendStatus:
    iterations: int = 0
    decisions_written: int = 0
    reduce_size_decisions: int = 0
    observe_only_decisions: int = 0
    last_action: str = ""
    last_path: str = ""
    last_error: str = ""
    active_bucket_count: int = 0
    negative_bucket_ratio: Decimal | None = None
    recent_bucket_net_realized_bps: Decimal | None = None
    cumulative_drawdown_usdt: Decimal | None = None


def evaluate_session_truth_trend(
    *,
    report: SessionTruthReport,
    thresholds: SessionTruthTrendThresholds | None = None,
    compared_at_ms: int = 0,
) -> SessionTruthTrendDecision:
    thresholds = thresholds or SessionTruthTrendThresholds()
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

    sample_ready = report.active_bucket_count >= thresholds.min_active_bucket_count
    if sample_ready:
        flag(
            report.negative_bucket_ratio > thresholds.max_negative_bucket_ratio_reduce,
            report.negative_bucket_ratio > thresholds.max_negative_bucket_ratio_observe,
            "negative_bucket_ratio_above_reduce_threshold",
            "negative_bucket_ratio_above_observe_threshold",
        )
        flag(
            report.trailing_negative_bucket_streak >= thresholds.consecutive_negative_buckets_reduce,
            report.trailing_negative_bucket_streak >= thresholds.consecutive_negative_buckets_observe,
            "trailing_negative_bucket_streak_above_reduce_threshold",
            "trailing_negative_bucket_streak_above_observe_threshold",
        )
        flag(
            report.recent_bucket_net_realized_bps < -thresholds.max_negative_recent_bucket_net_realized_bps_reduce,
            report.recent_bucket_net_realized_bps < -thresholds.max_negative_recent_bucket_net_realized_bps_observe,
            "recent_bucket_net_realized_bps_below_reduce_threshold",
            "recent_bucket_net_realized_bps_below_observe_threshold",
        )
        flag(
            report.recent_two_bucket_net_realized_bps < -thresholds.max_negative_recent_two_bucket_net_realized_bps_reduce,
            report.recent_two_bucket_net_realized_bps < -thresholds.max_negative_recent_two_bucket_net_realized_bps_observe,
            "recent_two_bucket_net_realized_bps_below_reduce_threshold",
            "recent_two_bucket_net_realized_bps_below_observe_threshold",
        )
        flag(
            report.recent_bucket_maker_ratio < thresholds.min_recent_bucket_maker_ratio_reduce,
            report.recent_bucket_maker_ratio < thresholds.min_recent_bucket_maker_ratio_observe,
            "recent_bucket_maker_ratio_below_reduce_threshold",
            "recent_bucket_maker_ratio_below_observe_threshold",
        )
        flag(
            report.worst_bucket_net_realized_bps < -thresholds.max_negative_worst_bucket_net_realized_bps_reduce,
            report.worst_bucket_net_realized_bps < -thresholds.max_negative_worst_bucket_net_realized_bps_observe,
            "worst_bucket_net_realized_bps_below_reduce_threshold",
            "worst_bucket_net_realized_bps_below_observe_threshold",
        )
        flag(
            report.cumulative_drawdown_usdt > thresholds.max_cumulative_drawdown_usdt_reduce,
            report.cumulative_drawdown_usdt > thresholds.max_cumulative_drawdown_usdt_observe,
            "cumulative_drawdown_above_reduce_threshold",
            "cumulative_drawdown_above_observe_threshold",
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

    score = Decimal(moderate) + (Decimal(severe) * Decimal("2"))
    return SessionTruthTrendDecision(
        action=action,
        size_multiplier=size_multiplier,
        score=score,
        moderate_breaches=moderate,
        severe_breaches=severe,
        reasons=reasons,
        compared_at_ms=compared_at_ms or report.compared_at_ms,
        lookback_start_ms=report.lookback_start_ms,
        lookback_end_ms=report.lookback_end_ms,
        window_mode=report.window_mode,
        session_started_at_ms=report.session_started_at_ms,
        bucket_ms=report.bucket_ms,
        bucket_count=report.bucket_count,
        active_bucket_count=report.active_bucket_count,
        negative_bucket_count=report.negative_bucket_count,
        negative_bucket_ratio=report.negative_bucket_ratio,
        trailing_negative_bucket_streak=report.trailing_negative_bucket_streak,
        recent_bucket_net_realized_bps=report.recent_bucket_net_realized_bps,
        recent_two_bucket_net_realized_bps=report.recent_two_bucket_net_realized_bps,
        recent_bucket_maker_ratio=report.recent_bucket_maker_ratio,
        worst_bucket_net_realized_bps=report.worst_bucket_net_realized_bps,
        cumulative_drawdown_usdt=report.cumulative_drawdown_usdt,
        sample_ready=sample_ready,
    )

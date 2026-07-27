from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from btcusdt_bot.reporting.economics_dashboard import EconomicsDashboard

_ZERO = Decimal("0")
_ONE = Decimal("1")


def _to_decimal(value: object) -> Decimal | None:
    if value in {None, "", "None"}:
        return None
    return Decimal(str(value))


@dataclass(slots=True)
class EconomicsRegimeThresholds:
    min_active_day_count: int = 3
    max_negative_day_ratio_reduce: Decimal = Decimal("0.50")
    max_negative_day_ratio_observe: Decimal = Decimal("0.75")
    consecutive_negative_days_reduce: int = 2
    consecutive_negative_days_observe: int = 3
    max_negative_recent_day_net_realized_bps_reduce: Decimal = Decimal("1.00")
    max_negative_recent_day_net_realized_bps_observe: Decimal = Decimal("3.00")
    max_negative_recent_two_day_net_realized_bps_reduce: Decimal = Decimal("0.75")
    max_negative_recent_two_day_net_realized_bps_observe: Decimal = Decimal("2.50")
    min_average_maker_ratio_reduce: Decimal = Decimal("0.35")
    min_average_maker_ratio_observe: Decimal = Decimal("0.15")
    max_average_commission_bps_reduce: Decimal = Decimal("6.00")
    max_average_commission_bps_observe: Decimal = Decimal("10.00")
    max_negative_average_funding_bps_reduce: Decimal = Decimal("0.50")
    max_negative_average_funding_bps_observe: Decimal = Decimal("2.00")
    max_average_negative_bucket_ratio_reduce: Decimal = Decimal("0.50")
    max_average_negative_bucket_ratio_observe: Decimal = Decimal("0.75")
    max_cumulative_drawdown_usdt_reduce: Decimal = Decimal("10.00")
    max_cumulative_drawdown_usdt_observe: Decimal = Decimal("25.00")
    reduce_size_multiplier: Decimal = Decimal("0.60")


@dataclass(slots=True)
class EconomicsRegimeDecision:
    action: str
    size_multiplier: Decimal = _ONE
    score: Decimal = _ZERO
    moderate_breaches: int = 0
    severe_breaches: int = 0
    reasons: list[str] = field(default_factory=list)
    compared_at_ms: int = 0
    start_date: str = ""
    end_date: str = ""
    lookback_days: int = 0
    available_day_count: int = 0
    missing_day_count: int = 0
    active_day_count: int = 0
    negative_day_count: int = 0
    negative_day_ratio: Decimal = _ZERO
    trailing_negative_day_streak: int = 0
    recent_day_net_realized_bps: Decimal = _ZERO
    recent_two_day_net_realized_bps: Decimal = _ZERO
    average_maker_ratio: Decimal = _ZERO
    average_commission_bps: Decimal = _ZERO
    average_funding_bps: Decimal = _ZERO
    average_negative_bucket_ratio: Decimal = _ZERO
    cumulative_drawdown_usdt: Decimal = _ZERO
    aggregate_net_realized_bps: Decimal = _ZERO
    total_net_realized_pnl_usdt: Decimal = _ZERO
    sample_ready: bool = False

    @property
    def observe_only(self) -> bool:
        return self.action == "observe_only"

    @property
    def reduce_size(self) -> bool:
        return self.action == "reduce_size"

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "EconomicsRegimeDecision":
        return cls(
            action=str(payload.get("action", "trade") or "trade"),
            size_multiplier=_to_decimal(payload.get("size_multiplier")) or _ONE,
            score=_to_decimal(payload.get("score")) or _ZERO,
            moderate_breaches=int(payload.get("moderate_breaches", 0) or 0),
            severe_breaches=int(payload.get("severe_breaches", 0) or 0),
            reasons=[str(item) for item in payload.get("reasons", [])],
            compared_at_ms=int(payload.get("compared_at_ms", 0) or 0),
            start_date=str(payload.get("start_date", "") or ""),
            end_date=str(payload.get("end_date", "") or ""),
            lookback_days=int(payload.get("lookback_days", 0) or 0),
            available_day_count=int(payload.get("available_day_count", 0) or 0),
            missing_day_count=int(payload.get("missing_day_count", 0) or 0),
            active_day_count=int(payload.get("active_day_count", 0) or 0),
            negative_day_count=int(payload.get("negative_day_count", 0) or 0),
            negative_day_ratio=_to_decimal(payload.get("negative_day_ratio")) or _ZERO,
            trailing_negative_day_streak=int(payload.get("trailing_negative_day_streak", 0) or 0),
            recent_day_net_realized_bps=_to_decimal(payload.get("recent_day_net_realized_bps")) or _ZERO,
            recent_two_day_net_realized_bps=_to_decimal(payload.get("recent_two_day_net_realized_bps")) or _ZERO,
            average_maker_ratio=_to_decimal(payload.get("average_maker_ratio")) or _ZERO,
            average_commission_bps=_to_decimal(payload.get("average_commission_bps")) or _ZERO,
            average_funding_bps=_to_decimal(payload.get("average_funding_bps")) or _ZERO,
            average_negative_bucket_ratio=_to_decimal(payload.get("average_negative_bucket_ratio")) or _ZERO,
            cumulative_drawdown_usdt=_to_decimal(payload.get("cumulative_drawdown_usdt")) or _ZERO,
            aggregate_net_realized_bps=_to_decimal(payload.get("aggregate_net_realized_bps")) or _ZERO,
            total_net_realized_pnl_usdt=_to_decimal(payload.get("total_net_realized_pnl_usdt")) or _ZERO,
            sample_ready=bool(payload.get("sample_ready", False)),
        )


@dataclass(slots=True)
class EconomicsRegimeStatus:
    iterations: int = 0
    decisions_written: int = 0
    reduce_size_decisions: int = 0
    observe_only_decisions: int = 0
    last_action: str = ""
    last_path: str = ""
    last_error: str = ""
    active_day_count: int = 0
    negative_day_ratio: Decimal | None = None
    recent_day_net_realized_bps: Decimal | None = None
    cumulative_drawdown_usdt: Decimal | None = None


def evaluate_economics_regime(
    *,
    dashboard: EconomicsDashboard,
    thresholds: EconomicsRegimeThresholds | None = None,
    compared_at_ms: int = 0,
) -> EconomicsRegimeDecision:
    thresholds = thresholds or EconomicsRegimeThresholds()
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

    sample_ready = dashboard.active_day_count >= thresholds.min_active_day_count
    if sample_ready:
        flag(
            dashboard.negative_day_ratio > thresholds.max_negative_day_ratio_reduce,
            dashboard.negative_day_ratio > thresholds.max_negative_day_ratio_observe,
            "negative_day_ratio_above_reduce_threshold",
            "negative_day_ratio_above_observe_threshold",
        )
        flag(
            dashboard.trailing_negative_day_streak >= thresholds.consecutive_negative_days_reduce,
            dashboard.trailing_negative_day_streak >= thresholds.consecutive_negative_days_observe,
            "trailing_negative_day_streak_above_reduce_threshold",
            "trailing_negative_day_streak_above_observe_threshold",
        )
        flag(
            dashboard.recent_day_net_realized_bps < -thresholds.max_negative_recent_day_net_realized_bps_reduce,
            dashboard.recent_day_net_realized_bps < -thresholds.max_negative_recent_day_net_realized_bps_observe,
            "recent_day_net_realized_bps_below_reduce_threshold",
            "recent_day_net_realized_bps_below_observe_threshold",
        )
        flag(
            dashboard.recent_two_day_net_realized_bps < -thresholds.max_negative_recent_two_day_net_realized_bps_reduce,
            dashboard.recent_two_day_net_realized_bps < -thresholds.max_negative_recent_two_day_net_realized_bps_observe,
            "recent_two_day_net_realized_bps_below_reduce_threshold",
            "recent_two_day_net_realized_bps_below_observe_threshold",
        )
        flag(
            dashboard.average_maker_ratio < thresholds.min_average_maker_ratio_reduce,
            dashboard.average_maker_ratio < thresholds.min_average_maker_ratio_observe,
            "average_maker_ratio_below_reduce_threshold",
            "average_maker_ratio_below_observe_threshold",
        )
        flag(
            dashboard.average_commission_bps > thresholds.max_average_commission_bps_reduce,
            dashboard.average_commission_bps > thresholds.max_average_commission_bps_observe,
            "average_commission_bps_above_reduce_threshold",
            "average_commission_bps_above_observe_threshold",
        )
        flag(
            dashboard.average_funding_bps < -thresholds.max_negative_average_funding_bps_reduce,
            dashboard.average_funding_bps < -thresholds.max_negative_average_funding_bps_observe,
            "average_funding_bps_below_reduce_threshold",
            "average_funding_bps_below_observe_threshold",
        )
        flag(
            dashboard.average_negative_bucket_ratio > thresholds.max_average_negative_bucket_ratio_reduce,
            dashboard.average_negative_bucket_ratio > thresholds.max_average_negative_bucket_ratio_observe,
            "average_negative_bucket_ratio_above_reduce_threshold",
            "average_negative_bucket_ratio_above_observe_threshold",
        )
        flag(
            dashboard.cumulative_drawdown_usdt > thresholds.max_cumulative_drawdown_usdt_reduce,
            dashboard.cumulative_drawdown_usdt > thresholds.max_cumulative_drawdown_usdt_observe,
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
    return EconomicsRegimeDecision(
        action=action,
        size_multiplier=size_multiplier,
        score=score,
        moderate_breaches=moderate,
        severe_breaches=severe,
        reasons=reasons,
        compared_at_ms=compared_at_ms,
        start_date=dashboard.start_date,
        end_date=dashboard.end_date,
        lookback_days=dashboard.lookback_days,
        available_day_count=dashboard.available_day_count,
        missing_day_count=dashboard.missing_day_count,
        active_day_count=dashboard.active_day_count,
        negative_day_count=dashboard.negative_day_count,
        negative_day_ratio=dashboard.negative_day_ratio,
        trailing_negative_day_streak=dashboard.trailing_negative_day_streak,
        recent_day_net_realized_bps=dashboard.recent_day_net_realized_bps,
        recent_two_day_net_realized_bps=dashboard.recent_two_day_net_realized_bps,
        average_maker_ratio=dashboard.average_maker_ratio,
        average_commission_bps=dashboard.average_commission_bps,
        average_funding_bps=dashboard.average_funding_bps,
        average_negative_bucket_ratio=dashboard.average_negative_bucket_ratio,
        cumulative_drawdown_usdt=dashboard.cumulative_drawdown_usdt,
        aggregate_net_realized_bps=dashboard.aggregate_net_realized_bps,
        total_net_realized_pnl_usdt=dashboard.total_net_realized_pnl_usdt,
        sample_ready=sample_ready,
    )

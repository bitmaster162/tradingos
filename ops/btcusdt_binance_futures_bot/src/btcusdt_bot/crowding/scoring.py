from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.domain.enums import Side


_RATIO_ONE = Decimal("1")
_ZERO = Decimal("0")


def _decimal_or_none(value: object | None) -> Decimal | None:
    if value in {None, "", "None"}:
        return None
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def _extract_ratio(snapshot: dict[str, Any], key: str, *, fallbacks: tuple[str, ...] = ()) -> Decimal | None:
    if not isinstance(snapshot, dict):
        return None
    for candidate in (key, *fallbacks):
        value = snapshot.get(candidate)
        ratio = _decimal_or_none(value)
        if ratio is not None:
            return ratio
    return None


def _extract_timestamp_ms(payload: dict[str, Any]) -> int:
    if not isinstance(payload, dict):
        return 0
    for key in ("snapshot_time_ms", "timestamp", "time", "T", "E"):
        value = payload.get(key)
        if value in {None, "", "None"}:
            continue
        try:
            return int(value)
        except Exception:  # noqa: BLE001
            continue
    return 0


@dataclass(slots=True)
class CrowdingContext:
    symbol: str
    period: str
    snapshot_time_ms: int
    snapshot_age_ms: int
    open_interest_current: Decimal | None
    open_interest_reference: Decimal | None
    oi_change_ratio: Decimal | None
    global_long_short_ratio: Decimal | None
    top_account_long_short_ratio: Decimal | None
    top_position_long_short_ratio: Decimal | None
    taker_buy_sell_ratio: Decimal | None


@dataclass(slots=True)
class CrowdingGateConfig:
    enabled: bool = False
    max_snapshot_age_ms: int | None = None
    min_side_score: Decimal | None = None
    oi_expansion_weight: Decimal = Decimal("0.5")
    max_component_value: Decimal = Decimal("1.5")


@dataclass(slots=True)
class CrowdingScore:
    symbol: str
    period: str
    side: Side
    snapshot_time_ms: int
    snapshot_age_ms: int
    directional_taker_component: Decimal
    oi_expansion_component: Decimal
    crowding_penalty: Decimal
    side_score: Decimal
    global_long_short_ratio: Decimal | None
    top_account_long_short_ratio: Decimal | None
    top_position_long_short_ratio: Decimal | None
    taker_buy_sell_ratio: Decimal | None
    oi_change_ratio: Decimal | None


def build_crowding_context(snapshot: dict[str, Any], *, now_ms_value: int | None = None) -> CrowdingContext | None:
    if not isinstance(snapshot, dict) or not snapshot:
        return None

    open_interest_payload = snapshot.get("open_interest") or {}
    open_interest_hist_payload = snapshot.get("open_interest_hist") or {}
    global_ratio_payload = snapshot.get("global_long_short_account_ratio") or {}
    top_account_ratio_payload = snapshot.get("top_long_short_account_ratio") or {}
    top_position_ratio_payload = snapshot.get("top_long_short_position_ratio") or {}
    taker_ratio_payload = snapshot.get("taker_buy_sell_ratio") or {}

    snapshot_time_ms = _extract_timestamp_ms(snapshot)
    if snapshot_time_ms <= 0:
        snapshot_time_ms = max(
            _extract_timestamp_ms(open_interest_payload),
            _extract_timestamp_ms(open_interest_hist_payload),
            _extract_timestamp_ms(global_ratio_payload),
            _extract_timestamp_ms(top_account_ratio_payload),
            _extract_timestamp_ms(top_position_ratio_payload),
            _extract_timestamp_ms(taker_ratio_payload),
        )
    current_ms = int(now_ms_value if now_ms_value is not None else now_ms())

    open_interest_current = _extract_ratio(open_interest_payload, "openInterest", fallbacks=("sumOpenInterest",))
    open_interest_reference = _extract_ratio(open_interest_hist_payload, "sumOpenInterest", fallbacks=("openInterest",))
    oi_change_ratio: Decimal | None = None
    if (
        open_interest_current is not None
        and open_interest_reference is not None
        and open_interest_reference != 0
    ):
        oi_change_ratio = (open_interest_current - open_interest_reference) / open_interest_reference

    return CrowdingContext(
        symbol=str(snapshot.get("symbol", "")),
        period=str(snapshot.get("period", "")),
        snapshot_time_ms=snapshot_time_ms,
        snapshot_age_ms=max(0, current_ms - snapshot_time_ms) if snapshot_time_ms > 0 else 0,
        open_interest_current=open_interest_current,
        open_interest_reference=open_interest_reference,
        oi_change_ratio=oi_change_ratio,
        global_long_short_ratio=_extract_ratio(global_ratio_payload, "longShortRatio"),
        top_account_long_short_ratio=_extract_ratio(top_account_ratio_payload, "longShortRatio"),
        top_position_long_short_ratio=_extract_ratio(top_position_ratio_payload, "longShortRatio"),
        taker_buy_sell_ratio=_extract_ratio(
            taker_ratio_payload,
            "buySellRatio",
            fallbacks=("takerBuySellRatio",),
        ),
    )


def evaluate_crowding_gate(
    *,
    side: Side,
    snapshot: dict[str, Any] | None,
    config: CrowdingGateConfig,
    now_ms_value: int | None = None,
) -> tuple[CrowdingScore | None, str]:
    if not config.enabled:
        return None, ""
    context = build_crowding_context(snapshot or {}, now_ms_value=now_ms_value)
    if context is None:
        return None, "missing_crowding_snapshot"
    if config.max_snapshot_age_ms is not None and context.snapshot_age_ms > config.max_snapshot_age_ms:
        return None, "stale_crowding_snapshot"

    score = score_crowding(context, side=side, config=config)
    if config.min_side_score is not None and score.side_score < config.min_side_score:
        return score, "crowding_score_below_threshold"
    return score, ""


def score_crowding(context: CrowdingContext, *, side: Side, config: CrowdingGateConfig) -> CrowdingScore:
    taker_component = _directional_taker_component(side, context.taker_buy_sell_ratio)
    oi_component = _oi_expansion_component(context.oi_change_ratio)
    crowding_penalty = _crowding_penalty(
        side,
        context.global_long_short_ratio,
        context.top_account_long_short_ratio,
        context.top_position_long_short_ratio,
    )

    side_score = taker_component + config.oi_expansion_weight * oi_component - crowding_penalty
    max_component = max(_ZERO, config.max_component_value)
    if side_score > max_component:
        side_score = max_component
    if side_score < -max_component:
        side_score = -max_component

    return CrowdingScore(
        symbol=context.symbol,
        period=context.period,
        side=side,
        snapshot_time_ms=context.snapshot_time_ms,
        snapshot_age_ms=context.snapshot_age_ms,
        directional_taker_component=taker_component,
        oi_expansion_component=oi_component,
        crowding_penalty=crowding_penalty,
        side_score=side_score,
        global_long_short_ratio=context.global_long_short_ratio,
        top_account_long_short_ratio=context.top_account_long_short_ratio,
        top_position_long_short_ratio=context.top_position_long_short_ratio,
        taker_buy_sell_ratio=context.taker_buy_sell_ratio,
        oi_change_ratio=context.oi_change_ratio,
    )


def _directional_taker_component(side: Side, ratio: Decimal | None) -> Decimal:
    if ratio is None:
        return _ZERO
    if side == Side.BUY:
        return max(_ZERO, ratio - _RATIO_ONE)
    return max(_ZERO, _RATIO_ONE - ratio)


def _oi_expansion_component(oi_change_ratio: Decimal | None) -> Decimal:
    if oi_change_ratio is None:
        return _ZERO
    return max(_ZERO, oi_change_ratio)


def _crowding_penalty(side: Side, *ratios: Decimal | None) -> Decimal:
    penalties: list[Decimal] = []
    for ratio in ratios:
        if ratio is None:
            continue
        if side == Side.BUY:
            penalties.append(max(_ZERO, ratio - _RATIO_ONE))
        else:
            penalties.append(max(_ZERO, _RATIO_ONE - ratio))
    if not penalties:
        return _ZERO
    return sum(penalties, start=_ZERO) / Decimal(len(penalties))

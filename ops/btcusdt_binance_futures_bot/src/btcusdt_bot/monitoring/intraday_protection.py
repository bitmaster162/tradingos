from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
import json

_ZERO = Decimal("0")
_ONE = Decimal("1")


def _to_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


@dataclass(slots=True)
class QuantitativeIndicatorSnapshot:
    indicator: str
    value: Decimal | None = None
    trigger_value: Decimal | None = None
    utilization: Decimal | None = None
    is_locked: bool = False
    planned_recover_time_ms: int | None = None


@dataclass(slots=True)
class QuantitativeRulesSnapshot:
    symbol: str
    indicators: list[QuantitativeIndicatorSnapshot] = field(default_factory=list)
    is_locked: bool = False
    planned_recover_time_ms: int | None = None
    max_utilization: Decimal | None = None


@dataclass(slots=True)
class ADLQuantileSnapshot:
    symbol: str
    position_side: str = "BOTH"
    quantile: int | None = None
    all_quantiles: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class IntradayProtectionThresholds:
    max_quant_utilization_reduce: Decimal = Decimal("0.90")
    max_quant_utilization_observe: Decimal = Decimal("0.97")
    max_adl_quantile_reduce: int = 3
    max_adl_quantile_observe: int = 4


@dataclass(slots=True)
class IntradayProtectionDecision:
    action: str
    size_multiplier: Decimal = _ONE
    score: Decimal = _ZERO
    moderate_breaches: int = 0
    severe_breaches: int = 0
    reasons: list[str] = field(default_factory=list)
    compared_at_ms: int = 0
    max_quant_utilization: Decimal | None = None
    adl_quantile: int | None = None
    planned_recover_time_ms: int | None = None

    @property
    def observe_only(self) -> bool:
        return self.action == "observe_only"

    @property
    def reduce_size(self) -> bool:
        return self.action == "reduce_size"

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "IntradayProtectionDecision":
        return cls(
            action=str(payload.get("action", "trade") or "trade"),
            size_multiplier=_to_decimal(payload.get("size_multiplier")) or _ONE,
            score=_to_decimal(payload.get("score")) or _ZERO,
            moderate_breaches=int(payload.get("moderate_breaches", 0) or 0),
            severe_breaches=int(payload.get("severe_breaches", 0) or 0),
            reasons=[str(item) for item in payload.get("reasons", [])],
            compared_at_ms=int(payload.get("compared_at_ms", 0) or 0),
            max_quant_utilization=_to_decimal(payload.get("max_quant_utilization")),
            adl_quantile=(
                int(payload.get("adl_quantile"))
                if payload.get("adl_quantile") not in {None, "", "None"}
                else None
            ),
            planned_recover_time_ms=(
                int(payload.get("planned_recover_time_ms"))
                if payload.get("planned_recover_time_ms") not in {None, "", "None"}
                else None
            ),
        )


@dataclass(slots=True)
class IntradayProtectionStatus:
    iterations: int = 0
    decisions_written: int = 0
    observe_only_decisions: int = 0
    reduce_size_decisions: int = 0
    last_action: str = ""
    last_path: str = ""
    last_error: str = ""
    latest_quant_utilization: Decimal | None = None
    latest_adl_quantile: int | None = None


def _coerce_indicator_rows(value: object) -> list[dict[str, object]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        return [dict(value)]
    return []


def normalize_api_trading_status(payload: dict[str, object], symbol: str) -> QuantitativeRulesSnapshot:
    raw_rows: list[dict[str, object]] = []
    if symbol in payload:
        raw_rows = _coerce_indicator_rows(payload[symbol])
    if not raw_rows:
        indicators = payload.get("indicators")
        if isinstance(indicators, dict) and symbol in indicators:
            raw_rows = _coerce_indicator_rows(indicators[symbol])
    if not raw_rows:
        data = payload.get("data")
        if isinstance(data, dict) and symbol in data:
            raw_rows = _coerce_indicator_rows(data[symbol])
        elif isinstance(data, list):
            for row in data:
                if isinstance(row, dict) and str(row.get("symbol", "")) == symbol:
                    raw_rows = _coerce_indicator_rows(row.get("indicators") or row.get("data") or row)
                    if raw_rows:
                        break

    indicators: list[QuantitativeIndicatorSnapshot] = []
    max_utilization: Decimal | None = None
    is_locked = False
    planned_recover_time_ms: int | None = None

    for row in raw_rows:
        value = _to_decimal(row.get("value"))
        trigger_value = _to_decimal(row.get("triggerValue"))
        utilization: Decimal | None = None
        if value is not None and trigger_value is not None and trigger_value > 0:
            utilization = value / trigger_value
            if max_utilization is None or utilization > max_utilization:
                max_utilization = utilization
        row_locked = bool(row.get("isLocked", False))
        if row_locked:
            is_locked = True
        planned = row.get("plannedRecoverTime")
        if planned not in {None, "", "None"}:
            planned_int = int(planned)
            if planned_recover_time_ms is None or planned_int > planned_recover_time_ms:
                planned_recover_time_ms = planned_int
        indicators.append(
            QuantitativeIndicatorSnapshot(
                indicator=str(row.get("indicator", "") or ""),
                value=value,
                trigger_value=trigger_value,
                utilization=utilization,
                is_locked=row_locked,
                planned_recover_time_ms=(int(planned) if planned not in {None, "", "None"} else None),
            )
        )

    return QuantitativeRulesSnapshot(
        symbol=symbol,
        indicators=indicators,
        is_locked=is_locked,
        planned_recover_time_ms=planned_recover_time_ms,
        max_utilization=max_utilization,
    )


def normalize_adl_quantile(
    payload: list[dict[str, object]] | dict[str, object],
    symbol: str,
    *,
    position_mode: str = "ONE_WAY",
) -> ADLQuantileSnapshot:
    rows: list[dict[str, object]] = []
    if isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
    elif isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            rows = [row for row in data if isinstance(row, dict)]
        elif isinstance(data, dict):
            rows = [data]
        else:
            rows = [payload]

    quantiles: dict[str, int] = {}
    for row in rows:
        if str(row.get("symbol", "")) != symbol:
            continue
        raw = row.get("adlQuantile")
        if isinstance(raw, dict):
            for side, value in raw.items():
                if value in {None, "", "None"}:
                    continue
                quantiles[str(side)] = int(value)
            break

    selected_side = "BOTH"
    quantile: int | None = None
    normalized_mode = str(position_mode or "ONE_WAY").upper()
    if normalized_mode == "HEDGE":
        for candidate in ("LONG", "SHORT", "BOTH", "HEDGE"):
            if candidate in quantiles:
                selected_side = candidate
                quantile = quantiles[candidate]
                break
    else:
        if "BOTH" in quantiles:
            quantile = quantiles["BOTH"]
            selected_side = "BOTH"
        elif quantiles:
            selected_side, quantile = max(quantiles.items(), key=lambda item: item[1])

    return ADLQuantileSnapshot(symbol=symbol, position_side=selected_side, quantile=quantile, all_quantiles=quantiles)


def evaluate_intraday_protection(
    *,
    quant_rules: QuantitativeRulesSnapshot,
    adl_quantile: ADLQuantileSnapshot | None = None,
    thresholds: IntradayProtectionThresholds | None = None,
    compared_at_ms: int = 0,
    now_ms_value: int | None = None,
) -> IntradayProtectionDecision:
    thresholds = thresholds or IntradayProtectionThresholds()
    now_ms_value = compared_at_ms if now_ms_value is None else now_ms_value
    reasons: list[str] = []
    moderate = 0
    severe = 0

    if quant_rules.is_locked:
        severe += 1
        reasons.append("quant_rules_locked")
    elif (
        quant_rules.planned_recover_time_ms is not None
        and quant_rules.planned_recover_time_ms > max(0, now_ms_value)
    ):
        severe += 1
        reasons.append("quant_rules_cooling_off")

    if quant_rules.max_utilization is not None:
        if quant_rules.max_utilization >= thresholds.max_quant_utilization_observe:
            severe += 1
            reasons.append("quant_utilization_above_observe_threshold")
        elif quant_rules.max_utilization >= thresholds.max_quant_utilization_reduce:
            moderate += 1
            reasons.append("quant_utilization_above_reduce_threshold")

    if adl_quantile is not None and adl_quantile.quantile is not None:
        if adl_quantile.quantile >= thresholds.max_adl_quantile_observe:
            severe += 1
            reasons.append("adl_quantile_above_observe_threshold")
        elif adl_quantile.quantile >= thresholds.max_adl_quantile_reduce:
            moderate += 1
            reasons.append("adl_quantile_above_reduce_threshold")

    if severe > 0:
        action = "observe_only"
        size_multiplier = _ZERO
    elif moderate >= 2:
        action = "reduce_size"
        size_multiplier = Decimal("0.35")
    elif moderate == 1:
        action = "reduce_size"
        size_multiplier = Decimal("0.60")
    else:
        action = "trade"
        size_multiplier = _ONE

    score = Decimal(moderate) + Decimal(severe) * Decimal("2")
    return IntradayProtectionDecision(
        action=action,
        size_multiplier=size_multiplier,
        score=score,
        moderate_breaches=moderate,
        severe_breaches=severe,
        reasons=reasons,
        compared_at_ms=compared_at_ms,
        max_quant_utilization=quant_rules.max_utilization,
        adl_quantile=(adl_quantile.quantile if adl_quantile is not None else None),
        planned_recover_time_ms=quant_rules.planned_recover_time_ms,
    )


def load_json(path: str | Path) -> dict[str, object]:
    return json.loads(Path(path).read_text(encoding="utf-8"))

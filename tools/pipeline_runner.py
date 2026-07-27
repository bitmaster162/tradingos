from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


TF_WEIGHTS = {
    "1m": 0.5,
    "5m": 0.75,
    "15m": 1.0,
    "30m": 1.2,
    "1h": 1.5,
    "4h": 2.0,
    "1d": 2.5,
}


@dataclass(slots=True)
class Series:
    rows: list[dict[str, Any]]
    missing: bool = False
    error: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_csv(path: str | None) -> Series:
    if not path:
        return Series([], missing=True, error="path_not_configured")
    file_path = Path(path)
    if not file_path.exists():
        return Series([], missing=True, error=f"file_not_found:{path}")
    try:
        with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except UnicodeDecodeError:
        with file_path.open("r", encoding="utf-16", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except Exception as exc:  # noqa: BLE001
        return Series([], missing=True, error=f"read_error:{exc}")
    return Series(rows)


def _num(row: dict[str, Any], *names: str, default: float = math.nan) -> float:
    for name in names:
        raw = row.get(name)
        if raw is None or raw == "":
            continue
        try:
            return float(raw)
        except ValueError:
            continue
    return default


def _col(rows: list[dict[str, Any]], *names: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = _num(row, *names)
        if not math.isnan(value):
            values.append(value)
    return values


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return mean(values[-period:])


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    ema = mean(values[:period])
    for value in values[period:]:
        ema = alpha * value + (1 - alpha) * ema
    return ema


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for prev, cur in zip(values[-period - 1 : -1], values[-period:], strict=True):
        diff = cur - prev
        gains.append(max(diff, 0.0))
        losses.append(abs(min(diff, 0.0)))
    avg_gain = mean(gains)
    avg_loss = mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _atr(rows: list[dict[str, Any]], period: int = 14) -> float | None:
    if len(rows) <= period:
        return None
    trs: list[float] = []
    prev_close = _num(rows[-period - 1], "close")
    for row in rows[-period:]:
        high = _num(row, "high")
        low = _num(row, "low")
        close = _num(row, "close")
        if any(math.isnan(v) for v in (high, low, close, prev_close)):
            continue
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        prev_close = close
    if not trs:
        return None
    return mean(trs)


def _ao(rows: list[dict[str, Any]]) -> float | None:
    mids = []
    for row in rows:
        high = _num(row, "high")
        low = _num(row, "low")
        if not math.isnan(high) and not math.isnan(low):
            mids.append((high + low) / 2)
    fast = _sma(mids, 5)
    slow = _sma(mids, 34)
    if fast is None or slow is None:
        return None
    return fast - slow


def _zscore(values: list[float], window: int = 20) -> float | None:
    clean = [v for v in values if not math.isnan(v)]
    if len(clean) < max(5, window // 2):
        return None
    sample = clean[-window:]
    avg = mean(sample)
    var = mean([(v - avg) ** 2 for v in sample])
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (sample[-1] - avg) / std


def _pct_change(values: list[float], lookback: int = 1) -> float | None:
    if len(values) <= lookback:
        return None
    prev = values[-lookback - 1]
    cur = values[-1]
    if prev == 0:
        return None
    return (cur - prev) / prev * 100


def _trend_strength(closes: list[float], atr: float | None) -> float | None:
    if len(closes) < 20 or atr is None or atr == 0:
        return None
    slope = closes[-1] - closes[-20]
    return slope / atr


def _sweep_flags(rows: list[dict[str, Any]], lookback: int = 20) -> dict[str, bool]:
    if len(rows) <= lookback:
        return {"bullish_sweep": False, "bearish_sweep": False}
    last = rows[-1]
    prev = rows[-lookback - 1 : -1]
    prev_high = max(_num(row, "high") for row in prev)
    prev_low = min(_num(row, "low") for row in prev)
    last_high = _num(last, "high")
    last_low = _num(last, "low")
    last_close = _num(last, "close")
    return {
        "bearish_sweep": last_high > prev_high and last_close < prev_high,
        "bullish_sweep": last_low < prev_low and last_close > prev_low,
    }


def _basis_values(rows: list[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for row in rows:
        price = _num(row, "price", "mark_price", "close")
        index = _num(row, "index_price", "spot")
        if not math.isnan(price) and not math.isnan(index):
            values.append(price - index)
    return values


def _safe_round(value: float | None, ndigits: int = 6) -> float | None:
    if value is None or math.isnan(value):
        return None
    return round(value, ndigits)


def _classify_regime(close: float, ema50: float | None, ema200: float | None, strength: float | None) -> str:
    if ema50 is None:
        return "unknown"
    if strength is not None and abs(strength) < 0.8:
        return "range_or_noise"
    if ema200 is not None:
        if close > ema50 > ema200:
            return "trend_up"
        if close < ema50 < ema200:
            return "trend_down"
    if close > ema50:
        return "up_bias"
    if close < ema50:
        return "down_bias"
    return "neutral"


def _strategy_v02_signal(
    *,
    rows: list[dict[str, Any]],
    close: float,
    ema20: float | None,
    ema50: float | None,
    ema200: float | None,
    rsi14: float | None,
    ao_value: float | None,
    atr14: float | None,
    strength: float | None,
    rel_volume: float | None,
    sweeps: dict[str, bool],
    oi_delta_pct: float | None,
    oi_z: float | None,
    funding: float | None,
    regime: str,
    score_delta: float,
) -> dict[str, Any]:
    abstain: list[str] = []
    reasons: list[str] = []
    warnings: list[str] = []

    if len(rows) < 220:
        abstain.append("need_220_ohlcv_bars")
    required = {
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "rsi14": rsi14,
        "ao_5_34": ao_value,
        "atr14": atr14,
        "trend_strength_proxy": strength,
    }
    for name, value in required.items():
        if value is None or math.isnan(value):
            abstain.append(f"missing_{name}")
    if rel_volume is not None and rel_volume < 0.55:
        abstain.append("relative_volume_too_low")
    if regime == "range_or_noise" and not (sweeps.get("bullish_sweep") or sweeps.get("bearish_sweep")):
        abstain.append("range_or_noise_without_sweep")

    derivatives_ready = oi_delta_pct is not None and funding is not None
    if not derivatives_ready:
        warnings.append("derivatives_not_ready")

    funding = funding if funding is not None else 0.0
    oi_delta_pct = oi_delta_pct if oi_delta_pct is not None else 0.0
    oi_z = oi_z if oi_z is not None else 0.0

    side = "NEUTRAL"
    setup = "ABSTAIN"
    confidence = 0.0
    risk_multiplier = 0.0

    trend_long = (
        close > (ema20 or math.inf) > (ema50 or math.inf) > (ema200 or math.inf)
        and (strength or 0.0) >= 1.2
        and (ao_value or 0.0) > 0
        and 42 <= (rsi14 or 0.0) <= 68
        and score_delta >= 5.0
        and not sweeps.get("bearish_sweep", False)
        and funding < 0.0008
    )
    trend_short = (
        close < (ema20 or -math.inf) < (ema50 or -math.inf) < (ema200 or -math.inf)
        and (strength or 0.0) <= -1.2
        and (ao_value or 0.0) < 0
        and 32 <= (rsi14 or 100.0) <= 58
        and score_delta <= -5.0
        and not sweeps.get("bullish_sweep", False)
        and funding > -0.0008
    )

    if derivatives_ready:
        trend_long = trend_long and oi_delta_pct > -0.05
        trend_short = trend_short and oi_delta_pct > -0.05

    sweep_long = (
        sweeps.get("bullish_sweep", False)
        and (rsi14 or 50.0) <= 48
        and score_delta >= 2.0
        and funding <= 0.0008
        and (oi_delta_pct <= 0 or abs(oi_z) >= 1.5)
    )
    sweep_short = (
        sweeps.get("bearish_sweep", False)
        and (rsi14 or 50.0) >= 52
        and score_delta <= -2.0
        and funding >= -0.0008
        and (oi_delta_pct <= 0 or abs(oi_z) >= 1.5)
    )

    if not abstain:
        if trend_long:
            side = "LONG"
            setup = "TREND_CONTINUATION"
            confidence = 0.68
            risk_multiplier = 1.0
            reasons.extend(["ema_stack_up", "trend_strength_ok", "momentum_ok", "funding_not_overheated"])
        elif trend_short:
            side = "SHORT"
            setup = "TREND_CONTINUATION"
            confidence = 0.68
            risk_multiplier = 1.0
            reasons.extend(["ema_stack_down", "trend_strength_ok", "momentum_ok", "funding_not_extreme_negative"])
        elif sweep_long:
            side = "LONG"
            setup = "LIQUIDITY_SWEEP_REVERSAL"
            confidence = 0.62
            risk_multiplier = 0.5
            reasons.extend(["bullish_sweep", "reversal_context", "crowding_or_oi_reset"])
        elif sweep_short:
            side = "SHORT"
            setup = "LIQUIDITY_SWEEP_REVERSAL"
            confidence = 0.62
            risk_multiplier = 0.5
            reasons.extend(["bearish_sweep", "reversal_context", "crowding_or_oi_reset"])
        else:
            abstain.append("no_v02_setup_confirmation")

    if side == "LONG" and funding > 0.0008:
        side = "NEUTRAL"
        setup = "ABSTAIN"
        risk_multiplier = 0.0
        abstain.append("long_blocked_by_positive_funding_crowding")
    if side == "SHORT" and funding < -0.0008:
        side = "NEUTRAL"
        setup = "ABSTAIN"
        risk_multiplier = 0.0
        abstain.append("short_blocked_by_negative_funding_crowding")

    return {
        "version": "0.2.0",
        "side": side,
        "setup": setup,
        "confidence": round(confidence, 3),
        "risk_multiplier": round(risk_multiplier, 3),
        "requires_confirmation": ["HTF_bias", "OI_or_funding", "no_mid_range_noise"],
        "reasons": reasons,
        "abstain_reasons": abstain,
        "warnings": warnings,
    }


def _short_continuation_pressure_alert(
    *,
    tf: str,
    regime: str,
    strength: float | None,
    oi_delta_pct: float | None,
    funding: float | None,
    sweeps: dict[str, bool],
    rows_count: int,
) -> dict[str, Any]:
    reasons: list[str] = []
    blockers: list[str] = []
    warnings: list[str] = []

    if rows_count < 220:
        blockers.append("need_220_ohlcv_bars")
    if regime not in {"trend_down", "down_bias"}:
        blockers.append("tf_not_down_biased")
    else:
        reasons.append(f"regime_{regime}")
    if strength is None or math.isnan(strength):
        blockers.append("missing_trend_strength")
    elif strength <= -1.0:
        reasons.append("local_20bar_downtrend")
    else:
        blockers.append("trend_strength_not_negative_enough")
    if oi_delta_pct is None or math.isnan(oi_delta_pct):
        blockers.append("missing_oi_delta")
    elif oi_delta_pct >= 0:
        reasons.append("oi_rising_with_downtrend")
    else:
        blockers.append("oi_not_rising")
    if sweeps.get("bullish_sweep") or sweeps.get("bearish_sweep"):
        blockers.append("recent_liquidity_sweep_present")
    else:
        reasons.append("no_20bar_sweep")

    if funding is None or math.isnan(funding):
        warnings.append("funding_missing")
    elif funding >= 0:
        reasons.append("funding_non_negative")

    active = not blockers
    return {
        "id": "short_continuation_pressure",
        "version": "1.8.0",
        "mode": "alert_only",
        "active": active,
        "side_context": "SHORT" if active else "NEUTRAL",
        "tf": tf,
        "can_trade": False,
        "risk_multiplier": 0.0,
        "entry_permission": "blocked_alert_only",
        "source_research": "MAX Core Lite v1.7 best lead did not pass trade gate; use only as market-state context.",
        "conditions": [
            "regime in trend_down/down_bias",
            "trend_strength_20_atr <= -1.0",
            "oi_delta_pct >= 0",
            "no bullish/bearish 20-bar sweep",
        ],
        "reasons": reasons,
        "blockers": blockers,
        "warnings": warnings,
        "notes": [
            "Do not open a trade from this alert alone.",
            "Use to reduce aggressive long bias or require stronger long confirmation.",
            "Manual short context still needs a separate entry trigger.",
        ],
    }


def run_pipeline(
    pair: str,
    files: dict[str, str],
    *,
    tf: str = "15m",
    pairB: str | None = None,
    filesB: dict[str, str] | None = None,
) -> dict[str, Any]:
    ohlcv = _read_csv(files.get("ohlcv"))
    oi = _read_csv(files.get("oi"))
    basis = _read_csv(files.get("basis"))
    ohlcv_b = _read_csv((filesB or {}).get("ohlcv")) if pairB else Series([], missing=True, error="pairB_not_configured")

    rows = ohlcv.rows
    closes = _col(rows, "close")
    highs = _col(rows, "high")
    lows = _col(rows, "low")
    volumes = _col(rows, "volume")
    last_close = closes[-1] if closes else math.nan
    prev_close = closes[-2] if len(closes) > 1 else math.nan
    price_delta_pct = _pct_change(closes) or 0.0

    atr14 = _atr(rows)
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    ema200 = _ema(closes, 200)
    rsi14 = _rsi(closes)
    ao_value = _ao(rows)
    rel_volume = None
    if volumes:
        avg_vol = _sma(volumes, min(20, len(volumes)))
        if avg_vol and avg_vol != 0:
            rel_volume = volumes[-1] / avg_vol
    strength = _trend_strength(closes, atr14)
    sweeps = _sweep_flags(rows)
    regime = _classify_regime(last_close, ema50, ema200, strength) if closes else "missing_ohlcv"

    oi_values = _col(oi.rows, "open_interest")
    oi_delta_pct = _pct_change(oi_values)
    oi_z = _zscore(oi_values)
    funding_values = _col(oi.rows, "funding")
    funding = funding_values[-1] if funding_values else None
    funding_z = _zscore(funding_values)

    basis_series = _basis_values(basis.rows)
    basis_z = _zscore(basis_series)
    basis_funding_values = _col(basis.rows, "funding")
    basis_funding_z = _zscore(basis_funding_values)

    smt: dict[str, Any] = {"enabled": False}
    if pairB and ohlcv_b.rows:
        b_closes = _col(ohlcv_b.rows, "close")
        primary_ret = _pct_change(closes, min(10, max(1, len(closes) - 1))) if closes else None
        secondary_ret = _pct_change(b_closes, min(10, max(1, len(b_closes) - 1))) if b_closes else None
        divergence = None
        if primary_ret is not None and secondary_ret is not None:
            divergence = primary_ret - secondary_ret
        smt = {
            "enabled": True,
            "pairB": pairB,
            "primary_ret_pct": _safe_round(primary_ret),
            "secondary_ret_pct": _safe_round(secondary_ret),
            "divergence_pct": _safe_round(divergence),
        }

    long_score = 0.0
    short_score = 0.0
    reasons: list[str] = []
    warnings: list[str] = []

    if ohlcv.missing or len(rows) < 30:
        warnings.append(f"ohlcv_degraded:{ohlcv.error or 'too_few_rows'}")
    if oi.missing:
        warnings.append(f"oi_missing:{oi.error}")
    if basis.missing:
        warnings.append(f"basis_missing:{basis.error}")
    if pairB and ohlcv_b.missing:
        warnings.append(f"pairB_ohlcv_missing:{ohlcv_b.error}")

    if regime in {"trend_up", "up_bias"}:
        long_score += 3.0
        reasons.append("price_above_trend_filter")
    if regime in {"trend_down", "down_bias"}:
        short_score += 3.0
        reasons.append("price_below_trend_filter")
    if strength is not None:
        if strength > 1.5:
            long_score += 2.0
            reasons.append("positive_trend_strength")
        elif strength < -1.5:
            short_score += 2.0
            reasons.append("negative_trend_strength")
    if rsi14 is not None:
        if rsi14 < 30:
            long_score += 2.0
            reasons.append("rsi_oversold")
        elif rsi14 > 70:
            short_score += 2.0
            reasons.append("rsi_overbought")
    if ao_value is not None:
        if ao_value > 0:
            long_score += 1.0
        elif ao_value < 0:
            short_score += 1.0
    if sweeps["bullish_sweep"]:
        long_score += 4.0
        reasons.append("bullish_liquidity_sweep")
    if sweeps["bearish_sweep"]:
        short_score += 4.0
        reasons.append("bearish_liquidity_sweep")
    if rel_volume is not None and rel_volume > 1.5:
        if price_delta_pct > 0:
            long_score += 1.0
            reasons.append("relative_volume_confirms_up_move")
        elif price_delta_pct < 0:
            short_score += 1.0
            reasons.append("relative_volume_confirms_down_move")
    if oi_delta_pct is not None:
        if price_delta_pct > 0 and oi_delta_pct > 0:
            long_score += 2.0
            reasons.append("price_up_oi_up")
        elif price_delta_pct > 0 and oi_delta_pct < 0:
            short_score += 1.0
            warnings.append("price_up_oi_down_short_squeeze_risk")
        elif price_delta_pct < 0 and oi_delta_pct > 0:
            short_score += 2.0
            reasons.append("price_down_oi_up")
        elif price_delta_pct < 0 and oi_delta_pct < 0:
            long_score += 1.0
            warnings.append("price_down_oi_down_capitulation_possible")
    if oi_z is not None and abs(oi_z) >= 2.0:
        warnings.append("oi_zscore_extreme")
    if funding is not None:
        if funding > 0.0008:
            short_score += 2.0
            warnings.append("positive_funding_crowding")
        elif funding < -0.0008:
            long_score += 2.0
            warnings.append("negative_funding_crowding")
    if basis_z is not None:
        if basis_z > 2.0:
            short_score += 1.0
            warnings.append("basis_positive_extreme")
        elif basis_z < -2.0:
            long_score += 1.0
            warnings.append("basis_negative_extreme")
    if smt.get("enabled") and smt.get("divergence_pct") is not None:
        div = float(smt["divergence_pct"])
        if div > 1.0 and price_delta_pct > 0:
            long_score += 1.0
            reasons.append("primary_outperforming_pairB")
        elif div < -1.0 and price_delta_pct < 0:
            short_score += 1.0
            reasons.append("primary_underperforming_pairB")

    score_delta = long_score - short_score
    if abs(score_delta) >= 8:
        decision = "LONG" if score_delta > 0 else "SHORT"
    else:
        decision = "NEUTRAL"

    data_degraded = bool(warnings) or ohlcv.missing or len(rows) < 30
    strategy_v02 = _strategy_v02_signal(
        rows=rows,
        close=last_close,
        ema20=ema20,
        ema50=ema50,
        ema200=ema200,
        rsi14=rsi14,
        ao_value=ao_value,
        atr14=atr14,
        strength=strength,
        rel_volume=rel_volume,
        sweeps=sweeps,
        oi_delta_pct=oi_delta_pct,
        oi_z=oi_z,
        funding=funding,
        regime=regime,
        score_delta=score_delta,
    )
    short_continuation_pressure = _short_continuation_pressure_alert(
        tf=tf,
        regime=regime,
        strength=strength,
        oi_delta_pct=oi_delta_pct,
        funding=funding,
        sweeps=sweeps,
        rows_count=len(rows),
    )
    market_state_alerts = {
        "short_continuation_pressure": short_continuation_pressure,
    }
    if short_continuation_pressure["active"]:
        warnings.append("short_continuation_pressure_alert_only")

    return {
        "engine": "MAX_CORE_LITE",
        "engine_version": "0.3.0",
        "generated_at": _now(),
        "pair": pair,
        "tf": tf,
        "pairB": pairB,
        "rows": {"ohlcv": len(rows), "oi": len(oi.rows), "basis": len(basis.rows), "pairB_ohlcv": len(ohlcv_b.rows)},
        "data_degraded": data_degraded,
        "last": {
            "close": _safe_round(last_close),
            "prev_close": _safe_round(prev_close),
            "price_delta_pct": _safe_round(price_delta_pct),
            "high": _safe_round(highs[-1] if highs else None),
            "low": _safe_round(lows[-1] if lows else None),
            "volume": _safe_round(volumes[-1] if volumes else None),
        },
        "indicators": {
            "ema20": _safe_round(ema20),
            "ema50": _safe_round(ema50),
            "ema200": _safe_round(ema200),
            "rsi14": _safe_round(rsi14),
            "ao_5_34": _safe_round(ao_value),
            "atr14": _safe_round(atr14),
            "trend_strength_proxy": _safe_round(strength),
            "relative_volume": _safe_round(rel_volume),
        },
        "derivatives": {
            "oi_delta_pct": _safe_round(oi_delta_pct),
            "oi_zscore": _safe_round(oi_z),
            "funding": _safe_round(funding, 8),
            "funding_zscore": _safe_round(funding_z),
            "basis_zscore": _safe_round(basis_z),
            "basis_funding_zscore": _safe_round(basis_funding_z),
        },
        "liquidity": sweeps,
        "smt": smt,
        "regime": regime,
        "scores": {
            "long_score": round(long_score, 3),
            "short_score": round(short_score, 3),
            "delta": round(score_delta, 3),
            "threshold": 8,
        },
        "decision": decision,
        "strategy_v02": strategy_v02,
        "market_state_alerts": market_state_alerts,
        "reasons": reasons,
        "warnings": warnings,
    }


def build_report(results: dict[str, dict[str, Any]], json_out: str, md_out: str) -> dict[str, Any]:
    weighted_long = 0.0
    weighted_short = 0.0
    total_weight = 0.0
    degraded = False
    active_market_alerts: list[dict[str, Any]] = []
    for tf, result in results.items():
        weight = TF_WEIGHTS.get(tf, 1.0)
        scores = result.get("scores", {})
        weighted_long += float(scores.get("long_score", 0.0)) * weight
        weighted_short += float(scores.get("short_score", 0.0)) * weight
        total_weight += weight
        degraded = degraded or bool(result.get("data_degraded"))
        alerts = result.get("market_state_alerts", {})
        if isinstance(alerts, dict):
            for alert in alerts.values():
                if isinstance(alert, dict) and alert.get("active"):
                    active_market_alerts.append({**alert, "tf": tf})

    delta = weighted_long - weighted_short
    decision = "NEUTRAL"
    if abs(delta) >= 8:
        decision = "LONG" if delta > 0 else "SHORT"

    report = {
        "engine": "MAX_CORE_LITE",
        "engine_version": "0.3.0",
        "generated_at": _now(),
        "decision": decision,
        "market_state_alerts": {
            "active_count": len(active_market_alerts),
            "active": active_market_alerts,
            "entry_permission": "alerts_do_not_grant_trade_entries",
        },
        "weighted": {
            "long_score": round(weighted_long, 3),
            "short_score": round(weighted_short, 3),
            "delta": round(delta, 3),
            "total_weight": round(total_weight, 3),
            "threshold": 8,
        },
        "data_degraded": degraded,
        "timeframes": results,
    }

    json_path = Path(json_out)
    md_path = Path(md_out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    return report


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MAX Core Lite Composite Report",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Engine: `{report['engine']} {report['engine_version']}`",
        f"- Decision: **{report['decision']}**",
        f"- Weighted long: `{report['weighted']['long_score']}`",
        f"- Weighted short: `{report['weighted']['short_score']}`",
        f"- Delta: `{report['weighted']['delta']}`",
        f"- Data degraded: `{report['data_degraded']}`",
        f"- Active market-state alerts: `{report['market_state_alerts']['active_count']}`",
        "",
        "## Timeframes",
        "",
    ]
    for tf, result in report["timeframes"].items():
        scores = result.get("scores", {})
        lines.extend(
            [
                f"### {tf}",
                "",
                f"- Pair: `{result.get('pair')}`",
                f"- Decision: **{result.get('decision')}**",
                f"- Strategy v0.2: **{result.get('strategy_v02', {}).get('side')}** / `{result.get('strategy_v02', {}).get('setup')}`",
                f"- Regime: `{result.get('regime')}`",
                f"- Close: `{result.get('last', {}).get('close')}`",
                f"- Long / Short / Delta: `{scores.get('long_score')}` / `{scores.get('short_score')}` / `{scores.get('delta')}`",
                f"- Data degraded: `{result.get('data_degraded')}`",
                f"- Short-continuation pressure: `{result.get('market_state_alerts', {}).get('short_continuation_pressure', {}).get('active')}` "
                f"(alert-only, can_trade=`{result.get('market_state_alerts', {}).get('short_continuation_pressure', {}).get('can_trade')}`)",
                f"- Reasons: `{', '.join(result.get('reasons', [])) or 'none'}`",
                f"- Warnings: `{', '.join(result.get('warnings', [])) or 'none'}`",
                "",
            ]
        )
    lines.extend(["## Market-State Alerts", ""])
    active_alerts = report["market_state_alerts"]["active"]
    if active_alerts:
        for alert in active_alerts:
            lines.extend(
                [
                    f"- `{alert.get('id')}` on `{alert.get('tf')}`: side context `{alert.get('side_context')}`, "
                    f"mode `{alert.get('mode')}`, can_trade `{alert.get('can_trade')}`.",
                ]
            )
    else:
        lines.append("- No active market-state alerts.")
    lines.append("")
    lines.extend(
        [
            "## Runtime Boundary",
            "",
            "This is a repo-local deterministic replacement seed for the missing historical MAX `tools.pipeline_runner`.",
            "It is useful for smoke tests and research iteration, but it is not a proven profitable live strategy.",
            "",
        ]
    )
    return "\n".join(lines)

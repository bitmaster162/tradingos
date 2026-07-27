from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import pipeline_runner as PR  # noqa: E402


BINANCE_ENDPOINTS = {
    "futures": "https://fapi.binance.com/fapi/v1/klines",
    "spot": "https://api.binance.com/api/v3/klines",
}

INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


@dataclass(slots=True)
class Trade:
    index: int
    entry_time: str
    exit_time: str
    side: str
    entry: float
    stop: float
    take_profit: float
    exit: float
    exit_reason: str
    bars_held: int
    gross_r: float
    net_r: float
    strategy: str
    setup: str
    confidence: float
    risk_multiplier: float
    htf_bias: str
    htf_regime: str
    spot_perp_divergence_12: float | None
    spot_volume_ratio: float | None
    long_score: float
    short_score: float
    delta: float
    regime: str
    reasons: list[str]
    warnings: list[str]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_float(value: Any, default: float = math.nan) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def ms_to_iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def read_ohlcv_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    normalized: list[dict[str, str]] = []
    for idx, row in enumerate(rows):
        close = parse_float(row.get("close"))
        high = parse_float(row.get("high"))
        low = parse_float(row.get("low"))
        open_ = parse_float(row.get("open"))
        volume = parse_float(row.get("volume"), 0.0)
        if any(math.isnan(v) for v in (open_, high, low, close)):
            continue
        normalized.append(
            {
                "time": row.get("time") or row.get("timestamp") or str(idx),
                "time_ms": row.get("time_ms") or row.get("open_time") or "",
                "open": str(open_),
                "high": str(high),
                "low": str(low),
                "close": str(close),
                "volume": str(volume),
            }
        )
    return normalized


def write_ohlcv_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["time", "open", "high", "low", "close", "volume"]
    if any(row.get("time_ms") for row in rows):
        fieldnames = ["time", "time_ms", "open", "high", "low", "close", "volume"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def read_dict_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def cache_klines_path(cache_dir: Path, market: str, symbol: str, interval: str) -> Path:
    return cache_dir / market / symbol.upper() / f"{interval}_klines.csv"


def cache_aligned_oi_path(cache_dir: Path, symbol: str, interval: str) -> Path:
    return cache_dir / "futures" / symbol.upper() / f"{interval}_oi_aligned.csv"


def load_cached_klines(cache_dir: Path, market: str, symbol: str, interval: str) -> tuple[list[dict[str, str]], str | None]:
    path = cache_klines_path(cache_dir, market, symbol, interval)
    if not path.exists():
        return [], None
    return read_ohlcv_csv(path), str(path)


def load_cached_oi(cache_dir: Path, symbol: str, interval: str) -> tuple[list[dict[str, str]], str | None]:
    path = cache_aligned_oi_path(cache_dir, symbol, interval)
    if not path.exists():
        return [], None
    return read_dict_csv(path), str(path)


def fetch_binance_klines(symbol: str, interval: str, limit: int, market: str, pages: int = 1) -> list[dict[str, str]]:
    if market not in BINANCE_ENDPOINTS:
        raise ValueError(f"unsupported_market:{market}")
    payload: list[list[Any]] = []
    end_time: int | None = None
    for _ in range(max(1, pages)):
        params: dict[str, Any] = {"symbol": symbol.upper(), "interval": interval, "limit": min(max(limit, 1), 1500)}
        if end_time is not None:
            params["endTime"] = end_time
        query = urlencode(params)
        url = f"{BINANCE_ENDPOINTS[market]}?{query}"
        with urlopen(url, timeout=30) as response:  # noqa: S310 - fixed public Binance endpoints.
            page = json.loads(response.read().decode("utf-8"))
        if not page:
            break
        payload = page + payload
        first_open = int(page[0][0])
        next_end = first_open - 1
        if end_time == next_end:
            break
        end_time = next_end

    seen: set[int] = set()
    rows: list[dict[str, str]] = []
    for item in payload:
        open_time = int(item[0])
        if open_time in seen:
            continue
        seen.add(open_time)
        rows.append(
            {
                "time": ms_to_iso(open_time),
                "time_ms": str(open_time),
                "open": str(parse_float(item[1])),
                "high": str(parse_float(item[2])),
                "low": str(parse_float(item[3])),
                "close": str(parse_float(item[4])),
                "volume": str(parse_float(item[5], 0.0)),
            }
        )
    return sorted(rows, key=lambda row: int(row.get("time_ms") or 0))


def fetch_open_interest_history(symbol: str, period: str, limit: int, pages: int = 1) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    end_time: int | None = None
    for _ in range(max(1, pages)):
        params: dict[str, Any] = {"symbol": symbol.upper(), "period": period, "limit": min(max(limit, 1), 500)}
        if end_time is not None:
            params["endTime"] = end_time
        query = urlencode(params)
        url = f"https://fapi.binance.com/futures/data/openInterestHist?{query}"
        with urlopen(url, timeout=30) as response:  # noqa: S310 - fixed public Binance endpoint.
            page = json.loads(response.read().decode("utf-8"))
        if not page:
            break
        payload = page + payload
        first_ts = int(page[0]["timestamp"])
        next_end = first_ts - 1
        if end_time == next_end:
            break
        end_time = next_end
    records: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in payload:
        ts = int(item["timestamp"])
        if ts in seen:
            continue
        seen.add(ts)
        records.append(
            {
                "timestamp": ts,
                "open_interest": parse_float(item.get("sumOpenInterest")),
            }
        )
    return sorted(records, key=lambda row: row["timestamp"])


def fetch_funding_history(symbol: str, limit: int = 1000, pages: int = 1) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    end_time: int | None = None
    for _ in range(max(1, pages)):
        params: dict[str, Any] = {"symbol": symbol.upper(), "limit": min(max(limit, 1), 1000)}
        if end_time is not None:
            params["endTime"] = end_time
        query = urlencode(params)
        url = f"https://fapi.binance.com/fapi/v1/fundingRate?{query}"
        with urlopen(url, timeout=30) as response:  # noqa: S310 - fixed public Binance endpoint.
            page = json.loads(response.read().decode("utf-8"))
        if not page:
            break
        payload = page + payload
        first_ts = int(page[0]["fundingTime"])
        next_end = first_ts - 1
        if end_time == next_end:
            break
        end_time = next_end
    records: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in payload:
        ts = int(item["fundingTime"])
        if ts in seen:
            continue
        seen.add(ts)
        records.append(
            {
                "timestamp": ts,
                "funding": parse_float(item.get("fundingRate")),
                "price": parse_float(item.get("markPrice")),
            }
        )
    return sorted(records, key=lambda row: row["timestamp"])


def align_derivatives(
    rows: list[dict[str, str]],
    *,
    interval: str,
    oi_records: list[dict[str, Any]],
    funding_records: list[dict[str, Any]],
) -> list[dict[str, str]]:
    interval_ms = INTERVAL_MS.get(interval, 900_000)
    aligned: list[dict[str, str]] = []
    oi_idx = -1
    funding_idx = -1
    for row in rows:
        open_ms = int(row.get("time_ms") or 0)
        close_ms = open_ms + interval_ms - 1
        while oi_idx + 1 < len(oi_records) and int(oi_records[oi_idx + 1]["timestamp"]) <= close_ms:
            oi_idx += 1
        while funding_idx + 1 < len(funding_records) and int(funding_records[funding_idx + 1]["timestamp"]) <= close_ms:
            funding_idx += 1
        oi_value = oi_records[oi_idx]["open_interest"] if oi_idx >= 0 else ""
        funding_value = funding_records[funding_idx]["funding"] if funding_idx >= 0 else ""
        aligned.append(
            {
                "time": row.get("time", ""),
                "price": row.get("close", ""),
                "open_interest": str(oi_value) if oi_value != "" and not math.isnan(float(oi_value)) else "",
                "volume": row.get("volume", ""),
                "funding": str(funding_value) if funding_value != "" and not math.isnan(float(funding_value)) else "",
            }
        )
    return aligned


def write_oi_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["time", "price", "open_interest", "volume", "funding"])
        writer.writeheader()
        writer.writerows(rows)


def candle_value(row: dict[str, str], name: str) -> float:
    return parse_float(row.get(name))


def row_open_ms(row: dict[str, str]) -> int:
    return int(parse_float(row.get("time_ms"), 0))


def completed_rows(rows: list[dict[str, str]], *, close_ms: int, interval: str) -> list[dict[str, str]]:
    interval_ms = INTERVAL_MS.get(interval, 3_600_000)
    return [row for row in rows if row_open_ms(row) + interval_ms - 1 <= close_ms]


def htf_bias_from_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    closes = [candle_value(row, "close") for row in rows if not math.isnan(candle_value(row, "close"))]
    if len(closes) < 220:
        return {"bias": "NEUTRAL", "regime": "insufficient_htf", "reason": "need_220_completed_htf_bars"}

    def ema(values: list[float], period: int) -> float:
        alpha = 2 / (period + 1)
        value = sum(values[:period]) / period
        for item in values[period:]:
            value = alpha * item + (1 - alpha) * value
        return value

    close = closes[-1]
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    slope_20 = close - closes[-20]
    if close > ema20 > ema50 > ema200 and slope_20 > 0:
        return {"bias": "LONG", "regime": "htf_trend_up", "reason": "htf_ema_stack_up"}
    if close < ema20 < ema50 < ema200 and slope_20 < 0:
        return {"bias": "SHORT", "regime": "htf_trend_down", "reason": "htf_ema_stack_down"}
    if close > ema200 and slope_20 > 0:
        return {"bias": "LONG", "regime": "htf_up_bias", "reason": "htf_above_ema200"}
    if close < ema200 and slope_20 < 0:
        return {"bias": "SHORT", "regime": "htf_down_bias", "reason": "htf_below_ema200"}
    return {"bias": "NEUTRAL", "regime": "htf_mixed", "reason": "htf_no_clear_bias"}


def pct_return(values: list[float], lookback: int) -> float | None:
    if len(values) <= lookback:
        return None
    prev = values[-lookback - 1]
    cur = values[-1]
    if prev == 0 or math.isnan(prev) or math.isnan(cur):
        return None
    return (cur - prev) / prev * 100


def spot_perp_context(perp_rows: list[dict[str, str]], spot_rows: list[dict[str, str]]) -> dict[str, Any]:
    perp_closes = [candle_value(row, "close") for row in perp_rows if not math.isnan(candle_value(row, "close"))]
    spot_closes = [candle_value(row, "close") for row in spot_rows if not math.isnan(candle_value(row, "close"))]
    spot_volumes = [candle_value(row, "volume") for row in spot_rows if not math.isnan(candle_value(row, "volume"))]
    if len(perp_closes) < 14 or len(spot_closes) < 14:
        return {"ready": False, "reason": "need_spot_perp_history"}

    spot_ret_3 = pct_return(spot_closes, 3)
    perp_ret_3 = pct_return(perp_closes, 3)
    spot_ret_12 = pct_return(spot_closes, 12)
    perp_ret_12 = pct_return(perp_closes, 12)
    volume_ratio = None
    if len(spot_volumes) >= 20:
        avg = sum(spot_volumes[-20:]) / 20
        if avg > 0:
            volume_ratio = spot_volumes[-1] / avg
    return {
        "ready": True,
        "spot_ret_3": spot_ret_3,
        "perp_ret_3": perp_ret_3,
        "divergence_3": None if spot_ret_3 is None or perp_ret_3 is None else spot_ret_3 - perp_ret_3,
        "spot_ret_12": spot_ret_12,
        "perp_ret_12": perp_ret_12,
        "divergence_12": None if spot_ret_12 is None or perp_ret_12 is None else spot_ret_12 - perp_ret_12,
        "spot_volume_ratio": volume_ratio,
    }


def v04_alpha_signal(strategy: str, window: list[dict[str, str]], result: dict[str, Any], htf: dict[str, Any]) -> dict[str, Any]:
    if len(window) < 90:
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "need_90_ltf_bars"}

    last = window[-1]
    close = candle_value(last, "close")
    high = candle_value(last, "high")
    low = candle_value(last, "low")
    lookback = 55
    prev = window[-lookback - 1 : -1]
    if len(prev) < lookback:
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "need_donchian_window"}

    upper = max(candle_value(row, "high") for row in prev)
    lower = min(candle_value(row, "low") for row in prev)
    width = upper - lower
    indicators = result.get("indicators", {})
    derivatives = result.get("derivatives", {})
    atr = parse_float(indicators.get("atr14"))
    rsi = parse_float(indicators.get("rsi14"), 50.0)
    strength = parse_float(indicators.get("trend_strength_proxy"), 0.0)
    rel_volume = parse_float(indicators.get("relative_volume"), 1.0)
    oi_delta = parse_float(derivatives.get("oi_delta_pct"), 0.0)
    funding = parse_float(derivatives.get("funding"), 0.0)
    htf_bias = str(htf.get("bias", "NEUTRAL"))

    if math.isnan(close) or math.isnan(high) or math.isnan(low) or math.isnan(atr) or atr <= 0 or width <= 0:
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "bad_price_or_atr"}

    width_atr = width / atr
    near_low = close <= lower + max(width * 0.18, atr * 0.9)
    near_high = close >= upper - max(width * 0.18, atr * 0.9)
    bullish_sweep = low < lower and close > lower
    bearish_sweep = high > upper and close < upper
    breakout_up = close > upper and htf_bias == "LONG"
    breakout_down = close < lower and htf_bias == "SHORT"
    range_ok = 2.0 <= width_atr <= 12.0 and abs(strength) <= 2.2
    funding_long_ok = funding <= 0.0008
    funding_short_ok = funding >= -0.0008

    def signal(side: str, setup: str, confidence: float, reason: str) -> dict[str, Any]:
        return {
            "side": side,
            "setup": setup,
            "confidence": round(confidence, 3),
            "reason": reason,
            "levels": {"upper": round(upper, 8), "lower": round(lower, 8), "width_atr": round(width_atr, 3)},
            "htf_bias": htf_bias,
        }

    if strategy == "v04_trend":
        if breakout_up and strength >= 0.8 and oi_delta >= -0.05 and funding_long_ok:
            return signal("LONG", "V04_TREND_BREAKOUT", 0.64, "htf_aligned_breakout_up")
        if breakout_down and strength <= -0.8 and oi_delta >= -0.05 and funding_short_ok:
            return signal("SHORT", "V04_TREND_BREAKOUT", 0.64, "htf_aligned_breakout_down")
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "no_v04_trend_breakout"}

    if strategy == "v04_sweep":
        if bullish_sweep and rsi <= 52 and funding_long_ok and htf_bias != "SHORT":
            return signal("LONG", "V04_SWEEP_REVERSAL", 0.62, "bullish_sweep_no_htf_short")
        if bearish_sweep and rsi >= 48 and funding_short_ok and htf_bias != "LONG":
            return signal("SHORT", "V04_SWEEP_REVERSAL", 0.62, "bearish_sweep_no_htf_long")
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "no_v04_sweep"}

    if strategy == "v04_range":
        if range_ok and near_low and rsi <= 46 and funding_long_ok and htf_bias != "SHORT" and rel_volume <= 1.8:
            return signal("LONG", "V04_RANGE_FADE", 0.58, "range_low_fade")
        if range_ok and near_high and rsi >= 54 and funding_short_ok and htf_bias != "LONG" and rel_volume <= 1.8:
            return signal("SHORT", "V04_RANGE_FADE", 0.58, "range_high_fade")
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "no_v04_range_fade"}

    if strategy == "v04_combo":
        sweep = v04_alpha_signal("v04_sweep", window, result, htf)
        if sweep.get("side") in {"LONG", "SHORT"}:
            return sweep
        trend = v04_alpha_signal("v04_trend", window, result, htf)
        if trend.get("side") in {"LONG", "SHORT"}:
            return trend
        return v04_alpha_signal("v04_range", window, result, htf)

    return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": f"unknown_strategy:{strategy}"}


def v05_alpha_signal(
    strategy: str,
    window: list[dict[str, str]],
    result: dict[str, Any],
    htf: dict[str, Any],
    spot_ctx: dict[str, Any],
) -> dict[str, Any]:
    if not spot_ctx.get("ready"):
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": spot_ctx.get("reason", "spot_perp_not_ready")}
    if len(window) < 90:
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "need_90_ltf_bars"}

    last = window[-1]
    close = candle_value(last, "close")
    high = candle_value(last, "high")
    low = candle_value(last, "low")
    prev = window[-56:-1]
    upper = max(candle_value(row, "high") for row in prev)
    lower = min(candle_value(row, "low") for row in prev)
    width = upper - lower
    indicators = result.get("indicators", {})
    derivatives = result.get("derivatives", {})
    atr = parse_float(indicators.get("atr14"))
    rsi = parse_float(indicators.get("rsi14"), 50.0)
    strength = parse_float(indicators.get("trend_strength_proxy"), 0.0)
    funding = parse_float(derivatives.get("funding"), 0.0)
    oi_delta = parse_float(derivatives.get("oi_delta_pct"), 0.0)
    htf_bias = str(htf.get("bias", "NEUTRAL"))
    div_3 = parse_float(spot_ctx.get("divergence_3"), 0.0)
    div_12 = parse_float(spot_ctx.get("divergence_12"), 0.0)
    spot_ret_12 = parse_float(spot_ctx.get("spot_ret_12"), 0.0)
    volume_ratio = parse_float(spot_ctx.get("spot_volume_ratio"), 1.0)

    if math.isnan(close) or math.isnan(high) or math.isnan(low) or math.isnan(atr) or atr <= 0 or width <= 0:
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "bad_price_or_atr"}

    width_atr = width / atr
    bullish_sweep = low < lower and close > lower
    bearish_sweep = high > upper and close < upper
    near_low = close <= lower + max(width * 0.18, atr * 0.9)
    near_high = close >= upper - max(width * 0.18, atr * 0.9)
    range_ok = 2.0 <= width_atr <= 12.0 and abs(strength) <= 2.2
    funding_long_ok = funding <= 0.0008
    funding_short_ok = funding >= -0.0008
    spot_long_ok = spot_ret_12 >= 0 and div_12 >= -0.25
    spot_short_ok = spot_ret_12 <= 0 and div_12 <= 0.25

    def signal(side: str, setup: str, confidence: float, reason: str) -> dict[str, Any]:
        return {
            "side": side,
            "setup": setup,
            "confidence": round(confidence, 3),
            "reason": reason,
            "levels": {"upper": round(upper, 8), "lower": round(lower, 8), "width_atr": round(width_atr, 3)},
            "htf_bias": htf_bias,
            "spot_perp": {
                "divergence_3": None if spot_ctx.get("divergence_3") is None else round(float(spot_ctx["divergence_3"]), 6),
                "divergence_12": None if spot_ctx.get("divergence_12") is None else round(float(spot_ctx["divergence_12"]), 6),
                "spot_volume_ratio": None if spot_ctx.get("spot_volume_ratio") is None else round(float(spot_ctx["spot_volume_ratio"]), 6),
            },
        }

    if strategy == "v05_spot_trend":
        if close > upper and htf_bias == "LONG" and spot_long_ok and oi_delta >= -0.05 and funding_long_ok:
            return signal("LONG", "V05_SPOT_CONFIRMED_TREND", 0.66, "spot_confirms_breakout_up")
        if close < lower and htf_bias == "SHORT" and spot_short_ok and oi_delta >= -0.05 and funding_short_ok:
            return signal("SHORT", "V05_SPOT_CONFIRMED_TREND", 0.66, "spot_confirms_breakout_down")
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "no_v05_spot_trend"}

    if strategy == "v05_spot_sweep":
        if bullish_sweep and htf_bias != "SHORT" and div_3 >= 0.05 and rsi <= 52 and funding_long_ok:
            return signal("LONG", "V05_SPOT_ABSORPTION_SWEEP", 0.64, "spot_absorption_after_bullish_sweep")
        if bearish_sweep and htf_bias != "LONG" and div_3 <= -0.05 and rsi >= 48 and funding_short_ok:
            return signal("SHORT", "V05_SPOT_ABSORPTION_SWEEP", 0.64, "spot_distribution_after_bearish_sweep")
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "no_v05_spot_sweep"}

    if strategy == "v05_spot_range":
        if range_ok and near_low and htf_bias != "SHORT" and div_3 >= 0 and volume_ratio <= 2.2 and funding_long_ok:
            return signal("LONG", "V05_SPOT_RANGE_FADE", 0.6, "spot_supports_range_low")
        if range_ok and near_high and htf_bias != "LONG" and div_3 <= 0 and volume_ratio <= 2.2 and funding_short_ok:
            return signal("SHORT", "V05_SPOT_RANGE_FADE", 0.6, "spot_supports_range_high")
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "no_v05_spot_range"}

    if strategy == "v05_spot_combo":
        for candidate in ("v05_spot_sweep", "v05_spot_trend", "v05_spot_range"):
            verdict = v05_alpha_signal(candidate, window, result, htf, spot_ctx)
            if verdict.get("side") in {"LONG", "SHORT"}:
                return verdict
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "no_v05_combo_signal"}

    return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": f"unknown_strategy:{strategy}"}


def v08_alpha_signal(
    strategy: str,
    window: list[dict[str, str]],
    result: dict[str, Any],
    htf: dict[str, Any],
    spot_ctx: dict[str, Any],
) -> dict[str, Any]:
    if strategy != "v08_mined_short":
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": f"unknown_strategy:{strategy}"}
    if not spot_ctx.get("ready"):
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": spot_ctx.get("reason", "spot_perp_not_ready")}
    if len(window) < 90:
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "need_90_ltf_bars"}

    last = window[-1]
    close = candle_value(last, "close")
    prev = window[-56:-1]
    upper = max(candle_value(row, "high") for row in prev)
    lower = min(candle_value(row, "low") for row in prev)
    width = upper - lower
    indicators = result.get("indicators", {})
    atr = parse_float(indicators.get("atr14"))
    rsi = parse_float(indicators.get("rsi14"), 50.0)
    volume_ratio = parse_float(spot_ctx.get("spot_volume_ratio"), 1.0)

    if math.isnan(close) or math.isnan(atr) or atr <= 0 or width <= 0:
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "bad_price_or_atr"}

    near_low = close <= lower + max(width * 0.18, atr * 0.9)
    if near_low and rsi <= 40 and volume_ratio <= 0.8:
        return {
            "side": "SHORT",
            "setup": "V08_MINED_WEAK_BID_SHORT",
            "confidence": 0.7,
            "reason": "near_low_rsi_weak_spot_volume",
            "levels": {"upper": round(upper, 8), "lower": round(lower, 8), "width_atr": round(width / atr, 3)},
            "htf_bias": str(htf.get("bias", "NEUTRAL")),
            "spot_perp": {
                "divergence_3": None if spot_ctx.get("divergence_3") is None else round(float(spot_ctx["divergence_3"]), 6),
                "divergence_12": None if spot_ctx.get("divergence_12") is None else round(float(spot_ctx["divergence_12"]), 6),
                "spot_volume_ratio": None if spot_ctx.get("spot_volume_ratio") is None else round(float(spot_ctx["spot_volume_ratio"]), 6),
            },
            "mined_from": "v0.7 top slice: near_low + rsi14<=40 + spot_volume_ratio<=0.8",
        }
    return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "no_v08_mined_short"}


def v10_alpha_signal(
    strategy: str,
    window: list[dict[str, str]],
    result: dict[str, Any],
    htf: dict[str, Any],
    spot_ctx: dict[str, Any],
) -> dict[str, Any]:
    if len(window) < 90:
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "need_90_ltf_bars"}

    last = window[-1]
    close = candle_value(last, "close")
    high = candle_value(last, "high")
    low = candle_value(last, "low")
    prev = window[-56:-1]
    upper = max(candle_value(row, "high") for row in prev)
    lower = min(candle_value(row, "low") for row in prev)
    width = upper - lower
    indicators = result.get("indicators", {})
    atr = parse_float(indicators.get("atr14"))
    rsi = parse_float(indicators.get("rsi14"), 50.0)
    strength = parse_float(indicators.get("trend_strength_proxy"), 0.0)
    rel_volume = parse_float(indicators.get("relative_volume"), 1.0)
    htf_bias = str(htf.get("bias", "NEUTRAL"))
    spot_volume_ratio = parse_float(spot_ctx.get("spot_volume_ratio"), 1.0)

    if math.isnan(close) or math.isnan(high) or math.isnan(low) or math.isnan(atr) or atr <= 0 or width <= 0:
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "bad_price_or_atr"}

    width_atr = width / atr
    near_low = close <= lower + max(width * 0.18, atr * 0.9)
    near_high = close >= upper - max(width * 0.18, atr * 0.9)
    range_ok = 2.0 <= width_atr <= 12.0 and abs(strength) <= 2.2

    def signal(side: str, setup: str, confidence: float, reason: str, mined_from: str) -> dict[str, Any]:
        return {
            "side": side,
            "setup": setup,
            "confidence": round(confidence, 3),
            "reason": reason,
            "levels": {"upper": round(upper, 8), "lower": round(lower, 8), "width_atr": round(width_atr, 3)},
            "htf_bias": htf_bias,
            "spot_perp": {
                "divergence_3": None if spot_ctx.get("divergence_3") is None else round(float(spot_ctx["divergence_3"]), 6),
                "divergence_12": None if spot_ctx.get("divergence_12") is None else round(float(spot_ctx["divergence_12"]), 6),
                "spot_volume_ratio": None if spot_ctx.get("spot_volume_ratio") is None else round(float(spot_ctx["spot_volume_ratio"]), 6),
            },
            "mined_from": mined_from,
        }

    if strategy == "v10_15m_fade_short":
        if near_high and rsi >= 60 and rel_volume <= 0.8:
            return signal(
                "SHORT",
                "V10_15M_WEAK_VOLUME_RANGE_TOP_SHORT",
                0.68,
                "near_high_rsi_hot_relative_volume_quiet",
                "v0.9 15m top slice: near_high + rsi14>=60 + relative_volume<=0.8",
            )
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "no_v10_15m_fade_short"}

    if strategy == "v10_1h_weak_bid_short":
        if not spot_ctx.get("ready"):
            return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": spot_ctx.get("reason", "spot_perp_not_ready")}
        if near_low and spot_volume_ratio <= 0.8 and 2.0 <= width_atr <= 8.0:
            return signal(
                "SHORT",
                "V10_1H_WEAK_BID_CONTINUATION_SHORT",
                0.7,
                "near_low_spot_volume_quiet_mid_width_range",
                "v0.9 1h top slice: near_low + spot_volume_ratio<=0.8 + donchian_width_atr_between_2_8",
            )
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "no_v10_1h_weak_bid_short"}

    if strategy == "v10_4h_range_long":
        if range_ok and htf_bias == "NEUTRAL" and rsi >= 70:
            return signal(
                "LONG",
                "V10_4H_RANGE_MOMENTUM_LONG",
                0.64,
                "range_ok_htf_neutral_rsi_hot",
                "v0.9 4h top slice: range_ok + htf_bias=NEUTRAL + rsi14>=70",
            )
        return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": "no_v10_4h_range_long"}

    return {"side": "NEUTRAL", "setup": "ABSTAIN", "reason": f"unknown_strategy:{strategy}"}


def score_window(
    rows: list[dict[str, str]],
    *,
    symbol: str,
    tf: str,
    temp_csv: Path,
    oi_rows: list[dict[str, str]] | None = None,
    temp_oi_csv: Path | None = None,
) -> dict[str, Any]:
    write_ohlcv_csv(temp_csv, rows)
    files = {"ohlcv": str(temp_csv)}
    if oi_rows is not None and temp_oi_csv is not None:
        write_oi_csv(temp_oi_csv, oi_rows)
        files["oi"] = str(temp_oi_csv)
    return PR.run_pipeline(symbol, files, tf=tf)


def find_exit(
    rows: list[dict[str, str]],
    *,
    start_index: int,
    side: str,
    entry: float,
    stop: float,
    take_profit: float,
    max_hold_bars: int,
) -> tuple[int, float, str]:
    last_index = min(len(rows) - 1, start_index + max_hold_bars - 1)
    for idx in range(start_index, last_index + 1):
        high = candle_value(rows[idx], "high")
        low = candle_value(rows[idx], "low")
        close = candle_value(rows[idx], "close")
        if side == "LONG":
            if low <= stop:
                return idx, stop, "stop"
            if high >= take_profit:
                return idx, take_profit, "take_profit"
        else:
            if high >= stop:
                return idx, stop, "stop"
            if low <= take_profit:
                return idx, take_profit, "take_profit"
        if math.isnan(close):
            continue
    return last_index, candle_value(rows[last_index], "close"), "time_stop"


EVENT_FIELDNAMES = [
    "index",
    "time",
    "symbol",
    "tf",
    "close",
    "next_open",
    "forward_bars",
    "future_ret_pct",
    "max_up_atr",
    "max_down_atr",
    "long_1r_outcome",
    "long_1r_reason",
    "short_1r_outcome",
    "short_1r_reason",
    "label_1r",
    "regime",
    "htf_bias",
    "htf_regime",
    "long_score",
    "short_score",
    "delta",
    "atr14",
    "rsi14",
    "trend_strength",
    "relative_volume",
    "oi_delta_pct",
    "oi_zscore",
    "funding",
    "spot_ret_3",
    "perp_ret_3",
    "spot_perp_divergence_3",
    "spot_ret_12",
    "perp_ret_12",
    "spot_perp_divergence_12",
    "spot_volume_ratio",
    "donchian_upper_55",
    "donchian_lower_55",
    "donchian_width_atr",
    "near_high",
    "near_low",
    "bullish_sweep",
    "bearish_sweep",
    "breakout_up",
    "breakout_down",
    "range_ok",
    "v02_side",
    "v02_setup",
    "v02_confidence",
    "v04_trend_side",
    "v04_sweep_side",
    "v04_range_side",
    "v05_spot_trend_side",
    "v05_spot_sweep_side",
    "v05_spot_range_side",
    "data_degraded",
    "warnings",
    "reasons",
]


def round_optional(value: Any, ndigits: int = 6) -> float | None:
    parsed = parse_float(value)
    if math.isnan(parsed):
        return None
    return round(parsed, ndigits)


def bool_flag(value: bool) -> int:
    return 1 if value else 0


def one_r_outcome(
    future_rows: list[dict[str, str]],
    *,
    side: str,
    entry: float,
    risk: float,
) -> tuple[int, str]:
    if risk <= 0 or math.isnan(entry):
        return 0, "bad_entry_or_risk"
    if side == "LONG":
        stop = entry - risk
        target = entry + risk
        for row in future_rows:
            hit_stop = candle_value(row, "low") <= stop
            hit_target = candle_value(row, "high") >= target
            if hit_stop and hit_target:
                return 0, "ambiguous_same_bar"
            if hit_target:
                return 1, "target_first"
            if hit_stop:
                return -1, "stop_first"
    else:
        stop = entry + risk
        target = entry - risk
        for row in future_rows:
            hit_stop = candle_value(row, "high") >= stop
            hit_target = candle_value(row, "low") <= target
            if hit_stop and hit_target:
                return 0, "ambiguous_same_bar"
            if hit_target:
                return 1, "target_first"
            if hit_stop:
                return -1, "stop_first"
    return 0, "no_1r_touch"


def future_event_stats(
    rows: list[dict[str, str]],
    *,
    start_index: int,
    forward_bars: int,
    risk: float,
) -> dict[str, Any]:
    end_index = min(len(rows) - 1, start_index + forward_bars - 1)
    future_rows = rows[start_index : end_index + 1]
    if not future_rows:
        return {"ready": False, "reason": "no_future_rows"}
    entry = candle_value(rows[start_index], "open")
    end_close = candle_value(rows[end_index], "close")
    highs = [candle_value(row, "high") for row in future_rows]
    lows = [candle_value(row, "low") for row in future_rows]
    if math.isnan(entry) or math.isnan(end_close) or risk <= 0:
        return {"ready": False, "reason": "bad_future_entry_or_risk"}

    long_outcome, long_reason = one_r_outcome(future_rows, side="LONG", entry=entry, risk=risk)
    short_outcome, short_reason = one_r_outcome(future_rows, side="SHORT", entry=entry, risk=risk)
    if long_outcome == 1 and short_outcome != 1:
        label = "LONG_1R"
    elif short_outcome == 1 and long_outcome != 1:
        label = "SHORT_1R"
    elif long_outcome == 1 and short_outcome == 1:
        label = "BOTH_1R_PATH_DEPENDENT"
    else:
        label = "NO_1R_EDGE"

    return {
        "ready": True,
        "entry": entry,
        "future_ret_pct": (end_close - entry) / entry * 100 if entry else None,
        "max_up_atr": (max(highs) - entry) / risk if highs else None,
        "max_down_atr": (entry - min(lows)) / risk if lows else None,
        "long_1r_outcome": long_outcome,
        "long_1r_reason": long_reason,
        "short_1r_outcome": short_outcome,
        "short_1r_reason": short_reason,
        "label_1r": label,
    }


def summarize_event_rows(events: list[dict[str, Any]]) -> dict[str, Any]:
    def avg(values: list[float]) -> float | None:
        clean = [value for value in values if not math.isnan(value)]
        if not clean:
            return None
        return round(sum(clean) / len(clean), 6)

    def slice_summary(name: str, selected: list[dict[str, Any]]) -> dict[str, Any]:
        long_hits = [row for row in selected if row.get("long_1r_outcome") == 1]
        short_hits = [row for row in selected if row.get("short_1r_outcome") == 1]
        return {
            "name": name,
            "rows": len(selected),
            "avg_future_ret_pct": avg([parse_float(row.get("future_ret_pct")) for row in selected]),
            "long_1r_hit_pct": round(len(long_hits) / len(selected) * 100, 3) if selected else None,
            "short_1r_hit_pct": round(len(short_hits) / len(selected) * 100, 3) if selected else None,
        }

    labels: dict[str, int] = {}
    for row in events:
        label = str(row.get("label_1r", "UNKNOWN"))
        labels[label] = labels.get(label, 0) + 1

    return {
        "events": len(events),
        "labels": labels,
        "avg_future_ret_pct": avg([parse_float(row.get("future_ret_pct")) for row in events]),
        "slices": [
            slice_summary("bullish_sweep", [row for row in events if row.get("bullish_sweep") == 1]),
            slice_summary("bearish_sweep", [row for row in events if row.get("bearish_sweep") == 1]),
            slice_summary("breakout_up", [row for row in events if row.get("breakout_up") == 1]),
            slice_summary("breakout_down", [row for row in events if row.get("breakout_down") == 1]),
            slice_summary("range_ok", [row for row in events if row.get("range_ok") == 1]),
            slice_summary("htf_long", [row for row in events if row.get("htf_bias") == "LONG"]),
            slice_summary("htf_short", [row for row in events if row.get("htf_bias") == "SHORT"]),
            slice_summary(
                "positive_spot_perp_divergence_12",
                [row for row in events if parse_float(row.get("spot_perp_divergence_12"), 0.0) > 0],
            ),
            slice_summary(
                "negative_spot_perp_divergence_12",
                [row for row in events if parse_float(row.get("spot_perp_divergence_12"), 0.0) < 0],
            ),
        ],
    }


def render_event_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MAX Core Lite v0.6 Event Export",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Rows scanned: `{report['data']['rows']}`",
        f"- Events exported: `{report['summary']['events']}`",
        f"- Period: `{report['data']['first_time']}` -> `{report['data']['last_time']}`",
        f"- Forward bars: `{report['params']['event_forward_bars']}`",
        f"- CSV: `{report['files']['csv']}`",
        f"- JSON: `{report['files']['json']}`",
        "",
        "## Label Counts",
        "",
        "| Label | Count |",
        "| --- | ---: |",
    ]
    for label, count in sorted(report["summary"]["labels"].items()):
        lines.append(f"| `{label}` | {count} |")
    lines.extend(
        [
            "",
            "## Slice Diagnostics",
            "",
            "| Slice | Rows | Avg future % | Long 1R hit % | Short 1R hit % |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in report["summary"]["slices"]:
        lines.append(
            f"| `{item['name']}` | {item['rows']} | {item['avg_future_ret_pct']} | "
            f"{item['long_1r_hit_pct']} | {item['short_1r_hit_pct']} |"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            report["runtime_boundary"],
            "",
            "## Next Use",
            "",
            "Use this export to find which feature slices have stable forward outcomes before adding another strategy rule.",
            "",
        ]
    )
    return "\n".join(lines)


def run_event_export(
    *,
    rows: list[dict[str, str]],
    oi_rows: list[dict[str, str]] | None,
    htf_rows: list[dict[str, str]] | None,
    spot_rows: list[dict[str, str]] | None,
    symbol: str,
    tf: str,
    htf_interval: str,
    warmup_bars: int,
    event_forward_bars: int,
    event_stride: int,
    out_prefix: Path,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    skipped = {"bad_atr_or_levels": 0, "bad_future_window": 0}
    stride = max(1, event_stride)
    forward = max(1, event_forward_bars)
    start = max(warmup_bars, 90)

    with tempfile.TemporaryDirectory(prefix="max_core_lite_events_") as temp_dir:
        temp_path = Path(temp_dir)
        for i in range(start, max(start, len(rows) - forward), stride):
            if i + 1 >= len(rows):
                break
            window = rows[: i + 1]
            oi_window = oi_rows[: i + 1] if oi_rows is not None else None
            result = score_window(
                window,
                symbol=symbol,
                tf=tf,
                temp_csv=temp_path / "window.csv",
                oi_rows=oi_window,
                temp_oi_csv=temp_path / "window_oi.csv" if oi_window is not None else None,
            )
            lower_close_ms = row_open_ms(rows[i]) + INTERVAL_MS.get(tf, INTERVAL_MS.get("1h", 3_600_000)) - 1
            htf_window = completed_rows(htf_rows or [], close_ms=lower_close_ms, interval=htf_interval)
            htf = htf_bias_from_rows(htf_window)
            spot_window = completed_rows(spot_rows or [], close_ms=lower_close_ms, interval=tf)
            spot_ctx = spot_perp_context(window, spot_window)

            indicators = result.get("indicators", {})
            derivatives = result.get("derivatives", {})
            scores = result.get("scores", {})
            v02 = result.get("strategy_v02", {}) if isinstance(result.get("strategy_v02"), dict) else {}
            atr = parse_float(indicators.get("atr14"))
            rsi = parse_float(indicators.get("rsi14"), 50.0)
            strength = parse_float(indicators.get("trend_strength_proxy"), 0.0)
            rel_volume = parse_float(indicators.get("relative_volume"), 1.0)
            last = window[-1]
            close = candle_value(last, "close")
            high = candle_value(last, "high")
            low = candle_value(last, "low")
            prev = window[-56:-1]
            if len(prev) < 55 or math.isnan(atr) or atr <= 0 or math.isnan(close):
                skipped["bad_atr_or_levels"] += 1
                continue

            upper = max(candle_value(row, "high") for row in prev)
            lower = min(candle_value(row, "low") for row in prev)
            width = upper - lower
            if width <= 0:
                skipped["bad_atr_or_levels"] += 1
                continue
            width_atr = width / atr
            near_low = close <= lower + max(width * 0.18, atr * 0.9)
            near_high = close >= upper - max(width * 0.18, atr * 0.9)
            bullish_sweep = low < lower and close > lower
            bearish_sweep = high > upper and close < upper
            htf_bias = str(htf.get("bias", "NEUTRAL"))
            breakout_up = close > upper and htf_bias == "LONG"
            breakout_down = close < lower and htf_bias == "SHORT"
            range_ok = 2.0 <= width_atr <= 12.0 and abs(strength) <= 2.2

            future = future_event_stats(rows, start_index=i + 1, forward_bars=forward, risk=atr)
            if not future.get("ready"):
                skipped["bad_future_window"] += 1
                continue

            v04_trend = v04_alpha_signal("v04_trend", window, result, htf)
            v04_sweep = v04_alpha_signal("v04_sweep", window, result, htf)
            v04_range = v04_alpha_signal("v04_range", window, result, htf)
            v05_trend = v05_alpha_signal("v05_spot_trend", window, result, htf, spot_ctx)
            v05_sweep = v05_alpha_signal("v05_spot_sweep", window, result, htf, spot_ctx)
            v05_range = v05_alpha_signal("v05_spot_range", window, result, htf, spot_ctx)

            event = {
                "index": len(events) + 1,
                "time": rows[i].get("time", str(i)),
                "symbol": symbol.upper(),
                "tf": tf,
                "close": round(close, 8),
                "next_open": round(float(future["entry"]), 8),
                "forward_bars": forward,
                "future_ret_pct": round_optional(future.get("future_ret_pct")),
                "max_up_atr": round_optional(future.get("max_up_atr")),
                "max_down_atr": round_optional(future.get("max_down_atr")),
                "long_1r_outcome": future.get("long_1r_outcome"),
                "long_1r_reason": future.get("long_1r_reason"),
                "short_1r_outcome": future.get("short_1r_outcome"),
                "short_1r_reason": future.get("short_1r_reason"),
                "label_1r": future.get("label_1r"),
                "regime": str(result.get("regime")),
                "htf_bias": htf_bias,
                "htf_regime": str(htf.get("regime", "unknown")),
                "long_score": round_optional(scores.get("long_score"), 3),
                "short_score": round_optional(scores.get("short_score"), 3),
                "delta": round_optional(scores.get("delta"), 3),
                "atr14": round_optional(atr),
                "rsi14": round_optional(rsi),
                "trend_strength": round_optional(strength),
                "relative_volume": round_optional(rel_volume),
                "oi_delta_pct": round_optional(derivatives.get("oi_delta_pct")),
                "oi_zscore": round_optional(derivatives.get("oi_zscore")),
                "funding": round_optional(derivatives.get("funding"), 8),
                "spot_ret_3": round_optional(spot_ctx.get("spot_ret_3")),
                "perp_ret_3": round_optional(spot_ctx.get("perp_ret_3")),
                "spot_perp_divergence_3": round_optional(spot_ctx.get("divergence_3")),
                "spot_ret_12": round_optional(spot_ctx.get("spot_ret_12")),
                "perp_ret_12": round_optional(spot_ctx.get("perp_ret_12")),
                "spot_perp_divergence_12": round_optional(spot_ctx.get("divergence_12")),
                "spot_volume_ratio": round_optional(spot_ctx.get("spot_volume_ratio")),
                "donchian_upper_55": round(upper, 8),
                "donchian_lower_55": round(lower, 8),
                "donchian_width_atr": round(width_atr, 6),
                "near_high": bool_flag(near_high),
                "near_low": bool_flag(near_low),
                "bullish_sweep": bool_flag(bullish_sweep),
                "bearish_sweep": bool_flag(bearish_sweep),
                "breakout_up": bool_flag(breakout_up),
                "breakout_down": bool_flag(breakout_down),
                "range_ok": bool_flag(range_ok),
                "v02_side": str(v02.get("side", "NEUTRAL")),
                "v02_setup": str(v02.get("setup", "ABSTAIN")),
                "v02_confidence": round_optional(v02.get("confidence"), 3),
                "v04_trend_side": str(v04_trend.get("side", "NEUTRAL")),
                "v04_sweep_side": str(v04_sweep.get("side", "NEUTRAL")),
                "v04_range_side": str(v04_range.get("side", "NEUTRAL")),
                "v05_spot_trend_side": str(v05_trend.get("side", "NEUTRAL")),
                "v05_spot_sweep_side": str(v05_sweep.get("side", "NEUTRAL")),
                "v05_spot_range_side": str(v05_range.get("side", "NEUTRAL")),
                "data_degraded": bool_flag(bool(result.get("data_degraded"))),
                "warnings": ";".join(result.get("warnings", [])),
                "reasons": ";".join(result.get("reasons", [])),
            }
            events.append(event)

    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    csv_path = out_prefix.with_suffix(".csv")
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EVENT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(events)

    report = {
        "generated_at": now_iso(),
        "engine": "MAX_CORE_LITE_EVENT_EXPORT",
        "engine_version": "0.6.0",
        "data": {
            "rows": len(rows),
            "oi_rows": len(oi_rows or []),
            "htf_rows": len(htf_rows or []),
            "spot_rows": len(spot_rows or []),
            "first_time": rows[0].get("time") if rows else None,
            "last_time": rows[-1].get("time") if rows else None,
        },
        "params": {
            "symbol": symbol.upper(),
            "tf": tf,
            "htf_interval": htf_interval,
            "warmup_bars": warmup_bars,
            "event_forward_bars": forward,
            "event_stride": stride,
        },
        "summary": summarize_event_rows(events),
        "skipped": skipped,
        "files": {
            "csv": str(csv_path),
            "json": str(json_path),
            "md": str(md_path),
        },
        "runtime_boundary": (
            "Research-only labelled event export. It creates training/diagnostic rows from public market data; "
            "it is not a trading signal, not a live bot, and not profitability proof."
        ),
    }
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_event_markdown(report), encoding="utf-8")
    return report


def simulate_backtest(
    rows: list[dict[str, str]],
    *,
    symbol: str,
    tf: str,
    warmup_bars: int,
    entry_threshold: float,
    stop_atr: float,
    take_atr: float,
    max_hold_bars: int,
    fee_bps: float,
    slippage_bps: float,
    allow_price_only: bool,
    strategy: str,
    invert_signal: bool,
    htf_rows: list[dict[str, str]] | None,
    htf_interval: str,
    spot_rows: list[dict[str, str]] | None,
    spot_interval: str,
    window_csv: Path,
    oi_rows: list[dict[str, str]] | None = None,
    window_oi_csv: Path | None = None,
    min_trades: int = 100,
    min_expectancy_r: float = 0.0,
    min_winrate_pct: float = 50.0,
) -> dict[str, Any]:
    trades: list[Trade] = []
    decisions = {"LONG": 0, "SHORT": 0, "NEUTRAL": 0}
    skipped = {
        "too_few_rows": 0,
        "degraded_data": 0,
        "low_score": 0,
        "v02_abstain": 0,
        "v03_htf_block": 0,
        "v04_abstain": 0,
        "v05_abstain": 0,
        "v08_abstain": 0,
        "v10_abstain": 0,
        "bad_atr_or_entry": 0,
        "no_next_bar": 0,
    }

    i = max(warmup_bars, 35)
    while i < len(rows) - 1:
        window = rows[: i + 1]
        oi_window = oi_rows[: i + 1] if oi_rows is not None else None
        result = score_window(window, symbol=symbol, tf=tf, temp_csv=window_csv, oi_rows=oi_window, temp_oi_csv=window_oi_csv)
        lower_close_ms = row_open_ms(rows[i]) + INTERVAL_MS.get(tf, INTERVAL_MS.get("1h", 3_600_000)) - 1
        htf = {"bias": "NEUTRAL", "regime": "not_used", "reason": "not_used"}
        if strategy == "v03" or strategy.startswith("v04_"):
            htf_window = completed_rows(htf_rows or [], close_ms=lower_close_ms, interval=htf_interval)
            htf = htf_bias_from_rows(htf_window)
        spot_ctx = {"ready": False, "reason": "not_used"}
        if strategy.startswith("v05_") or strategy.startswith("v08_") or strategy.startswith("v10_"):
            htf_window = completed_rows(htf_rows or [], close_ms=lower_close_ms, interval=htf_interval)
            htf = htf_bias_from_rows(htf_window)
            spot_window = completed_rows(spot_rows or [], close_ms=lower_close_ms, interval=spot_interval)
            spot_ctx = spot_perp_context(window, spot_window)
        scores = result.get("scores", {})
        long_score = float(scores.get("long_score", 0.0))
        short_score = float(scores.get("short_score", 0.0))
        delta = long_score - short_score
        v02 = result.get("strategy_v02", {}) if isinstance(result.get("strategy_v02"), dict) else {}
        v04 = v04_alpha_signal(strategy, window, result, htf) if strategy.startswith("v04_") else {}
        v05 = v05_alpha_signal(strategy, window, result, htf, spot_ctx) if strategy.startswith("v05_") else {}
        v08 = v08_alpha_signal(strategy, window, result, htf, spot_ctx) if strategy.startswith("v08_") else {}
        v10 = v10_alpha_signal(strategy, window, result, htf, spot_ctx) if strategy.startswith("v10_") else {}
        if strategy.startswith("v04_"):
            side = str(v04.get("side", "NEUTRAL"))
        elif strategy.startswith("v05_"):
            side = str(v05.get("side", "NEUTRAL"))
        elif strategy.startswith("v08_"):
            side = str(v08.get("side", "NEUTRAL"))
        elif strategy.startswith("v10_"):
            side = str(v10.get("side", "NEUTRAL"))
        elif strategy in {"v02", "v03"}:
            side = str(v02.get("side", "NEUTRAL"))
        else:
            side = "LONG" if delta >= entry_threshold else "SHORT" if delta <= -entry_threshold else "NEUTRAL"
        if strategy == "v03" and side in {"LONG", "SHORT"}:
            if htf.get("bias") != side:
                side = "NEUTRAL"
                skipped["v03_htf_block"] += 1
        if invert_signal and side in {"LONG", "SHORT"}:
            side = "SHORT" if side == "LONG" else "LONG"
        decisions[side] = decisions.get(side, 0) + 1

        if len(window) < warmup_bars:
            skipped["too_few_rows"] += 1
            i += 1
            continue
        if result.get("data_degraded") and not allow_price_only:
            skipped["degraded_data"] += 1
            i += 1
            continue
        if side == "NEUTRAL" and strategy in {"v02", "v03"}:
            skipped["v02_abstain"] += 1
            i += 1
            continue
        if side == "NEUTRAL" and strategy.startswith("v04_"):
            skipped["v04_abstain"] += 1
            i += 1
            continue
        if side == "NEUTRAL" and strategy.startswith("v05_"):
            skipped["v05_abstain"] += 1
            i += 1
            continue
        if side == "NEUTRAL" and strategy.startswith("v08_"):
            skipped["v08_abstain"] += 1
            i += 1
            continue
        if side == "NEUTRAL" and strategy.startswith("v10_"):
            skipped["v10_abstain"] += 1
            i += 1
            continue
        if side == "NEUTRAL":
            skipped["low_score"] += 1
            i += 1
            continue

        next_index = i + 1
        if next_index >= len(rows):
            skipped["no_next_bar"] += 1
            break

        entry_open = candle_value(rows[next_index], "open")
        atr14 = parse_float(result.get("indicators", {}).get("atr14"))
        if math.isnan(entry_open) or math.isnan(atr14) or atr14 <= 0:
            skipped["bad_atr_or_entry"] += 1
            i += 1
            continue

        slip = slippage_bps / 10000
        entry = entry_open * (1 + slip if side == "LONG" else 1 - slip)
        risk = stop_atr * atr14
        if risk <= 0:
            skipped["bad_atr_or_entry"] += 1
            i += 1
            continue

        if side == "LONG":
            stop = entry - risk
            take_profit = entry + take_atr * atr14
        else:
            stop = entry + risk
            take_profit = entry - take_atr * atr14

        exit_index, raw_exit, exit_reason = find_exit(
            rows,
            start_index=next_index,
            side=side,
            entry=entry,
            stop=stop,
            take_profit=take_profit,
            max_hold_bars=max_hold_bars,
        )
        exit_price = raw_exit * (1 - slip if side == "LONG" else 1 + slip)
        gross = (exit_price - entry) if side == "LONG" else (entry - exit_price)
        gross_r = gross / risk
        fee_cost = ((entry + exit_price) * (fee_bps / 10000)) / risk
        net_r = gross_r - fee_cost

        trades.append(
            Trade(
                index=len(trades) + 1,
                entry_time=rows[next_index].get("time", str(next_index)),
                exit_time=rows[exit_index].get("time", str(exit_index)),
                side=side,
                entry=round(entry, 8),
                stop=round(stop, 8),
                take_profit=round(take_profit, 8),
                exit=round(exit_price, 8),
                exit_reason=exit_reason,
                bars_held=max(1, exit_index - next_index + 1),
                gross_r=round(gross_r, 6),
                net_r=round(net_r, 6),
                strategy=strategy,
                setup=(
                    str(v10.get("setup", "RAW_SCORE"))
                    if strategy.startswith("v10_")
                    else str(v08.get("setup", "RAW_SCORE"))
                    if strategy.startswith("v08_")
                    else str(v05.get("setup", "RAW_SCORE"))
                    if strategy.startswith("v05_")
                    else str(v04.get("setup", "RAW_SCORE"))
                    if strategy.startswith("v04_")
                    else str(v02.get("setup", "RAW_SCORE")) if strategy in {"v02", "v03"} else "RAW_SCORE"
                )
                + ("_INVERTED" if invert_signal else ""),
                confidence=round(
                    float(
                        v10.get(
                            "confidence",
                            v08.get(
                                "confidence",
                                v05.get(
                                    "confidence",
                                    v04.get("confidence", v02.get("confidence", 0.0) if strategy in {"v02", "v03"} else 0.0),
                                ),
                            ),
                        )
                    ),
                    3,
                ),
                risk_multiplier=round(float(v02.get("risk_multiplier", 1.0)), 3) if strategy in {"v02", "v03"} else 1.0,
                htf_bias=str(htf.get("bias", "not_used")),
                htf_regime=str(htf.get("regime", "not_used")),
                spot_perp_divergence_12=None if spot_ctx.get("divergence_12") is None else round(float(spot_ctx["divergence_12"]), 6),
                spot_volume_ratio=None if spot_ctx.get("spot_volume_ratio") is None else round(float(spot_ctx["spot_volume_ratio"]), 6),
                long_score=round(long_score, 3),
                short_score=round(short_score, 3),
                delta=round(delta, 3),
                regime=str(result.get("regime")),
                reasons=list(result.get("reasons", [])),
                warnings=list(result.get("warnings", [])),
            )
        )
        i = exit_index + 1

    return summarize(
        rows,
        trades,
        decisions,
        skipped,
        min_trades=min_trades,
        min_expectancy_r=min_expectancy_r,
        min_winrate_pct=min_winrate_pct,
    )


def summarize(
    rows: list[dict[str, str]],
    trades: list[Trade],
    decisions: dict[str, int],
    skipped: dict[str, int],
    *,
    min_trades: int,
    min_expectancy_r: float,
    min_winrate_pct: float,
) -> dict[str, Any]:
    wins = [trade for trade in trades if trade.net_r > 0]
    losses = [trade for trade in trades if trade.net_r <= 0]
    net_values = [trade.net_r for trade in trades]
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    losing_streak = 0
    max_losing_streak = 0
    for value in net_values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
        if value <= 0:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        else:
            losing_streak = 0

    first_time = rows[0].get("time") if rows else None
    last_time = rows[-1].get("time") if rows else None
    trade_dicts = [asdict(trade) for trade in trades]
    winrate_pct = round(len(wins) / len(trades) * 100, 3) if trades else None
    expectancy_r = round(sum(net_values) / len(net_values), 6) if net_values else None
    gate_pass = bool(
        len(trades) >= min_trades
        and expectancy_r is not None
        and expectancy_r >= min_expectancy_r
        and winrate_pct is not None
        and winrate_pct >= min_winrate_pct
    )

    return {
        "generated_at": now_iso(),
        "engine": "MAX_CORE_LITE_BACKTEST",
        "engine_version": "0.1.0",
        "data": {
            "rows": len(rows),
            "first_time": first_time,
            "last_time": last_time,
        },
        "summary": {
            "trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "winrate_pct": winrate_pct,
            "expectancy_r": expectancy_r,
            "net_r_total": round(sum(net_values), 6) if net_values else 0.0,
            "avg_win_r": round(sum(t.net_r for t in wins) / len(wins), 6) if wins else None,
            "avg_loss_r": round(sum(t.net_r for t in losses) / len(losses), 6) if losses else None,
            "max_drawdown_r": round(max_drawdown, 6),
            "max_losing_streak": max_losing_streak,
        },
        "research_gate": {
            "pass": gate_pass,
            "min_trades": min_trades,
            "min_expectancy_r": min_expectancy_r,
            "min_winrate_pct": min_winrate_pct,
            "verdict": "candidate_for_paper_review" if gate_pass else "do_not_trade",
        },
        "decision_counts": decisions,
        "skipped": skipped,
        "trades": trade_dicts,
        "runtime_boundary": (
            "Research-only deterministic backtest. It proves code execution and exposes expectancy, "
            "but it is not live profitability proof and does not include order-book fills."
        ),
    }


def render_markdown(report: dict[str, Any], params: dict[str, Any]) -> str:
    summary = report["summary"]
    data = report["data"]
    gate = report.get("research_gate", {})
    lines = [
        "# MAX Core Lite Backtest Report",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Engine: `{report['engine']} {report['engine_version']}`",
        f"- Rows: `{data['rows']}`",
        f"- Period: `{data['first_time']}` -> `{data['last_time']}`",
        f"- Params: `{json.dumps(params, ensure_ascii=False)}`",
        "",
        "## Result",
        "",
        f"- Trades: `{summary['trades']}`",
        f"- Winrate: `{summary['winrate_pct']}`",
        f"- Expectancy R/trade: `{summary['expectancy_r']}`",
        f"- Total net R: `{summary['net_r_total']}`",
        f"- Avg win R: `{summary['avg_win_r']}`",
        f"- Avg loss R: `{summary['avg_loss_r']}`",
        f"- Max drawdown R: `{summary['max_drawdown_r']}`",
        f"- Max losing streak: `{summary['max_losing_streak']}`",
        f"- Research gate: **{gate.get('verdict')}**",
        "",
        "## Boundary",
        "",
        report["runtime_boundary"],
        "",
        "## Latest Trades",
        "",
    ]
    for trade in report["trades"][-20:]:
        lines.append(
            f"- `{trade['entry_time']}` {trade['side']} entry `{trade['entry']}` exit `{trade['exit']}` "
            f"netR `{trade['net_r']}` reason `{trade['exit_reason']}` delta `{trade['delta']}`"
        )
    lines.append("")
    return "\n".join(lines)


def aggregate_trade_dicts(trades: list[dict[str, Any]], *, min_trades: int, min_expectancy_r: float, min_winrate_pct: float) -> dict[str, Any]:
    wins = [trade for trade in trades if float(trade.get("net_r", 0.0)) > 0]
    losses = [trade for trade in trades if float(trade.get("net_r", 0.0)) <= 0]
    values = [float(trade.get("net_r", 0.0)) for trade in trades]
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    losing_streak = 0
    max_losing_streak = 0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
        if value <= 0:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        else:
            losing_streak = 0
    winrate = round(len(wins) / len(trades) * 100, 3) if trades else None
    expectancy = round(sum(values) / len(values), 6) if values else None
    pass_gate = bool(
        len(trades) >= min_trades
        and expectancy is not None
        and expectancy >= min_expectancy_r
        and winrate is not None
        and winrate >= min_winrate_pct
    )
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "winrate_pct": winrate,
        "expectancy_r": expectancy,
        "net_r_total": round(sum(values), 6) if values else 0.0,
        "avg_win_r": round(sum(float(t.get("net_r", 0.0)) for t in wins) / len(wins), 6) if wins else None,
        "avg_loss_r": round(sum(float(t.get("net_r", 0.0)) for t in losses) / len(losses), 6) if losses else None,
        "max_drawdown_r": round(max_drawdown, 6),
        "max_losing_streak": max_losing_streak,
        "gate_pass": pass_gate,
    }


def run_leaderboard(
    *,
    rows: list[dict[str, str]],
    oi_rows: list[dict[str, str]] | None,
    htf_rows: list[dict[str, str]] | None,
    spot_rows: list[dict[str, str]] | None,
    symbol: str,
    tf: str,
    htf_interval: str,
    warmup_bars: int,
    folds: int,
    fee_bps: float,
    slippage_bps: float,
    allow_price_only: bool,
    min_trades: int,
    min_expectancy_r: float,
    min_winrate_pct: float,
    out_prefix: Path,
) -> dict[str, Any]:
    candidates = [
        {"id": "score8", "strategy": "score", "entry_threshold": 8.0, "stop_atr": 1.0, "take_atr": 1.5, "max_hold_bars": 12},
        {"id": "score10", "strategy": "score", "entry_threshold": 10.0, "stop_atr": 1.0, "take_atr": 1.5, "max_hold_bars": 12},
        {"id": "v02", "strategy": "v02", "entry_threshold": 8.0, "stop_atr": 1.0, "take_atr": 1.5, "max_hold_bars": 12},
        {"id": "v03_htf", "strategy": "v03", "entry_threshold": 8.0, "stop_atr": 1.0, "take_atr": 1.5, "max_hold_bars": 12},
        {"id": "v03_htf_tp2", "strategy": "v03", "entry_threshold": 8.0, "stop_atr": 1.0, "take_atr": 2.0, "max_hold_bars": 16},
        {"id": "v04_trend_taker", "strategy": "v04_trend", "entry_threshold": 8.0, "stop_atr": 1.2, "take_atr": 2.0, "max_hold_bars": 18, "execution": "taker", "fee_bps": 5.0, "slippage_bps": 2.0},
        {"id": "v04_sweep_taker", "strategy": "v04_sweep", "entry_threshold": 8.0, "stop_atr": 0.9, "take_atr": 1.3, "max_hold_bars": 10, "execution": "taker", "fee_bps": 5.0, "slippage_bps": 2.0},
        {"id": "v04_range_maker", "strategy": "v04_range", "entry_threshold": 8.0, "stop_atr": 1.0, "take_atr": 1.15, "max_hold_bars": 12, "execution": "maker_proxy", "fee_bps": 2.0, "slippage_bps": 0.5},
        {"id": "v04_combo_maker", "strategy": "v04_combo", "entry_threshold": 8.0, "stop_atr": 1.0, "take_atr": 1.35, "max_hold_bars": 14, "execution": "maker_proxy", "fee_bps": 2.0, "slippage_bps": 0.5},
        {"id": "v05_spot_trend_taker", "strategy": "v05_spot_trend", "entry_threshold": 8.0, "stop_atr": 1.2, "take_atr": 2.0, "max_hold_bars": 18, "execution": "taker", "fee_bps": 5.0, "slippage_bps": 2.0},
        {"id": "v05_spot_sweep_taker", "strategy": "v05_spot_sweep", "entry_threshold": 8.0, "stop_atr": 0.9, "take_atr": 1.35, "max_hold_bars": 10, "execution": "taker", "fee_bps": 5.0, "slippage_bps": 2.0},
        {"id": "v05_spot_range_maker", "strategy": "v05_spot_range", "entry_threshold": 8.0, "stop_atr": 1.0, "take_atr": 1.15, "max_hold_bars": 12, "execution": "maker_proxy", "fee_bps": 2.0, "slippage_bps": 0.5},
        {"id": "v05_spot_combo_maker", "strategy": "v05_spot_combo", "entry_threshold": 8.0, "stop_atr": 1.0, "take_atr": 1.35, "max_hold_bars": 14, "execution": "maker_proxy", "fee_bps": 2.0, "slippage_bps": 0.5},
        {"id": "v08_mined_short_tp1_taker", "strategy": "v08_mined_short", "entry_threshold": 8.0, "stop_atr": 1.0, "take_atr": 1.0, "max_hold_bars": 12, "execution": "taker", "fee_bps": 5.0, "slippage_bps": 2.0},
        {"id": "v08_mined_short_tp15_taker", "strategy": "v08_mined_short", "entry_threshold": 8.0, "stop_atr": 1.0, "take_atr": 1.5, "max_hold_bars": 16, "execution": "taker", "fee_bps": 5.0, "slippage_bps": 2.0},
        {"id": "v10_15m_fade_short_taker", "strategy": "v10_15m_fade_short", "entry_threshold": 8.0, "stop_atr": 1.0, "take_atr": 1.2, "max_hold_bars": 16, "execution": "taker", "fee_bps": 5.0, "slippage_bps": 2.0},
        {"id": "v10_1h_weak_bid_short_taker", "strategy": "v10_1h_weak_bid_short", "entry_threshold": 8.0, "stop_atr": 1.0, "take_atr": 1.5, "max_hold_bars": 16, "execution": "taker", "fee_bps": 5.0, "slippage_bps": 2.0},
        {"id": "v10_4h_range_long_taker", "strategy": "v10_4h_range_long", "entry_threshold": 8.0, "stop_atr": 1.0, "take_atr": 1.4, "max_hold_bars": 10, "execution": "taker", "fee_bps": 5.0, "slippage_bps": 2.0},
    ]
    usable = max(0, len(rows) - warmup_bars)
    folds = max(1, min(folds, usable if usable else 1))
    fold_span = max(1, usable // folds) if usable else len(rows)
    report_candidates: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="max_core_lite_lb_") as temp_dir:
        temp_path = Path(temp_dir)
        for candidate in candidates:
            candidate_trades: list[dict[str, Any]] = []
            fold_reports: list[dict[str, Any]] = []
            for fold_idx in range(folds):
                test_start = warmup_bars + fold_idx * fold_span
                test_end = len(rows) if fold_idx == folds - 1 else min(len(rows), test_start + fold_span)
                if test_start >= len(rows) or test_end - test_start <= 5:
                    continue
                slice_start = max(0, test_start - warmup_bars)
                fold_rows = rows[slice_start:test_end]
                fold_oi_rows = oi_rows[slice_start:test_end] if oi_rows is not None else None
                fold_warmup = test_start - slice_start
                fold_report = simulate_backtest(
                    fold_rows,
                    symbol=symbol,
                    tf=tf,
                    warmup_bars=fold_warmup,
                    entry_threshold=float(candidate["entry_threshold"]),
                    stop_atr=float(candidate["stop_atr"]),
                    take_atr=float(candidate["take_atr"]),
                    max_hold_bars=int(candidate["max_hold_bars"]),
                    fee_bps=float(candidate.get("fee_bps", fee_bps)),
                    slippage_bps=float(candidate.get("slippage_bps", slippage_bps)),
                    allow_price_only=allow_price_only,
                    strategy=str(candidate["strategy"]),
                    invert_signal=False,
                    htf_rows=htf_rows,
                    htf_interval=htf_interval,
                    spot_rows=spot_rows,
                    spot_interval=tf,
                    window_csv=temp_path / f"{candidate['id']}_{fold_idx}_window.csv",
                    oi_rows=fold_oi_rows,
                    window_oi_csv=temp_path / f"{candidate['id']}_{fold_idx}_oi.csv" if fold_oi_rows is not None else None,
                    min_trades=1,
                    min_expectancy_r=min_expectancy_r,
                    min_winrate_pct=min_winrate_pct,
                )
                fold_summary = fold_report["summary"]
                fold_reports.append(
                    {
                        "fold": fold_idx + 1,
                        "row_start": test_start,
                        "row_end": test_end,
                        "trades": fold_summary["trades"],
                        "winrate_pct": fold_summary["winrate_pct"],
                        "expectancy_r": fold_summary["expectancy_r"],
                        "net_r_total": fold_summary["net_r_total"],
                    }
                )
                candidate_trades.extend(fold_report.get("trades", []))

            aggregate = aggregate_trade_dicts(
                candidate_trades,
                min_trades=min_trades,
                min_expectancy_r=min_expectancy_r,
                min_winrate_pct=min_winrate_pct,
            )
            stable_folds = [
                fold
                for fold in fold_reports
                if fold["trades"] > 0 and fold["expectancy_r"] is not None and float(fold["expectancy_r"]) >= min_expectancy_r
            ]
            promoted = bool(aggregate["gate_pass"] and len(stable_folds) == len(fold_reports) and fold_reports)
            report_candidates.append(
                {
                    "id": candidate["id"],
                    "params": candidate,
                    "aggregate": aggregate,
                    "folds": fold_reports,
                    "stable_folds": len(stable_folds),
                    "fold_count": len(fold_reports),
                    "promoted": promoted,
                    "verdict": "candidate_for_paper_review" if promoted else "do_not_trade",
                }
            )

    report_candidates.sort(
        key=lambda item: (
            1 if item["promoted"] else 0,
            float(item["aggregate"]["expectancy_r"] or -999),
            int(item["aggregate"]["trades"]),
        ),
        reverse=True,
    )
    report = {
        "generated_at": now_iso(),
        "engine": "MAX_CORE_LITE_LEADERBOARD",
        "engine_version": "1.0.0",
        "data": {
            "rows": len(rows),
            "oi_rows": len(oi_rows or []),
            "htf_rows": len(htf_rows or []),
            "spot_rows": len(spot_rows or []),
            "spot_perp_enabled": bool(spot_rows),
            "first_time": rows[0].get("time") if rows else None,
            "last_time": rows[-1].get("time") if rows else None,
            "folds": folds,
            "warmup_bars": warmup_bars,
        },
        "gate": {
            "min_trades": min_trades,
            "min_expectancy_r": min_expectancy_r,
            "min_winrate_pct": min_winrate_pct,
            "requires_all_folds_non_negative": True,
        },
        "candidates": report_candidates,
        "best": report_candidates[0] if report_candidates else None,
        "runtime_boundary": "Research-only walk-forward leaderboard. It is not live or paper approval unless a candidate is promoted.",
    }
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_leaderboard_markdown(report), encoding="utf-8")
    return report


def render_leaderboard_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MAX Core Lite v1.0 Leaderboard",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Rows: `{report['data']['rows']}`",
        f"- Period: `{report['data']['first_time']}` -> `{report['data']['last_time']}`",
        f"- Folds: `{report['data']['folds']}`",
        f"- Gate: `{json.dumps(report['gate'], ensure_ascii=False)}`",
        "",
        "## Candidates",
        "",
        "| Candidate | Trades | Winrate | Expectancy | Stable folds | Verdict |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in report["candidates"]:
        aggregate = item["aggregate"]
        lines.append(
            f"| `{item['id']}` | {aggregate['trades']} | {aggregate['winrate_pct']} | "
            f"{aggregate['expectancy_r']} | {item['stable_folds']}/{item['fold_count']} | {item['verdict']} |"
        )
    lines.extend(["", "## Boundary", "", report["runtime_boundary"], ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="MAX Core Lite research backtest runner")
    parser.add_argument("--ohlcv", help="Existing OHLCV CSV path. If omitted with --fetch-binance, data is fetched.")
    parser.add_argument("--fetch-binance", action="store_true", help="Fetch public Binance klines before backtest.")
    parser.add_argument("--fetch-derivatives", action="store_true", help="Fetch public futures Open Interest and funding and align them to klines.")
    parser.add_argument("--use-cache", action="store_true", help="Use data/cache Binance files when available before fetching public data.")
    parser.add_argument("--cache-dir", default="data/cache/binance", help="Repo-relative cache directory for --use-cache.")
    parser.add_argument("--market", choices=sorted(BINANCE_ENDPOINTS), default="futures")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--tf", default="15m")
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--pages", type=int, default=1, help="Backward pages to fetch. Each page is capped by Binance limits.")
    parser.add_argument("--derivatives-pages", type=int, help="Backward pages for OI/funding. Defaults to --pages.")
    parser.add_argument(
        "--strategy",
        choices=[
            "score",
            "v02",
            "v03",
            "v04_trend",
            "v04_sweep",
            "v04_range",
            "v04_combo",
            "v05_spot_trend",
            "v05_spot_sweep",
            "v05_spot_range",
            "v05_spot_combo",
            "v08_mined_short",
            "v10_15m_fade_short",
            "v10_1h_weak_bid_short",
            "v10_4h_range_long",
        ],
        default="score",
    )
    parser.add_argument("--htf-interval", default="4h", help="Higher timeframe interval used by strategy v03.")
    parser.add_argument("--htf-pages", type=int, help="Backward pages for HTF klines. Defaults to --pages.")
    parser.add_argument("--spot-pages", type=int, help="Backward pages for spot klines used by v05/leaderboard. Defaults to --pages.")
    parser.add_argument("--leaderboard", action="store_true", help="Run v0.5 walk-forward candidate leaderboard instead of one strategy.")
    parser.add_argument("--export-events", action="store_true", help="Run v0.6 labelled event export instead of a trade backtest.")
    parser.add_argument("--folds", type=int, default=4, help="Walk-forward fold count for --leaderboard.")
    parser.add_argument("--event-forward-bars", type=int, default=12, help="Forward bars used for v0.6 event labels.")
    parser.add_argument("--event-stride", type=int, default=1, help="Use every Nth bar for v0.6 event export.")
    parser.add_argument("--invert-signal", action="store_true", help="Research diagnostic only: swap LONG/SHORT after signal selection.")
    parser.add_argument("--out-prefix", default="_dl/max_backtest/BTCUSDT_15m")
    parser.add_argument("--save-data", help="Optional CSV path for fetched klines. Defaults to data/runtime/<symbol>_<interval>_klines.csv.")
    parser.add_argument("--save-oi", help="Optional CSV path for aligned OI/funding. Defaults to data/runtime/<symbol>_<interval>_oi.csv.")
    parser.add_argument("--warmup-bars", type=int, default=220)
    parser.add_argument("--entry-threshold", type=float, default=8.0)
    parser.add_argument("--stop-atr", type=float, default=1.0)
    parser.add_argument("--take-atr", type=float, default=1.5)
    parser.add_argument("--max-hold-bars", type=int, default=16)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--allow-price-only", action="store_true", help="Allow OHLCV-only research when OI/Basis are absent.")
    parser.add_argument("--min-trades", type=int, default=100)
    parser.add_argument("--min-expectancy-r", type=float, default=0.0)
    parser.add_argument("--min-winrate-pct", type=float, default=50.0)
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    if not cache_dir.is_absolute():
        cache_dir = ROOT / cache_dir

    if args.fetch_binance:
        rows: list[dict[str, str]] = []
        source = ""
        if args.use_cache:
            rows, cached_source = load_cached_klines(cache_dir, args.market, args.symbol, args.interval)
            source = cached_source or ""
        if not rows:
            rows = fetch_binance_klines(args.symbol, args.interval, args.limit, args.market, pages=args.pages)
            save_data = args.save_data or f"data/runtime/{args.symbol.upper()}_{args.interval}_klines.csv"
            save_path = ROOT / save_data
            write_ohlcv_csv(save_path, rows)
            source = str(save_path)
    elif args.ohlcv:
        source_path = Path(args.ohlcv)
        if not source_path.is_absolute():
            source_path = ROOT / source_path
        rows = read_ohlcv_csv(source_path)
        source = str(source_path)
    else:
        parser.error("Provide --ohlcv or --fetch-binance")

    htf_rows: list[dict[str, str]] | None = None
    htf_source: str | None = None
    if args.strategy == "v03" or args.strategy.startswith("v04_") or args.strategy.startswith("v05_") or args.strategy.startswith("v08_") or args.strategy.startswith("v10_") or args.leaderboard or args.export_events:
        if not args.fetch_binance:
            parser.error("--strategy v03/--leaderboard/--export-events currently requires --fetch-binance for HTF data")
        htf_pages = args.htf_pages or args.pages
        if args.use_cache:
            htf_rows, htf_source = load_cached_klines(cache_dir, args.market, args.symbol, args.htf_interval)
        if not htf_rows:
            htf_rows = fetch_binance_klines(args.symbol, args.htf_interval, args.limit, args.market, pages=htf_pages)
            htf_source = f"public_binance:{args.market}:{args.symbol.upper()}:{args.htf_interval}:pages={htf_pages}"

    spot_rows: list[dict[str, str]] | None = None
    spot_source: str | None = None
    if args.strategy.startswith("v05_") or args.strategy.startswith("v08_") or args.strategy.startswith("v10_") or args.leaderboard or args.export_events:
        if not args.fetch_binance:
            parser.error("--strategy v05/--leaderboard/--export-events requires --fetch-binance for spot/perp context")
        spot_pages = args.spot_pages or args.pages
        if args.use_cache:
            spot_rows, spot_source = load_cached_klines(cache_dir, "spot", args.symbol, args.interval)
        if not spot_rows:
            spot_rows = fetch_binance_klines(args.symbol, args.interval, args.limit, "spot", pages=spot_pages)
            spot_source = f"public_binance:spot:{args.symbol.upper()}:{args.interval}:pages={spot_pages}"

    oi_rows: list[dict[str, str]] | None = None
    oi_source: str | None = None
    if args.fetch_derivatives:
        if not args.fetch_binance or args.market != "futures":
            parser.error("--fetch-derivatives requires --fetch-binance --market futures")
        if args.use_cache:
            oi_rows, oi_source = load_cached_oi(cache_dir, args.symbol, args.interval)
        if not oi_rows:
            derivatives_pages = args.derivatives_pages or args.pages
            oi_records = fetch_open_interest_history(args.symbol, args.interval, args.limit, pages=derivatives_pages)
            funding_records = fetch_funding_history(args.symbol, pages=derivatives_pages)
            oi_rows = align_derivatives(rows, interval=args.interval, oi_records=oi_records, funding_records=funding_records)
            save_oi = args.save_oi or f"data/runtime/{args.symbol.upper()}_{args.interval}_oi.csv"
            oi_path = ROOT / save_oi
            write_oi_csv(oi_path, oi_rows)
            oi_source = str(oi_path)

    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    if args.export_events:
        report = run_event_export(
            rows=rows,
            oi_rows=oi_rows,
            htf_rows=htf_rows,
            spot_rows=spot_rows,
            symbol=args.symbol,
            tf=args.tf,
            htf_interval=args.htf_interval,
            warmup_bars=args.warmup_bars,
            event_forward_bars=args.event_forward_bars,
            event_stride=args.event_stride,
            out_prefix=out_prefix,
        )
        print(
            json.dumps(
                {
                    "csv": report["files"]["csv"],
                    "json": report["files"]["json"],
                    "md": report["files"]["md"],
                    "summary": report["summary"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.leaderboard:
        report = run_leaderboard(
            rows=rows,
            oi_rows=oi_rows,
            htf_rows=htf_rows,
            spot_rows=spot_rows,
            symbol=args.symbol,
            tf=args.tf,
            htf_interval=args.htf_interval,
            warmup_bars=args.warmup_bars,
            folds=args.folds,
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            allow_price_only=args.allow_price_only,
            min_trades=args.min_trades,
            min_expectancy_r=args.min_expectancy_r,
            min_winrate_pct=args.min_winrate_pct,
            out_prefix=out_prefix,
        )
        print(
            json.dumps(
                {
                    "json": str(out_prefix.with_suffix(".json")),
                    "md": str(out_prefix.with_suffix(".md")),
                    "best": report.get("best", {}).get("id") if isinstance(report.get("best"), dict) else None,
                    "best_summary": report.get("best", {}).get("aggregate") if isinstance(report.get("best"), dict) else None,
                    "best_verdict": report.get("best", {}).get("verdict") if isinstance(report.get("best"), dict) else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    with tempfile.TemporaryDirectory(prefix="max_core_lite_bt_") as temp_dir:
        temp_csv = Path(temp_dir) / "window.csv"
        temp_oi_csv = Path(temp_dir) / "window_oi.csv"
        report = simulate_backtest(
            rows,
            symbol=args.symbol,
            tf=args.tf,
            warmup_bars=args.warmup_bars,
            entry_threshold=args.entry_threshold,
            stop_atr=args.stop_atr,
            take_atr=args.take_atr,
            max_hold_bars=args.max_hold_bars,
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            allow_price_only=args.allow_price_only,
            strategy=args.strategy,
            invert_signal=args.invert_signal,
            htf_rows=htf_rows,
            htf_interval=args.htf_interval,
            spot_rows=spot_rows,
            spot_interval=args.interval,
            window_csv=temp_csv,
            oi_rows=oi_rows,
            window_oi_csv=temp_oi_csv if oi_rows is not None else None,
            min_trades=args.min_trades,
            min_expectancy_r=args.min_expectancy_r,
            min_winrate_pct=args.min_winrate_pct,
        )

    params = {
        "source": source,
        "oi_source": oi_source,
        "market": args.market if args.fetch_binance else "csv",
        "symbol": args.symbol,
        "tf": args.tf,
        "interval": args.interval,
        "pages": args.pages,
        "strategy": args.strategy,
        "htf_interval": args.htf_interval if args.strategy == "v03" or args.strategy.startswith("v04_") or args.strategy.startswith("v05_") or args.strategy.startswith("v08_") or args.strategy.startswith("v10_") else None,
        "htf_source": htf_source,
        "spot_source": spot_source,
        "invert_signal": args.invert_signal,
        "entry_threshold": args.entry_threshold,
        "stop_atr": args.stop_atr,
        "take_atr": args.take_atr,
        "max_hold_bars": args.max_hold_bars,
        "fee_bps": args.fee_bps,
        "slippage_bps": args.slippage_bps,
        "allow_price_only": args.allow_price_only,
        "use_cache": args.use_cache,
        "cache_dir": str(cache_dir),
        "min_trades": args.min_trades,
        "min_expectancy_r": args.min_expectancy_r,
        "min_winrate_pct": args.min_winrate_pct,
    }
    report["params"] = params

    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report, params), encoding="utf-8")
    print(
        json.dumps(
            {"json": str(json_path), "md": str(md_path), "summary": report["summary"], "research_gate": report["research_gate"]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

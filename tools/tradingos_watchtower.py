#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "tradingos.watchtower.v1"
CAPTURE_SCHEMA = "tradingos.binance_watchtower_capture.v1"
VERSION = "1.1.0"
TF_WEIGHTS = {"1h": 1, "4h": 2, "1d": 3}
TF_INTERVAL_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
EXPECTED_INTERVALS = tuple(TF_WEIGHTS)
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{5,20}$")
_ALLOWED_BIASES = {"WATCH_LONG", "WATCH_SHORT", "NO_ACTION"}
_ALLOWED_TF_STATES = {"LONG", "SHORT", "NEUTRAL"}


def finite(value: Any, field: str) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid number: {field}") from exc
    if not math.isfinite(n):
        raise ValueError(f"non-finite number: {field}")
    return n


def positive(value: Any, field: str) -> float:
    n = finite(value, field)
    if n <= 0:
        raise ValueError(f"non-positive number: {field}")
    return n


def nonnegative(value: Any, field: str) -> float:
    n = finite(value, field)
    if n < 0:
        raise ValueError(f"negative number: {field}")
    return n


def parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty string")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("invalid timestamp") from exc
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return dt.astimezone(timezone.utc)


def _stable_sha256(payload: Any) -> str:
    try:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("capture is not canonically serializable") from exc
    return hashlib.sha256(raw).hexdigest()


def _int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"invalid integer: {field}")
    try:
        out = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid integer: {field}") from exc
    return out


def ema(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"need at least {period} closes")
    a = 2.0 / (period + 1.0)
    out = values[0]
    for value in values[1:]:
        out = a * value + (1.0 - a) * out
    return out


def zscore(value: float, history: list[float]) -> float:
    if len(history) < 10:
        raise ValueError("z-score history too short")
    if any(not math.isfinite(x) for x in history):
        raise ValueError("z-score history contains non-finite value")
    sigma = statistics.pstdev(history)
    return 0.0 if sigma == 0 else (value - statistics.mean(history)) / sigma


def taker_direction(volume: float, buy: float) -> str:
    if volume < 0 or buy < 0 or buy > volume:
        raise ValueError("taker buy volume must be within total volume")
    net = 2.0 * buy - volume
    return "up" if net > 0 else "down" if net < 0 else "flat"


def _validate_kline_row(
    row: Any,
    field: str,
    index: int,
    interval_ms: int,
    *,
    signed_ohlc: bool = False,
) -> list[Any]:
    if not isinstance(row, (list, tuple)) or len(row) != 12:
        raise ValueError(f"{field}[{index}]: malformed kline; expected exactly 12 fields")
    open_ms = _int(row[0], f"{field}[{index}].open_time")
    close_ms = _int(row[6], f"{field}[{index}].close_time")
    if open_ms < 0 or close_ms < open_ms:
        raise ValueError(f"{field}[{index}]: invalid timestamps")
    if close_ms - open_ms + 1 != interval_ms:
        raise ValueError(f"{field}[{index}]: interval mismatch")

    ohlc_number = finite if signed_ohlc else positive
    open_px = ohlc_number(row[1], f"{field}[{index}].open")
    high = ohlc_number(row[2], f"{field}[{index}].high")
    low = ohlc_number(row[3], f"{field}[{index}].low")
    close = ohlc_number(row[4], f"{field}[{index}].close")
    if high < max(open_px, close) or low > min(open_px, close) or high < low:
        raise ValueError(f"{field}[{index}]: invalid OHLC envelope")

    volume = nonnegative(row[5], f"{field}[{index}].volume")
    taker_buy = nonnegative(row[9], f"{field}[{index}].taker_buy")
    if taker_buy > volume:
        raise ValueError(f"{field}[{index}]: taker buy exceeds total volume")
    return list(row)


def closed_rows(
    rows: Any,
    captured_ms: int,
    *,
    field: str,
    interval_ms: int,
    minimum: int = 22,
    signed_ohlc: bool = False,
) -> list[list[Any]]:
    if not isinstance(rows, list):
        raise ValueError(f"{field}: rows must be a list")
    validated = [
        _validate_kline_row(
            row,
            field,
            i,
            interval_ms,
            signed_ohlc=signed_ohlc,
        )
        for i, row in enumerate(rows)
    ]
    opens = [_int(row[0], f"{field}.open_time") for row in validated]
    closes = [_int(row[6], f"{field}.close_time") for row in validated]
    if any(opens[i] >= opens[i + 1] for i in range(len(opens) - 1)):
        raise ValueError(f"{field}: open times must be strictly increasing")
    if any(closes[i] >= closes[i + 1] for i in range(len(closes) - 1)):
        raise ValueError(f"{field}: close times must be strictly increasing")
    out = [row for row in validated if _int(row[6], f"{field}.close_time") <= captured_ms]
    if len(out) < minimum:
        raise ValueError(f"{field}: not enough closed bars: {len(out)} < {minimum}")
    return out


def timeframe_features(rows: Any, captured_ms: int, timeframe: str) -> dict[str, Any]:
    if timeframe not in TF_INTERVAL_MS:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    field = f"futures_klines.{timeframe}"
    rows_v = closed_rows(rows, captured_ms, field=field, interval_ms=TF_INTERVAL_MS[timeframe])
    closes = [positive(row[4], f"{field}.close") for row in rows_v]
    latest = rows_v[-1]
    last = closes[-1]
    fast, slow = ema(closes, 9), ema(closes, 21)
    trend = "up" if last > fast > slow else "down" if last < fast < slow else "range"

    true_ranges: list[float] = []
    prev = closes[0]
    for i, row in enumerate(rows_v[1:], start=1):
        high = positive(row[2], f"{field}[{i}].high")
        low = positive(row[3], f"{field}[{i}].low")
        close = positive(row[4], f"{field}[{i}].close")
        true_ranges.append(max(high - low, abs(high - prev), abs(low - prev)))
        prev = close
    atr_pct = statistics.mean(true_ranges[-14:]) / last * 100.0

    window = rows_v[-20:]
    support = min(positive(row[3], f"{field}.low") for row in window)
    resistance = max(positive(row[2], f"{field}.high") for row in window)
    denom = resistance - support
    range_position = 0.5 if denom <= 0 else (last - support) / denom

    volume = nonnegative(latest[5], f"{field}.volume")
    taker_buy = nonnegative(latest[9], f"{field}.taker_buy")
    prior_volumes = [nonnegative(row[5], f"{field}.prior_volume") for row in rows_v[-21:-1]]
    prior_mean = statistics.mean(prior_volumes)
    if prior_mean <= 0:
        raise ValueError(f"{field}: prior volume mean must be positive")
    relative_volume = volume / prior_mean
    change_pct = (last / closes[-2] - 1.0) * 100.0

    score = 0.0
    reasons: list[str] = []
    if trend == "up":
        score += 2.0
        reasons.append("EMA9>EMA21 with price above both")
    elif trend == "down":
        score -= 2.0
        reasons.append("EMA9<EMA21 with price below both")

    flow = taker_direction(volume, taker_buy)
    if flow == "up":
        score += 0.75
        reasons.append("perp taker flow up")
    elif flow == "down":
        score -= 0.75
        reasons.append("perp taker flow down")

    if relative_volume >= 1.2 and trend == "up":
        score += 0.5
        reasons.append("volume confirms up regime")
    elif relative_volume >= 1.2 and trend == "down":
        score -= 0.5
        reasons.append("volume confirms down regime")

    state = "LONG" if score >= 2.0 else "SHORT" if score <= -2.0 else "NEUTRAL"
    return {
        "state": state,
        "score": round(score, 3),
        "trend": trend,
        "last": round(last, 6),
        "ema9": round(fast, 6),
        "ema21": round(slow, 6),
        "change_pct": round(change_pct, 4),
        "atr_pct": round(atr_pct, 4),
        "support": round(support, 6),
        "resistance": round(resistance, 6),
        "range_position": round(max(0.0, min(1.0, range_position)), 4),
        "relative_volume": round(relative_volume, 4),
        "perp_taker_flow": flow,
        "reasons": reasons,
    }


def _require_symbol(container: dict[str, Any], expected: str, field: str) -> None:
    if container.get("symbol") != expected:
        raise ValueError(f"{field}.symbol: expected {expected}")


def _history_rates(rows: Any, field: str, captured_ms: int, symbol: str) -> list[float]:
    if not isinstance(rows, list) or len(rows) < 10:
        raise ValueError(f"{field}: at least 10 rows required")
    out: list[float] = []
    last_ts = -1
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or "fundingRate" not in row or "fundingTime" not in row:
            raise ValueError(f"{field}[{i}]: malformed funding row")
        _require_symbol(row, symbol, f"{field}[{i}]")
        ts = _int(row["fundingTime"], f"{field}[{i}].fundingTime")
        if ts < 0:
            raise ValueError(f"{field}[{i}].fundingTime: negative timestamp")
        if ts <= last_ts:
            raise ValueError(f"{field}: timestamps must be strictly increasing")
        last_ts = ts
        rate = finite(row["fundingRate"], f"{field}[{i}].fundingRate")
        if ts <= captured_ms:
            out.append(rate)
    if len(out) < 10:
        raise ValueError(f"{field}: at least 10 nonfuture rows required")
    return out


def _latest_oi_reference(rows: Any, captured_ms: int, symbol: str) -> float:
    field = f"{symbol}.open_interest_stats_4h"
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{field}: missing")
    eligible: list[tuple[int, float]] = []
    last_ts = -1
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"{field}[{i}]: malformed")
        _require_symbol(row, symbol, f"{field}[{i}]")
        ts = _int(row.get("timestamp"), f"{field}[{i}].timestamp")
        if ts <= last_ts:
            raise ValueError(f"{field}: timestamps must be strictly increasing")
        last_ts = ts
        value = positive(row.get("sumOpenInterest"), f"{field}[{i}].sumOpenInterest")
        if ts <= captured_ms:
            eligible.append((ts, value))
    if not eligible:
        raise ValueError(f"{field}: no observation at or before captured_at")
    return eligible[-1][1]


def _nonfuture_time(container: dict[str, Any], key: str, captured_ms: int, field: str) -> None:
    if key not in container:
        raise ValueError(f"{field}.{key}: timestamp missing")
    ts = _int(container[key], f"{field}.{key}")
    if ts < 0 or ts > captured_ms:
        raise ValueError(f"{field}.{key}: timestamp is after captured_at")


def asset_context(symbol: str, asset: Any, captured_ms: int) -> dict[str, Any]:
    if not isinstance(asset, dict):
        raise ValueError(f"{symbol}: asset must be an object")
    futures = asset.get("futures_klines")
    if not isinstance(futures, dict) or set(futures) != set(EXPECTED_INTERVALS):
        raise ValueError(f"{symbol}: futures_klines must exactly contain {EXPECTED_INTERVALS}")
    tfs = {tf: timeframe_features(futures[tf], captured_ms, tf) for tf in EXPECTED_INTERVALS}

    spot_rows = closed_rows(
        asset.get("spot_klines_4h"),
        captured_ms,
        field=f"{symbol}.spot_klines_4h",
        interval_ms=TF_INTERVAL_MS["4h"],
    )
    latest_spot = spot_rows[-1]
    spot_volume = nonnegative(latest_spot[5], f"{symbol}.spot.volume")
    spot_buy = nonnegative(latest_spot[9], f"{symbol}.spot.taker_buy")
    prior_spot_vols = [nonnegative(row[5], f"{symbol}.spot.prior_volume") for row in spot_rows[-21:-1]]
    prior_spot_mean = statistics.mean(prior_spot_vols)
    if prior_spot_mean <= 0:
        raise ValueError(f"{symbol}: prior spot volume mean must be positive")
    spot_flow = taker_direction(spot_volume, spot_buy)
    spot_rv = spot_volume / prior_spot_mean

    oi = asset.get("open_interest")
    if not isinstance(oi, dict):
        raise ValueError(f"{symbol}: open_interest missing")
    _require_symbol(oi, symbol, f"{symbol}.open_interest")
    _nonfuture_time(oi, "time", captured_ms, f"{symbol}.open_interest")
    oi_current = positive(oi.get("openInterest"), f"{symbol}.open_interest.openInterest")
    oi_ref = _latest_oi_reference(asset.get("open_interest_stats_4h"), captured_ms, symbol)
    oi_change = (oi_current / oi_ref - 1.0) * 100.0

    mark = asset.get("mark_price")
    if not isinstance(mark, dict):
        raise ValueError(f"{symbol}: mark_price missing")
    _require_symbol(mark, symbol, f"{symbol}.mark_price")
    _nonfuture_time(mark, "time", captured_ms, f"{symbol}.mark_price")
    mark_price = positive(mark.get("markPrice"), f"{symbol}.mark")
    index_price = positive(mark.get("indexPrice"), f"{symbol}.index")
    funding = finite(mark.get("lastFundingRate"), f"{symbol}.funding")
    funding_hist = _history_rates(asset.get("funding_history"), f"{symbol}.funding_history", captured_ms, symbol)
    funding_z = zscore(funding, funding_hist)

    basis = mark_price / index_price - 1.0
    premium_rows = closed_rows(
        asset.get("premium_index_4h"),
        captured_ms,
        field=f"{symbol}.premium_index_4h",
        interval_ms=TF_INTERVAL_MS["4h"],
        signed_ohlc=True,
    )
    premium_hist = [finite(row[4], f"{symbol}.premium_index_4h.close") for row in premium_rows]
    basis_z = zscore(basis, premium_hist)

    signs = {"LONG": 1, "SHORT": -1, "NEUTRAL": 0}
    weighted = sum(signs[tfs[tf]["state"]] * TF_WEIGHTS[tf] for tf in EXPECTED_INTERVALS)
    one_day = tfs["1d"]["state"]
    lower = {tfs["1h"]["state"], tfs["4h"]["state"]}
    conflict = None
    if one_day == "LONG" and "SHORT" in lower:
        conflict = "HTF_LTF_CONFLICT"
    elif one_day == "SHORT" and "LONG" in lower:
        conflict = "HTF_LTF_CONFLICT"
    elif one_day == "NEUTRAL" and {"LONG", "SHORT"}.issubset(lower):
        conflict = "LTF_SPLIT"

    bias = "NO_ACTION"
    if conflict is None and weighted >= 4:
        bias = "WATCH_LONG"
    elif conflict is None and weighted <= -4:
        bias = "WATCH_SHORT"

    four = tfs["4h"]
    last = four["last"]
    to_res = (four["resistance"] / last - 1.0) * 100.0
    to_sup = (last / four["support"] - 1.0) * 100.0
    proximity = min(abs(to_res), abs(to_sup))
    attention = abs(weighted) / 6.0 * 55.0
    if proximity <= 0.5:
        attention += 20.0
    elif proximity <= 1.0:
        attention += 10.0
    if abs(funding_z) >= 1.5:
        attention += 8.0
    if abs(basis_z) >= 1.5:
        attention += 7.0
    if conflict:
        attention += 10.0
    attention = min(100.0, attention)
    clarity = "CLEAR" if bias != "NO_ACTION" and conflict is None else "CONFLICT" if conflict else "MIXED"

    return {
        "symbol": symbol,
        "bias": bias,
        "clarity": clarity,
        "weighted_confluence": weighted,
        "confluence_normalized": round(weighted / 6.0, 4),
        "conflict": conflict,
        "timeframes": tfs,
        "derivatives": {
            "open_interest_change_pct": round(oi_change, 4),
            "funding_rate": round(funding, 8),
            "funding_z": round(funding_z, 4),
            "basis_pct": round(basis * 100.0, 5),
            "basis_z": round(basis_z, 4),
        },
        "spot_flow_4h": spot_flow,
        "spot_relative_volume_4h": round(spot_rv, 4),
        "distance_4h": {"to_support_pct": round(to_sup, 3), "to_resistance_pct": round(to_res, 3)},
        "attention_score": round(attention, 2),
        "can_trade": False,
    }


def _validate_capture(capture: Any) -> tuple[datetime, list[str], dict[str, Any]]:
    if not isinstance(capture, dict):
        raise ValueError("capture must be an object")
    if capture.get("schema") != CAPTURE_SCHEMA:
        raise ValueError("unsupported capture schema")
    if capture.get("credentials_used") is not False or capture.get("private_api_used") is not False:
        raise ValueError("capture must be credential-free and public")

    captured_at = parse_time(capture.get("captured_at"))
    intervals = capture.get("intervals")
    if not isinstance(intervals, list) or intervals != list(EXPECTED_INTERVALS):
        raise ValueError(f"intervals must exactly equal {list(EXPECTED_INTERVALS)}")

    raw_symbols = capture.get("symbols")
    if not isinstance(raw_symbols, list) or not raw_symbols:
        raise ValueError("symbols must be a non-empty list")
    if len(raw_symbols) > 20:
        raise ValueError("watchlist is limited to 20 symbols")
    symbols: list[str] = []
    for raw in raw_symbols:
        if not isinstance(raw, str) or not _SYMBOL_RE.fullmatch(raw) or not raw.endswith("USDT"):
            raise ValueError(f"unsupported symbol format: {raw}")
        if raw in symbols:
            raise ValueError(f"duplicate symbol: {raw}")
        symbols.append(raw)

    assets = capture.get("assets")
    if not isinstance(assets, dict) or set(assets) != set(symbols):
        raise ValueError("capture assets must exactly match symbols")
    return captured_at, symbols, assets


def build_watchtower(capture: dict[str, Any]) -> dict[str, Any]:
    captured_at, symbols, assets_map = _validate_capture(capture)
    captured_ms = int(captured_at.timestamp() * 1000)
    assets = [asset_context(symbol, assets_map[symbol], captured_ms) for symbol in symbols]
    assets.sort(key=lambda row: (-row["attention_score"], row["symbol"]))
    leaders = [row for row in assets if row["bias"] == "WATCH_LONG"]
    laggards = [row for row in assets if row["bias"] == "WATCH_SHORT"]
    cross = "ALIGNED" if len({row["bias"] for row in assets}) == 1 else "DIVERGENT"
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "captured_at": captured_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "symbols": [row["symbol"] for row in assets],
        "matrix": assets,
        "cross_asset": {
            "state": cross,
            "watch_long": [row["symbol"] for row in leaders],
            "watch_short": [row["symbol"] for row in laggards],
            "top_attention": assets[0]["symbol"],
        },
        "provenance": {
            "producer": "tools/tradingos_watchtower.py",
            "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "capture_sha256": _stable_sha256(capture),
            "contract": "offline closed-bars-only transform; EMA9/EMA21 + perp taker flow + relative volume; TF weights 1h=1,4h=2,1d=3; WATCH requires |weighted|>=4 and no HTF/LTF conflict; attention is urgency not trade quality",
        },
        "safety": {
            "read_only": True,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
            "capital_permission": "DENY",
        },
    }


def esc(value: Any) -> str:
    return html.escape(str(value))


def _validate_report(report: Any) -> None:
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        raise ValueError("unsupported watchtower schema")
    safety = report.get("safety")
    if (
        not isinstance(safety, dict)
        or safety.get("read_only") is not True
        or safety.get("signals_allowed") is not False
        or safety.get("orders_allowed") is not False
        or safety.get("can_trade") is not False
        or safety.get("capital_permission") != "DENY"
    ):
        raise ValueError("unsafe watchtower report")
    matrix = report.get("matrix")
    if not isinstance(matrix, list) or not matrix:
        raise ValueError("watchtower matrix missing")
    for row in matrix:
        if not isinstance(row, dict) or row.get("bias") not in _ALLOWED_BIASES:
            raise ValueError("invalid watchtower bias")
        tfs = row.get("timeframes")
        if not isinstance(tfs, dict) or set(tfs) != set(EXPECTED_INTERVALS):
            raise ValueError("invalid watchtower timeframes")
        if any(tfs[tf].get("state") not in _ALLOWED_TF_STATES for tf in EXPECTED_INTERVALS):
            raise ValueError("invalid timeframe state")


def render_html(report: dict[str, Any]) -> str:
    _validate_report(report)
    cards: list[str] = []
    for asset in report["matrix"]:
        cells = "".join(
            f'<div class="tf {asset["timeframes"][tf]["state"].lower()}"><small>{tf}</small><b>{asset["timeframes"][tf]["state"]}</b><i>{asset["timeframes"][tf]["score"]:+.2f}</i></div>'
            for tf in EXPECTED_INTERVALS
        )
        four = asset["timeframes"]["4h"]
        flags = [
            flag
            for flag in (
                asset["conflict"],
                "FUNDING_EXTREME" if abs(asset["derivatives"]["funding_z"]) >= 1.5 else None,
                "BASIS_EXTREME" if abs(asset["derivatives"]["basis_z"]) >= 1.5 else None,
            )
            if flag
        ]
        cards.append(
            f'<article><header><div><h2>{esc(asset["symbol"])}</h2><strong class="{asset["bias"].lower()}">{asset["bias"]}</strong></div><em>ATTN <b>{asset["attention_score"]:.0f}</b></em></header>'
            f'<section>{cells}</section><dl><div><dt>4h last</dt><dd>{four["last"]:,.2f}</dd></div><div><dt>to R</dt><dd>{asset["distance_4h"]["to_resistance_pct"]:.2f}%</dd></div>'
            f'<div><dt>OI Δ</dt><dd>{asset["derivatives"]["open_interest_change_pct"]:+.2f}%</dd></div><div><dt>fund z</dt><dd>{asset["derivatives"]["funding_z"]:+.2f}</dd></div>'
            f'<div><dt>basis z</dt><dd>{asset["derivatives"]["basis_z"]:+.2f}</dd></div><div><dt>spot</dt><dd>{esc(asset["spot_flow_4h"])}</dd></div></dl>'
            f'<p>{esc(" · ".join(flags) if flags else "no elevated deterministic veto")}</p></article>'
        )
    css = '*{box-sizing:border-box}body{margin:0;background:#071019;color:#f4f8fb;font:14px system-ui}main{max-width:1100px;margin:auto;padding:26px}nav{border-bottom:1px solid #263746;padding-bottom:16px}h1{font-size:54px;margin:3px 0;letter-spacing:-3px}nav span,dt,p{color:#8da4b7}article{background:#0d1823;border:1px solid #263746;border-radius:18px;padding:18px;margin:14px 0}header{display:flex;justify-content:space-between}h2{margin:0;font-size:28px}strong{font-size:12px}.watch_long,.long{color:#80f28b}.watch_short,.short{color:#ff7c7c}.no_action,.neutral{color:#ffc96b}em{font-style:normal;color:#8da4b7}em b{color:#f4f8fb;font-size:30px}section,dl{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:14px}.tf,dl div{background:#09141e;border:1px solid #263746;border-radius:11px;padding:10px}.tf b,.tf small,.tf i{display:block}.tf b{font-size:19px}.tf i{color:#8da4b7;font-style:normal}dl{grid-template-columns:repeat(6,1fr)}dt{font-size:10px}dd{margin:2px 0;font-weight:700}footer{color:#8da4b7;font-size:11px}@media(max-width:700px){h1{font-size:40px}dl{grid-template-columns:repeat(3,1fr)}}@media(max-width:450px){section,dl{grid-template-columns:1fr}}'
    return (
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>TradingOS Watchtower</title><style>{css}</style></head><body><main><nav><span>TRADINGOS · MULTI-ASSET WATCHTOWER</span>'
        f'<h1>Market Matrix</h1><span>{esc(report["captured_at"])} · urgency ≠ trade quality</span></nav>'
        f'<p>cross-asset {esc(report["cross_asset"]["state"])} · top attention {esc(report["cross_asset"]["top_attention"])} · 1h/4h/1d</p>'
        f'{"".join(cards)}<footer>Offline transform of closed public-market capture only · no credentials · no network fetch · no signals · no orders · can_trade=false · capital_permission=DENY.</footer></main></body></html>'
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the TradingOS multi-asset multi-timeframe Watchtower from an existing capture")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        capture = json.loads(args.input.read_text(encoding="utf-8-sig"))
        report = build_watchtower(capture)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        json_path = args.out_dir / "watchtower.json"
        html_path = args.out_dir / "watchtower.html"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        html_path.write_text(render_html(report), encoding="utf-8", newline="\n")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "result": "PASS",
                "top_attention": report["cross_asset"]["top_attention"],
                "outputs": {"json": str(json_path), "html": str(html_path)},
                "can_trade": False,
                "capital_permission": "DENY",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "tradingos.watchtower.v1"
CAPTURE_SCHEMA = "tradingos.binance_watchtower_capture.v1"
VERSION = "1.0.0"
TF_WEIGHTS = {"1h": 1, "4h": 2, "1d": 3}


def finite(value: Any, field: str) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid number: {field}") from exc
    if not math.isfinite(n):
        raise ValueError(f"non-finite number: {field}")
    return n


def parse_time(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return dt.astimezone(timezone.utc)


def ema(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"need at least {period} closes")
    a = 2.0 / (period + 1.0)
    out = values[0]
    for v in values[1:]:
        out = a * v + (1.0 - a) * out
    return out


def zscore(value: float, history: list[float]) -> float:
    if len(history) < 10:
        raise ValueError("z-score history too short")
    sigma = statistics.pstdev(history)
    return 0.0 if sigma == 0 else (value - statistics.mean(history)) / sigma


def taker_direction(volume: float, buy: float) -> str:
    net = 2.0 * buy - volume
    return "up" if net > 0 else "down" if net < 0 else "flat"


def closed_rows(rows: list[list[Any]], captured_ms: int, minimum: int = 22) -> list[list[Any]]:
    out = [row for row in rows if int(row[6]) <= captured_ms]
    if len(out) < minimum:
        raise ValueError(f"not enough closed bars: {len(out)} < {minimum}")
    return out


def timeframe_features(rows: list[list[Any]], captured_ms: int) -> dict[str, Any]:
    rows = closed_rows(rows, captured_ms)
    closes = [finite(r[4], "close") for r in rows]
    latest = rows[-1]
    last = closes[-1]
    fast, slow = ema(closes, 9), ema(closes, 21)
    trend = "up" if last > fast > slow else "down" if last < fast < slow else "range"
    trs: list[float] = []
    prev = closes[0]
    for r in rows[1:]:
        high, low, close = finite(r[2], "high"), finite(r[3], "low"), finite(r[4], "close")
        trs.append(max(high - low, abs(high - prev), abs(low - prev)))
        prev = close
    atr_pct = statistics.mean(trs[-14:]) / last * 100.0
    window = rows[-20:]
    support = min(finite(r[3], "low") for r in window)
    resistance = max(finite(r[2], "high") for r in window)
    denom = resistance - support
    range_position = 0.5 if denom <= 0 else (last - support) / denom
    volume = finite(latest[5], "volume")
    taker_buy = finite(latest[9], "taker_buy")
    prior_volumes = [finite(r[5], "prior_volume") for r in rows[-21:-1]]
    rel_volume = volume / statistics.mean(prior_volumes)
    change_pct = (last / closes[-2] - 1.0) * 100.0
    score = 0.0
    reasons: list[str] = []
    if trend == "up":
        score += 2.0; reasons.append("EMA9>EMA21 with price above both")
    elif trend == "down":
        score -= 2.0; reasons.append("EMA9<EMA21 with price below both")
    flow = taker_direction(volume, taker_buy)
    if flow == "up":
        score += 0.75; reasons.append("perp taker flow up")
    elif flow == "down":
        score -= 0.75; reasons.append("perp taker flow down")
    if rel_volume >= 1.2 and trend == "up":
        score += 0.5; reasons.append("volume confirms up regime")
    elif rel_volume >= 1.2 and trend == "down":
        score -= 0.5; reasons.append("volume confirms down regime")
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
        "relative_volume": round(rel_volume, 4),
        "perp_taker_flow": flow,
        "reasons": reasons,
    }


def asset_context(symbol: str, asset: dict[str, Any], captured_ms: int) -> dict[str, Any]:
    tfs = {tf: timeframe_features(asset["futures_klines"][tf], captured_ms) for tf in TF_WEIGHTS}
    spot_rows = closed_rows(asset["spot_klines_4h"], captured_ms)
    latest_spot = spot_rows[-1]
    spot_volume = finite(latest_spot[5], "spot.volume")
    spot_buy = finite(latest_spot[9], "spot.taker_buy")
    prior_spot_vols = [finite(r[5], "spot.prior_volume") for r in spot_rows[-21:-1]]
    spot_flow = taker_direction(spot_volume, spot_buy)
    spot_rv = spot_volume / statistics.mean(prior_spot_vols)

    oi_current = finite(asset["open_interest"]["openInterest"], "open_interest")
    oi_hist = asset["open_interest_stats_4h"]
    if not oi_hist:
        raise ValueError(f"{symbol}: open interest history missing")
    oi_ref = finite(oi_hist[-1]["sumOpenInterest"], "oi_reference")
    oi_change = (oi_current / oi_ref - 1.0) * 100.0
    mark = asset["mark_price"]
    mark_price = finite(mark["markPrice"], "mark")
    index_price = finite(mark["indexPrice"], "index")
    funding = finite(mark["lastFundingRate"], "funding")
    funding_hist = [finite(x["fundingRate"], "funding_hist") for x in asset["funding_history"]]
    funding_z = zscore(funding, funding_hist)
    basis = mark_price / index_price - 1.0
    premium_rows = closed_rows(asset["premium_index_4h"], captured_ms)
    premium_hist = [finite(r[4], "premium") for r in premium_rows]
    basis_z = zscore(basis, premium_hist)

    signs = {"LONG": 1, "SHORT": -1, "NEUTRAL": 0}
    weighted = sum(signs[tfs[tf]["state"]] * w for tf, w in TF_WEIGHTS.items())
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
    to_res = (four["resistance"] / last - 1.0) * 100.0 if last else 999.0
    to_sup = (last / four["support"] - 1.0) * 100.0 if four["support"] else 999.0
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


def build_watchtower(capture: dict[str, Any]) -> dict[str, Any]:
    if capture.get("schema") != CAPTURE_SCHEMA:
        raise ValueError("unsupported capture schema")
    if capture.get("credentials_used") is not False or capture.get("private_api_used") is not False:
        raise ValueError("capture must be credential-free and public")
    captured_at = parse_time(str(capture["captured_at"]))
    captured_ms = int(captured_at.timestamp() * 1000)
    assets = [asset_context(symbol, capture["assets"][symbol], captured_ms) for symbol in capture["symbols"]]
    assets.sort(key=lambda x: (-x["attention_score"], x["symbol"]))
    leaders = [a for a in assets if a["bias"] == "WATCH_LONG"]
    laggards = [a for a in assets if a["bias"] == "WATCH_SHORT"]
    cross = "ALIGNED" if assets and len({a["bias"] for a in assets}) == 1 else "DIVERGENT"
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "captured_at": capture["captured_at"],
        "symbols": [a["symbol"] for a in assets],
        "matrix": assets,
        "cross_asset": {
            "state": cross,
            "watch_long": [a["symbol"] for a in leaders],
            "watch_short": [a["symbol"] for a in laggards],
            "top_attention": assets[0]["symbol"] if assets else None,
        },
        "provenance": {
            "producer": "tools/tradingos_watchtower.py",
            "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "contract": "closed-bars-only; EMA9/EMA21 + perp taker flow + relative volume; TF weights 1h=1,4h=2,1d=3; WATCH requires |weighted|>=4 and no HTF/LTF conflict; attention is urgency not trade quality",
        },
        "safety": {"read_only": True, "signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"},
    }


def esc(v: Any) -> str:
    return html.escape(str(v))


def render_html(report: dict[str, Any]) -> str:
    cards=[]
    for a in report["matrix"]:
        cells="".join(f'<div class="tf {a["timeframes"][tf]["state"].lower()}"><small>{tf}</small><b>{a["timeframes"][tf]["state"]}</b><i>{a["timeframes"][tf]["score"]:+.2f}</i></div>' for tf in ("1h","4h","1d"))
        four=a["timeframes"]["4h"]
        flags=[x for x in (a["conflict"], "FUNDING_EXTREME" if abs(a["derivatives"]["funding_z"])>=1.5 else None, "BASIS_EXTREME" if abs(a["derivatives"]["basis_z"])>=1.5 else None) if x]
        cards.append(f'<article><header><div><h2>{esc(a["symbol"])}</h2><strong class="{a["bias"].lower()}">{a["bias"]}</strong></div><em>ATTN <b>{a["attention_score"]:.0f}</b></em></header><section>{cells}</section><dl><div><dt>4h last</dt><dd>{four["last"]:,.2f}</dd></div><div><dt>to R</dt><dd>{a["distance_4h"]["to_resistance_pct"]:.2f}%</dd></div><div><dt>OI Δ</dt><dd>{a["derivatives"]["open_interest_change_pct"]:+.2f}%</dd></div><div><dt>fund z</dt><dd>{a["derivatives"]["funding_z"]:+.2f}</dd></div><div><dt>basis z</dt><dd>{a["derivatives"]["basis_z"]:+.2f}</dd></div><div><dt>spot</dt><dd>{a["spot_flow_4h"]}</dd></div></dl><p>{esc(" · ".join(flags) if flags else "no elevated deterministic veto")}</p></article>')
    css='*{box-sizing:border-box}body{margin:0;background:#071019;color:#f4f8fb;font:14px system-ui}main{max-width:1100px;margin:auto;padding:26px}nav{border-bottom:1px solid #263746;padding-bottom:16px}h1{font-size:54px;margin:3px 0;letter-spacing:-3px}nav span,dt,p{color:#8da4b7}article{background:#0d1823;border:1px solid #263746;border-radius:18px;padding:18px;margin:14px 0}header{display:flex;justify-content:space-between}h2{margin:0;font-size:28px}strong{font-size:12px}.watch_long,.long{color:#80f28b}.watch_short,.short{color:#ff7c7c}.no_action,.neutral{color:#ffc96b}em{font-style:normal;color:#8da4b7}em b{color:#f4f8fb;font-size:30px}section,dl{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:14px}.tf,dl div{background:#09141e;border:1px solid #263746;border-radius:11px;padding:10px}.tf b,.tf small,.tf i{display:block}.tf b{font-size:19px}.tf i{color:#8da4b7;font-style:normal}dl{grid-template-columns:repeat(6,1fr)}dt{font-size:10px}dd{margin:2px 0;font-weight:700}footer{color:#8da4b7;font-size:11px}@media(max-width:700px){h1{font-size:40px}dl{grid-template-columns:repeat(3,1fr)}}@media(max-width:450px){section,dl{grid-template-columns:1fr}}'
    return f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TradingOS Watchtower</title><style>{css}</style></head><body><main><nav><span>TRADINGOS · MULTI-ASSET WATCHTOWER</span><h1>Market Matrix</h1><span>{esc(report["captured_at"])} · urgency ≠ trade quality</span></nav><p>cross-asset {esc(report["cross_asset"]["state"])} · top attention {esc(report["cross_asset"]["top_attention"])} · 1h/4h/1d</p>{"".join(cards)}<footer>Closed public Binance data only · no credentials · no signals · no orders · can_trade=false · capital_permission=DENY.</footer></main></body></html>'

def main() -> int:
    parser = argparse.ArgumentParser(description="Build the TradingOS multi-asset multi-timeframe Watchtower")
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
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(json.dumps({"result": "PASS", "top_attention": report["cross_asset"]["top_attention"], "outputs": {"json": str(json_path), "html": str(html_path)}, "can_trade": False, "capital_permission": "DENY"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

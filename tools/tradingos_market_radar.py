#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

WATCHTOWER_SCHEMA = "tradingos.watchtower.v1"
LIQUIDITY_SCHEMA = "tradingos.liquidity_lens.v1"
SCHEMA = "tradingos.market_radar.v1"
VERSION = "1.0.0"


def _index(rows: Any, field: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError(f"{field} must be a list")
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("symbol"), str):
            raise ValueError(f"{field}: invalid row")
        symbol = row["symbol"]
        if symbol in out:
            raise ValueError(f"{field}: duplicate symbol {symbol}")
        out[symbol] = row
    return out


def _safe(report: dict[str, Any], name: str) -> None:
    safety = report.get("safety")
    if not isinstance(safety, dict):
        raise ValueError(f"{name}: safety missing")
    if safety.get("can_trade") is not False or safety.get("orders_allowed") is not False or safety.get("signals_allowed") is not False:
        raise ValueError(f"{name}: unsafe permissions")


def _row(w: dict[str, Any], l: dict[str, Any] | None) -> dict[str, Any]:
    bias = str(w.get("bias", "NO_ACTION"))
    vetoes: list[str] = []
    notes: list[str] = []
    liquidity_state = "MISSING"
    liquidity_quality = "MISSING"
    liquidity_attention = 0.0
    spread_bps = None
    nearest_bid_wall = None
    nearest_ask_wall = None
    if l is None:
        notes.append("LIQUIDITY_CONTEXT_MISSING")
    else:
        liquidity_state = str(l.get("state", "UNKNOWN"))
        liquidity_quality = str(l.get("quality", "PARTIAL"))
        liquidity_attention = float(l.get("attention_score", 0.0))
        spread_bps = l.get("spread_bps")
        nearest_bid_wall, nearest_ask_wall = l.get("nearest_bid_wall"), l.get("nearest_ask_wall")
        if liquidity_quality != "PASS":
            notes.append("LIQUIDITY_CONTEXT_PARTIAL")
        if liquidity_quality == "PASS":
            if bias == "WATCH_LONG" and liquidity_state == "ASK_HEAVY":
                vetoes.append("MICROSTRUCTURE_OPPOSES_LONG")
            if bias == "WATCH_SHORT" and liquidity_state == "BID_HEAVY":
                vetoes.append("MICROSTRUCTURE_OPPOSES_SHORT")
        if bias == "WATCH_LONG" and "NEAR_ASK_WALL" in l.get("flags", []):
            vetoes.append("NEAR_ASK_WALL_FRICTION")
        if bias == "WATCH_SHORT" and "NEAR_BID_WALL" in l.get("flags", []):
            vetoes.append("NEAR_BID_WALL_FRICTION")

    base_attention = float(w.get("attention_score", 0.0))
    if l is not None and liquidity_quality == "PASS":
        priority = min(100.0, base_attention * 0.75 + liquidity_attention * 0.25 + (10.0 if vetoes else 0.0))
    else:
        priority = base_attention
    if bias == "NO_ACTION":
        quality = "BLOCKED_BY_CONFLUENCE" if w.get("conflict") else "NO_ACTION"
    elif vetoes:
        quality = "CAUTION"
    elif liquidity_quality == "PASS":
        quality = "CLEAR"
    else:
        quality = "CONTEXT_PARTIAL"
    tfs = w.get("timeframes", {})
    return {
        "symbol": w["symbol"],
        "bias": bias,
        "decision_quality": quality,
        "priority_score": round(priority, 2),
        "timeframes": {tf: tfs.get(tf, {}).get("state", "UNKNOWN") for tf in ("1h", "4h", "1d")},
        "confluence": w.get("weighted_confluence"),
        "watchtower_conflict": w.get("conflict"),
        "liquidity": {
            "quality": liquidity_quality,
            "state": liquidity_state,
            "spread_bps": spread_bps,
            "nearest_bid_wall": nearest_bid_wall,
            "nearest_ask_wall": nearest_ask_wall,
        },
        "vetoes": sorted(set(vetoes)),
        "notes": sorted(set(notes)),
        "can_trade": False,
    }


def build_radar(watchtower: dict[str, Any], liquidity: dict[str, Any]) -> dict[str, Any]:
    if watchtower.get("schema") != WATCHTOWER_SCHEMA:
        raise ValueError("unsupported watchtower schema")
    if liquidity.get("schema") != LIQUIDITY_SCHEMA:
        raise ValueError("unsupported liquidity schema")
    _safe(watchtower, "watchtower")
    _safe(liquidity, "liquidity")
    wrows, lrows = _index(watchtower.get("matrix"), "watchtower.matrix"), _index(liquidity.get("matrix"), "liquidity.matrix")
    if not wrows:
        raise ValueError("watchtower is empty")
    rows = [_row(wrows[s], lrows.get(s)) for s in wrows]
    rows.sort(key=lambda x: (-x["priority_score"], x["symbol"]))
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "watchtower_captured_at": watchtower.get("captured_at"),
        "liquidity_captured_at": liquidity.get("captured_at"),
        "matrix": rows,
        "top_priority": rows[0]["symbol"],
        "contract": {
            "watchtower_bias_is_authoritative": True,
            "liquidity_can_create_directional_bias": False,
            "liquidity_role": "attention + friction/veto context only",
            "priority_formula": "75% watchtower attention + 25% liquidity attention + 10 veto bump when liquidity coverage PASS; otherwise watchtower attention only",
        },
        "safety": {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"},
    }


def _esc(v: Any) -> str:
    return html.escape(str(v))


def render_html(report: dict[str, Any]) -> str:
    cards=[]
    for r in report["matrix"]:
        tf="".join(f'<span class="{r["timeframes"][x].lower()}"><small>{x}</small><b>{r["timeframes"][x]}</b></span>' for x in ("1h","4h","1d"))
        veto=" · ".join(r["vetoes"] or r["notes"] or ["no friction veto"])
        liq=r["liquidity"]
        cards.append(f'<article><header><div><h2>{_esc(r["symbol"])}</h2><strong>{_esc(r["bias"])}</strong></div><em>{r["priority_score"]:.0f}</em></header><div class="tfs">{tf}</div><div class="line"><span>QUALITY</span><b>{_esc(r["decision_quality"])}</b></div><div class="line"><span>LIQUIDITY</span><b>{_esc(liq["state"])} · {_esc(liq["quality"])}</b></div><footer>{_esc(veto)}</footer></article>')
    css='*{box-sizing:border-box}body{margin:0;background:#061019;color:#f5f8fb;font:14px system-ui}main{max-width:1100px;margin:auto;padding:26px}.k{color:#69d9ff;font-size:11px;letter-spacing:.16em;font-weight:800}h1{font-size:52px;margin:4px 0;letter-spacing:-3px}.sub,small,footer,.line span{color:#8da4b7}article{background:#0d1823;border:1px solid #263746;border-radius:18px;padding:18px;margin:14px 0}header{display:flex;justify-content:space-between}h2{margin:0;font-size:28px}strong{color:#dfeaf2}em{font-style:normal;font-size:32px;font-weight:800}.tfs{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:14px 0}.tfs span{padding:10px;background:#09141e;border:1px solid #263746;border-radius:10px}.tfs small,.tfs b{display:block}.long{color:#80f28b}.short{color:#ff7c7c}.neutral{color:#ffc96b}.line{display:flex;justify-content:space-between;border-top:1px solid #1c2b38;padding:10px 0}footer{margin-top:6px}@media(max-width:600px){h1{font-size:38px}}'
    return f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TradingOS Market Radar</title><style>{css}</style></head><body><main><div class="k">TRADINGOS · MARKET RADAR</div><h1>Scan → Context → Attention</h1><div class="sub">Liquidity may add friction/veto context but cannot create a directional bias.</div>{"".join(cards)}<div class="sub">signals=false · orders=false · can_trade=false · capital_permission=DENY</div></main></body></html>'


def main() -> int:
    p=argparse.ArgumentParser(description="Join TradingOS Watchtower and Liquidity Lens into a read-only Market Radar")
    p.add_argument("--watchtower",required=True,type=Path); p.add_argument("--liquidity",required=True,type=Path); p.add_argument("--out-dir",required=True,type=Path)
    a=p.parse_args()
    try:
        report=build_radar(json.loads(a.watchtower.read_text(encoding="utf-8-sig")),json.loads(a.liquidity.read_text(encoding="utf-8-sig")))
        a.out_dir.mkdir(parents=True,exist_ok=True); jp=a.out_dir/"market_radar.json"; hp=a.out_dir/"market_radar.html"
        jp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n"); hp.write_text(render_html(report),encoding="utf-8",newline="\n")
    except (OSError,ValueError,KeyError,json.JSONDecodeError) as exc:
        print(json.dumps({"result":"ERROR","error":str(exc),"can_trade":False},indent=2)); return 2
    print(json.dumps({"result":"PASS","top_priority":report["top_priority"],"outputs":{"json":str(jp),"html":str(hp)},"can_trade":False,"capital_permission":"DENY"},indent=2)); return 0

if __name__ == "__main__":
    raise SystemExit(main())

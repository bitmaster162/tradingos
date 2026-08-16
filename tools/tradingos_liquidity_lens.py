#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from tradingos_liquidity_lens_core import build_lens


def esc(value: Any) -> str:
    return html.escape(str(value))


def render_html(report: dict[str, Any]) -> str:
    if report.get("schema") != "tradingos.liquidity_lens.v1":
        raise ValueError("unsupported liquidity lens schema")
    safety = report.get("safety")
    if not isinstance(safety, dict) or safety.get("can_trade") is not False or safety.get("orders_allowed") is not False or safety.get("signals_allowed") is not False or safety.get("capital_permission") != "DENY":
        raise ValueError("unsafe liquidity lens report")

    cards: list[str] = []
    for row in report["matrix"]:
        bands = "".join(
            f'<div class="band"><small>{b} bps {"✓" if row["depth_bands_bps"][b]["coverage_complete"] else "PARTIAL"}</small><b>{row["depth_bands_bps"][b]["imbalance"]:+.2f}</b><i>${row["depth_bands_bps"][b]["bid_notional"]:,.0f} / ${row["depth_bands_bps"][b]["ask_notional"]:,.0f}</i></div>'
            for b in ("10", "25", "50")
        )
        bid, ask = row["nearest_bid_wall"], row["nearest_ask_wall"]
        bid_text = "none" if not bid else f'{bid["distance_bps"]:.1f} bps · ${bid["notional"]:,.0f}'
        ask_text = "none" if not ask else f'{ask["distance_bps"]:.1f} bps · ${ask["notional"]:,.0f}'
        composite = "n/a" if row["composite_imbalance"] is None else f'{row["composite_imbalance"]:+.2f}'
        flags = " · ".join(row["flags"]) if row["flags"] else "no elevated liquidity flag"
        cards.append(
            f'<article><header><div><h2>{esc(row["symbol"])}</h2><strong class="{row["state"].lower()}">{esc(row["state"])}</strong></div><em>ATTN <b>{row["attention_score"]:.0f}</b></em></header>'
            f'<div class="top"><div><small>MID</small><b>{row["mid"]:,.2f}</b></div><div><small>SPREAD</small><b>{row["spread_bps"]:.2f} bps</b></div><div><small>COMPOSITE</small><b>{composite}</b></div></div>'
            f'<section>{bands}</section><div class="walls"><p><span>BID WALL</span>{esc(bid_text)}</p><p><span>ASK WALL</span>{esc(ask_text)}</p></div><footer>{esc(flags)}</footer></article>'
        )
    css = '*{box-sizing:border-box}body{margin:0;background:#071019;color:#f4f8fb;font:14px system-ui}main{max-width:1100px;margin:auto;padding:26px}.k{color:#6fdbff;font-size:11px;letter-spacing:.16em;font-weight:800}h1{font-size:50px;margin:4px 0;letter-spacing:-3px}.sub,small,footer{color:#8da4b7}article{margin:14px 0;padding:18px;border:1px solid #263746;border-radius:18px;background:#0d1823}header{display:flex;justify-content:space-between}h2{margin:0;font-size:28px}strong{font-size:12px}.bid_heavy{color:#80f28b}.ask_heavy{color:#ff7c7c}.balanced{color:#ffc96b}.insufficient_depth_coverage{color:#ff9f6b}em{font-style:normal;color:#8da4b7}em b{color:#fff;font-size:28px}.top,section{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:14px}.top div,.band{padding:11px;border:1px solid #263746;border-radius:11px;background:#09141e}.top b,.band b,.band i{display:block}.top b,.band b{font-size:20px}.band i{font-style:normal;color:#8da4b7;font-size:11px}.walls{display:grid;grid-template-columns:1fr 1fr;gap:8px}.walls p{padding:10px;border-left:3px solid #263746;background:#09141e}.walls span{display:block;color:#8da4b7;font-size:10px}@media(max-width:600px){h1{font-size:38px}.top,section,.walls{grid-template-columns:1fr}}'
    return f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TradingOS Liquidity Lens</title><style>{css}</style></head><body><main><div class="k">TRADINGOS · LIQUIDITY LENS</div><h1>Visible Depth</h1><div class="sub">{esc(report["captured_at"])} · visible book snapshot, not liquidation map</div>{"".join(cards)}<div class="sub">Bands 10/25/50 bps · overall directional state requires complete coverage of all bands · wall ≥ 3× side median level notional · signals=false · orders=false · can_trade=false · capital_permission=DENY.</div></main></body></html>'


def main() -> int:
    parser = argparse.ArgumentParser(description="Build TradingOS Liquidity Lens from a provided public visible-order-book capture")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = build_lens(json.loads(args.input.read_text(encoding="utf-8-sig")))
        args.out_dir.mkdir(parents=True, exist_ok=True)
        jp, hp = args.out_dir / "liquidity_lens.json", args.out_dir / "liquidity_lens.html"
        jp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
        hp.write_text(render_html(report), encoding="utf-8", newline="\n")
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(json.dumps({"result": "PASS", "top_attention": report["top_attention"], "outputs": {"json": str(jp), "html": str(hp)}, "can_trade": False, "capital_permission": "DENY"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

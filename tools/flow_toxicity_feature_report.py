#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = Path("ops/btcusdt_binance_futures_bot/data")
DEFAULT_OUT_PREFIX = Path("docs/FLOW_TOXICITY_FEATURE_REPORT_2026-06-08")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) else out


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def payload_of(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    return payload if isinstance(payload, dict) else row


def latest_date_with_any(data_dir: Path, symbol: str) -> str | None:
    candidates: list[str] = []
    symbol_l = symbol.lower()
    for namespace in ("market", "public"):
        root = data_dir / namespace
        if not root.exists():
            continue
        for day_dir in root.iterdir():
            if not day_dir.is_dir():
                continue
            if any(path.name.startswith(symbol_l) and path.suffix == ".jsonl" for path in day_dir.iterdir() if path.is_file()):
                candidates.append(day_dir.name)
    return sorted(set(candidates))[-1] if candidates else None


def stream_paths(data_dir: Path, symbol: str, date: str) -> dict[str, Path]:
    symbol_l = symbol.lower()
    return {
        "agg_trade": data_dir / "market" / date / f"{symbol_l}_aggTrade.jsonl",
        "book_ticker": data_dir / "public" / date / f"{symbol_l}_bookTicker.jsonl",
        "depth": data_dir / "public" / date / f"{symbol_l}_localDepth20.jsonl",
        "rpi_depth": data_dir / "public" / date / f"{symbol_l}_localRPIDepth20.jsonl",
    }


def demo_rows() -> dict[str, list[dict[str, Any]]]:
    agg_rows = []
    for index in range(24):
        sell_aggressor = index < 17
        agg_rows.append(
            {
                "payload": {
                    "e": "aggTrade",
                    "E": 1_780_000_000_000 + index * 1000,
                    "p": str(65000 - index * 3),
                    "q": "0.40" if sell_aggressor else "0.16",
                    "m": sell_aggressor,
                }
            }
        )
    book_rows = [
        {"payload": {"b": "64990", "a": "65010", "B": "2.0", "A": "5.5"}},
        {"payload": {"b": "64980", "a": "65020", "B": "1.4", "A": "6.2"}},
    ]
    depth_rows = [
        {
            "payload": {
                "bids": [["64990", "2.0"], ["64980", "1.8"], ["64970", "1.0"]],
                "asks": [["65010", "5.5"], ["65020", "4.2"], ["65030", "2.0"]],
            }
        }
    ]
    return {"agg_trade": agg_rows, "book_ticker": book_rows, "depth": depth_rows, "rpi_depth": []}


def summarize_agg_trades(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buy_qty = 0.0
    sell_qty = 0.0
    buy_notional = 0.0
    sell_notional = 0.0
    for row in rows:
        payload = payload_of(row)
        qty = as_float(payload.get("q") or payload.get("quantity")) or 0.0
        price = as_float(payload.get("p") or payload.get("price")) or 0.0
        buyer_is_maker = bool(payload.get("m", False))
        if buyer_is_maker:
            sell_qty += qty
            sell_notional += qty * price
        else:
            buy_qty += qty
            buy_notional += qty * price
    total_qty = buy_qty + sell_qty
    total_notional = buy_notional + sell_notional
    imbalance = (buy_qty - sell_qty) / total_qty if total_qty > 0 else None
    return {
        "rows": len(rows),
        "buy_qty": round(buy_qty, 8),
        "sell_qty": round(sell_qty, 8),
        "total_qty": round(total_qty, 8),
        "buy_notional": round(buy_notional, 4),
        "sell_notional": round(sell_notional, 4),
        "total_notional": round(total_notional, 4),
        "aggressive_flow_imbalance": None if imbalance is None else round(imbalance, 6),
        "dominant_aggressor": "buy" if (imbalance or 0) > 0 else "sell" if (imbalance or 0) < 0 else "balanced",
    }


def summarize_book(rows: list[dict[str, Any]]) -> dict[str, Any]:
    spreads = []
    imbalances = []
    for row in rows:
        payload = payload_of(row)
        bid = as_float(payload.get("b") or payload.get("best_bid"))
        ask = as_float(payload.get("a") or payload.get("best_ask"))
        bid_qty = as_float(payload.get("B") or payload.get("bid_qty"))
        ask_qty = as_float(payload.get("A") or payload.get("ask_qty"))
        if bid and ask and bid > 0 and ask >= bid:
            mid = (bid + ask) / 2
            spreads.append((ask - bid) / mid * 10_000)
        if bid_qty is not None and ask_qty is not None and (bid_qty + ask_qty) > 0:
            imbalances.append((bid_qty - ask_qty) / (bid_qty + ask_qty))
    return {
        "rows": len(rows),
        "avg_spread_bps": round(sum(spreads) / len(spreads), 6) if spreads else None,
        "max_spread_bps": round(max(spreads), 6) if spreads else None,
        "avg_top_of_book_imbalance": round(sum(imbalances) / len(imbalances), 6) if imbalances else None,
    }


def levels(payload: dict[str, Any], *names: str) -> list[list[Any]]:
    for name in names:
        value = payload.get(name)
        if isinstance(value, list):
            return value
    return []


def summarize_depth(rows: list[dict[str, Any]], depth_levels: int) -> dict[str, Any]:
    imbalances = []
    bid_totals = []
    ask_totals = []
    for row in rows:
        payload = payload_of(row)
        bids = levels(payload, "bids", "b")[:depth_levels]
        asks = levels(payload, "asks", "a")[:depth_levels]
        bid_qty = sum(as_float(item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else None) or 0.0 for item in bids)
        ask_qty = sum(as_float(item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else None) or 0.0 for item in asks)
        if bid_qty + ask_qty > 0:
            imbalances.append((bid_qty - ask_qty) / (bid_qty + ask_qty))
            bid_totals.append(bid_qty)
            ask_totals.append(ask_qty)
    return {
        "rows": len(rows),
        "depth_levels": depth_levels,
        "avg_bid_depth_qty": round(sum(bid_totals) / len(bid_totals), 8) if bid_totals else None,
        "avg_ask_depth_qty": round(sum(ask_totals) / len(ask_totals), 8) if ask_totals else None,
        "avg_depth_imbalance": round(sum(imbalances) / len(imbalances), 6) if imbalances else None,
    }


def bounded_abs(value: float | None, cap: float) -> float:
    if value is None or cap <= 0:
        return 0.0
    return min(abs(value) / cap, 1.0)


def build_decision(agg: dict[str, Any], book: dict[str, Any], depth: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    flow = as_float(agg.get("aggressive_flow_imbalance"))
    top = as_float(book.get("avg_top_of_book_imbalance"))
    depth_imbalance = as_float(depth.get("avg_depth_imbalance"))
    spread = as_float(book.get("avg_spread_bps"))
    score = (
        0.45 * bounded_abs(flow, args.flow_imbalance_threshold)
        + 0.25 * bounded_abs(top, args.book_imbalance_threshold)
        + 0.20 * bounded_abs(depth_imbalance, args.depth_imbalance_threshold)
        + 0.10 * bounded_abs(spread, args.spread_bps_threshold)
    )
    flags = {
        "flow_imbalance_hot": abs(flow or 0.0) >= args.flow_imbalance_threshold,
        "top_of_book_imbalanced": abs(top or 0.0) >= args.book_imbalance_threshold,
        "depth_imbalanced": abs(depth_imbalance or 0.0) >= args.depth_imbalance_threshold,
        "spread_wide": (spread or 0.0) >= args.spread_bps_threshold,
    }
    if score >= 0.70:
        classification = "toxic_flow_risk"
    elif score >= 0.40:
        classification = "elevated_flow_risk"
    else:
        classification = "normal_or_insufficient_flow_evidence"
    if (flow or 0.0) > 0:
        bias = "buy_pressure"
    elif (flow or 0.0) < 0:
        bias = "sell_pressure"
    else:
        bias = "unknown_or_balanced"
    return {
        "toxicity_score_0_1": round(score, 6),
        "classification": classification,
        "dominant_flow_bias": bias,
        "flags": flags,
        "trading_use": "guard_only_no_entry_permission",
    }


def render_markdown(report: dict[str, Any]) -> str:
    decision = report["decision"]
    lines = [
        "# Flow Toxicity Feature Report",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Research-only feature report.",
        "- No orders, no credentials, no trade permission.",
        "- Intended as a future guard/filter for futures backtests and live-review cards.",
        "",
        "## Inputs",
        "",
        f"- Mode: `{report['mode']}`.",
        f"- Symbol: `{report['symbol']}`.",
        f"- Date: `{report['date']}`.",
        "",
        "## Result",
        "",
        f"- Toxicity score: `{decision['toxicity_score_0_1']}`.",
        f"- Classification: `{decision['classification']}`.",
        f"- Dominant flow bias: `{decision['dominant_flow_bias']}`.",
        "",
        "## Feature Summaries",
        "",
        f"- AggTrade: `{report['features']['agg_trade']}`",
        f"- BookTicker: `{report['features']['book_ticker']}`",
        f"- Depth: `{report['features']['depth']}`",
        "",
        "## Use Policy",
        "",
        "- Use as abstention/size-reduction evidence only.",
        "- Do not use as standalone entry signal.",
        "- Promote only after OOS/walk-forward validation on real collected streams.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Research-only order-flow toxicity feature report")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--date", default=None)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--depth-levels", type=int, default=20)
    parser.add_argument("--flow-imbalance-threshold", type=float, default=0.35)
    parser.add_argument("--book-imbalance-threshold", type=float, default=0.30)
    parser.add_argument("--depth-imbalance-threshold", type=float, default=0.25)
    parser.add_argument("--spread-bps-threshold", type=float, default=5.0)
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    args = parser.parse_args()

    data_dir = resolve_path(args.data_dir)
    if args.demo:
        rows = demo_rows()
        date = "demo"
        paths = {}
        mode = "demo_synthetic"
    else:
        date = args.date or latest_date_with_any(data_dir, args.symbol)
        if date is None:
            rows = {"agg_trade": [], "book_ticker": [], "depth": [], "rpi_depth": []}
            paths = {}
        else:
            paths = stream_paths(data_dir, args.symbol, date)
            rows = {
                "agg_trade": read_jsonl(paths["agg_trade"]),
                "book_ticker": read_jsonl(paths["book_ticker"]),
                "depth": read_jsonl(paths["depth"]),
                "rpi_depth": read_jsonl(paths["rpi_depth"]),
            }
        mode = "local_jsonl"
    depth_rows = rows["rpi_depth"] or rows["depth"]
    features = {
        "agg_trade": summarize_agg_trades(rows["agg_trade"]),
        "book_ticker": summarize_book(rows["book_ticker"]),
        "depth": summarize_depth(depth_rows, args.depth_levels),
    }
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "research_flow_toxicity_feature_report_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "mode": mode,
        "symbol": args.symbol,
        "date": date,
        "data_dir": str(data_dir),
        "paths": {key: str(value) for key, value in paths.items()},
        "thresholds": {
            "flow_imbalance_threshold": args.flow_imbalance_threshold,
            "book_imbalance_threshold": args.book_imbalance_threshold,
            "depth_imbalance_threshold": args.depth_imbalance_threshold,
            "spread_bps_threshold": args.spread_bps_threshold,
        },
        "features": features,
        "decision": build_decision(features["agg_trade"], features["book_ticker"], features["depth"], args),
        "can_trade": False,
    }
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "classification": report["decision"]["classification"],
                "toxicity_score_0_1": report["decision"]["toxicity_score_0_1"],
                "dominant_flow_bias": report["decision"]["dominant_flow_bias"],
                "json": str(json_path),
                "md": str(md_path),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

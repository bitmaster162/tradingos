#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

cap = load("r94_bench_cap", ROOT / "tools" / "r6_execution_capacity_gate.py")
r84 = load("r94_bench_r84", ROOT / "tools" / "r6_all16_admission_probe.py")
SCHEMA = "tradingos.r94_full_equity_capacity_benchmark.v1"


async def run(symbols: list[str]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        rules = await asyncio.to_thread(cap.fetch_symbol_rules, symbol)
        quote, book = await cap.capture_quote_and_capacity(symbol)
        result = cap.benchmark_full_equity_capacity(quote, book, rules)
        rows.append({
            "symbol": symbol,
            "quote_event_time_ms": quote["event_time_ms"],
            "quote_decision_time_ms": quote["decision_time_ms"],
            "quote_age_ms": quote["decision_age_ms"],
            "restart_count": quote.get("restart_count", 0),
            "captured_bid_notional": book["captured_bid_notional"],
            "captured_ask_notional": book["captured_ask_notional"],
            "result": result,
        })
    return {
        "schema": SCHEMA,
        "benchmark_notional_usdt": str(cap.RESEARCH_EQUITY_USDT),
        "symbol_count": len(rows),
        "pass_count": sum(r["result"].get("status") == "PASS" for r in rows),
        "fail_count": sum(r["result"].get("status") != "PASS" for r in rows),
        "results": rows,
        "can_trade": False,
        "capital_permission": "DENY",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run R94 full-equity depth-capacity benchmark")
    parser.add_argument("--all16", action="store_true")
    parser.add_argument("--symbol", action="append", dest="symbols")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    symbols = list(r84.ALL16) if args.all16 else (args.symbols or ["BTCUSDT"])
    try:
        result = asyncio.run(run(symbols))
        text = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if result["fail_count"] == 0 else 3
    except Exception as exc:
        print(json.dumps({
            "schema": SCHEMA,
            "result": "BENCHMARK_FAIL_CLOSED",
            "error": str(exc),
            "can_trade": False,
            "capital_permission": "DENY",
        }, sort_keys=True, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

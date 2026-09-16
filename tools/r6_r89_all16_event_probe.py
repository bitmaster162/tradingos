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


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


r84 = _load("r84_probe_live", ROOT / "tools" / "r6_all16_admission_probe.py")
r89 = _load("r89_contract_live", ROOT / "tools" / "r6_event_definition_contract.py")
SCHEMA = "tradingos.r89_all16_event_probe.v1"


def inspect_symbol(evidence: dict[str, Any]) -> dict[str, Any]:
    symbol = evidence["symbol"]
    decision_ms = int(evidence["quote"]["decision_time_ms"])
    raw = r84.r83.fetch_klines(symbol, "15m", 180)
    closed = [row for row in raw if int(row[6]) < decision_ms]
    try:
        bars = r89._bars(closed, decision_ms)
        ctha = r89._ctha_assessment(evidence, decision_ms)
        seq = r89._find_sequence(bars, decision_ms)
        targets = r89._targets(evidence, r89.dec(evidence["quote"]["ask"], "ask"), decision_ms)
    except Exception as exc:
        return {"symbol": symbol, "status": "NO_EVENT_FAIL_CLOSED", "error": str(exc),
                "quote_age_ms": evidence["quote"].get("decision_age_ms"),
                "restart_count": evidence["quote"].get("restart_count")}
    if seq is None:
        return {"symbol": symbol, "status": "NO_COMPLETE_SEQUENCE",
                "ctha_bias": ctha["bias"], "target_count": len(targets),
                "quote_age_ms": evidence["quote"].get("decision_age_ms"),
                "restart_count": evidence["quote"].get("restart_count")}
    return {"symbol": symbol, "status": "R89_SEQUENCE_DETECTED",
            "ctha_bias": ctha["bias"], "target_count": len(targets),
            "sweep_close_ms": bars[seq["sweep_i"]]["close_time_ms"],
            "bos_close_ms": bars[seq["bos_i"]]["close_time_ms"],
            "invalidation": str(bars[seq["sweep_i"]]["low"]),
            "quote_age_ms": evidence["quote"].get("decision_age_ms"),
            "restart_count": evidence["quote"].get("restart_count")}


async def run(symbols: list[str]) -> dict[str, Any]:
    snapshot = await r84.run_probe(symbols, max_age_ms=2000)
    results = [inspect_symbol(e) for e in snapshot["evidence"]]
    ages = [int(x["quote_age_ms"]) for x in results if isinstance(x.get("quote_age_ms"), int)]
    return {"schema": SCHEMA, "contract_id": r89.CONTRACT_ID,
            "snapshot_observed_at_ms": snapshot["observed_at_ms"],
            "symbol_count": len(results),
            "sequence_count": sum(x["status"] == "R89_SEQUENCE_DETECTED" for x in results),
            "fail_closed_count": sum(x["status"] == "NO_EVENT_FAIL_CLOSED" for x in results),
            "quote_age_ms": {"min": min(ages) if ages else None, "max": max(ages) if ages else None},
            "restart_sum": sum(int(x.get("restart_count") or 0) for x in results),
            "results": results, "ledger_write_authority": False,
            "can_trade": False, "capital_permission": "DENY"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", action="append", dest="symbols")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    symbols = args.symbols or list(r84.ALL16)
    payload = asyncio.run(run(symbols))
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

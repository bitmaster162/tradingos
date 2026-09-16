#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

r92 = load("r93_r92", ROOT / "tools" / "r6_symmetric_sweep_orchestrator.py")
gate = load("r93_gate", ROOT / "tools" / "r6_market_integrity_gate.py")
SCHEMA = "tradingos.r93_integrity_symmetric_sweep.v1"
VERSION = "R93_INTEGRITY_SYMMETRIC_SWEEP_V1_20260916"
def fail(reason: str, **details: Any) -> dict[str, Any]:
    return {"schema": SCHEMA, "version": VERSION, "result": "SWEEP_FAIL_CLOSED",
            "reason": reason, "details": details, "registration_candidates": [],
            "ledger_write_authority": False, "can_trade": False,
            "capital_permission": "DENY"}


def process_snapshot(snapshot: dict[str, Any], runtime_state: dict[str, Any],
                     references: dict[str, dict[str, Any] | None],
                     fetch_m15: Callable[[str, int], list[list[Any]]] = r92._bars) -> dict[str, Any]:
    evidence_raw = snapshot.get("evidence")
    if snapshot.get("schema") != r92.base.r84.SCHEMA or not isinstance(evidence_raw, list):
        return fail("R84_SNAPSHOT_INVALID")
    passed: list[dict[str, Any]] = []
    integrity_rows: list[dict[str, Any]] = []
    for evidence in evidence_raw:
        symbol = evidence.get("symbol") if isinstance(evidence, dict) else None
        reference = references.get(symbol) if isinstance(symbol, str) else None
        if not isinstance(reference, dict):
            integ = gate.fail("REFERENCE_FETCH_FAILED", symbol=symbol)
        else:
            integ = gate.evaluate(evidence.get("quote", {}), reference,
                                  final_time_ms=reference.get("integrity_final_time_ms", evidence.get("quote", {}).get("decision_time_ms")))
        integrity_rows.append({"symbol": symbol, "integrity": integ})
        if integ.get("status") == "PASS":
            enriched = copy.deepcopy(evidence)
            enriched["market_integrity"] = integ
            passed.append(enriched)
    passed_snapshot = {**snapshot, "evidence": passed,
                       "requested_symbols": [x["symbol"] for x in passed]}
    downstream = r92.process_snapshot(passed_snapshot, runtime_state, fetch_m15=fetch_m15)
    by_symbol = {x.get("symbol"): x for x in downstream.get("results", [])}
    merged: list[dict[str, Any]] = []
    for row in integrity_rows:
        symbol = row["symbol"]
        integ = row["integrity"]
        if integ.get("status") != "PASS":
            merged.append({"symbol": symbol, "status": "MARKET_INTEGRITY_FAIL_CLOSED",
                           "stage": "R93_MARKET_INTEGRITY", "market_integrity": integ,
                           "long_candidate": False, "short_candidate": False,
                           "registration_candidate": False})
            continue
        item = copy.deepcopy(by_symbol.get(symbol, {}))
        item["market_integrity"] = integ
        merged.append(item)
    return {"schema": SCHEMA, "version": VERSION, "result": "SWEEP_COMPLETE",
            "symbol_count": len(merged),
            "integrity_pass_count": sum(x["integrity"].get("status") == "PASS" for x in integrity_rows),
            "integrity_fail_count": sum(x["integrity"].get("status") != "PASS" for x in integrity_rows),
            "long_candidate_count": downstream.get("long_candidate_count", 0),
            "short_candidate_count": downstream.get("short_candidate_count", 0),
            "registration_candidate_count": downstream.get("registration_candidate_count", 0),
            "results": merged, "registration_candidates": downstream.get("registration_candidates", []),
            "intra_sweep_risk_reservation": True, "sole_resolver_handoff_required": bool(downstream.get("registration_candidates")),
            "ledger_write_authority": False, "can_trade": False, "capital_permission": "DENY"}
async def capture_snapshot(symbols: list[str], max_age_ms: int = 2000) -> tuple[dict[str, Any], dict[str, dict[str, Any] | None]]:
    bad = [s for s in symbols if s not in r92.base.r84.ALL16]
    if bad or len(set(symbols)) != len(symbols):
        raise ValueError("requested_symbols_invalid")
    refs_ctha = {"BTCUSDT": r92.base.r84.fetch_ctha("BTCUSDT"),
                 "ETHUSDT": r92.base.r84.fetch_ctha("ETHUSDT")}
    evidence: list[dict[str, Any]] = []
    refs_market: dict[str, dict[str, Any] | None] = {}
    for symbol in symbols:
        if symbol not in refs_ctha:
            refs_ctha[symbol] = r92.base.r84.fetch_ctha(symbol)
        try:
            reference = await asyncio.to_thread(gate.fetch_bybit_orderbook, symbol)
        except Exception:
            reference = None
        item = await r92.base.r84.capture_symbol(symbol, refs_ctha, max_age_ms=max_age_ms)
        if isinstance(reference, dict):
            final = item["quote"]["decision_time_ms"]
            integ = gate.evaluate(item["quote"], reference, final_time_ms=final)
            if integ.get("details", {}).get("error") == "reference_stale":
                reference = await asyncio.to_thread(gate.fetch_bybit_orderbook, symbol)
                final = reference["received_at_ms"]
                integ = gate.evaluate(item["quote"], reference, final_time_ms=final)
            if integ.get("details", {}).get("error") == "quote_stale_at_decision":
                item = await r92.base.r84.capture_symbol(symbol, refs_ctha, max_age_ms=max_age_ms)
                final = item["quote"]["decision_time_ms"]
                integ = gate.evaluate(item["quote"], reference, final_time_ms=final)
            reference["integrity_final_time_ms"] = final
        evidence.append(item)
        refs_market[symbol] = reference
    snapshot = {"schema": r92.base.r84.SCHEMA,
                "universe": list(r92.base.r84.ALL16), "requested_symbols": symbols,
                "evidence": evidence, "scored_admission_decision": "NOT_COMPUTED_BY_PROBE",
                "can_trade": False, "capital_permission": "DENY"}
    return snapshot, refs_market


async def run_sweep(symbols: list[str], runtime_state: dict[str, Any]) -> dict[str, Any]:
    snapshot, refs_market = await capture_snapshot(symbols)
    return process_snapshot(snapshot, runtime_state, refs_market)
def main() -> int:
    parser = argparse.ArgumentParser(description="Run R93 cross-venue integrity + R92 symmetric sweep")
    parser.add_argument("--runtime-state", required=True, type=Path)
    parser.add_argument("--symbol", action="append", dest="symbols")
    parser.add_argument("--all16", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        state = json.loads(args.runtime_state.read_text(encoding="utf-8"))
        symbols = list(r92.base.r84.ALL16) if args.all16 else (args.symbols or ["BTCUSDT"])
        result = asyncio.run(run_sweep(symbols, state))
        text = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if result.get("result") == "SWEEP_COMPLETE" else 3
    except Exception as exc:
        print(json.dumps(fail("R93_RUNTIME_ERROR", error=str(exc)), sort_keys=True, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

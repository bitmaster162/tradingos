#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import copy
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]

def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

base = _load("r92_base_r90", ROOT / "tools" / "r6_prospective_sweep_orchestrator.py")
short = _load("r92_short_contract", ROOT / "tools" / "r6_short_event_contract.py")
short_reg = _load("r92_short_reg", ROOT / "tools" / "r6_short_registration_adapter.py")

OUTPUT_SCHEMA = "tradingos.r92_symmetric_sweep.v1"
ORCHESTRATOR_VERSION = "R92_SYMMETRIC_SWEEP_ORCHESTRATOR_V1_20260916"


def stable_json(v: Any) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), allow_nan=False)

def fail(reason: str, **details: Any) -> dict[str, Any]:
    return {"schema": OUTPUT_SCHEMA, "result": "SWEEP_FAIL_CLOSED", "reason": reason,
            "details": details, "registration_candidates": [], "ledger_write_authority": False,
            "can_trade": False, "capital_permission": "DENY"}


def _reserve_risk(state: dict[str, Any], symbol: str) -> None:
    risk_by_symbol = state.get("risk_state_by_symbol")
    risk = risk_by_symbol.get(symbol) if isinstance(risk_by_symbol, dict) else None
    if risk is None:
        risk = state.get("risk_state")
    if not isinstance(risk, dict):
        raise ValueError("risk_state_missing")
    proposed = Decimal(str(risk["proposed_risk_pct"]))
    aggregate = Decimal(str(risk["aggregate_open_risk_pct"]))
    risk["aggregate_open_risk_pct"] = str(aggregate + proposed)
    risk["entries_today"] = int(risk.get("entries_today", 0)) + 1


def _bars(symbol: str, decision_ms: int) -> list[list[Any]]:
    return base._m15(symbol, decision_ms)


def _result(symbol: str, evidence: dict[str, Any]) -> dict[str, Any]:
    q = evidence.get("quote", {})
    return {"symbol": symbol, "quote_decision_time_ms": q.get("decision_time_ms"),
            "quote_age_ms": q.get("decision_age_ms"), "restart_count": q.get("restart_count"),
            "long_candidate": False, "short_candidate": False,
            "registration_candidate": False}

def process_snapshot(snapshot: dict[str, Any], runtime_state: dict[str, Any],
                     fetch_m15: Callable[[str, int], list[list[Any]]] = _bars) -> dict[str, Any]:
    try:
        state = copy.deepcopy(base._state(runtime_state))
    except Exception as exc:
        return fail("RUNTIME_STATE_INVALID", error=str(exc))
    if snapshot.get("schema") != base.r84.SCHEMA or not isinstance(snapshot.get("evidence"), list):
        return fail("R84_SNAPSHOT_INVALID")
    local_ledger = copy.deepcopy(state["ledger_state"])
    if not isinstance(local_ledger.get("existing_event_ids"), list):
        return fail("LEDGER_EVENT_IDS_INVALID")
    results = []
    candidates = []
    seen: set[str] = set()
    for evidence in snapshot["evidence"]:
        symbol = evidence.get("symbol") if isinstance(evidence, dict) else None
        if symbol not in base.r84.ALL16 or symbol in seen:
            return fail("SNAPSHOT_SYMBOL_SET_INVALID", symbol=symbol)
        seen.add(symbol)
        item = _result(symbol, evidence)
        decision_ms = evidence.get("quote", {}).get("decision_time_ms")
        if type(decision_ms) is not int or decision_ms <= 0:
            item.update(status="NO_CANDIDATE_FAIL_CLOSED", stage="R84", reason="DECISION_TIME_INVALID")
            results.append(item)
            continue
        try:
            bars = fetch_m15(symbol, decision_ms)
        except Exception as exc:
            item.update(status="NO_CANDIDATE_FAIL_CLOSED", stage="M15_DATA", reason=str(exc))
            results.append(item)
            continue
        long_ctha_ok = short_ctha_ok = False
        long_ctha_reason = short_ctha_reason = None
        try:
            base.r89._ctha_assessment(evidence, decision_ms)
            long_ctha_ok = True
        except Exception as exc:
            long_ctha_reason = str(exc)
        try:
            short._ctha_assessment(evidence, decision_ms)
            short_ctha_ok = True
        except Exception as exc:
            short_ctha_reason = str(exc)
        item["long_ctha_pass"] = long_ctha_ok
        item["short_ctha_pass"] = short_ctha_ok
        if not long_ctha_ok and not short_ctha_ok:
            item.update(status="NO_CANDIDATE_FAIL_CLOSED", stage="CTHA_BOTH_DIRECTIONS",
                        reason={"LONG": long_ctha_reason, "SHORT": short_ctha_reason})
            results.append(item)
            continue
        try:
            runtime, regime = base._symbol_state(state, symbol, decision_ms)
        except Exception as exc:
            item.update(status="NO_CANDIDATE_FAIL_CLOSED", stage="R91_RUNTIME_STATE", reason=str(exc))
            results.append(item)
            continue
        long_out = short_out = None
        if long_ctha_ok:
            p = {"schema": base.r89.INPUT_SCHEMA, "r84_evidence": evidence,
                 "m15_closed_bars": bars, "cost_model": runtime["cost_model"],
                 "risk_state": runtime["risk_state"], "expectancy": {"state": "RESEARCH_PENDING"}}
            long_out = base.r89.compile_candidate(p)
            item["long_candidate"] = bool(long_out.get("candidate_compiled"))
        if short_ctha_ok:
            p = {"schema": short.INPUT_SCHEMA, "r84_evidence": evidence,
                 "m15_closed_bars": bars, "cost_model": runtime["cost_model"],
                 "risk_state": runtime["risk_state"], "expectancy": {"state": "RESEARCH_PENDING"}}
            short_out = short.compile_candidate(p)
            item["short_candidate"] = bool(short_out.get("candidate_compiled"))
        if item["long_candidate"] and item["short_candidate"]:
            item.update(status="AMBIGUOUS_BIDIRECTIONAL_CANDIDATE_HOLD", stage="R92_DIRECTION_CONFLICT",
                        reason="same_symbol_same_decision_has_long_and_short_full_pass")
            results.append(item)
            continue
        if not item["long_candidate"] and not item["short_candidate"]:
            item.update(status="NO_CANDIDATE_FAIL_CLOSED", stage="EVENT_SEQUENCE",
                        reason={"LONG": None if long_out is None else long_out.get("reason"),
                                "SHORT": None if short_out is None else short_out.get("reason")})
            results.append(item)
            continue
        contract = base.r86.r87.build_contract(decision_ms, regime)
        if item["long_candidate"]:
            rr = long_out["r88_result"]
            reg_payload = {"schema": base.r86.INPUT_SCHEMA, "r85_input": rr["r85_input"],
                           "r85_evaluation": rr["r85_evaluation"],
                           "ledger_state": copy.deepcopy(local_ledger),
                           "registration_contract": contract}
            reg = base.r86.build_candidate(reg_payload)
            direction = "LONG"
        else:
            cr = short_out["compiler_result"]
            reg_payload = {"schema": short_reg.INPUT_SCHEMA, "r92_input": cr["r92_input"],
                           "r92_evaluation": cr["r92_evaluation"],
                           "ledger_state": copy.deepcopy(local_ledger),
                           "registration_contract": contract}
            reg = short_reg.build_candidate(reg_payload)
            direction = "SHORT"
        if reg.get("registration_candidate") is not True:
            item.update(status="NOT_REGISTERABLE_FAIL_CLOSED", stage="REGISTRATION_ADAPTER",
                        direction=direction, reason=reg.get("reason"), details=reg.get("details"))
            results.append(item)
            continue
        item.update(status="REGISTRATION_CANDIDATE_READY", stage="REGISTRATION_ADAPTER",
                    direction=direction, registration_candidate=True, event_id=reg["event_id"],
                    sample_phase=reg["sample_phase"])
        candidates.append(reg)
        local_ledger["existing_event_ids"].append(reg["event_id"])
        _reserve_risk(state, symbol)
        results.append(item)
    return {"schema": OUTPUT_SCHEMA, "result": "SWEEP_COMPLETE",
            "orchestrator_version": ORCHESTRATOR_VERSION,
            "requested_symbols": snapshot.get("requested_symbols"), "symbol_count": len(results),
            "long_candidate_count": sum(bool(x.get("long_candidate")) for x in results),
            "short_candidate_count": sum(bool(x.get("short_candidate")) for x in results),
            "registration_candidate_count": len(candidates), "results": results,
            "registration_candidates": candidates,
            "runtime_state_provenance": state["provenance"],
            "intra_sweep_risk_reservation": True,
            "sole_resolver_handoff_required": bool(candidates),
            "ledger_write_authority": False, "can_trade": False, "capital_permission": "DENY"}


async def run_sweep(symbols: list[str], runtime_state: dict[str, Any]) -> dict[str, Any]:
    if not symbols or any(s not in base.r84.ALL16 for s in symbols) or len(set(symbols)) != len(symbols):
        return fail("REQUESTED_SYMBOLS_INVALID", symbols=symbols)
    snapshot = await base.r84.run_probe(symbols, max_age_ms=2000)
    return process_snapshot(snapshot, runtime_state)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run R92 symmetric LONG/SHORT prospective sweep")
    parser.add_argument("--runtime-state", required=True, type=Path)
    parser.add_argument("--symbol", action="append", dest="symbols")
    parser.add_argument("--all16", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        state = json.loads(args.runtime_state.read_text(encoding="utf-8"))
        symbols = list(base.r84.ALL16) if args.all16 else (args.symbols or ["BTCUSDT"])
        result = asyncio.run(run_sweep(symbols, state))
        text = stable_json(result)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if result.get("result") == "SWEEP_COMPLETE" else 3
    except Exception as exc:
        print(stable_json(fail("R92_RUNTIME_ERROR", error=str(exc))))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

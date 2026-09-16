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


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


r84 = _load("r90_r84", ROOT / "tools" / "r6_all16_admission_probe.py")
r89 = _load("r90_r89", ROOT / "tools" / "r6_event_definition_contract.py")
r86 = _load("r90_r86", ROOT / "tools" / "r6_registration_adapter.py")

STATE_SCHEMA = "tradingos.r90_runtime_state.v1"
OUTPUT_SCHEMA = "tradingos.r90_prospective_sweep.v1"
ORCHESTRATOR_VERSION = "R90_PROSPECTIVE_SWEEP_ORCHESTRATOR_V1_20260916"


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fail(reason: str, **details: Any) -> dict[str, Any]:
    return {
        "schema": OUTPUT_SCHEMA,
        "result": "SWEEP_FAIL_CLOSED",
        "reason": reason,
        "details": details,
        "registration_candidates": [],
        "ledger_write_authority": False,
        "can_trade": False,
        "capital_permission": "DENY",
    }


def _state(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema") != STATE_SCHEMA:
        raise ValueError("runtime_state_schema_invalid")
    if payload.get("can_trade") is not False or payload.get("capital_permission") != "DENY":
        raise ValueError("runtime_state_authority_invalid")
    if type(payload.get("observed_at_ms")) is not int or payload["observed_at_ms"] <= 0:
        raise ValueError("runtime_state_time_invalid")
    if not isinstance(payload.get("provenance"), str) or not payload["provenance"].strip():
        raise ValueError("runtime_state_provenance_missing")
    if not isinstance(payload.get("ledger_state"), dict):
        raise ValueError("ledger_state_missing")
    return payload


def _symbol_state(state: dict[str, Any], symbol: str, decision_ms: int) -> tuple[dict[str, Any], str]:
    if state["observed_at_ms"] > decision_ms:
        raise ValueError("runtime_state_after_decision")
    regimes = state.get("regime_shadow_by_symbol")
    if not isinstance(regimes, dict) or not isinstance(regimes.get(symbol), str) or not regimes[symbol]:
        raise ValueError("regime_shadow_missing")
    cost = state.get("cost_model")
    if not isinstance(cost, dict):
        raise ValueError("cost_model_missing")
    risk_by_symbol = state.get("risk_state_by_symbol")
    risk = risk_by_symbol.get(symbol) if isinstance(risk_by_symbol, dict) else None
    if risk is None:
        risk = state.get("risk_state")
    if not isinstance(risk, dict):
        raise ValueError("risk_state_missing")
    return {"cost_model": copy.deepcopy(cost), "risk_state": copy.deepcopy(risk)}, regimes[symbol]


def _m15(symbol: str, decision_ms: int) -> list[list[Any]]:
    rows = r84.r83.fetch_klines(symbol, "15m", 180)
    return [row for row in rows if int(row[6]) < decision_ms]


def _base_result(symbol: str, evidence: dict[str, Any]) -> dict[str, Any]:
    q = evidence.get("quote", {})
    return {
        "symbol": symbol,
        "quote_decision_time_ms": q.get("decision_time_ms"),
        "quote_age_ms": q.get("decision_age_ms"),
        "restart_count": q.get("restart_count"),
        "structural_candidate": False,
        "registration_candidate": False,
    }


def process_snapshot(
    snapshot: dict[str, Any],
    runtime_state: dict[str, Any],
    fetch_m15: Callable[[str, int], list[list[Any]]] = _m15,
) -> dict[str, Any]:
    state = _state(runtime_state)
    if snapshot.get("schema") != r84.SCHEMA:
        return fail("R84_SNAPSHOT_SCHEMA_INVALID")
    evidence_rows = snapshot.get("evidence")
    if not isinstance(evidence_rows, list):
        return fail("R84_SNAPSHOT_EVIDENCE_INVALID")
    local_ledger = copy.deepcopy(state["ledger_state"])
    ids = local_ledger.get("existing_event_ids")
    if not isinstance(ids, list) or any(not isinstance(x, str) for x in ids):
        return fail("LEDGER_EVENT_IDS_INVALID")
    seen_symbols: set[str] = set()
    results: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for evidence in evidence_rows:
        symbol = evidence.get("symbol") if isinstance(evidence, dict) else None
        if symbol not in r84.ALL16 or symbol in seen_symbols:
            return fail("SNAPSHOT_SYMBOL_SET_INVALID", symbol=symbol)
        seen_symbols.add(symbol)
        result = _base_result(symbol, evidence)
        decision_ms = evidence.get("quote", {}).get("decision_time_ms")
        if type(decision_ms) is not int or decision_ms <= 0:
            result.update(stage="R84", status="FAIL_CLOSED", reason="DECISION_TIME_INVALID")
            results.append(result)
            continue
        try:
            bars = fetch_m15(symbol, decision_ms)
        except Exception as exc:
            result.update(stage="R89_DATA", status="NO_CANDIDATE_FAIL_CLOSED", reason=str(exc))
            results.append(result)
            continue
        try:
            ctha = r89._ctha_assessment(evidence, decision_ms)
            result["ctha_bias"] = ctha["bias"]
        except Exception as exc:
            result.update(stage="R89_CTHA", status="NO_CANDIDATE_FAIL_CLOSED", reason=str(exc))
            results.append(result)
            continue
        try:
            structural_probe = {
                "schema": r89.INPUT_SCHEMA,
                "r84_evidence": evidence,
                "m15_closed_bars": bars,
                "cost_model": None,
                "risk_state": None,
                "expectancy": {"state": "RESEARCH_PENDING"},
            }
            r88_input = r89.build_r88_input(structural_probe)
        except Exception as exc:
            result.update(stage="R89_SEQUENCE", status="NO_CANDIDATE_FAIL_CLOSED", reason=str(exc))
            results.append(result)
            continue
        result["structural_candidate"] = True
        result["reaction_id"] = r88_input["structural_event_evidence"][0]["reaction_id"]
        try:
            runtime, regime = _symbol_state(state, symbol, decision_ms)
        except ValueError as exc:
            result.update(stage="R90_RUNTIME_STATE", status="STRUCTURAL_CANDIDATE_BLOCKED_RUNTIME_STATE",
                          reason=str(exc))
            results.append(result)
            continue
        structural_probe["cost_model"] = runtime["cost_model"]
        structural_probe["risk_state"] = runtime["risk_state"]
        compiled = r89.compile_candidate(structural_probe)
        if compiled.get("candidate_compiled") is not True:
            result.update(stage="R89_R88_R85", status="NOT_ELIGIBLE_FAIL_CLOSED",
                          reason=compiled.get("reason"), details=compiled.get("details"))
            results.append(result)
            continue
        r88_result = compiled["r88_result"]
        r85_input = r88_result["r85_input"]
        r85_eval = r88_result["r85_evaluation"]
        registration = {
            "schema": r86.INPUT_SCHEMA,
            "r85_input": r85_input,
            "r85_evaluation": r85_eval,
            "ledger_state": copy.deepcopy(local_ledger),
            "registration_contract": r86.r87.build_contract(decision_ms, regime),
        }
        reg = r86.build_candidate(registration)
        if reg.get("registration_candidate") is not True:
            result.update(stage="R86_R87", status="NOT_REGISTERABLE_FAIL_CLOSED",
                          reason=reg.get("reason"), details=reg.get("details"))
            results.append(result)
            continue
        result.update(stage="R86_R87", status="REGISTRATION_CANDIDATE_READY",
                      registration_candidate=True, event_id=reg["event_id"],
                      sample_phase=reg["sample_phase"])
        candidates.append(reg)
        local_ledger["existing_event_ids"].append(reg["event_id"])
        results.append(result)
    return {
        "schema": OUTPUT_SCHEMA,
        "result": "SWEEP_COMPLETE",
        "orchestrator_version": ORCHESTRATOR_VERSION,
        "r89_contract_id": r89.CONTRACT_ID,
        "requested_symbols": snapshot.get("requested_symbols"),
        "symbol_count": len(results),
        "structural_candidate_count": sum(bool(x["structural_candidate"]) for x in results),
        "registration_candidate_count": len(candidates),
        "results": results,
        "registration_candidates": candidates,
        "runtime_state_provenance": state["provenance"],
        "sole_resolver_handoff_required": bool(candidates),
        "ledger_write_authority": False,
        "can_trade": False,
        "capital_permission": "DENY",
    }


async def run_sweep(symbols: list[str], runtime_state: dict[str, Any]) -> dict[str, Any]:
    if not symbols or any(s not in r84.ALL16 for s in symbols) or len(set(symbols)) != len(symbols):
        return fail("REQUESTED_SYMBOLS_INVALID", symbols=symbols)
    snapshot = await r84.run_probe(symbols, max_age_ms=2000)
    return process_snapshot(snapshot, runtime_state)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one prospective R6 Gate-v3 sweep without ledger writes")
    parser.add_argument("--runtime-state", required=True, type=Path)
    parser.add_argument("--symbol", action="append", dest="symbols")
    parser.add_argument("--all16", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        state = json.loads(args.runtime_state.read_text(encoding="utf-8"))
        symbols = list(r84.ALL16) if args.all16 else (args.symbols or ["BTCUSDT"])
        result = asyncio.run(run_sweep(symbols, state))
        text = stable_json(result)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if result.get("result") == "SWEEP_COMPLETE" else 3
    except Exception as exc:
        print(stable_json(fail("R90_RUNTIME_ERROR", error=str(exc))))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import copy
import importlib.util
import json
import sys
import time
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

r94 = _load("r95_r94_sweep", ROOT / "tools" / "r6_execution_capacity_sweep.py")
persist = _load("r95_gate", ROOT / "tools" / "r6_liquidity_persistence_gate.py")
SCHEMA = "tradingos.r95_persistent_capacity_sweep.v1"
VERSION = "R95_PERSISTENT_CAPACITY_SWEEP_V1_20260916"


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fail(reason: str, **details: Any) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "result": "SWEEP_FAIL_CLOSED",
        "reason": reason,
        "details": details,
        "registration_candidates": [],
        "ledger_write_authority": False,
        "can_trade": False,
        "capital_permission": "DENY",
    }


async def _capture_symbol(symbol: str, refs: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    if symbol not in refs:
        refs[symbol] = r94.r93.r92.base.r84.fetch_ctha(symbol)
    rules = await asyncio.to_thread(r94.cap.fetch_symbol_rules, symbol)
    sequence = await persist.capture_persistence_sequence(symbol)
    final_state = sequence["states"][-1]
    quote = final_state["quote"]
    book = final_state["book"]
    evidence = r94._build_evidence(symbol, refs, quote)
    try:
        reference = await asyncio.to_thread(r94.r93.gate.fetch_bybit_orderbook, symbol)
    except Exception:
        reference = None
    if isinstance(reference, dict):
        final = reference["received_at_ms"]
        integ = r94.r93.gate.evaluate(quote, reference, final_time_ms=final)
        if integ.get("details", {}).get("error") == "reference_stale":
            reference = await asyncio.to_thread(r94.r93.gate.fetch_bybit_orderbook, symbol)
            final = reference["received_at_ms"]
            integ = r94.r93.gate.evaluate(quote, reference, final_time_ms=final)
        reference["integrity_final_time_ms"] = final
    return evidence, book, rules, reference, sequence


async def capture_snapshot(symbols: list[str]) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any] | None], dict[str, dict[str, Any]], dict[str, str]]:
    r84 = r94.r93.r92.base.r84
    bad = [s for s in symbols if s not in r84.ALL16]
    if bad or len(set(symbols)) != len(symbols):
        raise ValueError("requested_symbols_invalid")
    refs = {"BTCUSDT": r84.fetch_ctha("BTCUSDT"), "ETHUSDT": r84.fetch_ctha("ETHUSDT")}
    evidence: list[dict[str, Any]] = []
    books: dict[str, dict[str, Any]] = {}
    rules: dict[str, dict[str, Any]] = {}
    market_refs: dict[str, dict[str, Any] | None] = {}
    sequences: dict[str, dict[str, Any]] = {}
    capture_errors: dict[str, str] = {}
    for symbol in symbols:
        try:
            item, book, rule, reference, sequence = await _capture_symbol(symbol, refs)
        except Exception as exc:
            capture_errors[symbol] = f"{type(exc).__name__}:{exc}"
            continue
        evidence.append(item)
        books[symbol] = book
        rules[symbol] = rule
        market_refs[symbol] = reference
        sequences[symbol] = sequence
    snapshot = {
        "schema": r84.SCHEMA,
        "observed_at_ms": int(time.time() * 1000),
        "universe": list(r84.ALL16),
        "requested_symbols": symbols,
        "evidence": evidence,
        "scored_admission_decision": "NOT_COMPUTED_BY_PROBE",
        "can_trade": False,
        "capital_permission": "DENY",
    }
    return snapshot, books, rules, market_refs, sequences, capture_errors


def _attach_persistence(candidate: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(candidate)
    row = out.get("row_values")
    if not isinstance(row, dict):
        raise ValueError("candidate_row_missing")
    trigger = json.loads(row["CURRENT_Trigger_Spec"])
    trigger["r95_liquidity_persistence"] = {
        "contract_id": result["contract_id"],
        "state_count": result["state_count"],
        "event_span_ms": result["event_span_ms"],
        "max_entry_impact_bps": result["max_entry_impact_bps"],
        "max_exit_impact_bps": result["max_exit_impact_bps"],
        "sequence_provenance_sha256": result["sequence_provenance_sha256"],
        "provenance_sha256": result["provenance_sha256"],
    }
    row["CURRENT_Trigger_Spec"] = stable_json(trigger)
    row["Operational_Risk"] = str(row.get("Operational_Risk", "")) + "; R95_LIQUIDITY_PERSISTENCE_PASS"
    row["Notes"] = (
        str(row.get("Notes", "")) +
        f"; R95 states={result['state_count']} spanMs={result['event_span_ms']} "
        f"maxEntryImpactBps={result['max_entry_impact_bps']} maxExitImpactBps={result['max_exit_impact_bps']}"
    )
    out["liquidity_persistence"] = result
    out["registration_row_sha256"] = r94.cap.sha256_json(row)
    out["liquidity_persistence_gate_pass"] = True
    out["ledger_write_authority"] = False
    out["can_trade"] = False
    out["capital_permission"] = "DENY"
    return out


def process_snapshot(
    snapshot: dict[str, Any],
    runtime_state: dict[str, Any],
    books: dict[str, dict[str, Any]],
    rules: dict[str, dict[str, Any]],
    references: dict[str, dict[str, Any] | None],
    sequences: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    try:
        state = copy.deepcopy(r94.r93.r92.base._state(runtime_state))
    except Exception as exc:
        return fail("RUNTIME_STATE_INVALID", error=str(exc))
    evidence_list = snapshot.get("evidence")
    if snapshot.get("schema") != r94.r93.r92.base.r84.SCHEMA or not isinstance(evidence_list, list):
        return fail("R84_SNAPSHOT_INVALID")
    results: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for evidence in evidence_list:
        symbol = evidence.get("symbol") if isinstance(evidence, dict) else None
        if symbol not in r94.r93.r92.base.r84.ALL16 or symbol in seen:
            return fail("SNAPSHOT_SYMBOL_SET_INVALID", symbol=symbol)
        seen.add(symbol)
        single = {**snapshot, "requested_symbols": [symbol], "evidence": [evidence]}
        upstream = r94.r93.process_snapshot(single, state, {symbol: references.get(symbol)})
        upstream_rows = upstream.get("results", []) if isinstance(upstream, dict) else []
        item = copy.deepcopy(upstream_rows[0]) if len(upstream_rows) == 1 else {"symbol": symbol}
        item["filter_provenance_sha256"] = rules.get(symbol, {}).get("provenance_sha256")
        item["capacity_book_provenance_sha256"] = books.get(symbol, {}).get("provenance_sha256")
        item["persistence_sequence_provenance_sha256"] = sequences.get(symbol, {}).get("provenance_sha256")
        candidates = upstream.get("registration_candidates", []) if isinstance(upstream, dict) else []
        if len(candidates) > 1:
            return fail("UPSTREAM_MULTIPLE_CANDIDATES_PER_SYMBOL", symbol=symbol)
        if not candidates:
            item["execution_capacity_status"] = "NOT_APPLICABLE_NO_UPSTREAM_CANDIDATE"
            item["liquidity_persistence_status"] = "NOT_APPLICABLE_NO_UPSTREAM_CANDIDATE"
            results.append(item)
            continue
        candidate = candidates[0]
        capacity = r94.cap.evaluate_capacity(
            candidate,
            state,
            evidence["quote"],
            books[symbol],
            rules[symbol],
        )
        item["execution_capacity"] = capacity
        if capacity.get("status") != "PASS":
            item.update(
                status="EXECUTION_CAPACITY_FAIL_CLOSED",
                stage="R94_EXECUTION_CAPACITY",
                registration_candidate=False,
                reason=capacity.get("reason"),
                liquidity_persistence_status="NOT_APPLICABLE_CAPACITY_FAIL",
            )
            results.append(item)
            continue
        capacity_candidate = r94._attach_capacity(candidate, capacity)
        persistence_result = persist.evaluate_persistence(
            capacity_candidate,
            capacity,
            sequences[symbol],
        )
        item["liquidity_persistence"] = persistence_result
        if persistence_result.get("status") != "PASS":
            item.update(
                status="LIQUIDITY_PERSISTENCE_FAIL_CLOSED",
                stage="R95_LIQUIDITY_PERSISTENCE",
                registration_candidate=False,
                reason=persistence_result.get("reason"),
                execution_capacity_status="PASS",
            )
            results.append(item)
            continue
        final_candidate = _attach_persistence(capacity_candidate, persistence_result)
        event_id = final_candidate.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            return fail("R95_EVENT_ID_INVALID", symbol=symbol)
        try:
            r94._reserve_external(state, symbol, event_id)
        except Exception as exc:
            return fail("R95_RISK_RESERVATION_FAILED", symbol=symbol, error=str(exc))
        accepted.append(final_candidate)
        item.update(
            status="REGISTRATION_CANDIDATE_READY",
            stage="R95_LIQUIDITY_PERSISTENCE",
            registration_candidate=True,
            event_id=event_id,
            execution_capacity_status="PASS",
            liquidity_persistence_status="PASS",
        )
        results.append(item)
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "result": "SWEEP_COMPLETE",
        "requested_symbols": snapshot.get("requested_symbols"),
        "symbol_count": len(results),
        "integrity_pass_count": sum(x.get("market_integrity", {}).get("status") == "PASS" for x in results),
        "execution_capacity_pass_count": sum(x.get("execution_capacity_status") == "PASS" for x in results),
        "execution_capacity_fail_count": sum(x.get("stage") == "R94_EXECUTION_CAPACITY" for x in results),
        "liquidity_persistence_pass_count": sum(x.get("liquidity_persistence_status") == "PASS" for x in results),
        "liquidity_persistence_fail_count": sum(x.get("stage") == "R95_LIQUIDITY_PERSISTENCE" and not x.get("registration_candidate") for x in results),
        "registration_candidate_count": len(accepted),
        "results": results,
        "registration_candidates": accepted,
        "intra_sweep_risk_reservation": True,
        "sole_resolver_handoff_required": bool(accepted),
        "ledger_write_authority": False,
        "can_trade": False,
        "capital_permission": "DENY",
    }


async def run_sweep(symbols: list[str], runtime_state: dict[str, Any]) -> dict[str, Any]:
    try:
        snapshot, books, rules, references, sequences, capture_errors = await capture_snapshot(symbols)
    except Exception as exc:
        return fail("R95_CAPTURE_FAIL_CLOSED", error=str(exc))
    result = process_snapshot(snapshot, runtime_state, books, rules, references, sequences)
    if result.get("result") != "SWEEP_COMPLETE":
        return result
    rows_by_symbol = {row.get("symbol"): row for row in result.get("results", []) if isinstance(row, dict)}
    for symbol, error in capture_errors.items():
        rows_by_symbol[symbol] = {
            "symbol": symbol,
            "status": "R95_PERSISTENCE_CAPTURE_FAIL_CLOSED",
            "stage": "R95_PERSISTENCE_CAPTURE",
            "reason": error,
            "registration_candidate": False,
            "execution_capacity_status": "NOT_APPLICABLE_CAPTURE_FAIL",
            "liquidity_persistence_status": "FAIL_CLOSED_CAPTURE",
        }
    result["results"] = [rows_by_symbol[s] for s in symbols if s in rows_by_symbol]
    result["requested_symbols"] = list(symbols)
    result["symbol_count"] = len(result["results"])
    result["persistence_capture_fail_count"] = len(capture_errors)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run R95 persistent-liquidity shadow sweep")
    parser.add_argument("--runtime-state", required=True, type=Path)
    parser.add_argument("--symbol", action="append", dest="symbols")
    parser.add_argument("--all16", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        state = json.loads(args.runtime_state.read_text(encoding="utf-8-sig"))
        symbols = list(r94.r93.r92.base.r84.ALL16) if args.all16 else (args.symbols or ["BTCUSDT"])
        result = asyncio.run(run_sweep(symbols, state))
        text = stable_json(result)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if result.get("result") == "SWEEP_COMPLETE" else 3
    except Exception as exc:
        print(stable_json(fail("R95_RUNTIME_ERROR", error=str(exc))))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

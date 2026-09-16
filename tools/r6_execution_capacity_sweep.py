#!/usr/bin/env python3
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

r93 = _load("r94_r93", ROOT / "tools" / "r6_integrity_symmetric_sweep.py")
cap = _load("r94_capacity", ROOT / "tools" / "r6_execution_capacity_gate.py")
SCHEMA = "tradingos.r94_execution_capacity_sweep.v1"
VERSION = "R94_EXECUTION_CAPACITY_SWEEP_V1_20260916"
def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fail(reason: str, **details: Any) -> dict[str, Any]:
    return {"schema": SCHEMA, "version": VERSION, "result": "SWEEP_FAIL_CLOSED",
            "reason": reason, "details": details, "registration_candidates": [],
            "ledger_write_authority": False, "can_trade": False,
            "capital_permission": "DENY"}


def _build_evidence(symbol: str, refs: dict[str, dict[str, Any]], quote: dict[str, Any]) -> dict[str, Any]:
    r84 = r93.r92.base.r84
    target = refs[symbol]
    btc = refs["BTCUSDT"]
    eth = refs["ETHUSDT"]
    return {
        "schema": r84.SYMBOL_SCHEMA,
        "symbol": symbol,
        "observed_at_ms": int(time.time() * 1000),
        "target_ctha": target,
        "reference_ctha": {"BTCUSDT": btc, "ETHUSDT": eth},
        "relative_strength": {
            tf: r84.relative_state(target[tf], btc[tf], eth[tf]) for tf in r84.r83.TF_LIMITS
        },
        "quote": quote,
        "admission_authority": "EVIDENCE_ONLY_FROZEN_CURRENT_RULES_APPLY",
        "can_trade": False,
        "capital_permission": "DENY",
    }
async def _capture_symbol(symbol: str, refs: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    if symbol not in refs:
        refs[symbol] = r93.r92.base.r84.fetch_ctha(symbol)
    rules = await asyncio.to_thread(cap.fetch_symbol_rules, symbol)
    # R94 capacity-mode order: synchronize Binance depth+L1 first, then
    # corroborate immediately with Bybit. Thresholds remain frozen at 2s.
    quote, book = await cap.capture_quote_and_capacity(symbol)
    evidence = _build_evidence(symbol, refs, quote)
    try:
        reference = await asyncio.to_thread(r93.gate.fetch_bybit_orderbook, symbol)
    except Exception:
        reference = None
    if isinstance(reference, dict):
        final = reference['received_at_ms']
        integ = r93.gate.evaluate(quote, reference, final_time_ms=final)
        if integ.get('details', {}).get('error') == 'reference_stale':
            reference = await asyncio.to_thread(r93.gate.fetch_bybit_orderbook, symbol)
            final = reference['received_at_ms']
            integ = r93.gate.evaluate(quote, reference, final_time_ms=final)
        reference['integrity_final_time_ms'] = final
    return evidence, book, rules, reference



async def capture_snapshot(symbols: list[str]) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any] | None]]:
    r84 = r93.r92.base.r84
    bad = [s for s in symbols if s not in r84.ALL16]
    if bad or len(set(symbols)) != len(symbols):
        raise ValueError("requested_symbols_invalid")
    refs = {"BTCUSDT": r84.fetch_ctha("BTCUSDT"), "ETHUSDT": r84.fetch_ctha("ETHUSDT")}
    evidence: list[dict[str, Any]] = []
    books: dict[str, dict[str, Any]] = {}
    rules: dict[str, dict[str, Any]] = {}
    market_refs: dict[str, dict[str, Any] | None] = {}
    for symbol in symbols:
        item, book, rule, reference = await _capture_symbol(symbol, refs)
        evidence.append(item)
        books[symbol] = book
        rules[symbol] = rule
        market_refs[symbol] = reference
    snapshot = {
        "schema": r84.SCHEMA, "observed_at_ms": int(time.time() * 1000),
        "universe": list(r84.ALL16), "requested_symbols": symbols,
        "evidence": evidence, "scored_admission_decision": "NOT_COMPUTED_BY_PROBE",
        "can_trade": False, "capital_permission": "DENY",
    }
    return snapshot, books, rules, market_refs


def _attach_capacity(candidate: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(candidate)
    row = out.get("row_values")
    if not isinstance(row, dict):
        raise ValueError("candidate_row_missing")
    trigger = json.loads(row["CURRENT_Trigger_Spec"])
    trigger["r94_execution_capacity"] = {
        k: result[k] for k in (
            "contract_id", "research_equity_usdt", "quantity", "gross_notional_usdt",
            "actual_planned_risk_usdt", "actual_planned_risk_pct", "entry_impact_bps",
            "exit_impact_bps", "rounded_stop", "rounded_target",
            "capacity_book_provenance_sha256", "symbol_rules_provenance_sha256",
        )
    }
    row["CURRENT_Trigger_Spec"] = stable_json(trigger)
    row["CURRENT_Stop"] = float(result["rounded_stop"])
    row["CURRENT_Target"] = float(result["rounded_target"])
    row["RR_Net"] = float(result["net_rr_after_venue_rounding"])
    row["Provenance_State"] = (
        str(row.get("Provenance_State", "")) +
        f"; R94_CAPACITY={result['provenance_sha256']}"
    )
    row["Operational_Risk"] = "PASS_AT_REGISTRATION; R93_INTEGRITY_PASS; R94_EXECUTION_CAPACITY_PASS"
    row["Notes"] = (
        str(row.get("Notes", "")) +
        f"; R94 qty={result['quantity']} notional={result['gross_notional_usdt']} "
        f"entryImpactBps={result['entry_impact_bps']} exitImpactBps={result['exit_impact_bps']}"
    )
    out["execution_capacity"] = result
    out["registration_row_sha256"] = cap.sha256_json(row)
    out["capacity_gate_pass"] = True
    out["ledger_write_authority"] = False
    out["can_trade"] = False
    out["capital_permission"] = "DENY"
    return out


def _reserve_external(state: dict[str, Any], symbol: str, event_id: str) -> None:
    ledger = state.get("ledger_state")
    if not isinstance(ledger, dict) or not isinstance(ledger.get("existing_event_ids"), list):
        raise ValueError("ledger_state_invalid")
    if event_id in ledger["existing_event_ids"]:
        raise ValueError("duplicate_event_id_during_r94_sweep")
    ledger["existing_event_ids"].append(event_id)
    r93.r92._reserve_risk(state, symbol)
def process_snapshot(
    snapshot: dict[str, Any], runtime_state: dict[str, Any], books: dict[str, dict[str, Any]],
    rules: dict[str, dict[str, Any]], references: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    try:
        state = copy.deepcopy(r93.r92.base._state(runtime_state))
    except Exception as exc:
        return fail("RUNTIME_STATE_INVALID", error=str(exc))
    evidence_list = snapshot.get("evidence")
    if snapshot.get("schema") != r93.r92.base.r84.SCHEMA or not isinstance(evidence_list, list):
        return fail("R84_SNAPSHOT_INVALID")
    results: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for evidence in evidence_list:
        symbol = evidence.get("symbol") if isinstance(evidence, dict) else None
        if symbol not in r93.r92.base.r84.ALL16 or symbol in seen:
            return fail("SNAPSHOT_SYMBOL_SET_INVALID", symbol=symbol)
        seen.add(symbol)
        single = {**snapshot, "requested_symbols": [symbol], "evidence": [evidence]}
        upstream = r93.process_snapshot(single, state, {symbol: references.get(symbol)})
        upstream_rows = upstream.get("results", []) if isinstance(upstream, dict) else []
        item = copy.deepcopy(upstream_rows[0]) if len(upstream_rows) == 1 else {"symbol": symbol}
        item["filter_provenance_sha256"] = rules.get(symbol, {}).get("provenance_sha256")
        item["capacity_book_provenance_sha256"] = books.get(symbol, {}).get("provenance_sha256")
        candidates = upstream.get("registration_candidates", []) if isinstance(upstream, dict) else []
        if len(candidates) > 1:
            return fail("UPSTREAM_MULTIPLE_CANDIDATES_PER_SYMBOL", symbol=symbol)
        if not candidates:
            item["execution_capacity_status"] = "NOT_APPLICABLE_NO_UPSTREAM_CANDIDATE"
            results.append(item)
            continue
        candidate = candidates[0]
        capacity = cap.evaluate_capacity(candidate, state, evidence["quote"], books[symbol], rules[symbol])
        item["execution_capacity"] = capacity
        if capacity.get("status") != "PASS":
            item.update(status="EXECUTION_CAPACITY_FAIL_CLOSED", stage="R94_EXECUTION_CAPACITY",
                        registration_candidate=False, reason=capacity.get("reason"))
            results.append(item)
            continue
        final_candidate = _attach_capacity(candidate, capacity)
        event_id = final_candidate.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            return fail("R94_EVENT_ID_INVALID", symbol=symbol)
        try:
            _reserve_external(state, symbol, event_id)
        except Exception as exc:
            return fail("R94_RISK_RESERVATION_FAILED", symbol=symbol, error=str(exc))
        accepted.append(final_candidate)
        item.update(status="REGISTRATION_CANDIDATE_READY", stage="R94_EXECUTION_CAPACITY",
                    registration_candidate=True, event_id=event_id,
                    execution_capacity_status="PASS")
        results.append(item)
    return {
        "schema": SCHEMA, "version": VERSION, "result": "SWEEP_COMPLETE",
        "requested_symbols": snapshot.get("requested_symbols"), "symbol_count": len(results),
        "integrity_pass_count": sum(x.get("market_integrity", {}).get("status") == "PASS" for x in results),
        "execution_capacity_pass_count": sum(x.get("execution_capacity_status") == "PASS" for x in results),
        "execution_capacity_fail_count": sum(x.get("stage") == "R94_EXECUTION_CAPACITY" and not x.get("registration_candidate") for x in results),
        "registration_candidate_count": len(accepted), "results": results,
        "registration_candidates": accepted, "intra_sweep_risk_reservation": True,
        "sole_resolver_handoff_required": bool(accepted), "ledger_write_authority": False,
        "can_trade": False, "capital_permission": "DENY",
    }
async def run_sweep(symbols: list[str], runtime_state: dict[str, Any]) -> dict[str, Any]:
    try:
        snapshot, books, rules, references = await capture_snapshot(symbols)
    except Exception as exc:
        return fail("R94_CAPTURE_FAIL_CLOSED", error=str(exc))
    return process_snapshot(snapshot, runtime_state, books, rules, references)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Gate-v7-ready R94 execution-capacity sweep")
    parser.add_argument("--runtime-state", required=True, type=Path)
    parser.add_argument("--symbol", action="append", dest="symbols")
    parser.add_argument("--all16", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        state = json.loads(args.runtime_state.read_text(encoding="utf-8-sig"))
        symbols = list(r93.r92.base.r84.ALL16) if args.all16 else (args.symbols or ["BTCUSDT"])
        result = asyncio.run(run_sweep(symbols, state))
        text = stable_json(result)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if result.get("result") == "SWEEP_COMPLETE" else 3
    except Exception as exc:
        print(stable_json(fail("R94_RUNTIME_ERROR", error=str(exc))))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

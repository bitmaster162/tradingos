#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import copy
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

r95 = _load("r97_sweep_r95", ROOT / "tools" / "r6_persistent_capacity_sweep.py")
cont = _load("r97_cont", ROOT / "tools" / "r6_decision_continuity_gate.py")
settle = _load("r97_settle", ROOT / "tools" / "r6_paper_fill_settlement.py")
SCHEMA = "tradingos.r97_paper_fill_settlement_sweep.v1"
VERSION = "R97_PAPER_FILL_SETTLEMENT_SWEEP_V1_20260916"

def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fail(reason: str, **details: Any) -> dict[str, Any]:
    return {"schema": SCHEMA, "version": VERSION, "result": "SWEEP_FAIL_CLOSED",
            "reason": reason, "details": details, "registration_candidates": [],
            "ledger_write_authority": False, "can_trade": False,
            "capital_permission": "DENY"}


def _attach_continuity(candidate: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(candidate)
    row = out.get("row_values")
    if not isinstance(row, dict):
        raise ValueError("candidate_row_missing")
    trigger = json.loads(row["CURRENT_Trigger_Spec"])
    trigger["r96_decision_continuity"] = {
        k: result[k] for k in (
            "contract_id", "continuity_gap_ms", "adverse_drift_bps", "frozen_quantity",
            "fresh_notional_usdt", "fresh_entry_impact_bps", "fresh_exit_impact_bps",
            "fresh_quote_provenance_sha256", "fresh_book_provenance_sha256",
            "reference_provenance_sha256", "provenance_sha256",
        )
    }
    row["CURRENT_Trigger_Spec"] = stable_json(trigger)
    row["Operational_Risk"] = str(row.get("Operational_Risk", "")) + "; R96_DECISION_CONTINUITY_PASS"
    row["Notes"] = str(row.get("Notes", "")) + f"; R96 gapMs={result['continuity_gap_ms']} adverseDriftBps={result['adverse_drift_bps']}"
    out["decision_continuity"] = result
    out["registration_row_sha256"] = r95.r94.cap.sha256_json(row)
    out["decision_continuity_gate_pass"] = True
    out["ledger_write_authority"] = False
    out["can_trade"] = False
    out["capital_permission"] = "DENY"
    return out

async def _fresh_continuity_state(symbol: str, rules: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    r95.r94.cap.validate_rules(rules, symbol)
    quote, book = await r95.r94.cap.capture_quote_and_capacity(symbol)
    try:
        reference = await asyncio.to_thread(r95.r94.r93.gate.fetch_bybit_orderbook, symbol)
    except Exception:
        reference = None
    if isinstance(reference, dict):
        final = reference["received_at_ms"]
        integ = r95.r94.r93.gate.evaluate(quote, reference, final_time_ms=final)
        if integ.get("details", {}).get("error") == "reference_stale":
            reference = await asyncio.to_thread(r95.r94.r93.gate.fetch_bybit_orderbook, symbol)
            final = reference["received_at_ms"]
        reference["integrity_final_time_ms"] = final
    return quote, book, rules, reference


async def run_sweep(symbols: list[str], runtime_state: dict[str, Any]) -> dict[str, Any]:
    try:
        state = copy.deepcopy(r95.r94.r93.r92.base._state(runtime_state))
    except Exception as exc:
        return fail("RUNTIME_STATE_INVALID", error=str(exc))
    r84 = r95.r94.r93.r92.base.r84
    if any(s not in r84.ALL16 for s in symbols) or len(set(symbols)) != len(symbols):
        return fail("REQUESTED_SYMBOLS_INVALID")
    refs = {"BTCUSDT": r84.fetch_ctha("BTCUSDT"), "ETHUSDT": r84.fetch_ctha("ETHUSDT")}
    results: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    for symbol in symbols:
        try:
            evidence, book, rules, reference, sequence = await r95._capture_symbol(symbol, refs)
        except Exception as exc:
            results.append({"symbol": symbol, "status": "R95_PERSISTENCE_CAPTURE_FAIL_CLOSED",
                            "stage": "R95_PERSISTENCE_CAPTURE", "reason": f"{type(exc).__name__}:{exc}",
                            "registration_candidate": False, "decision_continuity_status": "NOT_APPLICABLE"})
            continue
        snapshot = {"schema": r84.SCHEMA, "observed_at_ms": evidence.get("observed_at_ms"),
                    "universe": list(r84.ALL16), "requested_symbols": [symbol], "evidence": [evidence],
                    "scored_admission_decision": "NOT_COMPUTED_BY_PROBE", "can_trade": False,
                    "capital_permission": "DENY"}
        upstream = r95.process_snapshot(snapshot, state, {symbol: book}, {symbol: rules},
                                        {symbol: reference}, {symbol: sequence})
        rows = upstream.get("results", []) if isinstance(upstream, dict) else []
        item = copy.deepcopy(rows[0]) if len(rows) == 1 else {"symbol": symbol}
        candidates = upstream.get("registration_candidates", []) if isinstance(upstream, dict) else []
        if len(candidates) > 1:
            return fail("UPSTREAM_MULTIPLE_CANDIDATES_PER_SYMBOL", symbol=symbol)
        if not candidates:
            item["decision_continuity_status"] = "NOT_APPLICABLE_NO_R95_CANDIDATE"
            results.append(item)
            continue
        candidate = candidates[0]
        old_quote = sequence["states"][-1]["quote"]
        try:
            fresh_quote, fresh_book, fresh_rules, fresh_reference = await _fresh_continuity_state(symbol, rules)
        except Exception as exc:
            item.update(status="DECISION_CONTINUITY_CAPTURE_FAIL_CLOSED", stage="R96_DECISION_CONTINUITY",
                        registration_candidate=False, reason=f"{type(exc).__name__}:{exc}",
                        decision_continuity_status="FAIL_CLOSED_CAPTURE")
            results.append(item)
            continue
        continuity = cont.evaluate_continuity(candidate, old_quote, fresh_quote, fresh_book,
                                              fresh_rules, fresh_reference)
        item["decision_continuity"] = continuity
        if continuity.get("status") != "PASS":
            item.update(status="DECISION_CONTINUITY_FAIL_CLOSED", stage="R96_DECISION_CONTINUITY",
                        registration_candidate=False, reason=continuity.get("reason"),
                        decision_continuity_status="FAIL_CLOSED")
            results.append(item)
            continue
        continuity_candidate = _attach_continuity(candidate, continuity)
        settled = settle.settle_candidate(continuity_candidate, runtime_state)
        if settled.get("paper_fill_settlement_gate_pass") is not True:
            item.update(status="PAPER_FILL_SETTLEMENT_FAIL_CLOSED", stage="R97_PAPER_FILL_SETTLEMENT",
                        registration_candidate=False, reason=settled.get("reason"),
                        decision_continuity_status="PASS", paper_fill_settlement_status="FAIL_CLOSED")
            item["paper_fill_settlement"] = settled
            results.append(item)
            continue
        event_id = settled.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            return fail("R97_EVENT_ID_INVALID", symbol=symbol)
        try:
            r95.r94._reserve_external(state, symbol, event_id)
        except Exception as exc:
            return fail("R97_RISK_RESERVATION_FAILED", symbol=symbol, error=str(exc))
        accepted.append(settled)
        item.update(status="REGISTRATION_CANDIDATE_READY", stage="R97_PAPER_FILL_SETTLEMENT",
                    registration_candidate=True, event_id=event_id,
                    decision_continuity_status="PASS", paper_fill_settlement_status="PASS")
        results.append(item)
    return {
        "schema": SCHEMA, "version": VERSION, "result": "SWEEP_COMPLETE",
        "requested_symbols": list(symbols), "symbol_count": len(results),
        "registration_candidate_count": len(accepted),
        "decision_continuity_pass_count": sum(x.get("decision_continuity_status") == "PASS" for x in results),
        "decision_continuity_fail_count": sum(x.get("stage") == "R96_DECISION_CONTINUITY" and not x.get("registration_candidate") for x in results),
        "paper_fill_settlement_pass_count": sum(x.get("paper_fill_settlement_status") == "PASS" for x in results),
        "paper_fill_settlement_fail_count": sum(x.get("stage") == "R97_PAPER_FILL_SETTLEMENT" and not x.get("registration_candidate") for x in results),
        "results": results, "registration_candidates": accepted,
        "intra_sweep_risk_reservation": True, "sole_resolver_handoff_required": bool(accepted),
        "ledger_write_authority": False, "can_trade": False, "capital_permission": "DENY",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run R96 decision-continuity shadow sweep")
    parser.add_argument("--runtime-state", required=True, type=Path)
    parser.add_argument("--symbol", action="append", dest="symbols")
    parser.add_argument("--all16", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        state = json.loads(args.runtime_state.read_text(encoding="utf-8-sig"))
        all16 = list(r95.r94.r93.r92.base.r84.ALL16)
        symbols = all16 if args.all16 else (args.symbols or ["BTCUSDT"])
        result = asyncio.run(run_sweep(symbols, state))
        text = stable_json(result)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if result.get("result") == "SWEEP_COMPLETE" else 3
    except Exception as exc:
        print(stable_json(fail("R96_RUNTIME_ERROR", error=str(exc))))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

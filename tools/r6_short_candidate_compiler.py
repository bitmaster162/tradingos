#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVAL_PATH = ROOT / "tools" / "r6_directional_admission_evaluator.py"
SPEC = importlib.util.spec_from_file_location("r92_eval", EVAL_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load R92 evaluator")
evaluator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = evaluator
SPEC.loader.exec_module(evaluator)
base = evaluator.base

INPUT_SCHEMA = "tradingos.r92_short_compile_input.v1"
OUTPUT_SCHEMA = "tradingos.r92_short_compile_result.v1"
COMPILER_VERSION = "R92_SHORT_CANDIDATE_COMPILER_V1_20260916"
ALLOWED_EVENTS = {"LIQUIDITY_SWEEP_REJECT", "DISPLACEMENT", "MSS", "CHOCH", "BOS"}


def stable_json(v: Any) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(v: Any) -> str:
    return hashlib.sha256(stable_json(v).encode()).hexdigest()

def fail(reason: str, **details: Any) -> dict[str, Any]:
    return {"schema": OUTPUT_SCHEMA, "result": "NO_CANDIDATE_FAIL_CLOSED", "reason": reason,
            "details": details, "candidate_compiled": False, "ledger_write_authority": False,
            "can_trade": False, "capital_permission": "DENY"}


def _require_ref(item: dict[str, Any], prefix: str) -> None:
    if not isinstance(item.get("provenance"), str) or not item["provenance"].strip():
        raise ValueError(f"{prefix}_provenance_missing")
    if item.get("source_timeframe") not in {"1M", "1w", "1d", "4h", "1h", "15m", "1m", "aggTrades"}:
        raise ValueError(f"{prefix}_source_timeframe_invalid")


def _ctha(raw: Any, evidence: dict[str, Any], decision_ms: int) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("status") != "PASS" or raw.get("material_contradiction") is not False:
        raise ValueError("ctha_not_explicit_pass")
    if raw.get("r84_evidence_sha256") != sha(evidence):
        raise ValueError("ctha_evidence_binding_mismatch")
    if raw.get("direction") != "SHORT" or raw.get("bias") not in {"bearish", "range"}:
        raise ValueError("ctha_direction_or_bias_invalid")
    if tuple(raw.get("mapped_timeframes", [])) != base.REQUIRED_TFS:
        raise ValueError("ctha_timeframes_invalid")
    known = raw.get("known_at_ms")
    if type(known) is not int or known <= 0 or known > decision_ms:
        raise ValueError("ctha_known_at_invalid")
    refs = raw.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        raise ValueError("ctha_evidence_refs_missing")
    return copy.deepcopy(raw)

def _events(raw: Any, decision_ms: int) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("structural_event_evidence_missing")
    ids = {x.get("reaction_id") for x in raw if isinstance(x, dict)}
    if len(ids) != 1 or None in ids or "" in ids:
        raise ValueError("event_reaction_id_invalid")
    rid = next(iter(ids))
    out = []
    seen: set[int] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or item.get("type") not in ALLOWED_EVENTS:
            raise ValueError(f"event_{i}_type_invalid")
        if item.get("direction") != "SHORT" or item.get("reaction_id") != rid:
            raise ValueError(f"event_{i}_direction_or_link_invalid")
        _require_ref(item, f"event_{i}")
        ts = item.get("time_ms")
        if type(ts) is not int or ts <= 0 or ts > decision_ms or ts in seen:
            raise ValueError(f"event_{i}_time_invalid")
        seen.add(ts)
        copied = copy.deepcopy(item)
        if "level" in copied:
            copied["level"] = str(base.dec(copied["level"], f"event_{i}_level"))
        out.append(copied)
    out.sort(key=lambda x: x["time_ms"])
    return rid, out


def _confluence(raw: Any, rid: str, decision_ms: int) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("confluence_evidence_missing")
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or item.get("type") not in base.ALLOWED_CONFLUENCE:
            raise ValueError(f"confluence_{i}_type_invalid")
        _require_ref(item, f"confluence_{i}")
        known = item.get("known_at_ms")
        if type(known) is not int or known <= 0 or known > decision_ms:
            raise ValueError(f"confluence_{i}_time_invalid")
        if item.get("reaction_id") != rid or item.get("direction") != "SHORT" or item.get("status") != "PASS":
            raise ValueError(f"confluence_{i}_thesis_link_invalid")
        out.append(copy.deepcopy(item))
    return out

def _levels(raw: Any, decision_ms: int, bid: Decimal) -> tuple[dict[str, Any], list[dict[str, Any]], str, str]:
    if not isinstance(raw, dict) or not isinstance(raw.get("invalidation"), dict):
        raise ValueError("structural_levels_missing")
    inv = raw["invalidation"]
    _require_ref(inv, "invalidation")
    inv_known = inv.get("known_at_ms")
    inv_price = base.dec(inv.get("price"), "invalidation_price")
    if type(inv_known) is not int or inv_known <= 0 or inv_known > decision_ms or inv_price <= bid:
        raise ValueError("invalidation_invalid")
    targets_raw = raw.get("targets")
    if not isinstance(targets_raw, list) or not targets_raw:
        raise ValueError("targets_missing")
    targets = []
    downside: list[Decimal] = []
    for i, item in enumerate(targets_raw):
        if not isinstance(item, dict):
            raise ValueError(f"target_{i}_invalid")
        _require_ref(item, f"target_{i}")
        known = item.get("known_at_ms")
        price = base.dec(item.get("price"), f"target_{i}_price")
        if type(known) is not int or known <= 0 or known > decision_ms:
            raise ValueError(f"target_{i}_time_invalid")
        copied = copy.deepcopy(item)
        copied["price"] = str(price)
        targets.append(copied)
        if copied.get("valid") is True and 0 < price < bid:
            downside.append(price)
    if not downside:
        raise ValueError("no_valid_downside_target")
    objective = raw.get("liquidity_objective")
    if not isinstance(objective, str) or not objective.strip():
        raise ValueError("liquidity_objective_missing")
    return ({"price": str(inv_price), "known_at_ms": inv_known}, targets, str(max(downside)), objective)

def compile_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != INPUT_SCHEMA:
        return fail("INPUT_SCHEMA_INVALID")
    evidence = payload.get("r84_evidence")
    if not isinstance(evidence, dict):
        return fail("R84_EVIDENCE_MISSING")
    quote = evidence.get("quote")
    decision_ms = quote.get("decision_time_ms") if isinstance(quote, dict) else None
    if type(decision_ms) is not int or decision_ms <= 0:
        return fail("DECISION_TIME_INVALID")
    r84_gate = base.validate_r84_evidence(evidence, decision_ms)
    if r84_gate.get("status") != "PASS":
        return fail("R84_EVIDENCE_INVALID", gate=r84_gate)
    try:
        ctha = _ctha(payload.get("ctha_assessment"), evidence, decision_ms)
        rid, events = _events(payload.get("structural_event_evidence"), decision_ms)
        confluence = _confluence(payload.get("confluence_evidence"), rid, decision_ms)
        bid = base.dec(quote.get("bid"), "quote_bid")
        invalidation, targets, chosen, objective = _levels(payload.get("structural_levels"), decision_ms, bid)
        cost_model = payload.get("cost_model")
        risk_state = payload.get("risk_state")
        if not isinstance(cost_model, dict) or not isinstance(risk_state, dict):
            raise ValueError("runtime_state_missing")
    except ValueError as exc:
        return fail("EVIDENCE_COMPILATION_FAILED", error=str(exc))
    p = {"status": "PASS", "direction": "SHORT", "bias": ctha["bias"],
         "liquidity_objective": objective, "htf_invalidation": invalidation["price"],
         "reaction_id": rid, "known_at_ms": ctha["known_at_ms"]}
    bundle = {"setup_family": evaluator.SETUP_FAMILY, "direction": "SHORT", "ctha": ctha,
              "p": p, "r": {"setup_family": evaluator.SETUP_FAMILY, "reaction_id": rid, "events": events},
              "c": confluence, "invalidation": invalidation, "targets": targets,
              "chosen_target": chosen, "cost_model": copy.deepcopy(cost_model),
              "risk": copy.deepcopy(risk_state),
              "expectancy": payload.get("expectancy", {"state": "RESEARCH_PENDING"})}
    r92_input = {"schema": evaluator.INPUT_SCHEMA, "decision_time_ms": decision_ms,
                 "r84_evidence": evidence, "decision_bundle": bundle}
    evaluation = evaluator.evaluate(r92_input)
    if evaluation.get("trial_eligibility") != "R6_SHADOW_TRIAL_ELIGIBLE":
        return fail("R92_NOT_TRIAL_ELIGIBLE", evaluation=evaluation,
                    compiled_input=r92_input, input_sha256=sha(r92_input))
    return {"schema": OUTPUT_SCHEMA, "result": "SHORT_CANDIDATE_COMPILED_R92_PASS",
            "compiler_version": COMPILER_VERSION, "candidate_compiled": True,
            "r92_input": r92_input, "r92_input_sha256": sha(r92_input),
            "r92_evaluation": evaluation, "r92_evaluation_sha256": sha(evaluation),
            "ledger_write_authority": False, "can_trade": False, "capital_permission": "DENY"}


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Compile one R92 SHORT candidate")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = compile_candidate(json.loads(args.input.read_text(encoding="utf-8")))
        text = stable_json(result)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if result.get("candidate_compiled") else 3
    except Exception as exc:
        print(stable_json({"schema": OUTPUT_SCHEMA, "result": "ERROR", "error": str(exc),
                          "candidate_compiled": False, "ledger_write_authority": False,
                          "can_trade": False, "capital_permission": "DENY"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
R85_PATH = ROOT / "tools" / "r6_admission_evaluator.py"
SPEC = importlib.util.spec_from_file_location("r85_eval", R85_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load R85 evaluator")
r85 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = r85
SPEC.loader.exec_module(r85)

INPUT_SCHEMA = "tradingos.r88_candidate_compile_input.v1"
OUTPUT_SCHEMA = "tradingos.r88_candidate_compile_result.v1"
COMPILER_VERSION = "R88_PROSPECTIVE_CANDIDATE_COMPILER_V1_20260916"
ALLOWED_EVENT_TYPES = {"SFP", "SWEEP_RECLAIM", "LIQUIDITY_SWEEP_RECLAIM", "DISPLACEMENT", "MSS", "CHOCH", "BOS"}
ALLOWED_TFS = {"1M", "1w", "1d", "4h", "1h", "15m", "1m", "aggTrades"}

def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def dec(value: Any, name: str) -> Decimal:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid_{name}") from exc
    if not out.is_finite():
        raise ValueError(f"invalid_{name}")
    return out


def fail(reason: str, **details: Any) -> dict[str, Any]:
    return {"schema": OUTPUT_SCHEMA, "result": "NO_CANDIDATE_FAIL_CLOSED", "reason": reason,
            "details": details, "candidate_compiled": False, "ledger_write_authority": False,
            "can_trade": False, "capital_permission": "DENY"}


def _require_ref(item: dict[str, Any], prefix: str) -> None:
    if not isinstance(item.get("provenance"), str) or not item["provenance"].strip():
        raise ValueError(f"{prefix}_provenance_missing")
    if item.get("source_timeframe") not in ALLOWED_TFS:
        raise ValueError(f"{prefix}_source_timeframe_invalid")

def _validate_ctha(assessment: Any, evidence: dict[str, Any], decision_ms: int) -> dict[str, Any]:
    if not isinstance(assessment, dict):
        raise ValueError("ctha_assessment_missing")
    if assessment.get("r84_evidence_sha256") != sha256_json(evidence):
        raise ValueError("ctha_evidence_binding_mismatch")
    if assessment.get("status") != "PASS" or assessment.get("material_contradiction") is not False:
        raise ValueError("ctha_not_explicit_pass")
    if assessment.get("direction") != "LONG" or assessment.get("bias") not in {"bullish", "range"}:
        raise ValueError("ctha_direction_or_bias_invalid")
    if tuple(assessment.get("mapped_timeframes", [])) != r85.REQUIRED_TFS:
        raise ValueError("ctha_timeframes_invalid")
    known = assessment.get("known_at_ms")
    if type(known) is not int or known <= 0 or known > decision_ms:
        raise ValueError("ctha_known_at_invalid")
    refs = assessment.get("evidence_refs")
    if not isinstance(refs, list) or not refs or any(not isinstance(x, str) or not x for x in refs):
        raise ValueError("ctha_evidence_refs_missing")
    return {"status": "PASS", "material_contradiction": False, "direction": "LONG",
            "mapped_timeframes": list(r85.REQUIRED_TFS), "known_at_ms": known,
            "evidence_refs": refs}


def _events(raw: Any, decision_ms: int) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("structural_event_evidence_missing")
    reaction_ids = {x.get("reaction_id") for x in raw if isinstance(x, dict)}
    if len(reaction_ids) != 1 or None in reaction_ids or "" in reaction_ids:
        raise ValueError("event_reaction_id_invalid")
    reaction_id = next(iter(reaction_ids))
    out: list[dict[str, Any]] = []
    seen_times: set[int] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or item.get("type") not in ALLOWED_EVENT_TYPES:
            raise ValueError(f"event_{i}_type_invalid")
        if item.get("direction") != "LONG":
            raise ValueError(f"event_{i}_direction_invalid")
        _require_ref(item, f"event_{i}")
        ts = item.get("time_ms")
        if type(ts) is not int or ts <= 0 or ts > decision_ms or ts in seen_times:
            raise ValueError(f"event_{i}_time_invalid")
        seen_times.add(ts)
        copied = {"type": item["type"], "direction": "LONG", "reaction_id": reaction_id,
                  "time_ms": ts, "source_timeframe": item["source_timeframe"],
                  "provenance": item["provenance"]}
        if "level" in item:
            copied["level"] = str(dec(item["level"], f"event_{i}_level"))
        out.append(copied)
    out.sort(key=lambda x: x["time_ms"])
    return reaction_id, out


def _confluence(raw: Any, reaction_id: str, decision_ms: int) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("confluence_evidence_missing")
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or item.get("type") not in r85.ALLOWED_CONFLUENCE:
            raise ValueError(f"confluence_{i}_type_invalid")
        _require_ref(item, f"confluence_{i}")
        known = item.get("known_at_ms")
        if type(known) is not int or known <= 0 or known > decision_ms:
            raise ValueError(f"confluence_{i}_time_invalid")
        if item.get("reaction_id") != reaction_id or item.get("direction") != "LONG" or item.get("status") != "PASS":
            raise ValueError(f"confluence_{i}_thesis_link_invalid")
        copied = {k: item[k] for k in item if k in {
            "type", "status", "direction", "reaction_id", "known_at_ms",
            "linked_to_displacement", "confirmed_pivots", "aligned_intervals",
            "reliable", "predefined_before_decision"
        }}
        copied["source_timeframe"] = item["source_timeframe"]
        copied["provenance"] = item["provenance"]
        out.append(copied)
    return out


def _levels(raw: Any, decision_ms: int, ask: Decimal) -> tuple[dict[str, Any], list[dict[str, Any]], str, str]:
    if not isinstance(raw, dict) or not isinstance(raw.get("invalidation"), dict):
        raise ValueError("structural_levels_missing")
    inv = raw["invalidation"]
    _require_ref(inv, "invalidation")
    inv_known = inv.get("known_at_ms")
    inv_price = dec(inv.get("price"), "invalidation_price")
    if type(inv_known) is not int or inv_known <= 0 or inv_known > decision_ms or inv_price >= ask:
        raise ValueError("invalidation_invalid")
    targets_raw = raw.get("targets")
    if not isinstance(targets_raw, list) or not targets_raw:
        raise ValueError("targets_missing")
    targets: list[dict[str, Any]] = []
    for i, item in enumerate(targets_raw):
        if not isinstance(item, dict):
            raise ValueError(f"target_{i}_invalid")
        _require_ref(item, f"target_{i}")
        known = item.get("known_at_ms")
        price = dec(item.get("price"), f"target_{i}_price")
        if type(known) is not int or known <= 0 or known > decision_ms:
            raise ValueError(f"target_{i}_time_invalid")
        targets.append({"price": str(price), "valid": bool(item.get("valid") is True),
                        "known_at_ms": known, "source_timeframe": item["source_timeframe"],
                        "provenance": item["provenance"]})
    overhead = [dec(x["price"], "target_price") for x in targets if x["valid"] and dec(x["price"], "target_price") > ask]
    if not overhead:
        raise ValueError("no_valid_overhead_target")
    nearest = min(overhead)
    objective = raw.get("liquidity_objective")
    if not isinstance(objective, str) or not objective.strip():
        raise ValueError("liquidity_objective_missing")
    return ({"price": str(inv_price), "known_at_ms": inv_known}, targets, str(nearest), objective)


def _explicit_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name}_missing")
    return value


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
    r84_gate = r85.validate_r84_evidence(evidence, decision_ms)
    if r84_gate.get("status") != "PASS":
        return fail("R84_EVIDENCE_INVALID", gate=r84_gate)
    try:
        ctha = _validate_ctha(payload.get("ctha_assessment"), evidence, decision_ms)
        reaction_id, events = _events(payload.get("structural_event_evidence"), decision_ms)
        confluence = _confluence(payload.get("confluence_evidence"), reaction_id, decision_ms)
        ask = dec(quote.get("ask"), "quote_ask")
        invalidation, targets, chosen, objective = _levels(payload.get("structural_levels"), decision_ms, ask)
        cost_model = _explicit_mapping(payload.get("cost_model"), "cost_model")
        risk = _explicit_mapping(payload.get("risk_state"), "risk_state")
    except ValueError as exc:
        return fail("EVIDENCE_COMPILATION_FAILED", error=str(exc))
    assessment = payload["ctha_assessment"]
    p = {"status": "PASS", "direction": "LONG", "bias": assessment["bias"],
         "liquidity_objective": objective, "htf_invalidation": invalidation["price"],
         "reaction_id": reaction_id, "known_at_ms": assessment["known_at_ms"]}
    bundle = {
        "setup_family": r85.SETUP_FAMILY, "direction": "LONG", "ctha": ctha, "p": p,
        "r": {"setup_family": r85.SETUP_FAMILY, "reaction_id": reaction_id, "events": events},
        "c": confluence, "invalidation": invalidation, "targets": targets,
        "chosen_target": chosen, "cost_model": cost_model, "risk": risk,
        "expectancy": payload.get("expectancy", {"state": "RESEARCH_PENDING"}),
    }
    r85_input = {"schema": r85.INPUT_SCHEMA, "decision_time_ms": decision_ms,
                 "r84_evidence": evidence, "decision_bundle": bundle}
    evaluation = r85.evaluate(r85_input)
    if evaluation.get("trial_eligibility") != "R6_SHADOW_TRIAL_ELIGIBLE":
        return fail("R85_NOT_TRIAL_ELIGIBLE", r85_input_sha256=sha256_json(r85_input),
                    evaluation=evaluation, compiled_r85_input=r85_input)
    return {"schema": OUTPUT_SCHEMA, "result": "CANDIDATE_COMPILED_R85_PASS",
            "compiler_version": COMPILER_VERSION, "candidate_compiled": True,
            "r85_input": r85_input, "r85_input_sha256": sha256_json(r85_input),
            "r85_evaluation": evaluation, "r85_evaluation_sha256": sha256_json(evaluation),
            "ledger_write_authority": False, "can_trade": False, "capital_permission": "DENY"}

def main() -> int:
    parser = argparse.ArgumentParser(description="Compile explicit prospective evidence into one frozen R85 input")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        result = compile_candidate(payload)
        text = stable_json(result)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
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

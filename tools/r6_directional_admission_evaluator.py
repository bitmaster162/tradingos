#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = ROOT / "tools" / "r6_admission_evaluator.py"
SPEC = importlib.util.spec_from_file_location("r92_base_r85", BASE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load base R85 evaluator")
base = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = base
SPEC.loader.exec_module(base)

SCHEMA = "tradingos.r92_directional_admission_evaluation.v1"
INPUT_SCHEMA = "tradingos.r92_directional_admission_input.v1"
SETUP_FAMILY = "CURRENT_CANONICAL_LIQUIDITY_SEQUENCE_V2_SYMMETRIC"
DIRECTIONS = {"LONG", "SHORT"}


def gate(status: str, reason: str, **evidence: Any) -> dict[str, Any]:
    return {"status": status, "reason": reason, "evidence": evidence}

def _long_delegate(payload: dict[str, Any]) -> dict[str, Any]:
    mapped = copy.deepcopy(payload)
    mapped["schema"] = base.INPUT_SCHEMA
    bundle = mapped["decision_bundle"]
    bundle["setup_family"] = base.SETUP_FAMILY
    bundle["r"]["setup_family"] = base.SETUP_FAMILY
    out = base.evaluate(mapped)
    out["schema"] = SCHEMA
    out["setup_family"] = SETUP_FAMILY
    return out


def _ctha_short(bundle: dict[str, Any], decision_ms: int) -> dict[str, Any]:
    ctha = bundle.get("ctha")
    if not isinstance(ctha, dict):
        return gate("UNKNOWN", "CTHA_MISSING")
    if ctha.get("status") != "PASS" or ctha.get("material_contradiction") is not False:
        return gate("FAIL", "CTHA_NOT_PASS")
    if ctha.get("direction") != "SHORT":
        return gate("FAIL", "CTHA_DIRECTION_UNSUPPORTED")
    if tuple(ctha.get("mapped_timeframes", [])) != base.REQUIRED_TFS:
        return gate("UNKNOWN", "CTHA_TIMEFRAMES_NOT_EXPLICIT")
    try:
        known = base.before_or_at(ctha.get("known_at_ms"), decision_ms, "ctha_known_at_ms")
    except ValueError:
        return gate("UNKNOWN", "CTHA_TIME_INVALID")
    refs = ctha.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        return gate("UNKNOWN", "CTHA_PROVENANCE_MISSING")
    return gate("PASS", "CTHA_EXPLICIT_NO_MATERIAL_CONTRADICTION", known_at_ms=known)

def _p_short(bundle: dict[str, Any], decision_ms: int) -> dict[str, Any]:
    p = bundle.get("p")
    if not isinstance(p, dict):
        return gate("UNKNOWN", "P_MISSING")
    if p.get("status") != "PASS" or p.get("direction") != "SHORT":
        return gate("FAIL", "P_NOT_PASS")
    for key in ("bias", "liquidity_objective", "htf_invalidation", "reaction_id"):
        if not p.get(key):
            return gate("UNKNOWN", f"P_{key.upper()}_MISSING")
    if p.get("bias") not in {"bearish", "range"}:
        return gate("FAIL", "P_BIAS_CONTRADICTS_SHORT")
    try:
        base.before_or_at(p.get("known_at_ms"), decision_ms, "p_known_at_ms")
    except ValueError:
        return gate("UNKNOWN", "P_TIME_INVALID")
    return gate("PASS", "P_CONTEXT_EXPLICIT", reaction_id=p["reaction_id"])


def _r_short(bundle: dict[str, Any], decision_ms: int) -> dict[str, Any]:
    mapped = copy.deepcopy(bundle)
    mapped["r"]["setup_family"] = base.SETUP_FAMILY
    for event in mapped["r"].get("events", []):
        event["direction"] = "LONG"
        if event.get("type") == "LIQUIDITY_SWEEP_REJECT":
            event["type"] = "LIQUIDITY_SWEEP_RECLAIM"
    return base.eval_r(mapped, decision_ms)


def _c_short(bundle: dict[str, Any], decision_ms: int, reaction_id: str, min_known_ms: int | None) -> dict[str, Any]:
    mapped = copy.deepcopy(bundle)
    for item in mapped.get("c", []):
        item["direction"] = "LONG"
    return base.eval_c(mapped, decision_ms, reaction_id, min_known_ms)

def _invalidation_short(bundle: dict[str, Any], entry: Decimal, decision_ms: int) -> dict[str, Any]:
    inv = bundle.get("invalidation")
    if not isinstance(inv, dict):
        return gate("UNKNOWN", "INVALIDATION_MISSING")
    try:
        price = base.dec(inv.get("price"), "invalidation_price")
        base.before_or_at(inv.get("known_at_ms"), decision_ms, "invalidation_known_at_ms")
    except ValueError as exc:
        return gate("UNKNOWN", str(exc))
    if price <= entry:
        return gate("FAIL", "INVALIDATION_NOT_ABOVE_SHORT_ENTRY", price=str(price))
    return gate("PASS", "INVALIDATION_EXPLICIT", price=str(price))


def _target_short(bundle: dict[str, Any], entry: Decimal, decision_ms: int) -> dict[str, Any]:
    targets = bundle.get("targets")
    chosen_raw = bundle.get("chosen_target")
    if not isinstance(targets, list) or chosen_raw is None:
        return gate("UNKNOWN", "TARGETS_MISSING")
    valid: list[Decimal] = []
    for item in targets:
        if not isinstance(item, dict) or item.get("valid") is not True:
            continue
        try:
            price = base.dec(item.get("price"), "target_price")
            base.before_or_at(item.get("known_at_ms"), decision_ms, "target_known_at_ms")
        except ValueError:
            continue
        if 0 < price < entry:
            valid.append(price)
    if not valid:
        return gate("FAIL", "NO_VALID_DOWNSIDE_TARGET")
    nearest = max(valid)
    try:
        chosen = base.dec(chosen_raw, "chosen_target")
    except ValueError as exc:
        return gate("UNKNOWN", str(exc))
    if chosen != nearest:
        return gate("FAIL", "CHOSEN_TARGET_NOT_NEAREST", nearest=str(nearest), chosen=str(chosen))
    return gate("PASS", "NEAREST_VALID_TARGET", price=str(nearest), candidates=len(valid))

def _economics_short(bundle: dict[str, Any], entry_quote: Decimal, stop: Decimal, target: Decimal) -> dict[str, Any]:
    model = bundle.get("cost_model")
    if not isinstance(model, dict):
        return gate("UNKNOWN", "COST_MODEL_MISSING")
    try:
        fee_in = base.dec(model.get("entry_fee_rate"), "entry_fee_rate")
        fee_out = base.dec(model.get("exit_fee_rate"), "exit_fee_rate")
        slip_in = base.dec(model.get("entry_slippage_bps"), "entry_slippage_bps")
        slip_out = base.dec(model.get("exit_slippage_bps"), "exit_slippage_bps")
    except ValueError as exc:
        return gate("UNKNOWN", str(exc))
    if any(x < 0 for x in (fee_in, fee_out, slip_in, slip_out)):
        return gate("FAIL", "NEGATIVE_COST_INPUT")
    tenk = Decimal("10000")
    entry = entry_quote * (Decimal("1") - slip_in / tenk)
    stop_eff = stop * (Decimal("1") + slip_out / tenk)
    target_eff = target * (Decimal("1") + slip_out / tenk)
    risk = stop_eff - entry + fee_in * entry + fee_out * stop_eff
    reward = entry - target_eff - fee_in * entry - fee_out * target_eff
    if risk <= 0 or reward <= 0:
        return gate("FAIL", "NONPOSITIVE_NET_GEOMETRY", risk=str(risk), reward=str(reward))
    rr = reward / risk
    return gate("PASS", "NET_ECONOMICS_VALID", entry=str(entry), stop=str(stop_eff),
                target=str(target_eff), risk=str(risk), reward=str(reward), net_rr=str(rr))


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != INPUT_SCHEMA:
        raise ValueError("input_schema_invalid")
    evidence = payload.get("r84_evidence")
    bundle = payload.get("decision_bundle")
    if not isinstance(evidence, dict) or not isinstance(bundle, dict):
        raise ValueError("input_payload_incomplete")
    direction = bundle.get("direction")
    if direction not in DIRECTIONS or bundle.get("setup_family") != SETUP_FAMILY:
        raise ValueError("setup_family_or_direction_invalid")
    if direction == "LONG":
        return _long_delegate(payload)
    decision_ms = payload.get("decision_time_ms")
    if type(decision_ms) is not int or decision_ms <= 0:
        raise ValueError("decision_time_ms_invalid")
    gates: dict[str, Any] = {}
    gates["r84"] = base.validate_r84_evidence(evidence, decision_ms)
    gates["setup"] = gate("PASS", "FROZEN_SYMMETRIC_SETUP_FAMILY")
    gates["ctha"] = _ctha_short(bundle, decision_ms)
    gates["p"] = _p_short(bundle, decision_ms)
    gates["r"] = _r_short(bundle, decision_ms)
    reaction_id = None
    if gates["r"].get("status") == "PASS":
        reaction_id = gates["r"]["evidence"].get("reaction_id")
    if gates["p"].get("status") == "PASS" and reaction_id is not None:
        p_reaction = gates["p"]["evidence"].get("reaction_id")
        gates["reaction_link"] = gate("PASS", "P_R_REACTION_LINKED") if p_reaction == reaction_id else gate("FAIL", "P_R_REACTION_ID_MISMATCH")
    else:
        gates["reaction_link"] = gate("UNKNOWN", "P_R_REACTION_LINK_UNPROVEN")
    reaction_start_ms = None
    if gates["r"].get("status") == "PASS":
        reaction_start_ms = gates["r"]["evidence"]["ordered"][0][1]
    gates["c"] = _c_short(bundle, decision_ms, reaction_id or "", reaction_start_ms)

    entry_quote = None
    if gates["r84"].get("status") == "PASS":
        try:
            entry_quote = base.dec(evidence["quote"]["bid"], "quote_bid")
        except Exception:
            entry_quote = None
    if entry_quote is None:
        gates["invalidation"] = gate("UNKNOWN", "NO_VALID_ENTRY_QUOTE")
        gates["target"] = gate("UNKNOWN", "NO_VALID_ENTRY_QUOTE")
        gates["economics"] = gate("UNKNOWN", "NO_VALID_ENTRY_QUOTE")
    else:
        gates["invalidation"] = _invalidation_short(bundle, entry_quote, decision_ms)
        gates["target"] = _target_short(bundle, entry_quote, decision_ms)
        if gates["invalidation"].get("status") == "PASS" and gates["target"].get("status") == "PASS":
            stop = base.dec(gates["invalidation"]["evidence"]["price"], "stop")
            target = base.dec(gates["target"]["evidence"]["price"], "target")
            gates["economics"] = _economics_short(bundle, entry_quote, stop, target)
        else:
            gates["economics"] = gate("UNKNOWN", "GEOMETRY_NOT_PASS")
    gates["risk"] = base.eval_risk(bundle, decision_ms)
    gates["expectancy"], a_grade_expectancy = base.eval_expectancy(bundle)
    mandatory = ("r84", "setup", "ctha", "p", "r", "reaction_link", "c",
                 "invalidation", "target", "economics", "risk", "expectancy")
    passed = all(base._mandatory_pass(gates[name]) for name in mandatory)
    return {
        "schema": SCHEMA, "symbol": evidence.get("symbol"), "setup_family": SETUP_FAMILY,
        "direction": "SHORT", "decision_time_ms": decision_ms,
        "trial_eligibility": "R6_SHADOW_TRIAL_ELIGIBLE" if passed else "NOT_ELIGIBLE_FAIL_CLOSED",
        "a_grade_candidate": bool(passed and a_grade_expectancy), "gates": gates,
        "can_trade": False, "capital_permission": "DENY",
        "calibration_registration_candidate": passed, "ledger_write_authority": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate R92 symmetric LONG/SHORT admission bundle")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = evaluate(json.loads(args.input.read_text(encoding="utf-8")))
        text = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if result.get("trial_eligibility") == "R6_SHADOW_TRIAL_ELIGIBLE" else 3
    except Exception as exc:
        print(json.dumps({"schema": SCHEMA, "result": "ERROR", "error": str(exc),
                          "can_trade": False, "capital_permission": "DENY"}, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

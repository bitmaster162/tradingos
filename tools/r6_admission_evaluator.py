#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
R82_PATH = ROOT / "tools" / "binance_spot_event_time_quote_collector.py"
SPEC = importlib.util.spec_from_file_location("r82_quote", R82_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load R82 quote validator")
r82 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = r82
SPEC.loader.exec_module(r82)

SCHEMA = "tradingos.r85_admission_evaluation.v1"
INPUT_SCHEMA = "tradingos.r85_admission_input.v1"
R84_SYMBOL_SCHEMA = "tradingos.r6_symbol_admission_evidence.v1"
SETUP_FAMILY = "CURRENT_CANONICAL_LIQUIDITY_SEQUENCE_V1"
MAX_QUOTE_AGE_MS = 2000
REQUIRED_TFS = ("1M", "1w", "1d", "4h", "1h", "15m")
ALLOWED_CONFLUENCE = {
    "FVG", "RSI_DIVERGENCE", "SMT", "FLOW", "DERIVATIVES", "HTF_GEOMETRY"
}


def dec(value: Any, field: str) -> Decimal:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"invalid_{field}") from exc
    if not out.is_finite():
        raise ValueError(f"invalid_{field}")
    return out


def gate(status: str, reason: str, **evidence: Any) -> dict[str, Any]:
    return {"status": status, "reason": reason, "evidence": evidence}


def before_or_at(value: Any, limit: int, field: str) -> int:
    if type(value) is not int or value <= 0 or value > limit:
        raise ValueError(f"invalid_{field}")
    return value


def direction_matches(value: Any) -> bool:
    return value == "LONG"
def validate_r84_evidence(payload: dict[str, Any], decision_time_ms: int) -> dict[str, Any]:
    if payload.get("schema") != R84_SYMBOL_SCHEMA:
        return gate("FAIL", "R84_SYMBOL_SCHEMA_INVALID")
    symbol = payload.get("symbol")
    if not isinstance(symbol, str) or not symbol.endswith("USDT"):
        return gate("FAIL", "SYMBOL_INVALID")
    if payload.get("can_trade") is not False or payload.get("capital_permission") != "DENY":
        return gate("FAIL", "AUTHORITY_BOUNDARY_INVALID")
    target = payload.get("target_ctha")
    refs = payload.get("reference_ctha")
    if not isinstance(target, dict) or any(tf not in target for tf in REQUIRED_TFS):
        return gate("FAIL", "TARGET_CTHA_INCOMPLETE")
    if not isinstance(refs, dict) or set(refs) != {"BTCUSDT", "ETHUSDT"}:
        return gate("FAIL", "REFERENCE_CTHA_INVALID")
    if any(any(tf not in refs[s] for tf in REQUIRED_TFS) for s in refs):
        return gate("FAIL", "REFERENCE_CTHA_INCOMPLETE")
    quote = payload.get("quote")
    if not isinstance(quote, dict) or quote.get("symbol") != symbol:
        return gate("FAIL", "QUOTE_SYMBOL_MISMATCH")
    if quote.get("decision_time_ms") != decision_time_ms:
        return gate("FAIL", "QUOTE_DECISION_TIME_BINDING_MISMATCH")
    try:
        age = r82.validate_quote_at_decision(
            quote, decision_time_ms=decision_time_ms, max_age_ms=MAX_QUOTE_AGE_MS
        )
    except Exception as exc:
        return gate("FAIL", f"QUOTE_GATE:{exc}")
    if quote.get("can_trade") is not False or quote.get("capital_permission") != "DENY":
        return gate("FAIL", "QUOTE_AUTHORITY_BOUNDARY_INVALID")
    return gate("PASS", "R84_EVIDENCE_VALID", symbol=symbol, quote_age_ms=age)
def eval_ctha(bundle: dict[str, Any], decision_time_ms: int) -> dict[str, Any]:
    ctha = bundle.get("ctha")
    if not isinstance(ctha, dict):
        return gate("UNKNOWN", "CTHA_MISSING")
    if ctha.get("status") != "PASS":
        status = "FAIL" if ctha.get("status") == "FAIL" else "UNKNOWN"
        return gate(status, "CTHA_NOT_PASS")
    if ctha.get("material_contradiction") is not False:
        status = "FAIL" if ctha.get("material_contradiction") is True else "UNKNOWN"
        return gate(status, "CTHA_CONTRADICTION")
    if ctha.get("direction") != "LONG":
        return gate("FAIL", "CTHA_DIRECTION_UNSUPPORTED")
    mapped = ctha.get("mapped_timeframes")
    if not isinstance(mapped, list) or tuple(mapped) != REQUIRED_TFS:
        return gate("UNKNOWN", "CTHA_TIMEFRAMES_NOT_EXPLICIT")
    try:
        known_at = before_or_at(ctha.get("known_at_ms"), decision_time_ms, "ctha_known_at_ms")
    except ValueError:
        return gate("UNKNOWN", "CTHA_TIME_INVALID")
    refs = ctha.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        return gate("UNKNOWN", "CTHA_PROVENANCE_MISSING")
    return gate("PASS", "CTHA_EXPLICIT_NO_MATERIAL_CONTRADICTION", known_at_ms=known_at)


def eval_p(bundle: dict[str, Any], decision_time_ms: int) -> dict[str, Any]:
    p = bundle.get("p")
    if not isinstance(p, dict):
        return gate("UNKNOWN", "P_MISSING")
    if p.get("status") != "PASS" or p.get("direction") != "LONG":
        status = "FAIL" if p.get("status") == "FAIL" else "UNKNOWN"
        return gate(status, "P_NOT_PASS")
    for key in ("bias", "liquidity_objective", "htf_invalidation", "reaction_id"):
        if not p.get(key):
            return gate("UNKNOWN", f"P_{key.upper()}_MISSING")
    if p.get("bias") not in {"bullish", "range"}:
        return gate("FAIL", "P_BIAS_CONTRADICTS_LONG")
    try:
        before_or_at(p.get("known_at_ms"), decision_time_ms, "p_known_at_ms")
    except ValueError:
        return gate("UNKNOWN", "P_TIME_INVALID")
    return gate("PASS", "P_CONTEXT_EXPLICIT", reaction_id=p["reaction_id"])


def _event_role(event: dict[str, Any]) -> str | None:
    kind = event.get("type")
    if kind in {"SFP", "SWEEP_RECLAIM", "LIQUIDITY_SWEEP_RECLAIM"}:
        return "LIQUIDITY"
    if kind == "DISPLACEMENT":
        return "DISPLACEMENT"
    if kind in {"MSS", "CHOCH"}:
        return "SHIFT"
    if kind == "BOS":
        return "CONFIRMATION"
    return None
def eval_r(bundle: dict[str, Any], decision_time_ms: int) -> dict[str, Any]:
    r = bundle.get("r")
    if not isinstance(r, dict):
        return gate("UNKNOWN", "R_MISSING")
    if r.get("setup_family") != SETUP_FAMILY:
        return gate("UNKNOWN", "R_SETUP_FAMILY_UNSUPPORTED")
    events = r.get("events")
    if not isinstance(events, list):
        return gate("UNKNOWN", "R_EVENTS_MISSING")
    reaction_id = r.get("reaction_id")
    if not reaction_id:
        return gate("UNKNOWN", "R_REACTION_ID_MISSING")
    required = ("LIQUIDITY", "DISPLACEMENT", "SHIFT", "CONFIRMATION")
    matched: list[tuple[str, int]] = []
    for event in events:
        if not isinstance(event, dict) or event.get("direction") != "LONG":
            continue
        if event.get("reaction_id") != reaction_id:
            continue
        role = _event_role(event)
        if role is None:
            continue
        try:
            ts = before_or_at(event.get("time_ms"), decision_time_ms, "r_event_time_ms")
        except ValueError:
            continue
        matched.append((role, ts))
    cursor = -1
    ordered: list[tuple[str, int]] = []
    last_ts = -1
    for role in required:
        found = None
        for idx in range(cursor + 1, len(matched)):
            cand_role, cand_ts = matched[idx]
            if cand_role == role and cand_ts > last_ts:
                found = (idx, cand_ts)
                break
        if found is None:
            return gate("FAIL", f"R_SEQUENCE_MISSING_{role}")
        cursor, last_ts = found
        ordered.append((role, last_ts))
    return gate("PASS", "R_CHRONOLOGY_VALID", reaction_id=reaction_id, ordered=ordered)


def _valid_c(item: dict[str, Any], reaction_id: str, decision_time_ms: int, min_known_ms: int | None) -> bool:
    if item.get("type") not in ALLOWED_CONFLUENCE:
        return False
    if item.get("status") != "PASS" or item.get("direction") != "LONG":
        return False
    if item.get("reaction_id") != reaction_id:
        return False
    try:
        c_time = before_or_at(item.get("known_at_ms"), decision_time_ms, "c_known_at_ms")
        if min_known_ms is not None and c_time < min_known_ms:
            return False
    except ValueError:
        return False
    kind = item["type"]
    if kind == "FVG" and item.get("linked_to_displacement") is not True:
        return False
    if kind == "RSI_DIVERGENCE" and item.get("confirmed_pivots") is not True:
        return False
    if kind == "SMT" and item.get("aligned_intervals") is not True:
        return False
    if kind in {"FLOW", "DERIVATIVES"} and item.get("reliable") is not True:
        return False
    if kind == "HTF_GEOMETRY" and item.get("predefined_before_decision") is not True:
        return False
    return True


def eval_c(bundle: dict[str, Any], decision_time_ms: int, reaction_id: str, min_known_ms: int | None) -> dict[str, Any]:
    items = bundle.get("c")
    if not isinstance(items, list):
        return gate("UNKNOWN", "C_MISSING")
    valid = [item for item in items if isinstance(item, dict) and _valid_c(item, reaction_id, decision_time_ms, min_known_ms)]
    if not valid:
        return gate("FAIL", "C_NO_VALID_SAME_THESIS_CONFLUENCE")
    return gate("PASS", "C_VALID", count=len(valid), types=[x["type"] for x in valid])


def eval_invalidation(bundle: dict[str, Any], entry: Decimal, decision_time_ms: int) -> dict[str, Any]:
    inv = bundle.get("invalidation")
    if not isinstance(inv, dict):
        return gate("UNKNOWN", "INVALIDATION_MISSING")
    try:
        price = dec(inv.get("price"), "invalidation_price")
        before_or_at(inv.get("known_at_ms"), decision_time_ms, "invalidation_known_at_ms")
    except ValueError as exc:
        return gate("UNKNOWN", str(exc))
    if price <= 0 or price >= entry:
        return gate("FAIL", "INVALIDATION_NOT_BELOW_LONG_ENTRY", price=str(price))
    return gate("PASS", "INVALIDATION_EXPLICIT", price=str(price))
def eval_target(bundle: dict[str, Any], entry: Decimal, decision_time_ms: int) -> dict[str, Any]:
    targets = bundle.get("targets")
    chosen_raw = bundle.get("chosen_target")
    if not isinstance(targets, list) or chosen_raw is None:
        return gate("UNKNOWN", "TARGETS_MISSING")
    valid: list[Decimal] = []
    for item in targets:
        if not isinstance(item, dict) or item.get("valid") is not True:
            continue
        try:
            price = dec(item.get("price"), "target_price")
            before_or_at(item.get("known_at_ms"), decision_time_ms, "target_known_at_ms")
        except ValueError:
            continue
        if price > entry:
            valid.append(price)
    if not valid:
        return gate("FAIL", "NO_VALID_OVERHEAD_TARGET")
    nearest = min(valid)
    try:
        chosen = dec(chosen_raw, "chosen_target")
    except ValueError as exc:
        return gate("UNKNOWN", str(exc))
    if chosen != nearest:
        return gate("FAIL", "CHOSEN_TARGET_NOT_NEAREST", nearest=str(nearest), chosen=str(chosen))
    return gate("PASS", "NEAREST_VALID_TARGET", price=str(nearest), candidates=len(valid))


def eval_expectancy(bundle: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    exp = bundle.get("expectancy")
    if not isinstance(exp, dict):
        return gate("UNKNOWN", "EXPECTANCY_MISSING"), False
    state = exp.get("state")
    if state == "ROBUST_POSITIVE":
        return gate("PASS", "EXPECTANCY_ROBUST_POSITIVE"), True
    if state == "RESEARCH_PENDING":
        return gate("PASS_RESEARCH_ONLY", "EXPECTANCY_RESEARCH_PENDING"), False
    if state in {"NONPOSITIVE", "REJECTED"}:
        return gate("FAIL", f"EXPECTANCY_{state}"), False
    return gate("UNKNOWN", "EXPECTANCY_STATE_UNKNOWN"), False
def eval_economics(
    bundle: dict[str, Any], entry_quote: Decimal, stop: Decimal, target: Decimal
) -> dict[str, Any]:
    model = bundle.get("cost_model")
    if not isinstance(model, dict):
        return gate("UNKNOWN", "COST_MODEL_MISSING")
    try:
        fee_in = dec(model.get("entry_fee_rate"), "entry_fee_rate")
        fee_out = dec(model.get("exit_fee_rate"), "exit_fee_rate")
        slip_in_bps = dec(model.get("entry_slippage_bps"), "entry_slippage_bps")
        slip_out_bps = dec(model.get("exit_slippage_bps"), "exit_slippage_bps")
    except ValueError as exc:
        return gate("UNKNOWN", str(exc))
    if any(x < 0 for x in (fee_in, fee_out, slip_in_bps, slip_out_bps)):
        return gate("FAIL", "NEGATIVE_COST_INPUT")
    tenk = Decimal("10000")
    entry = entry_quote * (Decimal("1") + slip_in_bps / tenk)
    stop_eff = stop * (Decimal("1") - slip_out_bps / tenk)
    target_eff = target * (Decimal("1") - slip_out_bps / tenk)
    risk = entry - stop_eff + fee_in * entry + fee_out * stop_eff
    reward = target_eff - entry - fee_in * entry - fee_out * target_eff
    if risk <= 0 or reward <= 0:
        return gate("FAIL", "NONPOSITIVE_NET_GEOMETRY", risk=str(risk), reward=str(reward))
    rr = reward / risk
    return gate(
        "PASS", "NET_ECONOMICS_VALID", entry=str(entry), stop=str(stop_eff),
        target=str(target_eff), risk=str(risk), reward=str(reward), net_rr=str(rr)
    )
def eval_risk(bundle: dict[str, Any], decision_time_ms: int) -> dict[str, Any]:
    risk = bundle.get("risk")
    if not isinstance(risk, dict):
        return gate("UNKNOWN", "RISK_STATE_MISSING")
    try:
        proposed = dec(risk.get("proposed_risk_pct"), "proposed_risk_pct")
        aggregate = dec(risk.get("aggregate_open_risk_pct"), "aggregate_open_risk_pct")
        dd = dec(risk.get("drawdown_pct"), "drawdown_pct")
        base_unit = dec(risk.get("base_risk_unit_pct"), "base_risk_unit_pct")
    except ValueError as exc:
        return gate("UNKNOWN", str(exc))
    if proposed <= 0 or proposed > Decimal("1"):
        return gate("FAIL", "RISK_PER_TRADE_OUT_OF_RANGE", proposed=str(proposed))
    if aggregate < 0 or aggregate + proposed > Decimal("3"):
        return gate("FAIL", "AGGREGATE_RISK_LIMIT", aggregate=str(aggregate), proposed=str(proposed))
    if dd > Decimal("10"):
        return gate("FAIL", "DRAWDOWN_HARD_STOP", drawdown_pct=str(dd))
    entries = risk.get("entries_today")
    if type(entries) is not int or entries < 0:
        return gate("UNKNOWN", "ENTRIES_TODAY_INVALID")
    if entries >= 20:
        return gate("FAIL", "DAILY_ENTRY_CEILING")
    losses = risk.get("consecutive_net_losses")
    pause_until = risk.get("pause_until_ms", 0)
    if type(losses) is not int or losses < 0 or type(pause_until) is not int:
        return gate("UNKNOWN", "LOSS_PAUSE_STATE_INVALID")
    if losses >= 3 and pause_until > decision_time_ms:
        return gate("FAIL", "THREE_LOSS_PAUSE_ACTIVE", pause_until_ms=pause_until)
    weekly = risk.get("completed_week_expectancy_r")
    if weekly is not None:
        try:
            weekly_dec = dec(weekly, "completed_week_expectancy_r")
        except ValueError as exc:
            return gate("UNKNOWN", str(exc))
        if weekly_dec < 0 and proposed > base_unit / Decimal("2"):
            return gate("FAIL", "NEGATIVE_WEEK_RISK_NOT_HALVED")
    op = risk.get("operational_risk_state")
    if op != "PASS":
        return gate("FAIL" if op == "FAIL" else "UNKNOWN", "OPERATIONAL_RISK_NOT_PASS")
    return gate("PASS", "RISK_FIREWALL_PASS", proposed_risk_pct=str(proposed))


def _mandatory_pass(result: dict[str, Any]) -> bool:
    return result.get("status") in {"PASS", "PASS_RESEARCH_ONLY"}


def evaluate(input_payload: dict[str, Any]) -> dict[str, Any]:
    if input_payload.get("schema") != INPUT_SCHEMA:
        raise ValueError("input_schema_invalid")
    symbol_evidence = input_payload.get("r84_evidence")
    bundle = input_payload.get("decision_bundle")
    if not isinstance(symbol_evidence, dict) or not isinstance(bundle, dict):
        raise ValueError("input_payload_incomplete")
    decision_time_ms = input_payload.get("decision_time_ms")
    if type(decision_time_ms) is not int or decision_time_ms <= 0:
        raise ValueError("decision_time_ms_invalid")
    symbol = symbol_evidence.get("symbol")
    gates: dict[str, Any] = {}
    gates["r84"] = validate_r84_evidence(symbol_evidence, decision_time_ms)
    if bundle.get("setup_family") != SETUP_FAMILY or bundle.get("direction") != "LONG":
        gates["setup"] = gate("UNKNOWN", "UNSUPPORTED_OR_UNFROZEN_SETUP_FAMILY")
    else:
        gates["setup"] = gate("PASS", "FROZEN_SETUP_FAMILY")
    gates["ctha"] = eval_ctha(bundle, decision_time_ms)
    gates["p"] = eval_p(bundle, decision_time_ms)
    gates["r"] = eval_r(bundle, decision_time_ms)
    reaction_id = None
    if gates["r"].get("status") == "PASS":
        reaction_id = gates["r"]["evidence"].get("reaction_id")
    if gates["p"].get("status") == "PASS" and reaction_id is not None:
        p_reaction = gates["p"]["evidence"].get("reaction_id")
        if p_reaction != reaction_id:
            gates["reaction_link"] = gate("FAIL", "P_R_REACTION_ID_MISMATCH")
        else:
            gates["reaction_link"] = gate("PASS", "P_R_REACTION_LINKED")
    else:
        gates["reaction_link"] = gate("UNKNOWN", "P_R_REACTION_LINK_UNPROVEN")
    reaction_start_ms = None
    if gates["r"].get("status") == "PASS":
        reaction_start_ms = gates["r"]["evidence"]["ordered"][0][1]
    gates["c"] = eval_c(bundle, decision_time_ms, reaction_id or "", reaction_start_ms)

    entry_quote = None
    if gates["r84"].get("status") == "PASS":
        try:
            entry_quote = dec(symbol_evidence["quote"]["ask"], "quote_ask")
        except Exception:
            pass
    if entry_quote is None:
        gates["invalidation"] = gate("UNKNOWN", "NO_VALID_ENTRY_QUOTE")
        gates["target"] = gate("UNKNOWN", "NO_VALID_ENTRY_QUOTE")
        gates["economics"] = gate("UNKNOWN", "NO_VALID_ENTRY_QUOTE")
    else:
        gates["invalidation"] = eval_invalidation(bundle, entry_quote, decision_time_ms)
        gates["target"] = eval_target(bundle, entry_quote, decision_time_ms)
        if gates["invalidation"].get("status") == "PASS" and gates["target"].get("status") == "PASS":
            stop = dec(gates["invalidation"]["evidence"]["price"], "stop")
            target = dec(gates["target"]["evidence"]["price"], "target")
            gates["economics"] = eval_economics(bundle, entry_quote, stop, target)
        else:
            gates["economics"] = gate("UNKNOWN", "GEOMETRY_NOT_PASS")
    gates["risk"] = eval_risk(bundle, decision_time_ms)
    gates["expectancy"], a_grade_expectancy = eval_expectancy(bundle)

    mandatory = (
        "r84", "setup", "ctha", "p", "r", "reaction_link", "c",
        "invalidation", "target", "economics", "risk", "expectancy",
    )
    passed = all(_mandatory_pass(gates[name]) for name in mandatory)
    final_status = "R6_SHADOW_TRIAL_ELIGIBLE" if passed else "NOT_ELIGIBLE_FAIL_CLOSED"
    a_grade = passed and a_grade_expectancy
    return {
        "schema": SCHEMA,
        "symbol": symbol,
        "setup_family": bundle.get("setup_family"),
        "direction": bundle.get("direction"),
        "decision_time_ms": decision_time_ms,
        "trial_eligibility": final_status,
        "a_grade_candidate": a_grade,
        "gates": gates,
        "can_trade": False,
        "capital_permission": "DENY",
        "calibration_registration_candidate": passed,
        "ledger_write_authority": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate one frozen R6 shadow admission bundle")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        result = evaluate(payload)
        text = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0
    except Exception as exc:
        print(json.dumps({
            "schema": SCHEMA, "result": "ERROR", "error": str(exc),
            "can_trade": False, "capital_permission": "DENY"
        }, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import copy, hashlib, importlib.util, json, sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod; spec.loader.exec_module(mod); return mod

r96 = _load("r97_r96", ROOT / "tools" / "r6_decision_continuity_gate.py")
cap = r96.cap
SCHEMA = "tradingos.r97_paper_fill_settlement.v1"
CONTRACT_ID = "R97_PAPER_FILL_SETTLEMENT_V1_20260916"

def stable_json(v: Any) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), allow_nan=False)

def sha256_json(v: Any) -> str:
    return hashlib.sha256(stable_json(v).encode()).hexdigest()

def fail(reason: str, **details: Any) -> dict[str, Any]:
    return {"schema": SCHEMA, "contract_id": CONTRACT_ID, "status": "FAIL_CLOSED", "reason": reason,
            "details": details, "ledger_write_authority": False, "can_trade": False, "capital_permission": "DENY"}

def _direction(row: dict[str, Any]) -> str:
    d = str(row.get("CURRENT_Decision", ""))
    if "LONG" in d: return "LONG"
    if "SHORT" in d: return "SHORT"
    raise ValueError("direction_unknown")

def _cost(runtime_state: dict[str, Any]) -> dict[str, Decimal]:
    c = runtime_state.get("cost_model")
    if not isinstance(c, dict): raise ValueError("cost_model_missing")
    out = {k: cap.dec(c.get(k), k, allow_zero=True) for k in
           ("entry_fee_rate","exit_fee_rate","entry_slippage_bps","exit_slippage_bps")}
    if out["entry_slippage_bps"] != Decimal("2") or out["exit_slippage_bps"] != Decimal("2"):
        raise ValueError("slippage_contract_mismatch")
    return out

def _settled_entry(direction: str, cont: dict[str, Any], slip_bps: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    vwap = cap.dec(cont.get("fresh_entry_depth_vwap"), "fresh_entry_depth_vwap")
    impact = cap.dec(cont.get("fresh_entry_impact_bps"), "fresh_entry_impact_bps", allow_zero=True)
    if impact < 0 or impact > slip_bps: raise ValueError("depth_impact_outside_budget")
    best = cap.dec(cont.get("fresh_best_ask" if direction == "LONG" else "fresh_best_bid"), "fresh_best_side")
    residual = slip_bps - impact
    if direction == "LONG": settled = vwap + best * residual / Decimal("10000")
    else: settled = vwap - best * residual / Decimal("10000")
    if settled <= 0: raise ValueError("settled_entry_nonpositive")
    return settled, vwap, residual

def settle_candidate(candidate: dict[str, Any], runtime_state: dict[str, Any]) -> dict[str, Any]:
    try:
        if candidate.get("registration_candidate") is not True or candidate.get("ledger_write_authority") is not False:
            return fail("REGISTRATION_CANDIDATE_INVALID")
        if candidate.get("decision_continuity_gate_pass") is not True:
            return fail("R96_PASS_REQUIRED")
        row = copy.deepcopy(candidate.get("row_values"))
        if not isinstance(row, dict): return fail("REGISTRATION_ROW_MISSING")
        cont = candidate.get("decision_continuity")
        if not isinstance(cont, dict) or cont.get("status") != "PASS": return fail("R96_EVIDENCE_MISSING")
        direction = _direction(row); cost = _cost(runtime_state)
        quantity = cap.dec(cont.get("frozen_quantity"), "frozen_quantity")
        settled, depth_vwap, residual = _settled_entry(direction, cont, cost["entry_slippage_bps"])
        stop = cap.dec(row.get("CURRENT_Stop"), "stop"); target = cap.dec(row.get("CURRENT_Target"), "target")
    except Exception as exc:
        return fail("SETTLEMENT_INPUT_INVALID", error=str(exc))

    if direction == "LONG" and not (stop < settled < target): return fail("SETTLED_ENTRY_OUTSIDE_GEOMETRY")
    if direction == "SHORT" and not (target < settled < stop): return fail("SETTLED_ENTRY_OUTSIDE_GEOMETRY")
    exit_slip = cost["exit_slippage_bps"] / Decimal("10000")
    if direction == "LONG":
        stop_eff = stop * (Decimal("1") - exit_slip); target_eff = target * (Decimal("1") - exit_slip)
        unit_risk = settled - stop_eff + cost["entry_fee_rate"] * settled + cost["exit_fee_rate"] * stop_eff
        unit_reward = target_eff - settled - cost["entry_fee_rate"] * settled - cost["exit_fee_rate"] * target_eff
    else:
        stop_eff = stop * (Decimal("1") + exit_slip); target_eff = target * (Decimal("1") + exit_slip)
        unit_risk = stop_eff - settled + cost["entry_fee_rate"] * settled + cost["exit_fee_rate"] * stop_eff
        unit_reward = settled - target_eff - cost["entry_fee_rate"] * settled - cost["exit_fee_rate"] * target_eff
    if unit_risk <= 0 or unit_reward <= 0: return fail("SETTLED_NET_GEOMETRY_NONPOSITIVE")
    risk_budget = cap.RESEARCH_EQUITY_USDT * cap.dec(runtime_state["risk_state"]["proposed_risk_pct"], "proposed_risk_pct") / Decimal("100")
    actual_risk = quantity * unit_risk
    if actual_risk > risk_budget: return fail("SETTLED_RISK_BUDGET_EXCEEDED", actual=str(actual_risk), budget=str(risk_budget))

    net_rr = unit_reward / unit_risk
    try:
        trigger = json.loads(row["CURRENT_Trigger_Spec"])
        spec = trigger.get("settlement_spec")
        if not isinstance(spec, dict): raise ValueError("settlement_spec_missing")
        spec["initial_planned_risk_per_unit"] = str(unit_risk)
        spec["entry_fill_basis"] = "R97_FRESH_DEPTH_VWAP_PLUS_RESIDUAL_TO_FROZEN_2BPS_BUDGET"
        spec["entry_depth_vwap"] = str(depth_vwap)
        spec["entry_depth_impact_bps"] = str(cont["fresh_entry_impact_bps"])
        spec["entry_residual_slippage_bps"] = str(residual)
        spec["settled_entry_price"] = str(settled)
        trigger["settlement_spec"] = spec
        evidence = {"contract_id": CONTRACT_ID, "direction": direction, "quantity": str(quantity),
                    "fresh_decision_time_ms": int(cont["fresh_decision_time_ms"]),
                    "fresh_quote_provenance_sha256": cont["fresh_quote_provenance_sha256"],
                    "fresh_book_provenance_sha256": cont["fresh_book_provenance_sha256"],
                    "depth_vwap": str(depth_vwap), "depth_impact_bps": str(cont["fresh_entry_impact_bps"]),
                    "residual_slippage_bps": str(residual), "settled_entry_price": str(settled),
                    "unit_risk": str(unit_risk), "unit_reward": str(unit_reward), "net_rr": str(net_rr),
                    "actual_planned_risk_usdt": str(actual_risk), "risk_budget_usdt": str(risk_budget)}
        evidence["provenance_sha256"] = sha256_json(evidence); trigger["r97_paper_fill_settlement"] = evidence
    except Exception as exc:
        return fail("TRIGGER_SETTLEMENT_UPDATE_FAILED", error=str(exc))

    fresh_ms = int(cont["fresh_decision_time_ms"])
    fresh_iso = datetime.fromtimestamp(fresh_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    row["CURRENT_Trigger_Spec"] = stable_json(trigger)
    row["CURRENT_Entry_Price"] = float(settled)
    row["CURRENT_Entry_At_UTC"] = fresh_iso
    row["Registered_At_UTC"] = fresh_iso
    row["RR_Net"] = float(net_rr)
    row["CURRENT_Decision"] = f"ENTER_{direction}_AT_R97_SETTLED_FILL"
    row["Operational_Risk"] = str(row.get("Operational_Risk", "")) + "; R97_FILL_SETTLEMENT_PASS"
    row["Provenance_State"] = str(row.get("Provenance_State", "")) + f"; R97_FILL={evidence['provenance_sha256']}"
    row["Notes"] = str(row.get("Notes", "")) + (
        f"; R97 depthVWAP={depth_vwap} residualSlipBps={residual} settledEntry={settled} "
        f"actualRisk={actual_risk}/{risk_budget}"
    )
    out = copy.deepcopy(candidate)
    out["row_values"] = row
    out["paper_fill_settlement"] = {"status": "PASS", "reason": "PAPER_FILL_SETTLEMENT_PASS", **evidence}
    out["registration_row_sha256"] = sha256_json(row)
    out["paper_fill_settlement_gate_pass"] = True
    out["ledger_write_authority"] = False; out["can_trade"] = False; out["capital_permission"] = "DENY"
    return out

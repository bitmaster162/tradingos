#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
from decimal import Decimal
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

r95 = _load("r96_r95", ROOT / "tools" / "r6_persistent_capacity_sweep.py")
cap = r95.r94.cap
integrity = r95.r94.r93.gate
SCHEMA = "tradingos.r96_decision_continuity.v1"
CONTRACT_ID = "R96_DECISION_CONTINUITY_GATE_V1_20260916"
MAX_CONTINUITY_GAP_MS = 2000
MAX_ADVERSE_DRIFT_BPS = Decimal("2")

def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fail(reason: str, **details: Any) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "contract_id": CONTRACT_ID,
        "status": "FAIL_CLOSED",
        "reason": reason,
        "details": details,
        "ledger_write_authority": False,
        "can_trade": False,
        "capital_permission": "DENY",
    }


def _direction(candidate: dict[str, Any]) -> str:
    return cap._direction(candidate)


def _validate_candidate(candidate: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    if candidate.get("registration_candidate") is not True or candidate.get("ledger_write_authority") is not False:
        raise ValueError("registration_candidate_invalid")
    if candidate.get("capacity_gate_pass") is not True or candidate.get("liquidity_persistence_gate_pass") is not True:
        raise ValueError("upstream_gate_not_passed")
    row = candidate.get("row_values")
    capacity = candidate.get("execution_capacity")
    if not isinstance(row, dict) or not isinstance(capacity, dict) or capacity.get("status") != "PASS":
        raise ValueError("candidate_capacity_missing")
    return row, capacity, _direction(candidate)

def _fixed_quantity_capacity(direction: str, quantity: Decimal, quote: dict[str, Any], book: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    symbol = str(quote.get("symbol"))
    cap.r82.validate_quote_at_decision(quote, decision_time_ms=quote.get("decision_time_ms"), max_age_ms=2000)
    cap.validate_capacity_book(book, quote)
    cap.validate_rules(rules, symbol)
    step = cap.dec(rules.get("qty_step"), "qty_step")
    min_qty = cap.dec(rules.get("min_qty"), "min_qty", allow_zero=True)
    max_qty = cap.dec(rules.get("max_qty"), "max_qty")
    min_notional = cap.dec(rules.get("min_notional"), "min_notional", allow_zero=True)
    max_notional_raw = rules.get("max_notional")
    max_notional = None if max_notional_raw is None else cap.dec(max_notional_raw, "max_notional")
    if quantity <= 0 or cap._floor_to_step(quantity, step) != quantity:
        raise ValueError("frozen_quantity_not_on_venue_step")
    if quantity < min_qty or quantity > max_qty:
        raise ValueError("frozen_quantity_outside_venue_limits")
    bid = cap.dec(quote.get("bid"), "best_bid")
    ask = cap.dec(quote.get("ask"), "best_ask")
    entry_side = ask if direction == "LONG" else bid
    notional = quantity * entry_side
    if notional < min_notional or (max_notional is not None and notional > max_notional):
        raise ValueError("frozen_quantity_notional_invalid")
    buy_vwap, _ = cap._vwap(book.get("ask_levels"), quantity)
    sell_vwap, _ = cap._vwap(book.get("bid_levels"), quantity)
    buy_impact = cap._impact_bps("BUY", ask, buy_vwap)
    sell_impact = cap._impact_bps("SELL", bid, sell_vwap)
    entry_impact = buy_impact if direction == "LONG" else sell_impact
    exit_impact = sell_impact if direction == "LONG" else buy_impact
    return {"notional_usdt": notional, "entry_impact_bps": entry_impact, "exit_impact_bps": exit_impact,
            "best_bid": bid, "best_ask": ask, "buy_vwap": buy_vwap, "sell_vwap": sell_vwap}

def _adverse_drift_bps(direction: str, old_quote: dict[str, Any], fresh_quote: dict[str, Any]) -> Decimal:
    if direction == "LONG":
        old = cap.dec(old_quote.get("ask"), "old_ask")
        new = cap.dec(fresh_quote.get("ask"), "fresh_ask")
        return max(Decimal("0"), (new / old - Decimal("1")) * Decimal("10000"))
    old = cap.dec(old_quote.get("bid"), "old_bid")
    new = cap.dec(fresh_quote.get("bid"), "fresh_bid")
    return max(Decimal("0"), (Decimal("1") - new / old) * Decimal("10000"))


def evaluate_continuity(candidate: dict[str, Any], old_quote: dict[str, Any], fresh_quote: dict[str, Any],
                        fresh_book: dict[str, Any], rules: dict[str, Any], reference: dict[str, Any] | None) -> dict[str, Any]:
    try:
        row, old_capacity, direction = _validate_candidate(candidate)
        symbol = str(row.get("Symbol")) + "USDT"
        if old_quote.get("symbol") != symbol or fresh_quote.get("symbol") != symbol:
            return fail("SYMBOL_MISMATCH")
        if old_capacity.get("quote_provenance_sha256") != old_quote.get("provenance_sha256"):
            return fail("R95_FINAL_QUOTE_BINDING_MISMATCH")
        old_decision = int(old_quote.get("decision_time_ms"))
        fresh_decision = int(fresh_quote.get("decision_time_ms"))
        old_event = int(old_quote.get("event_time_ms"))
        fresh_event = int(fresh_quote.get("event_time_ms"))
        gap_ms = fresh_decision - old_decision
        if gap_ms <= 0 or gap_ms > MAX_CONTINUITY_GAP_MS:
            return fail("CONTINUITY_GAP_OUT_OF_RANGE", gap_ms=gap_ms, limit_ms=MAX_CONTINUITY_GAP_MS)
        if fresh_event <= old_event:
            return fail("EVENT_TIME_NOT_STRICT", old_event_time_ms=old_event, fresh_event_time_ms=fresh_event)
        if not isinstance(reference, dict):
            return fail("SECOND_VENUE_REFERENCE_MISSING")
        final_time = int(reference.get("integrity_final_time_ms", reference.get("received_at_ms")))
        integ = integrity.evaluate(fresh_quote, reference, final_time_ms=final_time)
        if integ.get("status") != "PASS":
            return fail("FRESH_MARKET_INTEGRITY_FAIL", integrity=integ)
    except Exception as exc:
        return fail("CONTINUITY_INPUT_INVALID", error=str(exc))

    try:
        quantity = cap.dec(old_capacity.get("quantity"), "frozen_quantity")
        fixed = _fixed_quantity_capacity(direction, quantity, fresh_quote, fresh_book, rules)
        if fixed["entry_impact_bps"] > cap.MAX_IMPACT_BPS or fixed["exit_impact_bps"] > cap.MAX_IMPACT_BPS:
            return fail("FRESH_CAPACITY_IMPACT_EXCEEDS_MODEL",
                        entry_impact_bps=str(fixed["entry_impact_bps"]),
                        exit_impact_bps=str(fixed["exit_impact_bps"]), limit_bps=str(cap.MAX_IMPACT_BPS))
        drift = _adverse_drift_bps(direction, old_quote, fresh_quote)
        if drift > MAX_ADVERSE_DRIFT_BPS:
            return fail("ADVERSE_DRIFT_EXCEEDS_MODEL", adverse_drift_bps=str(drift),
                        limit_bps=str(MAX_ADVERSE_DRIFT_BPS))
        stop = cap.dec(row.get("CURRENT_Stop"), "current_stop")
        target = cap.dec(row.get("CURRENT_Target"), "current_target")
        fresh_side = cap.dec(fresh_quote.get("ask" if direction == "LONG" else "bid"), "fresh_entry_side")
        if direction == "LONG" and not (stop < fresh_side < target):
            return fail("FRESH_PRICE_OUTSIDE_FROZEN_GEOMETRY", fresh_side=str(fresh_side), stop=str(stop), target=str(target))
        if direction == "SHORT" and not (target < fresh_side < stop):
            return fail("FRESH_PRICE_OUTSIDE_FROZEN_GEOMETRY", fresh_side=str(fresh_side), stop=str(stop), target=str(target))
    except Exception as exc:
        return fail("FRESH_CAPACITY_UNPROVEN", error=str(exc))
    out = {
        "schema": SCHEMA, "contract_id": CONTRACT_ID, "status": "PASS",
        "reason": "DECISION_CONTINUITY_PASS", "symbol": symbol, "direction": direction,
        "continuity_gap_ms": gap_ms, "continuity_limit_ms": MAX_CONTINUITY_GAP_MS,
        "adverse_drift_bps": str(drift), "adverse_drift_limit_bps": str(MAX_ADVERSE_DRIFT_BPS),
        "frozen_quantity": str(quantity), "fresh_notional_usdt": str(fixed["notional_usdt"]),
        "fresh_entry_impact_bps": str(fixed["entry_impact_bps"]),
        "fresh_exit_impact_bps": str(fixed["exit_impact_bps"]),
        "fresh_best_bid": str(fixed["best_bid"]), "fresh_best_ask": str(fixed["best_ask"]),
        "fresh_entry_depth_vwap": str(fixed["buy_vwap"] if direction == "LONG" else fixed["sell_vwap"]),
        "fresh_exit_depth_vwap_proxy": str(fixed["sell_vwap"] if direction == "LONG" else fixed["buy_vwap"]),
        "fresh_event_time_ms": fresh_event, "fresh_decision_time_ms": fresh_decision,
        "old_quote_provenance_sha256": old_quote["provenance_sha256"],
        "fresh_quote_provenance_sha256": fresh_quote["provenance_sha256"],
        "fresh_book_provenance_sha256": fresh_book["provenance_sha256"],
        "reference_provenance_sha256": reference.get("provenance_sha256") or reference.get("raw_sha256"),
        "ledger_write_authority": False, "can_trade": False, "capital_permission": "DENY",
    }
    out["provenance_sha256"] = cap.sha256_json(out)
    return out

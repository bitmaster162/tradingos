#!/usr/bin/env python3
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

SCHEMA = "tradingos.r87_frozen_setup_contract.v1"
CONTRACT_ID = "R87_CURRENT_CANONICAL_LIQUIDITY_SEQUENCE_V1_20260916"
SETUP_FAMILY = "CURRENT_CANONICAL_LIQUIDITY_SEQUENCE_V1"
DIRECTION = "LONG"
TIMEFRAME = "15m"
INTERVAL_MS = 15 * 60 * 1000
HORIZON_BARS = 96
ENTRY_MODE = "ENTRY_AT_R85_DECISION_QUOTE"
TIME_EXIT_STATE = "TERMINAL_TIME_EXIT"


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def dec(value: Any, field: str) -> Decimal:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"invalid_{field}") from exc
    if not out.is_finite():
        raise ValueError(f"invalid_{field}")
    return out

def horizon_geometry(decision_time_ms: int) -> dict[str, int]:
    if type(decision_time_ms) is not int or decision_time_ms <= 0:
        raise ValueError("decision_time_ms_invalid")
    first_open = (decision_time_ms // INTERVAL_MS + 1) * INTERVAL_MS
    horizon_open = first_open + (HORIZON_BARS - 1) * INTERVAL_MS
    horizon_end = horizon_open + INTERVAL_MS - 1
    return {
        "first_eligible_open_ms": first_open,
        "horizon_bar_open_ms": horizon_open,
        "horizon_end_ms": horizon_end,
    }


def build_contract(decision_time_ms: int, regime_shadow: str) -> dict[str, Any]:
    if not isinstance(regime_shadow, str) or not regime_shadow:
        raise ValueError("regime_shadow_missing")
    geo = horizon_geometry(decision_time_ms)
    return {
        "schema": SCHEMA,
        "contract_id": CONTRACT_ID,
        "setup_family": SETUP_FAMILY,
        "direction": DIRECTION,
        "timeframe": TIMEFRAME,
        "regime_shadow": regime_shadow,
        "entry_mode": ENTRY_MODE,
        "horizon_policy": "96_FULL_M15_BARS_STRICTLY_AFTER_DECISION",
        "horizon_bars": HORIZON_BARS,
        "interval_ms": INTERVAL_MS,
        **geo,
    }

def validate_contract(contract: dict[str, Any], decision_time_ms: int) -> dict[str, Any]:
    if not isinstance(contract, dict):
        raise ValueError("registration_contract_missing")
    regime = contract.get("regime_shadow")
    expected = build_contract(decision_time_ms, regime)
    if stable_json(contract) != stable_json(expected):
        raise ValueError("r87_contract_mismatch")
    return expected


def resolution_policy() -> dict[str, Any]:
    return {
        "contract_id": CONTRACT_ID,
        "path_start": "STRICTLY_AFTER_ENTRY_DECISION_TIME",
        "target_stop_precedence": "FIRST_CHRONOLOGICALLY_PROVEN_EVENT",
        "coarse_bar_both_reachable": "RESOLUTION_PENDING_SEQUENCE",
        "sequence_source": "1m_OR_ORDERED_AGGTRADES_AS_REQUIRED",
        "horizon_terminal": TIME_EXIT_STATE,
        "horizon_price_source": "BINANCE_SPOT_CLOSED_15M_HORIZON_BAR_CLOSE",
        "time_exit_fill": "LONG_CLOSE_MINUS_FROZEN_EXIT_SLIPPAGE_BPS",
        "time_exit_fee": "FROZEN_EXIT_FEE_RATE",
        "no_hindsight_extension": True,
        "can_trade": False,
        "capital_permission": "DENY",
    }


def settlement_spec(cost_model: dict[str, Any], initial_planned_risk: Any) -> dict[str, Any]:
    if not isinstance(cost_model, dict):
        raise ValueError("cost_model_missing")
    risk = dec(initial_planned_risk, "initial_planned_risk")
    if risk <= 0:
        raise ValueError("initial_planned_risk_nonpositive")
    entry_fee = dec(cost_model.get("entry_fee_rate"), "entry_fee_rate")
    exit_fee = dec(cost_model.get("exit_fee_rate"), "exit_fee_rate")
    entry_slip = dec(cost_model.get("entry_slippage_bps"), "entry_slippage_bps")
    exit_slip = dec(cost_model.get("exit_slippage_bps"), "exit_slippage_bps")
    if min(entry_fee, exit_fee, entry_slip, exit_slip) < 0:
        raise ValueError("negative_cost_input")
    return {
        "entry_fee_rate": str(entry_fee),
        "exit_fee_rate": str(exit_fee),
        "entry_slippage_bps": str(entry_slip),
        "exit_slippage_bps": str(exit_slip),
        "initial_planned_risk_per_unit": str(risk),
    }


def time_exit_outcome(
    modeled_entry: Any,
    horizon_close: Any,
    settlement: dict[str, Any],
) -> dict[str, Any]:
    entry = dec(modeled_entry, "modeled_entry")
    close = dec(horizon_close, "horizon_close")
    if entry <= 0 or close <= 0:
        raise ValueError("nonpositive_price")
    exit_fee = dec(settlement.get("exit_fee_rate"), "exit_fee_rate")
    entry_fee = dec(settlement.get("entry_fee_rate"), "entry_fee_rate")
    exit_slip = dec(settlement.get("exit_slippage_bps"), "exit_slippage_bps")
    risk = dec(settlement.get("initial_planned_risk_per_unit"), "initial_planned_risk")
    tenk = Decimal("10000")
    exit_fill = close * (Decimal("1") - exit_slip / tenk)
    net_pnl = exit_fill - entry - entry_fee * entry - exit_fee * exit_fill
    if risk <= 0:
        raise ValueError("initial_planned_risk_nonpositive")
    outcome_r = net_pnl / risk
    return {
        "terminal_event": TIME_EXIT_STATE,
        "horizon_close": str(close),
        "modeled_exit_fill": str(exit_fill),
        "net_pnl_per_unit": str(net_pnl),
        "outcome_r": str(outcome_r),
        "price_source": "BINANCE_SPOT_CLOSED_15M_HORIZON_BAR_CLOSE",
        "can_trade": False,
        "capital_permission": "DENY",
    }


def augment_trigger(
    trigger: dict[str, Any],
    contract: dict[str, Any],
    cost_model: dict[str, Any],
    initial_planned_risk: Any,
) -> dict[str, Any]:
    out = dict(trigger)
    out["r87_setup_contract"] = contract
    out["resolution_policy"] = resolution_policy()
    out["settlement_spec"] = settlement_spec(cost_model, initial_planned_risk)
    return out

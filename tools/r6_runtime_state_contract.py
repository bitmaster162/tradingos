#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
R90_PATH = ROOT / "tools" / "r6_prospective_sweep_orchestrator.py"
SPEC = importlib.util.spec_from_file_location("r90_runtime_consumer", R90_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load R90 orchestrator")
r90 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = r90
SPEC.loader.exec_module(r90)

INPUT_SCHEMA = "tradingos.r91_runtime_state_input.v1"
OUTPUT_SCHEMA = r90.STATE_SCHEMA
CONTRACT_ID = "R91_R6_RUNTIME_STATE_V1_20260916"
BKK = timezone(timedelta(hours=7))
FEE_RATE = Decimal("0.0005")
SLIPPAGE_BPS = Decimal("2")
STRESS_EXTRA_BPS = Decimal("10")
BASE_RISK_PCT = Decimal("0.5")
REGIME_LABEL = "UNCLASSIFIED_PREDECISION"


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def dec(value: Any, name: str) -> Decimal:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid_{name}") from exc
    if not out.is_finite():
        raise ValueError(f"invalid_{name}")
    return out


def _require_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name}_invalid")
    return value


def _bkk_week_start_ms(ms: int) -> int:
    dt = datetime.fromtimestamp(ms / 1000, BKK)
    start = (dt - timedelta(days=dt.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int(start.timestamp() * 1000)


def _events(payload: dict[str, Any], observed_ms: int) -> list[dict[str, Any]]:
    raw = payload.get("ledger_events", [])
    if not isinstance(raw, list):
        raise ValueError("ledger_events_invalid")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("ledger_event_invalid")
        event_id = item.get("event_id")
        if not isinstance(event_id, str) or not event_id or event_id in seen:
            raise ValueError("ledger_event_id_invalid_or_duplicate")
        seen.add(event_id)
        registered = _require_int(item.get("registered_at_ms"), "registered_at_ms")
        if registered > observed_ms:
            raise ValueError("ledger_event_after_observation")
        resolved = item.get("resolved_at_ms")
        if resolved is not None:
            resolved = _require_int(resolved, "resolved_at_ms")
            if resolved < registered or resolved > observed_ms:
                raise ValueError("resolved_at_ms_invalid")
        outcome = item.get("outcome_r")
        if outcome is not None:
            outcome = dec(outcome, "outcome_r")
            if resolved is None:
                raise ValueError("outcome_without_resolution")
        out.append({**item, "registered_at_ms": registered,
                    "resolved_at_ms": resolved, "outcome_r": outcome})
    return out


def _weekly_expectancy(events: list[dict[str, Any]], observed_ms: int) -> Decimal | None:
    current_start = _bkk_week_start_ms(observed_ms)
    prior_start = current_start - 7 * 24 * 60 * 60 * 1000
    values = [e["outcome_r"] for e in events
              if e["resolved_at_ms"] is not None
              and prior_start <= e["resolved_at_ms"] < current_start
              and e["outcome_r"] is not None]
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def _consecutive_losses(events: list[dict[str, Any]]) -> tuple[int, int]:
    resolved = sorted(
        (e for e in events if e["resolved_at_ms"] is not None and e["outcome_r"] is not None),
        key=lambda e: e["resolved_at_ms"],
    )
    count = 0
    last_ms = 0
    for event in reversed(resolved):
        if event["outcome_r"] >= 0:
            break
        count += 1
        last_ms = max(last_ms, int(event["resolved_at_ms"]))
    return count, last_ms


def _drawdown_pct(events: list[dict[str, Any]]) -> Decimal:
    equity = Decimal("100")
    peak = equity
    max_dd = Decimal("0")
    resolved = sorted(
        (e for e in events if e["resolved_at_ms"] is not None and e["outcome_r"] is not None),
        key=lambda e: e["resolved_at_ms"],
    )
    for event in resolved:
        equity *= Decimal("1") + event["outcome_r"] * BASE_RISK_PCT / Decimal("100")
        peak = max(peak, equity)
        if peak > 0:
            dd = (peak - equity) / peak * Decimal("100")
            max_dd = max(max_dd, dd)
    return max_dd


def _entries_today(events: list[dict[str, Any]], observed_ms: int) -> int:
    day = datetime.fromtimestamp(observed_ms / 1000, BKK).date()
    return sum(
        1 for e in events
        if datetime.fromtimestamp(e["registered_at_ms"] / 1000, BKK).date() == day
    )


def _ledger_state(payload: dict[str, Any]) -> dict[str, Any]:
    ledger = payload.get("ledger_state")
    if not isinstance(ledger, dict):
        raise ValueError("ledger_state_missing")
    ids = ledger.get("existing_event_ids")
    if not isinstance(ids, list) or any(not isinstance(x, str) or not x for x in ids):
        raise ValueError("existing_event_ids_invalid")
    if len(ids) != len(set(ids)):
        raise ValueError("existing_event_ids_duplicate")
    cal = _require_int(ledger.get("resolved_calibration_n"), "resolved_calibration_n")
    hold = _require_int(ledger.get("resolved_holdout_n", 0), "resolved_holdout_n")
    freeze = ledger.get("holdout_freeze")
    if not isinstance(freeze, str) or not freeze:
        raise ValueError("holdout_freeze_invalid")
    return {
        "existing_event_ids": list(ids),
        "resolved_calibration_n": cal,
        "resolved_holdout_n": hold,
        "holdout_freeze": freeze,
    }


def build_runtime_state(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != INPUT_SCHEMA:
        raise ValueError("input_schema_invalid")
    observed = _require_int(payload.get("observed_at_ms"), "observed_at_ms")
    if observed <= 0:
        raise ValueError("observed_at_ms_invalid")
    provenance = payload.get("provenance")
    if not isinstance(provenance, str) or not provenance.strip():
        raise ValueError("provenance_missing")
    op = payload.get("operational_risk_state")
    if op not in {"PASS", "FAIL", "UNKNOWN"}:
        raise ValueError("operational_risk_state_invalid")
    ledger = _ledger_state(payload)
    events = _events(payload, observed)
    if any(e["event_id"] not in set(ledger["existing_event_ids"]) for e in events):
        raise ValueError("ledger_event_not_in_existing_ids")
    weekly = _weekly_expectancy(events, observed)
    proposed = BASE_RISK_PCT / Decimal("2") if weekly is not None and weekly < 0 else BASE_RISK_PCT
    open_count = sum(1 for e in events if e["resolved_at_ms"] is None)
    aggregate = BASE_RISK_PCT * Decimal(open_count)
    losses, last_loss_ms = _consecutive_losses(events)
    pause_until = last_loss_ms + 60 * 60 * 1000 if losses >= 3 else 0
    risk = {
        "proposed_risk_pct": str(proposed),
        "aggregate_open_risk_pct": str(aggregate),
        "drawdown_pct": str(_drawdown_pct(events)),
        "base_risk_unit_pct": str(BASE_RISK_PCT),
        "entries_today": _entries_today(events, observed),
        "consecutive_net_losses": losses,
        "pause_until_ms": pause_until,
        "completed_week_expectancy_r": None if weekly is None else str(weekly),
        "operational_risk_state": op,
    }
    cost = {
        "entry_fee_rate": str(FEE_RATE),
        "exit_fee_rate": str(FEE_RATE),
        "entry_slippage_bps": str(SLIPPAGE_BPS),
        "exit_slippage_bps": str(SLIPPAGE_BPS),
    }
    regimes = {symbol: REGIME_LABEL for symbol in r90.r84.ALL16}
    return {
        "schema": OUTPUT_SCHEMA,
        "observed_at_ms": observed,
        "provenance": provenance,
        "runtime_contract_id": CONTRACT_ID,
        "cost_model": cost,
        "cost_stress_model": {
            "base_fee_bps_per_side": "5",
            "base_slippage_bps_per_side": str(SLIPPAGE_BPS),
            "base_total_bps_per_side": "7",
            "stress_extra_bps_per_side": str(STRESS_EXTRA_BPS),
            "stress_total_bps_per_side": "17",
        },
        "risk_state": risk,
        "regime_shadow_by_symbol": regimes,
        "regime_shadow_policy": "UNCLASSIFIED_PREDECISION_UNTIL_SEPARATE_REGIME_CONTRACT",
        "ledger_state": ledger,
        "can_trade": False,
        "capital_permission": "DENY",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build frozen R91 pre-decision R6 runtime state")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        result = build_runtime_state(payload)
        r90._state(result)
        text = stable_json(result)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0
    except Exception as exc:
        print(stable_json({
            "schema": OUTPUT_SCHEMA,
            "result": "RUNTIME_STATE_FAIL_CLOSED",
            "error": str(exc),
            "can_trade": False,
            "capital_permission": "DENY",
        }))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

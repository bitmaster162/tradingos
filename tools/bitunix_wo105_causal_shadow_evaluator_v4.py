#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bitunix_wo105_causal_shadow_evaluator as base  # noqa: E402
from tools import bitunix_wo105_causal_shadow_evaluator_v2 as v2  # noqa: E402
from tools import bitunix_wo105_causal_shadow_evaluator_v3 as v3  # noqa: E402


TOOL_PATH = "tools/bitunix_wo105_causal_shadow_evaluator_v4.py"
SCHEMA = "bitunix-wo105-causal-shadow-prereg-v4"
STATUS = "FROZEN_CAUSAL_SHADOW_EVALUATOR_V4"
CANDLE_SERIES = {"signal_bars", "htf_bars", "outcome_bars"}
BASE_VALIDATE_PACKET = base.validate_packet
TERMINAL_STATES = base.TERMINAL_STATES
canonical_sha256 = base.canonical_sha256
detect_setup = base.detect_setup
parse_iso_ms = base.parse_iso_ms
pre_entry_manifest = base.pre_entry_manifest
select_entry_book = base.select_entry_book
state_report = base.state_report


def resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_object(path: Path) -> dict[str, Any]:
    return base.read_object(path)


def _v3_compat_lock(lock: dict[str, Any]) -> dict[str, Any]:
    compatible = copy.deepcopy(lock)
    compatible["schema"] = v3.SCHEMA
    compatible["status"] = v3.STATUS
    compatible["bindings"]["evaluator"] = v3.TOOL_PATH
    compatible["bindings"]["evaluator_sha256"] = base.sha256_file(ROOT / v3.TOOL_PATH)
    return compatible


def validate_lock(lock: dict[str, Any], *, tool_path: Path | None = None) -> list[str]:
    failures: list[str] = []
    if lock.get("schema") != SCHEMA:
        failures.append("lock_schema_invalid")
    if lock.get("status") != STATUS:
        failures.append("lock_status_invalid")
    if lock.get("can_trade") is not False:
        failures.append("lock_can_trade_not_false")

    bindings = lock.get("bindings") if isinstance(lock.get("bindings"), dict) else {}
    actual_tool = tool_path or Path(__file__)
    if bindings.get("evaluator") != TOOL_PATH:
        failures.append("evaluator_path_invalid")
    if bindings.get("evaluator_sha256") != base.sha256_file(actual_tool):
        failures.append("evaluator_hash_mismatch")

    runtime = lock.get("runtime_contract") if isinstance(lock.get("runtime_contract"), dict) else {}
    order = runtime.get("candle_series_order") if isinstance(runtime.get("candle_series_order"), dict) else {}
    if order.get("event_order") != "close_ms_ascending_unique":
        failures.append("candle_event_order_contract_missing")
    if order.get("receipt_order") != "not_required_monotonic":
        failures.append("candle_receipt_order_contract_missing")
    if order.get("availability_rule") != "received_at_lte_evaluation_and_frozen_action_cutoff":
        failures.append("candle_availability_contract_missing")
    if set(order.get("applies_to") or []) != CANDLE_SERIES:
        failures.append("candle_order_scope_invalid")

    rollover = lock.get("operational_rollover") if isinstance(lock.get("operational_rollover"), dict) else {}
    if int(rollover.get("events_admitted") or 0) != 0:
        failures.append("predecessor_events_not_zero")
    if int(rollover.get("outcomes_observed") or 0) != 0:
        failures.append("predecessor_outcomes_not_zero")
    if rollover.get("outcome_metrics_inspected") is not False:
        failures.append("predecessor_outcome_blindness_invalid")
    if rollover.get("strategy_parameters_mutated") is not False:
        failures.append("strategy_parameters_mutated")

    failures.extend(v3.validate_lock(_v3_compat_lock(lock), tool_path=ROOT / v3.TOOL_PATH))
    return sorted(set(failures))


def validate_packet(packet: dict[str, Any], lock: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Keep candle event order strict without requiring network receipts to share that order."""
    records, failures = BASE_VALIDATE_PACKET(packet, lock)
    allowed = {f"{series}:receipt_time_reordered" for series in CANDLE_SERIES}
    return records, [failure for failure in failures if failure not in allowed]


def evaluate_packet(
    packet: dict[str, Any], lock: dict[str, Any], *, previous_events: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    failures = validate_lock(lock)
    failures.extend(v2.validate_unit_contracts(packet, lock))
    failures.extend(v2.validate_candle_availability(packet, lock))
    if failures:
        return state_report(
            state="CAPTURE_INVALID",
            decision="bitunix_wo105_v4_hold_lock_unit_or_causal_contract_invalid",
            failures=sorted(set(failures)),
        )

    original_validator = base.validate_packet
    base.validate_packet = validate_packet
    try:
        report = base.evaluate_packet(packet, v3._v1_compat_lock(lock), previous_events=previous_events)
    finally:
        base.validate_packet = original_validator
    report["evaluator_contract"] = "WO105_V4_EVENT_ORDER_WITH_CAUSAL_RECEIPT_CUTOFFS"
    report["can_trade"] = False
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="WO105 V4 fail-closed causal shadow evaluator")
    parser.add_argument("packet")
    parser.add_argument("--lock", default="configs/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R4_2026-07-15.json")
    parser.add_argument("--ledger", default="_dl/bitunix_wo105_shadow_v3r4/EVENT_LEDGER.jsonl")
    parser.add_argument("--out", default="_dl/bitunix_wo105_shadow_v3r4/LAST_EVALUATION.json")
    args = parser.parse_args()
    lock = read_object(resolve(args.lock))
    packet = read_object(resolve(args.packet))
    ledger = resolve(args.ledger)
    report = evaluate_packet(packet, lock, previous_events=base.load_previous_events(ledger))
    output = resolve(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    if report.get("event_id") and report.get("state") != "CAPTURE_INVALID":
        base.append_ledger(ledger, {**report, "cohort_binding_sha256": lock.get("parameter_cohort_sha256")})
    print(json.dumps({"decision": report["decision"], "state": report["state"], "can_trade": False}))
    return 0 if report["state"] != "CAPTURE_INVALID" else 1


if __name__ == "__main__":
    raise SystemExit(main())

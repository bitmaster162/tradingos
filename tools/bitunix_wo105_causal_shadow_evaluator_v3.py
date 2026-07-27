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


TOOL_PATH = "tools/bitunix_wo105_causal_shadow_evaluator_v3.py"
BASE_TOOL_PATH = "tools/bitunix_wo105_causal_shadow_evaluator.py"
SCHEMA = "bitunix-wo105-causal-shadow-prereg-v3"
STATUS = "FROZEN_CAUSAL_SHADOW_EVALUATOR_V3"
V2_TOMBSTONE_STATUS = "TOMBSTONED_PRE_FLOOR_CAUSAL_LIFECYCLE_GAP"
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


def _bound_file(bindings: dict[str, Any], path_key: str, hash_key: str, failures: list[str]) -> Path | None:
    raw_path = bindings.get(path_key)
    if not isinstance(raw_path, str) or not raw_path:
        failures.append(f"{path_key}_binding_missing")
        return None
    path = resolve(raw_path)
    expected = bindings.get(hash_key)
    if not path.is_file() or expected != base.sha256_file(path):
        failures.append(f"{path_key}_binding_mismatch")
        return None
    return path


def validate_lock(lock: dict[str, Any], *, tool_path: Path | None = None) -> list[str]:
    failures: list[str] = []
    if lock.get("schema") != SCHEMA:
        failures.append("lock_schema_invalid")
    if lock.get("status") != STATUS:
        failures.append("lock_status_invalid")
    if lock.get("can_trade") is not False:
        failures.append("lock_can_trade_not_false")
    scope = lock.get("scope") if isinstance(lock.get("scope"), dict) else {}
    required_scope = {
        "public_data_only": True,
        "credentials_allowed": False,
        "private_api_allowed": False,
        "signals_allowed": False,
        "paper_entries_allowed": False,
        "orders_allowed": False,
        "capital_permission": "DENY",
        "can_trade": False,
    }
    if any(scope.get(key) != value for key, value in required_scope.items()):
        failures.append("lock_scope_not_fail_closed")

    params = lock.get("params") if isinstance(lock.get("params"), dict) else {}
    if lock.get("parameter_cohort_sha256") != canonical_sha256(params):
        failures.append("parameter_cohort_hash_mismatch")
    floor = parse_iso_ms(lock.get("forward_start_at"))
    frozen = parse_iso_ms(lock.get("frozen_at_utc"))
    if floor is None or frozen is None or floor <= frozen:
        failures.append("forward_start_invalid")

    bindings = lock.get("bindings") if isinstance(lock.get("bindings"), dict) else {}
    actual_tool = tool_path or Path(__file__)
    if bindings.get("evaluator_sha256") != base.sha256_file(actual_tool):
        failures.append("evaluator_hash_mismatch")
    base_path = _bound_file(bindings, "base_evaluator", "base_evaluator_sha256", failures)
    v2_evaluator_path = _bound_file(bindings, "v2_evaluator", "v2_evaluator_sha256", failures)
    v2_lock_path = _bound_file(bindings, "v2_lock", "v2_lock_sha256", failures)
    replay_path = _bound_file(bindings, "exact_replay_report", "exact_replay_report_sha256", failures)
    _bound_file(bindings, "v3_path", "v3_sha256", failures)
    v1_tombstone_path = _bound_file(bindings, "v1_tombstone", "v1_tombstone_sha256", failures)
    v2_tombstone_path = _bound_file(bindings, "v2_tombstone", "v2_tombstone_sha256", failures)
    audit_path = _bound_file(bindings, "causal_lifecycle_audit", "causal_lifecycle_audit_sha256", failures)
    for path_key in (
        "rest_collector",
        "ws_intake",
        "v2_packet_assembler",
        "packet_assembler",
        "liquidation_context",
        "liquidation_collector",
        "liquidation_side_semantics",
        "liquidation_real_feed_contract",
        "rest_commissioning_manifest",
    ):
        _bound_file(bindings, path_key, f"{path_key}_sha256", failures)

    if base_path is not None and base_path.resolve() != (ROOT / BASE_TOOL_PATH).resolve():
        failures.append("base_evaluator_path_invalid")
    if v2_evaluator_path is not None and v2_evaluator_path.resolve() != (ROOT / v2.TOOL_PATH).resolve():
        failures.append("v2_evaluator_path_invalid")
    if replay_path is not None:
        replay = read_object(replay_path)
        if replay.get("canonical_replay") != "PASS" or replay.get("public_contract_confirmed") is not True:
            failures.append("exact_replay_not_pass")
    if v1_tombstone_path is not None:
        tombstone = read_object(v1_tombstone_path)
        if tombstone.get("status") != "TOMBSTONED_PRE_FLOOR_UNIT_CONTRACT_GAP":
            failures.append("v1_tombstone_status_invalid")
        if tombstone.get("events_observed") != 0 or tombstone.get("outcomes_observed") != 0:
            failures.append("v1_tombstone_not_zero_event")
    if v2_tombstone_path is not None:
        tombstone = read_object(v2_tombstone_path)
        if tombstone.get("status") != V2_TOMBSTONE_STATUS:
            failures.append("v2_tombstone_status_invalid")
        if tombstone.get("events_observed") != 0 or tombstone.get("outcomes_observed") != 0:
            failures.append("v2_tombstone_not_zero_event")
        if tombstone.get("interim_outcome_metrics_inspected") is not False:
            failures.append("v2_tombstone_outcome_blindness_invalid")
        if v2_lock_path is not None and tombstone.get("original_lock_sha256") != base.sha256_file(v2_lock_path):
            failures.append("v2_tombstone_lock_binding_mismatch")
    if audit_path is not None:
        audit = read_object(audit_path)
        if audit.get("decision") != "bitunix_wo105_v2_pre_floor_runtime_contract_not_executable_as_bound":
            failures.append("causal_lifecycle_audit_decision_invalid")
        if audit.get("events_observed") != 0 or audit.get("outcomes_observed") != 0:
            failures.append("causal_lifecycle_audit_not_zero_event")
    if v2_lock_path is not None:
        previous_lock = read_object(v2_lock_path)
        if previous_lock.get("parameter_cohort_sha256") != lock.get("parameter_cohort_sha256"):
            failures.append("strategy_parameter_hash_changed_from_v2")
        if canonical_sha256(previous_lock.get("params")) != canonical_sha256(params):
            failures.append("strategy_parameters_changed_from_v2")

    runtime_contract = lock.get("runtime_contract") if isinstance(lock.get("runtime_contract"), dict) else {}
    if runtime_contract.get("receipt_selection") != "earliest_received_record_per_close_ms":
        failures.append("earliest_receipt_contract_missing")
    lifecycle = runtime_contract.get("event_lifecycle") if isinstance(runtime_contract.get("event_lifecycle"), dict) else {}
    if lifecycle.get("open_event_continuation") != "immutable_initial_packet_plus_dynamic_post_entry_series":
        failures.append("open_event_continuation_contract_missing")
    if lifecycle.get("event_packet_archive_required") is not True:
        failures.append("event_packet_archive_contract_missing")
    return sorted(set(failures))


def _v1_compat_lock(lock: dict[str, Any]) -> dict[str, Any]:
    compatible = copy.deepcopy(lock)
    compatible["schema"] = "bitunix-wo105-causal-shadow-prereg-v1"
    compatible["status"] = "FROZEN_CAUSAL_SHADOW_EVALUATOR"
    compatible["bindings"]["evaluator"] = BASE_TOOL_PATH
    compatible["bindings"]["evaluator_sha256"] = compatible["bindings"]["base_evaluator_sha256"]
    return compatible


def evaluate_packet(
    packet: dict[str, Any], lock: dict[str, Any], *, previous_events: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    failures = validate_lock(lock)
    failures.extend(v2.validate_unit_contracts(packet, lock))
    failures.extend(v2.validate_candle_availability(packet, lock))
    if failures:
        return state_report(
            state="CAPTURE_INVALID",
            decision="bitunix_wo105_v3_hold_lock_unit_or_causal_contract_invalid",
            failures=sorted(set(failures)),
        )
    report = base.evaluate_packet(packet, _v1_compat_lock(lock), previous_events=previous_events)
    report["evaluator_contract"] = "WO105_V3_EARLIEST_RECEIPT_AND_EVENT_CONTINUATION"
    report["can_trade"] = False
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="WO105 V3 fail-closed causal shadow evaluator")
    parser.add_argument("packet")
    parser.add_argument("--lock", default="configs/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3_2026-07-14.json")
    parser.add_argument("--ledger", default="_dl/bitunix_wo105_shadow_v3/EVENT_LEDGER.jsonl")
    parser.add_argument("--out", default="_dl/bitunix_wo105_shadow_v3/LAST_EVALUATION.json")
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

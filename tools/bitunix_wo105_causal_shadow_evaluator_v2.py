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


TOOL_PATH = "tools/bitunix_wo105_causal_shadow_evaluator_v2.py"
BASE_TOOL_PATH = "tools/bitunix_wo105_causal_shadow_evaluator.py"
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
    path = resolve(str(bindings.get(path_key) or ""))
    if not path.is_file() or bindings.get(hash_key) != base.sha256_file(path):
        failures.append(f"{path_key}_binding_mismatch")
        return None
    return path


def validate_lock(lock: dict[str, Any], *, tool_path: Path | None = None) -> list[str]:
    failures: list[str] = []
    if lock.get("schema") != "bitunix-wo105-causal-shadow-prereg-v2":
        failures.append("lock_schema_invalid")
    if lock.get("status") != "FROZEN_CAUSAL_SHADOW_EVALUATOR_V2":
        failures.append("lock_status_invalid")
    if lock.get("can_trade") is not False:
        failures.append("lock_can_trade_not_false")
    scope = lock.get("scope") if isinstance(lock.get("scope"), dict) else {}
    required_scope = {
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
    replay_path = _bound_file(bindings, "exact_replay_report", "exact_replay_report_sha256", failures)
    _bound_file(bindings, "v3_path", "v3_sha256", failures)
    tombstone_path = _bound_file(bindings, "v1_tombstone", "v1_tombstone_sha256", failures)
    _bound_file(bindings, "rest_collector", "rest_collector_sha256", failures)
    _bound_file(bindings, "ws_intake", "ws_intake_sha256", failures)
    _bound_file(bindings, "packet_assembler", "packet_assembler_sha256", failures)
    _bound_file(bindings, "liquidation_context", "liquidation_context_sha256", failures)
    _bound_file(bindings, "liquidation_collector", "liquidation_collector_sha256", failures)
    _bound_file(bindings, "liquidation_side_semantics", "liquidation_side_semantics_sha256", failures)
    _bound_file(bindings, "liquidation_real_feed_contract", "liquidation_real_feed_contract_sha256", failures)
    _bound_file(bindings, "rest_commissioning_manifest", "rest_commissioning_manifest_sha256", failures)
    if base_path is not None and base_path.resolve() != (ROOT / BASE_TOOL_PATH).resolve():
        failures.append("base_evaluator_path_invalid")
    if replay_path is not None:
        replay = read_object(replay_path)
        if replay.get("canonical_replay") != "PASS" or replay.get("public_contract_confirmed") is not True:
            failures.append("exact_replay_not_pass")
    if tombstone_path is not None:
        tombstone = read_object(tombstone_path)
        if tombstone.get("status") != "TOMBSTONED_PRE_FLOOR_UNIT_CONTRACT_GAP":
            failures.append("v1_tombstone_status_invalid")
        if tombstone.get("events_observed") != 0 or tombstone.get("outcomes_observed") != 0:
            failures.append("v1_tombstone_not_zero_event")
    contracts = params.get("source_contracts") if isinstance(params.get("source_contracts"), dict) else {}
    funding = contracts.get("funding_rate_8h") if isinstance(contracts.get("funding_rate_8h"), dict) else {}
    if funding.get("raw_unit") != "percentage_points" or funding.get("evaluator_unit") != "decimal_fraction":
        failures.append("funding_unit_contract_invalid")
    if funding.get("normalization_rule") != "api_percentage_points_divide_by_100":
        failures.append("funding_normalization_contract_invalid")
    liquidation = contracts.get("liquidation_skew") if isinstance(contracts.get("liquidation_skew"), dict) else {}
    if liquidation.get("evaluator_unit") != "signed_notional_share":
        failures.append("liquidation_unit_contract_invalid")
    if liquidation.get("side_semantics") != {"BUY": "liquidated_SHORT", "SELL": "liquidated_LONG"}:
        failures.append("liquidation_side_contract_invalid")
    return sorted(set(failures))


def validate_unit_contracts(packet: dict[str, Any], lock: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    definitions = (((lock.get("params") or {}).get("crowd_funding") or {}).get("sources") or {})
    rows = packet.get("crowd")
    if not isinstance(rows, list):
        return ["crowd:not_list"]
    for index, row in enumerate(rows):
        payload = row.get("payload") if isinstance(row, dict) else None
        if not isinstance(payload, dict):
            continue
        kind = payload.get("kind")
        if kind not in definitions:
            failures.append(f"crowd[{index}]:kind_not_preregistered")
            continue
        expected_unit = definitions[kind].get("expected_unit")
        if expected_unit and payload.get("unit") != expected_unit:
            failures.append(f"crowd[{index}]:unit_mismatch:{kind}")
        if kind == "funding_rate_8h":
            if payload.get("raw_unit") != "percentage_points":
                failures.append(f"crowd[{index}]:funding_raw_unit_invalid")
            if payload.get("normalization_rule") != "api_percentage_points_divide_by_100":
                failures.append(f"crowd[{index}]:funding_normalization_invalid")
        if kind == "cvd_norm" and payload.get("method") != "sum(buy_size-sell_size)/sum(size)":
            failures.append(f"crowd[{index}]:cvd_method_invalid")
        if kind == "liquidation_skew":
            expected_method = "(short_liquidated_notional-long_liquidated_notional)/total_liquidated_notional"
            if payload.get("method") != expected_method:
                failures.append(f"crowd[{index}]:liquidation_method_invalid")
            if payload.get("side_semantics") != {"BUY": "liquidated_SHORT", "SELL": "liquidated_LONG"}:
                failures.append(f"crowd[{index}]:liquidation_side_semantics_invalid")
    funding_unit = (((lock.get("params") or {}).get("funding_treatment") or {}).get("expected_unit"))
    funding_rows = packet.get("funding_events")
    if not isinstance(funding_rows, list):
        failures.append("funding_events:not_list")
    else:
        for index, row in enumerate(funding_rows):
            payload = row.get("payload") if isinstance(row, dict) else None
            if isinstance(payload, dict) and funding_unit and payload.get("unit") != funding_unit:
                failures.append(f"funding_events[{index}]:unit_mismatch")
    return sorted(set(failures))


def validate_candle_availability(packet: dict[str, Any], lock: dict[str, Any]) -> list[str]:
    """Ensure a closed candle was actually received before its modeled action cutoff."""
    failures: list[str] = []
    params = lock.get("params") if isinstance(lock.get("params"), dict) else {}
    signal_rows = packet.get("signal_bars")
    htf_rows = packet.get("htf_bars")
    outcome_rows = packet.get("outcome_bars")
    if not isinstance(signal_rows, list) or not isinstance(htf_rows, list) or not isinstance(outcome_rows, list):
        return failures
    setup = base.detect_setup([row for row in signal_rows if isinstance(row, dict)], params)
    if setup is None:
        return failures
    signal_close = int(setup["signal_close_ms"])
    entry_cutoff = signal_close + int((params.get("entry") or {}).get("latency_ms", 0))
    matching_signal = [
        row
        for row in signal_rows
        if isinstance(row, dict)
        and isinstance(row.get("payload"), dict)
        and row["payload"].get("close_ms") == signal_close
    ]
    if len(matching_signal) != 1 or int(matching_signal[0].get("received_at", entry_cutoff + 1)) > entry_cutoff:
        failures.append("signal_bar_not_available_by_entry_cutoff")
    causal_htf = [
        row
        for row in htf_rows
        if isinstance(row, dict)
        and isinstance(row.get("payload"), dict)
        and isinstance(row["payload"].get("close_ms"), int)
        and int(row["payload"]["close_ms"]) <= signal_close
    ]
    if causal_htf:
        latest_htf = max(causal_htf, key=lambda row: int(row["payload"]["close_ms"]))
        if int(latest_htf.get("received_at", entry_cutoff + 1)) > entry_cutoff:
            failures.append("latest_htf_bar_not_available_by_entry_cutoff")
    exit_latency = int((params.get("exit") or {}).get("latency_ms", 0))
    for index, row in enumerate(outcome_rows):
        if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
            continue
        close_ms = row["payload"].get("close_ms")
        if not isinstance(close_ms, int) or close_ms <= signal_close:
            continue
        if int(row.get("received_at", close_ms + exit_latency + 1)) > close_ms + exit_latency:
            failures.append(f"outcome_bars[{index}]:not_available_by_exit_cutoff")
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
    failures.extend(validate_unit_contracts(packet, lock))
    failures.extend(validate_candle_availability(packet, lock))
    if failures:
        return state_report(
            state="CAPTURE_INVALID",
            decision="bitunix_wo105_v2_hold_lock_or_unit_contract_invalid",
            failures=sorted(set(failures)),
        )
    report = base.evaluate_packet(packet, _v1_compat_lock(lock), previous_events=previous_events)
    report["evaluator_contract"] = "WO105_V2_EXPLICIT_SOURCE_UNITS"
    report["can_trade"] = False
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="WO105 V2 fail-closed causal shadow evaluator with explicit source units")
    parser.add_argument("packet")
    parser.add_argument("--lock", default="configs/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V2_2026-07-14.json")
    parser.add_argument("--ledger", default="_dl/bitunix_wo105_shadow_v2/EVENT_LEDGER.jsonl")
    parser.add_argument("--out", default="_dl/bitunix_wo105_shadow_v2/LAST_EVALUATION.json")
    args = parser.parse_args()
    lock = read_object(resolve(args.lock))
    packet = read_object(resolve(args.packet))
    ledger = resolve(args.ledger)
    report = evaluate_packet(packet, lock, previous_events=base.load_previous_events(ledger))
    output = resolve(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if report.get("event_id") and report.get("state") != "CAPTURE_INVALID":
        base.append_ledger(ledger, {**report, "cohort_binding_sha256": lock.get("parameter_cohort_sha256")})
    print(json.dumps({"decision": report["decision"], "state": report["state"], "can_trade": False}))
    return 0 if report["state"] != "CAPTURE_INVALID" else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bitunix_wo105_causal_shadow_evaluator as base  # noqa: E402
from tools import bitunix_wo105_causal_shadow_evaluator_v4 as evaluator  # noqa: E402


TOOL_PATH = "tools/bitunix_wo105_v3r4_rollover.py"
DEFAULT_FORWARD_START = "2026-07-15T04:00:00Z"
PREDECESSOR_LOCK = ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R3_2026-07-14.json"
STOP_RECEIPT = ROOT / "docs" / "BITUNIX_WO105_V3R3_RUNTIME_STOP_RECEIPT_2026-07-15.json"
LAST_PACKET = ROOT / "_dl" / "bitunix_wo105_shadow_v3r3" / "LAST_PACKET.json"
LAST_EVALUATION = ROOT / "_dl" / "bitunix_wo105_shadow_v3r3" / "LAST_EVALUATION.json"
LEDGER = ROOT / "_dl" / "bitunix_wo105_shadow_v3r3" / "EVENT_LEDGER.jsonl"
AUDIT = ROOT / "docs" / "BITUNIX_WO105_V3R3_RECEIPT_ORDER_AUDIT_2026-07-15.json"
AUDIT_MD = AUDIT.with_suffix(".md")
TOMBSTONE = ROOT / "docs" / "BITUNIX_WO105_V3R3_RECEIPT_ORDER_TOMBSTONE_2026-07-15.json"
TOMBSTONE_MD = TOMBSTONE.with_suffix(".md")
OUTPUT_LOCK = ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R4_2026-07-15.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def portable(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def nonempty_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(bool(line.strip()) for line in path.read_text(encoding="utf-8-sig").splitlines())


def receipt_inversions(rows: list[dict[str, Any]]) -> list[dict[str, int]]:
    inversions: list[dict[str, int]] = []
    for index in range(1, len(rows)):
        previous = rows[index - 1]
        current = rows[index]
        if int(current["received_at"]) < int(previous["received_at"]):
            inversions.append(
                {
                    "previous_index": index - 1,
                    "current_index": index,
                    "previous_close_ms": int(previous["payload"]["close_ms"]),
                    "current_close_ms": int(current["payload"]["close_ms"]),
                    "previous_received_at": int(previous["received_at"]),
                    "current_received_at": int(current["received_at"]),
                }
            )
    return inversions


def build_audit(
    *,
    predecessor: dict[str, Any],
    stop_receipt: dict[str, Any],
    evaluation: dict[str, Any],
    packet: dict[str, Any],
    ledger_rows: int,
) -> dict[str, Any]:
    failures: list[str] = []
    expected = {"htf_bars:receipt_time_reordered", "outcome_bars:receipt_time_reordered"}
    actual = set(evaluation.get("failures") or [])
    if evaluation.get("state") != "CAPTURE_INVALID" or actual != expected:
        failures.append("receipt_order_failure_not_exactly_reproduced")
    if evaluation.get("event_id") is not None or evaluation.get("edge_evaluated") is not False:
        failures.append("predecessor_not_outcome_blind")
    if ledger_rows != 0:
        failures.append("predecessor_ledger_not_empty")
    if stop_receipt.get("decision") != "bitunix_wo105_v3r3_runtime_stopped_verified_after_receipt_order_failure":
        failures.append("verified_stop_receipt_missing")
    if predecessor.get("parameter_cohort_sha256") != base.canonical_sha256(predecessor.get("params")):
        failures.append("predecessor_parameter_hash_invalid")

    setup = base.detect_setup(packet.get("signal_bars") or [], predecessor["params"])
    if setup is None:
        failures.append("candidate_setup_not_reproducible")
    inversion_map = {
        series: receipt_inversions(packet.get(series) or [])
        for series in ("signal_bars", "htf_bars", "outcome_bars")
    }
    if inversion_map["signal_bars"] or not inversion_map["htf_bars"] or not inversion_map["outcome_bars"]:
        failures.append("observed_inversion_pattern_changed")
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "tool": TOOL_PATH,
        "decision": (
            "bitunix_wo105_v3r3_zero_event_candle_receipt_order_contradiction_confirmed"
            if not failures
            else "bitunix_wo105_v3r4_rollover_blocked"
        ),
        "failures": failures,
        "predecessor_cohort_id": predecessor.get("cohort_id"),
        "parameter_cohort_sha256": predecessor.get("parameter_cohort_sha256"),
        "candidate_setup": {
            key: setup.get(key) if setup else None
            for key in ("direction", "signal_close_ms", "pivot_price", "reclaim_close")
        },
        "receipt_inversions": {key: {"count": len(value), "sample": value[:3]} for key, value in inversion_map.items()},
        "contract_diagnosis": {
            "event_order": "close_ms_ascending_unique_is_required",
            "receipt_order": "may_differ_from_event_order_due_to_independent_snapshot_receipt_jitter",
            "causal_guard": "each_receipt_must_be_available_by_evaluation_and_frozen_action_cutoff",
            "strategy_parameters_affected": False,
        },
        "candidate_setups_observed": 1 if setup else 0,
        "events_admitted": ledger_rows,
        "outcomes_observed": 0,
        "outcome_metrics_inspected": False,
        "edge_evaluated": False,
        "strategy_failure": False,
        "operational_failure": True,
        "signals_allowed": False,
        "orders_allowed": False,
        "can_trade": False,
    }


def build_artifacts(
    *,
    predecessor: dict[str, Any],
    audit: dict[str, Any],
    frozen_at: str,
    forward_start: str,
    source_bindings: dict[str, tuple[str, str]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if audit.get("decision") != "bitunix_wo105_v3r3_zero_event_candle_receipt_order_contradiction_confirmed":
        raise ValueError("V3R3 receipt-order contradiction is not proven")
    tombstone = {
        "schema_version": 1,
        "generated_at": frozen_at,
        "status": "TOMBSTONED_POST_FLOOR_ZERO_EVENT_RECEIPT_ORDER_FAILURE",
        "decision": "bitunix_wo105_v3r3_tombstoned_without_edge_or_outcome_review",
        "cohort_id": predecessor["cohort_id"],
        "forward_start_at": predecessor["forward_start_at"],
        "parameter_cohort_sha256": predecessor["parameter_cohort_sha256"],
        "candidate_setups_observed": audit["candidate_setups_observed"],
        "events_admitted": 0,
        "outcomes_observed": 0,
        "outcome_metrics_inspected": False,
        "edge_evaluated": False,
        "strategy_failure": False,
        "operational_failure": True,
        "failure_class": "candle_receipt_order_contradicted_event_order",
        "audit": audit,
        "restart_allowed": False,
        "backfill_allowed": False,
        "retune_allowed": False,
        "resume_allowed": False,
        "signals_allowed": False,
        "orders_allowed": False,
        "capital_permission": "DENY",
        "can_trade": False,
    }
    lock = copy.deepcopy(predecessor)
    lock["schema"] = evaluator.SCHEMA
    lock["status"] = evaluator.STATUS
    lock["cohort_id"] = "SETUP_A_SFP_FAILED_BREAKOUT_WO105_V3R4_20260715"
    lock["frozen_at_utc"] = frozen_at
    lock["forward_start_at"] = forward_start
    lock["strategy_parameters_mutated_from_v3r3"] = False
    lock["runtime_contract"]["candle_series_order"] = {
        "event_order": "close_ms_ascending_unique",
        "receipt_order": "not_required_monotonic",
        "availability_rule": "received_at_lte_evaluation_and_frozen_action_cutoff",
        "applies_to": sorted(evaluator.CANDLE_SERIES),
        "book_trade_receipt_order_unchanged": True,
    }
    bindings = lock["bindings"]
    bindings["evaluator"] = evaluator.TOOL_PATH
    bindings["evaluator_sha256"] = source_bindings["evaluator_v4"][1]
    bindings["packet_assembler"] = "tools/bitunix_wo105_packet_assembler_v6.py"
    bindings["packet_assembler_sha256"] = source_bindings["packet_assembler_v6"][1]
    for key, (path, digest) in source_bindings.items():
        bindings[key] = path
        bindings[f"{key}_sha256"] = digest
    lock["operational_rollover"] = {
        "predecessor_cohort_id": predecessor["cohort_id"],
        "reason": "zero-event rollover after candle event order was incorrectly coupled to receipt order",
        "candidate_setups_observed": audit["candidate_setups_observed"],
        "events_admitted": 0,
        "outcomes_observed": 0,
        "outcome_metrics_inspected": False,
        "strategy_parameters_mutated": False,
        "historical_rows_admitted": 0,
        "predecessor_resume_allowed": False,
    }
    if lock["parameter_cohort_sha256"] != base.canonical_sha256(lock["params"]):
        raise ValueError("V3R4 parameter hash changed")
    return tombstone, lock


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Tombstone zero-event V3R3 receipt-order failure and freeze V3R4")
    parser.add_argument("--forward-start", default=DEFAULT_FORWARD_START)
    args = parser.parse_args()
    for output in (AUDIT, AUDIT_MD, TOMBSTONE, TOMBSTONE_MD, OUTPUT_LOCK):
        if output.exists():
            raise SystemExit(f"refusing to overwrite immutable rollover artifact: {output}")
    forward_ms = evaluator.parse_iso_ms(args.forward_start)
    if forward_ms is None or forward_ms <= now_ms() + 10 * 60 * 1000:
        raise SystemExit("V3R4 forward start must be timezone-aware and at least ten minutes in the future")

    predecessor = read_object(PREDECESSOR_LOCK)
    stop_receipt = read_object(STOP_RECEIPT)
    evaluation = read_object(LAST_EVALUATION)
    packet = read_object(LAST_PACKET)
    audit = build_audit(
        predecessor=predecessor,
        stop_receipt=stop_receipt,
        evaluation=evaluation,
        packet=packet,
        ledger_rows=nonempty_rows(LEDGER),
    )
    if audit["failures"]:
        raise SystemExit(f"V3R3 receipt-order audit failed: {audit['failures']}")
    write_json(AUDIT, audit)
    AUDIT_MD.write_text(
        "# Bitunix WO105 V3R3 receipt-order audit\n\n"
        "- One causal setup was assembled, but zero events entered the ledger.\n"
        "- No outcomes or edge metrics were inspected.\n"
        "- Candle event order was valid; independent REST receipt jitter made receipt timestamps non-monotonic.\n"
        "- V3R4 preserves all strategy parameters and validates causal availability at frozen cutoffs.\n"
        "- `can_trade=false`.\n",
        encoding="utf-8",
        newline="\n",
    )
    frozen_at = now_iso()
    bindings = {
        "predecessor_v3r3_lock": (portable(PREDECESSOR_LOCK), sha256_file(PREDECESSOR_LOCK)),
        "predecessor_v3r3_runtime_stop_receipt": (portable(STOP_RECEIPT), sha256_file(STOP_RECEIPT)),
        "predecessor_v3r3_last_evaluation": (portable(LAST_EVALUATION), sha256_file(LAST_EVALUATION)),
        "predecessor_v3r3_receipt_order_audit": (portable(AUDIT), sha256_file(AUDIT)),
        "evaluator_v4": (evaluator.TOOL_PATH, sha256_file(ROOT / evaluator.TOOL_PATH)),
        "packet_assembler_v6": (
            "tools/bitunix_wo105_packet_assembler_v6.py",
            sha256_file(ROOT / "tools/bitunix_wo105_packet_assembler_v6.py"),
        ),
    }
    tombstone, lock = build_artifacts(
        predecessor=predecessor,
        audit=audit,
        frozen_at=frozen_at,
        forward_start=args.forward_start,
        source_bindings=bindings,
    )
    write_json(TOMBSTONE, tombstone)
    TOMBSTONE_MD.write_text(
        "# Bitunix WO105 V3R3 receipt-order tombstone\n\n"
        "- Post-floor operational failure, but zero admitted events and zero inspected outcomes.\n"
        "- Failure class: candle receipt order was incorrectly required to match event order.\n"
        "- Strategy parameters were not changed; V3R3 cannot resume or backfill.\n"
        "- `can_trade=false`.\n",
        encoding="utf-8",
        newline="\n",
    )
    lock["bindings"]["predecessor_v3r3_tombstone"] = portable(TOMBSTONE)
    lock["bindings"]["predecessor_v3r3_tombstone_sha256"] = sha256_file(TOMBSTONE)
    failures = evaluator.validate_lock(lock)
    if failures:
        raise SystemExit(f"V3R4 lock validation failed: {failures}")
    write_json(OUTPUT_LOCK, lock)
    print(
        json.dumps(
            {
                "decision": "bitunix_wo105_v3r4_frozen_parameter_identical_receipt_contract_rollover",
                "cohort_id": lock["cohort_id"],
                "forward_start_at": lock["forward_start_at"],
                "parameter_cohort_sha256": lock["parameter_cohort_sha256"],
                "can_trade": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

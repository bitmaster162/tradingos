#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bitunix_wo105_causal_shadow_evaluator_v3 as evaluator  # noqa: E402
from tools import bitunix_wo105_liquidation_context as liquidation_v1  # noqa: E402
from tools import bitunix_wo105_liquidation_context_v2 as liquidation_v2  # noqa: E402


TOOL_PATH = "tools/bitunix_wo105_v3r2_rollover.py"
DEFAULT_FORWARD_START = "2026-07-14T18:00:00Z"
PREDECESSOR_LOCK = ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R1_2026-07-14.json"
FIRST_CYCLE = ROOT / "docs" / "BITUNIX_WO105_V3R1_FIRST_CYCLE_GATE_2026-07-14.json"
STATUS = ROOT / "docs" / "BITUNIX_WO105_V3R1_STATUS_2026-07-14.json"
STOP_RECEIPT = ROOT / "docs" / "BITUNIX_WO105_V3R1_RUNTIME_STOP_RECEIPT_2026-07-14.json"
LEDGER = ROOT / "_dl" / "bitunix_wo105_shadow_v3r1" / "EVENT_LEDGER.jsonl"
LIQUIDATION_DIR = ROOT / "data" / "live" / "liquidations" / "binance_force_order"
CLOCK_AUDIT = ROOT / "docs" / "BITUNIX_WO105_V3R1_LIQUIDATION_CLOCK_CONTRACT_AUDIT_2026-07-14.json"
CLOCK_AUDIT_MD = CLOCK_AUDIT.with_suffix(".md")
TOMBSTONE = ROOT / "docs" / "BITUNIX_WO105_V3R1_CLOCK_CONTRACT_TOMBSTONE_2026-07-14.json"
TOMBSTONE_MD = TOMBSTONE.with_suffix(".md")
OUTPUT_LOCK = ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R2_2026-07-14.json"


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
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def portable(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def nonempty_ledger_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip())


def build_clock_audit(rows: list[dict[str, Any]], *, floor_ms: int, cutoff_ms: int) -> dict[str, Any]:
    legacy = liquidation_v1.build_context(rows, floor_ms=floor_ms, cutoff_ms=cutoff_ms)
    corrected = liquidation_v2.build_context(rows, floor_ms=floor_ms, cutoff_ms=cutoff_ms)
    post_floor = [
        row
        for row in rows
        if row.get("symbol") == "BTCUSDT"
        and isinstance(row.get("event_time_ms"), int)
        and isinstance(row.get("received_at_ns"), int)
        and floor_ms <= row["event_time_ms"] <= cutoff_ms
        and floor_ms <= row["received_at_ns"] // 1_000_000 <= cutoff_ms
        and row.get("ingest_schema_version") == 2
    ]
    deltas = [row["received_at_ns"] // 1_000_000 - row["event_time_ms"] for row in post_floor]
    decision = (
        "bitunix_wo105_v3r1_zero_skew_contract_defect_confirmed"
        if legacy["accepted_events"] == 0
        and legacy["rejection_counts"].get("event_after_receipt", 0) > 0
        and corrected["record"] is not None
        else "bitunix_wo105_v3r1_clock_contract_rollover_not_proven"
    )
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "tool": TOOL_PATH,
        "decision": decision,
        "forward_floor_ms": floor_ms,
        "cutoff_ms": cutoff_ms,
        "post_floor_schema_v2_btc_rows": len(post_floor),
        "receive_minus_event_ms": {
            "minimum": min(deltas) if deltas else None,
            "median": statistics.median(deltas) if deltas else None,
            "maximum": max(deltas) if deltas else None,
            "negative_rows": sum(value < 0 for value in deltas),
            "nonnegative_rows": sum(value >= 0 for value in deltas),
        },
        "legacy_adapter": {
            "accepted_events": legacy["accepted_events"],
            "rejection_counts": legacy["rejection_counts"],
            "blockers": legacy["blockers"],
        },
        "versioned_adapter": {
            "accepted_events": corrected["accepted_events"],
            "total_liquidated_notional_usd": corrected["total_liquidated_notional_usd"],
            "maximum_observed_clock_lead_ms": corrected["maximum_observed_clock_lead_ms"],
            "max_clock_skew_ms": corrected["max_clock_skew_ms"],
            "causal_availability_rule": corrected["causal_availability_rule"],
            "blockers": corrected["blockers"],
        },
        "strategy_parameters_inspected": False,
        "outcome_metrics_inspected": False,
        "edge_evaluated": False,
        "signals_allowed": False,
        "orders_allowed": False,
        "capital_permission": "DENY",
        "can_trade": False,
    }


def validate_rollover_inputs(
    *,
    predecessor: dict[str, Any],
    first_cycle: dict[str, Any],
    status: dict[str, Any],
    stop_receipt: dict[str, Any],
    clock_audit: dict[str, Any],
    ledger_rows: int,
) -> None:
    expected_cohort = "SETUP_A_SFP_FAILED_BREAKOUT_WO105_V3R1_20260714"
    if predecessor.get("cohort_id") != expected_cohort:
        raise ValueError("unexpected V3R1 predecessor cohort")
    if predecessor.get("parameter_cohort_sha256") != evaluator.canonical_sha256(predecessor.get("params")):
        raise ValueError("V3R1 parameter hash mismatch")
    if first_cycle.get("cohort_id") != expected_cohort or first_cycle.get("can_trade") is not False:
        raise ValueError("V3R1 first-cycle identity or boundary invalid")
    if first_cycle.get("decision") != "bitunix_wo105_v3_first_cycle_accepted_shadow_only":
        raise ValueError("V3R1 first-cycle has not completed successfully")
    if not all((first_cycle.get("checks") or {}).values()) or first_cycle.get("failures"):
        raise ValueError("V3R1 first-cycle checks are incomplete")
    if status.get("cohort_id") != expected_cohort or status.get("edge_evaluated") is not False:
        raise ValueError("V3R1 status identity or blindness invalid")
    if int(status.get("forward_events") or 0) != 0 or int(status.get("terminal_forward_events") or 0) != 0:
        raise ValueError("V3R1 is not a zero-event operational rollover")
    if ledger_rows != 0:
        raise ValueError("V3R1 ledger is not empty")
    if stop_receipt.get("decision") != "bitunix_wo105_v3r1_runtime_stopped_verified":
        raise ValueError("V3R1 verified stop receipt missing")
    if stop_receipt.get("can_trade") is not False:
        raise ValueError("V3R1 stop receipt boundary invalid")
    if clock_audit.get("decision") != "bitunix_wo105_v3r1_zero_skew_contract_defect_confirmed":
        raise ValueError("clock-contract defect not proven")
    if clock_audit.get("outcome_metrics_inspected") is not False:
        raise ValueError("clock audit is not outcome-blind")


def build_artifacts(
    *,
    predecessor: dict[str, Any],
    first_cycle: dict[str, Any],
    status: dict[str, Any],
    stop_receipt: dict[str, Any],
    clock_audit: dict[str, Any],
    frozen_at: str,
    forward_start: str,
    source_bindings: dict[str, tuple[str, str]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_rollover_inputs(
        predecessor=predecessor,
        first_cycle=first_cycle,
        status=status,
        stop_receipt=stop_receipt,
        clock_audit=clock_audit,
        ledger_rows=0,
    )
    tombstone = {
        "schema_version": 1,
        "generated_at": frozen_at,
        "status": "TOMBSTONED_POST_FLOOR_ZERO_EVENT_CAUSAL_CLOCK_CONTRACT_FAILURE",
        "decision": "bitunix_wo105_v3r1_tombstoned_without_strategy_or_outcome_review",
        "cohort_id": predecessor["cohort_id"],
        "forward_start_at": predecessor["forward_start_at"],
        "parameter_cohort_sha256": predecessor["parameter_cohort_sha256"],
        "events_observed": 0,
        "terminal_events_observed": 0,
        "outcomes_observed": 0,
        "interim_outcome_metrics_inspected": False,
        "strategy_failure": False,
        "clock_contract_failure": True,
        "clock_contract_audit": clock_audit,
        "source_bindings": {
            key: {"path": path, "sha256": digest} for key, (path, digest) in source_bindings.items()
        },
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
    lock["cohort_id"] = "SETUP_A_SFP_FAILED_BREAKOUT_WO105_V3R2_20260714"
    lock["frozen_at_utc"] = frozen_at
    lock["forward_start_at"] = forward_start
    lock["strategy_parameters_mutated_from_v3"] = False
    lock["strategy_parameters_mutated_from_v3r1"] = False
    lock["runtime_contract"]["liquidation_timestamp_adapter"] = {
        "version": "v2",
        "max_clock_skew_ms": liquidation_v2.DEFAULT_MAX_CLOCK_SKEW_MS,
        "bound_derivation": "existing_reviewed_public_ws_future_skew_bound",
        "causal_availability_rule": "max(event_time_ms,raw_received_at_ms)",
        "lookahead_allowed": False,
    }
    bindings = lock["bindings"]
    bindings["liquidation_context"] = TOOL_PATH.replace("v3r2_rollover", "liquidation_context_v2")
    bindings["liquidation_context_sha256"] = source_bindings["liquidation_context_v2"][1]
    bindings["packet_assembler"] = "tools/bitunix_wo105_packet_assembler_v4.py"
    bindings["packet_assembler_sha256"] = source_bindings["packet_assembler_v4"][1]
    bindings.pop("predecessor_v3_first_cycle_gate", None)
    bindings.pop("predecessor_v3_first_cycle_gate_sha256", None)
    for key, (path, digest) in source_bindings.items():
        bindings[key] = path
        bindings[f"{key}_sha256"] = digest
    lock["operational_rollover"] = {
        "predecessor_cohort_id": predecessor["cohort_id"],
        "reason": "zero-event rollover after impossible zero-tolerance cross-host timestamp contract",
        "events_observed": 0,
        "outcomes_observed": 0,
        "interim_outcome_metrics_inspected": False,
        "strategy_parameters_mutated": False,
        "historical_rows_admitted": 0,
        "predecessor_resume_allowed": False,
    }
    if lock["parameter_cohort_sha256"] != evaluator.canonical_sha256(lock["params"]):
        raise ValueError("V3R2 parameter hash changed")
    return tombstone, lock


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Tombstone zero-event V3R1 clock contract and freeze V3R2")
    parser.add_argument("--forward-start", default=DEFAULT_FORWARD_START)
    args = parser.parse_args()
    for output in (CLOCK_AUDIT, CLOCK_AUDIT_MD, TOMBSTONE, TOMBSTONE_MD, OUTPUT_LOCK):
        if output.exists():
            raise SystemExit(f"refusing to overwrite immutable rollover artifact: {output}")
    forward_ms = evaluator.parse_iso_ms(args.forward_start)
    if forward_ms is None or forward_ms <= now_ms() + 10 * 60 * 1000:
        raise SystemExit("V3R2 forward start must be timezone-aware and at least ten minutes in the future")

    predecessor = read_object(PREDECESSOR_LOCK)
    first_cycle = read_object(FIRST_CYCLE)
    status = read_object(STATUS)
    stop_receipt = read_object(STOP_RECEIPT)
    rows, load_failures = liquidation_v1.load_rows(LIQUIDATION_DIR)
    if load_failures:
        raise SystemExit(f"liquidation source decode failures: {load_failures}")
    floor_ms = evaluator.parse_iso_ms(predecessor.get("forward_start_at"))
    if floor_ms is None:
        raise SystemExit("V3R1 forward floor invalid")
    clock_audit = build_clock_audit(rows, floor_ms=floor_ms, cutoff_ms=now_ms())
    frozen_at = now_iso()
    preliminary_bindings = {
        "predecessor_v3r1_lock": (portable(PREDECESSOR_LOCK), sha256_file(PREDECESSOR_LOCK)),
        "predecessor_v3r1_first_cycle_gate": (portable(FIRST_CYCLE), sha256_file(FIRST_CYCLE)),
        "predecessor_v3r1_status": (portable(STATUS), sha256_file(STATUS)),
        "predecessor_v3r1_runtime_stop_receipt": (portable(STOP_RECEIPT), sha256_file(STOP_RECEIPT)),
        "liquidation_context_v2": (portable(ROOT / liquidation_v2.TOOL_PATH), sha256_file(ROOT / liquidation_v2.TOOL_PATH)),
        "packet_assembler_v4": ("tools/bitunix_wo105_packet_assembler_v4.py", sha256_file(ROOT / "tools/bitunix_wo105_packet_assembler_v4.py")),
    }
    validate_rollover_inputs(
        predecessor=predecessor,
        first_cycle=first_cycle,
        status=status,
        stop_receipt=stop_receipt,
        clock_audit=clock_audit,
        ledger_rows=nonempty_ledger_rows(LEDGER),
    )
    write_json(CLOCK_AUDIT, clock_audit)
    CLOCK_AUDIT_MD.write_text(
        "# Bitunix WO105 V3R1 liquidation clock-contract audit\n\n"
        f"- Decision: `{clock_audit['decision']}`.\n"
        f"- Legacy/versioned accepted events: `{clock_audit['legacy_adapter']['accepted_events']}` / `{clock_audit['versioned_adapter']['accepted_events']}`.\n"
        "- The strategy and outcome metrics were not inspected.\n"
        "- This is an operational causal-timestamp correction; `can_trade=false`.\n",
        encoding="utf-8",
        newline="\n",
    )
    preliminary_bindings["predecessor_v3r1_clock_contract_audit"] = (portable(CLOCK_AUDIT), sha256_file(CLOCK_AUDIT))
    tombstone, lock = build_artifacts(
        predecessor=predecessor,
        first_cycle=first_cycle,
        status=status,
        stop_receipt=stop_receipt,
        clock_audit=clock_audit,
        frozen_at=frozen_at,
        forward_start=args.forward_start,
        source_bindings=preliminary_bindings,
    )
    write_json(TOMBSTONE, tombstone)
    TOMBSTONE_MD.write_text(
        "# Bitunix WO105 V3R1 clock-contract tombstone\n\n"
        "- Zero admitted events and zero inspected outcomes.\n"
        "- Failure class: impossible zero-tolerance cross-host timestamp contract.\n"
        "- Strategy parameters were not changed; V3R1 cannot resume.\n"
        "- `can_trade=false`.\n",
        encoding="utf-8",
        newline="\n",
    )
    lock["bindings"]["predecessor_v3r1_tombstone"] = portable(TOMBSTONE)
    lock["bindings"]["predecessor_v3r1_tombstone_sha256"] = sha256_file(TOMBSTONE)
    write_json(OUTPUT_LOCK, lock)
    print(
        json.dumps(
            {
                "decision": "bitunix_wo105_v3r2_frozen_parameter_identical_clock_contract_rollover",
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

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

from tools import bitunix_wo105_causal_shadow_evaluator_v3 as evaluator  # noqa: E402


DEFAULT_FORWARD_START = "2026-07-14T17:00:00Z"
PREDECESSOR_LOCK = ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3_2026-07-14.json"
SNAPSHOT = ROOT / "docs" / "BITUNIX_WO108_POST_FLOOR_OBSERVATION_2026-07-14.json"
FIRST_CYCLE = ROOT / "docs" / "BITUNIX_WO105_V3_FIRST_CYCLE_GATE_2026-07-14.json"
STOP_RECEIPT = ROOT / "docs" / "BITUNIX_WO105_V3_RUNTIME_STOP_RECEIPT_2026-07-14.json"
TOMBSTONE = ROOT / "docs" / "BITUNIX_WO105_V3_FIRST_CYCLE_OPERATIONAL_TOMBSTONE_2026-07-14.json"
TOMBSTONE_MD = ROOT / "docs" / "BITUNIX_WO105_V3_FIRST_CYCLE_OPERATIONAL_TOMBSTONE_2026-07-14.md"
OUTPUT_LOCK = ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R1_2026-07-14.json"


def read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object: {path}")
    return payload


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def portable(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def build_artifacts(
    *,
    predecessor: dict[str, Any],
    snapshot: dict[str, Any],
    first_cycle: dict[str, Any],
    stop_receipt: dict[str, Any],
    frozen_at: str,
    forward_start: str,
    source_bindings: dict[str, tuple[str, str]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if snapshot.get("decision") != "bitunix_wo108_v3_zero_event_operational_rollover_required":
        raise ValueError("WO108 snapshot does not authorize zero-event rollover")
    if snapshot.get("rollover_eligible") is not True:
        raise ValueError("WO108 rollover eligibility is not true")
    forward = snapshot.get("forward") if isinstance(snapshot.get("forward"), dict) else {}
    ledger = snapshot.get("ledger") if isinstance(snapshot.get("ledger"), dict) else {}
    packet = snapshot.get("packet") if isinstance(snapshot.get("packet"), dict) else {}
    if any((forward.get("forward_events"), forward.get("terminal_forward_events"), ledger.get("rows"))):
        raise ValueError("predecessor is not zero-event")
    if packet.get("present") is not False or packet.get("evaluation_run") is not False:
        raise ValueError("predecessor packet/evaluation state is not blind")
    if forward.get("interim_outcome_values_accessed") is not False or forward.get("interim_outcome_metrics_disclosed") is not False:
        raise ValueError("predecessor outcome blindness is not proven")
    if first_cycle.get("decision") != "bitunix_wo105_v3_first_cycle_operational_blocked":
        raise ValueError("V3 first-cycle gate is not terminally blocked")
    if set(first_cycle.get("overdue") or []) != {"loop_transitioned_after_floor", "post_floor_rest_snapshot"}:
        raise ValueError("unexpected V3 first-cycle failure set")
    if first_cycle.get("failures") not in ([], None):
        raise ValueError("V3 first-cycle integrity failures are present")
    if stop_receipt.get("decision") != "bitunix_wo105_v3_runtime_stopped_verified":
        raise ValueError("V3 runtime stop is not verified")
    if stop_receipt.get("exact_script_pids_remaining") not in ([], None):
        raise ValueError("V3 process identity still exists")
    if stop_receipt.get("receipt_removed") is not True or stop_receipt.get("lock_removed") is not True:
        raise ValueError("V3 lifecycle artifacts were not safely retired")
    if parse_iso(forward_start) <= parse_iso(frozen_at):
        raise ValueError("V3R1 forward floor must be after freeze")

    params = copy.deepcopy(predecessor.get("params"))
    if not isinstance(params, dict) or canonical_sha256(params) != predecessor.get("parameter_cohort_sha256"):
        raise ValueError("V3 frozen parameter object drifted")
    if predecessor.get("can_trade") is not False:
        raise ValueError("V3 predecessor is not fail-closed")

    tombstone = {
        "schema_version": 1,
        "generated_at": frozen_at,
        "status": "TOMBSTONED_POST_FLOOR_OPERATIONAL_STARTUP_DEADLINE_FAILURE",
        "decision": "bitunix_wo105_v3_tombstoned_zero_event_without_outcome_review",
        "cohort_id": predecessor.get("cohort_id"),
        "forward_start_at": predecessor.get("forward_start_at"),
        "parameter_cohort_sha256": predecessor.get("parameter_cohort_sha256"),
        "events_observed": 0,
        "terminal_events_observed": 0,
        "outcomes_observed": 0,
        "interim_outcome_values_accessed": False,
        "interim_outcome_metrics_disclosed": False,
        "operational_failure": {
            "decision": first_cycle.get("decision"),
            "overdue": sorted(first_cycle.get("overdue") or []),
            "strategy_failure": False,
            "data_result_failure": False,
            "startup_deadline_failure": True,
        },
        "source_bindings": {
            key: {"path": value[0], "sha256": value[1]} for key, value in sorted(source_bindings.items())
        },
        "restart_allowed": False,
        "backfill_allowed": False,
        "retune_allowed": False,
        "resume_allowed": False,
        "signals_allowed": False,
        "paper_entries_allowed": False,
        "orders_allowed": False,
        "capital_permission": "DENY",
        "can_trade": False,
    }

    lock = copy.deepcopy(predecessor)
    lock["cohort_id"] = "SETUP_A_SFP_FAILED_BREAKOUT_WO105_V3R1_20260714"
    lock["frozen_at_utc"] = frozen_at
    lock["forward_start_at"] = forward_start
    lock["status"] = evaluator.STATUS
    lock["strategy_parameters_mutated_from_v3"] = False
    lock["operational_rollover"] = {
        "predecessor_cohort_id": predecessor.get("cohort_id"),
        "reason": "zero-event operational rollover after immutable first-cycle startup deadlines were missed",
        "events_observed": 0,
        "outcomes_observed": 0,
        "interim_outcome_metrics_inspected": False,
        "strategy_parameters_mutated": False,
        "historical_rows_admitted": 0,
        "predecessor_resume_allowed": False,
    }
    bindings = lock.setdefault("bindings", {})
    for key, (path, digest) in source_bindings.items():
        bindings[key] = path
        bindings[f"{key}_sha256"] = digest
    lock["parameter_cohort_sha256"] = canonical_sha256(params)
    lock["can_trade"] = False
    failures = evaluator.validate_lock(lock)
    if failures:
        raise ValueError(f"V3R1 lock does not satisfy unchanged evaluator: {','.join(failures)}")
    return tombstone, lock


def render_tombstone(payload: dict[str, Any]) -> str:
    overdue = ", ".join(payload["operational_failure"]["overdue"])
    return (
        "# Bitunix WO105 V3 operational tombstone\n\n"
        f"- Status: `{payload['status']}`\n"
        f"- Cohort: `{payload['cohort_id']}`\n"
        "- Forward events/outcomes: `0 / 0`.\n"
        "- Interim outcome metrics inspected: `false`.\n"
        f"- Immutable startup deadlines missed: `{overdue}`.\n"
        "- This is an operational startup failure, not a strategy result.\n"
        "- V3 may not restart, resume, backfill, or retune. Its parameter-identical successor is V3R1.\n"
        "- Signals, paper entries, orders, and capital remain forbidden; `can_trade=false`.\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Tombstone zero-event WO105 V3 and freeze parameter-identical V3R1")
    parser.add_argument("--forward-start", default=DEFAULT_FORWARD_START)
    args = parser.parse_args()
    for output in (TOMBSTONE, TOMBSTONE_MD, OUTPUT_LOCK):
        if output.exists():
            raise SystemExit(f"refusing to overwrite immutable rollover artifact: {output}")

    predecessor = read_object(PREDECESSOR_LOCK)
    snapshot = read_object(SNAPSHOT)
    first_cycle = read_object(FIRST_CYCLE)
    stop_receipt = read_object(STOP_RECEIPT)
    frozen_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    source_bindings = {
        "predecessor_v3_lock": (portable(PREDECESSOR_LOCK), sha256_file(PREDECESSOR_LOCK)),
        "predecessor_v3_first_cycle_gate": (portable(FIRST_CYCLE), sha256_file(FIRST_CYCLE)),
        "predecessor_v3_runtime_stop_receipt": (portable(STOP_RECEIPT), sha256_file(STOP_RECEIPT)),
        "wo108_post_floor_snapshot": (portable(SNAPSHOT), sha256_file(SNAPSHOT)),
    }
    tombstone, lock = build_artifacts(
        predecessor=predecessor,
        snapshot=snapshot,
        first_cycle=first_cycle,
        stop_receipt=stop_receipt,
        frozen_at=frozen_at,
        forward_start=args.forward_start,
        source_bindings=source_bindings,
    )
    TOMBSTONE.write_text(json.dumps(tombstone, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    TOMBSTONE_MD.write_text(render_tombstone(tombstone), encoding="utf-8", newline="\n")
    lock["bindings"]["predecessor_v3_tombstone"] = portable(TOMBSTONE)
    lock["bindings"]["predecessor_v3_tombstone_sha256"] = sha256_file(TOMBSTONE)
    OUTPUT_LOCK.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "decision": "bitunix_wo105_v3r1_frozen_parameter_identical_operational_rollover",
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

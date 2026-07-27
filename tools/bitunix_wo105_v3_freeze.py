#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FORWARD_START = "2026-07-14T14:00:00Z"
V2_LOCK = ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V2_2026-07-14.json"
V2_TOMBSTONE = ROOT / "docs" / "BITUNIX_WO105_V2_PRE_FLOOR_RUNTIME_TOMBSTONE_2026-07-14.json"
CAUSAL_AUDIT = ROOT / "docs" / "BITUNIX_WO105_V2_CAUSAL_LIFECYCLE_AUDIT_2026-07-14.json"
V3_EVALUATOR = ROOT / "tools" / "bitunix_wo105_causal_shadow_evaluator_v3.py"
V3_ASSEMBLER = ROOT / "tools" / "bitunix_wo105_packet_assembler_v3.py"
V2_ASSEMBLER = ROOT / "tools" / "bitunix_wo105_packet_assembler.py"


def read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object: {path}")
    return payload


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def portable(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def bound(path: Path) -> tuple[str, str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return portable(path), sha256_file(path)


def build_lock(*, frozen_at: str, forward_start: str) -> dict[str, Any]:
    previous = read_object(V2_LOCK)
    tombstone = read_object(V2_TOMBSTONE)
    audit = read_object(CAUSAL_AUDIT)
    if tombstone.get("status") != "TOMBSTONED_PRE_FLOOR_CAUSAL_LIFECYCLE_GAP":
        raise ValueError("V2 tombstone is not terminal")
    if tombstone.get("events_observed") != 0 or tombstone.get("outcomes_observed") != 0:
        raise ValueError("V2 tombstone is not zero-event")
    if audit.get("events_observed") != 0 or audit.get("outcomes_observed") != 0:
        raise ValueError("causal audit is not outcome-blind")
    if parse_iso(forward_start) <= parse_iso(frozen_at):
        raise ValueError("forward floor must be after freeze")

    bindings = copy.deepcopy(previous["bindings"])
    bindings["v2_evaluator"] = previous["bindings"]["evaluator"]
    bindings["v2_evaluator_sha256"] = previous["bindings"]["evaluator_sha256"]
    bindings["v2_lock"], bindings["v2_lock_sha256"] = bound(V2_LOCK)
    bindings["v2_packet_assembler"], bindings["v2_packet_assembler_sha256"] = bound(V2_ASSEMBLER)
    bindings["evaluator"], bindings["evaluator_sha256"] = bound(V3_EVALUATOR)
    bindings["packet_assembler"], bindings["packet_assembler_sha256"] = bound(V3_ASSEMBLER)
    bindings["v2_tombstone"], bindings["v2_tombstone_sha256"] = bound(V2_TOMBSTONE)
    bindings["causal_lifecycle_audit"], bindings["causal_lifecycle_audit_sha256"] = bound(CAUSAL_AUDIT)

    params = copy.deepcopy(previous["params"])
    parameter_hash = canonical_sha256(params)
    if parameter_hash != previous.get("parameter_cohort_sha256"):
        raise ValueError("V2 parameter object no longer matches its frozen hash")
    return {
        "schema": "bitunix-wo105-causal-shadow-prereg-v3",
        "cohort_id": "SETUP_A_SFP_FAILED_BREAKOUT_WO105_V3_20260714",
        "frozen_at_utc": frozen_at,
        "forward_start_at": forward_start,
        "status": "FROZEN_CAUSAL_SHADOW_EVALUATOR_V3",
        "parameter_cohort_sha256": parameter_hash,
        "strategy_parameters_mutated_from_v2": False,
        "supersedes_for_evaluation_only": {
            "cohort_id": previous["cohort_id"],
            "v2_mutated": False,
            "v2_events_observed": 0,
            "v2_outcomes_observed": 0,
            "interim_outcome_metrics_inspected": False,
            "reason": "V2 was tombstoned before its floor because its bound latest-snapshot selection and current-setup-only orchestration could not satisfy the frozen causal lifecycle. V3 changes runtime delivery only and copies the V2 parameter object unchanged.",
        },
        "runtime_contract": {
            "receipt_selection": "earliest_received_record_per_close_ms",
            "late_refetch_policy": "never_replace_an_earlier_valid_receipt",
            "rest_schedule": {
                "cadence_seconds": 300,
                "post_close_offset_seconds": 2,
                "maximum_frozen_action_latency_ms": 5000,
            },
            "event_lifecycle": {
                "event_packet_archive_required": True,
                "archive_mutability": "immutable",
                "open_event_continuation": "immutable_initial_packet_plus_dynamic_post_entry_series",
                "active_states": ["HOLD", "SHADOW_OPEN"],
                "continuation_identity": "original_event_id_and_source_manifest_sha256",
                "manifest_drift": "CAPTURE_INVALID_NO_LEDGER_APPEND",
            },
            "historical_backfill_allowed": False,
            "retuning_allowed": False,
        },
        "bindings": bindings,
        "scope": copy.deepcopy(previous["scope"]),
        "params": params,
        "can_trade": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze the zero-event WO105 V3 causal-lifecycle successor")
    parser.add_argument("--forward-start", default=DEFAULT_FORWARD_START)
    parser.add_argument("--out", default="configs/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3_2026-07-14.json")
    args = parser.parse_args()
    output = Path(args.out)
    if not output.is_absolute():
        output = ROOT / output
    if output.exists():
        raise SystemExit(f"refusing to overwrite frozen lock: {output}")
    frozen_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    lock = build_lock(frozen_at=frozen_at, forward_start=args.forward_start)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "decision": "bitunix_wo105_v3_frozen_zero_event_successor",
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

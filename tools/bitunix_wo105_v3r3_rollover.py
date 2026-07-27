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
from tools import bitunix_wo105_liquidation_context as liquidation_v1  # noqa: E402
from tools import bitunix_wo105_liquidation_context_v2 as liquidation_v2  # noqa: E402
from tools import bitunix_wo105_liquidation_context_v3 as liquidation_v3  # noqa: E402


TOOL_PATH = "tools/bitunix_wo105_v3r3_rollover.py"
DEFAULT_FORWARD_START = "2026-07-14T19:30:00Z"
PREDECESSOR_LOCK = ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R2_2026-07-14.json"
LOOP_STATUS = ROOT / "logs" / "bitunix_wo105_v3r2" / "bitunix_wo105_v3r2_forward_loop_status.json"
STOP_RECEIPT = ROOT / "docs" / "BITUNIX_WO105_V3R2_RUNTIME_STOP_RECEIPT_2026-07-14.json"
CAPTURE_MANIFEST = (
    ROOT
    / "data"
    / "forward"
    / "bitunix_wo105_v3r2_ws"
    / "run_20260714T180001_227487Z"
    / "PUBLIC_CAPTURE_MANIFEST.json"
)
WS_INTAKE = ROOT / "_dl" / "bitunix_wo105_v3r2_ws_intake" / "WS_INTAKE_MANIFEST.json"
LEDGER = ROOT / "_dl" / "bitunix_wo105_shadow_v3r2" / "EVENT_LEDGER.jsonl"
INTERFACE_AUDIT = ROOT / "docs" / "BITUNIX_WO105_V3R2_ADAPTER_INTERFACE_AUDIT_2026-07-14.json"
INTERFACE_AUDIT_MD = INTERFACE_AUDIT.with_suffix(".md")
TOMBSTONE = ROOT / "docs" / "BITUNIX_WO105_V3R2_FIRST_CYCLE_OPERATIONAL_TOMBSTONE_2026-07-14.json"
TOMBSTONE_MD = TOMBSTONE.with_suffix(".md")
OUTPUT_LOCK = ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R3_2026-07-14.json"


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


def build_interface_audit(
    *,
    predecessor: dict[str, Any],
    loop_status: dict[str, Any],
    stop_receipt: dict[str, Any],
    capture: dict[str, Any],
    ws_intake: dict[str, Any],
    ledger_rows: int,
) -> dict[str, Any]:
    failures: list[str] = []
    if predecessor.get("cohort_id") != "SETUP_A_SFP_FAILED_BREAKOUT_WO105_V3R2_20260714":
        failures.append("predecessor_identity_invalid")
    if predecessor.get("parameter_cohort_sha256") != evaluator.canonical_sha256(predecessor.get("params")):
        failures.append("predecessor_parameter_hash_invalid")
    if loop_status.get("status") != "stopped" or loop_status.get("can_trade") is not False:
        failures.append("loop_not_stopped_fail_closed")
    if stop_receipt.get("decision") != "bitunix_wo105_v3r2_runtime_stopped_verified_after_interface_failure":
        failures.append("verified_stop_receipt_missing")
    if int(stop_receipt.get("ledger_rows") or 0) != 0 or ledger_rows != 0:
        failures.append("nonzero_event_ledger")
    capture_errors = capture.get("error_taxonomy") if isinstance(capture.get("error_taxonomy"), dict) else {}
    if int(capture.get("frames_total") or 0) < 100 or any(int(value or 0) for value in capture_errors.values()):
        failures.append("public_capture_not_clean")
    if (capture.get("subscription_acceptance") or {}).get("accepted") is not True:
        failures.append("public_capture_subscription_not_accepted")
    if ws_intake.get("decision") != "bitunix_wo105_ws_intake_ready" or ws_intake.get("can_trade") is not False:
        failures.append("ws_intake_not_ready")
    if hasattr(liquidation_v2, "load_rows"):
        failures.append("v2_interface_gap_not_reproduced")
    if liquidation_v3.load_rows is not liquidation_v1.load_rows:
        failures.append("v3_loader_not_bound_to_reviewed_base")
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "tool": TOOL_PATH,
        "decision": (
            "bitunix_wo105_v3r2_zero_event_adapter_interface_gap_confirmed"
            if not failures
            else "bitunix_wo105_v3r2_rollover_blocked"
        ),
        "failures": failures,
        "predecessor_cohort_id": predecessor.get("cohort_id"),
        "parameter_cohort_sha256": predecessor.get("parameter_cohort_sha256"),
        "capture": {
            "frames_total": capture.get("frames_total"),
            "error_taxonomy": capture_errors,
            "subscription_accepted": (capture.get("subscription_acceptance") or {}).get("accepted"),
            "started_utc": capture.get("started_utc"),
            "ended_utc": capture.get("ended_utc"),
        },
        "ws_intake": {
            "decision": ws_intake.get("decision"),
            "accepted_runs": ws_intake.get("accepted_runs"),
            "records": ws_intake.get("records"),
        },
        "interface_contract": {
            "assembler_required_method": "load_rows",
            "v2_adapter_has_load_rows": hasattr(liquidation_v2, "load_rows"),
            "v3_adapter_has_load_rows": hasattr(liquidation_v3, "load_rows"),
            "v3_context_semantics": "delegates_validate_row_and_build_context_to_v2",
            "native_stderr_failure": "AttributeError: module tools.bitunix_wo105_liquidation_context_v2 has no attribute load_rows",
        },
        "events_admitted": ledger_rows,
        "outcomes_observed": 0,
        "outcome_metrics_inspected": False,
        "strategy_failure": False,
        "operational_failure": True,
        "edge_evaluated": False,
        "signals_allowed": False,
        "orders_allowed": False,
        "capital_permission": "DENY",
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
    if audit.get("decision") != "bitunix_wo105_v3r2_zero_event_adapter_interface_gap_confirmed":
        raise ValueError("V3R2 interface gap is not proven")
    tombstone = {
        "schema_version": 1,
        "generated_at": frozen_at,
        "status": "TOMBSTONED_POST_FLOOR_ZERO_EVENT_ADAPTER_INTERFACE_FAILURE",
        "decision": "bitunix_wo105_v3r2_tombstoned_without_strategy_or_outcome_review",
        "cohort_id": predecessor["cohort_id"],
        "forward_start_at": predecessor["forward_start_at"],
        "parameter_cohort_sha256": predecessor["parameter_cohort_sha256"],
        "events_observed": 0,
        "terminal_events_observed": 0,
        "outcomes_observed": 0,
        "outcome_metrics_inspected": False,
        "strategy_failure": False,
        "operational_failure": True,
        "failure_class": "adapter_interface_missing_load_rows",
        "interface_audit": audit,
        "source_bindings": {key: {"path": path, "sha256": digest} for key, (path, digest) in source_bindings.items()},
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
    lock["cohort_id"] = "SETUP_A_SFP_FAILED_BREAKOUT_WO105_V3R3_20260714"
    lock["frozen_at_utc"] = frozen_at
    lock["forward_start_at"] = forward_start
    lock["strategy_parameters_mutated_from_v3r2"] = False
    lock["runtime_contract"]["liquidation_timestamp_adapter"] = {
        "version": "v3_interface_complete",
        "max_clock_skew_ms": liquidation_v3.DEFAULT_MAX_CLOCK_SKEW_MS,
        "bound_derivation": "unchanged_v2_clock_contract_plus_reviewed_base_load_rows_interface",
        "causal_availability_rule": "max(event_time_ms,raw_received_at_ms)",
        "required_loader": "load_rows",
        "lookahead_allowed": False,
    }
    bindings = lock["bindings"]
    bindings["liquidation_context"] = liquidation_v3.TOOL_PATH
    bindings["liquidation_context_sha256"] = source_bindings["liquidation_context_v3"][1]
    bindings["packet_assembler"] = "tools/bitunix_wo105_packet_assembler_v5.py"
    bindings["packet_assembler_sha256"] = source_bindings["packet_assembler_v5"][1]
    for key, (path, digest) in source_bindings.items():
        bindings[key] = path
        bindings[f"{key}_sha256"] = digest
    lock["operational_rollover"] = {
        "predecessor_cohort_id": predecessor["cohort_id"],
        "reason": "zero-event rollover after bound adapter omitted assembler-required load_rows interface",
        "events_observed": 0,
        "outcomes_observed": 0,
        "outcome_metrics_inspected": False,
        "strategy_parameters_mutated": False,
        "historical_rows_admitted": 0,
        "predecessor_resume_allowed": False,
    }
    if lock["parameter_cohort_sha256"] != evaluator.canonical_sha256(lock["params"]):
        raise ValueError("V3R3 parameter hash changed")
    return tombstone, lock


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Tombstone zero-event V3R2 interface failure and freeze V3R3")
    parser.add_argument("--forward-start", default=DEFAULT_FORWARD_START)
    args = parser.parse_args()
    for output in (INTERFACE_AUDIT, INTERFACE_AUDIT_MD, TOMBSTONE, TOMBSTONE_MD, OUTPUT_LOCK):
        if output.exists():
            raise SystemExit(f"refusing to overwrite immutable rollover artifact: {output}")
    forward_ms = evaluator.parse_iso_ms(args.forward_start)
    if forward_ms is None or forward_ms <= now_ms() + 10 * 60 * 1000:
        raise SystemExit("V3R3 forward start must be timezone-aware and at least ten minutes in the future")

    predecessor = read_object(PREDECESSOR_LOCK)
    loop_status = read_object(LOOP_STATUS)
    stop_receipt = read_object(STOP_RECEIPT)
    capture = read_object(CAPTURE_MANIFEST)
    ws_intake = read_object(WS_INTAKE)
    ledger_rows = nonempty_rows(LEDGER)
    audit = build_interface_audit(
        predecessor=predecessor,
        loop_status=loop_status,
        stop_receipt=stop_receipt,
        capture=capture,
        ws_intake=ws_intake,
        ledger_rows=ledger_rows,
    )
    if audit["failures"]:
        raise SystemExit(f"V3R2 interface audit failed: {audit['failures']}")
    write_json(INTERFACE_AUDIT, audit)
    INTERFACE_AUDIT_MD.write_text(
        "# Bitunix WO105 V3R2 adapter-interface audit\n\n"
        f"- Decision: `{audit['decision']}`.\n"
        f"- Public capture frames: `{audit['capture']['frames_total']}` with zero error taxonomy.\n"
        "- The bound V2 adapter omitted the assembler-required `load_rows` interface.\n"
        "- Zero ledger events and zero outcomes were admitted; this is not a strategy failure.\n"
        "- `can_trade=false`.\n",
        encoding="utf-8",
        newline="\n",
    )
    frozen_at = now_iso()
    preliminary_bindings = {
        "predecessor_v3r2_lock": (portable(PREDECESSOR_LOCK), sha256_file(PREDECESSOR_LOCK)),
        "predecessor_v3r2_runtime_stop_receipt": (portable(STOP_RECEIPT), sha256_file(STOP_RECEIPT)),
        "predecessor_v3r2_capture_manifest": (portable(CAPTURE_MANIFEST), sha256_file(CAPTURE_MANIFEST)),
        "predecessor_v3r2_ws_intake": (portable(WS_INTAKE), sha256_file(WS_INTAKE)),
        "predecessor_v3r2_interface_audit": (portable(INTERFACE_AUDIT), sha256_file(INTERFACE_AUDIT)),
        "liquidation_context_v3": (liquidation_v3.TOOL_PATH, sha256_file(ROOT / liquidation_v3.TOOL_PATH)),
        "packet_assembler_v5": (
            "tools/bitunix_wo105_packet_assembler_v5.py",
            sha256_file(ROOT / "tools/bitunix_wo105_packet_assembler_v5.py"),
        ),
    }
    tombstone, lock = build_artifacts(
        predecessor=predecessor,
        audit=audit,
        frozen_at=frozen_at,
        forward_start=args.forward_start,
        source_bindings=preliminary_bindings,
    )
    write_json(TOMBSTONE, tombstone)
    TOMBSTONE_MD.write_text(
        "# Bitunix WO105 V3R2 first-cycle operational tombstone\n\n"
        "- Zero admitted events and zero inspected outcomes.\n"
        "- Failure class: adapter interface omitted `load_rows`; capture transport itself passed.\n"
        "- Strategy parameters were not changed; V3R2 cannot resume.\n"
        "- `can_trade=false`.\n",
        encoding="utf-8",
        newline="\n",
    )
    lock["bindings"]["predecessor_v3r2_tombstone"] = portable(TOMBSTONE)
    lock["bindings"]["predecessor_v3r2_tombstone_sha256"] = sha256_file(TOMBSTONE)
    if evaluator.validate_lock(lock):
        raise SystemExit(f"V3R3 lock validation failed: {evaluator.validate_lock(lock)}")
    write_json(OUTPUT_LOCK, lock)
    print(
        json.dumps(
            {
                "decision": "bitunix_wo105_v3r3_frozen_parameter_identical_interface_rollover",
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

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bitunix_wo105_causal_shadow_evaluator_v2 as evaluator_v2  # noqa: E402
from tools import bitunix_wo105_causal_shadow_evaluator_v3 as evaluator_v3  # noqa: E402
from tools import bitunix_wo105_causal_shadow_evaluator_v4 as evaluator_v4  # noqa: E402


TOOL_PATH = "tools/bitunix_wo105_v2_status.py"
REVIEW_TERMINAL_STATES = {"NO_FILL", "SHADOW_CLOSED"}
evaluator = evaluator_v2


def select_evaluator(lock: dict[str, Any]):
    if lock.get("schema") == evaluator_v4.SCHEMA:
        return evaluator_v4
    return evaluator_v3 if lock.get("schema") == evaluator_v3.SCHEMA else evaluator_v2


def cohort_version(lock: dict[str, Any]) -> str:
    if lock.get("schema") == evaluator_v4.SCHEMA:
        return "v3r4"
    return "v3" if lock.get("schema") == evaluator_v3.SCHEMA else "v2"


def predecessor_tombstone(lock: dict[str, Any], supplied: dict[str, Any] | None) -> dict[str, Any] | None:
    """Use the immutable bound V3R3 tombstone for V3R4 instead of a legacy CLI default."""
    if lock.get("schema") != evaluator_v4.SCHEMA:
        return supplied
    bindings = lock.get("bindings") if isinstance(lock.get("bindings"), dict) else {}
    raw_path = bindings.get("predecessor_v3r3_tombstone")
    return read_optional(resolve(raw_path)) if isinstance(raw_path, str) and raw_path else None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_optional(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else None


def read_ledger(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    if not path.is_file():
        return rows, failures
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            failures.append(f"ledger_decode:{line_number}")
            continue
        if not isinstance(row, dict):
            failures.append(f"ledger_not_object:{line_number}")
            continue
        rows.append(row)
    return rows, failures


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_report(
    lock: dict[str, Any],
    *,
    tombstone: dict[str, Any] | None,
    packet_status: dict[str, Any] | None,
    ws_status: dict[str, Any] | None,
    liquidation_status: dict[str, Any] | None,
    ledger_path: Path,
    current_ms: int,
) -> dict[str, Any]:
    runtime_evaluator = select_evaluator(lock)
    version = cohort_version(lock)
    failures = runtime_evaluator.validate_lock(lock)
    tombstone = predecessor_tombstone(lock, tombstone)
    if version == "v3r4":
        expected_tombstone = "TOMBSTONED_POST_FLOOR_ZERO_EVENT_RECEIPT_ORDER_FAILURE"
        predecessor = "v3r3"
    elif version == "v3":
        expected_tombstone = evaluator_v3.V2_TOMBSTONE_STATUS
        predecessor = "v2"
    else:
        expected_tombstone = "TOMBSTONED_PRE_FLOOR_UNIT_CONTRACT_GAP"
        predecessor = "v1"
    if tombstone is None or tombstone.get("status") != expected_tombstone:
        failures.append(f"{predecessor}_tombstone_missing_or_invalid")
    rows, ledger_failures = read_ledger(ledger_path)
    failures.extend(ledger_failures)
    floor = runtime_evaluator.parse_iso_ms(lock.get("forward_start_at"))
    latest: dict[str, dict[str, Any]] = {}
    state_counts: Counter[str] = Counter()
    allowed_states = set((lock.get("params") or {}).get("lifecycle_states") or []) | {"HOLD"}
    for index, row in enumerate(rows, start=1):
        state = row.get("state")
        event_id = row.get("event_id")
        if state not in allowed_states:
            failures.append(f"ledger_state_invalid:{index}")
        if not isinstance(event_id, str) or len(event_id) != 64:
            failures.append(f"ledger_event_id_invalid:{index}")
            continue
        if row.get("cohort_binding_sha256") != lock.get("parameter_cohort_sha256"):
            failures.append(f"ledger_cohort_binding_mismatch:{index}")
        if state in REVIEW_TERMINAL_STATES:
            details = row.get("details") if isinstance(row.get("details"), dict) else {}
            setup = details.get("setup") if isinstance(details.get("setup"), dict) else {}
            signal_close_ms = setup.get("signal_close_ms")
            if not isinstance(signal_close_ms, int):
                failures.append(f"ledger_terminal_signal_close_missing:{index}")
            elif floor is None or signal_close_ms < floor:
                failures.append(f"ledger_terminal_signal_before_floor:{index}")
        previous = latest.get(event_id)
        if previous and previous.get("state") in runtime_evaluator.TERMINAL_STATES:
            failures.append(f"ledger_update_after_terminal:{index}")
        latest[event_id] = row
        state_counts[str(state)] += 1
    minimum = int((lock.get("params") or {}).get("evaluation", {}).get("minimum_new_post_freeze_events") or 30)
    forward_events = len(latest)
    terminal_forward_events = sum(1 for row in latest.values() if row.get("state") in REVIEW_TERMINAL_STATES)
    lock_ready = not failures
    if floor is None:
        phase = "HOLD_INVALID_FLOOR"
    elif current_ms < floor:
        phase = "WAITING_FORWARD_FLOOR"
    else:
        phase = "FORWARD_COLLECTION"
    packet_decision = packet_status.get("decision") if packet_status else "packet_assembly_status_missing"
    review_ready = lock_ready and current_ms >= (floor or current_ms + 1) and terminal_forward_events >= minimum
    if not lock_ready:
        decision = f"bitunix_wo105_{version}_hold_integrity_or_ledger_invalid"
    elif phase == "WAITING_FORWARD_FLOOR":
        decision = f"bitunix_wo105_{version}_ready_waiting_forward_floor"
    elif review_ready:
        decision = f"bitunix_wo105_{version}_forward_sample_ready_for_independent_review"
    else:
        decision = f"bitunix_wo105_{version}_collecting_causal_forward_sample"
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "tool": TOOL_PATH,
        "decision": decision,
        "cohort_version": version,
        "cohort_id": lock.get("cohort_id"),
        "phase": phase,
        "forward_start_at": lock.get("forward_start_at"),
        "v1_tombstoned_pre_floor": tombstone is not None,
        "v1_events_admitted": 0,
        "predecessor_tombstoned_pre_floor": tombstone is not None,
        "predecessor_cohort_version": predecessor,
        "v2_tombstoned_pre_floor": tombstone is not None if version == "v3" else None,
        "v3r3_tombstoned_post_floor": tombstone is not None if version == "v3r4" else None,
        "evaluator": "READY" if lock_ready else "HOLD",
        "source_pipeline": {
            "packet_assembly": packet_decision,
            "ws_intake": ws_status.get("decision") if ws_status else "ws_intake_status_missing",
            "liquidation_context": liquidation_status.get("decision") if liquidation_status else "liquidation_status_missing",
            "crowd_quorum_required": 3,
            "sources": ["bitunix_funding", "bitunix_trade_cvd", "binance_force_order_liquidation_skew"],
        },
        "forward_events": forward_events,
        "minimum_forward_events": minimum,
        "forward_progress": f"{forward_events}/{minimum}",
        "terminal_forward_events": terminal_forward_events,
        "minimum_terminal_forward_events": minimum,
        "terminal_forward_progress": f"{terminal_forward_events}/{minimum}",
        "latest_state_counts": dict(sorted(Counter(row.get("state") for row in latest.values()).items())),
        "all_row_state_counts": dict(sorted(state_counts.items())),
        "independent_edge_review_ready": review_ready,
        "edge_evaluated": False,
        "promotion": "HOLD",
        "failures": sorted(set(failures)),
        "next_action": (
            "wait_for_forward_floor_then_run_post_floor_rest_and_accepted_ws_capture"
            if phase == "WAITING_FORWARD_FLOOR"
            else "collect_without_retuning_until_30_terminal_forward_events"
        ),
        "runtime_boundary": {
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "capital_permission": "DENY",
            "can_trade": False,
        },
        "can_trade": False,
    }


def build_blind_review_gate(status: dict[str, Any], *, ledger_path: Path) -> dict[str, Any]:
    version = str(status.get("cohort_version") or "v2")
    ready = status.get("independent_edge_review_ready") is True
    evaluator_ready = status.get("evaluator") == "READY"
    if not evaluator_ready:
        decision = f"bitunix_wo105_{version}_blind_review_gate_hold_integrity_invalid"
    elif status.get("phase") == "WAITING_FORWARD_FLOOR":
        decision = f"bitunix_wo105_{version}_blind_review_gate_waiting_forward_floor"
    elif ready:
        decision = f"bitunix_wo105_{version}_terminal_sample_committed_ready_for_independent_review"
    else:
        decision = f"bitunix_wo105_{version}_blind_review_gate_collecting_terminal_sample"
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "tool": TOOL_PATH,
        "decision": decision,
        "cohort_version": version,
        "cohort_id": status.get("cohort_id"),
        "forward_start_at": status.get("forward_start_at"),
        "terminal_forward_events": status.get("terminal_forward_events", 0),
        "minimum_terminal_forward_events": status.get("minimum_terminal_forward_events", 30),
        "terminal_forward_progress": status.get("terminal_forward_progress", "0/30"),
        "ledger_sha256_commitment": sha256_file(ledger_path),
        "integrity_failures": status.get("failures") or [],
        "interim_outcome_values_accessed": False,
        "interim_outcome_metrics_disclosed": False,
        "independent_review_package_allowed": ready,
        "edge_evaluated": False,
        "promotion": "HOLD",
        "runtime_boundary": {
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "capital_permission": "DENY",
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_blind_gate_markdown(report: dict[str, Any]) -> str:
    version = str(report.get("cohort_version") or "v2").upper()
    return "\n".join(
        [
            f"# Bitunix WO105 {version} Blind Review Gate",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            f"- Terminal progress: `{report['terminal_forward_progress']}`",
            f"- Ledger commitment: `{report['ledger_sha256_commitment']}`",
            f"- Independent review package allowed: `{str(report['independent_review_package_allowed']).lower()}`",
            "- Interim outcome metrics disclosed: `false`",
            "- Edge evaluated: `false`",
            "- Promotion: `HOLD`",
            "- Can trade: `false`",
            "",
            "This gate publishes sample maturity and a ledger commitment only. It never publishes interim outcome values.",
            "",
        ]
    )


def render_markdown(report: dict[str, Any]) -> str:
    version = str(report.get("cohort_version") or "v2").upper()
    return "\n".join(
        [
            f"# Bitunix WO105 {version} Status",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            f"- Phase: `{report['phase']}`",
            f"- Cohort: `{report['cohort_id']}`",
            f"- Forward floor: `{report['forward_start_at']}`",
            f"- Predecessor tombstoned pre-floor: `{str(report['predecessor_tombstoned_pre_floor']).lower()}`",
            f"- Evaluator: `{report['evaluator']}`",
            f"- Source pipeline: `{report['source_pipeline']}`",
            f"- Forward progress: `{report['forward_progress']}`",
            f"- Terminal forward progress: `{report['terminal_forward_progress']}`",
            f"- Independent edge review ready: `{str(report['independent_edge_review_ready']).lower()}`",
            "- Edge evaluated: `false`",
            "- Promotion: `HOLD`",
            "- Signals/orders/capital: `DENY`",
            "- Can trade: `false`",
            f"- Failures: `{report['failures']}`",
            "",
            f"{version} proves only source contracts, causal assembly and a forward shadow process. It does not prove positive expectancy.",
            f"Predecessor outcomes are not admitted, and {version} cannot be retuned before the independent terminal sample gate.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Status proof for Bitunix WO105 V2")
    parser.add_argument("--lock", default="configs/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V2_2026-07-14.json")
    parser.add_argument("--tombstone", default="docs/BITUNIX_WO105_V1_PRE_FLOOR_UNIT_TOMBSTONE_2026-07-14.json")
    parser.add_argument("--packet-status", default="_dl/bitunix_wo105_shadow_v2/PACKET_ASSEMBLY_STATUS.json")
    parser.add_argument("--ws-status", default="_dl/bitunix_wo105_ws_intake/WS_INTAKE_MANIFEST.json")
    parser.add_argument("--liquidation-status", default="_dl/bitunix_wo105_liquidation_context/LAST_CONTEXT.json")
    parser.add_argument("--ledger", default="_dl/bitunix_wo105_shadow_v2/EVENT_LEDGER.jsonl")
    parser.add_argument("--out-prefix", default="docs/BITUNIX_WO105_V2_STATUS_2026-07-14")
    parser.add_argument("--blind-gate-prefix", default="docs/BITUNIX_WO105_V2_BLIND_REVIEW_GATE_2026-07-14")
    args = parser.parse_args()
    lock = read_optional(resolve(args.lock))
    if lock is None:
        raise SystemExit("V2 lock missing")
    report = build_report(
        lock,
        tombstone=read_optional(resolve(args.tombstone)),
        packet_status=read_optional(resolve(args.packet_status)),
        ws_status=read_optional(resolve(args.ws_status)),
        liquidation_status=read_optional(resolve(args.liquidation_status)),
        ledger_path=resolve(args.ledger),
        current_ms=now_ms(),
    )
    out = resolve(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    blind_gate = build_blind_review_gate(report, ledger_path=resolve(args.ledger))
    blind_out = resolve(args.blind_gate_prefix)
    blind_out.parent.mkdir(parents=True, exist_ok=True)
    blind_out.with_suffix(".json").write_text(
        json.dumps(blind_gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    blind_out.with_suffix(".md").write_text(render_blind_gate_markdown(blind_gate), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "phase": report["phase"],
                "forward_progress": report["forward_progress"],
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["evaluator"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())

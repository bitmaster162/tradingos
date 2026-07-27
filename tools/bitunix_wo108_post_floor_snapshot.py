#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bitunix_wo105_causal_shadow_evaluator_v3 as evaluator  # noqa: E402
from tools import bitunix_wo108_evidence_delivery as evidence  # noqa: E402


DEFAULT_INBOUND = Path.home() / "Downloads" / "TRADINGOS_BITUNIX_108_POST_FLOOR_OBSERVATION_SNAPSHOT.md"
DEFAULT_LOCK = "configs/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3_2026-07-14.json"
DEFAULT_OUT = "docs/BITUNIX_WO108_POST_FLOOR_OBSERVATION_2026-07-14"


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def portable(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return evidence.read_json(path)


def safe_hash(path: Path) -> str | None:
    return evidence.sha256_file(path) if path.is_file() else None


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    if not path.is_file():
        return rows, failures
    for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            failures.append(f"invalid_json:{line_number}")
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            failures.append(f"not_object:{line_number}")
    return rows, failures


def latest_rest_manifest(root: Path) -> dict[str, Any]:
    candidates: list[tuple[int, Path, dict[str, Any]]] = []
    for path in (root / "data" / "forward" / "bitunix_wo105_v3_rest").glob(
        "run_*/PUBLIC_REST_SNAPSHOT_MANIFEST.json"
    ):
        payload = read_json(path)
        generated_ms = evaluator.parse_iso_ms(payload.get("generated_at"))
        if generated_ms is not None:
            candidates.append((generated_ms, path, payload))
    if not candidates:
        return {"present": False}
    generated_ms, path, payload = max(candidates, key=lambda item: (item[0], item[1].as_posix()))
    return {
        "present": True,
        "path": portable(root, path),
        "sha256": safe_hash(path),
        "generated_at": payload.get("generated_at"),
        "generated_at_ms": generated_ms,
        "decision": payload.get("decision"),
        "snapshot_phase": payload.get("snapshot_phase"),
        "failures": payload.get("failures") or [],
        "can_trade": payload.get("can_trade"),
    }


def first_packet(root: Path) -> dict[str, Any]:
    shadow = root / "_dl" / "bitunix_wo105_shadow_v3"
    candidates = []
    last_packet = shadow / "LAST_PACKET.json"
    if last_packet.is_file():
        candidates.append(last_packet)
    event_dir = shadow / "EVENT_PACKETS"
    if event_dir.is_dir():
        candidates.extend(sorted(event_dir.glob("*.json")))
    if not candidates:
        return {"present": False, "evaluation_run": False, "reason": "no_complete_packet"}
    path = sorted(candidates, key=lambda item: (item.stat().st_mtime_ns, item.as_posix()))[0]
    return {
        "present": True,
        "path": portable(root, path),
        "sha256": safe_hash(path),
        "evaluation_run": False,
        "reason": "packet_recorded_but_snapshot_tool_does_not_mutate_ledger",
    }


def inbound_contract(path: Path, current_lock: dict[str, Any]) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    requests_v2 = "frozen V2 loop" in text or "V2 loop" in text
    requested_floor_bangkok = "2026-07-14 19:00 Asia/Bangkok" if "2026-07-14 19:00 Asia/Bangkok" in text else None
    return {
        "path": str(path.resolve()),
        "present": path.is_file(),
        "size": path.stat().st_size if path.is_file() else None,
        "sha256": safe_hash(path),
        "requests_v2": requests_v2,
        "requested_floor_bangkok": requested_floor_bangkok,
        "current_schema": current_lock.get("schema"),
        "current_cohort_id": current_lock.get("cohort_id"),
        "current_forward_start_at": current_lock.get("forward_start_at"),
        "request_matches_current_runtime": not requests_v2 and requested_floor_bangkok is None,
    }


def build_snapshot(root: Path, *, inbound_path: Path, generated_at: str | None = None) -> dict[str, Any]:
    lock_path = root / DEFAULT_LOCK
    lock = read_json(lock_path)
    loop_lock_path = root / "logs" / "bitunix_wo105_v3" / "bitunix_wo105_v3_forward_loop.lock.json"
    loop_status_path = root / "logs" / "bitunix_wo105_v3" / "bitunix_wo105_v3_forward_loop_status.json"
    job_path = root / "logs" / "runtime_jobs" / "bitunix_wo105_v3_forward.json"
    status_path = root / "docs" / "BITUNIX_WO105_V3_STATUS_2026-07-14.json"
    blind_path = root / "docs" / "BITUNIX_WO105_V3_BLIND_REVIEW_GATE_2026-07-14.json"
    first_cycle_path = root / "docs" / "BITUNIX_WO105_V3_FIRST_CYCLE_GATE_2026-07-14.json"
    packet_status_path = root / "_dl" / "bitunix_wo105_shadow_v3" / "PACKET_ASSEMBLY_STATUS.json"
    ws_path = root / "_dl" / "bitunix_wo105_v3_ws_intake" / "WS_INTAKE_MANIFEST.json"
    milestone_path = root / "logs" / "bitunix_wo105_v3" / "bitunix_wo105_v3_first_cycle_milestones.jsonl"
    ledger_path = root / "_dl" / "bitunix_wo105_shadow_v3" / "EVENT_LEDGER.jsonl"

    loop_lock = read_json(loop_lock_path)
    loop_status = read_json(loop_status_path)
    job = read_json(job_path)
    status = read_json(status_path)
    blind = read_json(blind_path)
    first_cycle = read_json(first_cycle_path)
    packet_status = read_json(packet_status_path)
    ws = read_json(ws_path)
    milestones, milestone_failures = read_jsonl(milestone_path)
    ledger, ledger_failures = read_jsonl(ledger_path)
    pid = int(job.get("pid") or loop_lock.get("pid") or 0)
    process = evidence.query_process(pid) if pid > 0 else {}
    command_line = str(job.get("command_line") or process.get("CommandLine") or "")
    bindings = lock.get("bindings") if isinstance(lock.get("bindings"), dict) else {}
    runtime = evidence.runtime_inventory(root)
    first_cycle_decision = first_cycle.get("decision")
    forward_events = int(status.get("forward_events") or 0)
    terminal_events = int(status.get("terminal_forward_events") or 0)
    packet = first_packet(root)
    state_counts = Counter(str(row.get("state") or "UNKNOWN") for row in ledger)
    inbound = inbound_contract(inbound_path, lock)

    rollover_eligible = bool(
        first_cycle_decision == "bitunix_wo105_v3_first_cycle_operational_blocked"
        and forward_events == 0
        and terminal_events == 0
        and not ledger
        and not packet["present"]
        and blind.get("interim_outcome_values_accessed") is False
        and blind.get("interim_outcome_metrics_disclosed") is False
    )
    blockers: list[str] = []
    if inbound.get("requests_v2"):
        blockers.append("inbound_request_targets_tombstoned_v2")
    if inbound.get("requested_floor_bangkok"):
        blockers.append("inbound_floor_does_not_match_current_v3_floor")
    blockers.extend(str(item) for item in first_cycle.get("overdue") or [])
    if packet_status.get("setup_status") == "NO_SETUP":
        blockers.append("no_current_causal_setup")
    if forward_events < int(status.get("minimum_forward_events") or 30):
        blockers.append("forward_sample_incomplete")

    if rollover_eligible:
        decision = "bitunix_wo108_v3_zero_event_operational_rollover_required"
        next_action = "tombstone_v3_without_outcome_review_and_open_parameter_identical_future_floor_rollover"
    elif packet["present"]:
        decision = "bitunix_wo108_complete_packet_present_manual_frozen_evaluator_review_required"
        next_action = "run_the_frozen_evaluator_once_without_retune_or_ledger_mutation"
    else:
        decision = "WAITING_POST_FLOOR_DATA"
        next_action = "continue_observation_without_mutation"

    return {
        "schema_version": 1,
        "generated_at": generated_at or now_iso(),
        "decision": decision,
        "inbound": inbound,
        "current_clock": {
            "utc": generated_at or now_iso(),
            "bangkok_offset": "+07:00",
        },
        "cohort": {
            "lock_path": portable(root, lock_path),
            "lock_sha256": safe_hash(lock_path),
            "schema": lock.get("schema"),
            "cohort_id": lock.get("cohort_id"),
            "parameter_cohort_sha256": lock.get("parameter_cohort_sha256"),
            "forward_start_at": lock.get("forward_start_at"),
            "evaluator": bindings.get("evaluator"),
            "evaluator_sha256": bindings.get("evaluator_sha256"),
            "evaluator_hash_matches": bool(
                bindings.get("evaluator")
                and safe_hash(resolve(root, str(bindings.get("evaluator")))) == bindings.get("evaluator_sha256")
            ),
        },
        "process": {
            "component": "bitunix_wo105_v3_forward",
            "pid": pid,
            "parent_pid": process.get("ParentProcessId"),
            "process_start_utc": job.get("process_creation_utc"),
            "alive": evidence.process_alive(pid),
            "command_line": command_line,
            "command_sha256": evidence.sha256_bytes(command_line.encode("utf-8")),
            "loop_script": job.get("expected_script_path"),
            "loop_script_sha256": safe_hash(root / "ops" / "autostart" / "Run-BitunixWO105V3ForwardLoop.ps1"),
            "heartbeat": loop_status.get("ts"),
            "heartbeat_status": loop_status.get("status"),
            "orders_allowed": loop_status.get("orders_allowed"),
            "can_trade": loop_status.get("can_trade"),
        },
        "sources": {
            "latest_rest": latest_rest_manifest(root),
            "ws_intake": {
                "path": portable(root, ws_path),
                "sha256": safe_hash(ws_path),
                "decision": ws.get("decision"),
                "accepted_runs": int(ws.get("accepted_runs") or 0),
                "records": ws.get("records") or {},
                "missing_for_packet": ws.get("missing_for_packet") or [],
            },
            "milestones": {
                "path": portable(root, milestone_path),
                "sha256": safe_hash(milestone_path),
                "rows": milestones,
                "parse_failures": milestone_failures,
            },
        },
        "packet": packet,
        "ledger": {
            "path": portable(root, ledger_path),
            "present": ledger_path.is_file(),
            "sha256": safe_hash(ledger_path),
            "rows": len(ledger),
            "state_counts": dict(sorted(state_counts.items())),
            "parse_failures": ledger_failures,
        },
        "forward": {
            "status_decision": status.get("decision"),
            "forward_events": forward_events,
            "terminal_forward_events": terminal_events,
            "minimum_terminal_forward_events": int(status.get("minimum_terminal_forward_events") or 30),
            "progress": status.get("terminal_forward_progress") or f"{terminal_events}/30",
            "blind_gate_decision": blind.get("decision"),
            "interim_outcome_values_accessed": blind.get("interim_outcome_values_accessed"),
            "interim_outcome_metrics_disclosed": blind.get("interim_outcome_metrics_disclosed"),
            "first_cycle_decision": first_cycle_decision,
            "first_cycle_checks": first_cycle.get("checks") or {},
            "first_cycle_overdue": first_cycle.get("overdue") or [],
            "packet_decision": packet_status.get("decision"),
            "packet_written": bool(packet_status.get("packet_written")),
            "evaluation_run": bool(packet_status.get("evaluation_run")),
            "setup_status": packet_status.get("setup_status"),
            "edge_evaluated": False,
        },
        "runtime_components": runtime,
        "blockers": sorted(set(blockers)),
        "rollover_eligible": rollover_eligible,
        "next_action": next_action,
        "mutation_audit": {
            "restart": False,
            "retune": False,
            "backfill": False,
            "provider_change": False,
            "evaluator_run": False,
            "order_or_signal_effect": False,
        },
        "runtime_boundary": {
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "capital_permission": "DENY",
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    process = report["process"]
    forward = report["forward"]
    latest_rest = report["sources"]["latest_rest"]
    ws = report["sources"]["ws_intake"]
    return "\n".join(
        [
            "# Bitunix WO108 Post-Floor Observation",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            f"- Current cohort: `{report['cohort']['cohort_id']}`",
            f"- Forward floor: `{report['cohort']['forward_start_at']}`",
            f"- PID / parent / alive: `{process['pid']}` / `{process['parent_pid']}` / `{process['alive']}`",
            f"- Heartbeat: `{process['heartbeat']}` (`{process['heartbeat_status']}`)",
            f"- REST: `{latest_rest.get('generated_at')}` / `{latest_rest.get('decision')}`",
            f"- WS accepted runs: `{ws.get('accepted_runs')}`; records: `{ws.get('records')}`",
            f"- Packet present: `{report['packet']['present']}`",
            f"- Forward progress: `{forward['progress']}`",
            f"- First-cycle gate: `{forward['first_cycle_decision']}`",
            f"- Overdue: `{forward['first_cycle_overdue']}`",
            f"- Runtime components: `{report['runtime_components']['verified']}/{report['runtime_components']['expected']}`",
            f"- Rollover eligible: `{report['rollover_eligible']}`",
            f"- Blockers: `{report['blockers']}`",
            f"- Next action: `{report['next_action']}`",
            "- No restart, retune, backfill, provider change, signal, order or capital action was performed by this snapshot.",
            "- `can_trade=false`.",
            "",
            "The inbound note targets the already tombstoned V2 runtime and a different floor. It is retained as a request artifact, not treated as current-state evidence.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Bitunix WO108 current post-floor observation")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--inbound", default=str(DEFAULT_INBOUND))
    parser.add_argument("--out-prefix", default=DEFAULT_OUT)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    inbound = Path(args.inbound).resolve()
    report = build_snapshot(root, inbound_path=inbound)
    out = resolve(root, args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_suffix(f".json.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, out.with_suffix(".json"))
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "progress": report["forward"]["progress"],
                "rollover_eligible": report["rollover_eligible"],
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

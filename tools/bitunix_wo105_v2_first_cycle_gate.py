#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bitunix_wo105_causal_shadow_evaluator_v2 as evaluator_v2  # noqa: E402
from tools import bitunix_wo105_causal_shadow_evaluator_v3 as evaluator_v3  # noqa: E402
from tools import bitunix_wo105_causal_shadow_evaluator_v4 as evaluator_v4  # noqa: E402


TOOL_PATH = "tools/bitunix_wo105_v2_first_cycle_gate.py"
evaluator = evaluator_v2
TRANSITION_GRACE_MS = 3 * 60 * 1000
REST_GRACE_MS = 7 * 60 * 1000
WS_GRACE_MS = 40 * 60 * 1000
PACKET_GRACE_MS = 45 * 60 * 1000
MILESTONE_NAMES = (
    "loop_transitioned_after_floor",
    "post_floor_rest_snapshot",
    "post_floor_ws_independently_accepted",
    "post_floor_packet_assembler_ran",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def generated_ms(payload: dict[str, Any] | None) -> int | None:
    return evaluator.parse_iso_ms((payload or {}).get("generated_at"))


def load_milestones(path: Path, *, cohort_id: str) -> tuple[dict[str, int], list[str]]:
    milestones: dict[str, int] = {}
    failures: list[str] = []
    if not path.is_file():
        return milestones, failures
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        return milestones, [f"milestone_journal_read_failed:{type(exc).__name__}"]
    for line_number, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            failures.append(f"milestone_journal_invalid_json:{line_number}")
            continue
        if not isinstance(row, dict):
            failures.append(f"milestone_journal_not_object:{line_number}")
            continue
        name = row.get("milestone")
        observed_ms = evaluator.parse_iso_ms(row.get("observed_at"))
        if name not in MILESTONE_NAMES:
            failures.append(f"milestone_journal_unknown_name:{line_number}")
            continue
        if row.get("cohort_id") != cohort_id:
            failures.append(f"milestone_journal_cohort_mismatch:{line_number}")
            continue
        if observed_ms is None:
            failures.append(f"milestone_journal_invalid_timestamp:{line_number}")
            continue
        row_boundary_failures = boundary_failures(row, prefix=f"milestone_{line_number}")
        if row_boundary_failures:
            failures.extend(row_boundary_failures)
            continue
        milestones[name] = min(observed_ms, milestones.get(name, observed_ms))
    return milestones, failures


def latest_forward_rest(rest_root: Path, floor_ms: int) -> tuple[dict[str, Any] | None, list[str]]:
    accepted: list[dict[str, Any]] = []
    malformed: list[str] = []
    for run_dir in sorted(rest_root.glob("run_*"), key=lambda item: item.name):
        if not run_dir.is_dir():
            continue
        manifest_path = run_dir / "PUBLIC_REST_SNAPSHOT_MANIFEST.json"
        manifest = read_object(manifest_path)
        if manifest is None:
            malformed.append(str(manifest_path))
            continue
        created = generated_ms(manifest)
        if (
            manifest.get("snapshot_phase") == "FORWARD"
            and created is not None
            and created >= floor_ms
            and manifest.get("failures") in ([], None)
            and manifest.get("can_trade") is False
        ):
            accepted.append({"path": str(manifest_path), "generated_at_ms": created, "decision": manifest.get("decision")})
    return (accepted[-1] if accepted else None), malformed


def boundary_failures(payload: dict[str, Any] | None, *, prefix: str) -> list[str]:
    if payload is None:
        return []
    failures: list[str] = []
    for field in ("signals_allowed", "paper_entries_allowed", "orders_allowed", "can_trade"):
        value = payload.get(field)
        if value is None and isinstance(payload.get("runtime_boundary"), dict):
            value = payload["runtime_boundary"].get(field)
        if value not in (False, None):
            failures.append(f"{prefix}_{field}_not_false")
    credentials = payload.get("credentials_allowed")
    if credentials is not None and credentials is not False:
        failures.append(f"{prefix}_credentials_allowed_not_false")
    return failures


def build_report(
    lock: dict[str, Any],
    *,
    loop_status: dict[str, Any] | None,
    rest_root: Path,
    ws_intake: dict[str, Any] | None,
    packet_status: dict[str, Any] | None,
    milestones: dict[str, int] | None = None,
    milestone_failures: list[str] | None = None,
    current_ms: int,
) -> dict[str, Any]:
    if lock.get("schema") == evaluator_v4.SCHEMA:
        runtime_evaluator = evaluator_v4
        version = "v3r4"
    elif lock.get("schema") == evaluator_v3.SCHEMA:
        runtime_evaluator = evaluator_v3
        version = "v3"
    else:
        runtime_evaluator = evaluator_v2
        version = "v2"
    failures = runtime_evaluator.validate_lock(lock)
    failures.extend(milestone_failures or [])
    floor_ms = runtime_evaluator.parse_iso_ms(lock.get("forward_start_at"))
    if floor_ms is None:
        failures.append("forward_floor_invalid")
        floor_ms = current_ms + 1
    failures.extend(boundary_failures(loop_status, prefix="loop"))
    failures.extend(boundary_failures(ws_intake, prefix="ws_intake"))
    failures.extend(boundary_failures(packet_status, prefix="packet"))

    rest, malformed_rest = latest_forward_rest(rest_root, floor_ms)
    elapsed_ms = current_ms - floor_ms
    milestone_times = milestones or {}
    deadlines = {
        "loop_transitioned_after_floor": TRANSITION_GRACE_MS,
        "post_floor_rest_snapshot": REST_GRACE_MS,
        "post_floor_ws_independently_accepted": WS_GRACE_MS,
        "post_floor_packet_assembler_ran": PACKET_GRACE_MS,
    }

    def milestone_on_time(name: str) -> bool:
        observed_ms = milestone_times.get(name)
        return observed_ms is not None and floor_ms <= observed_ms <= floor_ms + deadlines[name]

    loop_source_ready = bool(
        loop_status
        and loop_status.get("status")
        not in (None, "waiting_forward_floor", "stopped", "blocked_google_drive_runtime")
    )
    ws_source_ready = bool(
        ws_intake
        and ws_intake.get("decision") == "bitunix_wo105_ws_intake_ready"
        and int(ws_intake.get("accepted_runs") or 0) >= 1
    )
    packet_source_ready = bool(
        packet_status
        and int(packet_status.get("rest_eligible_runs") or 0) >= 1
        and int(packet_status.get("ws_accepted_runs") or 0) >= 1
        and packet_status.get("can_trade") is False
    )
    checks = {
        "loop_transitioned_after_floor": loop_source_ready and milestone_on_time("loop_transitioned_after_floor"),
        "post_floor_rest_snapshot": rest is not None and milestone_on_time("post_floor_rest_snapshot"),
        "post_floor_ws_independently_accepted": ws_source_ready
        and milestone_on_time("post_floor_ws_independently_accepted"),
        "post_floor_packet_assembler_ran": packet_source_ready
        and milestone_on_time("post_floor_packet_assembler_ran"),
    }
    overdue: list[str] = []
    pending: list[str] = []
    if current_ms >= floor_ms:
        for name, passed in checks.items():
            if passed:
                continue
            if elapsed_ms > deadlines[name]:
                overdue.append(name)
            else:
                pending.append(name)

    if failures:
        decision = f"bitunix_wo105_{version}_first_cycle_hold_integrity_or_boundary_invalid"
    elif current_ms < floor_ms:
        decision = f"bitunix_wo105_{version}_first_cycle_waiting_forward_floor"
    elif overdue:
        decision = f"bitunix_wo105_{version}_first_cycle_operational_blocked"
    elif all(checks.values()):
        decision = f"bitunix_wo105_{version}_first_cycle_accepted_shadow_only"
    else:
        decision = f"bitunix_wo105_{version}_first_cycle_within_grace_waiting_sources"

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "tool": TOOL_PATH,
        "decision": decision,
        "cohort_version": version,
        "cohort_id": lock.get("cohort_id"),
        "forward_start_at": lock.get("forward_start_at"),
        "elapsed_since_floor_ms": max(0, elapsed_ms),
        "grace_ms": {
            "loop_transition": TRANSITION_GRACE_MS,
            "rest_snapshot": REST_GRACE_MS,
            "accepted_ws": WS_GRACE_MS,
            "packet_assembly": PACKET_GRACE_MS,
        },
        "checks": checks,
        "pending_within_grace": pending,
        "overdue": overdue,
        "failures": sorted(set(failures)),
        "diagnostics": {
            "loop_status": (loop_status or {}).get("status"),
            "latest_forward_rest": rest,
            "malformed_rest_manifests": malformed_rest,
            "first_milestone_ms": {name: milestone_times.get(name) for name in MILESTONE_NAMES},
            "ws_intake_decision": (ws_intake or {}).get("decision"),
            "ws_accepted_runs": int((ws_intake or {}).get("accepted_runs") or 0),
            "packet_decision": (packet_status or {}).get("decision"),
            "packet_written": bool((packet_status or {}).get("packet_written")),
            "evaluation_run": bool((packet_status or {}).get("evaluation_run")),
        },
        "automatic_restart_attempted": False,
        "edge_evaluated": False,
        "promotion": "HOLD",
        "runtime_boundary": {
            "operational_gate_only": True,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "capital_permission": "DENY",
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    version = str(report.get("cohort_version") or "v2").upper()
    return "\n".join(
        [
            f"# Bitunix WO105 {version} First-Cycle Acceptance Gate",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            f"- Forward floor: `{report['forward_start_at']}`",
            f"- Checks: `{report['checks']}`",
            f"- Pending within grace: `{report['pending_within_grace']}`",
            f"- Overdue: `{report['overdue']}`",
            f"- Failures: `{report['failures']}`",
            "- Automatic restart attempted: `false`",
            "- Edge evaluated: `false`",
            "- Can trade: `false`",
            "",
            "This is an operational source-pipeline gate. Passing it does not establish a trading edge.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed first post-floor source-cycle gate for Bitunix WO105 V2")
    parser.add_argument("--lock", default="configs/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V2_2026-07-14.json")
    parser.add_argument("--loop-status", default="logs/bitunix_wo105_v2/bitunix_wo105_v2_forward_loop_status.json")
    parser.add_argument("--rest-root", default="data/forward/bitunix_wo105_rest")
    parser.add_argument("--ws-intake", default="_dl/bitunix_wo105_ws_intake/WS_INTAKE_MANIFEST.json")
    parser.add_argument("--packet-status", default="_dl/bitunix_wo105_shadow_v2/PACKET_ASSEMBLY_STATUS.json")
    parser.add_argument(
        "--milestone-journal",
        default="logs/bitunix_wo105_v2/bitunix_wo105_v2_first_cycle_milestones.jsonl",
    )
    parser.add_argument("--out-prefix", default="docs/BITUNIX_WO105_V2_FIRST_CYCLE_GATE_2026-07-14")
    args = parser.parse_args()
    lock = read_object(resolve(args.lock))
    if lock is None:
        raise SystemExit("V2 lock missing")
    milestones, milestone_failures = load_milestones(
        resolve(args.milestone_journal), cohort_id=str(lock.get("cohort_id") or "")
    )
    report = build_report(
        lock,
        loop_status=read_object(resolve(args.loop_status)),
        rest_root=resolve(args.rest_root),
        ws_intake=read_object(resolve(args.ws_intake)),
        packet_status=read_object(resolve(args.packet_status)),
        milestones=milestones,
        milestone_failures=milestone_failures,
        current_ms=now_ms(),
    )
    out = resolve(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "overdue": report["overdue"], "can_trade": False}, ensure_ascii=False))
    return 1 if report["failures"] or report["overdue"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else ROOT / value


def portable(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def latest(directory: Path, pattern: str, *, directories: bool = False) -> Path | None:
    if not directory.exists():
        return None
    values = [item for item in directory.glob(pattern) if item.is_dir() == directories]
    return max(values, key=lambda item: item.stat().st_mtime) if values else None


def windows_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    process_query_limited_information = 0x1000
    still_active = 259
    handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def build_report(
    *,
    logs_dir: Path,
    captures_dir: Path,
    intake_report_path: Path,
    cohort_report_path: Path,
    replay_report_path: Path,
    process_checker: Callable[[int], bool] = windows_pid_alive,
) -> dict[str, Any]:
    launch_path = latest(logs_dir, "capture_*_launch.json")
    source_receipt_path = latest(logs_dir, "capture_*_source_receipt*.json")
    attempt_receipts = [item for item in (launch_path, source_receipt_path) if item is not None]
    attempt_path = max(attempt_receipts, key=lambda item: item.stat().st_mtime) if attempt_receipts else None
    run_dir = latest(captures_dir, "run_*", directories=True)
    manifest_path = run_dir / "PUBLIC_CAPTURE_MANIFEST.json" if run_dir else None
    acceptance_path = run_dir / "TRADINGOS_INDEPENDENT_ACCEPTANCE.json" if run_dir else None
    close_path = run_dir / "TRADINGOS_CLOSE_RECEIPTS.json" if run_dir else None

    attempt = read_json(attempt_path)
    intake = read_json(intake_report_path)
    cohort = read_json(cohort_report_path)
    replay = read_json(replay_report_path)
    manifest = read_json(manifest_path)
    acceptance = read_json(acceptance_path)
    pid = int(attempt.get("pid") or 0)
    alive = process_checker(pid)

    if acceptance:
        decision = str(acceptance.get("decision") or "bitunix_wo104_acceptance_report_invalid")
        phase = "completed"
    elif alive:
        decision = "bitunix_wo104_bounded_public_capture_collecting"
        phase = "collecting"
    elif attempt:
        decision = "bitunix_wo104_capture_stopped_without_independent_acceptance"
        phase = "blocked"
    else:
        decision = "bitunix_wo104_bounded_public_capture_not_started"
        phase = "waiting"

    blockers: list[str] = []
    if intake.get("decision") != "external_proposal_ready_for_semantic_review":
        blockers.append("external_intake_not_ready")
    if cohort.get("decision") != "bitunix_wo104_cohort_scope_bound":
        blockers.append("cohort_scope_not_bound")
    if replay.get("canonical_replay_status") != "REPLAY_PENDING":
        blockers.append("canonical_replay_status_contract_invalid")
    if phase == "blocked":
        blockers.append("capture_stopped_without_acceptance")
    if acceptance and acceptance.get("decision") != "bitunix_wo104_public_contract_confirmed_shadow_hold":
        blockers.append("capture_independent_acceptance_failed")

    contract_confirmed = acceptance.get("decision") == "bitunix_wo104_public_contract_confirmed_shadow_hold"
    proposal_status = "PUBLIC_CONTRACT_CONFIRMED" if contract_confirmed else "PUBLIC_CONTRACT_NOT_CONFIRMED"
    setup_status = "FROZEN_SHADOW_POLICY_ORACLE" if contract_confirmed else "FROZEN_PENDING_PUBLIC_CONTRACT"

    return {
        "generated_at": now_iso(),
        "decision": decision,
        "phase": phase,
        "proposal_lane": "isolated_parallel_public_only",
        "intake": {
            "decision": intake.get("decision"),
            "path": portable(intake_report_path),
        },
        "cohort": {
            "decision": cohort.get("decision"),
            "binding_sha256": cohort.get("cohort_binding_sha256"),
            "path": portable(cohort_report_path),
        },
        "replay": {
            "decision": replay.get("decision"),
            "frames_total": replay.get("frames_total"),
            "unknown_schema": replay.get("unknown_schema"),
            "canonical_replay_status": replay.get("canonical_replay_status"),
            "path": portable(replay_report_path),
        },
        "capture": {
            "attempt_receipt": portable(attempt_path),
            "launch_receipt": portable(launch_path) if attempt_path == launch_path else None,
            "source_receipt": portable(source_receipt_path) if attempt_path == source_receipt_path else None,
            "pid": pid or None,
            "pid_alive": alive,
            "run_dir": portable(run_dir),
            "manifest": portable(manifest_path) if manifest_path and manifest_path.exists() else None,
            "close_receipts": portable(close_path) if close_path and close_path.exists() else None,
            "independent_acceptance": portable(acceptance_path) if acceptance_path and acceptance_path.exists() else None,
            "manifest_hold": manifest.get("hold") if manifest else None,
            "manifest_hold_reasons": manifest.get("hold_reasons") if manifest else None,
            "acceptance_decision": acceptance.get("decision") if acceptance else None,
        },
        "blockers": blockers,
        "proposal_status": proposal_status,
        "setup_status": setup_status,
        "edge_evaluated": False,
        "promotion": "HOLD",
        "signals_allowed": False,
        "paper_entries_allowed": False,
        "orders_allowed": False,
        "can_trade": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Status audit for the isolated Bitunix WO-104 lane")
    parser.add_argument("--logs-dir", default="logs/bitunix_wo104")
    parser.add_argument("--captures-dir", default="data/forward/bitunix_wo104")
    parser.add_argument("--intake", default="docs/BITUNIX_WO104_EXTERNAL_PROPOSAL_INTAKE_2026-07-13.json")
    parser.add_argument("--cohort", default="docs/BITUNIX_WO104_COHORT_BINDING_2026-07-13.json")
    parser.add_argument("--replay", default="docs/BITUNIX_WO104_LEGACY_CAPTURE_SCHEMA_REPLAY_2026-07-13.json")
    parser.add_argument("--out", default="docs/BITUNIX_WO104_STATUS_2026-07-13.json")
    args = parser.parse_args()

    report = build_report(
        logs_dir=resolve(args.logs_dir),
        captures_dir=resolve(args.captures_dir),
        intake_report_path=resolve(args.intake),
        cohort_report_path=resolve(args.cohort),
        replay_report_path=resolve(args.replay),
    )
    out = resolve(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "phase": report["phase"], "blockers": report["blockers"], "can_trade": False}))
    return 0 if report["phase"] != "blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())

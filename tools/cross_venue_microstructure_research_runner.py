#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "configs" / "CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT.json"
DEFAULT_GATE = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compact_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_under(root: Path, value: str) -> Path:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError(f"path_outside_root: {value}")
    return resolved


def load_contract(path: Path) -> dict[str, Any]:
    contract = read_json(path)
    if contract.get("status") != "locked_skeleton":
        raise ValueError("microstructure_runner_contract_not_locked")
    execution = contract.get("execution_contract")
    if not isinstance(execution, dict):
        raise ValueError("microstructure_runner_execution_contract_missing")
    required_false = (
        "credentials_allowed",
        "network_required",
        "orders_allowed",
        "signals_allowed",
        "observer_registration_allowed",
        "paper_or_live_promotion_allowed",
    )
    if any(execution.get(name) is not False for name in required_false):
        raise ValueError("microstructure_runner_unsafe_contract")
    if execution.get("exact_snapshot_id_required") is not True:
        raise ValueError("exact_snapshot_id_not_required")
    if execution.get("sealed_snapshot_required") is not True:
        raise ValueError("sealed_snapshot_not_required")
    if execution.get("snapshot_verification_required") is not True:
        raise ValueError("snapshot_verification_not_required")
    return contract


def snapshot_from_gate(gate: dict[str, Any]) -> tuple[str | None, str]:
    decision = str(gate.get("decision") or "")
    snapshot_id = gate.get("snapshot_id")
    if decision not in {"microstructure_snapshot_sealed", "snapshot_already_sealed_for_readiness_epoch"}:
        return None, "blocked_waiting_for_sealed_snapshot"
    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        return None, "blocked_missing_exact_snapshot_id"
    return snapshot_id, "sealed_snapshot_available"


def verify_microstructure_snapshot(snapshot_dir: Path, snapshot_id: str) -> dict[str, Any]:
    manifest_path = snapshot_dir / "SNAPSHOT_MANIFEST.json"
    manifest = read_json(manifest_path)
    checks: list[dict[str, Any]] = []
    for expected in manifest.get("files", []):
        if not isinstance(expected, dict):
            continue
        rel = str(expected.get("path") or "")
        path = snapshot_dir / rel
        exists = path.is_file()
        size = path.stat().st_size if exists else None
        digest = sha256_file(path) if exists else None
        expected_size = expected.get("bytes", expected.get("size"))
        checks.append(
            {
                "path": rel,
                "exists": exists,
                "size_match": exists and size == expected_size,
                "sha256_match": exists and digest == expected.get("sha256"),
            }
        )
    db_path = snapshot_dir / "microstructure.sqlite3"
    sqlite_ok = False
    if db_path.is_file():
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            sqlite_ok = bool(row and row[0] == "ok")
        finally:
            conn.close()
    passed = (
        manifest.get("snapshot_id") == snapshot_id
        and manifest.get("can_trade") is False
        and bool(checks)
        and all(item["exists"] and item["size_match"] and item["sha256_match"] for item in checks)
        and sqlite_ok
    )
    return {
        "verified_at": now_iso(),
        "snapshot_id": snapshot_id,
        "snapshot_dir": str(snapshot_dir),
        "manifest_sha256": sha256_file(manifest_path) if manifest_path.is_file() else None,
        "dataset_sha256": manifest.get("dataset_sha256"),
        "files_checked": len(checks),
        "failed_files": [item for item in checks if not (item["exists"] and item["size_match"] and item["sha256_match"])],
        "sqlite_integrity": "ok" if sqlite_ok else "failed",
        "passed": passed,
        "can_trade": False,
    }


def experiment_specs(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    experiments = contract.get("experiments")
    if not isinstance(experiments, dict) or not experiments:
        raise ValueError("microstructure_runner_experiments_missing")
    output: dict[str, dict[str, Any]] = {}
    for name, spec in sorted(experiments.items()):
        if not isinstance(spec, dict):
            raise ValueError(f"invalid_experiment_spec: {name}")
        if spec.get("implementation_status") != "implemented_locked":
            raise ValueError(f"experiment_not_implemented_locked: {name}")
        script = resolve_under(ROOT, str(spec.get("script") or ""))
        if not script.is_file() or script.suffix != ".py":
            raise ValueError(f"implemented_script_missing: {name}")
        output[name] = {**spec, "script_path": script}
    return output


def report_contract(report: dict[str, Any], spec: dict[str, Any], experiment: str) -> dict[str, Any]:
    runtime = report.get("runtime_boundary") if isinstance(report.get("runtime_boundary"), dict) else {}
    search = report.get("search") if isinstance(report.get("search"), dict) else {}
    splits = report.get("splits") if isinstance(report.get("splits"), dict) else {}
    checks = {
        "report_is_object": isinstance(report, dict),
        "hypothesis_matches": report.get("hypothesis_id") == spec.get("hypothesis_id"),
        "experiment_matches": report.get("experiment") == experiment,
        "family_matches": report.get("family") == spec.get("family"),
        "decision_present": isinstance(report.get("decision"), str) and bool(report.get("decision")),
        "search_tested_present": isinstance(search.get("tested"), int) and search.get("tested", 0) > 0,
        "train_qualified_present": isinstance(search.get("train_qualified"), int),
        "can_trade_false": report.get("can_trade") is False,
        "runtime_can_trade_false": runtime.get("can_trade") is False,
        "orders_forbidden": runtime.get("orders_allowed") is False,
        "signals_forbidden": runtime.get("signals_allowed") is False,
        "validation_closed": splits.get("validation_opened") is False,
        "oos_closed": splits.get("oos_opened") is False,
    }
    return {"pass": all(checks.values()), "checks": checks}


def run_id(snapshot_id: str) -> str:
    return f"{compact_ts()}-microstructure-{snapshot_id[-12:]}-{uuid.uuid4().hex[:6]}"


def build_command(spec: dict[str, Any], snapshot_dir: Path, report_prefix: Path, lock_path: Path) -> list[str]:
    return [
        sys.executable,
        str(spec["script_path"]),
        "--cache-dir",
        str(snapshot_dir),
        "--out-prefix",
        str(report_prefix),
        "--lock-path",
        str(lock_path),
    ]


def summarize_batch(run_id_value: str, snapshot_id: str, experiments: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [row for row in experiments if row.get("status") != "completed"]
    candidates = [
        row
        for row in experiments
        if isinstance(row.get("decision"), str) and row["decision"].startswith("candidate_requires_validation_review")
    ]
    if failed:
        decision = "microstructure_research_batch_failed"
    elif candidates:
        decision = "microstructure_candidates_require_validation_review"
    else:
        decision = "microstructure_research_batch_completed_no_candidate"
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "run_id": run_id_value,
        "snapshot_id": snapshot_id,
        "decision": decision,
        "experiments": len(experiments),
        "completed": sum(row.get("status") == "completed" for row in experiments),
        "failed": len(failed),
        "candidate_count": len(candidates),
        "train_qualified_total": sum(int(row.get("train_qualified") or 0) for row in experiments),
        "tested_total": sum(int(row.get("tested") or 0) for row in experiments),
        "experiment_results": experiments,
        "runtime_boundary": {
            "research_only": True,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cross-Venue Microstructure Research Runner",
        "",
        f"- Generated: `{report['generated_at']}`.",
        f"- Decision: `{report['decision']}`.",
        f"- Run ID: `{report.get('run_id')}`.",
        f"- Snapshot: `{report.get('snapshot_id')}`.",
        f"- Completed / failed: `{report.get('completed')}` / `{report.get('failed')}`.",
        f"- Candidate count: `{report.get('candidate_count')}`.",
        f"- Tested configs total: `{report.get('tested_total')}`.",
        "- Research-only verdict. No observer registration, no paper/live execution, no orders.",
        "- `can_trade=false`.",
        "",
    ]
    for row in report.get("experiment_results", []):
        lines.append(f"- `{row.get('experiment')}`: `{row.get('status')}` / `{row.get('decision')}`.")
    lines.append("")
    return "\n".join(lines)


def latest_status_path(active_root: Path, contract: dict[str, Any]) -> Path:
    return resolve_under(active_root, str(contract.get("run_root_relative_to_active") or "_dl/research_runs_cross_venue_microstructure")) / "LATEST.json"


def blocked_report(reason: str, gate: dict[str, Any], contract: dict[str, Any], out_prefix: Path) -> dict[str, Any]:
    report = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": reason,
        "gate_decision": gate.get("decision"),
        "snapshot_id": gate.get("snapshot_id"),
        "experiments": len(contract.get("experiments", {}) if isinstance(contract.get("experiments"), dict) else {}),
        "runtime_boundary": {
            "research_only": True,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown({**report, "run_id": None, "completed": 0, "failed": 0, "candidate_count": 0, "tested_total": 0, "experiment_results": []}), encoding="utf-8")
    return report


def run_if_ready(
    *,
    active_root: Path,
    contract_path: Path,
    gate_path: Path,
    out_prefix: Path,
    timeout_seconds: int,
    force: bool = False,
) -> tuple[int, dict[str, Any]]:
    contract = load_contract(contract_path)
    gate = read_json(gate_path)
    snapshot_id, state = snapshot_from_gate(gate)
    if not snapshot_id:
        return 0, blocked_report(state, gate, contract, out_prefix)
    latest_path = latest_status_path(active_root, contract)
    latest = read_json(latest_path)
    if not force and latest.get("snapshot_id") == snapshot_id and latest.get("status") == "completed":
        previous_batch = read_json(Path(str(latest.get("run_dir") or "")) / "RUN_BATCH.json")
        report = previous_batch if previous_batch else {
            "schema_version": 1,
            "generated_at": now_iso(),
            "decision": "microstructure_research_batch_already_completed_for_snapshot",
            "snapshot_id": snapshot_id,
            "latest_run": latest,
            "runtime_boundary": {"research_only": True, "signals_allowed": False, "orders_allowed": False, "can_trade": False},
            "can_trade": False,
        }
        report["idempotent_replay_skipped"] = True
        report["latest_run"] = latest
        write_json(out_prefix.with_suffix(".json"), report)
        out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
        return 0, report

    policy = read_json(resolve_under(ROOT, str(contract.get("snapshot_policy") or "")))
    snapshot_root = resolve_under(active_root, str(policy.get("snapshot_root_relative") or "data/research_snapshots_cross_venue_microstructure"))
    snapshot_dir = resolve_under(snapshot_root, snapshot_id)
    verification = verify_microstructure_snapshot(snapshot_dir, snapshot_id)
    if verification.get("passed") is not True:
        report = {
            "schema_version": 1,
            "generated_at": now_iso(),
            "decision": "microstructure_snapshot_verification_failed",
            "snapshot_id": snapshot_id,
            "verification": verification,
            "runtime_boundary": {"research_only": True, "signals_allowed": False, "orders_allowed": False, "can_trade": False},
            "can_trade": False,
        }
        write_json(out_prefix.with_suffix(".json"), report)
        out_prefix.with_suffix(".md").write_text(render_markdown({**report, "run_id": None, "completed": 0, "failed": 1, "candidate_count": 0, "tested_total": 0, "experiment_results": []}), encoding="utf-8")
        return 1, report

    specs = experiment_specs(contract)
    run_root = latest_path.parent
    current_run_id = run_id(snapshot_id)
    current_dir = resolve_under(run_root, current_run_id)
    current_dir.mkdir(parents=True, exist_ok=False)
    write_json(
        current_dir / "RUN_REQUEST.json",
        {
            "schema_version": 1,
            "requested_at": now_iso(),
            "run_id": current_run_id,
            "snapshot_id": snapshot_id,
            "snapshot_verification": verification,
            "contract_path": str(contract_path.resolve()),
            "contract_sha256": sha256_file(contract_path),
            "shell": False,
            "arbitrary_extra_args": False,
            "can_trade": False,
        },
    )
    experiment_results: list[dict[str, Any]] = []
    for experiment, spec in specs.items():
        experiment_dir = current_dir / experiment
        experiment_dir.mkdir(parents=True, exist_ok=True)
        report_prefix = experiment_dir / "REPORT"
        lock_path = experiment_dir / "RESEARCH_LOCK.json"
        command = build_command(spec, snapshot_dir, report_prefix, lock_path)
        started_at = now_iso()
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(1, timeout_seconds),
            )
            return_code = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            return_code = 124
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            timed_out = True
        (experiment_dir / "stdout.log").write_text(stdout, encoding="utf-8")
        (experiment_dir / "stderr.log").write_text(stderr, encoding="utf-8")
        report_path = report_prefix.with_suffix(".json")
        report = read_json(report_path)
        contract_check = report_contract(report, spec, experiment)
        status = "completed" if return_code == 0 and contract_check["pass"] else "failed"
        result = {
            "experiment": experiment,
            "hypothesis_id": spec.get("hypothesis_id"),
            "family": spec.get("family"),
            "status": status,
            "return_code": return_code,
            "timed_out": timed_out,
            "started_at": started_at,
            "finished_at": now_iso(),
            "script_sha256": sha256_file(spec["script_path"]),
            "report_path": str(report_path),
            "report_sha256": sha256_file(report_path) if report_path.is_file() else None,
            "report_contract": contract_check,
            "decision": report.get("decision"),
            "tested": report.get("search", {}).get("tested") if isinstance(report.get("search"), dict) else None,
            "train_qualified": report.get("search", {}).get("train_qualified") if isinstance(report.get("search"), dict) else None,
            "can_trade": False,
        }
        write_json(experiment_dir / "RUN_RESULT.json", result)
        experiment_results.append(result)

    batch = summarize_batch(current_run_id, snapshot_id, experiment_results)
    write_json(current_dir / "RUN_BATCH.json", batch)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    write_json(out_prefix.with_suffix(".json"), batch)
    out_prefix.with_suffix(".md").write_text(render_markdown(batch), encoding="utf-8")
    latest_payload = {
        "schema_version": 1,
        "updated_at": now_iso(),
        "run_id": current_run_id,
        "run_dir": str(current_dir),
        "snapshot_id": snapshot_id,
        "status": "completed" if batch["failed"] == 0 else "failed",
        "decision": batch["decision"],
        "completed": batch["completed"],
        "failed": batch["failed"],
        "candidate_count": batch["candidate_count"],
        "tested_total": batch["tested_total"],
        "train_qualified_total": batch["train_qualified_total"],
        "can_trade": False,
    }
    write_json(latest_path, latest_payload)
    append_jsonl(run_root / "RUN_INDEX.jsonl", latest_payload)
    return (0 if batch["failed"] == 0 else 1), batch


def main() -> int:
    parser = argparse.ArgumentParser(description="Run preregistered microstructure research scripts only after exact sealed snapshot")
    parser.add_argument("--active-root", default=str(ROOT))
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument("--snapshot-gate", default=str(DEFAULT_GATE))
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_RESEARCH_RUNNER_2026-06-25")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--force", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    subparsers.add_parser("run-if-ready")
    args = parser.parse_args()

    active_root = Path(args.active_root).resolve()
    contract_path = Path(args.contract).resolve()
    gate_path = Path(args.snapshot_gate)
    if not gate_path.is_absolute():
        gate_path = active_root / gate_path
    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = active_root / out_prefix
    if args.command == "status":
        contract = load_contract(contract_path)
        gate = read_json(gate_path)
        snapshot_id, state = snapshot_from_gate(gate)
        report = blocked_report(state if not snapshot_id else "sealed_snapshot_available_not_run_by_status_command", gate, contract, out_prefix)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    code, report = run_if_ready(
        active_root=active_root,
        contract_path=contract_path,
        gate_path=gate_path,
        out_prefix=out_prefix,
        timeout_seconds=args.timeout_seconds,
        force=args.force,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"decision": "microstructure_research_runner_rejected", "error": str(exc), "can_trade": False}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(2)

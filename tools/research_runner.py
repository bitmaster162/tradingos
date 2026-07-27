#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "configs" / "RESEARCH_RUNNER_CONTRACT.json"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research_data_snapshot import (  # noqa: E402
    read_json,
    resolve_active_root,
    sha256_file,
    verify_snapshot,
    write_json,
)
from tools.hypothesis_registry import authorize_run, assess_report  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_id(experiment: str, snapshot_id: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{experiment}-{snapshot_id[-12:]}-{uuid.uuid4().hex[:6]}"


def resolve_under_root(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if path != root.resolve() and root.resolve() not in path.parents:
        raise ValueError(f"path_outside_root: {relative}")
    return path


def load_contract(path: Path) -> dict[str, Any]:
    contract = read_json(path)
    if contract.get("status") != "locked_allowlist":
        raise ValueError("research_runner_contract_not_locked")
    execution = contract.get("execution_contract")
    if not isinstance(execution, dict):
        raise ValueError("research_runner_execution_contract_missing")
    required_false = (
        "shell",
        "arbitrary_extra_args",
        "credentials_allowed",
        "orders_allowed",
        "observer_registration_allowed",
        "paper_or_live_promotion_allowed",
    )
    if any(execution.get(name) is not False for name in required_false):
        raise ValueError("research_runner_unsafe_contract")
    if execution.get("exact_snapshot_id_required") is not True:
        raise ValueError("exact_snapshot_id_not_required")
    if execution.get("snapshot_verification_required") is not True:
        raise ValueError("snapshot_verification_not_required")
    if execution.get("hypothesis_authorization_required") is not True:
        raise ValueError("hypothesis_authorization_not_required")
    if execution.get("multiple_testing_assessment_required") is not True:
        raise ValueError("multiple_testing_assessment_not_required")
    return contract


def resolve_snapshot(
    active_root: Path,
    contract: dict[str, Any],
    snapshot_id: str,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    if not snapshot_id or snapshot_id.lower() == "latest":
        raise ValueError("exact_snapshot_id_required_not_latest")
    policy_path = resolve_under_root(ROOT, str(contract["snapshot_policy"]))
    policy = read_json(policy_path)
    snapshot_root = resolve_under_root(active_root, str(policy["snapshot_root_relative"]))
    snapshot_dir = resolve_under_root(snapshot_root, snapshot_id)
    manifest = read_json(snapshot_dir / "SNAPSHOT_MANIFEST.json")
    if manifest.get("snapshot_id") != snapshot_id:
        raise ValueError("snapshot_id_manifest_mismatch")
    verification = verify_snapshot(snapshot_dir, update_verification=False)
    if verification.get("passed") is not True:
        raise ValueError("snapshot_verification_failed")
    return snapshot_dir, manifest, verification


def experiment_spec(contract: dict[str, Any], experiment: str) -> dict[str, Any]:
    experiments = contract.get("experiments")
    if not isinstance(experiments, dict) or experiment not in experiments:
        raise ValueError(f"experiment_not_allowlisted: {experiment}")
    spec = experiments[experiment]
    if not isinstance(spec, dict):
        raise ValueError(f"invalid_experiment_spec: {experiment}")
    script = resolve_under_root(ROOT, str(spec["script"]))
    if script.suffix != ".py" or not script.is_file():
        raise ValueError(f"allowlisted_script_missing: {script}")
    return {**spec, "script_path": script}


def build_command(
    spec: dict[str, Any],
    snapshot_dir: Path,
    report_prefix: Path,
    lock_path: Path,
) -> list[str]:
    command = [
        sys.executable,
        str(spec["script_path"]),
        "--cache-dir",
        str(snapshot_dir),
        "--out-prefix",
        str(report_prefix),
    ]
    if spec.get("supports_lock_path") is True:
        command.extend(["--lock-path", str(lock_path)])
    return command


def report_contract(report: dict[str, Any], expected_family: str | None) -> dict[str, Any]:
    checks = {
        "report_is_object": isinstance(report, dict),
        "can_trade_false": report.get("can_trade") is False,
        "decision_present": isinstance(report.get("decision"), str) and bool(report.get("decision")),
        "runtime_boundary_safe": (
            not isinstance(report.get("runtime_boundary"), dict)
            or report.get("runtime_boundary", {}).get("can_trade") is False
        ),
        "family_matches": (
            expected_family is None
            or report.get("family") is None
            or report.get("family") == expected_family
        ),
    }
    return {"pass": all(checks.values()), "checks": checks}


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def run_experiment(
    *,
    active_root: Path,
    contract_path: Path,
    experiment: str,
    hypothesis_id: str,
    purpose: str,
    snapshot_id: str,
    timeout_seconds: int,
) -> tuple[int, dict[str, Any]]:
    contract = load_contract(contract_path)
    spec = experiment_spec(contract, experiment)
    if spec.get("hypothesis_id") != hypothesis_id:
        raise ValueError("runner_hypothesis_id_mismatch")
    registry_path = resolve_under_root(ROOT, str(contract["hypothesis_registry"]))
    registry = read_json(registry_path)
    authorization = authorize_run(
        registry,
        hypothesis_id=hypothesis_id,
        experiment=experiment,
        purpose=purpose,
        snapshot_id=snapshot_id,
    )
    if authorization.get("authorized") is not True:
        raise ValueError(f"hypothesis_run_not_authorized: {','.join(authorization.get('reasons', []))}")
    snapshot_dir, snapshot_manifest, snapshot_verification = resolve_snapshot(
        active_root, contract, snapshot_id
    )
    run_root = resolve_under_root(active_root, str(contract["run_root_relative_to_active"]))
    current_run_id = run_id(experiment, snapshot_id)
    current_dir = resolve_under_root(run_root, current_run_id)
    current_dir.mkdir(parents=True, exist_ok=False)
    report_prefix = current_dir / "REPORT"
    lock_path = current_dir / "RESEARCH_LOCK.json"
    command = build_command(spec, snapshot_dir, report_prefix, lock_path)
    request = {
        "schema_version": 1,
        "run_id": current_run_id,
        "requested_at": now_iso(),
        "experiment": experiment,
        "hypothesis_id": hypothesis_id,
        "purpose": purpose,
        "expected_family": spec.get("family"),
        "snapshot": {
            "snapshot_id": snapshot_id,
            "dataset_sha256": snapshot_manifest.get("dataset_sha256"),
            "manifest_sha256": snapshot_verification.get("manifest_sha256"),
            "verification_passed": snapshot_verification.get("passed"),
        },
        "provenance": {
            "contract_path": str(contract_path.resolve()),
            "contract_sha256": sha256_file(contract_path),
            "hypothesis_registry_path": str(registry_path),
            "hypothesis_registry_sha256": sha256_file(registry_path),
            "hypothesis_authorization": authorization,
            "script_path": str(spec["script_path"]),
            "script_sha256": sha256_file(spec["script_path"]),
            "python": sys.version.split()[0],
            "command": command,
            "shell": False,
            "arbitrary_extra_args": False,
        },
        "runtime_boundary": {
            "research_only": True,
            "credentials": False,
            "network": False,
            "orders": False,
            "observer_registration": False,
            "paper_or_live_promotion": False,
            "can_trade": False,
        },
    }
    write_json(current_dir / "RUN_REQUEST.json", request)
    started_at = now_iso()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            shell=False,
            check=False,
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
    (current_dir / "stdout.log").write_text(stdout, encoding="utf-8")
    (current_dir / "stderr.log").write_text(stderr, encoding="utf-8")
    report_path = report_prefix.with_suffix(".json")
    report = read_json(report_path) if report_path.is_file() else {}
    report_check = report_contract(report, spec.get("family"))
    multiplicity = assess_report(registry, hypothesis_id, report) if report else {
        "hypothesis_id": hypothesis_id,
        "multiplicity_status": "report_missing",
        "multiplicity_pass": False,
        "eligible_for_next_stage": False,
        "can_trade": False,
    }
    status = "completed" if return_code == 0 and report_check["pass"] else "failed"
    result = {
        "schema_version": 1,
        "run_id": current_run_id,
        "experiment": experiment,
        "hypothesis_id": hypothesis_id,
        "purpose": purpose,
        "started_at": started_at,
        "finished_at": now_iso(),
        "status": status,
        "return_code": return_code,
        "timed_out": timed_out,
        "snapshot_id": snapshot_id,
        "dataset_sha256": snapshot_manifest.get("dataset_sha256"),
        "report_path": str(report_path),
        "report_sha256": sha256_file(report_path) if report_path.is_file() else None,
        "decision": report.get("decision"),
        "report_contract": report_check,
        "multiplicity_assessment": multiplicity,
        "eligible_for_next_stage": multiplicity.get("eligible_for_next_stage") is True,
        "runtime_boundary": request["runtime_boundary"],
        "can_trade": False,
    }
    write_json(current_dir / "RUN_RESULT.json", result)
    latest = {
        "schema_version": 1,
        "updated_at": now_iso(),
        "run_id": current_run_id,
        "run_dir": str(current_dir),
        "experiment": experiment,
        "hypothesis_id": hypothesis_id,
        "purpose": purpose,
        "snapshot_id": snapshot_id,
        "status": status,
        "decision": result["decision"],
        "return_code": return_code,
        "report_contract_passed": report_check["pass"],
        "multiplicity_status": multiplicity.get("multiplicity_status"),
        "multiplicity_pass": multiplicity.get("multiplicity_pass") is True,
        "eligible_for_next_stage": multiplicity.get("eligible_for_next_stage") is True,
        "can_trade": False,
    }
    write_json(run_root / "LATEST.json", latest)
    append_jsonl(run_root / "RUN_INDEX.jsonl", latest)
    return (0 if status == "completed" else 1), result


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Allowlisted research runner requiring an exact verified snapshot")
    parser.add_argument("--active-root")
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--snapshot-id", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("experiment")
    run_parser.add_argument("--hypothesis-id", required=True)
    run_parser.add_argument("--purpose", choices=["proof", "discovery"], required=True)
    run_parser.add_argument("--snapshot-id", required=True)
    run_parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()

    active_root = resolve_active_root(args.active_root)
    contract_path = Path(args.contract).resolve()
    contract = load_contract(contract_path)
    if args.command == "list":
        print(json.dumps({
            "experiments": sorted(contract.get("experiments", {}).keys()),
            "exact_snapshot_id_required": True,
            "arbitrary_extra_args": False,
            "can_trade": False,
        }, ensure_ascii=False, indent=2))
        return 0
    if args.command == "verify":
        snapshot_dir, manifest, verification = resolve_snapshot(active_root, contract, args.snapshot_id)
        print(json.dumps({
            "snapshot_id": manifest.get("snapshot_id"),
            "snapshot_dir": str(snapshot_dir),
            "dataset_sha256": manifest.get("dataset_sha256"),
            "verification": verification,
            "can_trade": False,
        }, ensure_ascii=False, indent=2))
        return 0
    code, result = run_experiment(
        active_root=active_root,
        contract_path=contract_path,
        experiment=args.experiment,
        hypothesis_id=args.hypothesis_id,
        purpose=args.purpose,
        snapshot_id=args.snapshot_id,
        timeout_seconds=max(1, args.timeout_seconds),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc), "can_trade": False}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(2)

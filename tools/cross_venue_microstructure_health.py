#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.forward_runtime_health_check import age_minutes, process_alive  # noqa: E402


HEALTHY_COLLECTING = "cross_venue_microstructure_healthy_collecting"
HEALTHY_READY = "cross_venue_microstructure_healthy_research_ready"
DEGRADED = "cross_venue_microstructure_degraded"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest(manifest: dict[str, Any], base_root: Path = ROOT) -> dict[str, Any]:
    files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    checks: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        path = Path(str(item.get("path") or ""))
        if not path.is_absolute():
            path = base_root / path
        exists = path.is_file()
        actual = sha256_file(path) if exists else None
        checks.append(
            {
                "path": str(path),
                "exists": exists,
                "expected_sha256": item.get("sha256"),
                "actual_sha256": actual,
                "passed": exists and actual == item.get("sha256"),
            }
        )
    paths_exist = len(checks) == 3 and all(row["exists"] for row in checks)
    return {"files": checks, "paths_exist": paths_exist, "passed": paths_exist and all(row["passed"] for row in checks)}


def verify_manifest_coherently(
    manifest_path: Path,
    loop_status_path: Path,
    base_root: Path = ROOT,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    loop_before = read_json(loop_status_path)
    manifest_before = read_json(manifest_path)
    verification = verify_manifest(manifest_before, base_root)
    loop_after = read_json(loop_status_path)
    manifest_after = read_json(manifest_path)
    state_changed = (
        loop_before.get("ts") != loop_after.get("ts")
        or loop_before.get("status") != loop_after.get("status")
        or manifest_before.get("generated_at") != manifest_after.get("generated_at")
    )
    retried = False
    if state_changed and loop_after.get("status") != "running_once":
        verification = verify_manifest(manifest_after, base_root)
        retried = True
        loop_after = read_json(loop_status_path)
        manifest_after = read_json(manifest_path)
    verification["runtime_state_changed_during_verification"] = state_changed
    verification["verification_retried"] = retried
    return loop_after, manifest_after, verification


def gate(name: str, passed: bool, actual: Any, required: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "actual": actual, "required": required, "severity": "hard"}


def evaluate_health(
    *,
    report: dict[str, Any],
    loop: dict[str, Any],
    last_run: dict[str, Any],
    manifest_verification: dict[str, Any],
    report_age_minutes: float | None,
    loop_age_minutes: float | None,
    last_run_age_minutes: float | None,
    loop_pid_alive: bool,
    max_age_seconds: int,
    storage_guard: dict[str, Any] | None = None,
    storage_guard_age_minutes: float | None = None,
    max_storage_age_seconds: int = 300,
) -> dict[str, Any]:
    max_age_minutes = max_age_seconds / 60.0
    max_storage_age_minutes = max_storage_age_seconds / 60.0
    integrity = report.get("trade_id_integrity") if isinstance(report.get("trade_id_integrity"), dict) else {}
    backfill = report.get("gap_backfill") if isinstance(report.get("gap_backfill"), dict) else {}
    readiness = report.get("research_readiness") if isinstance(report.get("research_readiness"), dict) else {}
    binance_missing = int(integrity.get("binance", {}).get("missing_ids", -1))
    coinbase_missing = int(integrity.get("coinbase", {}).get("missing_ids", -1))
    classification = str(report.get("classification") or "missing")
    # Only an in-progress refresh may mutate the sqlite/features/state files between
    # health checks. Once the loop is sleeping, hashes must match again; otherwise a
    # partially written or externally changed collection could be reported healthy.
    manifest_collecting_update_ok = (
        classification == "cross_venue_microstructure_forward_collecting"
        and loop.get("status") == "running_once"
        and manifest_verification.get("paths_exist") is True
    )
    gates = [
        gate("report_present", bool(report), bool(report), True),
        gate(
            "collector_classification_safe",
            classification in {
                "cross_venue_microstructure_forward_collecting",
                "cross_venue_microstructure_ready_for_preregistered_research",
            },
            classification,
            "collecting_or_research_ready",
        ),
        gate("report_fresh", report_age_minutes is not None and report_age_minutes <= max_age_minutes, report_age_minutes, f"<={max_age_minutes}m"),
        gate("loop_status_fresh", loop_age_minutes is not None and loop_age_minutes <= max_age_minutes, loop_age_minutes, f"<={max_age_minutes}m"),
        gate("loop_status_expected", loop.get("status") in {"sleeping", "running_once"}, loop.get("status"), "sleeping_or_running_once"),
        gate("loop_pid_alive", loop_pid_alive, loop_pid_alive, True),
        gate("last_refresh_fresh", last_run_age_minutes is not None and last_run_age_minutes <= max_age_minutes, last_run_age_minutes, f"<={max_age_minutes}m"),
        gate("last_refresh_completed", last_run.get("status") == "completed_data_only", last_run.get("status"), "completed_data_only"),
        gate("last_refresh_exit_zero", int(last_run.get("exit_code", -1)) == 0, last_run.get("exit_code"), 0),
        gate("binance_trade_id_gaps_zero", binance_missing == 0, binance_missing, 0),
        gate("coinbase_trade_id_gaps_zero", coinbase_missing == 0, coinbase_missing, 0),
        gate("backfill_budget_available", backfill.get("page_budget_exhausted") is False, backfill.get("page_budget_exhausted"), False),
        gate(
            "collection_manifest_verified",
            manifest_verification.get("passed") is True or manifest_collecting_update_ok,
            {"hash_verified": manifest_verification.get("passed"), "paths_exist": manifest_verification.get("paths_exist"), "loop_status": loop.get("status")},
            "hash_verified_or_paths_exist_during_active_collecting",
        ),
        gate("collector_can_trade_false", report.get("can_trade") is False, report.get("can_trade"), False),
    ]
    if storage_guard is not None:
        gates.extend(
            [
                gate("storage_guard_present", bool(storage_guard), bool(storage_guard), True),
                gate(
                    "storage_guard_fresh",
                    storage_guard_age_minutes is not None and storage_guard_age_minutes <= max_storage_age_minutes,
                    storage_guard_age_minutes,
                    f"<={max_storage_age_minutes}m",
                ),
                gate(
                    "storage_guard_not_degraded",
                    storage_guard.get("classification") in {"cross_venue_microstructure_storage_ok", "cross_venue_microstructure_storage_warn"},
                    storage_guard.get("classification"),
                    "ok_or_warn",
                ),
                gate("storage_guard_can_trade_false", storage_guard.get("can_trade") is False, storage_guard.get("can_trade"), False),
            ]
        )
    passed = all(row["passed"] for row in gates)
    health_classification = HEALTHY_READY if passed and readiness.get("ready") is True else HEALTHY_COLLECTING if passed else DEGRADED
    failed = [row["name"] for row in gates if not row["passed"]]
    return {
        "classification": health_classification,
        "gates": gates,
        "failed_hard_gates": failed,
        "observed": {
            "collector_classification": classification,
            "report_age_minutes": report_age_minutes,
            "loop_status": loop.get("status"),
            "loop_pid": loop.get("pid"),
            "loop_pid_alive": loop_pid_alive,
            "loop_age_minutes": loop_age_minutes,
            "last_refresh_status": last_run.get("status"),
            "last_refresh_exit_code": last_run.get("exit_code"),
            "last_refresh_age_minutes": last_run_age_minutes,
            "binance_missing_ids": binance_missing,
            "coinbase_missing_ids": coinbase_missing,
            "backfill_page_budget_exhausted": backfill.get("page_budget_exhausted"),
            "research_ready": readiness.get("ready"),
            "manifest_verified": manifest_verification.get("passed"),
            "manifest_paths_exist": manifest_verification.get("paths_exist"),
            "storage_guard_classification": storage_guard.get("classification") if isinstance(storage_guard, dict) else None,
            "storage_guard_age_minutes": storage_guard_age_minutes,
            "storage_guard_failed_hard_gates": storage_guard.get("failed_hard_gates") if isinstance(storage_guard, dict) else None,
        },
        "next_action": "continue_forward_collection" if passed else "inspect_and_repair_microstructure_collector",
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    observed = report["observed"]
    return "\n".join(
        [
            "# Cross-Venue Microstructure Health",
            "",
            f"Generated: `{report['generated_at']}`",
            "",
            f"- Classification: `{report['classification']}`.",
            f"- Failed hard gates: `{', '.join(report['failed_hard_gates']) or 'none'}`.",
            f"- Loop: `{observed['loop_status']}`, PID `{observed['loop_pid']}`, alive `{observed['loop_pid_alive']}`.",
            f"- Report / loop / refresh age minutes: `{observed['report_age_minutes']}` / `{observed['loop_age_minutes']}` / `{observed['last_refresh_age_minutes']}`.",
            f"- Missing trade IDs Binance/Coinbase: `{observed['binance_missing_ids']}` / `{observed['coinbase_missing_ids']}`.",
            f"- Manifest verified: `{observed['manifest_verified']}`.",
            f"- Storage guard: `{observed.get('storage_guard_classification')}`, age `{observed.get('storage_guard_age_minutes')}`m, failed `{observed.get('storage_guard_failed_hard_gates')}`.",
            "- Health monitoring only; no signals and no orders.",
            "- `can_trade=false`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed health check for cross-venue microstructure collection")
    parser.add_argument("--report", default="docs/CROSS_VENUE_MICROSTRUCTURE_DATA_QUALITY_2026-06-24.json")
    parser.add_argument("--loop-status", default="logs/cross_venue_microstructure/microstructure_loop_status.json")
    parser.add_argument("--last-run", default="logs/cross_venue_microstructure/microstructure_refresh_last_run.json")
    parser.add_argument("--manifest", default="data/cross_venue_microstructure/COLLECTION_MANIFEST.json")
    parser.add_argument("--storage-guard", default="docs/CROSS_VENUE_MICROSTRUCTURE_STORAGE_GUARD_2026-06-25.json")
    parser.add_argument("--manifest-root", default=str(ROOT))
    parser.add_argument("--max-age-seconds", type=int, default=180)
    parser.add_argument("--max-storage-age-seconds", type=int, default=300)
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_HEALTH_2026-06-24")
    args = parser.parse_args()
    now = now_utc()
    report_path = resolve_path(args.report)
    loop_status_path = resolve_path(args.loop_status)
    last_run_path = resolve_path(args.last_run)
    manifest_path = resolve_path(args.manifest)
    storage_guard_path = resolve_path(args.storage_guard)
    loop, _manifest, manifest_verification = verify_manifest_coherently(
        manifest_path,
        loop_status_path,
        Path(args.manifest_root).resolve(),
    )
    report = read_json(report_path)
    last_run = read_json(last_run_path)
    storage_guard = read_json(storage_guard_path)
    result = evaluate_health(
        report=report,
        loop=loop,
        last_run=last_run,
        manifest_verification=manifest_verification,
        storage_guard=storage_guard,
        report_age_minutes=age_minutes(report.get("generated_at"), now),
        loop_age_minutes=age_minutes(loop.get("ts"), now),
        last_run_age_minutes=age_minutes(last_run.get("ts"), now),
        storage_guard_age_minutes=age_minutes(storage_guard.get("generated_at"), now),
        loop_pid_alive=process_alive(loop.get("pid")),
        max_age_seconds=args.max_age_seconds,
        max_storage_age_seconds=args.max_storage_age_seconds,
    )
    result["generated_at"] = now_iso()
    result["runtime_boundary"] = {"health_only": True, "signals_allowed": False, "orders_allowed": False, "can_trade": False}
    prefix = resolve_path(args.out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    prefix.with_suffix(".md").write_text(render_markdown(result), encoding="utf-8")
    print(json.dumps({"classification": result["classification"], "failed_hard_gates": result["failed_hard_gates"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if result["classification"] != DEGRADED else 2


if __name__ == "__main__":
    raise SystemExit(main())

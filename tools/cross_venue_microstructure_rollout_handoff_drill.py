#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cross_venue_microstructure_post_seal_auto_run_guard import (
    build_report as build_guard_report,
    latest_path_from_contract,
    read_json,
    write_json,
)
from tools.cross_venue_microstructure_seal_pipeline_drill import create_synthetic_snapshot
from tools.cross_venue_microstructure_snapshot_transition_monitor import build_transition_report


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def waiting_gate() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": "waiting_for_microstructure_readiness",
        "snapshot_id": None,
        "summary": {
            "passed": 8,
            "total": 12,
            "failed": [
                "collector_research_ready",
                "collector_classification_ready",
                "health_research_ready",
                "dual_book_coverage",
            ],
        },
        "readiness_diagnostics": {
            "primary_blocker": "coverage_threshold",
            "remaining_hours": 0.0,
            "trade_coverage_pct": 100.0,
            "book_coverage_pct": 55.0,
            "binance_missing_ids": 0,
            "coinbase_missing_ids": 0,
        },
        "runtime_boundary": {
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def book_coverage_diagnostic() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": "microstructure_book_coverage_wait_for_old_gaps_to_roll_out",
        "recent_windows": {
            "6h": {"dual_book_coverage_pct": 100.0},
            "24h": {"dual_book_coverage_pct": 99.65},
        },
        "eta": {"eta_utc": "2099-01-01T00:00:00+00:00"},
        "can_trade": False,
    }


def synthetic_preseal_plan() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": "preseal_launch_plan_ready_waiting_for_snapshot",
        "checks": {
            "prereg_queue_valid": True,
            "runner_contract_valid": True,
            "all_hypotheses_implemented_locked": True,
            "snapshot_not_yet_opened": True,
            "can_trade_false_everywhere": True,
        },
        "runtime_boundary": {
            "synthetic_drill_only": True,
            "runs_research": False,
            "opens_validation": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def sealed_gate(snapshot_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": "microstructure_snapshot_sealed",
        "snapshot_id": snapshot_id,
        "dataset_sha256": "synthetic-rollout-handoff-drill",
        "summary": {"passed": 12, "total": 12, "failed": []},
        "readiness_diagnostics": {
            "primary_blocker": "none",
            "remaining_hours": 0.0,
            "trade_coverage_pct": 100.0,
            "book_coverage_pct": 100.0,
            "binance_missing_ids": 0,
            "coinbase_missing_ids": 0,
        },
        "runtime_boundary": {
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "synthetic_drill": True,
        "can_trade": False,
    }


def runtime_is_closed(payload: dict[str, Any]) -> bool:
    runtime = payload.get("runtime_boundary") if isinstance(payload.get("runtime_boundary"), dict) else {}
    return (
        payload.get("can_trade") is False
        and runtime.get("signals_allowed") is False
        and runtime.get("orders_allowed") is False
        and runtime.get("can_trade") is False
    )


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Cross-Venue Microstructure Rollout Handoff Drill",
            "",
            f"- Generated: `{report['generated_at']}`.",
            f"- Decision: `{report['decision']}`.",
            f"- Checks passed: `{report['checks_passed']}/{report['checks_total']}`.",
            f"- Before seal: `{report['states']['before_seal']}`.",
            f"- Ready state: `{report['states']['ready_for_runner']}`.",
            f"- Runner execution: `{report['states']['runner_execution']}`.",
            f"- After runner: `{report['states']['after_runner']}`.",
            f"- Duplicate call: `{report['states']['duplicate_call']}`.",
            f"- Tested configurations: `{report.get('runner_tested_total')}`.",
            "- The drill uses a temporary synthetic snapshot and consumes no real OOS/forward evidence.",
            "- Validation, signals, paper execution and orders remain closed.",
            "- `can_trade=false`.",
            "",
        ]
    )


def run_drill(work_dir: Path, out_prefix: Path, timeout_seconds: int) -> tuple[int, dict[str, Any]]:
    active_root = work_dir / "Active"
    active_root.mkdir(parents=True, exist_ok=True)
    snapshot_id = "synthetic-rollout-handoff"
    create_synthetic_snapshot(active_root, snapshot_id)

    contract_path = ROOT / "configs" / "CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT.json"
    contract = read_json(contract_path)
    preseal = synthetic_preseal_plan()
    gate_path = active_root / "docs" / "SNAPSHOT_GATE.json"
    runner_out_prefix = active_root / "docs" / "RESEARCH_RUNNER"
    guard_out_prefix = active_root / "docs" / "POST_SEAL_GUARD"

    gate_waiting = waiting_gate()
    coverage = book_coverage_diagnostic()
    write_json(gate_path, gate_waiting)
    transition_waiting = build_transition_report(gate_waiting, {}, {}, coverage)
    guard_waiting = build_guard_report(
        active_root=active_root,
        preseal_plan=preseal,
        snapshot_gate=gate_waiting,
        contract=contract,
        latest_runner={},
        execute=False,
        force=False,
        contract_path=contract_path,
        snapshot_gate_path=gate_path,
        runner_out_prefix=runner_out_prefix,
        timeout_seconds=timeout_seconds,
    )

    gate_sealed = sealed_gate(snapshot_id)
    write_json(gate_path, gate_sealed)
    transition_ready = build_transition_report(gate_sealed, {}, transition_waiting, coverage)
    guard_preview = build_guard_report(
        active_root=active_root,
        preseal_plan=preseal,
        snapshot_gate=gate_sealed,
        contract=contract,
        latest_runner={},
        execute=False,
        force=False,
        contract_path=contract_path,
        snapshot_gate_path=gate_path,
        runner_out_prefix=runner_out_prefix,
        timeout_seconds=timeout_seconds,
    )
    guard_executed = build_guard_report(
        active_root=active_root,
        preseal_plan=preseal,
        snapshot_gate=gate_sealed,
        contract=contract,
        latest_runner={},
        execute=True,
        force=False,
        contract_path=contract_path,
        snapshot_gate_path=gate_path,
        runner_out_prefix=runner_out_prefix,
        timeout_seconds=timeout_seconds,
    )
    write_json(guard_out_prefix.with_suffix(".json"), guard_executed)

    runner_report = read_json(runner_out_prefix.with_suffix(".json"))
    latest_runner = read_json(latest_path_from_contract(active_root, contract))
    transition_done = build_transition_report(gate_sealed, runner_report, transition_ready, coverage)
    guard_duplicate = build_guard_report(
        active_root=active_root,
        preseal_plan=preseal,
        snapshot_gate=gate_sealed,
        contract=contract,
        latest_runner=latest_runner,
        execute=True,
        force=False,
        contract_path=contract_path,
        snapshot_gate_path=gate_path,
        runner_out_prefix=runner_out_prefix,
        timeout_seconds=timeout_seconds,
    )

    checks = {
        "rolling_gap_classified_as_waiting": transition_waiting.get("transition_state") == "waiting_for_book_coverage_rollout",
        "runner_closed_before_seal": transition_waiting.get("research_runner_can_attempt_now") is False,
        "guard_armed_before_seal": guard_waiting.get("decision") == "post_seal_auto_run_guard_armed_waiting_for_snapshot",
        "sealed_snapshot_ready_for_runner": transition_ready.get("transition_state") == "sealed_snapshot_ready_for_train_research_batch",
        "guard_preview_is_exactly_once": guard_preview.get("decision") == "post_seal_auto_run_guard_would_execute_once",
        "locked_runner_executed_once": guard_executed.get("decision") == "post_seal_auto_run_guard_executed_locked_runner_once",
        "runner_completed_locked_matrix": runner_report.get("completed") == 4
        and runner_report.get("failed") == 0
        and runner_report.get("tested_total") == 774,
        "transition_detects_completed_runner": transition_done.get("transition_state") == "sealed_snapshot_research_batch_already_completed",
        "duplicate_execution_blocked": guard_duplicate.get("decision") == "post_seal_auto_run_guard_duplicate_blocked_already_completed",
        "all_runtime_boundaries_closed": all(
            runtime_is_closed(payload)
            for payload in (
                transition_waiting,
                guard_waiting,
                transition_ready,
                guard_preview,
                guard_executed,
                transition_done,
                guard_duplicate,
            )
        )
        and runner_report.get("can_trade") is False,
    }
    passed = all(checks.values())
    report = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": "microstructure_rollout_handoff_drill_passed" if passed else "microstructure_rollout_handoff_drill_failed",
        "snapshot_id": snapshot_id,
        "work_dir": str(work_dir),
        "checks": checks,
        "checks_passed": sum(checks.values()),
        "checks_total": len(checks),
        "states": {
            "before_seal": transition_waiting.get("transition_state"),
            "guard_before_seal": guard_waiting.get("decision"),
            "ready_for_runner": transition_ready.get("transition_state"),
            "runner_preview": guard_preview.get("decision"),
            "runner_execution": guard_executed.get("decision"),
            "after_runner": transition_done.get("transition_state"),
            "duplicate_call": guard_duplicate.get("decision"),
        },
        "runner_decision": runner_report.get("decision"),
        "runner_completed": runner_report.get("completed"),
        "runner_failed": runner_report.get("failed"),
        "runner_tested_total": runner_report.get("tested_total"),
        "runtime_boundary": {
            "synthetic_drill_only": True,
            "consumes_real_trial_budget": False,
            "opens_validation": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    return (0 if passed else 1), report


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic rolling-gap to exactly-once research handoff drill")
    parser.add_argument("--work-dir")
    parser.add_argument(
        "--out-prefix",
        default="docs/CROSS_VENUE_MICROSTRUCTURE_ROLLOUT_HANDOFF_DRILL_2026-07-11",
    )
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()

    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    if args.work_dir:
        work_dir = Path(args.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        code, report = run_drill(work_dir, out_prefix, max(1, args.timeout_seconds))
    else:
        with tempfile.TemporaryDirectory(prefix="microstructure-rollout-handoff-") as temp_name:
            code, report = run_drill(Path(temp_name).resolve(), out_prefix, max(1, args.timeout_seconds))
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "checks": f"{report['checks_passed']}/{report['checks_total']}",
                "tested_total": report.get("runner_tested_total"),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())

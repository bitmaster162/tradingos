from __future__ import annotations

from pathlib import Path

from tools.cross_venue_microstructure_post_seal_auto_run_guard import build_report


def preseal() -> dict:
    return {"decision": "preseal_launch_plan_ready_waiting_for_snapshot", "can_trade": False}


def contract(tmp_path: Path) -> dict:
    return {
        "run_root_relative_to_active": str(tmp_path / "_dl" / "research_runs_cross_venue_microstructure"),
        "runtime_boundary": {"orders_allowed": False, "can_trade": False},
    }


def report(tmp_path: Path, *, gate: dict, latest: dict | None = None, execute: bool = False) -> dict:
    return build_report(
        active_root=tmp_path,
        preseal_plan=preseal(),
        snapshot_gate=gate,
        contract=contract(tmp_path),
        latest_runner=latest or {},
        execute=execute,
        force=False,
        contract_path=tmp_path / "configs" / "contract.json",
        snapshot_gate_path=tmp_path / "docs" / "gate.json",
        runner_out_prefix=tmp_path / "docs" / "runner",
        timeout_seconds=1,
    )


def test_post_seal_guard_armed_waiting_for_snapshot(tmp_path: Path) -> None:
    payload = report(tmp_path, gate={"decision": "waiting_for_microstructure_readiness", "snapshot_id": None, "can_trade": False})

    assert payload["decision"] == "post_seal_auto_run_guard_armed_waiting_for_snapshot"
    assert payload["runtime_boundary"]["orders_allowed"] is False
    assert payload["can_trade"] is False


def test_post_seal_guard_would_execute_once_when_sealed(tmp_path: Path) -> None:
    payload = report(tmp_path, gate={"decision": "microstructure_snapshot_sealed", "snapshot_id": "snap-1", "can_trade": False})

    assert payload["decision"] == "post_seal_auto_run_guard_would_execute_once"
    assert payload["snapshot"]["snapshot_id"] == "snap-1"
    assert payload["execute_requested"] is False
    assert payload["command"][-1] == "run-if-ready"
    assert payload["command"].index("--active-root") < payload["command"].index("run-if-ready")
    assert payload["can_trade"] is False


def test_post_seal_guard_blocks_duplicate_completed_snapshot(tmp_path: Path) -> None:
    payload = report(
        tmp_path,
        gate={"decision": "microstructure_snapshot_sealed", "snapshot_id": "snap-1", "can_trade": False},
        latest={"snapshot_id": "snap-1", "status": "completed", "run_id": "run-1"},
    )

    assert payload["decision"] == "post_seal_auto_run_guard_duplicate_blocked_already_completed"
    assert payload["latest_runner"]["run_id"] == "run-1"
    assert payload["can_trade"] is False

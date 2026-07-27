from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ops.control_panel.control_panel import latest_research_runtime_supervisor_summary


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def seed_supervisor(root: Path, *, age_seconds: int = 0) -> None:
    observed_at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    write_json(
        root / "runtime" / "LATEST.json",
        {
            "generated_at": observed_at.isoformat(),
            "decision": "research_runtime_registry_healthy",
            "summary": {
                "registered_components": 10,
                "healthy_components": 10,
                "registered_pids": 10,
                "unique_pids": 10,
                "retired_versions": 3,
                "signature_groups": 6,
            },
            "failed_checks": [],
            "can_trade": False,
        },
    )
    write_json(
        root / "runtime" / "loop_status.json",
        {
            "updated_at": observed_at.isoformat(),
            "status": "sleeping",
            "pid": os.getpid(),
            "orders_allowed": False,
            "can_trade": False,
        },
    )
    write_json(
        root / "launcher_status.json",
        {
            "status": "already_running",
            "startup_launch_only": True,
            "automatic_restart_allowed": False,
            "can_trade": False,
        },
    )


def test_supervisor_summary_accepts_fresh_healthy_runtime(tmp_path: Path) -> None:
    seed_supervisor(tmp_path)

    result = latest_research_runtime_supervisor_summary(tmp_path, tmp_path / "launcher_status.json")

    assert result["healthy"] is True
    assert result["healthy_components"] == 10
    assert result["loop_pid_alive"] is True
    assert result["launcher_status"] == "already_running"
    assert result["automatic_restart_allowed"] is False
    assert result["can_trade"] is False


def test_supervisor_summary_fails_closed_when_stale(tmp_path: Path) -> None:
    seed_supervisor(tmp_path, age_seconds=901)

    result = latest_research_runtime_supervisor_summary(tmp_path, tmp_path / "launcher_status.json")

    assert result["exists"] is True
    assert result["healthy"] is False
    assert result["can_trade"] is False


def test_supervisor_summary_fails_closed_when_missing(tmp_path: Path) -> None:
    result = latest_research_runtime_supervisor_summary(tmp_path)

    assert result["exists"] is False
    assert result["healthy"] is False
    assert result["can_trade"] is False

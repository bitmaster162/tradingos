from __future__ import annotations

import json
from datetime import datetime, timezone

from ops.control_panel import control_panel


def test_control_panel_surfaces_managed_runtime_and_active_bitunix_v3r4(tmp_path, monkeypatch) -> None:
    runtime_path = tmp_path / "logs" / "runtime_autostart_status.json"
    runtime_path.parent.mkdir(parents=True)
    runtime_path.write_text(
        json.dumps(
            {
                "status": "completed",
                "ts": "2026-07-14T18:54:35Z",
                "runtime_components_expected": 19,
                "runtime_components_healthy": 19,
                "runtime_components_failed": [],
                "runtime_component_states": [
                    {
                        "id": "bitunix_wo105_v3r4_forward",
                        "decision": "running_verified",
                        "ownership_decision": "running_verified_job_contained",
                        "job_contained": True,
                        "pid": 12296,
                    }
                ],
                "bitunix_wo105_v3r4_forward_eligible": True,
                "bitunix_wo105_v3r4_forward_already_running": False,
                "bitunix_wo105_v3r4_public_shadow_only": True,
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )
    bitunix_log_dir = tmp_path / "logs" / "bitunix_wo105_v3r4"
    bitunix_log_dir.mkdir(parents=True)
    (bitunix_log_dir / "bitunix_wo105_v3r4_forward_loop_status.json").write_text(
        json.dumps({"status": "waiting_forward_floor", "pid": 12296, "can_trade": False}),
        encoding="utf-8",
    )
    (bitunix_log_dir / "bitunix_wo105_v3r4_forward_loop.lock.json").write_text(
        json.dumps({"pid": 12296, "can_trade": False}),
        encoding="utf-8",
    )
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "BITUNIX_WO105_V3R4_STATUS_2026-07-15.json").write_text(
        json.dumps(
            {
                "phase": "WAITING_FORWARD_FLOOR",
                "forward_start_at": "2026-07-15T04:00:00Z",
                "forward_progress": "0/30",
                "terminal_forward_progress": "0/30",
                "edge_evaluated": False,
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )
    (docs_dir / "BITUNIX_WO105_V3R4_BLIND_REVIEW_GATE_2026-07-15.json").write_text(
        json.dumps({"decision": "blind_waiting", "can_trade": False}),
        encoding="utf-8",
    )
    (docs_dir / "BITUNIX_WO105_V3R4_FIRST_CYCLE_GATE_2026-07-15.json").write_text(
        json.dumps({"decision": "first_cycle_waiting", "can_trade": False}),
        encoding="utf-8",
    )
    (docs_dir / "BITUNIX_WO105_V3R4_FORWARD_HEALTH_2026-07-15.json").write_text(
        json.dumps(
            {
                "decision": "health_pass",
                "failures": [],
                "warnings": ["rest_snapshots_excluded_fail_closed"],
                "rest_quality": {"accepted_runs": 74, "candidate_runs": 75},
                "ws_quality": {"accepted_runs": 12, "candidate_runs": 12},
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(control_panel, "ROOT", tmp_path)

    runtime = control_panel.latest_autostart_summary()["runtime"]

    assert runtime["components_expected"] == 19
    assert runtime["components_healthy"] == 19
    assert runtime["components_failed"] == []
    assert runtime["component_states"][0]["id"] == "bitunix_wo105_v3r4_forward"
    assert runtime["snapshot_stale"] is True
    assert runtime["receipt_identity_alive_count"] == 0
    assert runtime["bitunix_wo105_v3r4"] == {
        "eligible": True,
        "already_running_at_start": False,
        "public_shadow_only": True,
        "decision": "running_verified",
        "ownership_decision": "running_verified_job_contained",
        "job_contained": True,
        "pid": 12296,
        "loop_status": "waiting_forward_floor",
        "loop_pid": 12296,
        "loop_lock_exists": True,
        "loop_lock_pid": 12296,
        "phase": "WAITING_FORWARD_FLOOR",
        "forward_start_at": "2026-07-15T04:00:00Z",
        "forward_progress": "0/30",
        "terminal_forward_progress": "0/30",
        "first_cycle_decision": "first_cycle_waiting",
        "blind_review_decision": "blind_waiting",
        "edge_evaluated": False,
        "health_decision": "health_pass",
        "health_failures": [],
        "health_warnings": ["rest_snapshots_excluded_fail_closed"],
        "rest_quality": {"accepted_runs": 74, "candidate_runs": 75},
        "ws_quality": {"accepted_runs": 12, "candidate_runs": 12},
        "can_trade": False,
    }


def test_dashboard_header_renders_runtime_and_bitunix_v3r4_state() -> None:
    source = (control_panel.ROOT / "ops" / "control_panel" / "control_panel.py").read_text(encoding="utf-8")

    assert "runtimeSnapshot:${fmt(autostart.runtime?.components_healthy)}/${fmt(autostart.runtime?.components_expected)}" in source
    assert "receiptAlive:${fmt(autostart.runtime?.receipt_identity_alive_count)}/${fmt(autostart.runtime?.receipt_components_observed)}" in source
    assert "pidDrift:${fmt(autostart.runtime?.receipt_pid_drift_count)}" in source
    assert "bitunixV3R4:${fmt(autostart.runtime?.bitunix_wo105_v3r4?.phase)}:${fmt(autostart.runtime?.bitunix_wo105_v3r4?.terminal_forward_progress)}" in source
    assert "rest_quality?.accepted_runs" in source
    assert "ws_quality?.accepted_runs" in source


def test_runtime_summary_distinguishes_stale_snapshot_pid_from_current_receipt(
    tmp_path, monkeypatch
) -> None:
    runtime_path = tmp_path / "logs" / "runtime_autostart_status.json"
    runtime_path.parent.mkdir(parents=True)
    runtime_path.write_text(
        json.dumps(
            {
                "status": "completed",
                "ts": datetime.now(timezone.utc).isoformat(),
                "runtime_components_expected": 1,
                "runtime_components_healthy": 1,
                "runtime_components_failed": [],
                "runtime_component_states": [
                    {
                        "id": "collector",
                        "decision": "running_verified",
                        "ownership_decision": "running_verified_job_contained",
                        "job_contained": True,
                        "pid": 100,
                    }
                ],
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )
    receipt_dir = tmp_path / "logs" / "runtime_jobs"
    receipt_dir.mkdir()
    (receipt_dir / "collector.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "component": "collector",
                "root": str(tmp_path),
                "pid": 200,
                "process_creation_utc": "2026-07-15T10:13:18.5004166Z",
                "session_id": 1,
                "launch_state": "running",
                "live_trading_locked": True,
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(control_panel, "ROOT", tmp_path)
    monkeypatch.setattr(
        control_panel,
        "process_alive",
        lambda pid, expected_creation_utc=None: int(pid) == 200 and expected_creation_utc is not None,
    )

    runtime = control_panel.latest_autostart_summary()["runtime"]
    state = runtime["component_states"][0]

    assert runtime["snapshot_stale"] is False
    assert runtime["receipt_identity_alive_count"] == 1
    assert runtime["receipt_pid_drift_count"] == 1
    assert runtime["receipt_identity_all_alive"] is True
    assert state["snapshot_pid_alive_unbound"] is False
    assert state["receipt_pid"] == 200
    assert state["receipt_identity_alive"] is True
    assert state["snapshot_pid_matches_receipt"] is False
    assert state["pid_drifted_from_snapshot"] is True

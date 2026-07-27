from __future__ import annotations

from datetime import datetime, timezone

from tools.cross_venue_microstructure_autopilot_audit import build_report


NOW = datetime(2026, 6, 29, 8, 40, tzinfo=timezone.utc)


def status(ts: str = "2026-06-29T08:39:00Z", *, can_trade: bool = False) -> dict:
    return {
        "ts": ts,
        "status": "sleeping",
        "pid": 1234,
        "sleep_seconds": 60,
        "sends_orders": False,
        "can_trade": can_trade,
        "extra": {
            "last_source_integrity_exit_code": 0,
            "last_storage_exit_code": 0,
            "last_collector_sla_exit_code": 0,
            "last_health_exit_code": 0,
            "last_seal_exit_code": 0,
            "last_readiness_progress_exit_code": 0,
            "last_snapshot_transition_exit_code": 0,
            "last_prereg_exit_code": 0,
            "last_runner_contract_exit_code": 0,
            "last_research_runner_exit_code": 0,
            "last_candidate_governance_exit_code": 0,
            "last_candidate_review_exit_code": 0,
            "last_validation_protocol_exit_code": 0,
            "last_validation_approval_exit_code": 0,
            "last_validation_runner_exit_code": 0,
        },
    }


def gate() -> dict:
    return {
        "decision": "waiting_for_microstructure_readiness",
        "readiness_diagnostics": {
            "remaining_hours": 55.5,
            "estimated_earliest_time_gate_at_utc": "2026-07-01T16:00:00+00:00",
        },
        "can_trade": False,
    }


def transition(state: str = "waiting_for_minimum_time_window") -> dict:
    return {
        "transition_state": state,
        "remaining_hours": 55.5,
        "earliest_time_gate_at_utc": "2026-07-01T16:00:00+00:00",
        "next_action": "continue_collecting_until_time_gate",
        "snapshot_id": None,
        "can_trade": False,
    }


def write_watchdog_script(tmp_path, *, has_run_if_ready: bool = True):
    text = (
        "tools\\active_source_integrity_guard.py\n"
        "if ($SourceIntegrityExit -eq 0) { tools\\cross_venue_microstructure_research_runner.py run-if-ready }\n"
        "source_integrity_blocked_research_runner"
        if has_run_if_ready
        else "no runner"
    )
    path = tmp_path / "Run-CrossVenueMicrostructureWatchdogLoop.ps1"
    path.write_text(text, encoding="utf-8")
    return path


def write_guarded_watchdog_script(tmp_path):
    path = tmp_path / "Run-CrossVenueMicrostructureWatchdogLoop.ps1"
    path.write_text(
        'tools\\active_source_integrity_guard.py\n'
        'if ($SourceIntegrityExit -eq 0) { tools\\cross_venue_microstructure_post_seal_auto_run_guard.py --execute }\n'
        'source_integrity_blocked_research_runner',
        encoding="utf-8",
    )
    return path


def test_autopilot_audit_waits_when_snapshot_window_not_ready(tmp_path) -> None:
    report = build_report(
        collector_status=status(),
        watchdog_status=status(),
        snapshot_gate=gate(),
        snapshot_transition=transition(),
        research_runner={},
        watchdog_script_path=write_watchdog_script(tmp_path),
        now=NOW,
    )

    assert report["decision"] == "microstructure_autopilot_waiting_for_snapshot_window"
    assert report["failed_checks"] == []
    assert report["snapshot"]["remaining_hours"] == 55.5
    assert report["can_trade"] is False


def test_autopilot_audit_detects_ready_for_locked_runner(tmp_path) -> None:
    report = build_report(
        collector_status=status(),
        watchdog_status=status(),
        snapshot_gate={"decision": "microstructure_snapshot_sealed", "snapshot_id": "snap-1", "can_trade": False},
        snapshot_transition=transition("sealed_snapshot_ready_for_train_research_batch") | {"snapshot_id": "snap-1"},
        research_runner={},
        watchdog_script_path=write_watchdog_script(tmp_path),
        now=NOW,
    )

    assert report["decision"] == "microstructure_autopilot_ready_for_locked_runner"
    assert report["next_action"] == "watchdog_should_run_research_runner_run_if_ready_on_next_cycle"
    assert report["runtime_boundary"]["orders_allowed"] is False


def test_autopilot_audit_blocks_when_watchdog_exit_failed(tmp_path) -> None:
    watchdog = status()
    watchdog["extra"]["last_research_runner_exit_code"] = 1

    report = build_report(
        collector_status=status(),
        watchdog_status=watchdog,
        snapshot_gate=gate(),
        snapshot_transition=transition(),
        research_runner={},
        watchdog_script_path=write_watchdog_script(tmp_path),
        now=NOW,
    )

    assert report["decision"] == "microstructure_autopilot_needs_repair"
    assert "watchdog_required_exits_zero" in report["failed_checks"]


def test_autopilot_audit_treats_running_watchdog_with_null_exits_as_in_progress(tmp_path) -> None:
    watchdog = status()
    watchdog["status"] = "running_health_check"
    watchdog["extra"] = {key: None for key in watchdog["extra"]}

    report = build_report(
        collector_status=status(),
        watchdog_status=watchdog,
        snapshot_gate=gate(),
        snapshot_transition=transition(),
        research_runner={},
        watchdog_script_path=write_watchdog_script(tmp_path),
        now=NOW,
    )

    assert report["decision"] == "microstructure_autopilot_cycle_in_progress"
    assert report["failed_checks"] == []
    assert report["watchdog"]["required_exits_state"] == "in_progress"
    assert report["next_action"] == "wait_for_watchdog_cycle_to_finish_then_recheck"
    assert report["can_trade"] is False


def test_autopilot_accepts_exactly_once_post_seal_guard_wiring(tmp_path) -> None:
    report = build_report(
        collector_status=status(),
        watchdog_status=status(),
        snapshot_gate=gate(),
        snapshot_transition=transition(),
        research_runner={},
        watchdog_script_path=write_guarded_watchdog_script(tmp_path),
        now=NOW,
    )

    assert report["failed_checks"] == []
    assert report["watchdog"]["runner_wiring"]["mode"] == "post_seal_exactly_once_guard"
    assert report["watchdog"]["runner_wiring"]["exactly_once_guard"] is True
    assert report["watchdog"]["runner_wiring"]["integrity_fail_closed"] is True
    assert report["runtime_boundary"]["orders_allowed"] is False


def test_autopilot_rejects_runner_without_source_integrity_guard(tmp_path) -> None:
    path = tmp_path / "Run-CrossVenueMicrostructureWatchdogLoop.ps1"
    path.write_text("tools\\cross_venue_microstructure_research_runner.py run-if-ready", encoding="utf-8")
    report = build_report(
        collector_status=status(),
        watchdog_status=status(),
        snapshot_gate=gate(),
        snapshot_transition=transition(),
        research_runner={},
        watchdog_script_path=path,
        now=NOW,
    )

    assert report["decision"] == "microstructure_autopilot_needs_repair"
    assert "watchdog_source_integrity_guard_wired" in report["failed_checks"]
    assert report["can_trade"] is False


def test_autopilot_reports_verified_book_coverage_rollout_wait(tmp_path) -> None:
    transition_report = transition("waiting_for_book_coverage_rollout")
    transition_report["earliest_time_gate_at_utc"] = "2026-07-15T05:27+00:00"
    report = build_report(
        collector_status=status(),
        watchdog_status=status(),
        snapshot_gate=gate(),
        snapshot_transition=transition_report,
        research_runner={},
        watchdog_script_path=write_guarded_watchdog_script(tmp_path),
        now=NOW,
    )

    assert report["decision"] == "microstructure_autopilot_waiting_for_book_coverage_rollout"
    assert report["next_action"] == "keep_collector_and_watchdog_running_until_rolling_window_eta"
    assert report["failed_checks"] == []
    assert report["can_trade"] is False

from __future__ import annotations

from tools.cross_venue_microstructure_snapshot_transition_monitor import build_transition_report


def waiting_gate(remaining: float, primary: str = "minimum_time_window") -> dict:
    return {
        "decision": "waiting_for_microstructure_readiness",
        "snapshot_id": None,
        "summary": {"passed": 7, "total": 11, "failed": ["minimum_hours"]},
        "readiness_diagnostics": {
            "primary_blocker": primary,
            "remaining_hours": remaining,
            "estimated_earliest_time_gate_at_utc": "2026-07-01T12:00:00+00:00",
            "trade_coverage_pct": 99.0,
            "book_coverage_pct": 98.0,
            "binance_missing_ids": 0,
            "coinbase_missing_ids": 0,
        },
        "can_trade": False,
    }


def sealed_gate(snapshot_id: str = "snap-1") -> dict:
    return {
        "decision": "microstructure_snapshot_sealed",
        "snapshot_id": snapshot_id,
        "summary": {"passed": 11, "total": 11, "failed": []},
        "readiness_diagnostics": {"primary_blocker": "none", "remaining_hours": 0.0},
        "can_trade": False,
    }


def test_transition_monitor_waits_for_minimum_time_window() -> None:
    report = build_transition_report(waiting_gate(12.5), {}, {})

    assert report["transition_state"] == "waiting_for_minimum_time_window"
    assert report["research_runner_can_attempt_now"] is False
    assert report["can_trade"] is False


def test_transition_monitor_blocks_after_time_window_when_quality_fails() -> None:
    gate = waiting_gate(0.0, "coverage_threshold")
    gate["summary"]["failed"] = ["dual_book_coverage"]
    report = build_transition_report(gate, {}, {})

    assert report["transition_state"] == "blocked_after_time_window"
    assert report["primary_blocker"] == "coverage_threshold"
    assert report["next_action"] == "fix_failed_snapshot_gates_before_research_batch"


def test_transition_monitor_waits_for_verified_legacy_book_gap_rollout() -> None:
    gate = waiting_gate(0.0, "coverage_threshold")
    gate["summary"]["failed"] = ["dual_book_coverage"]
    coverage = {
        "decision": "microstructure_book_coverage_wait_for_old_gaps_to_roll_out",
        "recent_windows": {
            "6h": {"dual_book_coverage_pct": 100.0},
            "24h": {"dual_book_coverage_pct": 99.5},
        },
        "eta": {"eta_utc": "2026-07-15T05:27+00:00"},
    }

    report = build_transition_report(gate, {}, {}, coverage)

    assert report["transition_state"] == "waiting_for_book_coverage_rollout"
    assert report["book_coverage_rollout_verified"] is True
    assert report["legacy_gap_rollout_verified"] is True
    assert report["current_gap_recovery_verified"] is False
    assert report["earliest_time_gate_at_utc"] == "2026-07-15T05:27+00:00"
    assert report["research_runner_can_attempt_now"] is False
    assert report["can_trade"] is False


def test_transition_monitor_waits_for_confirmed_current_gap_recovery_rollout() -> None:
    gate = waiting_gate(0.0, "coverage_threshold")
    gate["summary"]["failed"] = ["dual_book_coverage"]
    coverage = {
        "decision": "microstructure_book_coverage_recovered_waiting_recent_gap_rollout",
        "recent_windows": {
            "6h": {"dual_book_coverage_pct": 96.5},
            "24h": {"dual_book_coverage_pct": 99.0},
        },
        "eta": {"eta_utc": "2026-07-15T05:27+00:00"},
    }

    report = build_transition_report(gate, {}, {}, coverage)

    assert report["transition_state"] == "waiting_for_book_coverage_rollout"
    assert report["book_coverage_rollout_verified"] is True
    assert report["legacy_gap_rollout_verified"] is False
    assert report["current_gap_recovery_verified"] is True
    assert report["research_runner_can_attempt_now"] is False
    assert report["can_trade"] is False


def test_transition_monitor_marks_sealed_snapshot_ready_for_research_runner() -> None:
    report = build_transition_report(sealed_gate("snap-2"), {"decision": "blocked_waiting_for_sealed_snapshot"}, {})

    assert report["transition_state"] == "sealed_snapshot_ready_for_train_research_batch"
    assert report["snapshot_id"] == "snap-2"
    assert report["research_runner_can_attempt_now"] is True
    assert report["runtime_boundary"]["runs_research_batch"] is False


def test_transition_monitor_detects_completed_batch_for_snapshot() -> None:
    report = build_transition_report(
        sealed_gate("snap-3"),
        {"decision": "microstructure_research_batch_completed_no_candidate", "snapshot_id": "snap-3"},
        {},
    )

    assert report["transition_state"] == "sealed_snapshot_research_batch_already_completed"
    assert report["research_runner_can_attempt_now"] is False


def test_transition_monitor_reports_state_change() -> None:
    report = build_transition_report(
        sealed_gate("snap-4"),
        {"decision": "blocked_waiting_for_sealed_snapshot"},
        {"transition_state": "waiting_for_minimum_time_window"},
    )

    assert report["transition_changed"] is True
    assert report["previous_transition_state"] == "waiting_for_minimum_time_window"

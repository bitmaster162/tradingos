from __future__ import annotations

from tools.cross_venue_microstructure_research_runner_telegram_notify import (
    notification_key,
    notification_kind,
    render_message,
)


def test_waiting_runner_report_does_not_notify() -> None:
    report = {"decision": "blocked_waiting_for_sealed_snapshot", "snapshot_id": None, "can_trade": False}

    assert notification_kind(report) == "waiting_no_notification"


def test_candidate_runner_report_requires_review_notification() -> None:
    report = {
        "decision": "microstructure_candidates_require_validation_review",
        "snapshot_id": "snapshot-1",
        "run_id": "run-1",
        "experiments": 3,
        "completed": 3,
        "failed": 0,
        "candidate_count": 1,
        "tested_total": 450,
        "can_trade": False,
    }

    kind = notification_kind(report)
    message = render_message(kind, report)

    assert kind == "microstructure_candidates_require_review"
    assert notification_key(kind, report) == "microstructure_candidates_require_review|snapshot-1|run-1|microstructure_candidates_require_validation_review"
    assert "Candidates: 1" in message
    assert "No signals or orders" in message


def test_completed_and_failed_runner_reports_have_distinct_notification_kinds() -> None:
    completed = {"decision": "microstructure_research_batch_completed_no_candidate", "snapshot_id": "s", "run_id": "r"}
    failed = {"decision": "microstructure_research_batch_failed", "snapshot_id": "s", "run_id": "r"}

    assert notification_kind(completed) == "microstructure_research_completed"
    assert notification_kind(failed) == "microstructure_research_failed"

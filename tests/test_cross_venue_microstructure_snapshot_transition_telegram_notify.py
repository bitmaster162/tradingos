from __future__ import annotations

from tools.cross_venue_microstructure_snapshot_transition_telegram_notify import (
    notification_key,
    notification_kind,
    render_message,
)


def report(state: str, *, snapshot_id: str | None = None, changed: bool = False) -> dict:
    return {
        "transition_state": state,
        "previous_transition_state": "waiting_for_minimum_time_window" if changed else state,
        "transition_changed": changed,
        "gate_decision": "microstructure_snapshot_sealed" if snapshot_id else "waiting_for_microstructure_readiness",
        "runner_decision": "blocked_waiting_for_sealed_snapshot",
        "snapshot_id": snapshot_id,
        "checks_passed": 11 if snapshot_id else 7,
        "checks_total": 11,
        "primary_blocker": "none" if snapshot_id else "minimum_time_window",
        "remaining_hours": 0 if snapshot_id else 10,
        "research_runner_can_attempt_now": state == "sealed_snapshot_ready_for_train_research_batch",
        "failed_checks": [] if snapshot_id else ["minimum_hours"],
        "next_action": "run_microstructure_research_runner_run_if_ready",
        "can_trade": False,
    }


def test_transition_waiting_is_not_notified() -> None:
    assert notification_kind(report("waiting_for_minimum_time_window")) == "waiting_no_notification"


def test_transition_ready_is_notified() -> None:
    payload = report("sealed_snapshot_ready_for_train_research_batch", snapshot_id="snap-1", changed=True)
    kind = notification_kind(payload)

    assert kind == "snapshot_transition_ready_for_research"
    assert notification_key(kind, payload) == "snapshot_transition_ready_for_research|snap-1|sealed_snapshot_ready_for_train_research_batch|blocked_waiting_for_sealed_snapshot"


def test_transition_blocked_after_time_window_is_notified() -> None:
    payload = report("blocked_after_time_window", changed=True)

    assert notification_kind(payload) == "snapshot_transition_blocked_after_time_window"


def test_transition_done_is_notified() -> None:
    payload = report("sealed_snapshot_research_batch_already_completed", snapshot_id="snap-2", changed=True)

    assert notification_kind(payload) == "snapshot_transition_research_batch_done"


def test_transition_changed_blocked_state_is_notified() -> None:
    payload = report("blocked_unknown_snapshot_gate_decision", changed=True)

    assert notification_kind(payload) == "snapshot_transition_blocked_changed"


def test_transition_message_keeps_runtime_boundary_text() -> None:
    payload = report("sealed_snapshot_ready_for_train_research_batch", snapshot_id="snap-3", changed=True)
    message = render_message(notification_kind(payload), payload)

    assert "Boundary: TRANSITION NOTIFICATION ONLY" in message
    assert "No research run, no signals, no orders" in message

from __future__ import annotations

from tools.cross_venue_microstructure_snapshot_gate_telegram_notify import (
    notification_key,
    notification_kind,
    render_message,
)


def gate(remaining_hours: float, primary: str = "minimum_time_window") -> dict:
    return {
        "decision": "waiting_for_microstructure_readiness",
        "snapshot_id": None,
        "summary": {"passed": 7, "total": 11, "failed": ["minimum_hours"]},
        "readiness_diagnostics": {
            "primary_blocker": primary,
            "remaining_hours": remaining_hours,
            "estimated_earliest_time_gate_at_utc": "2026-07-01T12:00:00+00:00",
            "trade_coverage_pct": 99.0,
            "book_coverage_pct": 96.0,
            "binance_missing_ids": 0,
            "coinbase_missing_ids": 0,
        },
        "can_trade": False,
    }


def test_snapshot_gate_waiting_does_not_notify_before_eta_threshold() -> None:
    assert notification_kind(gate(25.0)) == "waiting_no_notification"


def test_snapshot_gate_eta_milestones_are_distinct() -> None:
    assert notification_kind(gate(23.9)) == "microstructure_snapshot_eta_24h"
    assert notification_kind(gate(5.9)) == "microstructure_snapshot_eta_6h"
    assert notification_kind(gate(0.9)) == "microstructure_snapshot_eta_1h"


def test_snapshot_gate_sealed_notifies_with_snapshot_id() -> None:
    payload = {
        "decision": "microstructure_snapshot_sealed",
        "snapshot_id": "snap-1",
        "summary": {"passed": 11, "total": 11, "failed": []},
        "readiness_diagnostics": {"primary_blocker": "none", "remaining_hours": 0},
        "can_trade": False,
    }

    kind = notification_kind(payload)
    message = render_message(kind, payload)

    assert kind == "microstructure_snapshot_sealed"
    assert notification_key(kind, payload) == "microstructure_snapshot_sealed|snap-1|microstructure_snapshot_sealed"
    assert "Snapshot: snap-1" in message
    assert "No signals or orders" in message


def test_snapshot_gate_reports_blocker_after_time_window() -> None:
    payload = gate(0.0, primary="coverage_threshold")
    payload["summary"]["failed"] = ["dual_book_coverage"]

    kind = notification_kind(payload)

    assert kind == "microstructure_readiness_blocked_after_time_window"
    assert notification_key(kind, payload) == "microstructure_readiness_blocked_after_time_window|coverage_threshold|dual_book_coverage"

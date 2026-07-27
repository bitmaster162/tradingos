from __future__ import annotations

from tools.cross_venue_microstructure_collector_sla_telegram_notify import (
    failure_signature,
    notification_key,
    notification_kind,
    update_incident_state,
)


def report(decision: str = "collector_sla_healthy", failed: list[str] | None = None) -> dict:
    return {
        "decision": decision,
        "classification": "cross_venue_microstructure_forward_collecting",
        "data_generated_at": "2026-06-25T12:00:00+00:00",
        "failed_checks": failed or [],
        "can_trade": False,
    }


def test_sla_telegram_skips_healthy_without_previous_incident() -> None:
    kind = notification_kind(report(), {})

    assert kind == "collector_sla_no_notification"


def test_sla_telegram_opens_new_degraded_incident() -> None:
    degraded = report("collector_sla_degraded_no_trade_inserts", ["cycle_inserted_trades"])

    kind = notification_kind(degraded, {})
    state = update_incident_state({}, degraded, kind)

    assert kind == "collector_sla_degraded"
    assert state["incident_id"] == 1
    assert state["incident_open"] is True
    assert state["last_report_degraded"] is True
    assert state["last_degraded_signature"] == failure_signature(degraded)


def test_sla_telegram_skips_repeated_same_degradation() -> None:
    degraded = report("collector_sla_degraded_no_trade_inserts", ["cycle_inserted_trades"])
    state = update_incident_state({}, degraded, "collector_sla_degraded")

    assert notification_kind(degraded, state) == "collector_sla_no_notification"


def test_sla_telegram_reports_changed_degradation_signature() -> None:
    first = report("collector_sla_degraded_no_trade_inserts", ["cycle_inserted_trades"])
    second = report("collector_sla_degraded_no_book_inserts", ["cycle_inserted_books"])
    state = update_incident_state({}, first, "collector_sla_degraded")

    assert notification_kind(second, state) == "collector_sla_degraded_changed"


def test_sla_telegram_reports_recovery_and_closes_incident() -> None:
    degraded = report("collector_sla_degraded_no_book_inserts", ["cycle_inserted_books"])
    state = update_incident_state({}, degraded, "collector_sla_degraded")
    recovered = report("collector_sla_healthy")

    kind = notification_kind(recovered, state)
    recovered_state = update_incident_state(state, recovered, kind)
    key = notification_key(kind, recovered, recovered_state)

    assert kind == "collector_sla_recovered"
    assert recovered_state["incident_open"] is False
    assert recovered_state["last_report_degraded"] is False
    assert key.startswith("collector_sla_recovered|1|collector_sla_healthy|")

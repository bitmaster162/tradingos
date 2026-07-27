from __future__ import annotations

from ops.control_panel.control_panel import TASKS, latest_autostart_summary


def test_panel_exposes_funding_freshness_watchdog_as_safe_task() -> None:
    task = TASKS["cex_funding_freshness_watchdog"]

    assert "cex_funding_freshness_watchdog.py" in " ".join(task.command)
    assert "no restart" in task.network_note
    assert "orders" in task.network_note

    alert = TASKS["cex_funding_freshness_incident_alert"]
    drill = TASKS["cex_funding_freshness_incident_alert_drill"]
    assert "cex_funding_freshness_incident_alert.py" in " ".join(alert.command)
    assert "--send-telegram" not in alert.command
    assert "no Telegram request" in alert.network_note
    assert "cex_funding_freshness_incident_alert_drill.py" in " ".join(drill.command)
    assert "no network" in drill.network_note


def test_autostart_summary_exposes_fail_closed_funding_watchdog() -> None:
    summary = latest_autostart_summary()
    watchdog = summary["cex_funding_freshness_watchdog_loop"]

    assert "pid_alive" in watchdog
    assert "decision" in watchdog
    assert "blockers" in watchdog
    assert "incident_alert_decision" in watchdog
    assert "incident_transition_kind" in watchdog
    assert "incident_pending_notifications" in watchdog
    assert watchdog["automatic_restart_allowed"] is False
    assert watchdog["signals_allowed"] is False
    assert watchdog["orders_allowed"] is False
    assert watchdog["can_trade"] is False

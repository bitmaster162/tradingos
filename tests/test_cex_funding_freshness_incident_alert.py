from __future__ import annotations

import json
from pathlib import Path

from tools.cex_funding_freshness_incident_alert import evaluate_transition, failure_signature, health_class, run
from tools.cex_funding_freshness_incident_alert_drill import build_drill


def report(healthy: bool, blockers: list[str] | None = None) -> dict:
    return {
        "decision": "cex_funding_freshness_healthy" if healthy else "cex_funding_freshness_blocked",
        "healthy": healthy,
        "blockers": blockers or [],
        "sources": {
            "aggregate": {"bucket_age_seconds": 20.0},
            "direct": {"bucket_age_seconds": 20.0 if healthy else 240.0},
            "latest_bucket_skew_minutes": 0.0 if healthy else 4.0,
        },
        "can_trade": False,
    }


def evaluate(value: dict, state: dict, index: int = 0):
    return evaluate_transition(value, state, f"2026-07-13T02:{10 + index:02d}:00Z")


def test_first_observation_records_silent_baseline() -> None:
    kind, event, state = evaluate(report(True), {})

    assert kind == "baseline_recorded"
    assert event is None
    assert state["last_health_class"] == "healthy"
    assert state["incident_open"] is False


def test_healthy_to_blocked_opens_one_incident() -> None:
    _, _, state = evaluate(report(True), {})
    kind, event, state = evaluate(report(False, ["direct_source_fresh"]), state, 1)

    assert kind == "funding_freshness_blocked"
    assert event is not None
    assert event["incident_id"] == 1
    assert event["trade_signal"] is False
    assert event["orders_allowed"] is False
    assert state["incident_open"] is True
    assert len(state["pending_notifications"]) == 1


def test_repeated_blocked_state_and_changed_signature_are_suppressed() -> None:
    _, _, state = evaluate(report(True), {})
    _, _, state = evaluate(report(False, ["direct_source_fresh"]), state, 1)
    kind_same, event_same, state = evaluate(report(False, ["direct_source_fresh"]), state, 2)
    kind_changed, event_changed, state = evaluate(report(False, ["pid_alive"]), state, 3)

    assert kind_same == "no_transition"
    assert event_same is None
    assert kind_changed == "no_transition"
    assert event_changed is None
    assert len(state["pending_notifications"]) == 1


def test_blocked_to_healthy_closes_same_incident() -> None:
    _, _, state = evaluate(report(True), {})
    _, blocked, state = evaluate(report(False, ["direct_source_fresh"]), state, 1)
    kind, recovered, state = evaluate(report(True), state, 2)

    assert kind == "funding_freshness_recovered"
    assert blocked is not None and recovered is not None
    assert blocked["incident_id"] == recovered["incident_id"] == 1
    assert state["incident_open"] is False
    assert len(state["pending_notifications"]) == 2


def test_initial_blocked_does_not_create_false_recovery() -> None:
    _, first, state = evaluate(report(False, ["report_missing"]), {})
    kind, recovered, state = evaluate(report(True), state, 1)

    assert first is None
    assert kind == "no_transition"
    assert recovered is None
    assert state["incident_open"] is False


def test_failure_signature_is_order_stable_and_missing_is_blocked() -> None:
    assert failure_signature(report(False, ["b", "a"])) == failure_signature(report(False, ["a", "b"]))
    assert health_class({}) == "blocked"


def test_synthetic_drill_covers_block_repeat_and_recovery_without_send() -> None:
    drill = build_drill()

    assert drill["decision"] == "cex_funding_freshness_incident_alert_drill_passed"
    assert all(drill["checks"].values())
    assert drill["telegram_send_attempted"] is False
    assert drill["can_trade"] is False


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def integration_contract(tmp_path: Path) -> Path:
    path = tmp_path / "contract.json"
    write_json(
        path,
        {
            "lock_id": "integration-test",
            "inputs": {
                "watchdog_report": str(tmp_path / "watchdog.json"),
                "state": str(tmp_path / "state.json"),
                "ledger": str(tmp_path / "ledger.jsonl"),
                "out_prefix": str(tmp_path / "alert"),
            },
            "transition_policy": {
                "maximum_pending_notifications": 20,
                "maximum_recorded_keys": 500,
            },
            "telegram": {
                "token_env": "TELEGRAM_BOT_TOKEN_TEST",
                "chat_id_env": "TELEGRAM_CHAT_ID_TEST",
                "env_files": [],
                "timeout_seconds": 1,
            },
            "runtime_boundary": {"orders_allowed": False, "can_trade": False},
        },
    )
    return path


def test_pending_incident_retries_delivery_without_duplicate_transition(tmp_path: Path, monkeypatch) -> None:
    contract_path = integration_contract(tmp_path)
    watchdog_path = tmp_path / "watchdog.json"
    write_json(watchdog_path, report(True))
    baseline = run(contract_path, send_requested=True, dry_run=False)
    assert baseline["decision"] == "skipped_no_transition"

    write_json(watchdog_path, report(False, ["direct_source_fresh"]))
    monkeypatch.setattr("tools.cex_funding_freshness_incident_alert.env_value", lambda *_args: None)
    blocked = run(contract_path, send_requested=True, dry_run=False)
    assert blocked["transition_kind"] == "funding_freshness_blocked"
    assert blocked["decision"] == "local_transition_pending_missing_telegram_env"
    assert blocked["pending_notifications"] == 1

    monkeypatch.setattr("tools.cex_funding_freshness_incident_alert.env_value", lambda *_args: "configured")
    sends: list[str] = []
    monkeypatch.setattr(
        "tools.cex_funding_freshness_incident_alert.send_telegram",
        lambda _token, _chat, message, _timeout: sends.append(message) or {"ok": True},
    )
    delivered = run(contract_path, send_requested=True, dry_run=False)
    repeated = run(contract_path, send_requested=True, dry_run=False)

    assert delivered["transition_kind"] == "no_transition"
    assert delivered["decision"] == "sent"
    assert delivered["pending_notifications"] == 0
    assert repeated["decision"] == "skipped_no_transition"
    assert len(sends) == 1
    assert len((tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()) == 1

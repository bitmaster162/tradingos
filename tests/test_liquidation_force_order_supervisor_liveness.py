from __future__ import annotations

from types import SimpleNamespace

from tools.liquidation_force_order_supervisor_summary import classify


def test_supervisor_accepts_transport_canary_while_waiting_for_first_event() -> None:
    current = {
        "status": {"pid_alive": True, "age_minutes": 0.1},
        "heartbeat": {"age_minutes": 0.1, "can_trade": False, "status": "transport_liveness_ok"},
        "latest_report": {
            "decision": "force_order_forward_collector_transport_live_no_liquidations_observed",
            "parse_errors_count": 0,
        },
        "data_quality": {"hard_failures": []},
        "event_storage": {"events": 0},
    }
    args = SimpleNamespace(max_status_age_minutes=30.0, max_heartbeat_age_minutes=30.0, min_events_for_research=500)

    decision, reasons, _ = classify(current, args)

    assert decision == "force_order_supervisor_healthy_waiting_events"
    assert reasons == []


def test_supervisor_does_not_use_all_market_count_for_research_readiness() -> None:
    current = {
        "status": {"pid_alive": True, "age_minutes": 0.1},
        "heartbeat": {"age_minutes": 0.1, "can_trade": False, "status": "event_written"},
        "latest_report": {"decision": "force_order_forward_collector_wrote_events", "parse_errors_count": 0},
        "data_quality": {"hard_failures": [], "preregistered_sample_events": 28, "research_universe_events": 28},
        "event_storage": {"events": 1000},
    }
    args = SimpleNamespace(max_status_age_minutes=30.0, max_heartbeat_age_minutes=30.0, min_events_for_research=500)

    decision, reasons, _ = classify(current, args)

    assert decision == "force_order_supervisor_collecting_real_events"
    assert reasons == []


def test_supervisor_preserves_zero_post_lock_count_without_fallback() -> None:
    current = {
        "status": {"pid_alive": True, "age_minutes": 0.1},
        "heartbeat": {"age_minutes": 0.1, "can_trade": False, "status": "event_written"},
        "latest_report": {"decision": "force_order_forward_collector_wrote_events", "parse_errors_count": 0},
        "data_quality": {
            "hard_failures": [],
            "preregistered_sample_events": 0,
            "research_universe_events": 31,
        },
        "event_storage": {"events": 323},
    }
    args = SimpleNamespace(max_status_age_minutes=30.0, max_heartbeat_age_minutes=30.0, min_events_for_research=500)

    decision, reasons, _ = classify(current, args)

    assert decision == "force_order_supervisor_waiting_preregistered_sample"
    assert reasons == []

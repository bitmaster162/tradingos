from __future__ import annotations

from tools.cross_venue_microstructure_snapshot_gate import readiness_checks, readiness_diagnostics


def test_snapshot_gate_waits_until_every_fixed_readiness_gate_passes() -> None:
    policy = {"status": "locked", "required_storage": "sqlite", "required_minimum_hours": 168, "required_dual_trade_coverage_pct": 95, "required_dual_book_coverage_pct": 95, "required_missing_trade_ids": 0}
    report = {"storage": {"engine": "sqlite"}, "classification": "cross_venue_microstructure_ready_for_preregistered_research", "coverage": {"span_hours": 168, "both_trade_coverage_pct": 99, "both_book_coverage_pct": 96}, "trade_id_integrity": {"binance": {"missing_ids": 0}, "coinbase": {"missing_ids": 0}}, "research_readiness": {"ready": True}, "can_trade": False}
    health = {"classification": "cross_venue_microstructure_healthy_research_ready", "can_trade": False}
    sla_replay = {"decision": "collector_sla_replay_stable", "can_trade": False}
    assert all(readiness_checks(report, health, policy, sla_replay).values())
    report["coverage"]["span_hours"] = 167.99
    checks = readiness_checks(report, health, policy, sla_replay)
    assert checks["minimum_hours"] is False


def test_snapshot_gate_reports_minimum_time_eta_and_blockers() -> None:
    policy = {"status": "locked", "required_storage": "sqlite", "required_minimum_hours": 168, "required_dual_trade_coverage_pct": 95, "required_dual_book_coverage_pct": 95, "required_missing_trade_ids": 0}
    report = {
        "generated_at": "2026-06-25T00:00:00+00:00",
        "storage": {"engine": "sqlite"},
        "classification": "cross_venue_microstructure_forward_collecting",
        "coverage": {"span_hours": 6, "both_trade_coverage_pct": 99, "both_book_coverage_pct": 91.5},
        "trade_id_integrity": {"binance": {"missing_ids": 0}, "coinbase": {"missing_ids": 0}},
        "research_readiness": {"ready": False},
        "can_trade": False,
    }
    health = {"classification": "cross_venue_microstructure_healthy_collecting", "can_trade": False}
    sla_replay = {"decision": "collector_sla_replay_stable", "can_trade": False}

    checks = readiness_checks(report, health, policy, sla_replay)
    diagnostics = readiness_diagnostics(report, health, policy, checks, sla_replay)

    assert diagnostics["status"] == "waiting"
    assert diagnostics["primary_blocker"] == "minimum_time_window"
    assert diagnostics["remaining_hours"] == 162
    assert diagnostics["estimated_earliest_time_gate_at_utc"] == "2026-07-01T18:00:00+00:00"
    assert "minimum_hours" in diagnostics["failed_checks"]
    assert "dual_book_coverage" in diagnostics["failed_checks"]
    assert diagnostics["metric_blockers"][0]["gate"] == "minimum_hours"


def test_snapshot_gate_blocks_recent_collector_sla_degradation_before_sealing() -> None:
    policy = {"status": "locked", "required_storage": "sqlite", "required_minimum_hours": 168, "required_dual_trade_coverage_pct": 95, "required_dual_book_coverage_pct": 95, "required_missing_trade_ids": 0}
    report = {"storage": {"engine": "sqlite"}, "classification": "cross_venue_microstructure_ready_for_preregistered_research", "coverage": {"span_hours": 168, "both_trade_coverage_pct": 99, "both_book_coverage_pct": 96}, "trade_id_integrity": {"binance": {"missing_ids": 0}, "coinbase": {"missing_ids": 0}}, "research_readiness": {"ready": True}, "can_trade": False}
    health = {"classification": "cross_venue_microstructure_healthy_research_ready", "can_trade": False}
    sla_replay = {
        "decision": "collector_sla_replay_recent_degradation",
        "incident_count": 1,
        "open_incident": False,
        "state_transitions": 2,
        "degraded_observations": 1,
        "stability_blocker": "recent_degradation_cooldown",
        "latest_degraded_generated_at": "2026-06-25T12:01:00+00:00",
        "stability_cooldown_until_utc": "2026-06-25T18:01:00+00:00",
        "stability_cooldown_remaining_minutes": 351.0,
        "can_trade": False,
    }

    checks = readiness_checks(report, health, policy, sla_replay)
    diagnostics = readiness_diagnostics(report, health, policy, checks, sla_replay)

    assert checks["collector_sla_replay_stable"] is False
    assert diagnostics["primary_blocker"] == "collector_sla_replay_not_stable"
    assert "collector_sla_replay_stable" in diagnostics["failed_checks"]
    assert diagnostics["collector_sla_replay_decision"] == "collector_sla_replay_recent_degradation"
    assert diagnostics["collector_sla_replay_stability_blocker"] == "recent_degradation_cooldown"
    assert diagnostics["collector_sla_replay_cooldown_until_utc"] == "2026-06-25T18:01:00+00:00"

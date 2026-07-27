from __future__ import annotations

import json
from pathlib import Path

from tools.tradingos_core_readiness_edge_report import build_report


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_report_prioritizes_data_readiness_over_telegram(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    write_json(
        docs / "CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json",
        {
            "decision": "waiting_for_microstructure_readiness",
            "checks": {"policy_locked": True, "source_can_trade_false": True},
            "summary": {"failed": ["minimum_hours"]},
            "readiness_diagnostics": {"primary_blocker": "minimum_time_window"},
            "can_trade": False,
        },
    )
    write_json(
        docs / "CROSS_VENUE_MICROSTRUCTURE_COLLECTOR_SLA_REPLAY_2026-06-25.json",
        {"decision": "collector_sla_replay_stable", "can_trade": False},
    )
    write_json(
        docs / "OI_FUNDING_DATA_QUALITY_2026-06-15.json",
        {
            "summary": {"classification": "oi_guard_data_ready"},
            "replay_trade_coverage": {"full_context_coverage_pct": 99.0},
        },
    )
    write_json(
        docs / "ACTIVE_STRATEGY_RUNTIME_MAP_2026-06-22.json",
        {
            "strategies": [{"family": "EDGE_FORWARD_4H", "runtime_status": "observer_running"}],
            "rejected_families": ["TREND_MIX_4H"],
            "decision": "one_observer_running",
            "can_trade": False,
        },
    )
    write_json(
        docs / "STRATEGY_POLYGON_PARALLEL_2026-06-04.json",
        {"polygon_candidate_count": 0, "watchlist_count": 1, "next_action": {"id": "expand_watchlist"}},
    )

    report = build_report(tmp_path)

    assert report["decision"] == "data_readiness_first_not_telegram"
    assert report["telegram_assessment"]["needed_now"] is False
    assert report["next_actions"][0]["id"] == "continue_microstructure_collection_until_gate_passes"
    assert "microstructure:minimum_hours" in report["blockers"]
    assert report["can_trade"] is False


def test_report_moves_to_oos_when_snapshot_and_candidate_are_ready(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    write_json(
        docs / "CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json",
        {
            "decision": "microstructure_snapshot_sealed",
            "checks": {"policy_locked": True, "source_can_trade_false": True},
            "summary": {"failed": []},
            "can_trade": False,
        },
    )
    write_json(
        docs / "CROSS_VENUE_MICROSTRUCTURE_COLLECTOR_SLA_REPLAY_2026-06-25.json",
        {"decision": "collector_sla_replay_stable", "can_trade": False},
    )
    write_json(
        docs / "OI_FUNDING_DATA_QUALITY_2026-06-15.json",
        {
            "summary": {"classification": "oi_guard_data_ready"},
            "replay_trade_coverage": {"full_context_coverage_pct": 100.0},
        },
    )
    write_json(
        docs / "ACTIVE_STRATEGY_RUNTIME_MAP_2026-06-22.json",
        {
            "strategies": [{"family": "EDGE_FORWARD_4H", "runtime_status": "observer_running"}],
            "rejected_families": [],
            "can_trade": False,
        },
    )
    write_json(
        docs / "STRATEGY_POLYGON_PARALLEL_2026-06-04.json",
        {"polygon_candidate_count": 1, "watchlist_count": 0, "next_action": {"id": "isolate_polygon_candidates_for_oos"}},
    )

    report = build_report(tmp_path)

    assert report["decision"] == "ready_for_oos_validation_not_trading"
    assert report["next_actions"][0]["id"] == "isolate_polygon_candidates_for_oos"
    assert report["telegram_assessment"]["priority"] == "low_after_existing_alerting"
    assert report["runtime_boundary"]["orders_allowed"] is False


def test_report_handles_missing_artifacts_without_promoting_trading(tmp_path: Path) -> None:
    report = build_report(tmp_path)

    assert report["decision"] == "data_readiness_first_not_telegram"
    assert report["scoreboard"]["active_observer_count"] == 0
    assert "strategy:no_active_observer" in report["blockers"]
    assert report["runtime_boundary"]["telegram_is_edge"] is False
    assert report["can_trade"] is False


def test_report_does_not_confuse_snapshot_gate_with_telegram_wrapper(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    write_json(
        docs / "CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json",
        {
            "decision": "waiting_for_microstructure_readiness",
            "checks": {"policy_locked": True, "source_can_trade_false": True},
            "summary": {"failed": ["minimum_hours"]},
            "readiness_diagnostics": {"primary_blocker": "minimum_time_window"},
            "can_trade": False,
        },
    )
    write_json(
        docs / "CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_TELEGRAM_2026-06-25.json",
        {
            "decision": "skipped_waiting",
            "checks": {},
            "summary": {"failed": []},
            "can_trade": False,
        },
    )

    report = build_report(tmp_path)
    snapshot_component = next(item for item in report["components"] if item["id"] == "microstructure_snapshot_gate")

    assert snapshot_component["decision"] == "waiting_for_microstructure_readiness"
    assert snapshot_component["path"].endswith("CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json")
    assert "microstructure:minimum_hours" in report["blockers"]


def test_report_surfaces_derivatives_event_validation_failure(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    write_json(
        docs / "DERIVATIVES_EVENT_EDGE_MINER_2026-06-25.json",
        {
            "decision": "reject_validation_gate_failed",
            "summary": {"train_qualified": 7, "validation_qualified": 0},
            "can_trade": False,
        },
    )

    report = build_report(tmp_path)
    component = next(item for item in report["components"] if item["id"] == "derivatives_event_edge_miner")

    assert component["status"] == "blocked"
    assert component["key_metric"] == "train=7 validation=0"
    assert "strategy:derivatives_event_reject_validation_gate_failed" in report["blockers"]
    assert any(item["id"] == "add_regime_filter_to_4h_oi_build_continuation_or_archive" for item in report["next_actions"])


def test_report_does_not_treat_quality_matrix_as_single_quality_report(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    write_json(
        docs / "OI_FUNDING_DATA_QUALITY_2026-06-15.json",
        {
            "summary": {"classification": "oi_guard_data_ready"},
            "replay_trade_coverage": {"full_context_coverage_pct": 99.0},
        },
    )
    write_json(
        docs / "OI_FUNDING_DATA_QUALITY_MATRIX_2026-06-29.json",
        {
            "decision": "oi_funding_quality_ready_for_research",
            "summary": {
                "ready_intervals": 2,
                "degraded_intervals": 0,
                "ready_interval_ids": ["1h", "4h"],
            },
            "can_trade": False,
        },
    )

    report = build_report(tmp_path)
    quality_component = next(item for item in report["components"] if item["id"] == "oi_funding_quality")
    matrix_component = next(item for item in report["components"] if item["id"] == "oi_funding_quality_matrix")

    assert quality_component["decision"] == "oi_guard_data_ready"
    assert quality_component["path"].endswith("OI_FUNDING_DATA_QUALITY_2026-06-15.json")
    assert matrix_component["decision"] == "oi_funding_quality_ready_for_research"


def test_report_surfaces_strategy_frontier_without_trade_permission(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    write_json(
        docs / "STRATEGY_RESEARCH_FRONTIER_MATRIX_2026-06-29.json",
        {
            "decision": "observer_families_waiting_forward_outcomes",
            "summary": {"promotable": 0, "observer_only": 1, "preregistered": 1, "rejected": 12},
            "can_trade": False,
        },
    )

    report = build_report(tmp_path)
    component = next(item for item in report["components"] if item["id"] == "strategy_research_frontier_matrix")

    assert component["status"] == "working"
    assert component["key_metric"] == "promotable=0 observer_only=1 preregistered=1 rejected=12"
    assert "strategy:no_promotable_family_in_frontier" in report["blockers"]


def test_report_blocks_on_microstructure_autopilot_failure(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    write_json(
        docs / "CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json",
        {
            "decision": "waiting_for_microstructure_readiness",
            "checks": {"policy_locked": True, "source_can_trade_false": True},
            "summary": {"failed": ["minimum_hours"]},
            "readiness_diagnostics": {"primary_blocker": "minimum_time_window"},
            "can_trade": False,
        },
    )
    write_json(
        docs / "CROSS_VENUE_MICROSTRUCTURE_AUTOPILOT_AUDIT_2026-06-29.json",
        {
            "decision": "microstructure_autopilot_needs_repair",
            "failed_checks": ["watchdog_loop_fresh_and_safe"],
            "snapshot": {"transition_state": "waiting_for_minimum_time_window", "remaining_hours": 51.6},
            "can_trade": False,
        },
    )

    report = build_report(tmp_path)
    component = next(item for item in report["components"] if item["id"] == "microstructure_autopilot_audit")

    assert component["status"] == "blocked"
    assert "microstructure_autopilot:watchdog_loop_fresh_and_safe" in report["blockers"]
    assert report["next_actions"][0]["id"] == "repair_microstructure_autopilot_before_waiting"
    assert report["scoreboard"]["runtime_checks"]["passed"] < report["scoreboard"]["runtime_checks"]["total"]
    assert report["can_trade"] is False

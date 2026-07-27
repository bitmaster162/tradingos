from __future__ import annotations

import json
from pathlib import Path

from tools.full_system_devil_audit import build_report, render_markdown


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def seed_runtime(root: Path, *, control_panel_tasks: bool = True) -> None:
    write_text(root / "README.md", "# Runtime fixture\n")
    write_json(root / "MANIFEST.json", {"files": []})
    write_text(
        root / "ops/autostart/Repair-TradingOSRuntime.ps1",
        "MaxRepairs WindowMinutes blocked_restart_budget_exhausted repair_timestamps\n",
    )
    if control_panel_tasks:
        control_panel_text = "\n".join(
            [
                "microstructure_autopilot_audit",
                "microstructure_post_seal_auto_run_guard",
                "microstructure_post_snapshot_launch_audit",
                "tradingos_core_readiness_edge_report",
            ]
        )
    else:
        control_panel_text = "legacy_task_only\n"
    write_text(root / "ops/control_panel/control_panel.py", control_panel_text)

    write_json(
        root / "docs/ACTIVE_STRATEGY_RUNTIME_MAP_2026-06-22.json",
        {
            "strategies": [
                {"family": "TREND_MIX_4H", "promotion": "rejected"},
                {"family": "RANGE_REFINED_4H", "promotion": "rejected"},
                {"family": "EDGE_FORWARD_4H", "promotion": "observer"},
                {"family": "CROWD_FADE_1H", "promotion": "rejected"},
            ],
            "can_trade": False,
        },
    )
    write_json(
        root / "docs/FORWARD_RUNTIME_HEALTH_2026-06-16.json",
        {"classification": "forward_runtime_healthy_observing", "gates": [], "can_trade": False},
    )
    write_json(
        root / "docs/STRATEGY_MIX_FORWARD_SCHEDULER_2026-06-08.json",
        {"latest_cycle": {"collector": {"exit_code": 0}}},
    )
    write_json(
        root / "docs/CROWD_FADE_POSITIONING_SHADOW_SCOREBOARD_2026-06-19.json",
        {
            "summary": {"resolved": 0, "expectancy_r": 0.0},
            "raw_unique_signal_events": 0,
            "independent_signal_events": 0,
            "overlap_suppressed_events": 0,
        },
    )
    write_json(root / "docs/CROWD_FADE_POSITIONING_PROMOTION_GATE_2026-06-19.json", {"decision": "blocked"})
    write_json(
        root / "docs/CROWD_FADE_POSITIONING_DIAGNOSTIC_2026-06-19.json",
        {"decision": "ok", "coverage": [{"matched_bars": 10_000, "first_match": "2021-01-01T00:00:00+00:00"}]},
    )
    write_json(
        root / "configs/CROWD_FADE_FORWARD_LOCK.json",
        {"enabled": False, "status": "historically_rejected", "invalidation": {"reason": "fixture"}},
    )
    write_json(
        root / "configs/TREND_MIX_FORWARD_LOCK.json",
        {"family": "TREND_MIX_4H", "enabled": False, "status": "historically_rejected", "boundaries": {"can_trade": False}},
    )
    write_json(
        root / "docs/TREND_MIX_NESTED_HOLDOUT_2026-06-23.json",
        {
            "method": "train_only_grid_selection_then_single_untouched_calendar_oos",
            "selection_frozen_before_oos": True,
            "decision": "reject_oos_gate_failed",
            "can_trade": False,
            "selected_on_train": {"train": {"summary": {}}},
            "oos": {"summary": {}},
            "oos_gate": {},
        },
    )
    write_json(
        root / "docs/FOUR_FAMILY_FORWARD_PORTFOLIO_SCOREBOARD_2026-06-22.json",
        {
            "decision": "ok",
            "families": [{"family": "TREND_MIX_4H"}, {"family": "RANGE_REFINED_4H"}, {"family": "EDGE_FORWARD_4H"}, {"family": "CROWD_FADE_1H"}],
            "runtime_boundary": {"can_trade": False},
            "can_trade": False,
        },
    )
    write_json(
        root / "docs/FORWARD_EVIDENCE_LIFECYCLE_2026-06-23.json",
        {
            "decision": "ok",
            "families": [
                {"family": "TREND_MIX_4H", "state": "rejected"},
                {"family": "RANGE_REFINED_4H", "state": "rejected_historical_invalidation"},
                {"family": "EDGE_FORWARD_4H", "state": "collecting"},
                {"family": "CROWD_FADE_1H", "state": "rejected"},
            ],
            "can_trade": False,
            "boundaries": {"sends_orders": False, "changes_strategy_parameters": False},
        },
    )
    write_json(
        root / "docs/RANGE_EDGE_NESTED_HOLDOUT_2026-06-23.json",
        {
            "method": "train_only_nested_selection_then_untouched_calendar_oos",
            "selection_frozen_before_oos": True,
            "can_trade": False,
            "families": [
                {"family": "RANGE_REFINED_4H", "decision": "reject_oos_gate_failed", "selected_on_train": {"train": {"summary": {}}}, "oos": {"summary": {}}},
                {"family": "EDGE_FORWARD_4H", "decision": "insufficient_oos_evidence_keep_observer_only", "oos": {"summary": {}}, "oos_gate": {}},
            ],
        },
    )
    write_json(
        root / "docs/RUNTIME_BACKUP_RESTORE_DRILL_2026-06-22.json",
        {
            "decision": "runtime_backup_restore_drill_passed",
            "all_hashes_match": True,
            "runtime_boundary": {"restores_into_active_runtime": False},
            "sampled_files": 1,
        },
    )
    write_json(root / "logs/runtime_backup/daily_drive_backup_last_run.json", {"status": "completed", "ts": "2026-06-29T00:00:00Z"})
    write_json(root / "logs/runtime_safe_repair_last_run.json", {"decision": "not_needed_or_nonrepairable", "max_repairs_in_window": 3, "window_minutes": 60})
    write_json(
        root / "docs/TRADINGOS_CORE_READINESS_EDGE_REPORT_2026-06-29.json",
        {
            "decision": "data_readiness_first_not_telegram",
            "scoreboard": {
                "runtime_checks": {"passed": 4, "total": 4},
                "data_checks": {"passed": 4, "total": 5},
                "strategy_checks": {"passed": 3, "total": 3},
            },
            "blockers": [],
            "can_trade": False,
        },
    )
    write_json(root / "docs/CROSS_VENUE_MICROSTRUCTURE_AUTOPILOT_AUDIT_2026-06-29.json", {"decision": "autopilot_clean", "failed_checks": [], "can_trade": False})
    write_json(
        root / "docs/CROSS_VENUE_MICROSTRUCTURE_POST_SEAL_AUTO_RUN_GUARD_2026-06-29.json",
        {"decision": "post_seal_auto_run_guard_armed_waiting_for_snapshot", "failed_checks": [], "can_trade": False},
    )
    write_json(
        root / "docs/CROSS_VENUE_MICROSTRUCTURE_POST_SNAPSHOT_LAUNCH_AUDIT_2026-06-29.json",
        {"decision": "post_snapshot_launch_ready_waiting_for_snapshot", "failed_checks": [], "can_trade": False},
    )
    write_json(
        root / "docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-29.json",
        {"decision": "waiting_for_microstructure_readiness", "summary": {"failed": ["minimum_hours"]}, "can_trade": False},
    )
    write_json(
        root / "docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_TRANSITION_MONITOR_2026-06-29.json",
        {"transition_state": "waiting_for_minimum_time_window", "remaining_hours": 50.0, "can_trade": False},
    )
    write_json(
        root / "docs/CROSS_VENUE_MICROSTRUCTURE_HEALTH_2026-06-29.json",
        {"classification": "cross_venue_microstructure_healthy_collecting", "failed_hard_gates": [], "can_trade": False},
    )
    write_json(
        root / "docs/CROSS_VENUE_MICROSTRUCTURE_DATA_QUALITY_2026-06-29.json",
        {"research_readiness": {"ready": False}, "can_trade": False},
    )
    write_json(
        root / "docs/OI_FUNDING_DATA_QUALITY_MATRIX_2026-06-29.json",
        {"decision": "oi_funding_quality_ready_for_research", "summary": {"ready_intervals": 2, "degraded_intervals": 0}, "can_trade": False},
    )
    write_json(
        root / "docs/STRATEGY_RESEARCH_FRONTIER_MATRIX_2026-06-29.json",
        {"decision": "observer_families_waiting_forward_outcomes", "summary": {"promotable": 0, "observer_only": 1}, "can_trade": False},
    )
    write_json(
        root / "docs/DERIVATIVES_EVENT_RESEARCH_MATRIX_2026-06-29.json",
        {"decision": "no_promotable_candidate", "summary": {"promotable": 0}, "can_trade": False},
    )
    write_json(
        root / "docs/CONTEXT_EVIDENCE_MATRIX_2026-06-29.json",
        {"decision": "no_ready_context_filter", "summary": {"ready_for_integration": 0}, "can_trade": False},
    )
    write_json(
        root / "docs/DERIVATIVES_EVENT_RUNTIME_DRIFT_AUDIT_2026-06-29.json",
        {"decision": "source_runtime_in_sync", "data_drift_count": 0, "report_drift_count": 0, "can_trade": False},
    )


def finding_ids(report: dict) -> set[str]:
    return {str(item.get("id")) for item in report["findings"]}


def test_devil_audit_surfaces_current_microstructure_wait_state(tmp_path: Path) -> None:
    seed_runtime(tmp_path)

    report = build_report(active_root=tmp_path, source_root=tmp_path)
    md = render_markdown(report)

    assert report["decision"] == "operational_runtime_healthy_but_edge_unproven"
    assert report["source_runtime_parity"]["passed"] is True
    assert report["microstructure"]["autopilot_failed_checks"] == []
    assert report["hardening_proof"]["microstructure_autopilot_clean"] is True
    assert report["hardening_proof"]["post_seal_auto_run_guard_ready"] is True
    assert "microstructure_snapshot_not_sealed" in finding_ids(report)
    assert "## Microstructure / Research Frontier" in md
    assert report["can_trade"] is False


def test_devil_audit_blocks_broken_autopilot_and_post_snapshot_chain(tmp_path: Path) -> None:
    seed_runtime(tmp_path)
    write_json(
        tmp_path / "docs/CROSS_VENUE_MICROSTRUCTURE_AUTOPILOT_AUDIT_2026-06-30.json",
        {"decision": "autopilot_needs_repair", "failed_checks": ["collector_watchdog"], "can_trade": False},
    )
    write_json(
        tmp_path / "docs/CROSS_VENUE_MICROSTRUCTURE_POST_SNAPSHOT_LAUNCH_AUDIT_2026-06-30.json",
        {"decision": "post_snapshot_launch_needs_repair", "failed_checks": ["runner_contract"], "can_trade": False},
    )

    report = build_report(active_root=tmp_path, source_root=tmp_path)
    ids = finding_ids(report)

    assert "microstructure_autopilot_failed" in ids
    assert "post_snapshot_launch_not_ready" in ids
    assert report["hardening_proof"]["microstructure_autopilot_clean"] is False
    assert report["hardening_proof"]["post_snapshot_launch_ready"] is False
    assert report["can_trade"] is False


def test_devil_audit_requires_control_panel_safety_tasks(tmp_path: Path) -> None:
    seed_runtime(tmp_path, control_panel_tasks=False)

    report = build_report(active_root=tmp_path, source_root=tmp_path)

    assert "control_panel_task_registry_incomplete" in finding_ids(report)
    assert report["microstructure"]["control_panel_tasks_present"] is False
    assert report["hardening_proof"]["control_panel_current_safety_tasks_present"] is False

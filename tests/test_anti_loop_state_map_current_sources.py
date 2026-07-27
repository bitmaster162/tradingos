from __future__ import annotations

import json
import os
from pathlib import Path

from tools.anti_loop_state_map import build_hermes_prompt, build_report


def write_json(path: Path, payload: dict, mtime: float) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_state_map_prefers_latest_liquidation_truth_and_clean_prompt(tmp_path: Path) -> None:
    write_json(
        tmp_path / "LIQUIDATION_FORCE_ORDER_DATA_QUALITY_2026-06-30_LOOP_STARTED.json",
        {"decision": "collector_alive_no_events", "events": {"events": 0}, "can_trade": False},
        1.0,
    )
    write_json(
        tmp_path / "LIQUIDATION_FORCE_ORDER_DATA_QUALITY_CURRENT.json",
        {
            "decision": "liquidation_force_order_collecting_insufficient_sample",
            "events": {"events": 42, "preregistered_sample": {"events": 13}},
            "hard_failures": [],
            "can_trade": False,
        },
        2.0,
    )
    write_json(
        tmp_path / "CROSS_VENUE_MICROSTRUCTURE_READINESS_PROGRESS_2026-06-30.json",
        {"decision": "stale_microstructure_forward_collecting", "span_hours": 141.6, "book_coverage_pct": 95.5, "can_trade": False},
        2.0,
    )
    write_json(
        tmp_path / "MICROSTRUCTURE_UNBLOCK_STATUS_2026-07-03.json",
        {
            "decision": "microstructure_wait_for_book_coverage",
            "snapshot_id": None,
            "coverage": {"span_hours": 168.0, "book_coverage_pct": 64.5},
            "book_diagnostic": {"recent_6h_dual_book_pct": 100.0, "eta_utc": "2026-07-15T05:36:00Z"},
            "can_trade": False,
        },
        3.0,
    )
    write_json(
        tmp_path / "LIQUIDATION_FORCE_ORDER_PREREGISTERED_PROGRESS_2026-07-12.json",
        {
            "decision": "force_order_preregistered_progress_collecting",
            "ready_for_pipeline": False,
            "sample": {
                "events": 42,
                "independent_4h_blocks": 6,
                "matured_independent_4h_blocks": 3,
            },
            "velocity": {"theoretical_earliest_pipeline_at": "2026-07-15T20:00:00Z"},
            "can_trade": False,
        },
        2.0,
    )
    write_json(
        tmp_path / "LIQUIDATION_BOOK_REPLENISHMENT_INDEPENDENCE_GATE_2026-07-12.json",
        {
            "decision": "liquidation_book_replenishment_independence_gate_collecting_base_sample",
            "evidence": {"candidate_events": 0, "independent_blocks": 0},
            "blockers": ["base_statistical_gate_not_passed"],
            "can_trade": False,
        },
        2.0,
    )
    write_json(
        tmp_path / "EXOGENOUS_LIQUIDITY_REGIME_FORWARD_OBSERVER_2026-07-12.json",
        {
            "decision": "exogenous_liquidity_regime_waiting_first_new_macro_date",
            "sample": {"events_total": 0},
            "source_readiness": {
                "stablecoin": {"metrics": {"new_unique_source_dates": 1}},
                "macro": {"metrics": {"new_unique_weekly_dates": 0}},
            },
            "blockers": ["no_post_floor_macro_proxy_date"],
            "can_trade": False,
        },
        2.0,
    )
    write_json(
        tmp_path / "REAL_EDGE_OBSERVER_PULSE_2026-07-03.json",
        {"decision": "real_edge_observer_pulse_observing_no_trade", "failed_steps": [], "can_trade": False},
        2.0,
    )
    write_json(
        tmp_path / "BYBIT_LIQUIDATION_CANONICAL_V3_CLOCK_TOMBSTONE_2026-07-14.json",
        {
            "decision": "bybit_canonical_v3_terminal_data_quality_tombstone_clock_domain_mismatch",
            "evidence": {"post_floor_events": 979, "post_floor_negative_raw_receipt_lag_pct": 30.133},
            "outcome_review": {"outcome_fields_computed": False},
            "can_trade": False,
        },
        2.0,
    )
    write_json(
        tmp_path / "BYBIT_LIQUIDATION_CANONICAL_FORWARD_OBSERVER_V5R2_2026-07-18.json",
        {
            "decision": "bybit_liquidation_canonical_v5_waiting_floor",
            "lock": {"forward_start_at": "2026-07-14T00:00:00Z"},
            "sample": {"resolved_events": 0, "independent_4h_blocks": 0},
            "outcome_review": {"interim_outcomes_hidden": True, "outcome_fields_computed": False},
            "can_trade": False,
        },
        3.0,
    )
    write_json(
        tmp_path / "BYBIT_LIQUIDATION_CANONICAL_V4_COMMISSIONING_2026-07-14.json",
        {
            "decision": "bybit_canonical_v4_commissioning_pass",
            "commissioning_window": {"schema3_rows": 15, "collector_sessions": 4, "unique_packets": 15},
            "hard_failures": [],
            "runtime_boundary": {"sample_admission_allowed": False, "outcome_fields_computed": False},
            "can_trade": False,
        },
        3.0,
    )
    write_json(
        tmp_path / "CEX_DEX_FUNDING_LEAD_LAG_DATA_QUALITY_2026-07-13.json",
        {
            "decision": "cex_dex_funding_snapshot_healthy_appended",
            "sample": {
                "unique_minute_buckets": 3,
                "span_minutes": 5.0,
                "required_point_coverage": 0.88888889,
            },
            "research_gate": {"minimum_unique_minute_snapshots": 10000},
            "can_trade": False,
        },
        2.0,
    )
    write_json(
        tmp_path / "CEX_FUNDING_DIRECT_REPLICATION_DATA_QUALITY_2026-07-13.json",
        {
            "decision": "direct_cex_funding_replication_snapshot_healthy_appended",
            "sample": {
                "unique_minute_buckets": 2,
                "span_minutes": 1.0,
                "required_point_coverage": 1.0,
            },
            "can_trade": False,
        },
        2.0,
    )
    write_json(
        tmp_path / "CEX_FUNDING_SOURCE_ALIGNMENT_V3_2026-07-14.json",
        {
            "decision": "cex_funding_source_alignment_collecting",
            "sample": {
                "matching_minute_buckets": 2,
                "valid_comparisons": 12,
                "comparison_coverage": 1.0,
            },
            "blockers": ["minimum_matching_minute_buckets"],
            "edge_evaluated": False,
            "can_trade": False,
        },
        2.0,
    )
    write_json(
        tmp_path / "CEX_FUNDING_RESEARCH_READINESS_2026-07-13.json",
        {
            "decision": "cex_funding_research_readiness_waiting_forward_gate",
            "primary_progress": {
                "current": {"span_days": 0.00347222, "unique_minute_snapshots": 3},
                "theoretical_earliest_utc": "2026-07-27T00:31:00Z",
            },
            "direct_progress": {"current": {"span_days": 0.00069444, "unique_minute_snapshots": 2}},
            "alignment": {"terminal": False},
            "stages": {
                "primary_observer_creation_gate_ready": False,
                "observer_creation_review_allowed": False,
                "edge_evaluated": False,
            },
            "contract_failures": [],
            "operational_blockers": [],
            "can_trade": False,
        },
        2.0,
    )
    write_json(
        tmp_path / "CEX_FUNDING_FRESHNESS_WATCHDOG_2026-07-13.json",
        {
            "decision": "cex_funding_freshness_healthy",
            "healthy": True,
            "blockers": [],
            "automatic_restart_attempted": False,
            "edge_evaluated": False,
            "can_trade": False,
        },
        2.0,
    )
    write_json(
        tmp_path / "CEX_FUNDING_FRESHNESS_INCIDENT_ALERT_2026-07-13.json",
        {
            "decision": "skipped_no_transition",
            "transition_kind": "no_transition",
            "pending_notifications": 0,
            "telegram_response_ok": None,
            "trade_signal": False,
            "can_trade": False,
        },
        2.0,
    )
    write_json(
        tmp_path / "CEX_FUNDING_FRESHNESS_INCIDENT_ALERT_DRILL_2026-07-13.json",
        {"decision": "synthetic_drill_must_not_replace_live_alert", "can_trade": False},
        3.0,
    )
    write_json(
        tmp_path / "DERIBIT_OPTIONS_RESEARCH_RUNTIME_AUDIT_2026-07-13.json",
        {
            "decision": "deribit_options_stack_forward_collecting_readiness",
            "runtime": {"all_components_passed": True},
            "forward_progress": {
                "readiness_gate_ready": False,
                "span_days": 1.5,
                "healthy_slots": 377,
                "scheduled_coverage": 0.847,
                "events_total": 0,
            },
            "can_trade": False,
        },
        3.0,
    )
    write_json(
        tmp_path / "ACTIVE_OBSERVER_RUNTIME_COVERAGE_2026-07-13.json",
        {
            "decision": "active_observer_runtime_coverage_pass",
            "summary": {
                "active_observer_families": 7,
                "covered_families": 7,
                "blocked_families": 0,
                "known_owner_families": 7,
            },
            "blockers": [],
            "can_trade": False,
        },
        3.0,
    )
    write_json(
        tmp_path / "BITUNIX_WO104_STATUS_2026-07-13.json",
        {
            "decision": "bitunix_wo104_bounded_public_capture_collecting",
            "phase": "collecting",
            "replay": {"frames_total": 12091, "canonical_replay_status": "REPLAY_PENDING"},
            "promotion": "HOLD",
            "can_trade": False,
        },
        3.0,
    )
    write_json(
        tmp_path / "BITUNIX_WO105_STATUS_2026-07-14.json",
        {
            "decision": "bitunix_wo105_causal_shadow_ready_waiting_forward_events",
            "canonical_replay": "PASS",
            "causal_shadow_evaluator": "READY",
            "forward_progress": "0/30",
            "edge_evaluated": False,
            "can_trade": False,
        },
        3.0,
    )
    write_json(
        tmp_path / "BITUNIX_WO105_V1_PRE_FLOOR_UNIT_TOMBSTONE_2026-07-14.json",
        {
            "status": "TOMBSTONED_PRE_FLOOR_UNIT_CONTRACT_GAP",
            "events_observed": 0,
            "outcomes_observed": 0,
            "superseded_by": "SETUP_A_SFP_FAILED_BREAKOUT_WO105_V2_20260714",
            "can_trade": False,
        },
        3.0,
    )
    write_json(
        tmp_path / "BITUNIX_WO105_V2_PRE_FLOOR_RUNTIME_TOMBSTONE_2026-07-14.json",
        {
            "status": "TOMBSTONED_PRE_FLOOR_CAUSAL_LIFECYCLE_GAP",
            "events_observed": 0,
            "outcomes_observed": 0,
            "superseded_by": "SETUP_A_SFP_FAILED_BREAKOUT_WO105_V3_20260714",
            "can_trade": False,
        },
        3.0,
    )
    write_json(
        tmp_path / "BITUNIX_WO105_V3R1_CLOCK_CONTRACT_TOMBSTONE_2026-07-14.json",
        {
            "status": "TOMBSTONED_POST_FLOOR_ZERO_EVENT_CAUSAL_CLOCK_CONTRACT_FAILURE",
            "events_observed": 0,
            "outcomes_observed": 0,
            "clock_contract_failure": True,
            "strategy_failure": False,
            "can_trade": False,
        },
        3.0,
    )
    write_json(
        tmp_path / "BITUNIX_WO105_V3R2_FIRST_CYCLE_OPERATIONAL_TOMBSTONE_2026-07-14.json",
        {
            "status": "TOMBSTONED_POST_FLOOR_ZERO_EVENT_ADAPTER_INTERFACE_FAILURE",
            "events_observed": 0,
            "outcomes_observed": 0,
            "failure_class": "adapter_interface_missing_load_rows",
            "strategy_failure": False,
            "can_trade": False,
        },
        3.0,
    )
    write_json(
        tmp_path / "BITUNIX_WO105_V3R3_RECEIPT_ORDER_TOMBSTONE_2026-07-15.json",
        {
            "status": "TOMBSTONED_POST_FLOOR_ZERO_EVENT_RECEIPT_ORDER_FAILURE",
            "candidate_setups_observed": 1,
            "events_admitted": 0,
            "outcomes_observed": 0,
            "failure_class": "candle_receipt_order_contradicted_event_order",
            "strategy_failure": False,
            "can_trade": False,
        },
        3.0,
    )
    write_json(
        tmp_path / "BITUNIX_WO105_V3R4_STATUS_2026-07-15.json",
        {
            "decision": "bitunix_wo105_v3r4_ready_waiting_forward_floor",
            "phase": "WAITING_FORWARD_FLOOR",
            "forward_start_at": "2026-07-15T04:00:00Z",
            "source_pipeline": {
                "crowd_quorum_required": 3,
                "sources": [
                    "bitunix_funding",
                    "bitunix_trade_cvd",
                    "binance_force_order_liquidation_skew",
                ],
            },
            "forward_events": 0,
            "minimum_forward_events": 30,
            "forward_progress": "0/30",
            "terminal_forward_events": 0,
            "minimum_terminal_forward_events": 30,
            "terminal_forward_progress": "0/30",
            "edge_evaluated": False,
            "can_trade": False,
        },
        3.0,
    )
    write_json(
        tmp_path / "BITUNIX_WO105_V3R4_BLIND_REVIEW_GATE_2026-07-15.json",
        {
            "decision": "bitunix_wo105_v3r4_blind_review_gate_waiting_forward_floor",
            "terminal_forward_progress": "0/30",
            "independent_review_package_allowed": False,
            "edge_evaluated": False,
            "can_trade": False,
        },
        3.0,
    )
    write_json(
        tmp_path / "BITUNIX_WO105_V3R4_FIRST_CYCLE_GATE_2026-07-15.json",
        {
            "decision": "bitunix_wo105_v3r4_first_cycle_waiting_forward_floor",
            "overdue": [],
            "edge_evaluated": False,
            "can_trade": False,
        },
        3.0,
    )
    write_json(
        tmp_path / "STRATEGY_RESEARCH_FRONTIER_MATRIX_CURRENT.json",
        {
            "decision": "observer_families_waiting_forward_outcomes",
            "summary": {"families": 36, "observer_only": 8, "promotable": 0, "rejected": 24},
            "families": [],
            "can_trade": False,
        },
        2.0,
    )

    report = build_report(tmp_path)
    liquidation_row = next(item for item in report["built_running"] if item["name"] == "liquidation_force_order_collector")
    assert "events=42" in liquidation_row["why"]
    assert "preregistered=13" in liquidation_row["why"]
    assert "liquidation_force_order_locked_sample_waiting_gates" in report["current_blockers"]
    assert "exogenous_liquidity_waiting_forward_macro_dates" in report["current_blockers"]
    assert "cex_funding_research_readiness_waiting_forward_gate" in report["current_blockers"]
    assert all("has no real events yet" not in item for item in report["not_done"])
    progress_row = next(
        item for item in report["built_running"] if item["name"] == "liquidation_force_order_preregistered_progress"
    )
    assert "matured_blocks=3" in progress_row["why"]
    assert "2026-07-15T20:00:00Z" in progress_row["why"]
    micro_row = next(item for item in report["built_running"] if item["name"] == "microstructure_collector")
    assert micro_row["status"] == "microstructure_wait_for_book_coverage"
    assert "book_coverage=64.5" in micro_row["why"]
    assert "recent_6h_book_coverage=100.0" in micro_row["why"]
    assert "2026-07-15T05:36:00Z" in micro_row["why"]
    assert "95.5" not in micro_row["why"]
    funding_row = next(item for item in report["built_running"] if item["name"] == "cex_dex_funding_lead_lag_collector")
    assert "snapshots=3" in funding_row["why"]
    assert "observer_not_built=true" in funding_row["why"]
    direct_row = next(item for item in report["built_running"] if item["name"] == "cex_funding_direct_replication_collector")
    assert "snapshots=2" in direct_row["why"]
    assert "evaluator_not_built=true" in direct_row["why"]
    alignment_row = next(item for item in report["built_running"] if item["name"] == "cex_funding_source_alignment_monitor")
    assert "matching_buckets=2" in alignment_row["why"]
    assert "edge_evaluated=False" in alignment_row["why"]
    readiness_row = next(item for item in report["built_running"] if item["name"] == "cex_funding_research_readiness_monitor")
    assert readiness_row["status"] == "cex_funding_research_readiness_waiting_forward_gate"
    assert "2026-07-27T00:31:00Z" in readiness_row["why"]
    assert "observer_review=False" in readiness_row["why"]
    watchdog_row = next(item for item in report["built_running"] if item["name"] == "cex_funding_freshness_watchdog")
    assert "healthy=True" in watchdog_row["why"]
    assert "automatic_restart=False" in watchdog_row["why"]
    assert "cex_funding_freshness_watchdog_blocked" not in report["current_blockers"]
    alert_row = next(item for item in report["built_running"] if item["name"] == "cex_funding_freshness_transition_alert")
    assert "transition=no_transition" in alert_row["why"]
    assert "trade_signal=False" in alert_row["why"]
    deribit_row = next(item for item in report["built_running"] if item["name"] == "deribit_options_skew_forward_stack")
    assert "runtime_ok=True" in deribit_row["why"]
    assert "span_days=1.5/7.0" in deribit_row["why"]
    assert "healthy_slots=377/1800" in deribit_row["why"]
    assert "events=0" in deribit_row["why"]
    assert "deribit_options_waiting_locked_readiness_gate" in report["current_blockers"]
    coverage_row = next(item for item in report["built_running"] if item["name"] == "active_observer_runtime_coverage")
    assert coverage_row["status"] == "active_observer_runtime_coverage_pass"
    assert "covered=7/7" in coverage_row["why"]
    assert "blocked=0" in coverage_row["why"]
    assert "active_observer_runtime_coverage_report_missing" not in report["current_blockers"]
    assert "active_observer_runtime_coverage_blocked" not in report["current_blockers"]
    bitunix_row = next(item for item in report["built_running"] if item["name"] == "bitunix_wo104_parallel_lane")
    assert bitunix_row["status"] == "bitunix_wo104_bounded_public_capture_collecting"
    assert "replay_frames=12091" in bitunix_row["why"]
    assert "promotion=HOLD" in bitunix_row["why"]
    v1_tombstone_row = next(
        item for item in report["built_running"]
        if item["name"] == "bitunix_wo105_v1_pre_floor_unit_tombstone"
    )
    assert v1_tombstone_row["status"] == "TOMBSTONED_PRE_FLOOR_UNIT_CONTRACT_GAP"
    assert "events=0" in v1_tombstone_row["why"]
    assert "resume=false" in v1_tombstone_row["why"]
    v2_tombstone_row = next(
        item for item in report["built_running"]
        if item["name"] == "bitunix_wo105_v2_pre_floor_runtime_tombstone"
    )
    assert v2_tombstone_row["status"] == "TOMBSTONED_PRE_FLOOR_CAUSAL_LIFECYCLE_GAP"
    assert "events=0" in v2_tombstone_row["why"]
    assert "resume=false" in v2_tombstone_row["why"]
    v3r1_tombstone_row = next(
        item for item in report["built_running"]
        if item["name"] == "bitunix_wo105_v3r1_clock_contract_tombstone"
    )
    assert v3r1_tombstone_row["status"] == "TOMBSTONED_POST_FLOOR_ZERO_EVENT_CAUSAL_CLOCK_CONTRACT_FAILURE"
    assert "clock_contract_failure=True" in v3r1_tombstone_row["why"]
    assert "strategy_failure=False" in v3r1_tombstone_row["why"]
    v3r2_tombstone_row = next(
        item for item in report["built_running"]
        if item["name"] == "bitunix_wo105_v3r2_adapter_interface_tombstone"
    )
    assert v3r2_tombstone_row["status"] == "TOMBSTONED_POST_FLOOR_ZERO_EVENT_ADAPTER_INTERFACE_FAILURE"
    assert "failure_class=adapter_interface_missing_load_rows" in v3r2_tombstone_row["why"]
    assert "strategy_failure=False" in v3r2_tombstone_row["why"]
    wo105_v3r3_row = next(
        item for item in report["built_running"]
        if item["name"] == "bitunix_wo105_v3r3_receipt_order_tombstone"
    )
    assert wo105_v3r3_row["status"] == "TOMBSTONED_POST_FLOOR_ZERO_EVENT_RECEIPT_ORDER_FAILURE"
    assert "candidate_setups=1" in wo105_v3r3_row["why"]
    assert "failure_class=candle_receipt_order_contradicted_event_order" in wo105_v3r3_row["why"]
    assert "resume=false" in wo105_v3r3_row["why"]
    wo105_v3r4_row = next(
        item for item in report["built_running"]
        if item["name"] == "bitunix_wo105_v3r4_causal_shadow"
    )
    assert wo105_v3r4_row["status"] == "bitunix_wo105_v3r4_ready_waiting_forward_floor"
    assert "sources=bitunix_funding,bitunix_trade_cvd,binance_force_order_liquidation_skew" in wo105_v3r4_row["why"]
    assert "quorum=3" in wo105_v3r4_row["why"]
    assert "terminal=0/30" in wo105_v3r4_row["why"]
    assert "blind_gate=bitunix_wo105_v3r4_blind_review_gate_waiting_forward_floor" in wo105_v3r4_row["why"]
    assert "first_cycle=bitunix_wo105_v3r4_first_cycle_waiting_forward_floor" in wo105_v3r4_row["why"]
    assert "bitunix_wo105_v3r4_causal_shadow_waiting_forward_sample" in report["current_blockers"]
    v3_row = next(
        item for item in report["built_running"]
        if item["name"] == "bybit_liquidation_canonical_reversal_v3_clock_tombstone"
    )
    assert "negative_raw_lag_pct=30.133" in v3_row["why"]
    assert "outcomes_computed=false" in v3_row["why"]
    v5_row = next(
        item for item in report["built_running"]
        if item["name"] == "bybit_liquidation_canonical_reversal_v5r1_observer"
    )
    assert "floor=2026-07-14T00:00:00Z" in v5_row["why"]
    assert "calibrated_receipts=true" in v5_row["why"]
    assert "packet_ordinals=true" in v5_row["why"]
    assert "bybit_canonical_liquidation_reversal_waiting_forward_sample" in report["current_blockers"]
    commissioning_row = next(
        item for item in report["built_running"]
        if item["name"] == "bybit_liquidation_canonical_v4_pre_floor_commissioning"
    )
    assert "schema3_rows=15" in commissioning_row["why"]
    assert "one_time_pre_floor_only=true" in commissioning_row["why"]

    prompt = build_hermes_prompt(report)
    assert "Hermes Prompt: Trading Bot Anti-Loop Runtime State Audit" in prompt
    assert "Ð" not in prompt
    assert "Do not rebuild an existing component under a new name" in prompt


def test_state_map_fails_closed_without_runtime_coverage_report(tmp_path: Path) -> None:
    report = build_report(tmp_path)

    assert "active_observer_runtime_coverage_report_missing" in report["current_blockers"]
    coverage_row = next(item for item in report["built_running"] if item["name"] == "active_observer_runtime_coverage")
    assert coverage_row["status"] == "missing"


def test_state_map_blocks_ready_force_order_sample_when_transport_is_degraded(tmp_path: Path) -> None:
    write_json(
        tmp_path / "LIQUIDATION_FORCE_ORDER_PREREGISTERED_PROGRESS_2026-07-12.json",
        {
            "decision": "force_order_preregistered_progress_ready_for_pipeline",
            "ready_for_pipeline": True,
            "sample": {"events": 6000, "independent_4h_blocks": 23, "matured_independent_4h_blocks": 22},
            "can_trade": False,
        },
        2.0,
    )
    write_json(
        tmp_path / "LIQUIDATION_FORCE_ORDER_TRANSPORT_CONTINUITY_2026-07-15.json",
        {
            "decision": "force_order_transport_continuity_degraded_gaps",
            "continuity_observed": False,
            "blockers": ["liveness_gaps_over_threshold"],
            "can_trade": False,
        },
        3.0,
    )

    report = build_report(tmp_path)

    assert "liquidation_force_order_transport_continuity_not_observed" in report["current_blockers"]
    row = next(
        item
        for item in report["built_running"]
        if item["name"] == "liquidation_force_order_transport_continuity"
    )
    assert row["status"] == "force_order_transport_continuity_degraded_gaps"
    assert "continuity_observed=False" in row["why"]
    assert report["can_trade"] is False


def test_state_map_prefers_current_v4_cross_venue_leadership(tmp_path: Path) -> None:
    write_json(
        tmp_path / "LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_FORWARD_OBSERVER_V3_2026-07-13.json",
        {
            "decision": "liquidation_cross_venue_paired_leadership_collecting_forward_sample",
            "primary_sample": {"matched_pairs": 12},
            "can_trade": False,
        },
        1.0,
    )
    v4_name = "LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_FORWARD_OBSERVER_V4_2026-07-15.json"
    write_json(
        tmp_path / v4_name,
        {
            "decision": "liquidation_cross_venue_paired_leadership_collecting_forward_sample",
            "lock": {"forward_start_at": "2026-07-15T08:00:00Z"},
            "primary_sample": {"matched_pairs": 57},
            "windows_seconds": {
                "5": {
                    "leader": {
                        "binance": {"leader_share": 0.42105263},
                        "bybit": {"leader_share": 0.57894737},
                    }
                }
            },
            "terminal": {"reached": False},
            "blockers": ["minimum_primary_window_pairs_not_met"],
            "can_trade": False,
        },
        2.0,
    )

    report = build_report(tmp_path)

    source = report["source_of_truth"]["liquidation_cross_venue_receipt_leadership"]
    row = next(
        item
        for item in report["built_running"]
        if item["name"] == "liquidation_cross_venue_receipt_leadership_observer"
    )
    assert source.endswith(v4_name)
    assert "primary_pairs=57" in row["why"]
    assert "liquidation_cross_venue_receipt_leadership_waiting_forward_sample" in report["current_blockers"]
    assert report["can_trade"] is False


def test_state_map_treats_terminal_funding_alignment_as_terminal_not_waiting(tmp_path: Path) -> None:
    write_json(
        tmp_path / "CEX_FUNDING_RESEARCH_READINESS_2026-07-13.json",
        {
            "decision": "cex_funding_research_readiness_blocked_alignment_terminal",
            "alignment": {
                "decision": "cex_funding_source_alignment_terminal_data_quality_failure",
                "terminal": True,
                "blockers": ["minimum_matching_time_coverage"],
            },
            "stages": {
                "primary_observer_creation_gate_ready": False,
                "observer_creation_review_allowed": False,
                "edge_evaluated": False,
            },
            "contract_failures": [],
            "operational_blockers": [],
            "can_trade": False,
        },
        1.0,
    )

    report = build_report(tmp_path)

    readiness = next(
        item for item in report["partial_waiting"] if item["name"] == "cex_funding_research_readiness_monitor"
    )
    assert "cex_funding_source_alignment_terminal_failure" in report["current_blockers"]
    assert "cex_funding_research_readiness_waiting_forward_gate" not in report["current_blockers"]
    assert readiness["blocker"] == "alignment_terminal_failure"
    assert any("parameter-identical future-floor successor" in item for item in report["next_actions"])
    assert any("failed alignment lock" in item for item in report["not_done"])
    assert report["can_trade"] is False


def test_state_map_uses_funding_successor_admission_instead_of_reopening_terminal_lock(tmp_path: Path) -> None:
    write_json(
        tmp_path / "CEX_FUNDING_RESEARCH_READINESS_2026-07-13.json",
        {
            "decision": "cex_funding_research_readiness_blocked_alignment_terminal",
            "alignment": {"terminal": True, "blockers": ["maximum_consecutive_gap_minutes"]},
            "stages": {"primary_observer_creation_gate_ready": False},
            "contract_failures": [],
            "operational_blockers": [],
            "can_trade": False,
        },
        1.0,
    )
    write_json(
        tmp_path / "CEX_FUNDING_SUCCESSOR_ADMISSION_2026-07-16.json",
        {
            "decision": "cex_funding_successor_admission_waiting_clean_window",
            "eligible_for_manual_successor_lock_review": False,
            "diagnostic_window": {"earliest_recheck_at_utc": "2026-07-17T05:24:00Z"},
            "rolling_alignment": {
                "sample": {
                    "matching_minute_buckets": 765,
                    "matching_time_coverage": 0.53,
                    "maximum_consecutive_gap_minutes": 667,
                },
                "blockers": ["minimum_matching_minute_buckets"],
            },
            "can_trade": False,
        },
        2.0,
    )

    report = build_report(tmp_path)

    assert "cex_funding_successor_admission_waiting_clean_window" in report["current_blockers"]
    assert "cex_funding_source_alignment_terminal_failure" not in report["current_blockers"]
    admission = next(
        item for item in report["built_running"] if item["name"] == "cex_funding_successor_admission_gate"
    )
    assert "successor_created=false" in admission["why"]
    assert any("2026-07-17T05:24:00Z" in item for item in report["next_actions"])
    assert report["can_trade"] is False


def test_state_map_surfaces_explicit_runtime_shutdown(tmp_path: Path) -> None:
    write_json(
        tmp_path / "runtime_shutdown.request.json",
        {
            "ts": "2026-07-13T23:15:20Z",
            "request_id": "test-stop",
            "requested_by": "Stop-TradingOSRuntime.ps1",
            "can_trade": False,
        },
        1.0,
    )

    report = build_report(tmp_path)

    assert "managed_runtime_operator_stopped" in report["current_blockers"]
    assert report["managed_runtime"]["operator_stopped"] is True
    assert report["managed_runtime"]["automatic_resume_allowed"] is False
    runtime_row = next(item for item in report["built_running"] if item["name"] == "managed_runtime")
    assert runtime_row["status"] == "operator_stopped"
    assert "automatic_resume=false" in runtime_row["why"]


def test_state_map_records_completed_wo108_delivery_without_edge_promotion(tmp_path: Path) -> None:
    write_json(
        tmp_path / "BITUNIX_WO108_EVIDENCE_DELIVERY_2026-07-14.json",
        {
            "decision": "bitunix_wo108_evidence_delivered_verified_with_declared_missing_objects",
            "package": {
                "name": "BITUNIX_WO105_V2_EVIDENCE_TEST",
                "files_verified": 74,
                "delivery_complete": True,
            },
            "missing_evidence": ["legacy_pid_receipt"],
            "verification": {"runtime_loop_still_running": True},
            "can_trade": False,
        },
        1.0,
    )

    report = build_report(tmp_path)

    row = next(item for item in report["built_running"] if item["name"] == "bitunix_wo108_evidence_delivery")
    assert row["status"] == "bitunix_wo108_evidence_delivered_verified_with_declared_missing_objects"
    assert "files=74" in row["why"]
    assert "missing=1" in row["why"]
    assert "transfer_only=true" in row["why"]
    assert any("do not create another package" in item for item in report["do_not_repeat"])
    assert report["can_trade"] is False


def test_state_map_records_post_fill_markout_without_premature_guard(tmp_path: Path) -> None:
    write_json(
        tmp_path / "POST_FILL_MARKOUT_PROOF_2026-07-14.json",
        {
            "decision": "working_research_only_waiting_authoritative_fills",
            "current_smoke": {
                "archive_source_mode": "archive_missing",
                "raw_fill_count": 0,
                "evaluated_fill_count": 0,
            },
            "can_trade": False,
        },
        1.0,
    )

    report = build_report(tmp_path)

    row = next(item for item in report["built_running"] if item["name"] == "post_fill_markout")
    assert row["status"] == "working_research_only_waiting_authoritative_fills"
    assert "raw_fills=0" in row["why"]
    assert "guard_wired=false" in row["why"]
    assert "post_fill_markout_waiting_authoritative_fills" in report["current_blockers"]
    assert any("do not rebuild" in item for item in report["do_not_repeat"] if "post_fill_markout" in item)
    assert report["can_trade"] is False


def test_state_map_prefers_locked_post_fill_forward_evidence_state(tmp_path: Path) -> None:
    write_json(
        tmp_path / "POST_FILL_MARKOUT_PROOF_2026-07-14.json",
        {
            "decision": "working_research_only_waiting_authoritative_fills",
            "current_smoke": {"raw_fill_count": 0},
            "can_trade": False,
        },
        1.0,
    )
    write_json(
        tmp_path / "POST_FILL_MARKOUT_FORWARD_PROOF_2026-07-14.json",
        {
            "decision": "forward_observer_working_waiting_authoritative_demo_fills",
            "lock_id": "test_forward_lock",
            "current_forward": {
                "decision": "waiting_demo_credentials_for_authoritative_fills",
                "book_capture": {"capture_fresh": True},
                "raw_fill_count": 0,
                "evaluated_fill_count": 0,
                "blockers": ["demo_credentials_missing_for_authoritative_fills"],
            },
            "durable_runtime": {
                "running_verified": True,
                "ownership_decision": "running_verified_job_contained",
            },
            "can_trade": False,
        },
        2.0,
    )

    report = build_report(tmp_path)

    row = next(
        item for item in report["built_running"]
        if item["name"] == "post_fill_markout_forward_observer"
    )
    assert row["status"] == "waiting_demo_credentials_for_authoritative_fills"
    assert "book_fresh=True" in row["why"]
    assert "durable_runtime=True" in row["why"]
    assert "ownership=running_verified_job_contained" in row["why"]
    assert "thresholds_locked=false" in row["why"]
    assert "post_fill_markout_forward_waiting_evidence" in report["current_blockers"]
    assert "post_fill_markout_waiting_authoritative_fills" not in report["current_blockers"]
    assert any("immutable" in item for item in report["do_not_repeat"] if "post_fill_markout" in item)
    assert any("second collector" in item for item in report["do_not_repeat"] if "post_fill_markout" in item)
    assert report["can_trade"] is False


def test_state_map_tracks_spot_perp_flow_as_data_gate_not_strategy(tmp_path: Path) -> None:
    write_json(
        tmp_path / "BINANCE_SPOT_PERP_AGGRESSOR_FLOW_DATA_QUALITY_2026-07-15.json",
        {
            "classification": "binance_spot_perp_aggressor_flow_forward_collecting",
            "coverage": {"span_hours": 0.5, "dual_market_coverage_pct": 100.0},
            "integrity": {
                "spot": {"missing_ids": 0},
                "perpetual": {"missing_ids": 0},
            },
            "research_readiness": {
                "ready": False,
                "blockers": ["minimum_forward_span_not_reached"],
            },
            "runtime_boundary": {
                "hypothesis_registered": False,
                "strategy_search_allowed": False,
                "can_trade": False,
            },
            "can_trade": False,
        },
        1.0,
    )

    report = build_report(tmp_path)

    row = next(
        item
        for item in report["built_running"]
        if item["name"] == "binance_spot_perp_aggressor_flow_collector"
    )
    waiting = next(
        item
        for item in report["partial_waiting"]
        if item["name"] == "binance_spot_perp_aggressor_flow_snapshot_guard"
    )
    assert "binance_spot_perp_aggressor_flow_waiting_forward_data_gate" in report["current_blockers"]
    assert "hypothesis_registered=False" in row["why"]
    assert "strategy_search_allowed=false" in row["why"]
    assert waiting["blocker"] == "minimum_forward_span_not_reached"
    assert any("seal the snapshot" in action for action in report["next_actions"])
    assert report["can_trade"] is False


def test_state_map_recognizes_sealed_spot_perp_snapshot_without_opening_research(tmp_path: Path) -> None:
    write_json(
        tmp_path / "BINANCE_SPOT_PERP_AGGRESSOR_FLOW_DATA_QUALITY_2026-07-15.json",
        {
            "classification": "binance_spot_perp_aggressor_flow_ready_for_seal_review",
            "coverage": {"span_hours": 168.0, "dual_market_coverage_pct": 100.0},
            "integrity": {
                "spot": {"missing_ids": 0},
                "perpetual": {"missing_ids": 0},
            },
            "research_readiness": {"ready": True, "blockers": []},
            "runtime_boundary": {
                "hypothesis_registered": False,
                "strategy_search_allowed": False,
                "can_trade": False,
            },
            "can_trade": False,
        },
        1.0,
    )
    write_json(
        tmp_path / "BINANCE_SPOT_PERP_AGGRESSOR_FLOW_SNAPSHOT_GUARD_2026-07-15.json",
        {
            "decision": "spot_perp_flow_snapshot_already_sealed_verified",
            "sealed": True,
            "snapshot_id": "SPOT_PERP_FLOW_V1_TEST",
            "runtime_boundary": {
                "research_run": False,
                "validation_open": False,
                "can_trade": False,
            },
            "can_trade": False,
        },
        2.0,
    )

    report = build_report(tmp_path)

    guard = next(
        item
        for item in report["built_running"]
        if item["name"] == "binance_spot_perp_aggressor_flow_snapshot_guard"
    )
    assert "binance_spot_perp_aggressor_flow_waiting_forward_data_gate" not in report["current_blockers"]
    assert guard["status"] == "spot_perp_flow_snapshot_already_sealed_verified"
    assert "sealed=True" in guard["why"]
    assert all(
        item["name"] != "binance_spot_perp_aggressor_flow_snapshot_guard"
        for item in report["partial_waiting"]
    )
    assert any("prospective" in action for action in report["next_actions"])
    assert report["can_trade"] is False


def test_state_map_preserves_terminal_bitunix_bar_source_finding(tmp_path: Path) -> None:
    write_json(
        tmp_path / "MAIN_EDGE_SEARCH_PASS_2026-07-16_BITUNIX_BAR_FINALITY_AUDIT_V104.json",
        {
            "decision": "bitunix_public_bar_sources_not_admissible_for_frozen_five_second_close_contract",
            "kline_finality": {"comparisons": 2, "exact_ohlc_matches": 1},
            "trade_bar_finality": {"accepted_capture_full_bars": 5, "exact_ohlcv_matches": 0},
            "can_trade": False,
        },
        1.0,
    )

    report = build_report(tmp_path)

    row = next(
        item for item in report["built_running"] if item["name"] == "bitunix_bar_finality_terminal_audit"
    )
    assert row["status"] == "bitunix_public_bar_sources_not_admissible_for_frozen_five_second_close_contract"
    assert "bitunix_v3r4_exact_bar_source_terminal" in report["current_blockers"]
    assert any("bar_source_rescue" in item for item in report["do_not_repeat"])
    assert any("raw event-time" in action for action in report["next_actions"])
    assert report["can_trade"] is False

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from tools.live_data_edge_focus_summary import build_report
from tools.real_edge_observer_pulse import classify


ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def fixture_args(
    tmp_path: Path,
    *,
    force_ready: bool = False,
    force_transport_ready: bool | None = None,
    snapshot_id: str | None = None,
    micro_research_completed: bool = False,
) -> argparse.Namespace:
    generated_at = datetime.now(timezone.utc).isoformat()
    if force_transport_ready is None:
        force_transport_ready = force_ready
    sources = {
        "bybit": {
            "generated_at": generated_at,
            "decision": "bybit_liquidation_canonical_v5_collecting_outcome_blind_sample",
            "source_progress": {
                "post_floor_raw_events": 436,
                "post_floor_schema_valid_events": 436,
                "post_floor_packets": 292,
                "eligible_event_bars": 16,
            },
            "sample": {"resolved_events": 0, "utc_days": 0},
            "lock": {"forward_start_at": "2026-07-15T03:00:00Z"},
            "outcome_review": {"interim_outcomes_hidden": True, "outcome_fields_computed": False},
            "blockers": ["minimum_resolved_events_not_met"],
        },
        "force": {
            "generated_at": generated_at,
            "decision": "force_order_preregistered_progress_ready" if force_ready else "force_order_preregistered_progress_collecting",
            "ready_for_pipeline": force_ready,
            "sample": {
                "events": 4882,
                "event_bars": 275,
                "symbols_with_events": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BCHUSDT"],
                "independent_4h_blocks": 21,
                "matured_independent_4h_blocks": 20 if force_ready else 18,
            },
            "velocity": {"theoretical_earliest_pipeline_at": "2026-07-15T20:00:00Z"},
            "gates": [
                {
                    "name": "minimum_matured_independent_4h_blocks",
                    "actual": 20 if force_ready else 18,
                    "required": 20,
                    "passed": force_ready,
                }
            ],
            "blockers": [] if force_ready else ["minimum_matured_independent_4h_blocks"],
        },
        "force_continuity": {
            "generated_at": generated_at,
            "decision": (
                "force_order_transport_continuity_observed"
                if force_transport_ready
                else "force_order_transport_continuity_degraded_gaps"
            ),
            "continuity_observed": force_transport_ready,
            "blockers": [] if force_transport_ready else ["liveness_gaps_over_threshold"],
            "can_trade": False,
        },
        "micro": {
            "generated_at": generated_at,
            "decision": "microstructure_unblock_requires_gate_investigation",
            "snapshot_id": snapshot_id,
            "coverage": {"span_hours": 168, "trade_coverage_pct": 100, "book_coverage_pct": 96.4},
            "sla": {
                "decision": "collector_sla_replay_flapping" if not force_ready else "collector_sla_blocked",
                "open_incident": False,
                "cooldown_until_utc": "2026-07-15T16:45:42Z" if not force_ready else None,
                "cooldown_remaining_minutes": 120,
            },
            "transition": {
                "state": (
                    "sealed_snapshot_research_batch_already_completed"
                    if micro_research_completed
                    else "waiting_for_microstructure_readiness"
                ),
                "snapshot_id": snapshot_id,
            },
            "blockers": (
                ["sealed_snapshot_research_batch_already_completed"]
                if micro_research_completed
                else ["collector_sla_replay_flapping"]
            ),
        },
        "deribit": {
            "generated_at": generated_at,
            "decision": "deribit_options_stack_forward_collecting_readiness",
            "forward_progress": {
                "readiness_gate_ready": False,
                "span_days": 3.94,
                "minimum_span_days": 7,
                "healthy_slots": 922,
                "minimum_healthy_slots": 1800,
                "scheduled_coverage": 0.81,
            },
            "runtime": {"all_components_passed": True},
        },
        "funding": {
            "generated_at": generated_at,
            "decision": "cex_funding_research_readiness_blocked_alignment_terminal",
            "alignment": {
                "decision": "cex_funding_source_alignment_terminal_data_quality_failure",
                "terminal": True,
                "blockers": ["minimum_matching_time_coverage"],
            },
            "freshness": {"healthy": True},
        },
        "funding_admission": {
            "generated_at": generated_at,
            "decision": "cex_funding_successor_admission_waiting_clean_window",
            "eligible_for_manual_successor_lock_review": False,
            "diagnostic_window": {"earliest_recheck_at_utc": "2026-07-17T05:24:00Z"},
            "rolling_alignment": {"blockers": ["minimum_matching_minute_buckets"]},
            "runtime_boundary": {"successor_created": False, "can_trade": False},
            "next_action": "Keep both collectors running and recheck after the bounded clean window.",
            "can_trade": False,
        },
        "spot_perp_flow": {
            "generated_at": generated_at,
            "classification": "binance_spot_perp_aggressor_flow_forward_collecting",
            "coverage": {
                "span_hours": 0.25,
                "dual_market_coverage_pct": 100.0,
            },
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
                "can_trade": False,
            },
            "can_trade": False,
        },
        "spot_perp_flow_snapshot": {
            "generated_at": generated_at,
            "decision": "spot_perp_flow_snapshot_guard_waiting_data_gate",
            "sealed": False,
            "snapshot_id": None,
            "runtime_boundary": {
                "research_run": False,
                "can_trade": False,
            },
            "can_trade": False,
        },
        "audit": {
            "generated_at": generated_at,
            "decision": "full_system_devil_audit_no_critical_findings",
            "open_severity_counts": {},
            "source_runtime_parity": {"passed": True},
        },
    }
    paths: dict[str, Path] = {}
    for name, payload in sources.items():
        paths[name] = tmp_path / f"{name}.json"
        write_json(paths[name], payload)
    return argparse.Namespace(
        bybit_canonical_forward=str(paths["bybit"]),
        force_order_progress=str(paths["force"]),
        force_order_continuity=str(paths["force_continuity"]),
        microstructure_unblock=str(paths["micro"]),
        deribit_audit=str(paths["deribit"]),
        funding_readiness=str(paths["funding"]),
        funding_successor_admission=str(paths["funding_admission"]),
        spot_perp_flow_readiness=str(paths["spot_perp_flow"]),
        spot_perp_flow_snapshot=str(paths["spot_perp_flow_snapshot"]),
        devil_audit=str(paths["audit"]),
        liquidation_refresh="",
        edge_sweep="",
    )


def test_current_collecting_state_prefers_armed_microstructure_cooldown(tmp_path: Path) -> None:
    report = build_report(fixture_args(tmp_path))

    assert report["decision"] == "live_data_focus_microstructure_cooldown_waiting"
    assert report["live_classes"]["bybit_liquidation_v5r1"]["post_floor_raw_events"] == 436
    assert report["live_classes"]["bybit_liquidation_v5r1"]["outcomes_hidden"] is True
    assert report["live_classes"]["binance_force_order"]["events"] == 4882
    assert report["live_classes"]["binance_spot_perp_aggressor_flow"]["status"] == "forward_data_collecting"
    assert report["live_classes"]["binance_spot_perp_aggressor_flow"]["hypothesis_registered"] is False
    assert report["live_classes"]["binance_spot_perp_aggressor_flow"]["snapshot_sealed"] is False
    assert report["live_classes"]["cex_funding"]["status"] == "successor_admission_waiting_clean_window"
    assert report["live_classes"]["cex_funding"]["successor_created"] is False
    assert report["boundary"]["reads_interim_returns"] is False
    assert report["can_trade"] is False


def test_force_order_ready_remains_a_manual_research_gate(tmp_path: Path) -> None:
    report = build_report(fixture_args(tmp_path, force_ready=True))

    assert report["decision"] == "live_data_focus_force_order_pipeline_ready_manual_gate"
    assert report["live_classes"]["binance_force_order"]["ready_for_pipeline"] is True
    assert report["orders_allowed"] is False


def test_force_order_sample_ready_stays_blocked_when_transport_is_degraded(tmp_path: Path) -> None:
    report = build_report(
        fixture_args(tmp_path, force_ready=True, force_transport_ready=False)
    )

    force = report["live_classes"]["binance_force_order"]
    assert report["decision"] == "live_data_focus_force_order_transport_blocked"
    assert force["status"] == "sample_gate_ready_transport_blocked"
    assert force["sample_gate_ready"] is True
    assert force["transport_continuity_observed"] is False
    assert force["ready_for_pipeline"] is False
    force_action = next(
        item
        for item in report["action_queue"]
        if item["edge_class"] == "binance_force_order_feed"
    )
    assert force_action["next_eligible_at_utc"] is None
    assert report["can_trade"] is False


def test_sealed_microstructure_snapshot_has_first_priority(tmp_path: Path) -> None:
    report = build_report(fixture_args(tmp_path, force_ready=True, snapshot_id="snapshot-001"))

    assert report["decision"] == "live_data_focus_microstructure_snapshot_ready"
    assert report["live_classes"]["microstructure"]["status"] == "sealed_snapshot_available"
    assert report["can_trade"] is False


def test_completed_microstructure_snapshot_is_terminal_and_yields_focus(tmp_path: Path) -> None:
    report = build_report(
        fixture_args(
            tmp_path,
            force_ready=True,
            snapshot_id="snapshot-001",
            micro_research_completed=True,
        )
    )

    assert report["decision"] == "live_data_focus_force_order_pipeline_ready_manual_gate"
    assert report["live_classes"]["microstructure"]["status"] == "research_batch_already_completed"
    assert report["live_classes"]["microstructure"]["research_batch_already_completed"] is True
    assert report["action_queue"][0]["edge_class"] == "binance_force_order_feed"
    micro_action = next(
        item
        for item in report["action_queue"]
        if item["edge_class"] == "cross_venue_microstructure_snapshot"
    )
    assert "do not rerun" in micro_action["next_action"]
    assert report["can_trade"] is False


def test_sealed_spot_perp_snapshot_opens_only_manual_preregistration_review(tmp_path: Path) -> None:
    args = fixture_args(tmp_path)
    snapshot_path = Path(args.spot_perp_flow_snapshot)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot.update(
        {
            "decision": "spot_perp_flow_snapshot_already_sealed_verified",
            "sealed": True,
            "snapshot_id": "SPOT_PERP_FLOW_V1_TEST",
        }
    )
    write_json(snapshot_path, snapshot)

    report = build_report(args)

    flow = report["live_classes"]["binance_spot_perp_aggressor_flow"]
    assert flow["status"] == "sealed_snapshot_available"
    assert flow["snapshot_sealed"] is True
    assert flow["snapshot_id"] == "SPOT_PERP_FLOW_V1_TEST"
    action = next(
        item
        for item in report["action_queue"]
        if item["edge_class"] == "binance_spot_perp_aggressor_flow_lead_lag"
    )
    assert "prospective preregistration" in action["next_action"]
    assert report["boundary"]["runs_research"] is False
    assert report["can_trade"] is False


def test_missing_canonical_input_fails_closed(tmp_path: Path) -> None:
    args = fixture_args(tmp_path)
    args.funding_readiness = str(tmp_path / "missing.json")

    report = build_report(args)

    assert report["decision"] == "live_data_focus_inputs_missing_fail_closed"
    assert "funding" in report["source_freshness"]["missing_inputs"]
    assert report["can_trade"] is False


def test_stale_canonical_input_fails_closed(tmp_path: Path) -> None:
    args = fixture_args(tmp_path)
    funding_path = Path(args.funding_readiness)
    funding = json.loads(funding_path.read_text(encoding="utf-8"))
    funding["generated_at"] = "2026-07-01T00:00:00Z"
    write_json(funding_path, funding)

    report = build_report(args)

    assert report["decision"] == "live_data_focus_inputs_stale_fail_closed"
    assert "funding" in report["source_freshness"]["stale_inputs"]
    assert report["can_trade"] is False


def test_real_edge_pulse_surfaces_stale_focus_at_top_level() -> None:
    sources = {
        "live_data_focus": {"decision": "live_data_focus_inputs_stale_fail_closed"},
        "transition_monitor": {"decision": "real_edge_transition_no_change"},
        "edge_waiting_board": {"decision": "edge_waiting_board_no_trade_observing"},
    }

    decision, next_action = classify([], sources)

    assert decision == "real_edge_observer_pulse_canonical_source_attention_required"
    assert "stale canonical" in next_action


def test_real_edge_pulse_refreshes_current_live_data_focus() -> None:
    pulse = (ROOT / "tools" / "real_edge_observer_pulse.py").read_text(encoding="utf-8")

    assert '"live_data_focus": "docs/LIVE_DATA_EDGE_FOCUS_SUMMARY_2026-07-03.json"' in pulse
    assert '"tools/live_data_edge_focus_summary.py"' in pulse
    assert '"--bybit-canonical-forward"' in pulse
    assert '"--force-order-progress"' in pulse
    assert '"--force-order-continuity"' in pulse
    assert '"--binance-force-order-continuity"' in pulse
    assert '"--microstructure-unblock"' in pulse
    assert '"--deribit-audit"' in pulse
    assert '"--funding-readiness"' in pulse
    assert '"tools/cex_funding_successor_admission_gate.py"' in pulse
    assert '"--funding-successor-admission"' in pulse
    assert '"--spot-perp-flow-readiness"' in pulse
    assert '"--spot-perp-flow-snapshot"' in pulse
    assert '"tools/binance_spot_perp_aggressor_flow_snapshot_guard.py"' in pulse

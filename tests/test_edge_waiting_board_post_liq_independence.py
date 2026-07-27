from __future__ import annotations

import json
from datetime import datetime, timezone

from tools.edge_waiting_board import (
    bybit_forward_row,
    bybit_canonical_forward_row,
    bybit_canonical_v2_tombstone_row,
    forward_observer_row,
    liquidation_timing_vol_row,
    microstructure_row,
    post_liq_absorption_row,
    strategy_frontier_row,
)


def test_canonical_bybit_row_exposes_hidden_outcome_boundary(tmp_path) -> None:
    report = tmp_path / "canonical.json"
    report.write_text(
        json.dumps(
            {
                "decision": "bybit_liquidation_canonical_v4_collecting_outcome_blind_sample",
                "can_trade": False,
                "orders_allowed": False,
                "lock": {"forward_start_at": "2026-07-13T09:00:00Z"},
                "sample": {
                    "resolved_events": 12,
                    "utc_days": 1,
                    "symbol_count": 4,
                    "independent_4h_blocks": 3,
                },
                "outcome_review": {"interim_outcomes_hidden": True, "terminal_metrics": None},
                "runtime_boundary": {"orders_allowed": False},
                "blockers": ["minimum_resolved_events_not_met"],
            }
        ),
        encoding="utf-8",
    )

    item = bybit_canonical_forward_row(report)

    assert item["state"] == "waiting_forward_sample"
    assert item["edge_class"] == "bybit_liquidation_canonical_reversal_v5r1"
    assert "resolved 12/100" in item["progress"]
    assert "outcomes_hidden True" in item["progress"]
    assert item["can_trade"] is False


def test_v2_design_tombstone_never_recommends_restart(tmp_path) -> None:
    report = tmp_path / "v2_tombstone.json"
    report.write_text(
        json.dumps(
            {
                "decision": "bybit_canonical_v2_design_tombstone_open_exit_bar_risk",
                "terminal": True,
                "can_trade": False,
                "v2": {"resolved_events_at_tombstone": 0},
            }
        ),
        encoding="utf-8",
    )

    item = bybit_canonical_v2_tombstone_row(report)

    assert item["state"] == "tombstoned_design_contract"
    assert "never resume V2" in item["next_action"]
    assert item["can_trade"] is False


def test_semantic_tombstones_never_recommend_legacy_runners(tmp_path) -> None:
    reports = []
    for name, decision in (
        ("bybit.json", "bybit_liquidation_forward_semantic_contract_invalid_tombstone"),
        ("absorption.json", "post_liquidation_absorption_semantic_contract_invalid_tombstone"),
        ("timing.json", "liquidation_timing_vol_semantic_contract_invalid_tombstone"),
    ):
        path = tmp_path / name
        path.write_text(json.dumps({"decision": decision, "can_trade": False}), encoding="utf-8")
        reports.append(path)

    rows = [
        bybit_forward_row(reports[0]),
        post_liq_absorption_row(reports[1]),
        liquidation_timing_vol_row(reports[2]),
    ]

    assert all(item["state"] == "tombstoned_semantic_contract" for item in rows)
    assert all("never resume" in item["next_action"] for item in rows)
    assert all("legacy outcomes excluded" in item["progress"] for item in rows)


def test_waiting_board_exposes_mandatory_independence_gate(tmp_path) -> None:
    report = tmp_path / "post_liq.json"
    report.write_text(
        json.dumps(
            {
                "decision": "post_liq_absorption_forward_observer_collecting_sample",
                "evidence": {
                    "selected_bucket_min_n": 10,
                    "required_new_events": 30,
                    "selected_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
                    "required_new_symbols": 2,
                    "positive_horizons": 0,
                    "required_positive_horizons": 2,
                    "independent_blocks_min": 5,
                    "required_independent_blocks": 20,
                    "independence_decision": "post_liq_independence_audit_waiting_independent_sample",
                    "independence_eligible_for_manual_review": False,
                },
                "blockers": ["minimum_new_events"],
                "next_action": "keep collecting",
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )

    row = post_liq_absorption_row(report)

    assert row["state"] == "waiting_new_post_lock_events"
    assert "independent_4h_blocks 5/20" in row["progress"]
    assert "independence_sample_not_ready" in row["blocker"]
    assert "independence-adjusted cost gates" in row["unlock_condition"]
    assert row["can_trade"] is False


def test_microstructure_row_distinguishes_legacy_gap_from_current_health(tmp_path) -> None:
    report = tmp_path / "microstructure.json"
    report.write_text(
        json.dumps(
            {
                "decision": "microstructure_wait_for_book_coverage",
                "coverage": {
                    "trade_coverage_pct": 100.0,
                    "required_trade_coverage_pct": 95.0,
                    "book_coverage_pct": 54.910714,
                    "required_book_coverage_pct": 95.0,
                },
                "book_diagnostic": {
                    "recent_1h_dual_book_pct": 100.0,
                    "recent_6h_dual_book_pct": 100.0,
                    "eta_utc": "2026-07-15T05:36+00:00",
                },
                "blockers": ["dual_book_coverage", "waiting_for_book_coverage_rollout"],
                "next_action": "keep collecting",
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )

    row = microstructure_row(report)

    assert "book_coverage 54.9107%/95%" in row["progress"]
    assert "recent_1h 100%" in row["progress"]
    assert "recent_6h 100%" in row["progress"]
    assert "eta_utc 2026-07-15T05:36+00:00" in row["progress"]
    assert row["wait_mode"] == "time_and_operational_gate"
    assert row["earliest_recheck_at_utc"] == "2026-07-15T05:36+00:00"
    assert row["can_trade"] is False


def test_strategy_frontier_row_exposes_runtime_truth_gap(tmp_path) -> None:
    report = tmp_path / "frontier.json"
    report.write_text(
        json.dumps(
            {
                "decision": "observer_runtime_truth_gap_detected",
                "summary": {
                    "promotable": 0,
                    "observer_only": 3,
                    "stale_observers": 2,
                    "candidate_needs_observer_runtime": 1,
                    "rejected": 23,
                },
                "next_action": "resolve runtime truth gaps",
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )

    row = strategy_frontier_row(report)

    assert row["state"] == "observer_runtime_truth_gap"
    assert "active_observers 3" in row["progress"]
    assert "stale_observers 2" in row["progress"]
    assert row["blocker"] == "stale_observers:2,candidate_needs_runtime:1"
    assert "fresh runtime-proven observers" in row["unlock_condition"]
    assert row["can_trade"] is False


def test_forward_observer_row_uses_canonical_sample_and_lock_gate(tmp_path) -> None:
    lock = tmp_path / "lock.json"
    lock.write_text(
        json.dumps({"forward_gate_required": {"minimum_new_resolved_signals": 30}}),
        encoding="utf-8",
    )
    report = tmp_path / "observer.json"
    report.write_text(
        json.dumps(
            {
                "generated_at": "2026-07-12T00:00:00Z",
                "decision": "observer_collecting_sample",
                "lock_path": str(lock),
                "latest_scan": {
                    "latest_bar_ts": "2026-07-11T20:00:00Z",
                    "pending": [{"signal_key": "pending-1"}],
                },
                "summary": {"trades": 1},
                "sample_integrity": {
                    "journal_rows": 2,
                    "canonical_nonoverlap_rows": 1,
                    "excluded_rows": 1,
                },
                "blockers": ["minimum_new_resolved_signals"],
                "runtime_boundary": {"orders_allowed": False},
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )

    item = forward_observer_row(
        report,
        edge_class="derivatives_squeeze_disagreement",
        priority=27,
        observed_at=datetime(2026, 7, 12, 1, 0, tzinfo=timezone.utc),
    )

    assert item["state"] == "waiting_forward_sample"
    assert "canonical 1/30" in item["progress"]
    assert "raw 2" in item["progress"]
    assert "excluded 1" in item["progress"]
    assert "pending 1" in item["progress"]
    assert item["can_trade"] is False

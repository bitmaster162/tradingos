from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from tools.liquidation_force_order_preregistered_progress import evaluate_progress


def lock_payload() -> dict:
    return {
        "lock_id": "test_progress_lock",
        "status": "accepted_preregistered_research_only",
        "can_trade": False,
        "orders_allowed": False,
        "fixed_study": {
            "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BCHUSDT"],
            "source": "binance_usdm_forceOrder_websocket",
            "interval": "1h",
            "signal_time": "event_bar_close",
            "entry_time": "next_bar_open",
            "return_measurement": "next_bar_open_to_horizon_close",
            "horizons_bars": [1, 2, 4, 8],
            "event_start_at": "2026-07-12T04:00:00Z",
            "minimum_events": 500,
            "minimum_event_bars": 50,
            "minimum_context_bars": 15,
        },
        "hypothesis": {"primary_metric": "reversal_return_bps", "primary_horizon_bars": 2},
        "evaluation_gate": {
            "cost_buffer_bps": 7.0,
            "cluster_key": "market_wide_nonoverlap_4h_block_from_event_bar",
            "cluster_hours": 4,
            "cluster_aggregation": "mean_reversal_return_after_cost_within_block",
            "bootstrap_method": "nonparametric_cluster_resample_with_replacement",
            "bootstrap_iterations": 10000,
            "bootstrap_seed": 20260712,
            "confidence_level": 0.95,
            "primary_cluster_ci_lower_must_exceed_bps": 0.0,
            "minimum_positive_horizons_after_cost": 3,
            "primary_mean_after_cost_must_be_positive": True,
            "primary_winrate_must_exceed_pct": 50.0,
            "primary_winrate_unit": "independent_4h_block_mean_after_cost",
            "minimum_symbols_with_events": 3,
            "minimum_symbols_with_events_scope": "each_horizon",
            "minimum_independent_4h_blocks": 20,
            "minimum_independent_4h_blocks_scope": "each_horizon",
            "terminal_pass_decision": "pass_for_manual_forward_review",
            "terminal_fail_decision": "tombstone_review_required",
            "no_parameter_changes": True,
            "no_pooling_with_pre_lock_events": True,
            "manual_review_before_any_forward_observer": True,
            "paper_entries_allowed": False,
        },
    }


def intake_payload(*, short_context: int = 15) -> dict:
    start = datetime(2026, 7, 12, 4, tzinfo=timezone.utc)
    return {
        "decision": "force_order_context_ready_for_preregistered_research",
        "summary": {
            "events": 500,
            "event_bars": 60,
            "matched_event_bars": 60,
            "events_excluded_before_start": 31,
        },
        "context_counts": {
            "long_liquidation_flush": 20,
            "short_liquidation_squeeze": short_context,
            "mixed": 25,
        },
        "by_symbol": {
            "BTCUSDT": {"events": 200},
            "ETHUSDT": {"events": 180},
            "SOLUSDT": {"events": 120},
            "BCHUSDT": {"events": 0},
        },
        "price_bar_coverage_by_symbol": {
            "BTCUSDT": {"bars": 100, "first_bar_ts": "2026-07-10T00:00:00.000Z", "last_bar_ts": "2026-07-16T00:00:00.000Z"},
            "ETHUSDT": {"bars": 100, "first_bar_ts": "2026-07-10T00:00:00.000Z", "last_bar_ts": "2026-07-16T00:00:00.000Z"},
        },
        "_aggregate_rows": [
            {"bar_ts": (start + timedelta(hours=4 * index)).isoformat().replace("+00:00", "Z")}
            for index in range(20)
        ],
    }


def test_progress_requires_every_locked_gate(tmp_path) -> None:
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock_payload()), encoding="utf-8")

    report = evaluate_progress(
        lock_payload(),
        lock_path,
        intake_payload(short_context=14),
        datetime(2026, 7, 12, 10, tzinfo=timezone.utc),
    )

    assert report["ready_for_pipeline"] is False
    assert report["blockers"] == [
        "minimum_short_liquidation_squeeze_bars",
        "minimum_matured_independent_4h_blocks",
    ]
    assert report["can_trade"] is False


def test_progress_becomes_ready_without_reading_outcomes(tmp_path) -> None:
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock_payload()), encoding="utf-8")

    report = evaluate_progress(
        lock_payload(),
        lock_path,
        intake_payload(),
        datetime(2026, 7, 15, 20, tzinfo=timezone.utc),
    )

    assert report["decision"] == "force_order_preregistered_progress_ready_for_pipeline"
    assert report["ready_for_pipeline"] is True
    assert report["boundary"]["reads_outcomes"] is False
    assert report["boundary"]["runs_event_study"] is False
    assert report["sample"]["price_cache_watermarks"]["BTCUSDT"]["bars"] == 100
    assert report["can_trade"] is False


def test_progress_waits_for_max_horizon_maturity(tmp_path) -> None:
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock_payload()), encoding="utf-8")

    report = evaluate_progress(
        lock_payload(),
        lock_path,
        intake_payload(),
        datetime(2026, 7, 15, 19, 59, tzinfo=timezone.utc),
    )

    assert report["sample"]["independent_4h_blocks"] == 20
    assert report["sample"]["matured_independent_4h_blocks"] == 19
    assert report["sample"]["horizon_maturity_lag_hours"] == 12
    assert report["ready_for_pipeline"] is False
    assert report["blockers"] == ["minimum_matured_independent_4h_blocks"]
    assert report["velocity"]["theoretical_earliest_pipeline_at"] == "2026-07-15T20:00:00Z"

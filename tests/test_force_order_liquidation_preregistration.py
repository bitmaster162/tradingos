from __future__ import annotations

import json
from types import SimpleNamespace

from tools.force_order_liquidation_research_pipeline import (
    build_report,
    event_study_allowed,
    locked_study,
    pipeline_decision,
)


def test_preregistration_contract_has_fixed_no_trade_study() -> None:
    lock = json.loads(
        open("configs/BINANCE_FORCE_ORDER_EVENT_STUDY_PREREG_2026-07-12.json", encoding="utf-8").read()
    )

    params, errors = locked_study(lock)

    assert errors == []
    assert params["symbols"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BCHUSDT"]
    assert params["event_start_at"] == "2026-07-12T04:00:00Z"
    assert params["entry_time"] == "next_bar_open"
    assert params["min_independent_4h_blocks"] == 20
    assert params["min_events_for_research"] == 500
    assert lock["can_trade"] is False


def test_pipeline_blocks_cli_universe_mismatch_before_subprocess(tmp_path) -> None:
    lock = {
        "lock_id": "test_lock",
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
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    args = SimpleNamespace(
        prereg_lock=str(lock_path),
        out_prefix=str(tmp_path / "out"),
        data_dir=str(tmp_path / "events"),
        symbols="ALL",
        interval="1h",
        horizons="1,2,4,8",
        min_events_for_research=500,
        min_event_bars_for_research=50,
        min_context_bars=15,
        timeout_seconds=10,
    )

    report = build_report(args)

    assert report["decision"] == "force_order_pipeline_blocked_preregistration_lock"
    assert "cli_override_mismatch:symbols" in report["preregistration"]["errors"]
    assert report["runs"] == {"intake": None, "event_study": None, "evaluation": None}
    assert report["can_trade"] is False


def test_event_study_is_blocked_until_intake_clears_all_locked_sample_gates() -> None:
    assert event_study_allowed(
        {"decision": "collecting_force_order_context_sample", "aggregate_csv": "context.csv"}
    ) is False
    assert event_study_allowed(
        {"decision": "collecting_force_order_context_bars", "aggregate_csv": "context.csv"}
    ) is False
    assert event_study_allowed(
        {"decision": "force_order_context_ready_for_preregistered_research", "aggregate_csv": "context.csv"}
    ) is True


def test_pipeline_terminal_decisions_come_only_from_cluster_evaluator() -> None:
    intake = {"decision": "force_order_context_ready_for_preregistered_research"}
    event = {"decision": "force_order_event_study_ready_for_review"}
    successful_run = {"exit_code": 0}

    passed, _ = pipeline_decision(
        intake,
        event,
        {"decision": "pass_for_manual_forward_review"},
        successful_run,
        successful_run,
        successful_run,
    )
    tombstoned, _ = pipeline_decision(
        intake,
        event,
        {"decision": "tombstone_review_required"},
        successful_run,
        successful_run,
        successful_run,
    )

    assert passed == "force_order_pipeline_pass_for_manual_forward_review"
    assert tombstoned == "force_order_pipeline_tombstone_review_required"

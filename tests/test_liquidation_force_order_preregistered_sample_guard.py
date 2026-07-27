from __future__ import annotations

import json
from types import SimpleNamespace

from tools.force_order_liquidation_research_pipeline import sha256_file
from tools.liquidation_force_order_collector_watchdog import render_markdown
from tools.liquidation_force_order_preregistered_sample_guard import (
    TERMINAL_PIPELINE_DECISIONS,
    build_report,
    readiness_fingerprint,
)


def write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_lock(path) -> None:
    write_json(
        path,
        {
            "lock_id": "test_preregistered_guard_lock",
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
        },
    )


def args_for(tmp_path):
    return SimpleNamespace(
        data_quality=str(tmp_path / "dq.json"),
        prereg_lock=str(tmp_path / "lock.json"),
        progress=str(tmp_path / "progress.json"),
        pipeline_out_prefix=str(tmp_path / "pipeline"),
        state_path=str(tmp_path / "state.json"),
        receipt_path=str(tmp_path / "receipt.json"),
        receipt_ledger=str(tmp_path / "receipts.jsonl"),
        out_prefix=str(tmp_path / "guard"),
        min_retry_event_delta=50,
        timeout_seconds=10,
    )


def test_guard_waits_below_locked_post_lock_minimum(tmp_path) -> None:
    args = args_for(tmp_path)
    write_lock(tmp_path / "lock.json")
    write_json(
        tmp_path / "dq.json",
        {"hard_failures": [], "events": {"preregistered_sample": {"events": 499}}},
    )

    report = build_report(args)

    assert report["decision"] == "force_order_preregistered_guard_waiting_sample"
    assert report["events"] == 499
    assert report["required_events"] == 500
    assert report["pipeline_run"] is None
    assert report["can_trade"] is False


def test_completed_lock_sha_is_exactly_once(tmp_path, monkeypatch) -> None:
    args = args_for(tmp_path)
    lock_path = tmp_path / "lock.json"
    write_lock(lock_path)
    write_json(
        tmp_path / "dq.json",
        {"hard_failures": [], "events": {"preregistered_sample": {"events": 900}}},
    )
    write_json(
        tmp_path / "state.json",
        {
            "completed": True,
            "lock_sha256": sha256_file(lock_path),
            "pipeline_output": str(tmp_path / "pipeline.json"),
        },
    )
    monkeypatch.setattr(
        "tools.liquidation_force_order_preregistered_sample_guard.create_or_verify_terminal_receipt",
        lambda *_args: {"decision": "terminal_receipt_verified", "receipt": {"evidence_chain_sha256": "abc"}},
    )

    report = build_report(args)

    assert report["decision"] == "force_order_preregistered_guard_already_completed"
    assert report["pipeline_run"] is None
    assert report["state"]["completed"] is True
    assert report["terminal_receipt"]["decision"] == "terminal_receipt_verified"
    assert report["can_trade"] is False


def test_watchdog_markdown_accepts_waiting_guard_without_pipeline() -> None:
    report = {
        "generated_at": "2026-07-12T03:59:00Z",
        "decision": "healthy",
        "can_trade": False,
        "restart_attempted": False,
        "before": {},
        "after": {},
        "data_quality": {"report": {"events": {}, "gates": []}},
        "first_event_guard": {"report": {}},
        "preregistered_sample_guard": {
            "report": {
                "decision": "force_order_preregistered_guard_waiting_sample",
                "events": 0,
                "required_events": 500,
                "pipeline": None,
            }
        },
        "supervisor_summary": {"report": {"current": {"event_storage": {}}, "history_summary": {}}},
    }

    markdown = render_markdown(report)

    assert "force_order_preregistered_guard_waiting_sample" in markdown
    assert "Pipeline decision: `None`" in markdown


def test_guard_requires_all_progress_gates_before_pipeline(tmp_path) -> None:
    args = args_for(tmp_path)
    lock_path = tmp_path / "lock.json"
    write_lock(lock_path)
    write_json(tmp_path / "dq.json", {"hard_failures": [], "events": {"preregistered_sample": {"events": 500}}})
    write_json(
        tmp_path / "progress.json",
        {
            "lock": {"sha256": sha256_file(lock_path)},
            "ready_for_pipeline": False,
            "blockers": ["minimum_matched_price_bars"],
        },
    )

    report = build_report(args)

    assert report["decision"] == "force_order_preregistered_guard_waiting_sample_gates"
    assert report["pipeline_run"] is None
    assert report["can_trade"] is False


def test_guard_reports_fresh_lock_matched_progress_count(tmp_path) -> None:
    args = args_for(tmp_path)
    lock_path = tmp_path / "lock.json"
    write_lock(lock_path)
    write_json(tmp_path / "dq.json", {"hard_failures": [], "events": {"preregistered_sample": {"events": 500}}})
    write_json(
        tmp_path / "progress.json",
        {
            "lock": {"sha256": sha256_file(lock_path)},
            "sample": {"events": 600},
            "ready_for_pipeline": False,
            "blockers": ["minimum_matured_independent_4h_blocks"],
        },
    )

    report = build_report(args)

    assert report["events"] == 600
    assert report["data_quality_events"] == 500
    assert report["progress_events"] == 600
    assert report["decision"] == "force_order_preregistered_guard_waiting_sample_gates"


def test_guard_terminal_set_requires_evaluator_pass_or_tombstone() -> None:
    assert TERMINAL_PIPELINE_DECISIONS == {
        "force_order_pipeline_pass_for_manual_forward_review",
        "force_order_pipeline_tombstone_review_required",
    }


def ready_progress(lock_sha: str, *, events: int = 600, last_bar_ts: str = "2026-07-15T20:00:00.000Z") -> dict:
    return {
        "lock": {"sha256": lock_sha},
        "ready_for_pipeline": True,
        "sample": {
            "events": events,
            "event_bars": 80,
            "matched_price_bars": 80,
            "contexts": {"long_liquidation_flush": 30, "short_liquidation_squeeze": 30, "mixed": 20},
            "symbols_with_events": ["BCHUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"],
            "independent_4h_blocks": 20,
            "matured_independent_4h_blocks": 20,
            "price_cache_watermarks": {
                "BTCUSDT": {"bars": 100, "first_bar_ts": "2026-07-10T00:00:00.000Z", "last_bar_ts": last_bar_ts}
            },
        },
        "gates": [{"name": "minimum_matured_independent_4h_blocks", "passed": True, "actual": 20, "required": 20}],
        "blockers": [],
    }


def test_guard_throttles_unchanged_outcome_blind_readiness_snapshot(tmp_path) -> None:
    args = args_for(tmp_path)
    lock_path = tmp_path / "lock.json"
    write_lock(lock_path)
    lock_sha = sha256_file(lock_path)
    progress = ready_progress(lock_sha)
    write_json(tmp_path / "dq.json", {"hard_failures": [], "events": {"preregistered_sample": {"events": 600}}})
    write_json(tmp_path / "progress.json", progress)
    write_json(
        tmp_path / "state.json",
        {
            "completed": False,
            "lock_sha256": lock_sha,
            "last_attempt_events": 600,
            "last_attempt_readiness_fingerprint": readiness_fingerprint(progress),
        },
    )

    report = build_report(args)

    assert report["decision"] == "force_order_preregistered_guard_waiting_retry_delta"
    assert report["pipeline_run"] is None


def test_guard_retries_when_price_cache_watermark_advances_without_event_delta(tmp_path, monkeypatch) -> None:
    args = args_for(tmp_path)
    lock_path = tmp_path / "lock.json"
    write_lock(lock_path)
    lock_sha = sha256_file(lock_path)
    previous = ready_progress(lock_sha)
    current = ready_progress(lock_sha, last_bar_ts="2026-07-15T21:00:00.000Z")
    write_json(tmp_path / "dq.json", {"hard_failures": [], "events": {"preregistered_sample": {"events": 600}}})
    write_json(tmp_path / "progress.json", current)
    write_json(
        tmp_path / "state.json",
        {
            "completed": False,
            "lock_sha256": lock_sha,
            "last_attempt_events": 600,
            "last_attempt_readiness_fingerprint": readiness_fingerprint(previous),
        },
    )
    monkeypatch.setattr(
        "tools.liquidation_force_order_preregistered_sample_guard.run_pipeline",
        lambda *_args: {"exit_code": 1, "timed_out": False, "stdout": "", "stderr": "synthetic"},
    )

    report = build_report(args)

    assert report["decision"] == "force_order_preregistered_guard_pipeline_failed"
    assert report["pipeline_run"] is not None
    assert report["state"]["last_attempt_readiness_fingerprint"] == readiness_fingerprint(current)


def test_guard_keeps_event_delta_throttle_when_only_event_count_changes(tmp_path) -> None:
    args = args_for(tmp_path)
    lock_path = tmp_path / "lock.json"
    write_lock(lock_path)
    lock_sha = sha256_file(lock_path)
    previous = ready_progress(lock_sha, events=600)
    current = ready_progress(lock_sha, events=601)
    write_json(tmp_path / "dq.json", {"hard_failures": [], "events": {"preregistered_sample": {"events": 601}}})
    write_json(tmp_path / "progress.json", current)
    write_json(
        tmp_path / "state.json",
        {
            "completed": False,
            "lock_sha256": lock_sha,
            "last_attempt_events": 600,
            "last_attempt_readiness_fingerprint": readiness_fingerprint(previous),
        },
    )

    report = build_report(args)

    assert readiness_fingerprint(previous) == readiness_fingerprint(current)
    assert report["decision"] == "force_order_preregistered_guard_waiting_retry_delta"
    assert report["pipeline_run"] is None

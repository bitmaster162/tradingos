from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from tools.force_order_liquidation_event_study import sha256_file, write_event_records
from tools.liquidation_force_order_cluster_evaluator import (
    bootstrap_cluster_mean,
    build_report,
    evaluate_records,
)


ROOT = Path(__file__).resolve().parents[1]


def params() -> dict:
    return {
        "horizons": [1, 2, 4, 8],
        "cost_buffer_bps": 7.0,
        "bootstrap_iterations": 250,
        "bootstrap_seed": 20260712,
        "confidence_level": 0.95,
        "min_independent_4h_blocks": 20,
        "minimum_symbols_with_events": 3,
        "min_context_bars": 15,
        "primary_horizon_bars": 2,
        "primary_winrate_must_exceed_pct": 50.0,
        "primary_cluster_ci_lower_must_exceed_bps": 0.0,
        "minimum_positive_horizons_after_cost": 3,
        "terminal_pass_decision": "pass_for_manual_forward_review",
        "terminal_fail_decision": "tombstone_review_required",
    }


def records(gross_reversal_bps: float, blocks: int = 20) -> list[dict]:
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BCHUSDT"]
    rows: list[dict] = []
    for horizon in (1, 2, 4, 8):
        for block in range(blocks):
            block_id = f"block-{block:02d}"
            rows.extend(
                [
                    {
                        "symbol": symbols[block % len(symbols)],
                        "independent_4h_block": block_id,
                        "horizon_bars": horizon,
                        "dominant_context": "long_liquidation_flush",
                        "reversal_return_bps": gross_reversal_bps,
                    },
                    {
                        "symbol": symbols[(block + 1) % len(symbols)],
                        "independent_4h_block": block_id,
                        "horizon_bars": horizon,
                        "dominant_context": "short_liquidation_squeeze",
                        "reversal_return_bps": gross_reversal_bps,
                    },
                ]
            )
    return rows


def test_cluster_evaluator_passes_only_strong_cost_adjusted_sample() -> None:
    result = evaluate_records(records(20.0), params())

    assert result["sample_ready"] is True
    assert result["decision"] == "pass_for_manual_forward_review"
    assert result["primary"]["cluster_after_cost"]["mean_bps"] == 13.0
    assert result["primary"]["independent_4h_blocks"] == 20
    assert result["symbol_concentration_diagnostics"]["informational_only_not_a_v3_gate"] is True
    assert result["symbol_concentration_diagnostics"]["primary_sign_flip_symbols"] == []
    assert all(result["economic_checks"].values())


def test_cluster_evaluator_tombstones_failed_locked_economics() -> None:
    result = evaluate_records(records(6.0), params())

    assert result["sample_ready"] is True
    assert result["decision"] == "tombstone_review_required"
    assert result["primary"]["cluster_after_cost"]["mean_bps"] == -1.0
    assert result["economic_checks"]["primary_cluster_mean_after_cost_positive"] is False


def test_cluster_evaluator_waits_instead_of_failing_undersized_sample() -> None:
    result = evaluate_records(records(20.0, blocks=19), params())

    assert result["sample_ready"] is False
    assert result["decision"] == "force_order_cluster_evaluator_waiting_independent_sample"
    assert result["economic_checks_evaluated"] is False
    assert result["economic_checks"] == {}


def test_cluster_bootstrap_is_deterministic() -> None:
    first = bootstrap_cluster_mean([10.0, -2.0, 5.0], 200, 42, 0.95)
    second = bootstrap_cluster_mean([10.0, -2.0, 5.0], 200, 42, 0.95)

    assert first == second
    assert first["mean_ci_bps"][0] <= first["mean_ci_bps"][1]


def persisted_records(gross_reversal_bps: float) -> list[dict]:
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BCHUSDT"]
    start = datetime(2026, 7, 12, 4, tzinfo=timezone.utc)
    rows: list[dict] = []
    for horizon in (1, 2, 4, 8):
        for block in range(20):
            bar = start + timedelta(hours=4 * block)
            bar_ts = bar.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            block_id = bar.isoformat(timespec="seconds").replace("+00:00", "Z")
            for offset, context in enumerate(("long_liquidation_flush", "short_liquidation_squeeze")):
                rows.append(
                    {
                        "symbol": symbols[(block + offset) % len(symbols)],
                        "bar_ts": bar_ts,
                        "independent_4h_block": block_id,
                        "signal_time": "event_bar_close",
                        "entry_time": (bar + timedelta(hours=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                        "entry_model": "next_bar_open",
                        "entry_price": 100.0,
                        "event_bar_close": 100.0,
                        "exit_time": (bar + timedelta(hours=horizon)).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                        "exit_price": 101.0,
                        "horizon_bars": horizon,
                        "dominant_context": context,
                        "total_notional_usd": 10000.0,
                        "raw_return_bps": gross_reversal_bps,
                        "continuation_return_bps": -gross_reversal_bps,
                        "reversal_return_bps": gross_reversal_bps,
                    }
                )
    return rows


def test_evaluator_verifies_records_hash_before_outcome_decision(tmp_path) -> None:
    records_path = tmp_path / "records.csv"
    event_report_path = tmp_path / "event.json"
    rows = persisted_records(20.0)
    write_event_records(records_path, rows)
    event_report = {
        "decision": "force_order_event_study_ready_for_review",
        "can_trade": False,
        "boundary": {"entry_at_next_bar_open": True, "event_bar_close_fill_forbidden": True},
        "inputs": {
            "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BCHUSDT"],
            "horizons_bars": [1, 2, 4, 8],
            "interval": "1h",
        },
        "artifacts": {
            "records_csv": str(records_path),
            "records_csv_sha256": sha256_file(records_path),
            "records": len(rows),
        },
    }
    event_report_path.write_text(json.dumps(event_report), encoding="utf-8")
    args = SimpleNamespace(
        prereg_lock=str(ROOT / "configs" / "BINANCE_FORCE_ORDER_EVENT_STUDY_PREREG_2026-07-12.json"),
        event_study_report=str(event_report_path),
        records_csv=str(records_path),
        out_prefix=str(tmp_path / "evaluation"),
    )

    valid = build_report(args)
    with records_path.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    tampered = build_report(args)

    assert valid["decision"] == "pass_for_manual_forward_review"
    assert valid["integrity_errors"] == []
    assert tampered["decision"] == "force_order_cluster_evaluator_integrity_blocked"
    assert "records_artifact_hash_mismatch" in tampered["integrity_errors"]

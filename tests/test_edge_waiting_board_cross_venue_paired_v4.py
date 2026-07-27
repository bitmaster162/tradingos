from __future__ import annotations

import json
from pathlib import Path

from tools.edge_waiting_board import cross_venue_paired_v4_row


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_paired_v4_row_exposes_forward_sample_and_frozen_gates(tmp_path: Path) -> None:
    lock = tmp_path / "lock.json"
    report = tmp_path / "report.json"
    write_json(
        lock,
        {
            "terminal_gate": {
                "minimum_primary_window_pairs": 200,
                "minimum_utc_days": 5,
                "minimum_symbols": 5,
                "maximum_single_symbol_share": 0.5,
            }
        },
    )
    write_json(
        report,
        {
            "decision": "liquidation_cross_venue_paired_leadership_collecting_forward_sample",
            "can_trade": False,
            "lock": {"path": str(lock), "forward_start_at": "2026-07-15T08:00:00Z"},
            "source_counters": {"binance": {"accepted": 38}, "bybit": {"accepted": 34}},
            "evaluation_cutoff": "2026-07-15T08:07:25.803Z",
            "primary_sample": {
                "matched_pairs": 11,
                "utc_days": 1,
                "symbol_count": 3,
                "max_single_symbol_share": 0.45454545,
            },
            "terminal": {"reached": False},
            "blockers": ["minimum_primary_window_pairs_not_met", "minimum_symbols_not_met"],
            "next_action": "keep both public collectors running",
            "runtime_boundary": {"can_trade": False, "orders_allowed": False},
        },
    )

    item = cross_venue_paired_v4_row(report)

    assert item["state"] == "waiting_forward_sample"
    assert item["edge_class"] == "liquidation_cross_venue_paired_leadership_v4"
    assert "pairs5s 11/200" in item["progress"]
    assert "days 1/5" in item["progress"]
    assert "symbols 3/5" in item["progress"]
    assert "accepted Bn/By 38/34" in item["progress"]
    assert "minimum_primary_window_pairs_not_met" in item["blocker"]
    assert item["wait_mode"] == "outcome_blind_sample_accumulation"
    assert item["gate_progress"][0]["actual"] == 11.0
    assert item["gate_progress"][0]["required"] == 200.0
    assert item["can_trade"] is False
    assert item["orders_allowed"] is False


def test_paired_v4_row_fails_closed_on_unsafe_runtime_boundary(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    write_json(
        report,
        {
            "decision": "liquidation_cross_venue_paired_leadership_collecting_forward_sample",
            "can_trade": False,
            "primary_sample": {},
            "terminal": {"reached": False},
            "runtime_boundary": {"can_trade": True, "orders_allowed": False},
        },
    )

    item = cross_venue_paired_v4_row(report)

    assert item["state"] == "unsafe_boundary_attention"
    assert item["can_trade"] is False
    assert item["orders_allowed"] is False

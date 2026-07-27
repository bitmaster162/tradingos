from __future__ import annotations

import json
from pathlib import Path

from tools.edge_waiting_board import binance_force_order_row


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_force_order_row_prefers_current_outcome_blind_progress(tmp_path: Path) -> None:
    data_quality = tmp_path / "data_quality.json"
    progress = tmp_path / "progress.json"
    write_json(
        data_quality,
        {
            "decision": "liquidation_force_order_data_ready_for_preregistered_research",
            "ready_for_preregistered_research": True,
            "events": {
                "events": 48613,
                "preregistered_sample": {"events": 3089},
            },
            "hard_failures": [],
            "soft_failures": [],
        },
    )
    write_json(
        progress,
        {
            "decision": "force_order_preregistered_progress_collecting",
            "ready_for_pipeline": False,
            "sample": {
                "events": 4600,
                "event_bars": 258,
                "matched_price_bars": 258,
                "contexts": {
                    "long_liquidation_flush": 121,
                    "short_liquidation_squeeze": 119,
                },
                "symbols_with_events": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BCHUSDT"],
                "independent_4h_blocks": 19,
                "matured_independent_4h_blocks": 16,
            },
            "blockers": [
                "minimum_independent_4h_blocks",
                "minimum_matured_independent_4h_blocks",
            ],
            "velocity": {"theoretical_earliest_pipeline_at": "2026-07-15T20:00:00Z"},
        },
    )

    result = binance_force_order_row(data_quality, progress)

    assert result["state"] == "waiting_preregistered_sample_gates"
    assert "preregistered_events 4600" in result["progress"]
    assert "independent4h 19" in result["progress"]
    assert "matured4h 16" in result["progress"]
    assert "earliest 2026-07-15T20:00:00Z" in result["progress"]
    assert result["wait_mode"] == "sample_maturity_gate"
    assert result["earliest_recheck_at_utc"] == "2026-07-15T20:00:00Z"
    assert result["source"] == str(progress.resolve())


def test_force_order_row_exposes_locked_pipeline_only_after_all_gates(tmp_path: Path) -> None:
    data_quality = tmp_path / "data_quality.json"
    progress = tmp_path / "progress.json"
    continuity = tmp_path / "continuity.json"
    write_json(
        data_quality,
        {
            "ready_for_preregistered_research": True,
            "events": {"events": 5000, "preregistered_sample": {"events": 4000}},
            "hard_failures": [],
            "soft_failures": [],
        },
    )
    write_json(
        progress,
        {
            "ready_for_pipeline": True,
            "sample": {
                "events": 5000,
                "independent_4h_blocks": 20,
                "matured_independent_4h_blocks": 20,
            },
            "blockers": [],
        },
    )
    write_json(
        continuity,
        {
            "decision": "force_order_transport_continuity_observed",
            "continuity_observed": True,
            "sample": {"observation_hours": 24.5, "invalid_liveness_rows": 0},
            "gaps_over_threshold": [],
            "blockers": [],
        },
    )

    result = binance_force_order_row(data_quality, progress, continuity)

    assert result["state"] == "ready_for_locked_research_pipeline"
    assert result["gate_progress"][-1]["name"] == "transport_continuity_observed"
    assert result["gate_progress"][-1]["passed"] is True
    assert result["can_trade"] is False
    assert result["orders_allowed"] is False


def test_force_order_row_blocks_ready_sample_until_transport_continuity(tmp_path: Path) -> None:
    data_quality = tmp_path / "data_quality.json"
    progress = tmp_path / "progress.json"
    continuity = tmp_path / "continuity.json"
    write_json(
        data_quality,
        {
            "ready_for_preregistered_research": True,
            "events": {"events": 5000, "preregistered_sample": {"events": 4000}},
            "hard_failures": [],
            "soft_failures": [],
        },
    )
    write_json(progress, {"ready_for_pipeline": True, "sample": {"events": 5000}, "blockers": []})
    write_json(
        continuity,
        {
            "decision": "force_order_transport_continuity_degraded_gaps",
            "continuity_observed": False,
            "sample": {"observation_hours": 21.0, "invalid_liveness_rows": 0},
            "gaps_over_threshold": [{"seconds": 600}],
            "blockers": ["liveness_gaps_over_threshold"],
            "recovery": {"earliest_recheck_at_utc": "2026-07-17T18:18:00Z"},
        },
    )

    result = binance_force_order_row(data_quality, progress, continuity)

    assert result["state"] == "waiting_transport_continuity"
    assert result["wait_mode"] == "transport_continuity_gate"
    assert "liveness_gaps_over_threshold" in result["blocker"]
    assert result["source"] == str(continuity.resolve())
    assert result["earliest_recheck_at_utc"] == "2026-07-17T18:18:00Z"
    assert result["gate_progress"][-1]["passed"] is False
    assert result["can_trade"] is False

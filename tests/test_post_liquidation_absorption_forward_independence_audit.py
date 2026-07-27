from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tools.post_liquidation_absorption_forward_independence_audit import (
    bootstrap_mean,
    classify_source_messages,
    cluster_values,
    horizon_audit,
    nonoverlap_block_id,
)


ROOT = Path(__file__).resolve().parents[1]


def row(symbol: str, bar_ts: str, value: float, horizon: int = 4) -> dict:
    return {
        "symbol": symbol,
        "bar_ts": bar_ts,
        "horizon_bars": horizon,
        "side_forward_bps": value,
    }


def config() -> dict:
    return {
        "execution_realism": {
            "base_round_trip_cost_bps": 20.0,
            "stress_round_trip_cost_bps": 30.0,
            "nonoverlap_block_hours": 4,
        },
        "bootstrap": {"iterations": 100, "seed": 7},
    }


def test_same_four_hour_window_is_one_independent_block() -> None:
    rows = [
        row("BTCUSDT", "2026-07-03T01:00:00Z", 50.0),
        row("ETHUSDT", "2026-07-03T02:00:00Z", 30.0),
        row("SOLUSDT", "2026-07-03T05:00:00Z", 40.0),
    ]
    values, clusters = cluster_values(rows, cost_bps=20.0, block_hours=4)

    assert len(clusters) == 2
    assert values == [20.0, 20.0]
    assert nonoverlap_block_id("2026-07-03T03:59:59Z", 4) == "2026-07-03T00:00:00Z"


def test_horizon_audit_applies_costs_and_leave_one_symbol_out() -> None:
    rows = [
        row("BTCUSDT", "2026-07-03T01:00:00Z", 50.0),
        row("ETHUSDT", "2026-07-03T01:00:00Z", 30.0),
        row("BTCUSDT", "2026-07-03T05:00:00Z", -10.0),
    ]
    audit = horizon_audit(rows, config(), 4)

    assert audit["raw_events"] == 3
    assert audit["independent_4h_blocks"] == 2
    assert audit["base_cost_cluster_summary"]["mean_bps"] == -5.0
    assert set(audit["leave_one_symbol_out"]) == {"BTCUSDT", "ETHUSDT"}


def test_duplicate_event_keys_are_visible() -> None:
    duplicate = row("BTCUSDT", "2026-07-03T01:00:00Z", 50.0)
    audit = horizon_audit([duplicate, dict(duplicate)], config(), 4)

    assert audit["duplicate_event_keys"] == 1


def test_cluster_bootstrap_is_deterministic() -> None:
    first = bootstrap_mean([10.0, -5.0, 20.0], 100, 42)
    second = bootstrap_mean([10.0, -5.0, 20.0], 100, 42)

    assert first == second
    assert 0.0 <= first["probability_mean_gt_zero"] <= 1.0


def test_source_rejections_are_not_mislabeled_as_runtime_errors() -> None:
    exclusions, errors = classify_source_messages(
        ["row_6:non_directional_context", "missing_aligned_bar:BTCUSDT:2026-07-03T00:00:00Z"]
    )

    assert exclusions == ["row_6:non_directional_context"]
    assert errors == ["missing_aligned_bar:BTCUSDT:2026-07-03T00:00:00Z"]


def test_direct_cli_import_path_is_runnable() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "post_liquidation_absorption_forward_independence_audit.py"), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Cluster-aware audit" in result.stdout

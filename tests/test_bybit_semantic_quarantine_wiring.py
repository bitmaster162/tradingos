from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.strategy_research_frontier_matrix import build_report


ROOT = Path(__file__).resolve().parents[1]


def test_real_edge_pulse_does_not_run_legacy_bybit_directional_observers() -> None:
    source = (ROOT / "tools" / "real_edge_observer_pulse.py").read_text(encoding="utf-8")
    assert "tools/bybit_liquidation_side_semantics_audit.py" in source
    assert "tools/bybit_liquidation_canonical_forward_observer_v5.py" in source
    assert "tools/bybit_liquidation_canonical_input_quality_v5.py" in source
    assert "configs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_PREREG_V5R2_2026-07-18.json" in source
    assert "configs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_LOCK_V5R2_2026-07-18.json" in source
    assert "configs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_LOCK_V5R1_2026-07-15.json" not in source
    assert "logs/bybit_liquidation_canonical_forward_v5r2/terminal_receipt.json" in source
    assert "tools/bybit_liquidation_canonical_forward_observer_v4.py" not in source
    assert "tools/bybit_liquidation_canonical_forward_observer_v3.py" not in source
    assert "tools/bybit_liquidation_canonical_v2_bar_closure_audit.py" in source
    assert "tools/deribit_options_v3_runtime_audit.py" in source
    assert "tools/deribit_options_research_runtime_audit.py" not in source
    assert "tools/active_observer_runtime_coverage_audit.py" in source
    assert "tools/bitunix_wo105_v2_first_cycle_gate.py" in source
    assert "tools/liquidation_cross_venue_canonical_paired_leadership_forward_observer_v4.py" in source
    assert "configs/LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_LOCK_V4_2026-07-15.json" in source
    assert "LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_FORWARD_OBSERVER_V3_2026-07-13.json" not in source
    assert "tools/bybit_liquidation_canonical_forward_observer.py\",\n                \"run-once" not in source
    assert "tools/bybit_liquidation_forward_gate_pulse.py" not in source
    assert "tools/post_liquidation_absorption_forward_observer_runner.py" not in source
    assert "tools/liquidation_timing_vol_forward_observer_runner.py" not in source


def test_bybit_runtime_loop_uses_packet_ordinal_collector() -> None:
    source = (ROOT / "ops" / "autostart" / "Run-BybitAllLiquidationCollectorLoop.ps1").read_text(encoding="utf-8-sig")
    assert "tools\\bybit_all_liquidation_real_feed_collector_v2.py" in source
    assert '"tools\\bybit_all_liquidation_real_feed_collector.py"' not in source
    assert "ingest_schema_version = 4" in source


def test_frontier_tracks_packet_ordinal_bybit_forward_as_a_separate_family(tmp_path: Path) -> None:
    report_path = tmp_path / "BYBIT_LIQUIDATION_CANONICAL_FORWARD_OBSERVER_V5R2_2026-07-18.json"
    report_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-07-13T08:00:00Z",
                "decision": "bybit_liquidation_canonical_v5_waiting_floor",
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )

    report = build_report(tmp_path, as_of=datetime(2026, 7, 13, 8, 30, tzinfo=timezone.utc))
    item = next(row for row in report["families"] if row["family"] == "bybit_liquidation_canonical_reversal_v5r2")
    assert item["status"] == "observer_only_waiting_forward"
    assert item["path"].endswith(report_path.name)
    assert item["can_trade"] is False


def test_frontier_keeps_v3_clock_failure_as_a_separate_tombstone(tmp_path: Path) -> None:
    report_path = tmp_path / "BYBIT_LIQUIDATION_CANONICAL_V3_CLOCK_TOMBSTONE_2026-07-14.json"
    report_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-07-13T18:44:57Z",
                "decision": "bybit_canonical_v3_terminal_data_quality_tombstone_clock_domain_mismatch",
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )

    report = build_report(tmp_path, as_of=datetime(2026, 7, 13, 19, 0, tzinfo=timezone.utc))
    item = next(row for row in report["families"] if row["family"] == "bybit_liquidation_canonical_reversal_v3")
    assert item["status"] == "rejected_research_only"
    assert item["path"].endswith(report_path.name)
    assert item["can_trade"] is False


def test_frontier_prefers_v2_design_tombstone(tmp_path: Path) -> None:
    report_path = tmp_path / "BYBIT_LIQUIDATION_CANONICAL_FORWARD_V2_DESIGN_TOMBSTONE_2026-07-13.json"
    report_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-07-13T08:00:00Z",
                "decision": "bybit_canonical_v2_design_tombstone_open_exit_bar_risk",
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )

    report = build_report(tmp_path, as_of=datetime(2026, 7, 13, 8, 30, tzinfo=timezone.utc))
    item = next(row for row in report["families"] if row["family"] == "bybit_liquidation_canonical_reversal_v2")
    assert item["status"] == "rejected_research_only"
    assert item["path"].endswith(report_path.name)


def test_frontier_prefers_semantic_tombstone_over_legacy_runtime(tmp_path: Path) -> None:
    (tmp_path / "POST_LIQUIDATION_ABSORPTION_FORWARD_OBSERVER_RUNNER_2026-07-03.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-07-13T08:00:00Z",
                "decision": "post_liquidation_absorption_collecting_forward_sample",
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "POST_LIQUIDATION_ABSORPTION_SEMANTIC_TOMBSTONE_2026-07-13.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-07-13T07:00:00Z",
                "decision": "post_liquidation_absorption_semantic_contract_invalid_tombstone",
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )
    report = build_report(tmp_path, as_of=datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc))
    item = next(row for row in report["families"] if row["family"] == "post_liquidation_absorption_spot_perp")
    assert item["status"] == "rejected_research_only"
    assert "semantic_contract_invalid_tombstone" in item["decision"]


def test_tombstone_registry_imports_all_impacted_semantic_families(tmp_path: Path) -> None:
    audit = tmp_path / "audit.json"
    audit.write_text(
        json.dumps(
            {
                "decision": "bybit_liquidation_side_semantics_v1_terminal_contract_failure",
                "contract_failure_proven": True,
                "same_input_diagnostic": {"exact_directional_context_swap": True},
                "impacted_families": [
                    {"family": "BYBIT_LIQUIDATION_FORWARD_OBSERVER", "locks": ["a.json"]},
                    {"family": "POST_LIQUIDATION_ABSORPTION_SPOT_PERP", "locks": ["b.json"]},
                    {"family": "LIQUIDATION_TIMING_VOL_CONTINUATION", "locks": ["c.json"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "registry"
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "edge_tombstone_registry.py"),
            "--bybit-side-semantics-audit",
            str(audit),
            "--out-prefix",
            str(out),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    families = {item["family"] for item in report["entries"]}
    assert {
        "BYBIT_LIQUIDATION_FORWARD_OBSERVER",
        "POST_LIQUIDATION_ABSORPTION_SPOT_PERP",
        "LIQUIDATION_TIMING_VOL_CONTINUATION",
    }.issubset(families)

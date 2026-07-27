from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.cross_venue_microstructure_research_runner import verify_microstructure_snapshot
from tools.cross_venue_microstructure_seal_pipeline_drill import create_synthetic_snapshot


def test_create_synthetic_snapshot_is_verifiable(tmp_path: Path) -> None:
    active_root = tmp_path / "Active"
    snapshot_dir = create_synthetic_snapshot(active_root, "synthetic-sealed-test")

    verification = verify_microstructure_snapshot(snapshot_dir, "synthetic-sealed-test")

    assert verification["passed"] is True
    assert verification["files_checked"] == 3
    assert verification["sqlite_integrity"] == "ok"


def test_seal_pipeline_drill_runs_end_to_end(tmp_path: Path) -> None:
    out_prefix = tmp_path / "DRILL"
    completed = subprocess.run(
        [
            sys.executable,
            "tools/cross_venue_microstructure_seal_pipeline_drill.py",
            "--work-dir",
            str(tmp_path / "work"),
            "--out-prefix",
            str(out_prefix),
            "--timeout-seconds",
            "120",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "microstructure_seal_pipeline_drill_passed" in completed.stdout
    report = json.loads(out_prefix.with_suffix(".json").read_text(encoding="utf-8"))
    assert report["decision"] == "microstructure_seal_pipeline_drill_passed"
    assert report["runner_completed"] == 4
    assert report["runner_failed"] == 0
    assert report["runner_tested_total"] == 774
    assert report["snapshot_notify_decision"] == "dry_run_ready"
    assert report["runner_notify_decision"] == "dry_run_ready"
    assert report["can_trade"] is False

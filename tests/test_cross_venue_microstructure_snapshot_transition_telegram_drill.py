from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_snapshot_transition_telegram_drill_runs_end_to_end(tmp_path: Path) -> None:
    out_prefix = tmp_path / "TRANSITION_TELEGRAM_DRILL"
    completed = subprocess.run(
        [
            sys.executable,
            "tools/cross_venue_microstructure_snapshot_transition_telegram_drill.py",
            "--work-dir",
            str(tmp_path / "work"),
            "--out-prefix",
            str(out_prefix),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(out_prefix.with_suffix(".json").read_text(encoding="utf-8"))
    assert report["decision"] == "snapshot_transition_telegram_drill_passed"
    assert report["steps_passed"] == 5
    assert report["steps_total"] == 5
    assert report["waiting_decision"] == "skipped_waiting"
    assert report["ready_first_decision"] == "dry_run_ready"
    assert report["ready_duplicate_decision"] == "skipped_duplicate"
    assert report["blocked_decision"] == "dry_run_ready"
    assert report["done_decision"] == "dry_run_ready"
    assert report["can_trade"] is False

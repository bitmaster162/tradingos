from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_rollout_handoff_drill_proves_exactly_once_chain(tmp_path: Path) -> None:
    out_prefix = tmp_path / "ROLLOUT_HANDOFF_DRILL"
    completed = subprocess.run(
        [
            sys.executable,
            "tools/cross_venue_microstructure_rollout_handoff_drill.py",
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

    assert "microstructure_rollout_handoff_drill_passed" in completed.stdout
    report = json.loads(out_prefix.with_suffix(".json").read_text(encoding="utf-8"))
    assert report["checks_passed"] == report["checks_total"] == 10
    assert report["states"]["before_seal"] == "waiting_for_book_coverage_rollout"
    assert report["states"]["runner_execution"] == "post_seal_auto_run_guard_executed_locked_runner_once"
    assert report["states"]["duplicate_call"] == "post_seal_auto_run_guard_duplicate_blocked_already_completed"
    assert report["runner_completed"] == 4
    assert report["runner_failed"] == 0
    assert report["runner_tested_total"] == 774
    assert report["can_trade"] is False

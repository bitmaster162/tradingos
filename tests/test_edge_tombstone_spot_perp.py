from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_spot_perp_standalone_divergence_is_tombstoned(tmp_path: Path) -> None:
    out_prefix = tmp_path / "registry"
    completed = subprocess.run(
        [
            sys.executable,
            "tools/edge_tombstone_registry.py",
            "--out-prefix",
            str(out_prefix),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(out_prefix.with_suffix(".json").read_text(encoding="utf-8"))
    entry = next(item for item in report["entries"] if item["family"] == "SPOT_PERP_STANDALONE_DIVERGENCE")
    assert entry["status"] == "tombstoned_no_retune"
    assert entry["evidence"]["passed_count"] == 0
    assert entry["can_trade"] is False

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_large_trade_tail_terminal_rejection_enters_tombstone_registry(tmp_path: Path) -> None:
    review = tmp_path / "review.json"
    review.write_text(
        json.dumps(
            {
                "decision": "reject_large_trade_tail_nonpositive_forward_economics_tombstone",
                "hypothesis_id": "h1",
                "economics": {"1m": {"mean_net_base_bps": -1.0}},
                "sample_checks": {"1m": True},
                "integrity_checks": {"observer_hash_matches_lock": True},
            }
        ),
        encoding="utf-8",
    )
    out_prefix = tmp_path / "registry"
    completed = subprocess.run(
        [
            sys.executable,
            "tools/edge_tombstone_registry.py",
            "--large-trade-tail-review",
            str(review),
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
    entry = next(item for item in report["entries"] if item["family"] == "CROSS_VENUE_LARGE_TRADE_TAIL_CONTINUATION")
    assert entry["status"] == "tombstoned_no_retune"
    assert entry["can_trade"] is False

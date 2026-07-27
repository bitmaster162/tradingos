from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.large_trade_tail_terminal_review import build_report


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path: Path, *, resolved: int = 50, mean: float = -1.0) -> Path:
    root = tmp_path / "observer"
    root.mkdir()
    observer = root / "observer.py"
    prereg = root / "PREREG.json"
    observer.write_text("print('observer')\n", encoding="utf-8")
    write_json(
        prereg,
        {
            "hypothesis_id": "h1",
            "family": "CROSS_VENUE_LARGE_TRADE_TAIL_CONTINUATION",
            "outcomes": {"horizons_minutes": [1, 5, 15], "minimum_resolved_events_per_horizon": 50},
        },
    )
    write_json(
        root / "IMMUTABLE_LOCK.json",
        {
            "hypothesis_id": "h1",
            "script_sha256": sha(observer),
            "prereg_sha256": sha(prereg),
            "retuning_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
    )
    summary = {
        horizon: {
            "resolved": resolved,
            "mean_net_base_bps": mean,
            "mean_net_stress_bps": mean - 10.0,
            "base_winrate_pct": 10.0,
            "threshold_ready": resolved >= 50,
        }
        for horizon in ("1m", "5m", "15m")
    }
    write_json(
        root / "runtime" / "LATEST.json",
        {
            "hypothesis_id": "h1",
            "summary": summary,
            "runtime_boundary": {"orders_allowed": False, "can_trade": False},
            "can_trade": False,
        },
    )
    (root / "runtime" / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "runtime" / "outcomes.jsonl").write_text("{}\n", encoding="utf-8")
    return root


def test_terminal_review_tombstones_all_horizons_with_negative_net_economics(tmp_path: Path) -> None:
    report = build_report(fixture(tmp_path))
    assert report["decision"] == "reject_large_trade_tail_nonpositive_forward_economics_tombstone"
    assert report["status"] == "tombstoned_no_retune"
    assert report["sample_ready"] is True
    assert report["can_trade"] is False


def test_terminal_review_waits_for_fixed_sample_floor(tmp_path: Path) -> None:
    report = build_report(fixture(tmp_path, resolved=49))
    assert report["decision"] == "large_trade_tail_terminal_review_waiting_sample"
    assert report["status"] == "waiting_forward"


def test_terminal_review_never_auto_promotes_positive_economics(tmp_path: Path) -> None:
    report = build_report(fixture(tmp_path, mean=5.0))
    assert report["decision"] == "large_trade_tail_terminal_review_manual_economics_review"
    assert report["status"] == "manual_review_only"
    assert report["can_trade"] is False


def test_terminal_review_blocks_tampered_observer(tmp_path: Path) -> None:
    root = fixture(tmp_path)
    (root / "observer.py").write_text("print('tampered')\n", encoding="utf-8")
    report = build_report(root)
    assert report["decision"] == "large_trade_tail_terminal_review_integrity_blocked"
    assert report["integrity_checks"]["observer_hash_matches_lock"] is False

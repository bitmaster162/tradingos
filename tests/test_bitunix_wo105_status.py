from __future__ import annotations

import json
from pathlib import Path

from tools import bitunix_wo105_status as module


ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads(
    (ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_2026-07-14.json").read_text(encoding="utf-8")
)
REPLAY = json.loads((ROOT / "docs" / "BITUNIX_WO105_EXACT_REPLAY_2026-07-14.json").read_text(encoding="utf-8"))


def test_empty_forward_ledger_is_ready_but_not_edge_evaluated(tmp_path: Path) -> None:
    report = module.build_report(LOCK, REPLAY, tmp_path / "missing.jsonl")

    assert report["decision"] == "bitunix_wo105_causal_shadow_ready_waiting_forward_events"
    assert report["canonical_replay"] == "PASS"
    assert report["causal_shadow_evaluator"] == "READY"
    assert report["forward_progress"] == "0/30"
    assert report["independent_edge_review_ready"] is False
    assert report["edge_evaluated"] is False
    assert report["can_trade"] is False


def test_ledger_update_after_terminal_fails_closed(tmp_path: Path) -> None:
    event_id = "a" * 64
    rows = [
        {
            "event_id": event_id,
            "state": "NO_FILL",
            "cohort_binding_sha256": LOCK["parameter_cohort_sha256"],
        },
        {
            "event_id": event_id,
            "state": "SHADOW_CLOSED",
            "cohort_binding_sha256": LOCK["parameter_cohort_sha256"],
        },
    ]
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    report = module.build_report(LOCK, REPLAY, ledger)

    assert report["causal_shadow_evaluator"] == "HOLD"
    assert any("ledger_update_after_terminal" in failure for failure in report["failures"])
    assert report["can_trade"] is False


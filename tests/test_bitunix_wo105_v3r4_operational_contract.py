from __future__ import annotations

import json
from pathlib import Path

from tools import bitunix_wo105_causal_shadow_evaluator_v4 as evaluator_v4
from tools import bitunix_wo105_v2_first_cycle_gate as first_cycle
from tools import bitunix_wo105_v2_status as status


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R4_2026-07-15.json"


def load_lock() -> dict:
    return json.loads(LOCK.read_text(encoding="utf-8"))


def test_status_dispatches_v3r4_to_v4_and_bound_v3r3_tombstone(tmp_path: Path) -> None:
    lock = load_lock()
    floor_ms = evaluator_v4.parse_iso_ms(lock["forward_start_at"])

    report = status.build_report(
        lock,
        tombstone=None,
        packet_status=None,
        ws_status=None,
        liquidation_status=None,
        ledger_path=tmp_path / "missing.jsonl",
        current_ms=floor_ms - 1,
    )

    assert report["cohort_version"] == "v3r4"
    assert report["evaluator"] == "READY"
    assert report["predecessor_cohort_version"] == "v3r3"
    assert report["v3r3_tombstoned_post_floor"] is True
    assert report["decision"] == "bitunix_wo105_v3r4_ready_waiting_forward_floor"
    assert report["failures"] == []
    assert report["can_trade"] is False


def test_first_cycle_dispatches_v3r4_to_v4_before_floor(tmp_path: Path) -> None:
    lock = load_lock()
    floor_ms = evaluator_v4.parse_iso_ms(lock["forward_start_at"])

    report = first_cycle.build_report(
        lock,
        loop_status={"status": "waiting_forward_floor", "can_trade": False},
        rest_root=tmp_path / "rest",
        ws_intake=None,
        packet_status=None,
        current_ms=floor_ms - 1,
    )

    assert report["cohort_version"] == "v3r4"
    assert report["decision"] == "bitunix_wo105_v3r4_first_cycle_waiting_forward_floor"
    assert report["failures"] == []
    assert report["overdue"] == []
    assert report["can_trade"] is False

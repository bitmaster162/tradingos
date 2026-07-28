from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from tools import bybit_liquidation_canonical_forward_observer_v5 as observer


ROOT = Path(__file__).resolve().parents[1]
V5R1_PREREG = ROOT / "configs" / "BYBIT_LIQUIDATION_CANONICAL_FORWARD_PREREG_V5R1_2026-07-15.json"
V5R2_PREREG = ROOT / "configs" / "BYBIT_LIQUIDATION_CANONICAL_FORWARD_PREREG_V5R2_2026-07-18.json"
V5R2_LOCK = ROOT / "configs" / "BYBIT_LIQUIDATION_CANONICAL_FORWARD_LOCK_V5R2_2026-07-18.json"
V5R2_REPORT = ROOT / "docs" / "BYBIT_LIQUIDATION_CANONICAL_FORWARD_OBSERVER_V5R2_2026-07-18.json"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_z(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_v5r2_is_an_unchanged_independent_replication() -> None:
    predecessor = read_json(V5R1_PREREG)
    successor = read_json(V5R2_PREREG)

    assert observer.validate_prereg(successor) == []
    assert successor["candidate"] == predecessor["candidate"]
    assert successor["sample_gate"] == predecessor["sample_gate"]
    assert successor["terminal_outcome_gate"] == predecessor["terminal_outcome_gate"]
    assert successor["input_quality_gate"] == predecessor["input_quality_gate"]
    assert successor["receipt_contract"] == predecessor["receipt_contract"]
    assert successor["supersedes"]["strategy_parameters_changed"] is False


def test_v5r2_excludes_predecessor_and_wo005_outcomes() -> None:
    prereg = read_json(V5R2_PREREG)

    assert prereg["supersedes"]["v5r1_observations_admitted"] is False
    assert prereg["supersedes"]["v5r1_terminal_metrics_admitted"] is False
    assert prereg["supersedes"]["wo005_descriptive_metrics_admitted"] is False
    assert prereg["research_boundary"]["interim_outcome_review_allowed"] is False
    assert prereg["research_boundary"]["retuning_forbidden"] is True
    assert parse_z(prereg["created_at"]) < parse_z(prereg["forward_floor_at"])


def test_v5r2_lock_is_sealed_to_the_real_consumer() -> None:
    lock = read_json(V5R2_LOCK)

    assert observer.validate_lock(lock) == []
    assert lock["preregistration"]["sha256"] == sha256(V5R2_PREREG)
    assert lock["observer"]["path"] == "tools/bybit_liquidation_canonical_forward_observer_v5.py"
    assert lock["forward_start_at"] == "2026-07-18T12:00:00Z"
    assert lock["orders_allowed"] is False
    assert lock["can_trade"] is False


def test_v5r2_pre_floor_smoke_kept_outcomes_closed() -> None:
    report = read_json(V5R2_REPORT)

    assert report["decision"] in {
        "bybit_liquidation_canonical_v5_waiting_floor",
        "bybit_liquidation_canonical_v5_collecting_outcome_blind_sample",
    }
    assert report["input_quality"]["decision"] == "bybit_canonical_v5_input_quality_pass"
    assert report["outcome_review"]["interim_outcomes_hidden"] is True
    assert report["outcome_review"]["outcome_fields_computed"] is False
    assert report["outcome_review"]["terminal_metrics"] is None
    assert report["terminal"]["reached"] is False
    assert report["can_trade"] is False

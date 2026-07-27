from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import bybit_liquidation_canonical_forward_observer_v5 as module


ROOT = Path(__file__).resolve().parents[1]
REAL_PREREG = ROOT / "configs" / "BYBIT_LIQUIDATION_CANONICAL_FORWARD_PREREG_V5_2026-07-15.json"
R1_PREREG = ROOT / "configs" / "BYBIT_LIQUIDATION_CANONICAL_FORWARD_PREREG_V5R1_2026-07-15.json"


def write_prereg(tmp_path: Path, *, floor: str = "2099-01-02T00:00:00Z") -> Path:
    payload = json.loads(REAL_PREREG.read_text(encoding="utf-8"))
    discovery = tmp_path / "discovery.json"
    tombstone = tmp_path / "tombstone.json"
    discovery.write_text(json.dumps({"decision": "outcome_reviewed_discovery"}), encoding="utf-8")
    tombstone.write_text(json.dumps({"decision": "v4_packet_identity_tombstone"}), encoding="utf-8")
    payload["forward_floor_at"] = floor
    payload["discovery_provenance"]["report"] = str(discovery)
    payload["data_quality_tombstone"] = str(tombstone)
    payload["sources"] = {"liquidations": str(tmp_path / "liquidations"), "bars_root": str(tmp_path / "bars")}
    path = tmp_path / "prereg.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_lock(tmp_path: Path, *, floor: str = "2099-01-02T00:00:00Z") -> Path:
    lock = module.build_lock(write_prereg(tmp_path, floor=floor), created_at="2099-01-01T00:00:00Z")
    path = tmp_path / "lock.json"
    module.core.write_json(path, lock)
    return path


def progress_result(*, failures: list[str] | None = None):
    hard_failures = failures or []
    quality = {
        "decision": "bybit_canonical_v5_input_quality_pass" if not hard_failures else "blocked",
        "hard_failures": hard_failures,
        "bars": {"closed_cutoff_bar_open": "2099-01-01T01:00:00Z"},
        "events": {"post_floor_events": 0, "post_floor_schema_valid_events": 0},
    }
    return [], {}, [], {"post_floor_packet_rows": 0, "post_floor_packets": 0}, quality


def test_v5_lock_changes_only_receipt_protocol_not_strategy(tmp_path: Path):
    lock = module.build_lock(write_prereg(tmp_path), created_at="2099-01-01T00:00:00Z")

    assert module.validate_lock(lock) == []
    assert lock["receipt_contract"]["required_ingest_schema_version"] == 4
    assert lock["receipt_contract"]["packet_item_identity"].endswith("packet_item_index")
    assert lock["research_boundary"]["v4_observations_admitted"] is False
    assert lock["supersedes"]["strategy_parameters_changed"] is False


def test_v5r1_source_fix_does_not_retune_candidate_or_gates():
    v5 = json.loads(REAL_PREREG.read_text(encoding="utf-8"))
    r1 = json.loads(R1_PREREG.read_text(encoding="utf-8"))

    assert module.validate_prereg(r1) == []
    assert r1["candidate"] == v5["candidate"]
    assert r1["sample_gate"] == v5["sample_gate"]
    assert r1["terminal_outcome_gate"] == v5["terminal_outcome_gate"]
    assert r1["input_quality_gate"] == v5["input_quality_gate"]
    assert r1["receipt_contract"]["approved_source"] == "bybit_v5_allLiquidation_websocket"
    assert r1["supersedes"]["strategy_parameters_changed"] is False


def test_before_floor_keeps_outcomes_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    lock_path = write_lock(tmp_path)
    monkeypatch.setattr(module, "load_forward_progress", lambda _lock, now: progress_result())
    monkeypatch.setattr(
        module.core.study,
        "build_event_records",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("outcomes opened before floor")),
    )

    report = module.run_observer(
        lock_path,
        tmp_path / "report",
        tmp_path / "terminal.json",
        now=datetime(2099, 1, 1, 12, tzinfo=timezone.utc),
    )

    assert report["decision"] == "bybit_liquidation_canonical_v5_waiting_floor"
    assert report["outcome_review"]["outcome_fields_computed"] is False


def test_packet_quality_failure_blocks_without_outcome_computation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    lock_path = write_lock(tmp_path)
    monkeypatch.setattr(
        module,
        "load_forward_progress",
        lambda _lock, now: progress_result(failures=["post_floor_duplicate_packet_item_identities"]),
    )

    report = module.run_observer(
        lock_path,
        tmp_path / "report",
        tmp_path / "terminal.json",
        now=datetime(2099, 1, 3, tzinfo=timezone.utc),
    )

    assert report["decision"] == "bybit_liquidation_canonical_v5_blocked_input_quality"
    assert report["blockers"] == ["input_quality:post_floor_duplicate_packet_item_identities"]
    assert report["outcome_review"]["outcome_fields_computed"] is False

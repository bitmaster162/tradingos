from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import bybit_liquidation_canonical_forward_observer_v4 as module


ROOT = Path(__file__).resolve().parents[1]
REAL_PREREG = ROOT / "configs" / "BYBIT_LIQUIDATION_CANONICAL_FORWARD_PREREG_V4_2026-07-14.json"


def write_prereg(tmp_path: Path, *, floor: str = "2099-01-02T00:00:00Z") -> Path:
    payload = json.loads(REAL_PREREG.read_text(encoding="utf-8"))
    discovery = tmp_path / "discovery.json"
    tombstone = tmp_path / "tombstone.json"
    discovery.write_text(json.dumps({"decision": "outcome_reviewed_discovery"}), encoding="utf-8")
    tombstone.write_text(json.dumps({"decision": "clock_tombstone"}), encoding="utf-8")
    payload["forward_floor_at"] = floor
    payload["discovery_provenance"]["report"] = str(discovery)
    payload["data_quality_tombstone"] = str(tombstone)
    payload["sources"] = {"liquidations": str(tmp_path / "liquidations"), "bars_root": str(tmp_path / "bars")}
    path = tmp_path / "prereg.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_lock(tmp_path: Path, *, floor: str = "2099-01-02T00:00:00Z") -> Path:
    prereg_path = write_prereg(tmp_path, floor=floor)
    lock = module.build_lock(prereg_path, created_at="2099-01-01T00:00:00Z")
    path = tmp_path / "lock.json"
    module.v2.write_json(path, lock)
    return path


def quality_report(*, failures: list[str] | None = None) -> dict:
    hard_failures = failures or []
    return {
        "decision": "bybit_canonical_v4_input_quality_pass" if not hard_failures else "blocked",
        "hard_failures": hard_failures,
        "bars": {"closed_cutoff_bar_open": "2099-01-01T01:00:00Z"},
        "events": {"post_floor_events": 0, "post_floor_schema_valid_events": 0},
    }


def progress_result(*, metadata: list[dict] | None = None, failures: list[str] | None = None):
    return [], {}, metadata or [], {
        "closed_cutoff_bar_open": None,
        "post_floor_raw_events": 0,
        "post_floor_schema_valid_events": 0,
    }, quality_report(failures=failures)


def test_v4_lock_binds_calibrated_receipts_and_excludes_v3(tmp_path: Path) -> None:
    lock = module.build_lock(write_prereg(tmp_path), created_at="2099-01-01T00:00:00Z")

    assert module.validate_lock(lock) == []
    assert lock["receipt_contract"]["required_ingest_schema_version"] == 3
    assert lock["research_boundary"]["v3_observations_admitted"] is False
    assert lock["supersedes"]["strategy_parameters_changed"] is False


def test_before_floor_does_not_compute_outcome_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_path = write_lock(tmp_path)
    monkeypatch.setattr(module, "load_forward_progress", lambda _lock, now: progress_result())
    monkeypatch.setattr(
        module.v2.study,
        "build_event_records",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("outcomes opened before floor")),
    )

    report = module.run_observer(
        lock_path,
        tmp_path / "report",
        tmp_path / "terminal.json",
        now=datetime(2099, 1, 1, 12, tzinfo=timezone.utc),
    )

    assert report["decision"] == "bybit_liquidation_canonical_v4_waiting_floor"
    assert report["outcome_review"]["outcome_fields_computed"] is False
    assert report["outcome_review"]["terminal_metrics"] is None


def test_immature_sample_does_not_compute_outcome_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_path = write_lock(tmp_path)
    monkeypatch.setattr(module, "load_forward_progress", lambda _lock, now: progress_result())
    monkeypatch.setattr(
        module.v2.study,
        "build_event_records",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("interim outcomes opened")),
    )

    report = module.run_observer(
        lock_path,
        tmp_path / "report",
        tmp_path / "terminal.json",
        now=datetime(2099, 1, 3, 0, tzinfo=timezone.utc),
    )

    assert report["decision"] == "bybit_liquidation_canonical_v4_collecting_outcome_blind_sample"
    assert report["outcome_review"]["outcome_fields_computed"] is False


def test_input_quality_failure_blocks_without_outcome_computation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_path = write_lock(tmp_path)
    monkeypatch.setattr(
        module,
        "load_forward_progress",
        lambda _lock, now: progress_result(failures=["clock_calibration"]),
    )

    report = module.run_observer(
        lock_path,
        tmp_path / "report",
        tmp_path / "terminal.json",
        now=datetime(2099, 1, 3, 0, tzinfo=timezone.utc),
    )

    assert report["decision"] == "bybit_liquidation_canonical_v4_blocked_input_quality"
    assert report["blockers"] == ["input_quality:clock_calibration"]
    assert report["outcome_review"]["outcome_fields_computed"] is False

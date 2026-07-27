from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import bybit_liquidation_canonical_forward_observer_v3 as module
from tools.liquidity_sweep_detector import OhlcvBar


ROOT = Path(__file__).resolve().parents[1]
REAL_PREREG = ROOT / "configs" / "BYBIT_LIQUIDATION_CANONICAL_FORWARD_PREREG_V3_2026-07-13.json"


def write_prereg(tmp_path: Path, *, floor: str = "2099-01-02T00:00:00Z") -> Path:
    payload = json.loads(REAL_PREREG.read_text(encoding="utf-8"))
    discovery = tmp_path / "discovery.json"
    discovery.write_text(json.dumps({"decision": "outcome_reviewed_discovery"}), encoding="utf-8")
    payload["forward_floor_at"] = floor
    payload["discovery_provenance"]["report"] = str(discovery)
    payload["sources"] = {
        "liquidations": str(tmp_path / "liquidations"),
        "bars_root": str(tmp_path / "bars"),
    }
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
        "decision": "pass" if not hard_failures else "blocked",
        "hard_failures": hard_failures,
        "bars": {"closed_cutoff_bar_open": "2099-01-01T01:00:00Z"},
        "events": {"post_floor_events": 0},
    }


def test_lock_binds_v3_contract_and_cannot_be_sealed_at_floor(tmp_path: Path) -> None:
    prereg_path = write_prereg(tmp_path)
    lock = module.build_lock(prereg_path, created_at="2099-01-01T00:00:00Z")

    assert module.validate_lock(lock) == []
    assert lock["bar_contract"]["fully_closed_bars_only"] is True
    assert lock["research_boundary"]["v2_observations_admitted"] is False

    with pytest.raises(ValueError, match="before forward_floor_at"):
        module.build_lock(prereg_path, created_at="2099-01-02T00:00:00Z")


def test_before_floor_hides_outcomes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_path = write_lock(tmp_path)
    monkeypatch.setattr(
        module,
        "load_forward_records",
        lambda _lock, now: ([], {"resolved_records": 0, "closed_cutoff_bar_open": None}, quality_report()),
    )

    report = module.run_observer(
        lock_path,
        tmp_path / "report",
        tmp_path / "terminal.json",
        now=datetime(2099, 1, 1, 12, tzinfo=timezone.utc),
    )

    assert report["decision"] == "bybit_liquidation_canonical_v3_waiting_floor"
    assert report["outcome_review"]["interim_outcomes_hidden"] is True
    assert report["outcome_review"]["terminal_metrics"] is None
    assert report["can_trade"] is False


def test_v3_does_not_resolve_against_current_open_exit_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = [
        OhlcvBar(index=index, ts=f"2099-01-01T0{index}:00:00Z", open=100 + index, high=102 + index, low=99, close=101 + index, volume=1)
        for index in range(3)
    ]
    row = {
        "symbol": "BTCUSDT",
        "bar_ts": "2099-01-01T00:00:00.000Z",
        "matched_price_bar": True,
        "dominant_context": "long_liquidation_flush",
        "side_semantics_version": module.v2.semantics.CANONICAL_SIDE_SCHEMA_VERSION,
        "total_notional_usd": 100.0,
    }
    lock = {
        "forward_start_at": "2099-01-01T00:00:00Z",
        "candidate": {
            "symbols": ["BTCUSDT"],
            "interval": "1h",
            "horizon_bars": 2,
            "context": "long_liquidation_flush",
        },
        "sources": {"liquidations": "unused", "bars_root": "unused"},
    }
    monkeypatch.setattr(module.quality, "build_quality", lambda _lock, now: quality_report())
    monkeypatch.setattr(
        module.v2.intake,
        "build_report",
        lambda _args: {"_aggregate_rows": [row], "summary": {"events": 1, "aggregate_rows": 1}},
    )
    monkeypatch.setattr(
        module.v2.study,
        "load_bars_by_symbol",
        lambda _symbols, _interval, _root: ({"BTCUSDT": bars}, {"BTCUSDT": "test.csv"}),
    )

    records, progress, _ = module.load_forward_records(
        lock,
        now=datetime(2099, 1, 1, 2, 30, tzinfo=timezone.utc),
    )

    assert records == []
    assert progress["open_or_invalid_bar_rows_excluded"] == {"BTCUSDT": 1}


def test_input_quality_failure_blocks_terminal_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_path = write_lock(tmp_path)
    monkeypatch.setattr(
        module,
        "load_forward_records",
        lambda _lock, now: ([], {"resolved_records": 0, "closed_cutoff_bar_open": None}, quality_report(failures=["schema"])),
    )

    report = module.run_observer(
        lock_path,
        tmp_path / "report",
        tmp_path / "terminal.json",
        now=datetime(2099, 1, 3, 0, tzinfo=timezone.utc),
    )

    assert report["decision"] == "bybit_liquidation_canonical_v3_blocked_input_quality"
    assert report["blockers"] == ["input_quality:schema"]
    assert report["outcome_review"]["terminal_metrics"] is None
    assert report["terminal"]["reached"] is False


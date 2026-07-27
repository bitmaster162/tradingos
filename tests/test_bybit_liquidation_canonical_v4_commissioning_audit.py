from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tools import bybit_liquidation_canonical_v4_commissioning_audit as module


NOW = datetime(2026, 7, 13, 19, 0, tzinfo=timezone.utc)


def lock() -> dict:
    return {
        "forward_start_at": "2026-07-14T00:00:00Z",
        "candidate": {"symbols": ["BTCUSDT"]},
        "sources": {"liquidations": "unused"},
        "can_trade": False,
    }


def write_event(path: Path, *, liquidation_ms: int, session: str = "session-1", sequence: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ingest_schema_version": 3,
        "symbol": "BTCUSDT",
        "liquidation_time_ms": liquidation_ms,
        "collector_session_id": session,
        "packet_sequence": sequence,
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


def quality_snapshot(*_args, **_kwargs) -> dict:
    return {
        "hard_failures": [],
        "events": {
            "post_floor_events": 1,
            "post_floor_schema_valid_events": 1,
            "corrected_receipt_lag_ms": {"min": 10.0, "max": 20.0},
        },
        "boundary": {"outcome_fields_computed": False},
        "can_trade": False,
    }


def test_pre_floor_schema3_commissioning_passes_without_sample_admission(tmp_path: Path) -> None:
    write_event(tmp_path / "BTCUSDT" / "20260713.jsonl", liquidation_ms=1783969200000)
    report = module.build_report(lock(), tmp_path, observed_at=NOW, quality_builder=quality_snapshot)

    assert report["decision"] == "bybit_canonical_v4_commissioning_pass"
    assert report["commissioning_window"]["schema3_rows"] == 1
    assert report["runtime_boundary"]["sample_admission_allowed"] is False
    assert report["runtime_boundary"]["outcome_fields_computed"] is False
    assert report["can_trade"] is False


def test_commissioning_blocks_quality_failure(tmp_path: Path) -> None:
    write_event(tmp_path / "BTCUSDT" / "20260713.jsonl", liquidation_ms=1783969200000)

    def failed(*_args, **_kwargs) -> dict:
        payload = quality_snapshot()
        payload["hard_failures"] = ["post_floor_clock_offset_failures"]
        return payload

    report = module.build_report(lock(), tmp_path, observed_at=NOW, quality_builder=failed)

    assert report["decision"] == "bybit_canonical_v4_commissioning_blocked_input_quality"
    assert report["hard_failures"] == ["quality:post_floor_clock_offset_failures"]


def test_commissioning_window_closes_without_reading_quality_after_floor(tmp_path: Path) -> None:
    write_event(tmp_path / "BTCUSDT" / "20260713.jsonl", liquidation_ms=1783969200000)

    def forbidden(*_args, **_kwargs) -> dict:
        raise AssertionError("quality must not be inspected after the actual forward floor")

    report = module.build_report(
        lock(),
        tmp_path,
        observed_at=datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc),
        quality_builder=forbidden,
    )

    assert report["decision"] == "bybit_canonical_v4_commissioning_window_closed"
    assert report["quality_snapshot"] is None
    assert "commissioning_must_run_before_actual_forward_floor" in report["hard_failures"]

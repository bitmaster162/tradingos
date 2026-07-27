from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tools import bybit_liquidation_canonical_input_quality_v4 as module


def gate() -> dict:
    return {
        "required_ingest_schema_version": 3,
        "maximum_clock_calibration_rtt_ms": 2000,
        "maximum_clock_calibration_age_s": 600,
        "maximum_absolute_clock_offset_ms": 10000,
        "minimum_clock_calibration_samples": 3,
        "maximum_exchange_event_lag_ms": 2000,
        "maximum_corrected_receipt_lag_ms": 10000,
        "corrected_receipt_uncertainty_grace_ms": 50,
    }


def event(ts: str, *, sequence: int = 1, monotonic_ns: int = 1_000_000, tamper: bool = False) -> dict:
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    liquidation_ms = int(parsed.timestamp() * 1000)
    received_ns = (liquidation_ms - 300) * 1_000_000
    offset_ns = 400_000_000
    corrected_ns = received_ns + offset_ns + int(tamper)
    return {
        "event_time_ms": liquidation_ms + 20,
        "event_time": ts,
        "liquidation_time_ms": liquidation_ms,
        "liquidation_time": ts,
        "symbol": "BTCUSDT",
        "side": "BUY",
        "price": 100.0,
        "quantity": 1.0,
        "notional_usd": 100.0,
        "venue": "bybit",
        "source": "bybit_v5_allLiquidation_websocket",
        "is_real_liquidation_feed": True,
        "received_at_ns": received_ns,
        "received_at": ts,
        "received_monotonic_ns": monotonic_ns,
        "corrected_received_at_ns": corrected_ns,
        "corrected_received_at": ts,
        "collector_session_id": "session-1",
        "packet_sequence": sequence,
        "collector_host": "test",
        "collector_pid": 1,
        "collector_clock_source": "time.time_ns+time.perf_counter_ns+bybit_server_midpoint",
        "clock_calibration_id": "calibration-1",
        "clock_calibrated_at_ns": received_ns - 1_000_000_000,
        "clock_calibration_age_ns": 1_000_000_000,
        "clock_offset_ns": offset_ns,
        "clock_rtt_ns": 200_000_000,
        "clock_uncertainty_ns": 100_000_000,
        "clock_calibration_samples": 3,
        "clock_calibration_source": "https://api.bybit.com/v5/market/time",
        "ingest_schema_version": 3,
    }


def write_events(root: Path, rows: list[dict]) -> None:
    path = root / "BTCUSDT" / "20990103.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_calibrated_receipt_passes_when_raw_wall_clock_is_behind_exchange(tmp_path: Path) -> None:
    write_events(tmp_path, [event("2099-01-03T12:00:00Z")])

    report = module.scan_events(tmp_path, ["BTCUSDT"], "2099-01-03T00:00:00Z", gate())

    assert report["post_floor_events"] == 1
    assert report.get("post_floor_schema_errors", 0) == 0
    assert report.get("post_floor_corrected_before_event_failures", 0) == 0
    assert report["raw_receipt_lag_ms"]["min"] == -300.0
    assert report["corrected_receipt_lag_ms"]["min"] == 100.0


def test_tampered_correction_equation_is_rejected(tmp_path: Path) -> None:
    write_events(tmp_path, [event("2099-01-03T12:00:00Z", tamper=True)])

    report = module.scan_events(tmp_path, ["BTCUSDT"], "2099-01-03T00:00:00Z", gate())

    assert report["post_floor_correction_equation_failures"] == 1


def test_monotonic_packet_receipt_regression_is_rejected(tmp_path: Path) -> None:
    write_events(
        tmp_path,
        [
            event("2099-01-03T12:00:00Z", sequence=1, monotonic_ns=2_000_000),
            event("2099-01-03T12:00:01Z", sequence=2, monotonic_ns=1_000_000),
        ],
    )

    report = module.scan_events(tmp_path, ["BTCUSDT"], "2099-01-03T00:00:00Z", gate())

    assert report["post_floor_monotonic_receipt_failures"] == 1


def test_prefloor_schema_v2_row_is_diagnostic_only(tmp_path: Path) -> None:
    row = event("2099-01-03T12:00:00Z")
    row["ingest_schema_version"] = 2
    write_events(tmp_path, [row])

    report = module.scan_events(tmp_path, ["BTCUSDT"], "2099-01-04T00:00:00Z", gate())

    assert report["schema_errors"] == 1
    assert report.get("post_floor_schema_errors", 0) == 0
    assert report["gate_scope"]["pre_floor_and_schema_v2_rows_are_diagnostic_only"] is True

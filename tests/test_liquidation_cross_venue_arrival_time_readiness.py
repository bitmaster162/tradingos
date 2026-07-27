import json
from pathlib import Path

from tools.liquidation_cross_venue_arrival_time_readiness import evaluate, source_stats


GATES = {
    "minimum_events_per_venue": 2,
    "minimum_shared_symbols": 1,
    "minimum_overlapping_receipt_span_seconds": 1,
    "required_schema_version": 2,
    "required_clock_source": "time.time_ns",
    "same_collector_host_required": True,
    "minimum_arrival_field_ratio": 1.0,
    "maximum_large_negative_delay_ratio": 0.0,
    "large_negative_delay_ms": -2000.0,
    "maximum_p99_delivery_delay_ms": 5000.0,
}
REQUIRED = [
    "received_at_ns",
    "received_at",
    "collector_host",
    "collector_pid",
    "collector_clock_source",
    "ingest_schema_version",
]


def write_rows(root: Path, host: str, start_ns: int) -> None:
    path = root / "BTCUSDT" / "sample.jsonl"
    path.parent.mkdir(parents=True)
    rows = []
    for offset in (0, 2_000_000_000):
        received_ns = start_ns + offset
        rows.append(
            {
                "event_time_ms": received_ns // 1_000_000 - 100,
                "symbol": "BTCUSDT",
                "received_at_ns": received_ns,
                "received_at": "2027-01-15T08:00:00.000000Z",
                "collector_host": host,
                "collector_pid": 123,
                "collector_clock_source": "time.time_ns",
                "ingest_schema_version": 2,
            }
        )
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_arrival_time_gate_ready_on_same_host_and_overlap(tmp_path: Path):
    floor_ns = 1_800_000_000_000_000_000
    binance_dir = tmp_path / "binance"
    bybit_dir = tmp_path / "bybit"
    write_rows(binance_dir, "same-host", floor_ns + 1_000_000_000)
    write_rows(bybit_dir, "same-host", floor_ns + 1_500_000_000)

    binance = source_stats("binance", binance_dir, floor_ns, REQUIRED, GATES)
    bybit = source_stats("bybit", bybit_dir, floor_ns, REQUIRED, GATES)
    decision, blockers = evaluate({"gates": GATES}, binance, bybit)

    assert decision == "liquidation_arrival_time_ready_for_manual_preregistration_review"
    assert blockers == []


def test_arrival_time_gate_fails_closed_on_different_hosts(tmp_path: Path):
    floor_ns = 1_800_000_000_000_000_000
    binance_dir = tmp_path / "binance"
    bybit_dir = tmp_path / "bybit"
    write_rows(binance_dir, "host-a", floor_ns + 1_000_000_000)
    write_rows(bybit_dir, "host-b", floor_ns + 1_500_000_000)

    binance = source_stats("binance", binance_dir, floor_ns, REQUIRED, GATES)
    bybit = source_stats("bybit", bybit_dir, floor_ns, REQUIRED, GATES)
    decision, blockers = evaluate({"gates": GATES}, binance, bybit)

    assert decision == "liquidation_arrival_time_readiness_hard_fail"
    assert "collector_hosts_not_identical" in blockers

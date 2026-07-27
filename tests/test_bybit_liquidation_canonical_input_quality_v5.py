from __future__ import annotations

import json
from pathlib import Path

from tools.bybit_all_liquidation_real_feed_collector import capture_reception, synthetic_clock_calibration
from tools.bybit_all_liquidation_real_feed_collector_v2 import parse_bybit_message
from tools.bybit_liquidation_canonical_input_quality_v5 import packet_contract_scan


def rows() -> list[dict]:
    raw = {
        "topic": "allLiquidation.BTCUSDT",
        "type": "snapshot",
        "ts": 4_071_686_400_500,
        "data": [
            {"T": 4_071_686_400_100, "s": "BTCUSDT", "S": "Sell", "v": "0.004", "p": "65000.0"},
            {"T": 4_071_686_400_100, "s": "BTCUSDT", "S": "Sell", "v": "0.004", "p": "65000.0"},
        ],
    }
    reception = capture_reception(
        4_071_686_400_700_000_000,
        received_monotonic_ns=123_000,
        calibration=synthetic_clock_calibration(4_071_686_400_600_000_000),
        collector_session_id="session-v5",
        packet_sequence=9,
    )
    return parse_bybit_message(raw, reception)


def write(root: Path, payloads: list[dict]) -> None:
    path = root / "BTCUSDT" / "20990101.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(row) for row in payloads) + "\n", encoding="utf-8")


def test_identical_market_tuples_are_valid_when_packet_ordinals_are_unique(tmp_path: Path):
    write(tmp_path, rows())

    report = packet_contract_scan(tmp_path, ["BTCUSDT"], "2099-01-01T00:00:00Z", 4)

    assert report["post_floor_packet_rows"] == 2
    assert report["post_floor_packets"] == 1
    assert report.get("post_floor_duplicate_packet_item_identities", 0) == 0
    assert report.get("post_floor_packet_item_coverage_failures", 0) == 0
    assert report.get("post_floor_packet_item_payload_mismatches", 0) == 0


def test_repeated_physical_packet_item_is_rejected(tmp_path: Path):
    payloads = rows()
    payloads.append(dict(payloads[0]))
    write(tmp_path, payloads)

    report = packet_contract_scan(tmp_path, ["BTCUSDT"], "2099-01-01T00:00:00Z", 4)

    assert report["post_floor_duplicate_packet_item_identities"] == 1
    assert report["post_floor_packet_item_coverage_failures"] == 1

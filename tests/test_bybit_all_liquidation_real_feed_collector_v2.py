from tools.bybit_all_liquidation_real_feed_collector import SOURCE, capture_reception, synthetic_clock_calibration
from tools.bybit_all_liquidation_real_feed_collector_v2 import REQUIRED_FIELDS, parse_bybit_message


def test_packet_ordinals_preserve_identical_exchange_items_as_distinct_rows():
    raw = {
        "topic": "allLiquidation.BTCUSDT",
        "type": "snapshot",
        "ts": 1_800_000_000_500,
        "data": [
            {"T": 1_800_000_000_100, "s": "BTCUSDT", "S": "Sell", "v": "0.004", "p": "65000.0"},
            {"T": 1_800_000_000_100, "s": "BTCUSDT", "S": "Sell", "v": "0.004", "p": "65000.0"},
        ],
    }
    reception = capture_reception(
        1_800_000_000_700_000_000,
        received_monotonic_ns=123_000,
        calibration=synthetic_clock_calibration(1_800_000_000_600_000_000),
        collector_session_id="session-v4",
        packet_sequence=9,
    )

    rows = parse_bybit_message(raw, reception)

    assert len(rows) == 2
    assert rows[0]["packet_item_index"] == 0
    assert rows[1]["packet_item_index"] == 1
    assert {row["packet_item_count"] for row in rows} == {2}
    assert {row["ingest_schema_version"] for row in rows} == {4}
    assert {row["source"] for row in rows} == {SOURCE}
    assert all(REQUIRED_FIELDS.issubset(row) for row in rows)
    assert rows[0]["liquidation_time_ms"] == rows[1]["liquidation_time_ms"]
    assert rows[0]["quantity"] == rows[1]["quantity"]

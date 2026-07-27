from tools.bybit_all_liquidation_real_feed_collector import (
    REQUIRED_FIELDS,
    capture_reception,
    parse_bybit_message,
    sample_message,
    select_clock_calibration,
)


def test_parse_bybit_sample_has_arrival_time_contract_fields():
    calibration = select_clock_calibration(
        [
            {
                "local_start_ns": 1_800_000_000_000_000_000,
                "local_end_ns": 1_800_000_000_100_000_000,
                "monotonic_elapsed_ns": 100_000_000,
                "server_time_ns": 1_800_000_000_250_000_000,
            }
        ],
        calibration_id="test-calibration",
    )
    reception = capture_reception(
        1_800_000_000_123_456_789,
        received_monotonic_ns=123_456_789,
        calibration=calibration,
        collector_session_id="session-1",
        packet_sequence=7,
    )
    rows = parse_bybit_message(sample_message(), reception)

    assert len(rows) == 1
    row = rows[0]
    assert REQUIRED_FIELDS.issubset(row)
    assert row["symbol"] == "BTCUSDT"
    assert row["received_at_ns"] == 1_800_000_000_123_456_789
    assert row["received_monotonic_ns"] == 123_456_789
    assert row["corrected_received_at_ns"] == 1_800_000_000_323_456_789
    assert row["collector_session_id"] == "session-1"
    assert row["packet_sequence"] == 7
    assert row["clock_calibration_id"] == "test-calibration"
    assert row["clock_offset_ns"] == 200_000_000
    assert row["ingest_schema_version"] == 3
    assert row["is_real_liquidation_feed"] is True


def test_clock_calibration_selects_lowest_rtt_sample():
    calibration = select_clock_calibration(
        [
            {
                "local_start_ns": 1_000,
                "local_end_ns": 1_500,
                "monotonic_elapsed_ns": 500,
                "server_time_ns": 1_400,
            },
            {
                "local_start_ns": 2_000,
                "local_end_ns": 2_100,
                "monotonic_elapsed_ns": 100,
                "server_time_ns": 2_250,
            },
        ],
        calibration_id="best",
    )

    assert calibration["clock_calibration_id"] == "best"
    assert calibration["clock_rtt_ns"] == 100
    assert calibration["clock_offset_ns"] == 200
    assert calibration["clock_uncertainty_ns"] == 50
    assert calibration["clock_calibration_samples"] == 2

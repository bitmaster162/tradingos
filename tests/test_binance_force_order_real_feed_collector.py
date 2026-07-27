from tools.binance_force_order_real_feed_collector import (
    BINANCE_USDM_COMBINED_STREAM,
    DEFAULT_LIVENESS_STREAM,
    REQUIRED_FIELDS,
    build_streams,
    capture_reception,
    parse_force_order_message,
    parse_force_order_rows,
    sample_message,
)


def test_parse_force_order_sample_has_contract_fields():
    row = parse_force_order_message(sample_message())
    assert row is not None
    assert REQUIRED_FIELDS.issubset(row)
    assert row["symbol"] == "BTCUSDT"
    assert row["is_real_liquidation_feed"] is True
    assert row["notional_usd"] > 0


def test_force_order_stream_uses_current_market_namespace_and_liveness_canary():
    streams, symbols = build_streams("ALL", "all_market", DEFAULT_LIVENESS_STREAM)

    assert "/market/stream?streams=" in BINANCE_USDM_COMBINED_STREAM
    assert streams == ["!forceOrder@arr", "btcusdt@markPrice@1s"]
    assert symbols == ["ALL"]


def test_force_order_rows_preserve_one_packet_reception_timestamp():
    reception = capture_reception(1_800_000_000_123_456_789)
    rows = parse_force_order_rows(sample_message(), {"BTCUSDT"}, True, reception)

    assert len(rows) == 1
    assert rows[0]["received_at_ns"] == 1_800_000_000_123_456_789
    assert rows[0]["received_at"].endswith("Z")
    assert rows[0]["collector_clock_source"] == "time.time_ns"
    assert rows[0]["ingest_schema_version"] == 2

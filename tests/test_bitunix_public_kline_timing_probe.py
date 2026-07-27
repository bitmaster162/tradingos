from __future__ import annotations

from tools import bitunix_public_kline_timing_probe as module


def frame(ts: int, *, recv_ms: int, bucket: int) -> dict:
    return {
        "recv_ns": recv_ms * 1_000_000,
        "recv_ms": recv_ms,
        "server_ts_ms": ts,
        "bucket_start_ms": bucket,
        "interval_ms": 300_000,
    }


def test_parse_kline_frame_normalizes_public_payload() -> None:
    parsed, error = module.parse_kline_frame(
        {
            "ch": "market_kline_5min",
            "symbol": "BTCUSDT",
            "ts": 300_250,
            "data": {"o": "10", "h": "12", "l": "9", "c": "11", "b": "2", "q": "21"},
        },
        recv_ns=301_000_000_000,
        expected_symbol="BTCUSDT",
        expected_channel="market_kline_5min",
    )

    assert error is None
    assert parsed is not None
    assert parsed["bucket_start_ms"] == 300_000
    assert parsed["recv_minus_server_ms"] == 750
    assert parsed["payload"]["c"] == "11"


def test_parse_kline_frame_rejects_wrong_symbol_and_bad_numeric_field() -> None:
    wrong, wrong_error = module.parse_kline_frame(
        {"ch": "market_kline_5min", "symbol": "ETHUSDT", "ts": 1, "data": {}},
        recv_ns=1_000_000,
        expected_symbol="BTCUSDT",
        expected_channel="market_kline_5min",
    )
    bad, bad_error = module.parse_kline_frame(
        {
            "ch": "market_kline_5min",
            "symbol": "BTCUSDT",
            "ts": 1,
            "data": {"o": "bad", "h": "1", "l": "1", "c": "1", "b": "1", "q": "1"},
        },
        recv_ns=1_000_000,
        expected_symbol="BTCUSDT",
        expected_channel="market_kline_5min",
    )

    assert wrong is None and wrong_error == "symbol_mismatch"
    assert bad is None and bad_error == "kline_field_invalid:o"


def test_analysis_proves_bounded_rollover_confirmation() -> None:
    report = module.analyze_records(
        [
            frame(299_900, recv_ms=299_950, bucket=0),
            frame(300_100, recv_ms=301_250, bucket=300_000),
        ],
        latency_cutoff_ms=5000,
    )

    assert report["transition_count"] == 1
    assert report["transitions"][0]["close_confirmation_latency_ms"] == 1250
    assert report["all_observed_transitions_within_cutoff"] is True
    assert report["transitions"][0]["final_value_verified_against_later_rest"] is False


def test_analysis_fails_latency_without_overclaiming_final_value() -> None:
    report = module.analyze_records(
        [
            frame(299_900, recv_ms=299_950, bucket=0),
            frame(300_100, recv_ms=306_001, bucket=300_000),
        ],
        latency_cutoff_ms=5000,
    )

    assert report["transition_count"] == 1
    assert report["transitions"][0]["within_cutoff"] is False
    assert report["all_observed_transitions_within_cutoff"] is False

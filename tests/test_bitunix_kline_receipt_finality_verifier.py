from __future__ import annotations

from tools import bitunix_kline_receipt_finality_verifier as module


def ws_row(bucket: int, recv_ms: int, close: str = "11") -> dict:
    return {
        "bucket_start_ms": bucket,
        "recv_ns": recv_ms * 1_000_000,
        "recv_ms": recv_ms,
        "payload": {"o": "10", "h": "12", "l": "9", "c": close, "b": "2", "q": "21"},
    }


def transition(bucket: int, *, timely: bool = True) -> dict:
    return {
        "closed_bucket_start_ms": bucket,
        "boundary_ms": bucket + 300_000,
        "within_cutoff": timely,
    }


def rest_row(bucket: int, close: str = "11") -> dict:
    return {"time": bucket, "open": "10.0", "high": "12", "low": "9.00", "close": close}


def test_finality_verifier_accepts_equal_ohlc_and_timely_transition() -> None:
    report = module.verify_final_values(
        [ws_row(0, 299_900)],
        [transition(0)],
        [rest_row(0)],
    )

    assert report["blockers"] == []
    assert report["comparisons"][0]["final_ohlc_equal"] is True
    assert report["comparisons"][0]["ws_ohlc"]["close"] == "11"
    assert report["comparisons"][0]["rest_ohlc"]["open"] == "10.0"
    assert report["timely_final_ohlc_verified"] is True
    assert report["volume_finality_verified"] is False


def test_finality_verifier_rejects_price_mismatch() -> None:
    report = module.verify_final_values(
        [ws_row(0, 299_900)],
        [transition(0)],
        [rest_row(0, close="11.1")],
    )

    assert report["blockers"] == ["final_ohlc_mismatch:0"]
    assert report["timely_final_ohlc_verified"] is False


def test_finality_verifier_rejects_late_transition_even_when_values_match() -> None:
    report = module.verify_final_values(
        [ws_row(0, 306_000)],
        [transition(0, timely=False)],
        [rest_row(0)],
    )

    assert report["blockers"] == []
    assert report["comparisons"][0]["final_ohlc_equal"] is True
    assert report["timely_final_ohlc_verified"] is False

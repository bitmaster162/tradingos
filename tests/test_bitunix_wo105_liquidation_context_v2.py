from __future__ import annotations

import pytest

from tools import bitunix_wo105_liquidation_context_v2 as module


def row(event_ms: int, side: str, notional: float, *, receive_minus_event_ms: int = 50) -> dict:
    price = 100.0
    return {
        "event_time_ms": event_ms,
        "symbol": "BTCUSDT",
        "side": side,
        "price": price,
        "quantity": notional / price,
        "notional_usd": notional,
        "source": module.SOURCE,
        "is_real_liquidation_feed": True,
        "received_at_ns": (event_ms + receive_minus_event_ms) * 1_000_000,
        "ingest_schema_version": 2,
    }


def test_bounded_clock_lead_uses_later_timestamp_as_causal_availability() -> None:
    floor = 2_000_000_000_000
    cutoff = floor + 60_000
    candidate = row(cutoff - 100, "SELL", 2_000.0, receive_minus_event_ms=-55)

    normalized, failure = module.validate_row(candidate, floor_ms=floor, cutoff_ms=cutoff)

    assert failure is None
    assert normalized is not None
    assert normalized["raw_received_at_ms"] == cutoff - 155
    assert normalized["event_time_ms"] == cutoff - 100
    assert normalized["received_at_ms"] == cutoff - 100
    assert normalized["causal_available_ms"] == cutoff - 100
    assert normalized["clock_lead_ms"] == 55


def test_clock_lead_beyond_frozen_bound_fails_closed() -> None:
    floor = 2_000_000_000_000
    cutoff = floor + 60_000
    candidate = row(cutoff - 100, "BUY", 2_000.0, receive_minus_event_ms=-5_001)

    normalized, failure = module.validate_row(candidate, floor_ms=floor, cutoff_ms=cutoff)

    assert normalized is None
    assert failure == "event_after_receipt_beyond_clock_skew"


def test_causal_availability_cannot_cross_cutoff() -> None:
    floor = 2_000_000_000_000
    cutoff = floor + 60_000
    candidate = row(cutoff + 1, "BUY", 2_000.0, receive_minus_event_ms=-55)

    normalized, failure = module.validate_row(candidate, floor_ms=floor, cutoff_ms=cutoff)

    assert normalized is None
    assert failure == "after_cutoff"


def test_realistic_clock_lead_can_form_context_without_lookahead() -> None:
    floor = 2_000_000_000_000
    cutoff = floor + 60_000
    rows = [
        row(cutoff - 3_000, "SELL", 1_000.0, receive_minus_event_ms=-59),
        row(cutoff - 2_000, "SELL", 500.0, receive_minus_event_ms=-55),
        row(cutoff - 1_000, "BUY", 500.0, receive_minus_event_ms=-55),
    ]

    result = module.build_context(rows, floor_ms=floor, cutoff_ms=cutoff)

    assert result["blockers"] == []
    assert result["accepted_events"] == 3
    assert result["maximum_observed_clock_lead_ms"] == 59
    assert result["causal_availability_rule"] == "max(event_time_ms,raw_received_at_ms)"
    record = result["record"]
    assert record is not None
    assert record["received_at"] >= record["observed_at"]
    assert record["payload"]["value"] == (500.0 - 1_500.0) / 2_000.0


def test_invalid_clock_bound_is_rejected() -> None:
    floor = 2_000_000_000_000
    with pytest.raises(ValueError, match="non-negative integer"):
        module.validate_row(row(floor + 1, "BUY", 1_000.0), floor_ms=floor, cutoff_ms=floor + 2, max_clock_skew_ms=-1)

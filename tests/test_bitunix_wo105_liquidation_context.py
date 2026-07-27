from __future__ import annotations

from tools import bitunix_wo105_liquidation_context as module


def row(event_ms: int, side: str, notional: float, *, received_delay_ms: int = 50) -> dict:
    price = 100.0
    quantity = notional / price
    return {
        "event_time_ms": event_ms,
        "event_time": "unused",
        "trade_time_ms": event_ms,
        "symbol": "BTCUSDT",
        "side": side,
        "price": price,
        "quantity": quantity,
        "notional_usd": notional,
        "source": module.SOURCE,
        "is_real_liquidation_feed": True,
        "received_at_ns": (event_ms + received_delay_ms) * 1_000_000,
        "received_at": "unused",
        "collector_host": "test",
        "collector_pid": 1,
        "ingest_schema_version": 2,
    }


def test_real_post_floor_rows_produce_canonical_signed_notional_share() -> None:
    floor = 2_000_000_000_000
    cutoff = floor + 60 * 60 * 1000
    rows = [
        row(cutoff - 30_000, "BUY", 700.0),
        row(cutoff - 20_000, "BUY", 300.0),
        row(cutoff - 10_000, "SELL", 500.0),
    ]

    result = module.build_context(rows, floor_ms=floor, cutoff_ms=cutoff)

    assert result["blockers"] == []
    record = result["record"]
    assert record is not None
    assert record["payload"]["unit"] == "signed_notional_share"
    assert record["payload"]["value"] == (1000.0 - 500.0) / 1500.0
    assert record["payload"]["side_semantics"] == {"BUY": "liquidated_SHORT", "SELL": "liquidated_LONG"}


def test_pre_floor_and_after_cutoff_rows_cannot_pad_context() -> None:
    floor = 2_000_000_000_000
    cutoff = floor + 60_000
    rows = [
        row(floor - 1, "BUY", 10_000.0),
        row(cutoff + 1, "BUY", 10_000.0),
        row(cutoff - 100, "SELL", 2_000.0),
    ]

    result = module.build_context(rows, floor_ms=floor, cutoff_ms=cutoff)

    assert result["record"] is None
    assert "minimum_events_not_met:1<3" in result["blockers"]
    assert result["rejection_counts"]["pre_floor"] == 1
    assert result["rejection_counts"]["after_cutoff"] == 1


def test_proxy_or_wrong_schema_rows_are_rejected() -> None:
    floor = 2_000_000_000_000
    cutoff = floor + 60_000
    invalid = row(cutoff - 1, "BUY", 10_000.0)
    invalid["is_real_liquidation_feed"] = False
    old_schema = row(cutoff - 2, "SELL", 10_000.0)
    old_schema["ingest_schema_version"] = 1

    result = module.build_context([invalid, old_schema], floor_ms=floor, cutoff_ms=cutoff)

    assert result["record"] is None
    assert result["accepted_events"] == 0
    assert result["rejection_counts"] == {
        "ingest_schema_invalid": 1,
        "source_or_real_feed_flag_invalid": 1,
    }


def test_notional_must_be_recomputable() -> None:
    floor = 2_000_000_000_000
    cutoff = floor + 60_000
    invalid = row(cutoff - 100, "BUY", 1000.0)
    invalid["notional_usd"] = 2000.0

    normalized, failure = module.validate_row(invalid, floor_ms=floor, cutoff_ms=cutoff)

    assert normalized is None
    assert failure == "notional_not_recomputable"

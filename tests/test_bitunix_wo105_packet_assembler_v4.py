from __future__ import annotations

from tools import bitunix_wo105_liquidation_context_v2 as liquidation_v2
from tools import bitunix_wo105_packet_assembler_v4 as module


def test_v4_reuses_v3_lifecycle_with_clock_skew_bounded_adapter(monkeypatch) -> None:
    monkeypatch.setattr(module.assembler_v3, "liquidation", object())
    monkeypatch.setattr(module.assembler_v3, "TOOL_PATH", "sentinel")

    configured = module.configure_for_v4()

    assert configured.liquidation is liquidation_v2
    assert configured.TOOL_PATH == "tools/bitunix_wo105_packet_assembler_v4.py"
    assert liquidation_v2.DEFAULT_MAX_CLOCK_SKEW_MS == 5_000


def test_v4_adapter_remains_no_trade() -> None:
    floor = 2_000_000_000_000
    event = {
        "event_time_ms": floor + 100,
        "symbol": "BTCUSDT",
        "side": "SELL",
        "price": 100.0,
        "quantity": 10.0,
        "notional_usd": 1_000.0,
        "source": liquidation_v2.SOURCE,
        "is_real_liquidation_feed": True,
        "received_at_ns": (floor + 50) * 1_000_000,
        "ingest_schema_version": 2,
    }

    result = liquidation_v2.build_context(
        [event, {**event, "event_time_ms": floor + 200, "received_at_ns": (floor + 150) * 1_000_000},
         {**event, "event_time_ms": floor + 300, "received_at_ns": (floor + 250) * 1_000_000}],
        floor_ms=floor,
        cutoff_ms=floor + 500,
    )

    assert result["record"] is not None
    assert result["record"]["received_at"] >= result["record"]["observed_at"]

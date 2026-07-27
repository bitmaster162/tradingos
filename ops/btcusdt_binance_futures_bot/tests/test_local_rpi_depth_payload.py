from decimal import Decimal

from btcusdt_bot.simulator.depth_book import DepthBookSnapshot, DepthLevel


def test_depth_snapshot_can_serialize_as_local_rpi_depth_snapshot() -> None:
    snapshot = DepthBookSnapshot(
        event_time_ms=1_700_000_000_000,
        transaction_time_ms=1_700_000_000_000,
        last_update_id=123,
        levels=2,
        bids=[DepthLevel(Decimal("100.0"), Decimal("1.0"))],
        asks=[DepthLevel(Decimal("100.5"), Decimal("1.5"))],
    )

    payload = snapshot.to_payload(symbol="BTCUSDT", event_type="localRpiDepthSnapshot")

    assert payload["e"] == "localRpiDepthSnapshot"
    assert payload["s"] == "BTCUSDT"
    assert payload["levels"] == 2
    assert payload["bids"] == [["100.0", "1.0"]]
    assert payload["asks"] == [["100.5", "1.5"]]

from __future__ import annotations

from tools import bitunix_wo105_public_rest_collector as module


NOW = 1_800_000_000_000


def kline_rows(interval: str, count: int) -> list[dict]:
    step = module.INTERVAL_MS[interval]
    latest_open = NOW - step
    rows = []
    for index in range(count):
        open_ms = latest_open - index * step
        price = 60_000.0 - index
        rows.append(
            {
                "time": str(open_ms),
                "open": str(price),
                "high": str(price + 10),
                "low": str(price - 10),
                "close": str(price + 1),
                "quoteVol": "1",
                "baseVol": "60000",
            }
        )
    return rows


def requester_factory() -> module.Requester:
    histories = {interval: kline_rows(interval, 260) for interval in module.INTERVAL_MS}

    def requester(endpoint: str, params: dict) -> tuple[dict, dict]:
        receipt = {
            "endpoint": endpoint,
            "received_at": NOW,
            "started_at": NOW - 5,
            "body_sha256": "a" * 64,
            "credentials_used": 0,
            "private_calls": 0,
            "order_calls": 0,
        }
        if endpoint == "kline":
            interval = params["interval"]
            end_time = params.get("endTime")
            rows = histories[interval]
            if end_time is not None:
                rows = [row for row in rows if int(row["time"]) <= int(end_time)]
            return {"code": 0, "data": rows[: int(params["limit"])], "msg": "Success"}, receipt
        if endpoint == "funding":
            return {
                "code": 0,
                "data": {
                    "symbol": "BTCUSDT",
                    "markPrice": "60000",
                    "lastPrice": "60001",
                    "indexPrice": "59999",
                    "fundingRate": "0.01",
                    "fundingInterval": 8,
                    "nextFundingTime": str(NOW + 8 * 3_600_000),
                },
                "msg": "Success",
            }, receipt
        if endpoint == "depth":
            return {
                "code": 0,
                "data": {"bids": [["60000", "1"], ["59999.9", "2"]], "asks": [["60000.1", "1"]]},
                "msg": "Success",
            }, receipt
        raise AssertionError(endpoint)

    return requester


def test_kline_pagination_returns_only_closed_native_bars() -> None:
    rows, receipts, failures = module.fetch_closed_klines(
        requester_factory(), symbol="BTCUSDT", interval="4h", required=220
    )

    assert len(rows) == 220
    assert len(receipts) == 2
    assert failures == []
    assert rows == sorted(rows, key=lambda item: item["payload"]["close_ms"])
    assert all(row["payload"]["close_ms"] <= row["received_at"] for row in rows)
    assert all(row["schema_version"] == "ohlcv-bar-v1" for row in rows)


def test_funding_api_percentage_points_are_explicitly_normalized() -> None:
    requester = requester_factory()
    envelope, receipt = requester("funding", {"symbol": "BTCUSDT"})
    raw, crowd, event = module.funding_records(envelope, receipt, symbol="BTCUSDT")

    assert raw["payload"]["funding_rate_api"] == 0.01
    assert raw["payload"]["api_unit"] == "percentage_points"
    assert crowd["payload"]["value"] == 0.0001
    assert crowd["payload"]["unit"] == "decimal_fraction"
    assert event["payload"]["rate"] == 0.0001
    assert event["payload"]["unit"] == "decimal_fraction"


def test_snapshot_is_public_only_and_not_an_evaluator_packet() -> None:
    snapshot = module.build_snapshot(
        requester_factory(),
        symbol="BTCUSDT",
        required={"5m": 20, "1h": 24, "4h": 203},
        forward_floor_ms=NOW + 1,
    )

    assert snapshot["decision"] == "bitunix_wo105_public_rest_snapshot_collected"
    assert snapshot["snapshot_phase"] == "COMMISSIONING_PRE_FLOOR"
    assert snapshot["source_contract"]["native_public_oi_available"] is False
    assert snapshot["source_contract"]["rest_depth_evaluator_admission_allowed"] is False
    assert snapshot["evaluator_packet_ready"] is False
    assert snapshot["can_trade"] is False


def test_malformed_depth_and_funding_fail_snapshot_closed() -> None:
    base = requester_factory()

    def malformed(endpoint: str, params: dict) -> tuple[dict, dict]:
        envelope, receipt = base(endpoint, params)
        if endpoint == "funding":
            envelope["data"]["fundingRate"] = "NaN"
        if endpoint == "depth":
            envelope["data"]["bids"] = []
        return envelope, receipt

    snapshot = module.build_snapshot(
        malformed,
        symbol="BTCUSDT",
        required={"5m": 20, "1h": 24, "4h": 203},
        forward_floor_ms=NOW + 1,
    )

    assert snapshot["decision"] == "bitunix_wo105_public_rest_snapshot_partial_hold"
    assert any(item.startswith("funding:") for item in snapshot["failures"])
    assert any(item.startswith("depth:") for item in snapshot["failures"])
    assert snapshot["can_trade"] is False


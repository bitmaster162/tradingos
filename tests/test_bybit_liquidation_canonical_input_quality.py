from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tools import bybit_liquidation_canonical_input_quality as module
from tools.liquidity_sweep_detector import OhlcvBar


def event(ts: str, *, schema_v2: bool = True) -> dict:
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    timestamp_ms = int(parsed.timestamp() * 1000)
    payload = {
        "event_time_ms": timestamp_ms + 10,
        "event_time": ts,
        "liquidation_time_ms": timestamp_ms,
        "liquidation_time": ts,
        "symbol": "BTCUSDT",
        "side": "BUY",
        "price": 100.0,
        "quantity": 1.0,
        "notional_usd": 100.0,
        "venue": "bybit",
        "source": "bybit_v5_allLiquidation_websocket",
        "is_real_liquidation_feed": True,
    }
    if schema_v2:
        payload.update(
            {
                "received_at_ns": (timestamp_ms + 20) * 1_000_000,
                "collector_host": "test-host",
                "ingest_schema_version": 2,
            }
        )
    return payload


def write_events(root: Path, day: str, rows: list[dict]) -> None:
    path = root / "BTCUSDT" / f"{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_filter_fully_closed_bars_excludes_current_interval() -> None:
    bars = [
        OhlcvBar(index=index, ts=f"2099-01-01T0{index}:00:00Z", open=1, high=1, low=1, close=1, volume=1)
        for index in range(3)
    ]

    closed, excluded = module.filter_fully_closed_bars(
        bars,
        now=datetime(2099, 1, 1, 2, 30, tzinfo=timezone.utc),
        interval="1h",
    )

    assert [bar.index for bar in closed] == [0, 1]
    assert excluded == 1


def test_historical_schema_defect_is_diagnostic_not_post_floor_gate(tmp_path: Path) -> None:
    write_events(tmp_path, "20990101", [event("2099-01-01T12:00:00Z", schema_v2=False)])

    report = module.scan_events(tmp_path, ["BTCUSDT"], "2099-01-02T00:00:00Z")

    assert report["events"] == 1
    assert report["schema_errors"] == 1
    assert report["post_floor_events"] == 0
    assert report["post_floor_schema_errors"] == 0
    assert report["gate_scope"]["historical_metrics_are_diagnostic_only"] is True


def test_post_floor_duplicate_is_a_hard_gate_metric(tmp_path: Path) -> None:
    duplicate = event("2099-01-03T12:00:00Z")
    write_events(tmp_path, "20990103", [duplicate, duplicate])

    report = module.scan_events(tmp_path, ["BTCUSDT"], "2099-01-02T00:00:00Z")

    assert report["post_floor_events"] == 2
    assert report["post_floor_duplicate_event_identities"] == 1


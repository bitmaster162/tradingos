from __future__ import annotations

import csv
from datetime import datetime, timezone

from tools.liquidation_force_order_major_cache_refresh import tail_status


def write_tail(path, time_ms: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["time", "time_ms", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerow({"time": "x", "time_ms": time_ms, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1})


def test_tail_status_marks_stale_and_fresh_symbols(tmp_path) -> None:
    observed = datetime(2026, 7, 12, 4, 30, tzinfo=timezone.utc)
    write_tail(tmp_path / "futures" / "BTCUSDT" / "1h_klines.csv", int(datetime(2026, 7, 12, 4, tzinfo=timezone.utc).timestamp() * 1000))
    write_tail(tmp_path / "futures" / "ETHUSDT" / "1h_klines.csv", int(datetime(2026, 7, 12, 1, tzinfo=timezone.utc).timestamp() * 1000))

    rows = tail_status(tmp_path, ["BTCUSDT", "ETHUSDT"], "1h", observed, 130.0)

    assert rows[0]["fresh"] is True
    assert rows[1]["fresh"] is False

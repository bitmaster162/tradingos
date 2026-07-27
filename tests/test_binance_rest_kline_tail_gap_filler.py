from __future__ import annotations

import csv
from types import SimpleNamespace

from tools import binance_rest_kline_tail_gap_filler as filler


def test_fill_symbol_refetches_and_replaces_last_tail_candle_without_backup(tmp_path, monkeypatch) -> None:
    output = tmp_path / "futures" / "BTCUSDT" / "1h_klines.csv"
    output.parent.mkdir(parents=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=filler.FIELDNAMES)
        writer.writeheader()
        writer.writerow(
            {
                "time": "1970-01-01T01:00:00+00:00",
                "time_ms": "3600000",
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100",
                "volume": "10",
            }
        )
    starts = []

    def fake_fetch(**kwargs):
        starts.append(kwargs["start_ms"])
        return [
            [3600000, "100", "105", "98", "104", "20"],
            [7200000, "104", "106", "103", "105", "15"],
        ]

    monkeypatch.setattr(filler, "fetch_klines", fake_fetch)
    args = SimpleNamespace(
        interval="1h",
        cache_dir=str(tmp_path),
        market="futures",
        start="",
        end="1970-01-01T02:00:00+00:00",
        limit=1000,
        max_pages=1,
        timeout=1,
        sleep_sec=0.0,
        dry_run=False,
        no_backup=True,
    )

    result = filler.fill_symbol(args, "BTCUSDT")
    rows = filler.read_existing(output)

    assert starts == [3600000]
    assert result["overlap_rows_existing_replaced"] == 1
    assert result["backup_path"] is None
    assert rows[0]["close"] == "104.0"
    assert rows[-1]["time_ms"] == "7200000"
    assert not (output.parent / "_rest_tail_backup").exists()

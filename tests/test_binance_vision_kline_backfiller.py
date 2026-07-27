from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from binance_vision_kline_backfiller import archive_base_url, merge_rows, normalize_timestamp, parse_archive  # noqa: E402


class BinanceVisionKlineBackfillerTests(unittest.TestCase):
    def test_selects_official_archive_root_by_market(self) -> None:
        self.assertIn("/data/spot/monthly/klines", archive_base_url("spot"))
        self.assertIn("/data/futures/um/monthly/klines", archive_base_url("futures"))

    def test_parses_headerless_kline_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "BTCUSDT-1h-2021-01.zip"
            with ZipFile(path, "w") as archive:
                archive.writestr(
                    "BTCUSDT-1h-2021-01.csv",
                    "1609459200000,29000,29100,28900,29050,100,1609462799999,0,1,0,0,0\n",
                )
            rows, stats = parse_archive(path)
        self.assertIsNone(stats["error"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["time_ms"], "1609459200000")
        self.assertEqual(rows[0]["close"], "29050.0")

    def test_normalizes_microsecond_timestamp(self) -> None:
        self.assertEqual(normalize_timestamp("1609459200000000"), 1609459200000)

    def test_existing_api_row_wins_overlap(self) -> None:
        historical = [{"time": "old", "time_ms": "1609459200000", "open": "1", "high": "1", "low": "1", "close": "1", "volume": "1"}]
        existing = [{"time": "new", "time_ms": "1609459200000", "open": "2", "high": "2", "low": "2", "close": "2", "volume": "2"}]
        merged, overlap = merge_rows(historical, existing)
        self.assertEqual(overlap, 1)
        self.assertEqual(merged[0]["close"], "2")


if __name__ == "__main__":
    unittest.main()

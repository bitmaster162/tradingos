from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from tools.binance_vision_funding_backfiller import merge_rows, parse_archive


class BinanceVisionFundingBackfillerTests(unittest.TestCase):
    def test_parses_funding_archive_header(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "BTCUSDT-fundingRate-2021-01.zip"
            with ZipFile(path, "w") as archive:
                archive.writestr(
                    "BTCUSDT-fundingRate-2021-01.csv",
                    "calc_time,funding_interval_hours,last_funding_rate\n1609459200002,8,0.00022753\n",
                )
            rows, stats = parse_archive(path)
        self.assertIsNone(stats["error"])
        self.assertEqual(rows[0]["timestamp"], "1609459200002")
        self.assertEqual(rows[0]["funding"], "0.00022753")

    def test_existing_api_row_wins_overlap(self) -> None:
        historical = [{"timestamp": "1609459200002", "funding": "0.1", "price": "nan"}]
        existing = [{"timestamp": "1609459200002", "funding": "0.2", "price": "30000"}]
        merged, overlap = merge_rows(historical, existing)
        self.assertEqual(overlap, 1)
        self.assertEqual(merged[0]["funding"], "0.2")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from binance_vision_crowd_backfiller import merge_rows, parse_metrics_zip  # noqa: E402


CSV_TEXT = """create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio
2021-01-01 00:00:00,BTCUSDT,1,1,1.5,1.2,1.6,0.4
2021-01-01 00:05:00,BTCUSDT,1,1,9.5,9.2,9.6,0.4
2021-01-01 00:15:00,BTCUSDT,1,1,1.7,1.3,1.8,0.4
"""


class BinanceVisionCrowdBackfillerTests(unittest.TestCase):
    def test_uses_earliest_snapshot_in_each_bar(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "BTCUSDT-metrics-2021-01-01.zip"
            with ZipFile(path, "w") as archive:
                archive.writestr("BTCUSDT-metrics-2021-01-01.csv", CSV_TEXT)
            buckets = {"15m": {}, "1h": {}, "4h": {}}
            stats = parse_metrics_zip(path, buckets)
        self.assertIsNone(stats["error"])
        first = buckets["15m"][min(buckets["15m"])]
        self.assertEqual(first["global_long_short_ratio"], "1.6")
        self.assertEqual(first["top_account_long_short_ratio"], "1.5")
        self.assertEqual(first["top_position_long_short_ratio"], "1.2")
        self.assertEqual(len(buckets["15m"]), 2)
        self.assertEqual(len(buckets["1h"]), 1)

    def test_existing_api_row_wins_overlap(self) -> None:
        historical = [{"timestamp": "1000", "time": "x", "global_long_short_ratio": "1.1", "source": "binance_vision_daily_metrics"}]
        existing = [{"timestamp": "1000", "time": "x", "global_long_short_ratio": "2.2", "source": "binance_futures_data_api"}]
        merged, overlap = merge_rows(existing, historical)
        self.assertEqual(overlap, 1)
        self.assertEqual(merged[0]["global_long_short_ratio"], "2.2")
        self.assertEqual(merged[0]["source"], "binance_futures_data_api")


if __name__ == "__main__":
    unittest.main()

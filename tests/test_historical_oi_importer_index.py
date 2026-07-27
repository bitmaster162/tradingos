import unittest

from tools.historical_oi_importer import latest_fresh_record, raw_oi_delta_context_available


class HistoricalOiImporterIndexTests(unittest.TestCase):
    def test_indexed_lookup_matches_causal_latest_record(self) -> None:
        rows = [
            {"timestamp": 1_000, "open_interest": 10.0},
            {"timestamp": 2_000, "open_interest": 11.0},
            {"timestamp": 3_000, "open_interest": 12.0},
        ]
        timestamps = [1_000, 2_000, 3_000]

        result = latest_fresh_record(rows, 2_500, max_staleness_ms=600, timestamps=timestamps)

        self.assertEqual(result, rows[1])

    def test_indexed_delta_context_respects_lookback(self) -> None:
        hour = 3_600_000
        rows = [
            {"timestamp": 0, "open_interest": 100.0},
            {"timestamp": hour, "open_interest": 95.0},
            {"timestamp": 2 * hour, "open_interest": 90.0},
            {"timestamp": 3 * hour, "open_interest": 85.0},
        ]
        timestamps = [int(row["timestamp"]) for row in rows]

        available = raw_oi_delta_context_available(
            rows,
            3 * hour,
            interval="1h",
            lookback=3,
            max_staleness_bars=1.0,
            timestamps=timestamps,
        )

        self.assertTrue(available)


if __name__ == "__main__":
    unittest.main()

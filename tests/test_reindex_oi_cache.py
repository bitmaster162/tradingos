import unittest

from tools.reindex_oi_cache import sample_oi_at_bar_close


class ReindexOiCacheTests(unittest.TestCase):
    def test_samples_latest_value_available_by_bar_close(self) -> None:
        bars = [
            {"time_ms": "0"},
            {"time_ms": "3600000"},
            {"time_ms": "7200000"},
        ]
        source = [
            {"timestamp": 3_300_000, "open_interest": 10.0},
            {"timestamp": 6_900_000, "open_interest": 20.0},
            {"timestamp": 10_500_000, "open_interest": 30.0},
        ]

        sampled = sample_oi_at_bar_close(bars, source, interval="1h", max_staleness_bars=1.0)

        self.assertEqual(
            sampled,
            [
                {"timestamp": 3_600_000, "open_interest": 20.0},
                {"timestamp": 7_200_000, "open_interest": 30.0},
            ],
        )

    def test_rejects_stale_source_observation(self) -> None:
        bars = [{"time_ms": "7200000"}]
        source = [{"timestamp": 1_000_000, "open_interest": 10.0}]

        sampled = sample_oi_at_bar_close(bars, source, interval="1h", max_staleness_bars=1.0)

        self.assertEqual(sampled, [])


if __name__ == "__main__":
    unittest.main()

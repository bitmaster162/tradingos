import importlib.util
import sys
import unittest
from pathlib import Path


PATH = Path(__file__).with_name("evaluate_cycle02.py")
SPEC = importlib.util.spec_from_file_location("cycle02", PATH)
M = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)


def bars(count, interval=3_600_000, start=1_700_000_000_000, step=1.0):
    return [
        M.Bar(start + index * interval, 100 + index * step, 101 + index * step, 99 + index * step, 100 + index * step)
        for index in range(count)
    ]


class Cycle02Tests(unittest.TestCase):
    def test_timestamp_microseconds_normalize(self):
        self.assertEqual(M.normalize_ms(1_735_689_600_000_000), 1_735_689_600_000)

    def test_cost_is_deducted_once(self):
        item = M.trade(1, M.Bar(2, 100, 100, 100, 100), M.Bar(3, 101, 101, 101, 101), 1, 0.0024)
        self.assertAlmostEqual(item.gross_return, 0.01)
        self.assertAlmostEqual(item.net_return, 0.0076)

    def test_funding_uses_distinct_entry_exit_and_no_overlap(self):
        sample = bars(30)
        event = sample[1].ts
        trades = M.funding_reversal([(event, 0.001), (event + 1, 0.001)], sample)
        self.assertEqual(len(trades), 1)
        self.assertGreater(trades[0].entry_ts, trades[0].signal_ts)
        self.assertGreater(trades[0].exit_ts, trades[0].entry_ts)

    def test_disposition_fails_closed_on_small_sample(self):
        metrics = {
            "n": 19,
            "net_mean": 1.0,
            "net_median": 1.0,
            "bootstrap_95_lower_mean": 1.0,
            "profit_factor": 2.0,
            "max_positive_quarter_concentration": 0.2,
        }
        self.assertEqual(M.disposition("H01", metrics)[0], "INSUFFICIENT_DATA")
        self.assertIn("20 additional", M.next_requirement("H01", "INSUFFICIENT_DATA", 0))

    def test_compression_entry_exit_are_separate(self):
        sample = [M.Bar(i * 3_600_000, 100, 100.5, 99.5, 100) for i in range(40)]
        sample[24] = M.Bar(24 * 3_600_000, 100, 102, 100, 101)
        result = M.compression_breakout(sample)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].exit_ts - result[0].entry_ts, 12 * 3_600_000)


if __name__ == "__main__":
    unittest.main()

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


PATH = Path(__file__).with_name("evaluate_cycle03.py")
SPEC = importlib.util.spec_from_file_location("cycle03", PATH)
M = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)


def bars(count, interval=3_600_000, start=1_767_225_600_000, step=1.0):
    return [
        M.Bar(
            start + index * interval,
            100 + index * step,
            101 + index * step,
            99 + index * step,
            100 + index * step,
        )
        for index in range(count)
    ]


class Cycle03Tests(unittest.TestCase):
    def test_frozen_plan_has_exactly_24_sources(self):
        plan = json.loads(
            Path(__file__).with_name("FROZEN_SOURCE_PLAN.json").read_text(encoding="utf-8")
        )
        records = M.expand_plan(plan)
        self.assertEqual(len(records), 24)
        self.assertEqual(len({item["source_id"] for item in records}), 24)

    def test_timestamp_microseconds_normalize(self):
        self.assertEqual(M.normalize_ms(1_735_689_600_000_000), 1_735_689_600_000)

    def test_cost_is_deducted_once(self):
        item = M.trade(
            1,
            M.Bar(2, 100, 100, 100, 100),
            M.Bar(3, 101, 101, 101, 101),
            1,
            0.0024,
        )
        self.assertAlmostEqual(item.gross_return, 0.01)
        self.assertAlmostEqual(item.net_return, 0.0076)

    def test_funding_uses_distinct_entry_exit_and_no_overlap(self):
        sample = bars(30)
        event = sample[1].ts
        trades = M.funding_reversal([(event, 0.001), (event + 1, 0.001)], sample)
        self.assertEqual(len(trades), 1)
        self.assertGreater(trades[0].entry_ts, trades[0].signal_ts)
        self.assertGreater(trades[0].exit_ts, trades[0].entry_ts)

    def test_merge_rejects_duplicate(self):
        item = M.Trade(1, 2, 3, 1, 0.01, 0.0012, 0.0088)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            M.merge_ledgers([item], [item], 0.0012)

    def test_merge_rejects_cross_period_overlap(self):
        prior = M.Trade(1, 2, 5, 1, 0.01, 0.0012, 0.0088)
        extension = M.Trade(3, 4, 6, 1, 0.01, 0.0012, 0.0088)
        with self.assertRaisesRegex(ValueError, "overlapping"):
            M.merge_ledgers([prior], [extension], 0.0012)

    def test_disposition_fails_closed_on_small_sample(self):
        result = {
            "n": 19,
            "net_mean": 1.0,
            "net_median": 1.0,
            "bootstrap_95_lower_mean": 1.0,
            "profit_factor": 2.0,
            "max_positive_quarter_concentration": 0.2,
        }
        self.assertEqual(M.disposition("H01", result)[0], "INSUFFICIENT_DATA")
        self.assertIn("20 additional", M.next_requirement("H01", "INSUFFICIENT_DATA", 0))

    def test_disposition_kills_missing_concentration(self):
        result = {
            "n": 100,
            "net_mean": 1.0,
            "net_median": 1.0,
            "bootstrap_95_lower_mean": 1.0,
            "profit_factor": None,
            "max_positive_quarter_concentration": None,
        }
        self.assertEqual(M.disposition("H02", result)[0], "KILL")

    def test_trade_csv_round_trip(self):
        item = M.Trade(1, 2, 3, -1, 0.01, 0.0024, 0.0076)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trades.csv"
            M.write_trade_csv(path, [item])
            self.assertEqual(M.read_trade_csv(path), [item])

    def test_bootstrap_is_deterministic(self):
        values = [0.01, -0.02, 0.03]
        self.assertEqual(M.bootstrap_lower(values), M.bootstrap_lower(values))


if __name__ == "__main__":
    unittest.main()

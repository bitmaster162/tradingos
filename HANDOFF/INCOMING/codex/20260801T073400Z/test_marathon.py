import importlib.util
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


PATH = Path(__file__).with_name("evaluate_marathon.py")
SPEC = importlib.util.spec_from_file_location("m1", PATH)
M = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)


def observation(value=0.01, secondary=0.01, half="JULY_FIRST", offset=0):
    return M.Observation(
        hypothesis_id=M.H01,
        direction=1,
        signal_ts=1000 + offset,
        trigger_ts=1100 + offset,
        entry_ts=1200 + offset,
        secondary_exit_ts=1300 + offset,
        primary_exit_ts=1400 + offset,
        secondary_gross_return=secondary + M.TOTAL_COST,
        primary_gross_return=value + M.TOTAL_COST,
        secondary_net_edge=secondary,
        primary_net_edge=value,
        cost_return=M.TOTAL_COST,
        half=half,
    )


class MarathonTests(unittest.TestCase):
    def test_exact_three_preregistered_hypotheses(self):
        prereg = json.loads(Path(__file__).with_name("PREREGISTRATION.json").read_text(encoding="utf-8"))
        self.assertEqual(prereg["hypothesis_count"], 3)
        self.assertEqual(tuple(item["id"] for item in prereg["hypotheses"]), M.HYPOTHESES)

    def test_source_plan_expands_to_exactly_307_unique_files(self):
        plan = json.loads(Path(__file__).with_name("FROZEN_SOURCE_PLAN.json").read_text(encoding="utf-8"))
        records = M.expand_plan(plan)
        self.assertEqual(len(records), 307)
        self.assertEqual(len({item["source_id"] for item in records}), 307)

    def test_latest_metric_join_never_uses_future(self):
        times = [100, 200, 300]
        self.assertEqual(M.latest_index_at_or_before(times, 250), 1)
        self.assertEqual(M.latest_index_at_or_before(times, 99), -1)

    def test_metrics_loader_uses_taker_volume_ratio_and_skips_blank_rows(self):
        header = (
            "create_time,symbol,sum_open_interest,sum_open_interest_value,"
            "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
            "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
        )
        rows = (
            "2026-01-01 00:00:00,BTCUSDT,,,,,,\n"
            "2026-01-01 00:05:00,BTCUSDT,100,1,1,1.2,1,0.75\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("metrics.csv", header + rows)
            result = M.load_metrics([path])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].open_interest, 100.0)
        self.assertEqual(result[0].taker_ratio, 0.75)

    def test_cost_is_deducted_exactly_once(self):
        item = observation(value=0.02)
        self.assertEqual(item.cost_return, 0.0012)
        self.assertAlmostEqual(item.primary_gross_return - item.primary_net_edge, 0.0012)

    def test_observation_snapshots_are_strictly_ordered(self):
        item = observation()
        self.assertLessEqual(item.signal_ts, item.trigger_ts)
        self.assertLess(item.trigger_ts, item.entry_ts)
        self.assertLess(item.entry_ts, item.secondary_exit_ts)
        self.assertLess(item.secondary_exit_ts, item.primary_exit_ts)

    def test_h02_requires_a_separate_trigger_bar(self):
        base = M.OOS_START
        btc = []
        eth = []
        for index in range(50):
            ts = base + index * M.BAR15_MS
            btc.append(M.Bar(ts, ts + M.BAR15_MS - 1, 100, 101, 99, 100))
            eth.append(M.Bar(ts, ts + M.BAR15_MS - 1, 100, 101, 99, 100))
        btc[24] = M.Bar(btc[24].open_ts, btc[24].close_ts, 100, 102, 99, 100.5)
        eth[24] = M.Bar(eth[24].open_ts, eth[24].close_ts, 100, 101, 99, 100)
        btc[25] = M.Bar(btc[25].open_ts, btc[25].close_ts, 100.5, 101.5, 100, 101)
        observations, diagnostics, _ = M.evaluate_h02(btc, eth)
        self.assertEqual(diagnostics["raw_confirmed_signals"], 0)
        self.assertEqual(observations, [])

    def test_h02_entry_follows_separate_trigger(self):
        base = M.OOS_START
        btc = []
        eth = []
        for index in range(50):
            ts = base + index * M.BAR15_MS
            btc.append(M.Bar(ts, ts + M.BAR15_MS - 1, 100, 101, 99, 100))
            eth.append(M.Bar(ts, ts + M.BAR15_MS - 1, 100, 101, 99, 100))
        btc[24] = M.Bar(btc[24].open_ts, btc[24].close_ts, 100, 102, 99, 100.5)
        eth[24] = M.Bar(eth[24].open_ts, eth[24].close_ts, 100, 101, 99, 100)
        btc[25] = M.Bar(btc[25].open_ts, btc[25].close_ts, 100.5, 101, 99, 100)
        observations, diagnostics, _ = M.evaluate_h02(btc, eth)
        self.assertEqual(diagnostics["raw_confirmed_signals"], 1)
        self.assertEqual(len(observations), 1)
        item = observations[0]
        self.assertEqual(item.signal_ts, btc[24].close_ts)
        self.assertEqual(item.trigger_ts, btc[25].close_ts)
        self.assertEqual(item.entry_ts, btc[26].open_ts)
        self.assertLess(item.trigger_ts, item.entry_ts)

    def test_strict_pivot_needs_three_right_bars(self):
        bars = []
        lows = [5, 4, 3, 1, 2, 3, 4]
        for index, low in enumerate(lows):
            ts = index * M.HOUR_MS
            bars.append(M.Bar(ts, ts + M.HOUR_MS, 10, 11, low, 10))
        pivot_lows, _ = M.strict_pivots(bars, 3)
        self.assertEqual(pivot_lows, [3])
        self.assertEqual(3 + 3, 6)

    def test_small_samples_fail_closed(self):
        self.assertEqual(M.classify([], 1.0)[0], "INSUFFICIENT_DATA")
        negative = [observation(-0.01, offset=index) for index in range(4)]
        self.assertEqual(M.classify(negative, 1.0)[0], "KILL")
        positive = [observation(0.01, offset=index) for index in range(4)]
        self.assertEqual(M.classify(positive, 1.0)[0], "INSUFFICIENT_DATA")

    def test_low_coverage_is_insufficient(self):
        values = [observation(0.01, offset=index) for index in range(20)]
        self.assertEqual(M.classify(values, 0.949)[0], "INSUFFICIENT_DATA")

    def test_keep_requires_both_chronological_halves(self):
        first = [observation(0.01, half="JULY_FIRST", offset=index) for index in range(10)]
        self.assertEqual(M.classify(first, 1.0)[0], "KILL")
        both = first + [observation(0.01, half="JULY_SECOND", offset=100 + index) for index in range(10)]
        self.assertEqual(M.classify(both, 1.0)[0], "KEEP_FOR_FORWARD_PAPER")

    def test_bootstrap_is_deterministic(self):
        values = [0.01, -0.02, 0.03, 0.04]
        self.assertEqual(M.bootstrap_lower(values), M.bootstrap_lower(values))


if __name__ == "__main__":
    unittest.main()

import importlib.util
import json
import sys
import unittest
from pathlib import Path


PATH = Path(__file__).with_name("evaluate_r62.py")
SPEC = importlib.util.spec_from_file_location("r62", PATH)
M = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)


def feature(
    entry_ts,
    *,
    oi_z=2.0,
    oi_change=0.02,
    funding=0.001,
    ratio=2.0,
    ret_1h=-0.01,
    ret_4h=-0.01,
    ret_24h=0.03,
    rv=0.01,
    entry_open=100.0,
    exit_1h_open=99.0,
    exit_4h_open=98.0,
):
    return M.Feature(
        signal_bar_open_ts=entry_ts - M.HOUR_MS,
        signal_close_ts=entry_ts,
        entry_ts=entry_ts,
        entry_open=entry_open,
        exit_1h_ts=entry_ts + M.HOUR_MS,
        exit_1h_open=exit_1h_open,
        exit_4h_ts=entry_ts + 4 * M.HOUR_MS,
        exit_4h_open=exit_4h_open,
        oi_level_z_30d=oi_z,
        oi_change_4h=oi_change,
        funding_rate=funding,
        funding_ts=entry_ts,
        funding_age_bucket=0,
        top_position_ratio=ratio,
        ret_1h=ret_1h,
        ret_4h=ret_4h,
        ret_24h=ret_24h,
        rv_24h=rv,
    )


def evidence_stub(n=30, value=0.01):
    metric = {
        "n": n,
        "mean_matched_underperformance_after_cost": value,
        "median_matched_underperformance_after_cost": value,
        "bootstrap_95_lower_mean": value,
        "matched_win_rate": 0.6,
        "mean_short_net_return": value,
        "mean_signal_return": -value,
        "mean_control_return": 0.0,
    }
    return {
        "full": {"1h": dict(metric), "4h": dict(metric)},
        "chronological_halves": {
            "first": {"1h": dict(metric), "4h": dict(metric)},
            "second": {"1h": dict(metric), "4h": dict(metric)},
        },
    }


class R62Tests(unittest.TestCase):
    def setUp(self):
        self.band = {
            "oi_level_z_min": 1.0,
            "oi_change_4h_min": 0.01,
            "funding_rate_min": 0.0005,
            "top_position_ratio_min": 1.5,
        }
        self.vol = {"low_max": 0.005, "mid_max": 0.02}

    def test_single_hypothesis_identity(self):
        self.assertEqual(M.HYPOTHESIS, "BTC_CROWDING_EXHAUSTION")

    def test_source_plan_has_exactly_39_files(self):
        plan = json.loads(
            Path(__file__).with_name("FROZEN_SOURCE_PLAN.json").read_text(
                encoding="utf-8"
            )
        )
        records = M.expand_plan(plan)
        self.assertEqual(len(records), 39)
        self.assertEqual(len({item["source_id"] for item in records}), 39)

    def test_frozen_utc_boundaries(self):
        self.assertEqual(M.CAL_START, 1_751_328_000_000)
        self.assertEqual(M.OOS_START, 1_767_225_600_000)
        self.assertEqual(M.OOS_SPLIT, 1_775_001_600_000)
        self.assertEqual(M.OOS_END, 1_782_864_000_000)

    def test_latest_lookup_never_uses_future(self):
        times = [100, 200, 300]
        self.assertEqual(M.latest_index_at_or_before(times, 250), 1)
        self.assertEqual(M.latest_index_at_or_before(times, 99), -1)

    def test_quantile_linear_interpolation(self):
        self.assertEqual(M.quantile([0.0, 10.0], 0.6), 6.0)

    def test_signal_requires_all_frozen_conditions(self):
        self.assertTrue(M.is_signal(feature(M.OOS_START), self.band))
        self.assertFalse(
            M.is_signal(feature(M.OOS_START, funding=0.0), self.band)
        )
        self.assertFalse(
            M.is_signal(feature(M.OOS_START, ret_24h=-0.01), self.band)
        )

    def test_four_hour_overlap_is_removed(self):
        start = M.OOS_START
        candidates = [
            feature(start),
            feature(start + M.HOUR_MS),
            feature(start + 5 * M.HOUR_MS),
        ]
        selected, raw = M.select_non_overlapping(candidates, self.band)
        self.assertEqual(raw, 3)
        self.assertEqual(len(selected), 2)

    def test_matching_uses_five_controls_and_cost_once(self):
        start = M.OOS_START + 24 * M.HOUR_MS
        signal = feature(start)
        controls = [
            feature(
                start + (10 + index) * M.HOUR_MS,
                oi_z=0.0,
                oi_change=0.0,
                funding=0.0,
                ratio=1.0,
                entry_open=100.0,
                exit_1h_open=100.0,
                exit_4h_open=100.0,
            )
            for index in range(5)
        ]
        observations, unmatched = M.match_observations(
            [signal, *controls], [signal], self.band, self.vol
        )
        self.assertEqual(unmatched, 0)
        self.assertEqual(len(observations), 1)
        item = observations[0]
        self.assertGreater(item.exit_1h_ts, item.entry_ts)
        self.assertGreater(item.exit_4h_ts, item.entry_ts)
        self.assertAlmostEqual(item.cost_return, 0.0012)
        self.assertAlmostEqual(item.matched_alpha_4h, 0.0188)
        self.assertEqual(len(item.control_entry_timestamps.split(";")), 5)

    def test_small_sample_is_insufficient(self):
        primary = evidence_stub(n=29)
        sensitivity = evidence_stub(n=40)
        decision, _ = M.disposition(primary, sensitivity, 1.0)
        self.assertEqual(decision, "INSUFFICIENT_DATA")

    def test_low_coverage_is_insufficient(self):
        decision, _ = M.disposition(
            evidence_stub(n=100), evidence_stub(n=100), 0.949
        )
        self.assertEqual(decision, "INSUFFICIENT_DATA")

    def test_negative_primary_half_kills_even_if_sensitivity_passes(self):
        primary = evidence_stub(n=100)
        primary["chronological_halves"]["second"]["4h"][
            "mean_matched_underperformance_after_cost"
        ] = -0.001
        decision, _ = M.disposition(primary, evidence_stub(n=100), 1.0)
        self.assertEqual(decision, "KILL")

    def test_all_frozen_gates_keep(self):
        decision, _ = M.disposition(
            evidence_stub(n=100), evidence_stub(n=100), 1.0
        )
        self.assertEqual(decision, "KEEP_FOR_LARGER_FORWARD_WATCH")

    def test_bootstrap_is_deterministic(self):
        values = [0.01, -0.02, 0.03]
        self.assertEqual(M.bootstrap_lower(values), M.bootstrap_lower(values))


if __name__ == "__main__":
    unittest.main()

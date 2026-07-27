from __future__ import annotations

import argparse
import unittest
from datetime import datetime, timedelta, timezone

from tools.liquidation_impulse_reversal_nested_holdout import ImpulseConfig, build_features, gate, signal_matches, split_index


class LiquidationImpulseReversalNestedHoldoutTests(unittest.TestCase):
    def test_split_uses_first_row_at_boundary(self) -> None:
        rows = [{"time": "2024-12-31T23:00:00+00:00"}, {"time": "2025-01-01T00:00:00+00:00"}]
        self.assertEqual(split_index(rows, "2025-01-01T00:00:00+00:00"), 1)

    def test_derivatives_join_by_timestamp_when_input_is_reversed(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        rows = []
        derivatives = []
        for index in range(223):
            timestamp = (start + timedelta(hours=index)).isoformat()
            price = 50_000.0 - index
            rows.append({"time": timestamp, "open": str(price), "high": str(price + 10), "low": str(price - 10), "close": str(price), "volume": "100"})
            derivatives.append({"time": timestamp, "open_interest": str(1000 - index)})
        features = build_features(rows, list(reversed(derivatives)))
        expected = ((1000 - 220) - (1000 - 217)) / (1000 - 217) * 100.0
        self.assertAlmostEqual(features[220]["oi_delta_pct"], expected)

    def test_long_and_short_reclaim_conditions_are_symmetric(self) -> None:
        common = {"oi_delta_pct": -2.0, "volume_z": 3.0}
        long_config = ImpulseConfig("long", "reversal", "LONG", "reclaim", 2.0, 1.0, 2.0, 1.0, 2.0, 12)
        short_config = ImpulseConfig("short", "reversal", "SHORT", "reclaim", 2.0, 1.0, 2.0, 1.0, 2.0, 12)
        self.assertTrue(signal_matches(long_config, {**common, "displacement_atr": -2.5, "close_location": 0.8}))
        self.assertTrue(signal_matches(short_config, {**common, "displacement_atr": 2.5, "close_location": 0.2}))

    def test_continuation_conditions_follow_impulse_direction(self) -> None:
        common = {"oi_delta_pct": -2.0, "volume_z": 3.0}
        long_config = ImpulseConfig("long", "continuation", "LONG", "acceptance", 2.0, 1.0, 2.0, 1.0, 2.0, 12)
        short_config = ImpulseConfig("short", "continuation", "SHORT", "acceptance", 2.0, 1.0, 2.0, 1.0, 2.0, 12)
        self.assertTrue(signal_matches(long_config, {**common, "displacement_atr": 2.5, "close_location": 0.8}))
        self.assertTrue(signal_matches(short_config, {**common, "displacement_atr": -2.5, "close_location": 0.2}))

    def test_train_gate_requires_stress_and_bootstrap(self) -> None:
        args = argparse.Namespace(
            min_train_trades=25,
            min_train_expectancy_r=0.10,
            min_train_stable_folds=3,
            max_train_drawdown_r=12.0,
            min_train_bootstrap_prob=0.80,
        )
        result = {
            "summary": {"trades": 40, "expectancy_r": 0.2, "max_drawdown_r": -5.0},
            "stable_folds": 4,
            "bootstrap": {"expectancy_r": {"prob_gt_0": 0.9}},
            "cost_stress": {"summary": {"expectancy_r": 0.02}},
        }
        self.assertTrue(gate(result, stage="train", args=args)["pass"])
        result["cost_stress"] = {"summary": {"expectancy_r": -0.01}}
        self.assertFalse(gate(result, stage="train", args=args)["pass"])


if __name__ == "__main__":
    unittest.main()

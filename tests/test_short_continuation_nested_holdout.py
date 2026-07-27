from __future__ import annotations

import argparse
import unittest
from datetime import datetime, timedelta, timezone

from tools.short_continuation_nested_holdout import (
    ShortConfig,
    build_features,
    oos_gate,
    signal_matches,
    split_index,
    train_gate,
)


def _gate_args() -> argparse.Namespace:
    return argparse.Namespace(
        min_train_trades=60,
        min_train_expectancy_r=0.10,
        min_train_stable_folds=4,
        min_train_bootstrap_prob=0.80,
        max_train_drawdown_r=12.0,
        min_oos_trades=30,
        min_oos_expectancy_r=0.10,
        min_oos_stable_folds=2,
        max_oos_drawdown_r=8.0,
    )


class ShortContinuationNestedHoldoutTests(unittest.TestCase):
    def test_calendar_split_uses_first_row_at_boundary(self) -> None:
        rows = [
            {"time": "2024-12-31T23:00:00+00:00"},
            {"time": "2025-01-01T00:00:00+00:00"},
            {"time": "2025-01-01T01:00:00+00:00"},
        ]
        self.assertEqual(split_index(rows, "2025-01-01T00:00:00+00:00"), 1)

    def test_derivatives_are_joined_by_timestamp_not_row_position(self) -> None:
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        rows = []
        derivatives = []
        for index in range(223):
            timestamp = (start + timedelta(hours=index)).isoformat()
            price = 50_000.0 - index * 5.0
            rows.append(
                {
                    "time": timestamp,
                    "open": str(price + 1.0),
                    "high": str(price + 10.0),
                    "low": str(price - 10.0),
                    "close": str(price),
                    "volume": "100",
                }
            )
            derivatives.append(
                {
                    "time": timestamp,
                    "open_interest": str(100.0 + index),
                    "funding": "0.0001",
                }
            )

        features = build_features(
            rows,
            list(reversed(derivatives)),
            [{"bias": "SHORT", "regime": "trend"} for _ in rows],
            220,
        )

        expected = (320.0 - 308.0) / 308.0 * 100.0
        self.assertAlmostEqual(features[220]["oi_delta_12_pct"], expected)
        self.assertEqual(features[220]["htf_bias"], "SHORT")

    def test_signal_modes_are_explicit(self) -> None:
        feature = {
            "htf_bias": "SHORT",
            "trend_strength_20_atr": -1.2,
            "oi_delta_12_pct": 0.3,
            "funding": 0.0001,
            "sweep_side": "none",
            "near_low": True,
        }
        for mode in ("base", "no_sweep", "funding_positive", "near_low"):
            config = ShortConfig(f"test_{mode}", mode, -1.0, 0.1, 1.0, 2.0, 12)
            self.assertTrue(signal_matches(config, feature), mode)

        wrong_bias = dict(feature, htf_bias="LONG")
        config = ShortConfig("test", "base", -1.0, 0.1, 1.0, 2.0, 12)
        self.assertFalse(signal_matches(config, wrong_bias))

    def test_train_and_oos_gates_are_fixed_and_cost_aware(self) -> None:
        passing = {
            "summary": {"trades": 80, "expectancy_r": 0.20, "max_drawdown_r": -5.0},
            "stable_folds": 5,
            "bootstrap": {"expectancy_r": {"prob_gt_0": 0.91}},
            "cost_stress": {"summary": {"expectancy_r": 0.05}},
        }
        args = _gate_args()
        self.assertTrue(train_gate(passing, args)["pass"])
        self.assertTrue(oos_gate(passing, args)["pass"])

        failing = dict(passing)
        failing["cost_stress"] = {"summary": {"expectancy_r": -0.01}}
        self.assertFalse(train_gate(failing, args)["pass"])
        self.assertFalse(oos_gate(failing, args)["pass"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import argparse
import unittest

from tools.liquidity_sweep_detector import OhlcvBar
from tools.trend_mix_nested_holdout import find_split_index, window_gate


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        take_floor_for_breakeven=2.0,
        min_train_trades=60,
        min_train_expectancy_r=0.10,
        min_train_stable_folds=4,
        min_train_bootstrap_prob=0.80,
        max_train_drawdown_r=12.0,
        min_oos_trades=20,
        min_oos_expectancy_r=0.10,
        min_oos_stable_folds=2,
        max_oos_drawdown_r=8.0,
    )


class TrendMixNestedHoldoutTests(unittest.TestCase):
    def test_split_uses_first_bar_at_boundary(self) -> None:
        bars = [
            OhlcvBar(0, "2024-12-31T20:00:00+00:00", 1, 1, 1, 1, 1),
            OhlcvBar(1, "2025-01-01T00:00:00+00:00", 1, 1, 1, 1, 1),
        ]
        self.assertEqual(find_split_index(bars, "2025-01-01T00:00:00+00:00"), 1)

    def test_train_gate_requires_positive_cost_stress(self) -> None:
        window = {
            "summary": {"trades": 80, "expectancy_r": 0.20, "winrate_pct": 45.0, "max_drawdown_r": -5.0},
            "stable_folds": 5,
            "bootstrap_prob_expectancy_gt_zero": 0.90,
            "cost_stress": {"summary": {"expectancy_r": 0.04}},
        }
        self.assertTrue(window_gate(window, train=True, args=_args())["pass"])
        window["cost_stress"] = {"summary": {"expectancy_r": -0.01}}
        self.assertFalse(window_gate(window, train=True, args=_args())["pass"])

    def test_oos_gate_does_not_require_bootstrap(self) -> None:
        window = {
            "summary": {"trades": 25, "expectancy_r": 0.15, "winrate_pct": 44.0, "max_drawdown_r": -4.0},
            "stable_folds": 3,
            "bootstrap_prob_expectancy_gt_zero": None,
            "cost_stress": {"summary": {"expectancy_r": 0.02}},
        }
        self.assertTrue(window_gate(window, train=False, args=_args())["pass"])


if __name__ == "__main__":
    unittest.main()

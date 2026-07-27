from __future__ import annotations

import argparse
import unittest
from types import SimpleNamespace

from tools.range_edge_nested_holdout import (
    LANES,
    classify_oos_gate,
    find_split_index,
    oos_gate,
    signal_overlap,
    train_gate,
)


class RangeEdgeNestedHoldoutTests(unittest.TestCase):
    def test_split_uses_first_bar_at_or_after_boundary(self) -> None:
        bars = [
            SimpleNamespace(ts="2024-12-31T20:00:00+00:00"),
            SimpleNamespace(ts="2025-01-01T00:00:00+00:00"),
            SimpleNamespace(ts="2025-01-01T04:00:00+00:00"),
        ]
        self.assertEqual(find_split_index(bars, "2025-01-01T00:00:00+00:00"), 1)

    def test_lanes_are_precommitted_and_disjoint(self) -> None:
        self.assertEqual(LANES["RANGE_REFINED_4H"]["holds"], {12, 16})
        self.assertEqual(LANES["EDGE_FORWARD_4H"]["holds"], {8})
        self.assertFalse(LANES["RANGE_REFINED_4H"]["holds"] & LANES["EDGE_FORWARD_4H"]["holds"])

    def test_train_and_oos_gates_use_fixed_thresholds(self) -> None:
        args = argparse.Namespace(
            min_train_trades=40,
            min_train_expectancy_r=0.10,
            min_train_stable_folds=2,
            max_train_drawdown_r=10.0,
            min_oos_trades=20,
            min_oos_expectancy_r=0.10,
            min_oos_stable_folds=2,
            max_oos_drawdown_r=6.0,
        )
        passing = {
            "summary": {"trades": 40, "expectancy_r": 0.10, "max_drawdown_r": -5.0},
            "stable_folds": 2,
            "cost_stress": {"summary": {"expectancy_r": 0.01}},
        }
        self.assertTrue(train_gate(passing, args)["pass"])
        self.assertTrue(oos_gate(passing, args)["pass"])
        failing = {
            **passing,
            "cost_stress": {"summary": {"expectancy_r": 0.0}},
        }
        self.assertFalse(train_gate(failing, args)["pass"])
        self.assertFalse(oos_gate(failing, args)["pass"])

    def test_signal_overlap_reports_duplicate_family_risk(self) -> None:
        duplicate = signal_overlap([1, 2, 3], [1, 2, 3])
        self.assertEqual(duplicate["jaccard"], 1.0)
        self.assertFalse(duplicate["independent_enough"])
        distinct = signal_overlap([1, 2], [3, 4])
        self.assertEqual(distinct["jaccard"], 0.0)
        self.assertTrue(distinct["independent_enough"])

    def test_positive_but_small_oos_is_insufficient_not_rejected(self) -> None:
        gate = {
            "pass": False,
            "checks": {
                "min_trades": False,
                "min_expectancy_r": True,
                "min_stable_folds": False,
                "max_drawdown_r": True,
                "cost_stress_positive": True,
            },
        }
        self.assertEqual(classify_oos_gate(gate), "insufficient_oos_evidence_keep_observer_only")
        gate["checks"]["min_expectancy_r"] = False
        self.assertEqual(classify_oos_gate(gate), "reject_oos_gate_failed")


if __name__ == "__main__":
    unittest.main()

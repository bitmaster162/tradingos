from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from crowd_fade_nested_holdout import oos_decision  # noqa: E402


class CrowdNestedHoldoutTests(unittest.TestCase):
    def test_rejects_insufficient_oos_sample(self) -> None:
        result = {"summary": {"trades": 19, "expectancy_r": 1.0, "max_drawdown_r": -1.0}, "stable_folds": 3}
        self.assertEqual(oos_decision(result, 20, 0.1, 2, 6.0), "reject_oos_insufficient_trades")

    def test_rejects_negative_oos_expectancy(self) -> None:
        result = {"summary": {"trades": 30, "expectancy_r": -0.01, "max_drawdown_r": -2.0}, "stable_folds": 2}
        self.assertEqual(oos_decision(result, 20, 0.1, 2, 6.0), "reject_oos_expectancy")

    def test_review_never_implies_execution(self) -> None:
        result = {"summary": {"trades": 30, "expectancy_r": 0.2, "max_drawdown_r": -3.0}, "stable_folds": 2}
        self.assertEqual(oos_decision(result, 20, 0.1, 2, 6.0), "oos_candidate_for_separate_forward_design_review")


if __name__ == "__main__":
    unittest.main()

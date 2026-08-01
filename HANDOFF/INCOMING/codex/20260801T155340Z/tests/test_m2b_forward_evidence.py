from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence"
ORIGINAL = ROOT / "original_candidates"
TOOL = ROOT / "tools" / "evaluate_m2b_forward_evidence.py"


def load(name: str):
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8"))


class ForwardEvidencePackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("m2b_evaluator", TOOL)
        assert spec and spec.loader
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_matrix_has_exactly_three_bound_tracks(self):
        matrix = load("FORWARD_EDGE_DECISION_MATRIX.json")
        self.assertEqual(matrix["row_count"], 3)
        self.assertEqual(
            {row["track_id"] for row in matrix["rows"]},
            {"RANGE_REFINED_FORWARD", "HYP-SPOT-LEAD-001", "LIQUIDATION_CONTINUOUS_SCORE"},
        )
        self.assertFalse(matrix["code05_handoff_created"])
        self.assertFalse(matrix["can_trade"])
        self.assertEqual(matrix["capital_permission"], "DENY")
        self.assertEqual(matrix["deploy_permission"], "DENY")

    def test_range_is_killed_by_untouched_oos_and_tombstone(self):
        result = load("RANGE_REFINED_FORWARD_RESULT.json")
        self.assertEqual(result["terminal"], "KILL")
        self.assertTrue(result["independent_oos"]["selection_frozen_before_oos"])
        self.assertFalse(result["independent_oos"]["gate"]["pass"])
        self.assertLess(result["independent_oos"]["expectancy_r"], 0)
        self.assertLess(result["independent_oos"]["stress_expectancy_r"], 0)
        self.assertEqual(result["tombstone"]["status"], "tombstoned_no_retune")
        self.assertEqual(result["forward_journal"]["event_counts"], {"range_refined_no_signal": 60})

    def test_spot_led_counts_only_fresh_resolved_trades(self):
        result = load("HYP-SPOT-LEAD-001_RESULT.json")
        source = result["fresh_source"]
        self.assertEqual(result["terminal"], "INSUFFICIENT_DATA")
        self.assertEqual(source["fresh_effective_trades"], 2)
        self.assertEqual(source["old_trades_not_counted_as_new"], 43)
        self.assertEqual(source["combined_evidence_count"], 45)
        self.assertLess(source["combined_evidence_count"], result["freeze"]["minimum_trades"])
        self.assertEqual(result["freeze"]["cost_bps_per_side"], 7.0)
        self.assertEqual(result["freeze"]["stress_extra_bps_per_side"], 10.0)
        self.assertFalse(result["historical_observations_reused_as_new"])

    def test_liquidation_score_uses_frozen_bins_and_fails_sample_gate(self):
        result = load("LIQUIDATION_CONTINUOUS_SCORE_RESULT.json")
        self.assertEqual(result["terminal"], "INSUFFICIENT_DATA")
        self.assertEqual(result["freeze"]["q25"], 0.426128)
        self.assertEqual(result["freeze"]["q50"], 1.414128)
        self.assertEqual(result["freeze"]["q75"], 5.109507)
        self.assertFalse(result["freeze"]["bins_recomputed_from_outcomes"])
        self.assertEqual(result["reconciliation"]["resolved"], 1)
        self.assertEqual(result["reconciliation"]["inactive_resolved"], 0)
        self.assertFalse(any(result["gate_checks"].values()))
        self.assertFalse(result["filter_change_allowed"])
        self.assertFalse(result["veto_allowed"])

    def test_score_bin_boundaries_are_locked(self):
        lock = json.loads((ORIGINAL / "EDGE_LIQUIDATION_SCORE_SHADOW_LOCK.json").read_text(encoding="utf-8"))
        self.assertEqual(self.module.score_bin(0.0, lock), "inactive")
        self.assertEqual(self.module.score_bin(0.426128, lock), "low")
        self.assertEqual(self.module.score_bin(0.426129, lock), "medium")
        self.assertEqual(self.module.score_bin(1.414128, lock), "medium")
        self.assertEqual(self.module.score_bin(5.109507, lock), "elevated")
        self.assertEqual(self.module.score_bin(5.109508, lock), "extreme")

    def test_m2a_terminal_engine_agrees_with_domain_results(self):
        for track in ("RANGE_REFINED_FORWARD", "HYP-SPOT-LEAD-001", "LIQUIDATION_CONTINUOUS_SCORE"):
            domain = load(f"{track}_RESULT.json")
            terminal = load(f"{track}_M2A_TERMINAL.json")
            self.assertEqual(domain["terminal"], terminal["terminal"])
            self.assertFalse(terminal["strategy_accepted"])
            self.assertFalse(terminal["can_trade"])


if __name__ == "__main__":
    unittest.main()

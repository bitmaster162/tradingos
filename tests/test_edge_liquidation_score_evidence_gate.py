import unittest

from tools.edge_liquidation_score_evidence_gate import evaluate_gate


class EdgeLiquidationScoreEvidenceGateTests(unittest.TestCase):
    def test_collects_before_total_sample(self) -> None:
        result = evaluate_gate(
            [],
            min_total_resolved=20,
            min_baseline_resolved=8,
            min_bin_resolved=8,
            min_bin_expectancy_r=0.05,
            min_delta_r=0.10,
            bootstrap_iterations=100,
        )

        self.assertEqual(result["classification"], "collecting_total_forward_outcomes")
        self.assertFalse(result["research_review_allowed"])
        self.assertFalse(result["filter_change_allowed"])

    def test_strong_independent_bin_allows_research_review_only(self) -> None:
        rows = [{"score_bin": "inactive", "r": -0.25}] * 10
        rows.extend({"score_bin": "elevated", "r": 0.75} for _ in range(10))

        result = evaluate_gate(
            rows,
            min_total_resolved=20,
            min_baseline_resolved=8,
            min_bin_resolved=8,
            min_bin_expectancy_r=0.05,
            min_delta_r=0.10,
            bootstrap_iterations=200,
        )

        self.assertEqual(result["classification"], "independent_forward_score_evidence_ready_for_research_review")
        self.assertTrue(result["research_review_allowed"])
        self.assertFalse(result["filter_change_allowed"])
        self.assertFalse(result["paper_execution_allowed"])
        self.assertFalse(result["can_trade"])


if __name__ == "__main__":
    unittest.main()

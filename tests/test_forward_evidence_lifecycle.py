from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from forward_evidence_lifecycle_controller import build_report, classify_family  # noqa: E402


class ForwardEvidenceLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = json.loads((ROOT / "configs" / "FORWARD_EVIDENCE_LIFECYCLE.json").read_text(encoding="utf-8"))

    def test_collects_before_precommitted_sample(self) -> None:
        row = {
            "family": "CROWD_FADE_1H",
            "resolved": 1,
            "resolved_required": 20,
            "expectancy_r": -1.0,
            "max_drawdown_r": -1.0,
        }
        result = classify_family(row, self.policy)
        self.assertEqual(result["state"], "collecting_independent_evidence")
        self.assertFalse(result["can_trade"])

    def test_pauses_only_after_early_risk_checkpoint(self) -> None:
        row = {
            "family": "RISKY",
            "resolved": 10,
            "resolved_required": 30,
            "expectancy_r": -0.8,
            "max_drawdown_r": -8.0,
        }
        self.assertEqual(classify_family(row, self.policy)["state"], "paused_early_risk_breach")

    def test_rejects_negative_edge_at_full_checkpoint(self) -> None:
        row = {
            "family": "NEGATIVE",
            "resolved": 30,
            "resolved_required": 30,
            "expectancy_r": -0.05,
            "winrate_pct": 50.0,
            "breakeven_winrate_pct": 40.0,
            "max_drawdown_r": -3.0,
        }
        self.assertEqual(classify_family(row, self.policy)["state"], "rejected_no_positive_edge")

    def test_allows_review_but_never_execution(self) -> None:
        row = {
            "family": "POSITIVE",
            "resolved": 30,
            "resolved_required": 30,
            "expectancy_r": 0.25,
            "winrate_pct": 55.0,
            "breakeven_winrate_pct": 40.0,
            "max_drawdown_r": -4.0,
            "eligible_for_paper_design": True,
        }
        result = classify_family(row, self.policy)
        self.assertEqual(result["state"], "paper_design_review_only")
        self.assertFalse(result["paper_execution_allowed"])

    def test_report_requires_exactly_four_families(self) -> None:
        report = build_report({"families": []}, self.policy)
        self.assertEqual(report["decision"], "blocked_invalid_family_inventory")
        self.assertFalse(report["can_trade"])

    def test_historical_invalidation_overrides_small_forward_sample(self) -> None:
        rows = [
            {"family": family, "resolved": 0, "resolved_required": 20}
            for family in ("TREND_MIX_4H", "RANGE_REFINED_4H", "EDGE_FORWARD_4H", "CROWD_FADE_1H")
        ]
        report = build_report({"families": rows}, self.policy, {"CROWD_FADE_1H": {"expectancy_r": -0.1}})
        crowd = next(item for item in report["families"] if item["family"] == "CROWD_FADE_1H")
        self.assertEqual(crowd["state"], "rejected_historical_invalidation")
        self.assertFalse(crowd["can_trade"])

    def test_insufficient_historical_evidence_does_not_fake_rejection(self) -> None:
        rows = [
            {"family": family, "resolved": 0, "resolved_required": 20}
            for family in ("TREND_MIX_4H", "RANGE_REFINED_4H", "EDGE_FORWARD_4H", "CROWD_FADE_1H")
        ]
        evidence = {"EDGE_FORWARD_4H": {"decision": "insufficient_oos_evidence_keep_observer_only", "oos_trades": 17}}
        report = build_report({"families": rows}, self.policy, {}, evidence)
        edge = next(item for item in report["families"] if item["family"] == "EDGE_FORWARD_4H")
        self.assertEqual(edge["state"], "collecting_independent_evidence")
        self.assertEqual(edge["historical_evidence"]["oos_trades"], 17)


if __name__ == "__main__":
    unittest.main()

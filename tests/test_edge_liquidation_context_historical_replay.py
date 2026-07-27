import unittest

from tools.edge_liquidation_context_historical_replay import aggregate_context, evidence_verdict


class EdgeLiquidationContextHistoricalReplayTests(unittest.TestCase):
    def test_strongest_prefers_non_none_event_inside_signal_bar(self) -> None:
        contexts = [
            {"bar_ts": "2026-01-01T00:00:00+00:00", "context": "down_liquidation_impulse", "context_score": 2.0},
            {"bar_ts": "2026-01-01T01:00:00+00:00", "context": "none", "context_score": 0.0},
            {"bar_ts": "2026-01-01T03:00:00+00:00", "context": "none", "context_score": 0.0},
            {"bar_ts": "2026-01-01T04:00:00+00:00", "context": "up_liquidation_impulse", "context_score": 9.0},
        ]

        result = aggregate_context("2026-01-01T00:00:00+00:00", contexts)

        self.assertEqual(result["latest"], "none")
        self.assertEqual(result["strongest"], "down_liquidation_impulse")
        self.assertEqual(result["hours"], 3)

    def test_evidence_gate_requires_oos_context_sample(self) -> None:
        train = [{"strongest_context": "down_liquidation_impulse", "r": 1.0, "r_cost10": 0.8}] * 10
        oos = [{"strongest_context": "down_liquidation_impulse", "r": 1.0, "r_cost10": 0.8}] * 3

        gate = evidence_verdict(train, oos, min_oos_group=8)

        self.assertEqual(gate["classification"], "insufficient_oos_context_subgroup_sample")
        self.assertFalse(gate["recommended_filter_change"])


if __name__ == "__main__":
    unittest.main()

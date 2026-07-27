from __future__ import annotations

import unittest

from tools.edge_liquidation_context_shadow_observer import classify_context, continuous_score, score_bin
from tools.edge_liquidation_context_shadow_scoreboard import nearest_context


class EdgeLiquidationContextShadowTests(unittest.TestCase):
    def test_continuous_score_and_frozen_bin_are_deterministic(self) -> None:
        feature = {"displacement_atr": -2.0, "oi_delta_pct": -3.0, "volume_z": 2.0}
        thresholds = {"oi_drop_pct": 2.0, "volume_z": 1.5}
        lock = {
            "bins": [
                {"id": "inactive", "min_inclusive": 0.0, "max_inclusive": 0.0},
                {"id": "low", "min_exclusive": 0.0, "max_inclusive": 1.0},
                {"id": "extreme", "min_exclusive": 1.0, "max_inclusive": None},
            ]
        }

        score = continuous_score(feature, thresholds)

        self.assertEqual(score, 4.0)
        self.assertEqual(score_bin(score, lock), "extreme")

    def test_classifies_symmetric_confirmed_impulses(self) -> None:
        common = {"oi_delta_pct": -2.5, "volume_z": 2.0}
        up = classify_context(
            {**common, "displacement_atr": 2.0, "close_location": 0.8},
            displacement_threshold=1.5,
            oi_drop_threshold=2.0,
            volume_z_threshold=1.5,
        )
        down = classify_context(
            {**common, "displacement_atr": -2.0, "close_location": 0.2},
            displacement_threshold=1.5,
            oi_drop_threshold=2.0,
            volume_z_threshold=1.5,
        )
        self.assertEqual(up, "up_liquidation_impulse")
        self.assertEqual(down, "down_liquidation_impulse")

    def test_requires_oi_and_volume_confluence(self) -> None:
        context = classify_context(
            {"oi_delta_pct": -0.5, "volume_z": 3.0, "displacement_atr": -3.0, "close_location": 0.1},
            displacement_threshold=1.5,
            oi_drop_threshold=2.0,
            volume_z_threshold=1.5,
        )
        self.assertEqual(context, "none")

    def test_nearest_context_stays_inside_edge_bar(self) -> None:
        signal = {"bar_ts": "2026-06-23T00:00:00+00:00"}
        contexts = [
            {"bar_ts": "2026-06-22T23:00:00+00:00", "context": "outside_before"},
            {"bar_ts": "2026-06-23T01:00:00+00:00", "context": "inside_early"},
            {"bar_ts": "2026-06-23T03:00:00+00:00", "context": "inside_latest"},
            {"bar_ts": "2026-06-23T04:00:00+00:00", "context": "outside_after"},
        ]
        self.assertEqual(nearest_context(signal, contexts)["context"], "inside_latest")

    def test_nearest_context_prefers_newer_enriched_duplicate(self) -> None:
        signal = {"bar_ts": "2026-06-23T00:00:00+00:00"}
        contexts = [
            {"bar_ts": "2026-06-23T03:00:00+00:00", "context": "none"},
            {"bar_ts": "2026-06-23T03:00:00+00:00", "context": "none", "score_bin": "medium"},
        ]

        self.assertEqual(nearest_context(signal, contexts)["score_bin"], "medium")


if __name__ == "__main__":
    unittest.main()

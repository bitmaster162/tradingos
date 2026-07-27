from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.crowd_fade_positioning_diagnostic import build_signals, pct_change, rolling_z  # noqa: E402


class CrowdDiagnosticPrecomputeTests(unittest.TestCase):
    def test_precomputed_features_match_legacy_path(self) -> None:
        bars = [SimpleNamespace(ts=f"t{index}") for index in range(40)]
        crowd = {bar.ts: {"global_long_short_ratio": 1.0 + index * 0.01} for index, bar in enumerate(bars)}
        derivatives = {
            bar.ts: {"open_interest": 100.0 + index, "funding": 0.001}
            for index, bar in enumerate(bars)
        }
        ratios = [crowd[bar.ts]["global_long_short_ratio"] for bar in bars]
        oi_values = [derivatives[bar.ts]["open_interest"] for bar in bars]
        prepared = {
            "ratios_by_field": {"global_long_short_ratio": ratios},
            "z_by_field_window": {("global_long_short_ratio", 12): rolling_z(ratios, 12)},
            "oi_delta_values": [pct_change(oi_values, index, 3) for index in range(len(bars))],
            "funding_values": [derivatives[bar.ts]["funding"] for bar in bars],
        }
        kwargs = {
            "bars": bars,
            "crowd_by_time": crowd,
            "derivatives_by_time": derivatives,
            "ratio_field": "global_long_short_ratio",
            "z_window": 12,
            "z_threshold": 0.8,
            "side_mode": "crowded_longs_fade_short",
            "oi_lookback": 3,
            "require_oi_expansion": True,
            "require_funding_alignment": True,
            "atr_values": [1.0] * len(bars),
        }
        legacy = build_signals(**kwargs)
        optimized = build_signals(**kwargs, prepared=prepared)
        self.assertEqual(optimized, legacy)


if __name__ == "__main__":
    unittest.main()

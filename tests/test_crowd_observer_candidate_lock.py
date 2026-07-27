from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.crowd_fade_positioning_shadow_observer import load_locked_candidate  # noqa: E402


class CrowdObserverCandidateLockTests(unittest.TestCase):
    def test_loads_only_explicit_locked_candidate(self) -> None:
        candidate, version, enabled = load_locked_candidate(ROOT / "configs" / "CROWD_FADE_FORWARD_LOCK.json")
        self.assertEqual(version, "1.0.0")
        self.assertEqual(candidate["strategy_id"], "crowd_fade_1h_global_long_short_ratio_crowded_longs_fade_short_z1.25_w24_oi0_fund0_s1_t2_h8")
        self.assertFalse(enabled)

    def test_missing_candidate_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "lock.json"
            path.write_text(json.dumps({"version": "x"}), encoding="utf-8")
            candidate, version, enabled = load_locked_candidate(path)
        self.assertIsNone(candidate)
        self.assertEqual(version, "x")
        self.assertFalse(enabled)


if __name__ == "__main__":
    unittest.main()

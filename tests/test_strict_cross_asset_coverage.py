from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    spec = importlib.util.spec_from_file_location(
        "r101_relative_strength",
        ROOT / "tools/relative_strength_rotation_nested_holdout.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


REL = load_module()


def config():
    return REL.RotationConfig(
        strategy_id="coverage-test",
        interval="1h",
        side="LONG",
        mode="btc_leads",
        lookback=1,
        min_rel_strength_pct=0.5,
        alt_symbols=("ETHUSDT", "SOLUSDT", "BCHUSDT"),
        alt_confirm="none",
        atr_regime_filter="none",
        stop_atr=1.0,
        take_atr=2.0,
        max_hold_bars=1,
    )


def bars(n=6):
    return [SimpleNamespace(ts=f"2026-01-01T0{i}:00:00+00:00") for i in range(n)]


def features(sol_value):
    n=6
    return {
        "atr": [1.0] * n,
        "atr_ratio": [1.0] * n,
        "btc_closes": [100.0] * n,
        "btc_returns": {1: [None, 3.0, 3.0, 3.0, 3.0, 3.0]},
        "alt_closes": {
            "ETHUSDT": [100.0] * n,
            "SOLUSDT": [100.0] * n,
            "BCHUSDT": [100.0] * n,
        },
        "alt_returns": {
            "ETHUSDT": {1: [None, 0.0, 0.0, 0.0, 0.0, 0.0]},
            "SOLUSDT": {1: [None, sol_value, sol_value, sol_value, sol_value, sol_value]},
            "BCHUSDT": {1: [None, 0.0, 0.0, 0.0, 0.0, 0.0]},
        },
    }


class StrictCrossAssetCoverageTests(unittest.TestCase):
    def test_missing_configured_alt_return_blocks_signal(self):
        rows = REL.generate_signals(config(), bars(), features(None))
        self.assertEqual(rows, [])

    def test_complete_configured_alt_coverage_allows_signal(self):
        rows = REL.generate_signals(config(), bars(), features(0.0))
        self.assertGreater(len(rows), 0)
        self.assertTrue(all(row["reason"] == "relative_strength_rotation" for row in rows))

    def test_one_missing_alt_cannot_change_majority_denominator(self):
        cfg = config()
        f = features(1.0)
        f["alt_returns"]["ETHUSDT"][1][1] = 1.0
        f["alt_returns"]["BCHUSDT"][1][1] = -1.0
        f["alt_returns"]["SOLUSDT"][1][1] = None
        rows = REL.generate_signals(cfg, bars(), f)
        self.assertEqual(rows, [])

    def test_missing_alt_is_not_imputed_from_neighboring_timestamp(self):
        cfg = config()
        f = features(0.0)
        f["alt_returns"]["SOLUSDT"][1][2] = None
        rows = REL.generate_signals(cfg, bars(), f)
        signal_indexes = {row["bar_index"] for row in rows}
        self.assertNotIn(2, signal_indexes)
        self.assertIn(1, signal_indexes)


if __name__ == "__main__":
    unittest.main()

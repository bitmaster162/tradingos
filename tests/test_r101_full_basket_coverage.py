from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "tools" / "relative_strength_rotation_nested_holdout.py"
    spec = importlib.util.spec_from_file_location("r101_relative_strength", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


REL = load_module()


def config(max_hold=1):
    return REL.RotationConfig(
        strategy_id="basket-test",
        interval="1h",
        side="LONG",
        mode="btc_leads",
        lookback=1,
        min_rel_strength_pct=1.0,
        alt_symbols=("ETHUSDT", "SOLUSDT"),
        alt_confirm="none",
        atr_regime_filter="none",
        stop_atr=1.0,
        take_atr=2.0,
        max_hold_bars=max_hold,
    )


def bars(n):
    return [SimpleNamespace(ts=f"2026-01-01T{i:02d}:00:00+00:00") for i in range(n)]


def features(n, sol_values):
    return {
        "atr": [1.0] * n,
        "atr_ratio": [1.0] * n,
        "btc_closes": [100.0] * n,
        "alt_closes": {
            "ETHUSDT": [100.0] * n,
            "SOLUSDT": [100.0] * n,
        },
        "btc_returns": {1: [None] + [2.0] * (n - 1)},
        "alt_returns": {
            "ETHUSDT": {1: [None] + [0.0] * (n - 1)},
            "SOLUSDT": {1: sol_values},
        },
    }


class FullBasketCoverageTests(unittest.TestCase):
    def test_missing_constituent_blocks_partial_basket_signal(self):
        f=features(4,[None,None,0.0,0.0])
        signals=REL.generate_signals(config(),bars(4),f)
        self.assertEqual([s["bar_index"] for s in signals],[2])
        self.assertEqual(signals[0]["alt_coverage_count"],2)
        self.assertEqual(signals[0]["alt_expected_count"],2)
        self.assertEqual(signals[0]["alt_symbols"],["ETHUSDT","SOLUSDT"])

    def test_all_missing_second_constituent_yields_no_signal(self):
        f=features(4,[None,None,None,None])
        self.assertEqual(REL.generate_signals(config(),bars(4),f),[])

    def test_exact_max_hold_horizon_allows_last_complete_signal(self):
        f=features(3,[None,0.0,0.0])
        signals=REL.generate_signals(config(max_hold=1),bars(3),f)
        self.assertIn(1,[s["bar_index"] for s in signals])
        self.assertNotIn(2,[s["bar_index"] for s in signals])

    def test_strategy_id_does_not_hide_dynamic_basket_shrink(self):
        f=features(5,[None,0.0,None,0.0,0.0])
        signals=REL.generate_signals(config(),bars(5),f)
        self.assertNotIn(2,[s["bar_index"] for s in signals])
        for row in signals:
            self.assertEqual(row["alt_coverage_count"],len(config().alt_symbols))


if __name__=="__main__":
    unittest.main()

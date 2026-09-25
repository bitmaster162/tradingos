from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    name = "r101_relative_strength"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "tools" / "relative_strength_rotation_nested_holdout.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


REL = load_module()


def config(alts=("ETHUSDT", "SOLUSDT", "BCHUSDT")):
    return REL.RotationConfig(
        strategy_id="fixed-basket-test",
        interval="1h",
        side="LONG",
        mode="btc_leads",
        lookback=1,
        min_rel_strength_pct=1.0,
        alt_symbols=alts,
        alt_confirm="none",
        atr_regime_filter="none",
        stop_atr=1.0,
        take_atr=2.0,
        max_hold_bars=1,
    )


def features(sol_value=1.0, bch_value=1.0):
    n = 6
    return {
        "btc_closes": [100.0] * n,
        "atr": [1.0] * n,
        "atr_ratio": [1.0] * n,
        "btc_returns": {1: [None, 3.0, 3.0, 3.0, 3.0, 3.0]},
        "alt_closes": {
            "ETHUSDT": [1.0] * n,
            "SOLUSDT": [1.0] * n,
            "BCHUSDT": [1.0] * n,
        },
        "alt_returns": {
            "ETHUSDT": {1: [None, 1.0, 1.0, 1.0, 1.0, 1.0]},
            "SOLUSDT": {1: [None, sol_value, sol_value, sol_value, sol_value, sol_value]},
            "BCHUSDT": {1: [None, bch_value, bch_value, bch_value, bch_value, bch_value]},
        },
    }


class FixedBasketAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.bars = [SimpleNamespace() for _ in range(6)]

    def test_complete_configured_basket_can_signal(self):
        rows = REL.generate_signals(config(), self.bars, features())
        self.assertGreater(len(rows), 0)

    def test_missing_sol_return_blocks_signal_instead_of_shrinking_basket(self):
        f = features()
        f["alt_returns"]["SOLUSDT"][1][1] = None
        rows = REL.generate_signals(config(), self.bars, f)
        self.assertFalse(any(row["bar_index"] == 1 for row in rows))

    def test_missing_bch_return_blocks_signal(self):
        f = features()
        f["alt_returns"]["BCHUSDT"][1][2] = None
        rows = REL.generate_signals(config(), self.bars, f)
        self.assertFalse(any(row["bar_index"] == 2 for row in rows))

    def test_single_alt_config_does_not_require_unconfigured_symbols(self):
        f = features()
        f["alt_returns"]["SOLUSDT"][1][1] = None
        f["alt_returns"]["BCHUSDT"][1][1] = None
        rows = REL.generate_signals(config(("ETHUSDT",)), self.bars, f)
        self.assertTrue(any(row["bar_index"] == 1 for row in rows))

    def test_config_identity_is_not_reinterpreted_as_partial_basket(self):
        cfg = config()
        f = features()
        f["alt_returns"]["SOLUSDT"][1][1] = None
        partial = REL.generate_signals(cfg, self.bars, f)
        full = REL.generate_signals(cfg, self.bars, features())
        self.assertFalse(any(row["bar_index"] == 1 for row in partial))
        self.assertTrue(any(row["bar_index"] == 1 for row in full))


if __name__ == "__main__":
    unittest.main()

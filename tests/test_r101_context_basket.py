from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "r101_relative_strength",
    ROOT / "tools" / "relative_strength_rotation_nested_holdout.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def config(*, alts=("ETHUSDT", "SOLUSDT")):
    return MODULE.RotationConfig(
        strategy_id="r101-test",
        interval="1h",
        side="LONG",
        mode="btc_leads",
        lookback=2,
        min_rel_strength_pct=1.0,
        alt_symbols=alts,
        alt_confirm="none",
        atr_regime_filter="high",
        stop_atr=1.0,
        take_atr=2.0,
        max_hold_bars=1,
    )


class StrictContextBasketTests(unittest.TestCase):
    def test_complete_configured_basket_is_returned_in_declared_order(self):
        features={
            "alt_returns":{
                "ETHUSDT":{2:[None,None,1.0]},
                "SOLUSDT":{2:[None,None,2.0]},
            }
        }
        self.assertEqual(
            MODULE.configured_alt_returns(config(),features,2),
            [1.0,2.0],
        )

    def test_missing_later_listed_symbol_blocks_basket(self):
        features={
            "alt_returns":{
                "ETHUSDT":{2:[None,None,1.0]},
                "SOLUSDT":{2:[None,None,None]},
            }
        }
        self.assertIsNone(MODULE.configured_alt_returns(config(),features,2))

    def test_absent_configured_symbol_blocks_basket(self):
        features={"alt_returns":{"ETHUSDT":{2:[None,None,1.0]}}}
        self.assertIsNone(MODULE.configured_alt_returns(config(),features,2))

    def test_missing_lookback_blocks_basket(self):
        features={
            "alt_returns":{
                "ETHUSDT":{2:[None,None,1.0]},
                "SOLUSDT":{6:[None,None,2.0]},
            }
        }
        self.assertIsNone(MODULE.configured_alt_returns(config(),features,2))

    def test_generate_signals_does_not_shrink_multi_alt_basket(self):
        bars=[SimpleNamespace(ts=f"t{i}") for i in range(8)]
        features={
            "atr":[None,None,1.0,1.0,1.0,1.0,1.0,1.0],
            "atr_ratio":[None,None,1.2,1.2,1.2,1.2,1.2,1.2],
            "btc_closes":[100.0]*8,
            "btc_returns":{2:[None,None,5.0,5.0,5.0,5.0,5.0,5.0]},
            "alt_returns":{
                "ETHUSDT":{2:[None,None,1.0,1.0,1.0,1.0,1.0,1.0]},
                "SOLUSDT":{2:[None,None,None,None,None,None,None,None]},
            },
        }
        self.assertEqual(MODULE.generate_signals(config(),bars,features),[])

        features["alt_returns"]["SOLUSDT"][2]=2.0
        signals=MODULE.generate_signals(config(),bars,features)
        self.assertEqual(len(signals),1)
        self.assertAlmostEqual(signals[0]["alt_basket_return_pct"],1.5)

    def test_eth_only_config_is_not_blocked_by_sol_absence(self):
        features={
            "alt_returns":{
                "ETHUSDT":{2:[None,None,1.0]},
                "SOLUSDT":{2:[None,None,None]},
            }
        }
        self.assertEqual(
            MODULE.configured_alt_returns(config(alts=("ETHUSDT",)),features,2),
            [1.0],
        )


if __name__=="__main__":
    unittest.main()

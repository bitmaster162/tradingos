from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


REL = load_module("r98_relative_strength_nested", "tools/relative_strength_rotation_nested_holdout.py")
SES = load_module("r98_session_compression_nested", "tools/session_volatility_compression_breakout_nested_holdout.py")


def fake_window():
    return {
        "signals": 50,
        "summary": {
            "trades": 50,
            "winrate_pct": 50.0,
            "expectancy_r": 0.25,
            "max_drawdown_r": -2.0,
        },
        "stable_folds": 3,
        "folds": [],
        "cost_stress": {"summary": {"expectancy_r": 0.10}},
        "bootstrap_probability_expectancy_gt_0": 0.9,
        "trades": [],
    }


class StrictOosOpenOnceTests(unittest.TestCase):
    def relative_config(self):
        return REL.RotationConfig(
            strategy_id="relative-test",
            interval="1h",
            side="LONG",
            mode="btc_leads",
            lookback=12,
            min_rel_strength_pct=2.0,
            alt_symbols=("ETHUSDT",),
            alt_confirm="alts_up",
            atr_regime_filter="atr_high",
            stop_atr=1.0,
            take_atr=2.0,
            max_hold_bars=16,
        )

    def session_config(self):
        return SES.CompressionConfig(
            strategy_id="session-test",
            interval="1h",
            side="LONG",
            session=SES.SessionWindow("test", 0, 23),
            lookback=12,
            max_range_atr=1.0,
            breakout_buffer_atr=0.1,
            min_volume_z=0.0,
            trend_filter="none",
            stop_atr=1.0,
            take_atr=2.0,
            max_hold_bars=16,
        )

    def test_relative_grid_never_opens_oos(self):
        calls = []
        def evaluate(config, bars, features, args, folds):
            calls.append(bars[0])
            return fake_window()
        args = SimpleNamespace(
            folds=4,
            train_min_trades=1, train_min_expectancy_r=0.0,
            train_min_stable_folds=1, train_min_winrate_pct=0.0, train_max_drawdown_r=99.0,
            validation_min_trades=1, validation_min_expectancy_r=0.0,
            validation_min_stable_folds=1, validation_min_winrate_pct=0.0, validation_max_drawdown_r=99.0,
            oos_min_trades=1, oos_min_expectancy_r=0.0,
            oos_min_stable_folds=1, oos_min_winrate_pct=0.0, oos_max_drawdown_r=99.0,
        )
        windows = {
            "train": {"bars": ["train"], "features": {}},
            "validation": {"bars": ["validation"], "features": {}},
            "oos": {"bars": ["oos"], "features": {}},
        }
        with patch.object(REL, "evaluate_window", side_effect=evaluate), patch.object(
            REL, "gate", return_value={"pass": True, "checks": {}}
        ):
            row = REL.evaluate_config(self.relative_config(), windows, args)
            self.assertEqual(calls, ["train", "validation"])
            self.assertEqual(row["oos"]["status"], "UNOPENED")
            opened = REL.open_oos_once(row, self.relative_config(), windows, args)
            self.assertEqual(calls, ["train", "validation", "oos"])
            self.assertEqual(opened["oos"]["status"], "OPENED_ONCE_FOR_FROZEN_VALIDATION_WINNER")
            with self.assertRaisesRegex(ValueError, "already opened"):
                REL.open_oos_once(opened, self.relative_config(), windows, args)

    def test_session_grid_never_opens_oos(self):
        calls = []
        def evaluate(config, bars, features, cost, stress, folds):
            calls.append(bars[0])
            return fake_window()
        args = SimpleNamespace(cost_bps_per_side=7.0, stress_extra_bps_per_side=7.0, folds=4)
        windows = {
            "train": {"bars": ["train"], "features": {}},
            "validation": {"bars": ["validation"], "features": {}},
            "oos": {"bars": ["oos"], "features": {}},
        }
        passing = {"pass": True, "checks": {}}
        with patch.object(SES, "evaluate_window", side_effect=evaluate),              patch.object(SES, "train_gate", return_value=passing),              patch.object(SES, "validation_gate", return_value=passing),              patch.object(SES, "oos_gate", return_value=passing):
            row = SES.evaluate_config(self.session_config(), windows, args)
            self.assertEqual(calls, ["train", "validation"])
            self.assertEqual(row["oos"]["status"], "UNOPENED")
            opened = SES.open_oos_once(row, self.session_config(), windows, args)
            self.assertEqual(calls, ["train", "validation", "oos"])
            self.assertEqual(opened["decision"], "candidate_needs_forward_proof")

    def test_relative_pre_oos_sort_key_cannot_read_oos(self):
        row = {
            "gates": {"train": {"pass": True}, "validation": {"pass": True}},
            "train": {"summary": {"expectancy_r": 0.1}},
            "validation": {"summary": {"expectancy_r": 0.2, "trades": 40}},
            "oos": {"summary": {"expectancy_r": -999.0}},
        }
        changed = copy.deepcopy(row)
        changed["oos"]["summary"]["expectancy_r"] = 999.0
        self.assertEqual(REL.result_sort_key(row), REL.result_sort_key(changed))

    def test_session_pre_oos_sort_key_cannot_read_oos(self):
        row = {
            "gates": {"train": {"pass": True}, "validation": {"pass": True}},
            "train": {"summary": {"expectancy_r": 0.1}},
            "validation": {"summary": {"expectancy_r": 0.2, "trades": 40}},
            "oos": {"summary": {"expectancy_r": -999.0}},
        }
        changed = copy.deepcopy(row)
        changed["oos"]["summary"]["expectancy_r"] = 999.0
        self.assertEqual(SES.result_sort_key(row), SES.result_sort_key(changed))

    def test_unqualified_rows_cannot_open_oos(self):
        row = {
            "gates": {"train": {"pass": True}, "validation": {"pass": False}, "oos": {"pass": False}},
            "oos": {"status": "UNOPENED"},
        }
        args = SimpleNamespace()
        with self.assertRaisesRegex(ValueError, "train\+validation-qualified"):
            REL.open_oos_once(row, self.relative_config(), {}, args)


if __name__ == "__main__":
    unittest.main()

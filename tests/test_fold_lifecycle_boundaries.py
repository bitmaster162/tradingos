from __future__ import annotations

import importlib.util
import statistics
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


REL = load_module("r100_relative", "tools/relative_strength_rotation_nested_holdout.py")
SES = load_module("r100_session", "tools/session_volatility_compression_breakout_nested_holdout.py")


def bars(n=8):
    return [SimpleNamespace(ts=f"2026-01-01T{i:02d}:00:00+00:00") for i in range(n)]


def row(entry_hour, entry_minute, exit_hour, exit_minute, value=1.0):
    return SimpleNamespace(
        entry_ts=f"2026-01-01T{entry_hour:02d}:{entry_minute:02d}:00+00:00",
        exit_ts=f"2026-01-01T{exit_hour:02d}:{exit_minute:02d}:00+00:00",
        r_net=value,
    )


def summary(rows):
    vals=[float(x.r_net) for x in rows]
    return {"trades":len(vals),"expectancy_r":statistics.mean(vals) if vals else None}


class FoldLifecycleBoundaryTests(unittest.TestCase):
    def test_relative_excludes_exit_crossing_fold_end(self):
        rows=[row(1,30,2,30)]
        with patch.object(REL,"summarize_trades",side_effect=summary):
            result=REL.fold_summaries(rows,bars(),4)
        self.assertEqual(result[0]["trades"],0)
        self.assertEqual(result[0]["excluded_cross_boundary"],1)
        self.assertEqual(result[0]["lifecycle_policy"],"entry_and_exit_inside_fold")

    def test_session_excludes_exit_crossing_fold_end(self):
        rows=[row(1,30,2,30)]
        with patch.object(SES,"summarize_trades",side_effect=summary):
            result=SES.fold_summaries_expectancy(rows,bars(),4)
        self.assertEqual(result[0]["trades"],0)
        self.assertEqual(result[0]["excluded_cross_boundary"],1)

    def test_fully_contained_trade_is_kept(self):
        rows=[row(0,30,1,30)]
        with patch.object(REL,"summarize_trades",side_effect=summary):
            result=REL.fold_summaries(rows,bars(),4)
        self.assertEqual(result[0]["trades"],1)
        self.assertEqual(result[0]["excluded_cross_boundary"],0)

    def test_exit_exactly_at_exclusive_boundary_is_excluded(self):
        rows=[row(1,30,2,0)]
        with patch.object(SES,"summarize_trades",side_effect=summary):
            result=SES.fold_summaries_expectancy(rows,bars(),4)
        self.assertEqual(result[0]["trades"],0)
        self.assertEqual(result[0]["excluded_cross_boundary"],1)

    def test_invalid_trade_chronology_is_rejected(self):
        rows=[row(1,30,1,0)]
        with patch.object(REL,"summarize_trades",side_effect=summary):
            with self.assertRaisesRegex(ValueError,"exit precedes entry"):
                REL.fold_summaries(rows,bars(),4)

    def test_last_fold_keeps_lifecycle_inside_evaluation_window(self):
        rows=[row(6,30,7,30)]
        with patch.object(REL,"summarize_trades",side_effect=summary):
            result=REL.fold_summaries(rows,bars(),4)
        self.assertEqual(result[-1]["trades"],1)
        self.assertEqual(result[-1]["end_exclusive_ts"],None)


if __name__=="__main__":
    unittest.main()

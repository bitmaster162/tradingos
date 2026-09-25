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


REL = load_module("r99_relative_strength_nested", "tools/relative_strength_rotation_nested_holdout.py")
SES = load_module("r99_session_compression_nested", "tools/session_volatility_compression_breakout_nested_holdout.py")


def bars(n=8):
    return [
        SimpleNamespace(ts=f"2026-01-01T{i:02d}:00:00+00:00")
        for i in range(n)
    ]


def trades(hours, values=None):
    values = values or [1.0] * len(hours)
    return [
        SimpleNamespace(
            entry_ts=f"2026-01-01T{hour:02d}:30:00+00:00",
            r_net=value,
        )
        for hour, value in zip(hours, values)
    ]


def summary(rows):
    vals = [float(row.r_net) for row in rows]
    return {
        "trades": len(vals),
        "expectancy_r": statistics.mean(vals) if vals else None,
    }


class TimeFoldAndBootstrapTests(unittest.TestCase):
    def test_relative_folds_partition_time_not_trade_count(self):
        rows = trades([0, 0, 1, 1], [1, 1, 1, 1])
        with patch.object(REL, "summarize_trades", side_effect=summary):
            result = REL.fold_summaries(rows, bars(), 4)
        self.assertEqual([x["trades"] for x in result], [4, 0, 0, 0])
        self.assertTrue(all(x["partition"] == "equal_bar_time_window" for x in result))

    def test_session_folds_partition_time_not_trade_count(self):
        rows = trades([0, 0, 1, 1], [1, 1, 1, 1])
        with patch.object(SES, "summarize_trades", side_effect=summary):
            result = SES.fold_summaries_expectancy(rows, bars(), 4)
        self.assertEqual([x["trades"] for x in result], [4, 0, 0, 0])
        self.assertTrue(all(x["partition"] == "equal_bar_time_window" for x in result))

    def test_empty_time_period_is_not_hidden_by_equal_trade_chunks(self):
        rows = trades([0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        with patch.object(SES, "summarize_trades", side_effect=summary):
            result = SES.fold_summaries_expectancy(rows, bars(), 4)
        self.assertEqual([x["trades"] for x in result], [10, 0, 0, 0])
        self.assertEqual(sum(bool(x["stable"]) for x in result), 1)

    def test_moving_block_bootstrap_is_order_sensitive(self):
        clustered = [1, 1, 1, -1, -1, -1]
        alternating = [1, -1, 1, -1, 1, -1]
        p_clustered = SES.bootstrap_positive_probability(
            clustered, iterations=5000, seed=20260630, block_size=3
        )
        p_alternating = SES.bootstrap_positive_probability(
            alternating, iterations=5000, seed=20260630, block_size=3
        )
        self.assertGreater(abs(p_clustered - p_alternating), 0.05)

    def test_bootstrap_rejects_boolean_and_nonpositive_block(self):
        with self.assertRaises(ValueError):
            SES.bootstrap_positive_probability([1.0, -1.0], block_size=True)
        with self.assertRaises(ValueError):
            SES.bootstrap_positive_probability([1.0, -1.0], block_size=0)

    def test_fold_count_rejects_boolean(self):
        with self.assertRaises(ValueError):
            REL.fold_summaries([], bars(), True)
        with self.assertRaises(ValueError):
            SES.fold_summaries_expectancy([], bars(), True)


if __name__ == "__main__":
    unittest.main()

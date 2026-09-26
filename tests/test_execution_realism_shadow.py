from __future__ import annotations

import unittest
from types import SimpleNamespace

from tools.execution_realism_shadow import (
    replay_gap_aware,
    simulate_trade_gap_aware,
    standard_8h_boundaries_crossed,
)
from tools.liquidity_sweep_hardening import simulate_trade


def bar(ts: str, open_: float, high: float, low: float, close: float):
    return SimpleNamespace(ts=ts, open=open_, high=high, low=low, close=close)


def long_signal():
    return {"bar_index": 0, "side_hint": "LONG", "atr": 10.0}


def short_signal():
    return {"bar_index": 0, "side_hint": "SHORT", "atr": 10.0}


class ExecutionRealismShadowTests(unittest.TestCase):
    def test_long_gap_through_stop_uses_worse_open(self):
        bars = [
            bar("2026-01-01T00:00:00+00:00", 100, 101, 99, 100),
            bar("2026-01-01T01:00:00+00:00", 100, 105, 95, 101),
            bar("2026-01-01T02:00:00+00:00", 80, 85, 75, 82),
        ]
        legacy = simulate_trade(
            dataset_id="x",
            strategy_id="s",
            bars=bars,
            signal=long_signal(),
            stop_atr=1.0,
            take_atr=2.0,
            max_hold_bars=3,
            cost_bps_per_side=0.0,
        )
        shadow, diagnostic = simulate_trade_gap_aware(
            dataset_id="x",
            strategy_id="s",
            bars=bars,
            signal=long_signal(),
            stop_atr=1.0,
            take_atr=2.0,
            max_hold_bars=3,
            cost_bps_per_side=0.0,
        )
        self.assertEqual(legacy.exit, 90.0)
        self.assertEqual(shadow.exit, 80.0)
        self.assertEqual(legacy.r_net, -1.0)
        self.assertEqual(shadow.r_net, -2.0)
        self.assertTrue(diagnostic["stop_gap_open"])
        self.assertEqual(shadow.exit_reason, "stop_gap_open")

    def test_short_gap_through_stop_uses_worse_open(self):
        bars = [
            bar("2026-01-01T00:00:00+00:00", 100, 101, 99, 100),
            bar("2026-01-01T01:00:00+00:00", 100, 105, 95, 99),
            bar("2026-01-01T02:00:00+00:00", 125, 130, 120, 126),
        ]
        shadow, diagnostic = simulate_trade_gap_aware(
            dataset_id="x",
            strategy_id="s",
            bars=bars,
            signal=short_signal(),
            stop_atr=1.0,
            take_atr=2.0,
            max_hold_bars=3,
            cost_bps_per_side=0.0,
        )
        self.assertEqual(shadow.exit, 125.0)
        self.assertEqual(shadow.r_net, -2.5)
        self.assertTrue(diagnostic["stop_gap_open"])

    def test_favorable_gap_is_capped_at_target(self):
        bars = [
            bar("2026-01-01T00:00:00+00:00", 100, 101, 99, 100),
            bar("2026-01-01T01:00:00+00:00", 100, 105, 95, 101),
            bar("2026-01-01T02:00:00+00:00", 130, 132, 129, 131),
        ]
        shadow, diagnostic = simulate_trade_gap_aware(
            dataset_id="x",
            strategy_id="s",
            bars=bars,
            signal=long_signal(),
            stop_atr=1.0,
            take_atr=2.0,
            max_hold_bars=3,
            cost_bps_per_side=0.0,
        )
        self.assertEqual(shadow.exit, 120.0)
        self.assertEqual(shadow.r_net, 2.0)
        self.assertTrue(diagnostic["favorable_take_gap"])
        self.assertEqual(shadow.exit_reason, "take_gap_capped_at_target")

    def test_same_bar_ambiguity_remains_stop_first(self):
        bars = [
            bar("2026-01-01T00:00:00+00:00", 100, 101, 99, 100),
            bar("2026-01-01T01:00:00+00:00", 100, 121, 89, 100),
        ]
        shadow, diagnostic = simulate_trade_gap_aware(
            dataset_id="x",
            strategy_id="s",
            bars=bars,
            signal=long_signal(),
            stop_atr=1.0,
            take_atr=2.0,
            max_hold_bars=1,
            cost_bps_per_side=0.0,
        )
        self.assertEqual(shadow.exit, 90.0)
        self.assertEqual(shadow.r_net, -1.0)
        self.assertTrue(diagnostic["same_bar_ambiguous"])

    def test_standard_funding_boundary_counter_is_strict_interior_only(self):
        self.assertEqual(
            standard_8h_boundaries_crossed(
                "2026-01-01T01:00:00+00:00",
                "2026-01-01T17:00:00+00:00",
            ),
            2,
        )
        self.assertEqual(
            standard_8h_boundaries_crossed(
                "2026-01-01T08:00:00+00:00",
                "2026-01-01T16:00:00+00:00",
            ),
            0,
        )

    def test_replay_reports_funding_as_unmodeled_not_zero_cost(self):
        bars = [
            bar("2026-01-01T00:00:00+00:00", 100, 101, 99, 100),
            bar("2026-01-01T01:00:00+00:00", 100, 105, 95, 101),
        ]
        for hour in range(2, 19):
            bars.append(
                bar(
                    f"2026-01-01T{hour:02d}:00:00+00:00",
                    101,
                    105,
                    95,
                    101,
                )
            )
        result = replay_gap_aware(
            dataset_id="x",
            strategy_id="s",
            bars=bars,
            signals=[long_signal()],
            stop_atr=10.0,
            take_atr=10.0,
            max_hold_bars=17,
            cost_bps_per_side=0.0,
            no_overlap=True,
        )
        self.assertEqual(result["trade_count"], 1)
        self.assertGreater(
            result["standard_8h_funding_boundaries_crossed"],
            0,
        )
        self.assertEqual(
            result["funding_status"],
            "UNMODELED_NO_HISTORICAL_RATE_SERIES",
        )
        self.assertEqual(result["posthoc_use"], "DOWNGRADE_ONLY_NEVER_PROMOTION")


if __name__ == "__main__":
    unittest.main()

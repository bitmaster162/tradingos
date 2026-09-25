from __future__ import annotations

import unittest
from types import SimpleNamespace

from tools.liquidity_sweep_hardening import simulate_trade


def bar(ts, open_, high, low, close):
    return SimpleNamespace(ts=ts, open=open_, high=high, low=low, close=close)


def signal(side="LONG", atr=2.0):
    return {"bar_index": 0, "side_hint": side, "atr": atr}


class GapCensorHoldTests(unittest.TestCase):
    def simulate(self, bars, *, side="LONG", hold=2, stop=1.0, take=2.0):
        return simulate_trade(
            dataset_id="synthetic",
            strategy_id="s",
            bars=bars,
            signal=signal(side),
            stop_atr=stop,
            take_atr=take,
            max_hold_bars=hold,
            cost_bps_per_side=0.0,
        )

    def test_long_adverse_gap_fills_at_open_not_stop(self):
        rows=[
            bar("s0",100,101,99,100),
            bar("e1",100,101,99,100),
            bar("e2",95,96,94,95),
        ]
        t=self.simulate(rows)
        self.assertEqual(t.exit_reason,"gap_stop_open")
        self.assertEqual(t.stop,98.0)
        self.assertEqual(t.exit,95.0)
        self.assertEqual(t.r_net,-2.5)

    def test_short_adverse_gap_fills_at_open_not_stop(self):
        rows=[
            bar("s0",100,101,99,100),
            bar("e1",100,101,99,100),
            bar("e2",105,106,104,105),
        ]
        t=self.simulate(rows,side="SHORT")
        self.assertEqual(t.exit_reason,"gap_stop_open")
        self.assertEqual(t.stop,102.0)
        self.assertEqual(t.exit,105.0)
        self.assertEqual(t.r_net,-2.5)

    def test_favorable_take_gap_gets_no_extra_price_improvement(self):
        rows=[
            bar("s0",100,101,99,100),
            bar("e1",100,101,99,100),
            bar("e2",110,111,109,110),
        ]
        t=self.simulate(rows)
        self.assertEqual(t.exit_reason,"gap_take_conservative")
        self.assertEqual(t.take,104.0)
        self.assertEqual(t.exit,104.0)
        self.assertEqual(t.r_net,2.0)

    def test_max_hold_one_uses_exactly_one_entry_bar(self):
        rows=[
            bar("s0",100,101,99,100),
            bar("e1",100,101,99,100.5),
            bar("future",100.5,110,90,105),
        ]
        t=self.simulate(rows,hold=1)
        self.assertEqual(t.exit_reason,"time_exit")
        self.assertEqual(t.exit_ts,"e1")
        self.assertEqual(t.bars_held,1)

    def test_incomplete_time_exit_horizon_is_censored(self):
        rows=[
            bar("s0",100,101,99,100),
            bar("e1",100,101,99,100.5),
        ]
        self.assertIsNone(self.simulate(rows,hold=2))

    def test_resolved_stop_before_truncated_horizon_is_kept(self):
        rows=[
            bar("s0",100,101,99,100),
            bar("e1",100,101,97,98),
        ]
        t=self.simulate(rows,hold=3)
        self.assertIsNotNone(t)
        self.assertEqual(t.exit_reason,"stop_loss")
        self.assertEqual(t.bars_held,1)

    def test_same_bar_collision_remains_stop_first(self):
        rows=[
            bar("s0",100,101,99,100),
            bar("e1",100,105,97,100),
        ]
        t=self.simulate(rows,hold=1)
        self.assertEqual(t.exit_reason,"stop_first_same_bar")
        self.assertEqual(t.exit,98.0)

    def test_invalid_side_is_rejected(self):
        rows=[bar("s0",100,101,99,100),bar("e1",100,101,99,100)]
        self.assertIsNone(self.simulate(rows,side="SIDEWAYS",hold=1))

    def test_boolean_or_zero_hold_is_rejected(self):
        rows=[bar("s0",100,101,99,100),bar("e1",100,101,99,100)]
        self.assertIsNone(self.simulate(rows,hold=True))
        self.assertIsNone(self.simulate(rows,hold=0))


if __name__=="__main__":
    unittest.main()

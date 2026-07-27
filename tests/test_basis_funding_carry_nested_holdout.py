from __future__ import annotations

import argparse
import unittest

from tools.basis_funding_carry_nested_holdout import gate, split_index, trade_pnl


class BasisFundingCarryNestedHoldoutTests(unittest.TestCase):
    def test_delta_neutral_flat_prices_receive_positive_funding_net_of_zero_costs(self) -> None:
        entry = {"time_ms": 0, "spot_open": 100.0, "futures_open": 101.0}
        exit_row = {"time_ms": 28_800_000, "spot_open": 100.0, "futures_open": 101.0}
        pnl = trade_pnl(
            entry=entry,
            exit_row=exit_row,
            events=[{"timestamp": 28_800_000.0, "rate": 0.001}],
            futures_prices={28_800_000: 101.0},
            fee_bps=0.0,
            slippage_bps=0.0,
        )
        self.assertAlmostEqual(pnl["price_pnl_quote"], 0.0)
        self.assertAlmostEqual(pnl["funding_pnl_quote"], 0.101)
        self.assertGreater(pnl["net_return_bps_on_gross_capital"], 0.0)

    def test_four_leg_side_fees_make_flat_no_funding_trade_negative(self) -> None:
        entry = {"time_ms": 0, "spot_open": 100.0, "futures_open": 100.0}
        exit_row = {"time_ms": 3_600_000, "spot_open": 100.0, "futures_open": 100.0}
        pnl = trade_pnl(
            entry=entry,
            exit_row=exit_row,
            events=[],
            futures_prices={},
            fee_bps=5.0,
            slippage_bps=0.0,
        )
        self.assertAlmostEqual(pnl["fees_quote"], 0.2)
        self.assertLess(pnl["net_return_bps_on_gross_capital"], 0.0)

    def test_calendar_split_uses_first_row_at_boundary(self) -> None:
        rows = [{"time": "2024-12-31T23:00:00+00:00"}, {"time": "2025-01-01T00:00:00+00:00"}]
        self.assertEqual(split_index(rows, "2025-01-01T00:00:00+00:00"), 1)

    def test_stress_must_remain_positive(self) -> None:
        args = argparse.Namespace(
            min_train_trades=20,
            min_train_mean_bps=5.0,
            min_train_positive_pct=55.0,
            max_train_drawdown_bps=150.0,
            min_train_positive_folds=3,
        )
        result = {
            "summary": {"trades": 30, "mean_net_bps": 8.0, "positive_pct": 60.0, "max_drawdown_bps": -50.0},
            "positive_folds": 4,
            "cost_stress": {"summary": {"mean_net_bps": -0.1}},
        }
        self.assertFalse(gate(result, stage="train", args=args)["pass"])


if __name__ == "__main__":
    unittest.main()

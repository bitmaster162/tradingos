from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.basis_funding_carry_multi_symbol_nested_holdout import positive_folds_by_time, strict_gate, with_symbol


def test_with_symbol_adds_symbol_to_trades() -> None:
    trades = [{"entry_time": "2024-01-01T00:00:00+00:00", "net_return_bps_on_gross_capital": 1.0}]
    assert with_symbol("ETHUSDT", trades)[0]["symbol"] == "ETHUSDT"


def test_positive_folds_by_time_uses_carry_return_key() -> None:
    trades = [
        {"symbol": "B", "entry_time": "2024-01-01T01:00:00+00:00", "net_return_bps_on_gross_capital": 2.0},
        {"symbol": "A", "entry_time": "2024-01-01T00:00:00+00:00", "net_return_bps_on_gross_capital": 2.0},
        {"symbol": "A", "entry_time": "2024-01-01T02:00:00+00:00", "net_return_bps_on_gross_capital": 2.0},
        {"symbol": "A", "entry_time": "2024-01-01T03:00:00+00:00", "net_return_bps_on_gross_capital": 2.0},
        {"symbol": "A", "entry_time": "2024-01-01T04:00:00+00:00", "net_return_bps_on_gross_capital": 2.0},
        {"symbol": "A", "entry_time": "2024-01-01T05:00:00+00:00", "net_return_bps_on_gross_capital": 2.0},
    ]
    assert positive_folds_by_time(trades, 2) == 2


def test_strict_train_gate_requires_bootstrap_probability() -> None:
    args = argparse.Namespace(
        min_train_trades=40,
        min_train_mean_bps=5.0,
        min_train_positive_pct=55.0,
        max_train_drawdown_bps=200.0,
        min_train_positive_folds=3,
        min_train_bootstrap_probability=0.95,
    )
    result = {
        "summary": {"trades": 50, "mean_net_bps": 6.0, "positive_pct": 60.0, "max_drawdown_bps": -20.0},
        "positive_folds": 3,
        "bootstrap_probability_mean_gt_0": 0.5,
        "cost_stress": {"summary": {"mean_net_bps": 1.0}},
    }
    gate = strict_gate(result, stage="train", args=args)
    assert gate["pass"] is False
    assert gate["checks"]["bootstrap_probability"] is False

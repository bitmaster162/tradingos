from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.basis_shock_reversion_multi_symbol_nested_holdout import positive_folds_by_time, with_symbol


def test_with_symbol_adds_symbol_to_trades() -> None:
    trades = [{"entry_time": "2024-01-01T00:00:00+00:00", "net_return_bps": 1.0}]
    assert with_symbol("BTCUSDT", trades)[0]["symbol"] == "BTCUSDT"


def test_positive_folds_by_time_uses_chronological_order() -> None:
    trades = [
        {"symbol": "B", "entry_time": "2024-01-01T01:00:00+00:00", "net_return_bps": 2.0},
        {"symbol": "A", "entry_time": "2024-01-01T00:00:00+00:00", "net_return_bps": 2.0},
        {"symbol": "A", "entry_time": "2024-01-01T02:00:00+00:00", "net_return_bps": 2.0},
        {"symbol": "A", "entry_time": "2024-01-01T03:00:00+00:00", "net_return_bps": 2.0},
        {"symbol": "A", "entry_time": "2024-01-01T04:00:00+00:00", "net_return_bps": 2.0},
        {"symbol": "A", "entry_time": "2024-01-01T05:00:00+00:00", "net_return_bps": 2.0},
    ]
    assert positive_folds_by_time(trades, 2) == 2

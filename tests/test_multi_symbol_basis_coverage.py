from __future__ import annotations

import argparse
import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.multi_symbol_basis_coverage import build_report


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class MultiSymbolBasisCoverageTests(unittest.TestCase):
    def test_builds_panel_for_complete_symbol_and_flags_missing_symbol(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cache = root / "cache"
            rows = [
                {
                    "time": "2024-01-01T00:00:00+00:00",
                    "time_ms": "1704067200000",
                    "open": "100",
                    "high": "101",
                    "low": "99",
                    "close": "100",
                    "volume": "10",
                },
                {
                    "time": "2024-01-01T01:00:00+00:00",
                    "time_ms": "1704070800000",
                    "open": "100",
                    "high": "101",
                    "low": "99",
                    "close": "100",
                    "volume": "11",
                },
            ]
            perp = [dict(row, open="101", close="101", volume="20") for row in rows]
            write_csv(cache / "spot" / "BTCUSDT" / "1h_klines.csv", ["time", "time_ms", "open", "high", "low", "close", "volume"], rows)
            write_csv(cache / "futures" / "BTCUSDT" / "1h_klines.csv", ["time", "time_ms", "open", "high", "low", "close", "volume"], perp)
            write_csv(cache / "futures" / "BTCUSDT" / "funding_raw.csv", ["timestamp", "funding", "price"], [{"timestamp": "1704067200000", "funding": "0.0001", "price": "nan"}])
            args = argparse.Namespace(
                cache_dir=str(cache),
                symbols="BTCUSDT,ETHUSDT",
                interval="1h",
                start="2024-01",
                end="2024-01",
                min_complete_symbols=2,
                min_rows_per_symbol=1,
                shock_train_end="2024-01-02T00:00:00+00:00",
                shock_validation_end="2024-01-03T00:00:00+00:00",
                carry_train_end="2024-01-02T00:00:00+00:00",
                carry_validation_end="2024-01-03T00:00:00+00:00",
                write_panel=True,
                panel_out=str(root / "panel.csv"),
            )
            report = build_report(args)
            self.assertEqual(report["decision"], "basis_coverage_partial_more_symbols_needed")
            self.assertEqual(report["summary"]["complete_symbols"], 1)
            self.assertEqual(report["summary"]["missing_symbols"], ["ETHUSDT"])
            self.assertEqual(report["summary"]["panel_rows"], 2)
            self.assertTrue((root / "panel.csv").is_file())
            self.assertIn("basis_close_bps", (root / "panel.csv").read_text(encoding="utf-8").splitlines()[0])


if __name__ == "__main__":
    unittest.main()

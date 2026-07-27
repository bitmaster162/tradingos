import json
import tempfile
import unittest
from pathlib import Path

from tools.oi_funding_data_quality_matrix import build_report


def write_quality(path: Path, interval: str, classification: str, full_context: float, oi: float, funding: float) -> None:
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-06-29T00:00:00+00:00",
                "summary": {
                    "interval": interval,
                    "classification": classification,
                    "aligned_oi_coverage_pct": oi,
                    "aligned_funding_coverage_pct": funding,
                    "kline_rows": 100,
                    "merged_oi_rows": 100,
                    "merged_funding_rows": 10,
                },
                "replay_trade_coverage": {
                    "trades": 10,
                    "full_context_coverage_pct": full_context,
                },
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )


class OiFundingDataQualityMatrixTests(unittest.TestCase):
    def test_ready_intervals_are_grouped_by_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_quality(docs / "OI_FUNDING_DATA_QUALITY_1H.json", "1h", "oi_guard_data_ready", 100.0, 100.0, 100.0)
            write_quality(docs / "OI_FUNDING_DATA_QUALITY_4H.json", "4h", "oi_guard_data_ready", 99.0, 98.0, 100.0)

            report = build_report(docs)

        self.assertEqual(report["decision"], "oi_funding_quality_ready_for_research")
        self.assertEqual(report["summary"]["ready_interval_ids"], ["1h", "4h"])
        self.assertFalse(report["can_trade"])

    def test_degraded_interval_blocks_matrix_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            write_quality(docs / "OI_FUNDING_DATA_QUALITY_1H.json", "1h", "oi_guard_data_ready", 100.0, 100.0, 100.0)
            write_quality(docs / "OI_FUNDING_DATA_QUALITY_15M.json", "15m", "oi_guard_blocked_sparse_historical_oi", 40.0, 10.0, 100.0)

            report = build_report(docs)

        self.assertEqual(report["decision"], "oi_funding_quality_partial_do_not_promote")
        self.assertEqual(report["summary"]["ready_interval_ids"], ["1h"])
        self.assertEqual(report["summary"]["degraded_interval_ids"], ["15m"])


if __name__ == "__main__":
    unittest.main()

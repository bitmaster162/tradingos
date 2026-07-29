import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("arb_radar_r52_audit.py")
SPEC = importlib.util.spec_from_file_location("arb_radar_r52_audit", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def snapshot(updated="2026-07-29 09:02 UTC", streak=1):
    return {
        "updated": updated,
        "opportunities": [
            {
                "kind": "funding",
                "symbol": "TESTUSDT",
                "long_venue": "a",
                "short_venue": "b",
                "long": "a perp",
                "short": "b perp",
                "apr": 1.0,
                "apr_robust": 0.5,
                "venues_n": 5,
                "breakeven_h": 8,
                "streak": streak,
            }
        ],
        "book": {
            "closed": 10,
            "closed_pnl": 2.5,
            "winrate": 50,
            "by_kind": {"spread": [4, 3.0]},
            "robust_vs_fragile": {"robust": {"n": 4, "pnl": 3.0, "wr": 50}},
        },
    }


ENGINE = '''
def open_paper(conn, o, now_ms, notional=1000):
    if o["kind"] == "spread":
        cost = o["cost_round"] * notional
        pnl = o["gross"] * notional - cost
        sql = "opened_ms,closed_ms"
        values = (o["kind"], o["symbol"], now_ms, now_ms,)
    meta = {"legs": {}}
    json.dumps(meta)
summary = "funding_collected+spread_pnl-entry_cost-exit_cost"
'''
SERVICE = '''
o["_tag"] = ("robust" if (o["kind"] != "funding" or o.get("apr_robust", 0) >= 0.25)
              else "fragile")
'''


class ArbRadarR52AuditTests(unittest.TestCase):
    def test_single_snapshot_fails_closed(self):
        result = MODULE.evaluate(
            [snapshot()],
            ENGINE,
            SERVICE,
            datetime(2026, 7, 29, 13, 25, tzinfo=timezone.utc),
        )
        self.assertEqual(result["terminal"], "INSUFFICIENT_FORWARD_EVIDENCE")
        self.assertEqual(result["candidate_edges"], [])
        self.assertTrue(result["robust_equals_spread_book"])
        self.assertEqual(result["forward_watchlist"][0]["status"], "HOLD_FORWARD_OBSERVATION_ONLY")

    def test_source_defects_are_detected(self):
        result = MODULE.evaluate(
            [snapshot()],
            ENGINE,
            SERVICE,
            datetime(2026, 7, 29, 13, 25, tzinfo=timezone.utc),
        )
        ids = {item["id"] for item in result["source_findings"]}
        self.assertIn("ROBUST_TAG_CLASS_COLLISION", ids)
        self.assertIn("SPREAD_SAME_SNAPSHOT_CLOSE", ids)
        self.assertIn("SPREAD_COST_DOUBLE_SUBTRACTION", ids)
        self.assertIn("PUBLISHED_ROBUST_EQUALS_SPREAD_BOOK", ids)

    def test_three_snapshots_still_do_not_bypass_execution_gates(self):
        snapshots = [
            snapshot("2026-07-29 09:02 UTC", 1),
            snapshot("2026-07-29 09:07 UTC", 2),
            snapshot("2026-07-29 09:12 UTC", 3),
        ]
        result = MODULE.evaluate(
            snapshots,
            ENGINE,
            SERVICE,
            datetime(2026, 7, 29, 9, 13, tzinfo=timezone.utc),
        )
        self.assertEqual(result["terminal"], "EDGE_NOT_SUPPORTED")
        self.assertEqual(result["candidate_edges"], [])
        self.assertEqual(result["forward_watchlist"][0]["independent_snapshot_count"], 3)
        self.assertIn(
            "synchronized_executable_depth_both_legs",
            result["forward_watchlist"][0]["missing_gates"],
        )

    def test_cli_writes_required_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "snapshot.json").write_text(json.dumps(snapshot()), encoding="utf-8")
            (root / "engine.py").write_text(ENGINE, encoding="utf-8")
            (root / "service.py").write_text(SERVICE, encoding="utf-8")
            output = root / "out"
            argv = [
                "--snapshot",
                str(root / "snapshot.json"),
                "--engine",
                str(root / "engine.py"),
                "--service",
                str(root / "service.py"),
                "--out",
                str(output),
                "--captured-at",
                "2026-07-29T13:25:00Z",
            ]
            import sys

            old = sys.argv
            try:
                sys.argv = ["audit", *argv]
                self.assertEqual(MODULE.main(), 0)
            finally:
                sys.argv = old
            for name in (
                "EDGE_RESEARCH_R52.md",
                "candidate_edges.json",
                "rejected_hypotheses.json",
                "forward_watchlist.json",
                "source_manifest.json",
                "audit_summary.json",
            ):
                self.assertTrue((output / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()

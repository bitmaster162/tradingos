from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.edge_forward_candidate_export import select_nested_edge, validate_candidate_lock
from tools.range_refined_observer_scoreboard import expected_strategy_id
from tools.range_refined_forward_observer import historical_rejection
from tools.strategy_mix_forward_scheduler import edge_candidate_locked, family_historically_rejected, range_historically_rejected


class RangeEdgeRuntimeGatingTests(unittest.TestCase):
    def test_range_rejection_pauses_only_default_range_refiner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "holdout.json"
            report.write_text(
                json.dumps({"families": [{"family": "RANGE_REFINED_4H", "decision": "reject_oos_gate_failed"}]}),
                encoding="utf-8",
            )
            default_refiner = root / "RANGE_WATCHLIST_REFINER_2026-06-16.json"
            edge_refiner = root / "EDGE_FORWARD_CANDIDATE_REFINER_2026-06-18.json"
            self.assertIsNotNone(historical_rejection(default_refiner, report))
            self.assertIsNone(historical_rejection(edge_refiner, report))

    def test_nested_edge_accepts_insufficient_positive_observer_candidate(self) -> None:
        selected = {"strategy_id": "train_selected_edge"}
        payload = {
            "families": [
                {
                    "family": "EDGE_FORWARD_4H",
                    "decision": "insufficient_oos_evidence_keep_observer_only",
                    "selected_on_train": selected,
                }
            ]
        }
        result = select_nested_edge(payload)
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["strategy_id"], selected["strategy_id"])

    def test_scheduler_detects_range_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "holdout.json"
            report.write_text(
                json.dumps({"families": [{"family": "RANGE_REFINED_4H", "decision": "reject_oos_gate_failed"}]}),
                encoding="utf-8",
            )
            self.assertTrue(range_historically_rejected(str(report)))

    def test_edge_lock_must_match_candidate_and_safe_boundaries(self) -> None:
        selected = {
            "strategy_id": "edge-v1",
            "base_strategy_id": "base-v1",
            "filter_mode": "oi_contraction",
            "filters": ["oi_contraction"],
            "interval": "4h",
            "side": "LONG",
            "trigger": "sweep_down_reclaim",
            "rr": "1:2",
            "max_hold_bars": 8,
        }
        lock = {
            "enabled": True,
            "candidate": dict(selected),
            "boundaries": {"observer_only": True, "allow_orders": False, "can_trade": False},
        }
        self.assertEqual(validate_candidate_lock(lock, selected), (True, []))
        lock["candidate"]["strategy_id"] = "different"
        self.assertFalse(validate_candidate_lock(lock, selected)[0])

    def test_scheduler_recognizes_safe_edge_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "edge-lock.json"
            path.write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "candidate": {"strategy_id": "edge-v1"},
                        "boundaries": {"observer_only": True, "allow_orders": False, "can_trade": False},
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(edge_candidate_locked(str(path)))

    def test_scheduler_pauses_historically_rejected_trend_family(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trend-lock.json"
            path.write_text(
                json.dumps(
                    {
                        "family": "TREND_MIX_4H",
                        "enabled": False,
                        "status": "historically_rejected_2026-06-23",
                        "boundaries": {"can_trade": False},
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(family_historically_rejected(str(path), "TREND_MIX_4H"))
            self.assertFalse(family_historically_rejected(str(path), "EDGE_FORWARD_4H"))

    def test_scheduler_does_not_pause_paper_shadow_trend_family(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trend-lock.json"
            path.write_text(
                json.dumps(
                    {
                        "family": "TREND_MIX_4H",
                        "enabled": True,
                        "status": "paper_shadow_collecting_2026-06-29",
                        "candidate": {"strategy_id": "paper-shadow-v1"},
                        "boundaries": {
                            "observer_allowed": True,
                            "forward_paper_shadow_allowed": True,
                            "paper_execution_allowed": False,
                            "live_execution_allowed": False,
                            "can_trade": False,
                        },
                    }
                ),
                encoding="utf-8",
            )
            self.assertFalse(family_historically_rejected(str(path), "TREND_MIX_4H"))

    def test_scoreboard_resolves_strategy_from_refiner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "refiner.json"
            path.write_text(json.dumps({"selected_candidate": {"strategy_id": "edge-v1"}}), encoding="utf-8")
            args = type("Args", (), {"strategy_id": "", "refiner_report": str(path)})()
            self.assertEqual(expected_strategy_id(args), "edge-v1")


if __name__ == "__main__":
    unittest.main()

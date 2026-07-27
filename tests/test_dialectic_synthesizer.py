from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from dialectic_synthesizer import build_report  # noqa: E402


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class DialecticSynthesizerTests(unittest.TestCase):
    def build_fixture(self, root: Path, *, p0: int = 0, stopped: list[str] | None = None) -> argparse.Namespace:
        reports = {
            "devil": {
                "can_trade": False,
                "decision": "operational_runtime_healthy_but_edge_unproven",
                "open_severity_counts": {"P0": p0, "P1": 1, "P2": 1, "P3": 0},
                "runtime": {"health": "forward_runtime_healthy_observing"},
            },
            "angel": {
                "can_trade": False,
                "strengths": [] if p0 else [{"id": "safety_containment_verified"}],
                "runtime": {"stopped_or_stale": stopped or []},
            },
            "frontier": {"can_trade": False, "summary": {"families": 3, "promotable": 0, "unsafe": 0}},
            "execution": {
                "can_trade": False,
                "promotion": {"execution_realism_gate_passed": True, "candidate_specific_overlay_present": False},
            },
            "replication": {"can_trade": False, "transition": {"threshold_ready": False}},
            "micro": {"can_trade": False, "decision": "waiting_for_microstructure_readiness"},
        }
        paths = {}
        for name, payload in reports.items():
            path = root / "docs" / f"{name}.json"
            write_json(path, payload)
            paths[name] = str(path)
        return argparse.Namespace(
            active_root=str(root),
            devil_report=paths["devil"],
            angel_report=paths["angel"],
            frontier_report=paths["frontier"],
            execution_gate=paths["execution"],
            replication_monitor=paths["replication"],
            microstructure_gate=paths["micro"],
            book_coverage_diagnostic=None,
            out_prefix=None,
        )

    def test_p0_overrides_positive_case(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report = build_report(self.build_fixture(Path(temp), p0=1))
        self.assertEqual(report["decision"], "dialectic_stop_repair_safety_boundary")
        self.assertFalse(report["can_trade"])
        self.assertTrue(any(item["severity"] == "P0" for item in report["blockers"]))

    def test_stopped_observer_forces_runtime_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report = build_report(self.build_fixture(Path(temp), stopped=["microstructure_book"]))
        self.assertEqual(report["decision"], "dialectic_repair_observer_runtime_then_collect_evidence")
        self.assertFalse(report["can_trade"])

    def test_no_promotable_family_stays_research_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report = build_report(self.build_fixture(Path(temp)))
        self.assertEqual(report["decision"], "dialectic_collect_precommitted_evidence_no_trade")
        self.assertEqual(report["state"]["paper_design_review"], "blocked")
        self.assertFalse(report["can_trade"])


if __name__ == "__main__":
    unittest.main()

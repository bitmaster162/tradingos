from __future__ import annotations

import json
from pathlib import Path

from tools.derivatives_event_research_matrix import build_report


def _write_report(path: Path, *, decision: str, validation_qualified: int, oos_pass: bool | None) -> None:
    selected = None
    if oos_pass is not None:
        selected = {
            "strategy_id": "demo",
            "config": {"family": "funding_extreme_fade", "side": "SHORT", "interval": "4h"},
            "train": {"summary": {"trades": 30, "expectancy_r": 0.1}},
            "validation": {"summary": {"trades": 10, "expectancy_r": 0.2}},
            "oos": {"summary": {"trades": 8, "expectancy_r": 0.3 if oos_pass else -0.1}},
            "oos_gate": {"pass": oos_pass},
        }
    payload = {
        "decision": decision,
        "summary": {"tested": 100, "train_qualified": 5, "validation_qualified": validation_qualified},
        "selected": selected,
        "can_trade": False,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_research_matrix_detects_validation_mirage(tmp_path: Path) -> None:
    report_path = tmp_path / "DERIVATIVES_EVENT_DEMO.json"
    _write_report(report_path, decision="oos_failed_or_insufficient_research_only", validation_qualified=2, oos_pass=False)

    report = build_report([str(report_path)])

    assert report["decision"] == "validation_mirage_no_oos_edge"
    assert report["summary"]["promotable"] == 0
    assert report["summary"]["validation_mirages"] == 1
    assert report["can_trade"] is False


def test_research_matrix_detects_promotable_observer_candidate(tmp_path: Path) -> None:
    report_path = tmp_path / "DERIVATIVES_EVENT_DEMO.json"
    _write_report(report_path, decision="oos_pass_observer_candidate_not_trade_permission", validation_qualified=1, oos_pass=True)

    report = build_report([str(report_path)])

    assert report["decision"] == "promotable_observer_candidate_found_not_trade_permission"
    assert report["summary"]["promotable"] == 1
    assert report["runtime_boundary"]["orders_allowed"] is False

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from tools.derivatives_event_signal_alert_drill import build_report


def test_derivatives_event_signal_alert_drill_passes(tmp_path: Path) -> None:
    report = build_report(Namespace(drill_dir=str(tmp_path / "drill")))
    assert report["decision"] == "derivatives_event_signal_alert_drill_passed"
    assert report["first_notify_decision"] == "dry_run_ready"
    assert report["second_notify_decision"] == "skipped_duplicate"
    assert report["can_trade"] is False

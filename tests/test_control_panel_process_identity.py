from __future__ import annotations

import json
from pathlib import Path

from ops.control_panel import control_panel


def write_receipt(root: Path, payload: dict[str, object]) -> None:
    receipt_dir = root / "logs" / "runtime_jobs"
    receipt_dir.mkdir(parents=True)
    (receipt_dir / "sample_component.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def runtime_evidence(root: Path) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    report = {"pid": 4242, "root": str(root)}
    lock = {"pid": 4242, "root": str(root)}
    receipt = {
        "schema_version": 1,
        "component": "sample_component",
        "root": str(root),
        "pid": 4242,
        "process_creation_utc": "2026-07-14T01:02:03.1234560Z",
        "live_trading_locked": True,
        "can_trade": False,
    }
    return report, lock, receipt


def test_runtime_report_health_binds_pid_to_receipt_creation(
    tmp_path: Path, monkeypatch
) -> None:
    report, lock, receipt = runtime_evidence(tmp_path)
    write_receipt(tmp_path, receipt)
    observed: dict[str, object] = {}

    def fake_process_alive(pid: object, *, expected_creation_utc: object | None = None) -> bool:
        observed.update(pid=pid, expected_creation_utc=expected_creation_utc)
        return True

    monkeypatch.setattr(control_panel, "ROOT", tmp_path)
    monkeypatch.setattr(control_panel, "process_alive", fake_process_alive)

    assert control_panel.runtime_report_process_alive("sample_component", report, lock) is True
    assert observed == {
        "pid": 4242,
        "expected_creation_utc": "2026-07-14T01:02:03.1234560Z",
    }


def test_runtime_report_health_rejects_cross_process_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    report, lock, receipt = runtime_evidence(tmp_path)
    receipt["pid"] = 9001
    write_receipt(tmp_path, receipt)

    monkeypatch.setattr(control_panel, "ROOT", tmp_path)
    monkeypatch.setattr(
        control_panel,
        "process_alive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not query")),
    )

    assert control_panel.runtime_report_process_alive("sample_component", report, lock) is False


def test_runtime_report_health_accepts_running_v2_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    report, lock, receipt = runtime_evidence(tmp_path)
    receipt.update(schema_version=2, session_id=1, launch_state="running")
    write_receipt(tmp_path, receipt)
    observed: dict[str, object] = {}

    def fake_process_alive(pid: object, *, expected_creation_utc: object | None = None) -> bool:
        observed.update(pid=pid, expected_creation_utc=expected_creation_utc)
        return True

    monkeypatch.setattr(control_panel, "ROOT", tmp_path)
    monkeypatch.setattr(control_panel, "process_alive", fake_process_alive)

    assert control_panel.runtime_report_process_alive("sample_component", report, lock) is True
    assert observed["pid"] == 4242


def test_runtime_report_health_rejects_suspended_v2_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    report, lock, receipt = runtime_evidence(tmp_path)
    receipt.update(schema_version=2, session_id=1, launch_state="suspended_assigned")
    write_receipt(tmp_path, receipt)

    monkeypatch.setattr(control_panel, "ROOT", tmp_path)
    monkeypatch.setattr(
        control_panel,
        "process_alive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not query")),
    )

    assert control_panel.runtime_report_process_alive("sample_component", report, lock) is False

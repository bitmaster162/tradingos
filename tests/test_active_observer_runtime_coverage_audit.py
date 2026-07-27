from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tools.active_observer_runtime_coverage_audit import FAMILY_OWNERS, build_report


NOW = datetime(2099, 1, 1, 12, tzinfo=timezone.utc)


def test_current_bybit_v5r2_family_has_real_edge_runtime_owner() -> None:
    assert FAMILY_OWNERS["bybit_liquidation_canonical_reversal_v5r2"] == "real_edge_pulse"
    assert "bybit_liquidation_canonical_reversal_v5r1" not in FAMILY_OWNERS


def write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def fixture(tmp_path: Path, *, extra_family: str | None = None) -> tuple[Path, Path, Path, Path, Path, Path]:
    families = []
    for family in FAMILY_OWNERS:
        report = write(
            tmp_path / f"{family}.json",
            {"generated_at": "2099-01-01T11:55:00Z", "decision": "collecting", "can_trade": False},
        )
        families.append(
            {
                "family": family,
                "status": "observer_only_waiting_forward",
                "path": str(report),
                "can_trade": False,
            }
        )
    if extra_family:
        report = write(tmp_path / f"{extra_family}.json", {"generated_at": "2099-01-01T11:55:00Z", "can_trade": False})
        families.append({"family": extra_family, "status": "observer_only_waiting_forward", "path": str(report), "can_trade": False})
    frontier = write(tmp_path / "frontier.json", {"families": families, "can_trade": False})
    real = write(
        tmp_path / "real.json",
        {
            "ts": "2099-01-01T11:55:00Z",
            "status": "ran_observer_pulse_cycle",
            "pid": 1,
            "sleep_seconds": 900,
            "live_trading_locked": True,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
        },
    )
    forward = write(
        tmp_path / "forward.json",
        {
            "ts": "2099-01-01T11:00:00Z",
            "status": "sleeping",
            "pid": 2,
            "sleep_seconds": 14_400,
            "live_trading_locked": True,
        },
    )
    force_order = write(
        tmp_path / "force_order.json",
        {
            "ts": "2099-01-01T11:55:00Z",
            "status": "running_watchdog_cycle",
            "exit_code": 0,
            "pid": 3,
            "sleep_seconds": 600,
            "live_trading_locked": True,
            "data_collector_only": True,
        },
    )
    force_order_collector = write(
        tmp_path / "force_order_collector.json",
        {
            "ts": "2099-01-01T11:55:00Z",
            "status": "running_collector_cycle",
            "exit_code": 0,
            "pid": 4,
            "cycle_seconds": 300,
            "live_trading_locked": True,
            "data_collector_only": True,
        },
    )
    deribit = write(
        tmp_path / "deribit.json",
        {
            "generated_at": "2099-01-01T11:55:00Z",
            "decision": "deribit_options_stack_forward_collecting_readiness",
            "runtime": {"all_components_passed": True},
            "can_trade": False,
        },
    )
    return frontier, real, forward, force_order, force_order_collector, deribit


def test_all_known_active_families_require_real_runtime_coverage(tmp_path: Path) -> None:
    paths = fixture(tmp_path)
    report = build_report(*paths, now=NOW, process_checker=lambda pid: pid in {1, 2, 3, 4})

    assert report["decision"] == "active_observer_runtime_coverage_pass"
    assert report["summary"]["active_observer_families"] == 8
    assert report["summary"]["covered_families"] == 8
    assert report["can_trade"] is False


def test_unknown_active_family_blocks_instead_of_inventing_runtime(tmp_path: Path) -> None:
    paths = fixture(tmp_path, extra_family="unknown_edge")
    report = build_report(*paths, now=NOW, process_checker=lambda _pid: True)

    assert report["decision"] == "active_observer_runtime_coverage_blocked"
    unknown = next(item for item in report["rows"] if item["family"] == "unknown_edge")
    assert unknown["failed_checks"] == ["known_runtime_owner"]


def test_dead_scheduler_blocks_its_families(tmp_path: Path) -> None:
    paths = fixture(tmp_path)
    report = build_report(*paths, now=NOW, process_checker=lambda _pid: False)

    assert report["decision"] == "active_observer_runtime_coverage_blocked"
    assert any("scheduler_pid_alive" in item["failed_checks"] for item in report["rows"])


def test_real_edge_scheduler_is_valid_while_current_pulse_cycle_is_running(tmp_path: Path) -> None:
    frontier, real, forward, force_order, force_order_collector, deribit = fixture(tmp_path)
    payload = json.loads(real.read_text(encoding="utf-8"))
    payload["status"] = "running_observer_pulse_cycle"
    real.write_text(json.dumps(payload), encoding="utf-8")

    report = build_report(
        frontier,
        real,
        forward,
        force_order,
        force_order_collector,
        deribit,
        now=NOW,
        process_checker=lambda pid: pid in {1, 2, 3, 4},
    )

    assert report["decision"] == "active_observer_runtime_coverage_pass"
    real_edge_rows = [item for item in report["rows"] if item["owner"] == "real_edge_pulse"]
    assert real_edge_rows
    assert all(item["checks"]["scheduler_status_allowed"] is True for item in real_edge_rows)


def test_force_order_runtime_requires_collector_only_boundary(tmp_path: Path) -> None:
    frontier, real, forward, force_order, force_order_collector, deribit = fixture(tmp_path)
    payload = json.loads(force_order.read_text(encoding="utf-8"))
    payload["data_collector_only"] = False
    force_order.write_text(json.dumps(payload), encoding="utf-8")

    report = build_report(
        frontier,
        real,
        forward,
        force_order,
        force_order_collector,
        deribit,
        now=NOW,
        process_checker=lambda pid: pid in {1, 2, 3, 4},
    )

    assert report["decision"] == "active_observer_runtime_coverage_blocked"
    force_order_row = next(item for item in report["rows"] if item["family"] == "force_order_liquidation_context")
    assert force_order_row["failed_checks"] == ["watchdog_data_collector_only"]


def test_force_order_runtime_requires_live_collector(tmp_path: Path) -> None:
    frontier, real, forward, force_order, force_order_collector, deribit = fixture(tmp_path)

    report = build_report(
        frontier,
        real,
        forward,
        force_order,
        force_order_collector,
        deribit,
        now=NOW,
        process_checker=lambda pid: pid in {1, 2, 3},
    )

    assert report["decision"] == "active_observer_runtime_coverage_blocked"
    force_order_row = next(item for item in report["rows"] if item["family"] == "force_order_liquidation_context")
    assert force_order_row["failed_checks"] == ["collector_pid_alive"]

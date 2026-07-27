from __future__ import annotations

import copy
import json
from pathlib import Path

from tools import bitunix_wo105_v3r4_forward_health as module


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def manifest(
    run: Path,
    *,
    network: int = 0,
    parser: int = 0,
    terminal_hold: bool = False,
    unrelated_hold: bool = False,
) -> None:
    held = bool(network or terminal_hold or unrelated_hold)
    write_json(
        run / "PUBLIC_CAPTURE_MANIFEST.json",
        {
            "subscription_acceptance": {"accepted": not network and not parser},
            "error_taxonomy": {"NETWORK": network, "PARSER": parser, "LOCAL": 0, "STORAGE": 0},
            "hold": held,
            "terminal_hold": terminal_hold,
            "hold_reasons": (
                ["terminal_contract_failure"]
                if terminal_hold
                else ["unexpected_quality_hold"]
                if unrelated_hold
                else ["recv_silence:20000ms>15000ms"]
                if network
                else []
            ),
            "can_trade": False,
            "credentials_used": 0,
            "private_calls": 0,
            "order_calls": 0,
        },
    )


def incomplete_capture(run: Path, *, empty_trades: bool = False, unexpected_file: bool = False) -> None:
    run.mkdir(parents=True, exist_ok=True)
    for name in module.INCOMPLETE_PUBLIC_CAPTURE_FILES:
        content = "" if empty_trades and name == "TRADES.jsonl" else "public-data\n"
        (run / name).write_text(content, encoding="utf-8")
    if unexpected_file:
        (run / "UNEXPECTED.bin").write_bytes(b"unexpected")


def safe_intake(capture_root: Path, runs: list[dict]) -> dict:
    return {
        "capture_root": str(capture_root),
        "runs": runs,
        "runtime_boundary": {
            "public_read_only": True,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
    }


def test_network_run_requires_later_clean_recovery(tmp_path: Path) -> None:
    bad = tmp_path / "run_bad"
    clean = tmp_path / "run_clean"
    manifest(bad, network=1)
    manifest(clean)

    unrecovered = module.ws_quality({"runs": [{"run_dir": str(bad), "accepted": False}]})
    assert unrecovered["network_only_excluded_runs"]
    assert unrecovered["latest_completed_run_accepted"] is False

    recovered = module.ws_quality(
        {"runs": [{"run_dir": str(bad), "accepted": False}, {"run_dir": str(clean), "accepted": True}]}
    )
    assert recovered["fatal_invalid_runs"] == []
    assert recovered["latest_completed_run_accepted"] is True


def test_terminal_or_unrelated_hold_is_never_downgraded(tmp_path: Path) -> None:
    clean = tmp_path / "run_clean"
    terminal = tmp_path / "run_terminal"
    unrelated = tmp_path / "run_unrelated"
    manifest(clean)
    manifest(terminal, network=1, terminal_hold=True)
    manifest(unrelated, network=1, unrelated_hold=True)

    for bad in (terminal, unrelated):
        report = module.ws_quality(
            {"runs": [{"run_dir": str(bad), "accepted": False}, {"run_dir": str(clean), "accepted": True}]}
        )
        assert report["fatal_invalid_runs"]
        assert report["network_only_excluded_runs"] == []


def test_incomplete_capture_requires_later_clean_recovery(tmp_path: Path) -> None:
    bad = tmp_path / "run_bad"
    clean = tmp_path / "run_clean"
    incomplete_capture(bad)
    manifest(clean)
    bad_row = {
        "run_dir": str(bad),
        "accepted": False,
        "failures": ["capture_metadata_invalid:FileNotFoundError"],
    }

    unrecovered = module.ws_quality(safe_intake(tmp_path, [bad_row]))
    assert unrecovered["fatal_invalid_runs"]
    assert unrecovered["abandoned_incomplete_runs"] == []

    recovered = module.ws_quality(
        safe_intake(tmp_path, [bad_row, {"run_dir": str(clean), "accepted": True, "failures": []}])
    )
    assert recovered["fatal_invalid_runs"] == []
    assert len(recovered["abandoned_incomplete_runs"]) == 1
    assert recovered["abandoned_incomplete_runs"][0]["later_clean_recovery"] is True


def test_incomplete_capture_with_unexpected_file_remains_fatal(tmp_path: Path) -> None:
    bad = tmp_path / "run_bad"
    clean = tmp_path / "run_clean"
    incomplete_capture(bad, unexpected_file=True)
    manifest(clean)

    report = module.ws_quality(
        safe_intake(
            tmp_path,
            [
                {
                    "run_dir": str(bad),
                    "accepted": False,
                    "failures": ["capture_metadata_invalid:FileNotFoundError"],
                },
                {"run_dir": str(clean), "accepted": True, "failures": []},
            ],
        )
    )

    assert report["fatal_invalid_runs"]
    assert report["abandoned_incomplete_runs"] == []


def test_incomplete_capture_with_empty_trades_is_excluded_after_clean_recovery(tmp_path: Path) -> None:
    bad = tmp_path / "run_bad"
    clean = tmp_path / "run_clean"
    incomplete_capture(bad, empty_trades=True)
    manifest(clean)
    bad_row = {
        "run_dir": str(bad),
        "accepted": False,
        "failures": ["capture_metadata_invalid:FileNotFoundError"],
    }

    report = module.ws_quality(
        safe_intake(tmp_path, [bad_row, {"run_dir": str(clean), "accepted": True, "failures": []}])
    )

    assert report["fatal_invalid_runs"] == []
    assert len(report["abandoned_incomplete_runs"]) == 1
    assert report["abandoned_incomplete_runs"][0]["later_clean_recovery"] is True


def test_parser_error_is_never_downgraded_to_network_warning(tmp_path: Path) -> None:
    bad = tmp_path / "run_parser"
    clean = tmp_path / "run_clean"
    manifest(bad, parser=1)
    manifest(clean)

    report = module.ws_quality(
        {"runs": [{"run_dir": str(bad), "accepted": False}, {"run_dir": str(clean), "accepted": True}]}
    )

    assert report["fatal_invalid_runs"]
    assert report["network_only_excluded_runs"] == []


def test_pre_floor_wait_does_not_require_forward_capture(tmp_path: Path, monkeypatch) -> None:
    lock = tmp_path / "lock.json"
    loop = tmp_path / "loop.json"
    status = tmp_path / "status.json"
    first_cycle = tmp_path / "first_cycle.json"
    write_json(lock, {"forward_start_at": "2099-01-01T00:00:00Z"})
    write_json(loop, {"status": "waiting_forward_floor"})
    write_json(status, {"phase": "WAITING_FORWARD_FLOOR"})
    write_json(
        first_cycle,
        {"decision": "bitunix_wo105_v3r4_first_cycle_waiting_forward_floor", "failures": []},
    )
    base_report = {
        "failures": [
            "first_cycle_operational_gate_not_accepted",
            "rest_acceptance_below_floor",
            "ws_capture_quality_invalid",
        ],
        "warnings": ["no_forward_setup_events_yet"],
        "ws_quality": {
            "candidate_runs": 0,
            "accepted_runs": 0,
            "invalid_runs": [],
            "network_only_excluded_runs": [],
            "abandoned_incomplete_runs": [],
            "fatal_invalid_runs": [],
            "latest_completed_run_accepted": False,
        },
        "rest_quality": {"candidate_runs": 0, "accepted_runs": 0},
        "forward_sample": {"events": 0},
        "can_trade": False,
    }
    monkeypatch.setattr(module.v3r3, "build_report", lambda **_: copy.deepcopy(base_report))

    report = module.build_report(
        lock_path=lock,
        loop_status_path=loop,
        status_path=status,
        first_cycle_path=first_cycle,
        packet_path=tmp_path / "packet.json",
        ws_intake_path=tmp_path / "ws.json",
        rest_root=tmp_path / "rest",
        ledger_path=tmp_path / "ledger.jsonl",
        loop_freshness_seconds=900,
        minimum_rest_acceptance_pct=95.0,
    )

    assert report["failures"] == []
    assert "forward_floor_not_reached" in report["warnings"]
    assert report["decision"] == "bitunix_wo105_v3r4_forward_health_pass_with_exclusions"
    assert report["can_trade"] is False


def test_v3r4_accepted_first_cycle_satisfies_legacy_health_alias(tmp_path: Path, monkeypatch) -> None:
    lock = tmp_path / "lock.json"
    loop = tmp_path / "loop.json"
    status = tmp_path / "status.json"
    first_cycle = tmp_path / "first_cycle.json"
    write_json(lock, {"forward_start_at": "2020-01-01T00:00:00Z"})
    write_json(loop, {"status": "public_ws_capture_running"})
    write_json(status, {"phase": "FORWARD_COLLECTION"})
    write_json(
        first_cycle,
        {"decision": "bitunix_wo105_v3r4_first_cycle_accepted_shadow_only", "failures": []},
    )
    base_report = {
        "failures": ["first_cycle_operational_gate_not_accepted"],
        "warnings": ["no_forward_setup_events_yet"],
        "ws_quality": {
            "candidate_runs": 1,
            "accepted_runs": 1,
            "invalid_runs": [],
            "network_only_excluded_runs": [],
            "abandoned_incomplete_runs": [],
            "fatal_invalid_runs": [],
            "latest_completed_run_accepted": True,
        },
        "rest_quality": {"candidate_runs": 1, "accepted_runs": 1},
        "forward_sample": {"events": 0},
        "can_trade": False,
    }
    monkeypatch.setattr(module.v3r3, "build_report", lambda **_: copy.deepcopy(base_report))

    report = module.build_report(
        lock_path=lock,
        loop_status_path=loop,
        status_path=status,
        first_cycle_path=first_cycle,
        packet_path=tmp_path / "packet.json",
        ws_intake_path=tmp_path / "ws.json",
        rest_root=tmp_path / "rest",
        ledger_path=tmp_path / "ledger.jsonl",
        loop_freshness_seconds=900,
        minimum_rest_acceptance_pct=95.0,
    )

    assert report["failures"] == []
    assert report["decision"] == "bitunix_wo105_v3r4_forward_health_pass_with_exclusions"
    assert report["can_trade"] is False


def test_recovered_incomplete_capture_becomes_warning_not_failure(tmp_path: Path, monkeypatch) -> None:
    lock = tmp_path / "lock.json"
    loop = tmp_path / "loop.json"
    status = tmp_path / "status.json"
    first_cycle = tmp_path / "first_cycle.json"
    write_json(lock, {"forward_start_at": "2020-01-01T00:00:00Z"})
    write_json(loop, {"status": "public_ws_capture_running"})
    write_json(status, {"phase": "FORWARD_COLLECTION"})
    write_json(
        first_cycle,
        {"decision": "bitunix_wo105_v3r4_first_cycle_accepted_shadow_only", "failures": []},
    )
    base_report = {
        "failures": ["ws_capture_quality_invalid"],
        "warnings": ["no_forward_setup_events_yet"],
        "ws_quality": {
            "candidate_runs": 2,
            "accepted_runs": 1,
            "invalid_runs": [{"run_dir": "abandoned"}],
            "network_only_excluded_runs": [],
            "abandoned_incomplete_runs": [{"run_dir": "abandoned", "later_clean_recovery": True}],
            "fatal_invalid_runs": [],
            "latest_completed_run_accepted": True,
        },
        "rest_quality": {"candidate_runs": 1, "accepted_runs": 1},
        "forward_sample": {"events": 0},
        "can_trade": False,
    }
    monkeypatch.setattr(module.v3r3, "build_report", lambda **_: copy.deepcopy(base_report))

    report = module.build_report(
        lock_path=lock,
        loop_status_path=loop,
        status_path=status,
        first_cycle_path=first_cycle,
        packet_path=tmp_path / "packet.json",
        ws_intake_path=tmp_path / "ws.json",
        rest_root=tmp_path / "rest",
        ledger_path=tmp_path / "ledger.jsonl",
        loop_freshness_seconds=900,
        minimum_rest_acceptance_pct=95.0,
    )

    assert report["failures"] == []
    assert "abandoned_incomplete_capture_excluded_after_clean_recovery" in report["warnings"]
    assert report["decision"] == "bitunix_wo105_v3r4_forward_health_pass_with_exclusions"
    assert report["can_trade"] is False


def test_bounded_causal_packet_hold_becomes_warning_not_failure(tmp_path: Path, monkeypatch) -> None:
    lock = tmp_path / "lock.json"
    loop = tmp_path / "loop.json"
    status = tmp_path / "status.json"
    first_cycle = tmp_path / "first_cycle.json"
    write_json(lock, {"forward_start_at": "2020-01-01T00:00:00Z"})
    write_json(loop, {"status": "public_ws_capture_running"})
    write_json(status, {"phase": "FORWARD_COLLECTION"})
    write_json(
        first_cycle,
        {"decision": "bitunix_wo105_v3r4_first_cycle_accepted_shadow_only", "failures": []},
    )
    base_report = {
        "failures": ["packet_assembly_blockers_nonempty"],
        "warnings": ["no_forward_setup_events_yet"],
        "ws_quality": {
            "candidate_runs": 1,
            "accepted_runs": 1,
            "invalid_runs": [],
            "network_only_excluded_runs": [],
            "abandoned_incomplete_runs": [],
            "fatal_invalid_runs": [],
            "latest_completed_run_accepted": True,
        },
        "packet": {
            "decision": "bitunix_wo105_v3_packet_hold_unit_or_causal_availability_invalid",
            "blockers": [
                "latest_htf_bar_not_available_by_entry_cutoff",
                "signal_bar_not_available_by_entry_cutoff",
            ],
            "packet_written": False,
            "evaluation_run": False,
            "source_read_failures": [],
        },
        "rest_quality": {"candidate_runs": 1, "accepted_runs": 1},
        "forward_sample": {"events": 0},
        "can_trade": False,
    }
    monkeypatch.setattr(module.v3r3, "build_report", lambda **_: copy.deepcopy(base_report))

    report = module.build_report(
        lock_path=lock,
        loop_status_path=loop,
        status_path=status,
        first_cycle_path=first_cycle,
        packet_path=tmp_path / "packet.json",
        ws_intake_path=tmp_path / "ws.json",
        rest_root=tmp_path / "rest",
        ledger_path=tmp_path / "ledger.jsonl",
        loop_freshness_seconds=900,
        minimum_rest_acceptance_pct=95.0,
    )

    assert report["failures"] == []
    assert "causal_pre_entry_packet_hold_excluded_before_event_admission" in report["warnings"]
    assert report["packet"]["bounded_causal_hold_excluded"] is True
    assert report["decision"] == "bitunix_wo105_v3r4_forward_health_pass_with_exclusions"
    assert report["can_trade"] is False


def test_unknown_packet_blocker_remains_fatal(tmp_path: Path, monkeypatch) -> None:
    lock = tmp_path / "lock.json"
    loop = tmp_path / "loop.json"
    status = tmp_path / "status.json"
    first_cycle = tmp_path / "first_cycle.json"
    write_json(lock, {"forward_start_at": "2020-01-01T00:00:00Z"})
    write_json(loop, {"status": "public_ws_capture_running"})
    write_json(status, {"phase": "FORWARD_COLLECTION"})
    write_json(
        first_cycle,
        {"decision": "bitunix_wo105_v3r4_first_cycle_accepted_shadow_only", "failures": []},
    )
    base_report = {
        "failures": ["packet_assembly_blockers_nonempty"],
        "warnings": [],
        "ws_quality": {
            "candidate_runs": 1,
            "accepted_runs": 1,
            "invalid_runs": [],
            "network_only_excluded_runs": [],
            "abandoned_incomplete_runs": [],
            "fatal_invalid_runs": [],
            "latest_completed_run_accepted": True,
        },
        "packet": {
            "decision": "bitunix_wo105_v3_packet_hold_unit_or_causal_availability_invalid",
            "blockers": ["outcome_bar_not_available"],
            "packet_written": False,
            "evaluation_run": False,
            "source_read_failures": [],
        },
        "rest_quality": {"candidate_runs": 1, "accepted_runs": 1},
        "forward_sample": {"events": 0},
        "can_trade": False,
    }
    monkeypatch.setattr(module.v3r3, "build_report", lambda **_: copy.deepcopy(base_report))

    report = module.build_report(
        lock_path=lock,
        loop_status_path=loop,
        status_path=status,
        first_cycle_path=first_cycle,
        packet_path=tmp_path / "packet.json",
        ws_intake_path=tmp_path / "ws.json",
        rest_root=tmp_path / "rest",
        ledger_path=tmp_path / "ledger.jsonl",
        loop_freshness_seconds=900,
        minimum_rest_acceptance_pct=95.0,
    )

    assert report["failures"] == ["packet_assembly_blockers_nonempty"]
    assert report["packet"]["bounded_causal_hold_excluded"] is False
    assert report["decision"] == "bitunix_wo105_v3r4_forward_health_blocked"
    assert report["can_trade"] is False

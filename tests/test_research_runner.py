from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.research_data_snapshot import create_snapshot
from tools.research_runner import (
    build_command,
    load_contract,
    report_contract,
    resolve_snapshot,
)


def test_production_contract_is_locked_and_safe() -> None:
    contract = load_contract(Path("configs/RESEARCH_RUNNER_CONTRACT.json").resolve())
    assert sorted(contract["experiments"]) == [
        "basis_funding_carry",
        "basis_shock_reversion",
        "funding_settlement_reversion",
        "session_opening_range",
        "spot_led_continuation",
    ]
    assert contract["execution_contract"]["arbitrary_extra_args"] is False
    assert contract["execution_contract"]["orders_allowed"] is False
    assert contract["execution_contract"]["hypothesis_authorization_required"] is True
    assert contract["experiments"]["basis_shock_reversion"]["hypothesis_id"] == "HYP-BASIS-SHOCK-001"
    assert contract["experiments"]["funding_settlement_reversion"]["hypothesis_id"] == "HYP-FUNDING-EVENT-001"
    assert contract["experiments"]["spot_led_continuation"]["hypothesis_id"] == "HYP-SPOT-LEAD-001"


def test_build_command_uses_token_list_and_snapshot_cache(tmp_path: Path) -> None:
    script = tmp_path / "experiment.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    snapshot = tmp_path / "snapshot"
    report = tmp_path / "run" / "REPORT"
    lock = tmp_path / "run" / "LOCK.json"
    command = build_command(
        {"script_path": script, "supports_lock_path": True}, snapshot, report, lock
    )
    assert isinstance(command, list)
    assert command[command.index("--cache-dir") + 1] == str(snapshot)
    assert command[command.index("--out-prefix") + 1] == str(report)
    assert command[command.index("--lock-path") + 1] == str(lock)


def test_report_contract_rejects_trade_permission() -> None:
    assert report_contract({"decision": "x", "can_trade": False}, None)["pass"] is True
    rejected = report_contract({"decision": "x", "can_trade": True}, None)
    assert rejected["pass"] is False
    assert rejected["checks"]["can_trade_false"] is False


def test_exact_snapshot_id_is_required(tmp_path: Path) -> None:
    contract = load_contract(Path("configs/RESEARCH_RUNNER_CONTRACT.json").resolve())
    with pytest.raises(ValueError, match="exact_snapshot_id_required"):
        resolve_snapshot(tmp_path, contract, "latest")


def test_resolve_snapshot_verifies_hashes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    active = tmp_path / "Active"
    source = active / "data/cache/source/spot/BTCUSDT/1h_klines.csv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("time,value\n2024-01-01T00:00:00+00:00,1\n", encoding="utf-8")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({
        "authority": "test",
        "source_cache_relative": "data/cache/source",
        "snapshot_root_relative": "data/research_snapshots",
        "reject_google_drive_source": True,
        "profile": "TEST",
        "required_files": ["spot/BTCUSDT/1h_klines.csv"],
    }), encoding="utf-8")
    payload = create_snapshot(active, policy)
    contract = {
        "snapshot_policy": str(policy),
    }
    monkeypatch.setattr("tools.research_runner.ROOT", tmp_path)
    snapshot_id = payload["manifest"]["snapshot_id"]
    snapshot_dir, manifest, verification = resolve_snapshot(active, contract, snapshot_id)
    assert snapshot_dir.name == snapshot_id
    assert manifest["snapshot_id"] == snapshot_id
    assert verification["passed"] is True

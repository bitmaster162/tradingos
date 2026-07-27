from __future__ import annotations

import json
from pathlib import Path

from tools.cross_venue_microstructure_runner_contract import audit_contract


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_runner_contract_skeleton_matches_prereg_queue() -> None:
    contract = load("configs/CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT.json")
    queue = load("configs/CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE.json")
    report = audit_contract(contract, queue, {"decision": "waiting_for_microstructure_readiness", "snapshot_id": None})

    assert report["decision"] == "microstructure_runner_contract_valid_locked"
    assert report["execution_state"] == "blocked_waiting_for_first_sealed_snapshot"
    assert report["summary"]["experiments"] == 4
    assert report["summary"]["planned_not_implemented"] == 0
    assert report["summary"]["implemented_locked"] == 4
    assert report["summary"]["scripts_existing"] == 4
    assert report["can_trade"] is False


def test_runner_contract_rejects_unregistered_experiment() -> None:
    contract = load("configs/CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT.json")
    queue = load("configs/CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE.json")
    contract["experiments"]["unregistered"] = {
        "hypothesis_id": "HYP-UNKNOWN",
        "family": "UNKNOWN",
        "script": "tools/unknown.py",
        "implementation_status": "planned_not_implemented",
        "supports_lock_path": True,
    }

    report = audit_contract(contract, queue, {"decision": "waiting_for_microstructure_readiness"})

    assert report["decision"] == "microstructure_runner_contract_invalid"
    assert report["checks"]["all_queue_experiments_covered"] is False


def test_runner_contract_rejects_unsafe_execution_flag() -> None:
    contract = load("configs/CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT.json")
    queue = load("configs/CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE.json")
    contract["execution_contract"]["orders_allowed"] = True

    report = audit_contract(contract, queue, {"decision": "waiting_for_microstructure_readiness"})

    assert report["decision"] == "microstructure_runner_contract_invalid"
    assert report["checks"]["unsafe_execution_flags_false"] is False


def test_runner_contract_still_blocks_after_seal_until_implementation() -> None:
    contract = load("configs/CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT.json")
    queue = load("configs/CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE.json")
    report = audit_contract(contract, queue, {"decision": "microstructure_snapshot_sealed", "snapshot_id": "sealed-id"})

    assert report["decision"] == "microstructure_runner_contract_valid_locked"
    assert report["execution_state"] == "sealed_snapshot_available_contract_ready_for_explicit_runner_wiring"

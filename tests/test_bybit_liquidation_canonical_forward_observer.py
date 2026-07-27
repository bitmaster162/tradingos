from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import bybit_liquidation_canonical_forward_observer as module


ROOT = Path(__file__).resolve().parents[1]
REAL_PREREG = ROOT / "configs" / "BYBIT_LIQUIDATION_CANONICAL_FORWARD_PREREG_V2_2026-07-13.json"


def prereg(tmp_path: Path, *, floor: str = "2099-01-02T00:00:00Z") -> dict:
    payload = json.loads(REAL_PREREG.read_text(encoding="utf-8"))
    discovery = tmp_path / "discovery.json"
    discovery.write_text(json.dumps({"decision": "outcome_reviewed_discovery"}), encoding="utf-8")
    payload["forward_floor_at"] = floor
    payload["discovery_provenance"]["report"] = str(discovery)
    payload["sources"] = {
        "liquidations": str(tmp_path / "liquidations"),
        "bars_root": str(tmp_path / "bars"),
    }
    return payload


def write_prereg(tmp_path: Path, *, floor: str = "2099-01-02T00:00:00Z") -> Path:
    path = tmp_path / "prereg.json"
    path.write_text(json.dumps(prereg(tmp_path, floor=floor)), encoding="utf-8")
    return path


def test_lock_binds_prereg_observer_dependencies_and_discovery(tmp_path: Path) -> None:
    prereg_path = write_prereg(tmp_path)
    lock = module.build_lock(prereg_path, created_at="2099-01-01T00:00:00Z")

    assert len(lock["dependencies"]) == len(module.DEPENDENCY_PATHS)
    assert lock["research_boundary"]["old_v1_outcomes_admitted"] is False
    assert lock["side_contract"]["raw_side_mapping"] == {"BUY": "LONG", "SELL": "SHORT"}
    assert module.validate_lock(lock) == []

    prereg_path.write_text("{}", encoding="utf-8")
    assert "preregistration_integrity" in module.validate_lock(lock)


def test_lock_cannot_be_sealed_at_or_after_floor(tmp_path: Path) -> None:
    prereg_path = write_prereg(tmp_path)
    with pytest.raises(ValueError, match="before forward_floor_at"):
        module.build_lock(prereg_path, created_at="2099-01-02T00:00:00Z")


def test_before_floor_hides_outcomes_and_cannot_trade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prereg_path = write_prereg(tmp_path)
    lock = module.build_lock(prereg_path, created_at="2099-01-01T00:00:00Z")
    lock_path = tmp_path / "lock.json"
    module.write_json(lock_path, lock)
    monkeypatch.setattr(module, "load_forward_records", lambda _lock: ([], {"resolved_records": 0}))

    report = module.run_observer(lock_path, tmp_path / "report", tmp_path / "terminal.json")

    assert report["decision"] == "bybit_liquidation_canonical_forward_waiting_floor"
    assert report["outcome_review"]["interim_outcomes_hidden"] is True
    assert report["outcome_review"]["terminal_metrics"] is None
    assert report["terminal"]["reached"] is False
    assert report["can_trade"] is False
    assert report["orders_allowed"] is False


def test_sample_blockers_enforce_every_preregistered_gate() -> None:
    sample = {
        "resolved_events": 99,
        "utc_days": 4,
        "symbol_count": 4,
        "independent_4h_blocks": 19,
        "max_single_symbol_share": 0.51,
    }
    gate = {
        "minimum_resolved_events": 100,
        "minimum_utc_days": 5,
        "minimum_symbols": 5,
        "minimum_independent_4h_blocks": 20,
        "maximum_single_symbol_share": 0.5,
    }

    assert set(module.sample_blockers(sample, gate)) == {
        "minimum_resolved_events_not_met",
        "minimum_utc_days_not_met",
        "minimum_symbols_not_met",
        "minimum_independent_4h_blocks_not_met",
        "maximum_single_symbol_share_exceeded",
    }


def test_terminal_evaluation_has_fixed_pass_and_tombstone_paths() -> None:
    candidate = {
        "direction": "reversal",
        "base_cost_bps": 7.0,
        "stress_cost_bps": 12.0,
    }
    gate = {
        "minimum_mean_net_bps": 15.0,
        "minimum_winrate_net_positive_pct": 55.0,
        "minimum_stress_mean_net_bps": 0.0,
        "minimum_positive_symbols": 4,
    }
    passing = [
        {"symbol": f"S{index % 5}", "reversal_return_bps": 30.0}
        for index in range(100)
    ]
    failing = [
        {"symbol": f"S{index % 5}", "reversal_return_bps": 0.0}
        for index in range(100)
    ]

    pass_decision, pass_metrics, pass_failures = module.terminal_evaluation(passing, candidate, gate)
    fail_decision, fail_metrics, fail_failures = module.terminal_evaluation(failing, candidate, gate)

    assert pass_decision.endswith("accepted_manual_shadow_only")
    assert pass_metrics["mean_net_bps"] == 23.0
    assert pass_failures == []
    assert fail_decision.endswith("no_edge_tombstone")
    assert fail_metrics["mean_net_bps"] == -7.0
    assert set(fail_failures) == set(fail_metrics["checks"])

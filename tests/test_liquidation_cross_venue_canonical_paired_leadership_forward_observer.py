from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import liquidation_cross_venue_canonical_paired_leadership_forward_observer as module
from tools import liquidation_cross_venue_side_semantics_audit as audit


def event(venue: str, seconds: float, side: str) -> dict:
    ns = int(seconds * 1_000_000_000)
    return {
        "venue": venue,
        "symbol": "BTCUSDT",
        "side": side,
        "received_at_ns": ns,
        "received_at": module.base.iso_from_ns(ns),
        "notional_usd": 1.0,
    }


def prereg(tmp_path: Path, floor: str = "2099-01-02T00:00:00Z") -> dict:
    return {
        "schema_version": 3,
        "prereg_id": "canonical_paired_test",
        "status": "prospective_preregistration_before_forward_floor",
        "forward_floor_at": floor,
        "can_trade": False,
        "orders_allowed": False,
        "sources": {"binance": str(tmp_path / "binance"), "bybit": str(tmp_path / "bybit")},
        "source_semantics": {
            venue: {"mapping_to_liquidated_position_side": mapping}
            for venue, mapping in module.CANONICAL_SIDE_MAP.items()
        },
        "shared_symbols": ["BTCUSDT"],
        "fixed_rules": {
            "required_collector_host": "TEST",
            "required_ingest_schema_version": 2,
            "match_dimensions": ["symbol", "liquidated_position_side"],
            "pair_windows_seconds": [1, 5, 15],
            "primary_window_seconds": 5,
        },
        "terminal_gate": {
            "minimum_primary_window_pairs": 2,
            "minimum_utc_days": 1,
            "minimum_symbols": 1,
            "maximum_single_symbol_share": 1.0,
            "primary_window_seconds": 5,
            "minimum_candidate_leader_share": 0.65,
        },
        "research_boundary": {"v2_observations_admitted": False},
        "runtime_boundary": {
            "observer_only": True,
            "paper_entries_allowed": False,
            "live_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
    }


def test_canonical_mapping_compares_liquidated_position_side() -> None:
    binance = module.canonicalize_events("binance", [event("binance", 10, "SELL")])
    bybit = module.canonicalize_events("bybit", [event("bybit", 12, "BUY")])
    assert binance[0]["raw_source_side"] == "SELL"
    assert binance[0]["liquidated_position_side"] == "LONG"
    assert bybit[0]["raw_source_side"] == "BUY"
    assert bybit[0]["liquidated_position_side"] == "LONG"
    pairs = module.paired.build_pairs(binance, bybit, cutoff_ns=int(100e9), maximum_window_ns=int(15e9))
    assert len(pairs) == 1
    assert pairs[0]["side"] == "LONG"


def test_v2_raw_side_and_canonical_contract_produce_different_pairing() -> None:
    binance = [event("binance", 10, "SELL")]
    bybit = [event("bybit", 12, "BUY")]
    result = audit.compare_pairing(binance, bybit, cutoff_ns=int(100e9), windows_seconds=[1, 5, 15])
    assert result["raw_same_side"]["primary_sample"]["matched_pairs"] == 0
    assert result["canonical_liquidated_position_side"]["primary_sample"]["matched_pairs"] == 1


def test_lock_binds_v3_prereg_observer_and_dependencies(tmp_path: Path) -> None:
    prereg_path = tmp_path / "prereg.json"
    prereg_path.write_text(json.dumps(prereg(tmp_path)), encoding="utf-8")
    lock = module.build_lock(prereg_path, created_at="2099-01-01T00:00:00Z")
    assert len(lock["dependencies"]) == 2
    assert lock["research_boundary"]["v2_observations_admitted"] is False
    assert module.validate_lock(lock) == []


def test_lock_cannot_be_sealed_at_or_after_floor(tmp_path: Path) -> None:
    prereg_path = tmp_path / "prereg.json"
    prereg_path.write_text(json.dumps(prereg(tmp_path)), encoding="utf-8")
    with pytest.raises(ValueError, match="before forward_floor_at"):
        module.build_lock(prereg_path, created_at="2099-01-02T00:00:00Z")


def test_run_before_floor_excludes_all_events_and_cannot_trade(tmp_path: Path) -> None:
    prereg_path = tmp_path / "prereg.json"
    prereg_path.write_text(json.dumps(prereg(tmp_path)), encoding="utf-8")
    lock = module.build_lock(prereg_path, created_at="2099-01-01T00:00:00Z")
    lock_path = tmp_path / "lock.json"
    module.base.write_json(lock_path, lock)
    report = module.run_observer(lock_path, tmp_path / "report", tmp_path / "terminal.json")
    assert report["decision"].endswith("waiting_forward_floor")
    assert report["primary_sample"]["matched_pairs"] == 0
    assert report["side_contract"]["v2_observations_admitted"] is False
    assert report["can_trade"] is False
    assert report["runtime_boundary"]["orders_allowed"] is False

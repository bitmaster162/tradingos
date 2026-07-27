from __future__ import annotations

import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "liquidation_cross_venue_paired_leadership_forward_observer.py"
SPEC = importlib.util.spec_from_file_location("liquidation_cross_venue_paired_leadership_forward_observer", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def prereg(tmp_path: Path) -> dict:
    return {
        "schema_version": 2,
        "prereg_id": "paired_test",
        "status": "prospective_preregistration_before_forward_floor",
        "created_at": "2026-01-01T00:00:00Z",
        "forward_floor_at": "2026-01-02T00:00:00Z",
        "can_trade": False,
        "orders_allowed": False,
        "sources": {"binance": str(tmp_path / "binance"), "bybit": str(tmp_path / "bybit")},
        "shared_symbols": ["BTCUSDT"],
        "fixed_rules": {
            "required_collector_host": "TEST",
            "required_ingest_schema_version": 2,
            "pair_windows_seconds": [1, 5, 15],
            "primary_window_seconds": 5,
        },
        "terminal_gate": gate(minimum_pairs=2, minimum_days=1, minimum_symbols=1, maximum_share=1.0),
        "runtime_boundary": {
            "observer_only": True,
            "paper_entries_allowed": False,
            "live_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
    }


def gate(
    minimum_pairs: int = 100,
    minimum_days: int = 5,
    minimum_symbols: int = 5,
    maximum_share: float = 0.5,
) -> dict:
    return {
        "minimum_primary_window_pairs": minimum_pairs,
        "minimum_utc_days": minimum_days,
        "minimum_symbols": minimum_symbols,
        "maximum_single_symbol_share": maximum_share,
        "primary_window_seconds": 5,
        "minimum_candidate_leader_share": 0.65,
    }


def event(venue: str, seconds: float, symbol: str = "BTCUSDT", side: str = "SELL") -> dict:
    ns = int(seconds * 1_000_000_000)
    return {
        "venue": venue,
        "symbol": symbol,
        "side": side,
        "received_at_ns": ns,
        "received_at": MODULE.base.iso_from_ns(ns),
        "notional_usd": 1.0,
    }


def make_pairs(total: int, binance_leaders: int) -> list[dict]:
    rows = []
    for index in range(total):
        rows.append(
            {
                "symbol": f"S{index % 5}",
                "side": "SELL",
                "leader_venue": "binance" if index < binance_leaders else "bybit",
                "first_received_at_ns": index * 1_000_000_000,
                "first_received_at": f"2026-01-0{1 + index % 5}T00:00:00.000Z",
                "absolute_delay_ms": 1000.0,
            }
        )
    return rows


def test_lock_binds_observer_prereg_and_dependency(tmp_path: Path) -> None:
    prereg_path = tmp_path / "prereg.json"
    prereg_path.write_text(json.dumps(prereg(tmp_path)), encoding="utf-8")
    lock = MODULE.build_lock(prereg_path, created_at="2026-01-01T12:00:00Z")
    assert lock["can_trade"] is False
    assert len(lock["dependencies"]) == 1
    assert MODULE.validate_lock(lock) == []

    prereg_path.write_text("{}", encoding="utf-8")
    assert "preregistration_integrity" in MODULE.validate_lock(lock)


def test_pairing_uses_nearest_one_to_one_match_not_first_available() -> None:
    binance = [event("binance", 0), event("binance", 13)]
    bybit = [event("bybit", 14)]
    rows = MODULE.build_pairs(binance, bybit, cutoff_ns=int(100e9), maximum_window_ns=int(15e9))
    assert len(rows) == 1
    assert rows[0]["binance_received_at_ns"] == int(13e9)
    assert rows[0]["absolute_delay_ms"] == 1000.0


def test_unmatched_events_are_excluded_instead_of_scored_as_failure() -> None:
    binance = [event("binance", 0), event("binance", 100)]
    bybit = [event("bybit", 2)]
    rows = MODULE.build_pairs(binance, bybit, cutoff_ns=int(200e9), maximum_window_ns=int(15e9))
    windows = MODULE.summarize_pairs(rows, [1, 5, 15])
    assert len(rows) == 1
    assert windows["1"]["matched_pairs"] == 0
    assert windows["5"]["matched_pairs"] == 1


def test_pairing_requires_same_symbol_and_side_and_nonzero_delay() -> None:
    binance = [event("binance", 10, side="SELL"), event("binance", 20, side="BUY")]
    bybit = [event("bybit", 10, side="SELL"), event("bybit", 21, symbol="ETHUSDT", side="SELL")]
    rows = MODULE.build_pairs(binance, bybit, cutoff_ns=int(100e9), maximum_window_ns=int(15e9))
    assert rows == []


def test_terminal_collects_until_sample_gate() -> None:
    pairs = make_pairs(99, 90)
    windows = MODULE.summarize_pairs(pairs, [1, 5, 15])
    sample = MODULE.primary_sample(pairs, 5)
    decision, blockers, evidence = MODULE.evaluate_terminal(sample, windows, gate())
    assert decision.endswith("collecting_forward_sample")
    assert "minimum_primary_window_pairs_not_met" in blockers
    assert evidence == {}


def test_terminal_accepts_strong_wilson_separated_leader() -> None:
    pairs = make_pairs(300, 240)
    windows = MODULE.summarize_pairs(pairs, [1, 5, 15])
    sample = MODULE.primary_sample(pairs, 5)
    decision, blockers, evidence = MODULE.evaluate_terminal(sample, windows, gate())
    assert decision.endswith("candidate_for_manual_price_impact_preregistration")
    assert blockers == []
    assert evidence["candidate_venue"] == "binance"
    assert evidence["wilson_95"]["lower"] > 0.5


def test_terminal_tombstones_ambiguous_leadership() -> None:
    pairs = make_pairs(300, 165)
    windows = MODULE.summarize_pairs(pairs, [1, 5, 15])
    sample = MODULE.primary_sample(pairs, 5)
    decision, blockers, evidence = MODULE.evaluate_terminal(sample, windows, gate())
    assert decision.endswith("no_stable_leader_tombstone")
    assert blockers == []
    assert evidence["leader_share"] == 0.55


def test_trade_capable_lock_is_rejected(tmp_path: Path) -> None:
    prereg_path = tmp_path / "prereg.json"
    prereg_path.write_text(json.dumps(prereg(tmp_path)), encoding="utf-8")
    lock = MODULE.build_lock(prereg_path, created_at="2026-01-01T12:00:00Z")
    lock["orders_allowed"] = True
    assert "top_level_runtime_boundary" in MODULE.validate_lock(lock)

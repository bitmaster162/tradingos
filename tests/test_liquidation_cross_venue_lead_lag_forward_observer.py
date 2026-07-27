from __future__ import annotations

import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "liquidation_cross_venue_lead_lag_forward_observer.py"
SPEC = importlib.util.spec_from_file_location("liquidation_cross_venue_lead_lag_forward_observer", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def prereg_payload(tmp_path: Path) -> dict:
    return {
        "schema_version": 1,
        "prereg_id": "test_lock",
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
            "preceding_exclusion_seconds": 15,
            "leader_cooldown_seconds": 15,
            "follow_windows_seconds": [1, 5, 15],
            "primary_window_seconds": 5,
        },
        "terminal_gate": {
            "minimum_clean_leaders_per_direction": 2,
            "minimum_utc_days_per_direction": 1,
            "minimum_symbols_per_direction": 1,
            "maximum_single_symbol_share": 1.0,
            "primary_window_seconds": 5,
            "minimum_absolute_follow_rate_gap": 0.15,
        },
        "runtime_boundary": {
            "observer_only": True,
            "paper_entries_allowed": False,
            "live_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
    }


def event(venue: str, seconds: float, symbol: str = "BTCUSDT", side: str = "SELL") -> dict:
    ns = int(seconds * 1_000_000_000)
    return {
        "venue": venue,
        "symbol": symbol,
        "side": side,
        "received_at_ns": ns,
        "received_at": MODULE.iso_from_ns(ns),
        "notional_usd": 100.0,
    }


def summary(total: int, successes: int, symbols: int = 5, days: int = 5) -> dict:
    lower, upper = MODULE.wilson_interval(successes, total)
    return {
        "clean_leader_events": total,
        "utc_days": days,
        "symbol_count": symbols,
        "max_single_symbol_share": 0.25,
        "windows_seconds": {
            "5": {
                "follow_count": successes,
                "follow_rate": successes / total,
                "wilson_95": {"lower": lower, "upper": upper},
            }
        },
    }


def terminal_gate() -> dict:
    return {
        "minimum_clean_leaders_per_direction": 100,
        "minimum_utc_days_per_direction": 5,
        "minimum_symbols_per_direction": 5,
        "maximum_single_symbol_share": 0.5,
        "primary_window_seconds": 5,
        "minimum_absolute_follow_rate_gap": 0.15,
    }


def test_build_lock_is_prospective_and_integrity_bound(tmp_path: Path) -> None:
    prereg_path = tmp_path / "prereg.json"
    prereg_path.write_text(json.dumps(prereg_payload(tmp_path)), encoding="utf-8")
    lock = MODULE.build_lock(prereg_path, created_at="2026-01-01T12:00:00Z")
    assert lock["status"] == "prospective_forward_lock_before_outcome_review"
    assert lock["can_trade"] is False
    assert MODULE.validate_lock(lock) == []

    prereg_path.write_text("{}", encoding="utf-8")
    assert "preregistration_integrity" in MODULE.validate_lock(lock)


def test_build_lock_refuses_post_floor_seal(tmp_path: Path) -> None:
    prereg_path = tmp_path / "prereg.json"
    prereg_path.write_text(json.dumps(prereg_payload(tmp_path)), encoding="utf-8")
    try:
        MODULE.build_lock(prereg_path, created_at="2026-01-02T00:00:00Z")
    except ValueError as exc:
        assert "before forward_floor_at" in str(exc)
    else:
        raise AssertionError("post-floor lock unexpectedly accepted")


def test_pairing_requires_clean_leader_and_uses_first_later_event_once() -> None:
    leaders = [event("binance", 100), event("binance", 120), event("binance", 140)]
    followers = [event("bybit", 95), event("bybit", 122), event("bybit", 143)]
    rows = MODULE.build_direction_observations(
        leaders,
        followers,
        cutoff_ns=int(200e9),
        preceding_exclusion_ns=int(15e9),
        leader_cooldown_ns=int(15e9),
        windows_ns=[int(1e9), int(5e9), int(15e9)],
    )
    assert [row["leader_received_at_ns"] for row in rows] == [int(120e9), int(140e9)]
    assert rows[0]["delay_ms"] == 2000.0
    assert rows[0]["followed"] == {"1": False, "5": True, "15": True}
    assert rows[1]["delay_ms"] == 3000.0


def test_pairing_respects_cooldown_and_resolution_cutoff() -> None:
    leaders = [event("binance", 100), event("binance", 105), event("binance", 121)]
    rows = MODULE.build_direction_observations(
        leaders,
        [],
        cutoff_ns=int(120e9),
        preceding_exclusion_ns=int(15e9),
        leader_cooldown_ns=int(15e9),
        windows_ns=[int(1e9), int(5e9), int(15e9)],
    )
    assert len(rows) == 1
    assert rows[0]["leader_received_at_ns"] == int(100e9)


def test_terminal_gate_collects_until_both_directions_are_ready() -> None:
    decision, blockers, evidence = MODULE.evaluate_terminal(
        {"binance_leads_bybit": summary(99, 90), "bybit_leads_binance": summary(100, 20)},
        terminal_gate(),
    )
    assert decision.endswith("collecting_forward_sample")
    assert "binance_leads_bybit_minimum_clean_leaders_not_met" in blockers
    assert evidence == {}


def test_terminal_gate_accepts_only_separated_large_gap() -> None:
    decision, blockers, evidence = MODULE.evaluate_terminal(
        {"binance_leads_bybit": summary(200, 180), "bybit_leads_binance": summary(200, 80)},
        terminal_gate(),
    )
    assert decision.endswith("candidate_for_manual_price_impact_preregistration")
    assert blockers == []
    assert evidence["candidate_direction"] == "binance_leads_bybit"
    assert evidence["wilson_intervals_separate"] is True


def test_terminal_gate_tombstones_ambiguous_result() -> None:
    decision, blockers, evidence = MODULE.evaluate_terminal(
        {"binance_leads_bybit": summary(200, 110), "bybit_leads_binance": summary(200, 100)},
        terminal_gate(),
    )
    assert decision.endswith("no_stable_leader_tombstone")
    assert blockers == []
    assert evidence["wilson_intervals_separate"] is False


def test_trade_capable_lock_is_rejected(tmp_path: Path) -> None:
    prereg_path = tmp_path / "prereg.json"
    prereg_path.write_text(json.dumps(prereg_payload(tmp_path)), encoding="utf-8")
    lock = MODULE.build_lock(prereg_path, created_at="2026-01-01T12:00:00Z")
    lock["can_trade"] = True
    assert "top_level_runtime_boundary" in MODULE.validate_lock(lock)

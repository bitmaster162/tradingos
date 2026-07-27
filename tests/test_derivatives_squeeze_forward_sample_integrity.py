from __future__ import annotations

from types import SimpleNamespace

from tools.forward_sample_integrity import (
    canonical_nonoverlap_events,
    last_exit_index,
)


def event(key: str, signal_ts: str, exit_ts: str) -> dict:
    return {
        "signal_key": key,
        "signal_ts": signal_ts,
        "exit_ts": exit_ts,
        "r": 1.0,
    }


def test_overlapping_raw_event_remains_visible_but_is_excluded() -> None:
    rows = [
        event("first", "2026-07-04T08:00:00Z", "2026-07-05T20:00:00Z"),
        event("overlap", "2026-07-04T12:00:00Z", "2026-07-05T20:00:00Z"),
        event("next", "2026-07-06T00:00:00Z", "2026-07-06T08:00:00Z"),
    ]

    accepted, excluded = canonical_nonoverlap_events(rows)

    assert [row["signal_key"] for row in accepted] == ["first", "next"]
    assert [row["signal_key"] for row in excluded] == ["overlap"]
    assert excluded[0]["sample_exclusion_reason"] == "overlaps_prior_open_trade"
    assert len(rows) == 3


def test_duplicate_signal_key_is_excluded() -> None:
    duplicate = event("same", "2026-07-04T08:00:00Z", "2026-07-04T12:00:00Z")

    accepted, excluded = canonical_nonoverlap_events([duplicate, dict(duplicate)])

    assert len(accepted) == 1
    assert len(excluded) == 1
    assert excluded[0]["sample_exclusion_reason"] == "duplicate_signal_key"


def test_last_exit_index_restores_cross_run_nonoverlap_state() -> None:
    bars = [
        SimpleNamespace(ts="2026-07-04T08:00:00Z"),
        SimpleNamespace(ts="2026-07-04T12:00:00Z"),
        SimpleNamespace(ts="2026-07-05T20:00:00Z"),
        SimpleNamespace(ts="2026-07-06T00:00:00Z"),
    ]

    assert last_exit_index(bars, "2026-07-05T20:00:00Z") == 2
    assert last_exit_index(bars, None) == -1

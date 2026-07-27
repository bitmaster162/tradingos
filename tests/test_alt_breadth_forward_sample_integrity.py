from __future__ import annotations

from pathlib import Path

from tools.forward_sample_integrity import canonical_nonoverlap_events


ROOT = Path(__file__).resolve().parents[1]
OBSERVER = ROOT / "tools" / "alt_breadth_dislocation_forward_observer.py"


def test_alt_observer_uses_cross_run_nonoverlap_state() -> None:
    source = OBSERVER.read_text(encoding="utf-8")

    assert 'last_exit_index(btc, state.get("last_exit_ts"))' in source
    assert 'state["last_exit_ts"] = trade["exit_ts"]' in source
    assert "canonical_nonoverlap_events(journal_rows)" in source
    assert '"summary": summarize(canonical_events)' in source


def test_alt_pending_is_created_only_after_signal_identity() -> None:
    source = OBSERVER.read_text(encoding="utf-8")
    key_position = source.index("key = signal_key")
    pending_position = source.index('pending.append({"signal_key": key')

    assert pending_position > key_position


def test_shared_integrity_policy_rejects_overlap_for_alt_events() -> None:
    accepted, excluded = canonical_nonoverlap_events([
        {"signal_key": "alt-1", "signal_ts": "2026-07-04T08:00:00Z", "exit_ts": "2026-07-04T16:00:00Z"},
        {"signal_key": "alt-2", "signal_ts": "2026-07-04T12:00:00Z", "exit_ts": "2026-07-04T20:00:00Z"},
    ])

    assert [row["signal_key"] for row in accepted] == ["alt-1"]
    assert excluded[0]["sample_exclusion_reason"] == "overlaps_prior_open_trade"

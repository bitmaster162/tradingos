from __future__ import annotations

from datetime import datetime, timezone

from tools.edge_waiting_board import select_next_recheck


def waiting_row(edge_class: str, at_utc: str | None, priority: int = 10) -> dict:
    return {
        "edge_class": edge_class,
        "state": "waiting_sample_gate",
        "earliest_recheck_at_utc": at_utc,
        "priority": priority,
    }


def test_gate_clock_selects_earliest_future_recheck() -> None:
    observed_at = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
    result = select_next_recheck(
        [
            waiting_row("force_order", "2026-07-15T20:00:00Z"),
            waiting_row("microstructure", "2026-07-15T15:56:10+00:00"),
            waiting_row("sample_only", None),
        ],
        observed_at,
    )

    assert result == {
        "status": "scheduled",
        "edge_class": "microstructure",
        "at_utc": "2026-07-15T15:56:10Z",
        "seconds_until": 21370,
        "read_only_recheck": True,
    }


def test_gate_clock_marks_elapsed_recheck_due_but_never_enables_trade() -> None:
    observed_at = datetime(2026, 7, 15, 21, 0, tzinfo=timezone.utc)
    result = select_next_recheck(
        [waiting_row("force_order", "2026-07-15T20:00:00Z")],
        observed_at,
    )

    assert result["status"] == "due"
    assert result["edge_class"] == "force_order"
    assert result["read_only_recheck"] is True


def test_gate_clock_handles_sample_driven_only_rows() -> None:
    observed_at = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
    result = select_next_recheck([waiting_row("sample_only", None)], observed_at)

    assert result["status"] == "sample_driven_only"
    assert result["edge_class"] is None
    assert result["read_only_recheck"] is True

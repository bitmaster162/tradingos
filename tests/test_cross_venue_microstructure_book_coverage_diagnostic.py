from __future__ import annotations

from tools.cross_venue_microstructure_book_coverage_diagnostic import (
    MINUTE_MS,
    classify_coverage,
)


LATEST_MS = 1_000 * MINUTE_MS


def classify(
    *,
    current_pct: float,
    recent_1h_pct: float,
    recent_6h_pct: float,
    minutes_since_gap: int | None,
) -> tuple[str, str, dict]:
    missing = [] if minutes_since_gap is None else [LATEST_MS - minutes_since_gap * MINUTE_MS]
    return classify_coverage(
        {"dual_book_coverage_pct": current_pct},
        {
            "1h": {"dual_book_coverage_pct": recent_1h_pct},
            "6h": {"dual_book_coverage_pct": recent_6h_pct},
        },
        missing,
        LATEST_MS,
        95.0,
        30,
    )


def test_classification_passes_only_when_full_window_meets_locked_threshold() -> None:
    decision, _, recovery = classify(
        current_pct=95.0,
        recent_1h_pct=100.0,
        recent_6h_pct=100.0,
        minutes_since_gap=200,
    )

    assert decision == "microstructure_book_coverage_pass"
    assert recovery["confirmed_since_last_gap"] is True


def test_classification_waits_for_old_gaps_when_recent_windows_are_healthy() -> None:
    decision, _, _ = classify(
        current_pct=80.0,
        recent_1h_pct=100.0,
        recent_6h_pct=99.0,
        minutes_since_gap=45,
    )

    assert decision == "microstructure_book_coverage_wait_for_old_gaps_to_roll_out"


def test_classification_marks_recovered_after_confirmation_even_if_one_hour_window_lags() -> None:
    decision, _, recovery = classify(
        current_pct=80.0,
        recent_1h_pct=86.67,
        recent_6h_pct=96.67,
        minutes_since_gap=44,
    )

    assert decision == "microstructure_book_coverage_recovered_waiting_recent_gap_rollout"
    assert recovery["minutes_since_last_missing"] == 44
    assert recovery["confirmed_since_last_gap"] is True


def test_classification_keeps_recent_gap_as_active_degradation_before_confirmation() -> None:
    decision, _, recovery = classify(
        current_pct=80.0,
        recent_1h_pct=86.67,
        recent_6h_pct=96.67,
        minutes_since_gap=29,
    )

    assert decision == "microstructure_book_coverage_current_polling_degraded"
    assert recovery["confirmed_since_last_gap"] is False


def test_classification_keeps_partial_recovery_when_one_hour_is_healthy_but_six_hour_is_not() -> None:
    decision, _, _ = classify(
        current_pct=80.0,
        recent_1h_pct=100.0,
        recent_6h_pct=90.0,
        minutes_since_gap=45,
    )

    assert decision == "microstructure_book_coverage_partial_recent_recovery"

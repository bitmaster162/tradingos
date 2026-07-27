from __future__ import annotations

from datetime import datetime, timezone

from tools.cross_venue_microstructure_collector_sla_replay import build_replay_report


NOW = datetime(2026, 6, 25, 12, 10, tzinfo=timezone.utc)


def row(ts: str, decision: str = "collector_sla_healthy", failed: list[str] | None = None) -> dict:
    degraded = decision.startswith("collector_sla_degraded")
    return {
        "generated_at": ts,
        "decision": decision,
        "inserted_trades": 0 if degraded else 100,
        "inserted_books": 0 if decision == "collector_sla_degraded_no_book_inserts" else 2,
        "archive_trades_delta": 0 if degraded else 100,
        "archive_books_delta": 0 if degraded else 2,
        "trade_coverage_pct": 99.0,
        "book_coverage_pct": 98.0,
        "failed_checks": failed or ([] if not degraded else ["cycle_inserted_trades"]),
        "can_trade": False,
    }


def test_replay_reports_missing_history() -> None:
    report = build_replay_report([], now=NOW)

    assert report["decision"] == "collector_sla_replay_missing_history"
    assert report["stability_blocker"] == "missing_history"
    assert report["can_trade"] is False


def test_replay_reports_stable_history() -> None:
    report = build_replay_report(
        [
            row("2026-06-25T12:00:00+00:00"),
            row("2026-06-25T12:01:00+00:00"),
            row("2026-06-25T12:02:00+00:00"),
        ],
        now=NOW,
    )

    assert report["decision"] == "collector_sla_replay_stable"
    assert report["observations"] == 3
    assert report["incident_count"] == 0
    assert report["min_inserted_trades"] == 100
    assert report["stability_blocker"] == "none"
    assert report["stability_cooldown_until_utc"] is None


def test_replay_reports_current_degradation() -> None:
    report = build_replay_report(
        [
            row("2026-06-25T12:00:00+00:00"),
            row("2026-06-25T12:01:00+00:00", "collector_sla_degraded_no_trade_inserts", ["cycle_inserted_trades"]),
        ],
        now=NOW,
    )

    assert report["decision"] == "collector_sla_replay_currently_degraded"
    assert report["open_incident"] is True
    assert report["failed_checks"]["cycle_inserted_trades"] == 1
    assert report["stability_blocker"] == "open_degradation_requires_recovery"
    assert report["latest_degraded_generated_at"] == "2026-06-25T12:01:00+00:00"
    assert report["stability_cooldown_until_utc"] == "2026-06-25T18:01:00+00:00"
    assert report["stability_cooldown_remaining_minutes"] == 351.0


def test_replay_reports_recent_recovered_incident() -> None:
    report = build_replay_report(
        [
            row("2026-06-25T12:00:00+00:00"),
            row("2026-06-25T12:01:00+00:00", "collector_sla_degraded_no_book_inserts", ["cycle_inserted_books"]),
            row("2026-06-25T12:02:00+00:00"),
        ],
        now=NOW,
    )

    assert report["decision"] == "collector_sla_replay_recent_degradation"
    assert report["incident_count"] == 1
    assert report["open_incident"] is False
    assert report["stability_blocker"] == "recent_degradation_cooldown"
    assert report["stability_cooldown_until_utc"] == "2026-06-25T18:01:00+00:00"


def test_replay_reports_flapping() -> None:
    report = build_replay_report(
        [
            row("2026-06-25T12:00:00+00:00"),
            row("2026-06-25T12:01:00+00:00", "collector_sla_degraded_no_trade_inserts", ["cycle_inserted_trades"]),
            row("2026-06-25T12:02:00+00:00"),
            row("2026-06-25T12:03:00+00:00", "collector_sla_degraded_no_book_inserts", ["cycle_inserted_books"]),
            row("2026-06-25T12:04:00+00:00"),
        ],
        now=NOW,
    )

    assert report["decision"] == "collector_sla_replay_flapping"
    assert report["incident_count"] == 2
    assert report["state_transitions"] == 4
    assert report["stability_blocker"] == "flapping_cooldown"


def test_replay_filters_by_lookback_window() -> None:
    report = build_replay_report(
        [
            row("2026-06-25T00:00:00+00:00", "collector_sla_degraded_no_trade_inserts", ["cycle_inserted_trades"]),
            row("2026-06-25T12:09:00+00:00"),
        ],
        now=NOW,
        lookback_hours=1,
    )

    assert report["decision"] == "collector_sla_replay_stable"
    assert report["observations"] == 1
    assert report["stability_cooldown_until_utc"] is None


def test_replay_supersedes_only_verified_legacy_gap_false_positives() -> None:
    legacy = row(
        "2026-06-25T12:00:00+00:00",
        "collector_sla_degraded_archive_regressed",
        ["archive_trade_rows_not_regressed", "book_coverage_above_sla"],
    )
    recovered = row("2026-06-25T12:01:00+00:00", "collector_sla_healthy_legacy_gap_rolling_out")
    recovered["legacy_gap_recent_coverage_verified"] = True
    recovered["superseded_legacy_failure_checks"] = [
        "archive_trade_rows_not_regressed",
        "archive_book_rows_not_regressed",
        "archive_feature_rows_not_regressed",
        "book_coverage_above_sla",
    ]

    report = build_replay_report([legacy, recovered], now=NOW)

    assert report["decision"] == "collector_sla_replay_stable"
    assert report["raw_degraded_observations"] == 1
    assert report["superseded_degraded_observations"] == 1
    assert report["degraded_observations"] == 0
    assert report["incident_count"] == 0
    assert report["can_trade"] is False

from datetime import datetime, timezone

from tools.liquidation_real_feed_status_refresh import source_report_freshness


def test_source_report_freshness_rejects_stale_and_future_reports() -> None:
    reference = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
    freshness, blockers = source_report_freshness(
        {
            "fresh": {"generated_at": "2026-07-14T11:55:00Z"},
            "stale": {"generated_at": "2026-07-14T10:00:00Z"},
            "future": {"generated_at": "2026-07-14T12:06:00Z"},
            "missing": {},
        },
        reference=reference,
        maximum_age_minutes=30.0,
    )

    assert freshness["fresh"]["fresh"] is True
    assert freshness["stale"]["fresh"] is False
    assert freshness["future"]["fresh"] is False
    assert freshness["missing"]["fresh"] is False
    assert blockers == [
        "stale_stale_report",
        "future_future_report",
        "missing_or_invalid_timestamp_missing_report",
    ]


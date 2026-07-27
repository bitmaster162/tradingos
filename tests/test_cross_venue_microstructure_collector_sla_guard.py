from __future__ import annotations

from datetime import datetime, timezone

from tools.cross_venue_microstructure_collector_sla_guard import build_sla_report


NOW = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)


def data_quality(
    *,
    generated_at: str = "2026-06-25T11:59:00+00:00",
    inserted_trades: int = 100,
    inserted_books: int = 2,
    trade_cov: float = 99.0,
    book_cov: float = 98.0,
    binance_gaps: int = 0,
    coinbase_gaps: int = 0,
    can_trade: bool = False,
    retention_hours: int | None = None,
) -> dict:
    return {
        "generated_at": generated_at,
        "classification": "cross_venue_microstructure_forward_collecting",
        "current_cycle": {"new_rows": inserted_trades, "inserted_trades": inserted_trades, "inserted_books": inserted_books},
        "archive": {
            "trades": 1000,
            "book_snapshots": 100,
            "minute_feature_rows": 50,
            **({"retention_hours": retention_hours} if retention_hours is not None else {}),
        },
        "coverage": {"span_hours": 20.0, "both_trade_coverage_pct": trade_cov, "both_book_coverage_pct": book_cov},
        "trade_id_integrity": {
            "binance": {"missing_ids": binance_gaps},
            "coinbase": {"missing_ids": coinbase_gaps},
        },
        "runtime_boundary": {"public_data_only": True, "can_trade": can_trade},
        "can_trade": can_trade,
    }


def previous(
    *,
    trades: int = 900,
    books: int = 98,
    features: int = 49,
    trade_cov: float = 99.0,
    book_cov: float = 98.0,
    data_generated_at: str = "2026-06-25T11:58:00+00:00",
) -> dict:
    return {
        "data_generated_at": data_generated_at,
        "archive_trades": trades,
        "archive_books": books,
        "archive_features": features,
        "trade_coverage_pct": trade_cov,
        "book_coverage_pct": book_cov,
        "can_trade": False,
    }


def test_sla_guard_records_baseline_without_previous_state() -> None:
    report = build_sla_report(data_quality(), {}, now=NOW)

    assert report["decision"] == "collector_sla_baseline_recorded"
    assert report["can_trade"] is False
    assert report["runtime_boundary"]["orders_allowed"] is False


def test_sla_guard_accepts_healthy_cycle() -> None:
    report = build_sla_report(data_quality(), previous(), now=NOW)

    assert report["decision"] == "collector_sla_healthy"
    assert report["archive_trades_delta"] == 100
    assert report["failed_checks"] == []


def test_sla_guard_detects_no_trade_inserts() -> None:
    report = build_sla_report(data_quality(inserted_trades=0), previous(), now=NOW)

    assert report["decision"] == "collector_sla_degraded_no_trade_inserts"
    assert "cycle_inserted_trades" in report["failed_checks"]


def test_sla_guard_detects_no_book_inserts() -> None:
    report = build_sla_report(data_quality(inserted_books=0), previous(), now=NOW)

    assert report["decision"] == "collector_sla_degraded_no_book_inserts"
    assert "cycle_inserted_books" in report["failed_checks"]


def test_sla_guard_detects_stale_report() -> None:
    report = build_sla_report(data_quality(generated_at="2026-06-25T11:40:00+00:00"), previous(), now=NOW)

    assert report["decision"] == "collector_sla_degraded_report_stale"
    assert "report_fresh" in report["failed_checks"]


def test_sla_guard_detects_archive_regression() -> None:
    report = build_sla_report(data_quality(), previous(trades=1001), now=NOW)

    assert report["decision"] == "collector_sla_degraded_archive_regressed"
    assert "archive_trade_rows_retention_safe" in report["failed_checks"]


def test_sla_guard_detects_coverage_and_trade_id_failures() -> None:
    report = build_sla_report(data_quality(book_cov=90.0, binance_gaps=3), previous(), now=NOW)

    assert report["decision"] == "collector_sla_degraded_coverage_below_sla"
    assert "collector_book_coverage_above_sla" in report["failed_checks"]
    assert "trade_id_gaps_zero" in report["failed_checks"]


def test_sla_guard_detects_runtime_boundary_violation() -> None:
    report = build_sla_report(data_quality(can_trade=True), previous(), now=NOW)

    assert report["decision"] == "collector_sla_degraded_runtime_boundary"
    assert "can_trade_false" in report["failed_checks"]


def book_diagnostic(*, coverage_6h: float = 99.0, coverage_24h: float = 99.0) -> dict:
    return {
        "recent_windows": {
            "6h": {"dual_book_coverage_pct": coverage_6h},
            "24h": {"dual_book_coverage_pct": coverage_24h},
        }
    }


def test_rolling_retention_and_legacy_gap_do_not_degrade_healthy_collector() -> None:
    report = build_sla_report(
        data_quality(book_cov=55.0, retention_hours=168),
        previous(trades=1100, books=110, features=52, book_cov=55.0),
        book_diagnostic(),
        now=NOW,
    )

    assert report["decision"] == "collector_sla_healthy_legacy_gap_rolling_out"
    assert report["failed_checks"] == []
    assert report["readiness_blockers"] == ["rolling_168h_book_coverage_above_sla"]
    assert report["checks"]["archive_trade_rows_not_regressed"] is False
    assert report["hard_checks"]["archive_trade_rows_retention_safe"] is True
    assert report["feature_retention_drop_rows"] == 2
    assert report["feature_retention_drop_allowance_rows"] == 2
    assert report["feature_retention_drop_bounded"] is True
    assert report["can_trade"] is False


def test_large_feature_retention_drop_remains_hard_failure() -> None:
    report = build_sla_report(
        data_quality(book_cov=98.0, retention_hours=168),
        previous(trades=1100, books=110, features=80, book_cov=98.0),
        book_diagnostic(),
        now=NOW,
    )

    assert report["feature_retention_drop_rows"] == 30
    assert report["feature_retention_drop_allowance_rows"] == 2
    assert report["feature_retention_drop_bounded"] is False
    assert report["decision"] == "collector_sla_degraded_archive_regressed"
    assert "archive_feature_rows_retention_safe" in report["failed_checks"]


def test_bounded_feature_expiry_does_not_flap_when_current_coverage_is_safe() -> None:
    report = build_sla_report(
        data_quality(book_cov=95.5, retention_hours=168),
        previous(trades=1100, books=98, features=52, book_cov=95.46),
        book_diagnostic(coverage_6h=98.33, coverage_24h=85.9),
        now=NOW,
    )

    assert report["archive_features_delta"] == -2
    assert report["feature_retention_drop_bounded"] is True
    assert report["checks"]["recent_24h_book_coverage_above_sla"] is False
    assert report["hard_checks"]["archive_feature_rows_retention_safe"] is True
    assert report["decision"] == "collector_sla_healthy"


def test_recent_book_outage_remains_hard_sla_failure() -> None:
    report = build_sla_report(
        data_quality(book_cov=55.0, retention_hours=168),
        previous(trades=1100, books=110, features=60, book_cov=55.0),
        book_diagnostic(coverage_6h=90.0, coverage_24h=94.0),
        now=NOW,
    )

    assert report["decision"] == "collector_sla_degraded_archive_regressed"
    assert "archive_book_rows_retention_safe" in report["failed_checks"]

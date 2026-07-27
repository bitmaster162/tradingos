import json
from datetime import UTC, datetime

import pytest

from btcusdt_bot.authoritative.archive import AuthoritativeArchive, USER_TRADES_DATASET
from btcusdt_bot.monitoring.post_fill_forward import build_post_fill_forward_report


FLOOR_MS = 1_700_000_000_000
GENERATED_AT_MS = FLOOR_MS + 10_000
COMMON_END_MS = GENERATED_AT_MS - 2_000 - 100


def _bucket(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def _write_lock(tmp_path, *, can_trade=False, minimum_fills=1):
    path = tmp_path / "configs" / "prereg.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "lock_id": "test_post_fill_forward_v1",
        "status": "forward_execution_quality_preregistered",
        "forward_start_ms": FLOOR_MS,
        "data_contract": {
            "symbol": "BTCUSDT",
            "archive_root": "archive",
            "market_root": "market",
            "reference_source": "book_mid",
        },
        "timing": {
            "primary_horizon_seconds": 1,
            "secondary_horizons_seconds": [2],
            "max_pre_fill_age_ms": 100,
            "max_post_horizon_delay_ms": 100,
            "max_capture_event_age_ms": 5_000,
        },
        "evidence_floor": {
            "minimum_evaluated_fills_per_horizon": minimum_fills,
            "minimum_distinct_utc_days": 1,
            "minimum_evaluation_coverage_ratio": 1.0,
        },
        "runtime_boundary": {
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "automatic_promotion_allowed": False,
            "can_trade": can_trade,
        },
        "can_trade": can_trade,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _book_row(timestamp_ms, bid="99", ask="101"):
    return {
        "received_at_ms": timestamp_ms + 1,
        "payload": {
            "e": "bookTicker",
            "E": timestamp_ms,
            "s": "BTCUSDT",
            "b": bid,
            "a": ask,
        },
    }


def _write_book(tmp_path, rows):
    path = tmp_path / "market" / "public" / _bucket(FLOOR_MS) / "btcusdt_bookTicker.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _write_archive(tmp_path, *, rows, end_ms=COMMON_END_MS):
    archive = AuthoritativeArchive(tmp_path / "archive", symbol="BTCUSDT")
    archive.upsert_rows(
        USER_TRADES_DATASET,
        rows,
        coverage_intervals=[(FLOOR_MS, end_ms)],
        updated_at_ms=GENERATED_AT_MS,
    )


def test_forward_observer_uses_one_closed_cohort_and_stays_non_trading(tmp_path) -> None:
    prereg = _write_lock(tmp_path)
    fill_time = FLOOR_MS + 1_000
    _write_archive(
        tmp_path,
        rows=[
            {
                "id": 1,
                "orderId": 2,
                "symbol": "BTCUSDT",
                "time": fill_time,
                "side": "BUY",
                "price": "101",
                "qty": "1",
                "quoteQty": "101",
                "maker": False,
            }
        ],
    )
    _write_book(
        tmp_path,
        [
            _book_row(FLOOR_MS - 10),
            _book_row(fill_time - 50),
            _book_row(fill_time + 1_000, "100", "102"),
            _book_row(fill_time + 2_000, "101", "103"),
            _book_row(GENERATED_AT_MS - 50, "101", "103"),
        ],
    )

    report = build_post_fill_forward_report(
        prereg,
        project_root=tmp_path,
        generated_at_ms=GENERATED_AT_MS,
        credentials_present=False,
    )

    assert report["decision"] == "markout_distribution_ready_for_manual_review"
    assert report["common_window_end_ms"] == COMMON_END_MS
    assert [item["horizon_seconds"] for item in report["horizons"]] == [1, 2]
    assert all(item["raw_fill_count"] == 1 for item in report["horizons"])
    assert all(item["evaluated_fill_count"] == 1 for item in report["horizons"])
    assert report["checks"]["evaluation_coverage_reached"] is True
    assert report["runtime_boundary"]["orders_allowed"] is False
    assert report["can_trade"] is False


def test_missing_archive_and_credentials_are_explicit_blocker(tmp_path) -> None:
    prereg = _write_lock(tmp_path)
    _write_book(
        tmp_path,
        [
            _book_row(FLOOR_MS - 10),
            _book_row(GENERATED_AT_MS - 50),
        ],
    )

    report = build_post_fill_forward_report(
        prereg,
        project_root=tmp_path,
        generated_at_ms=GENERATED_AT_MS,
        credentials_present=False,
    )

    assert report["decision"] == "waiting_demo_credentials_for_authoritative_fills"
    assert "demo_credentials_missing_for_authoritative_fills" in report["blockers"]
    assert report["book_capture"]["capture_fresh"] is True
    assert report["can_trade"] is False


def test_complete_archive_with_no_fills_is_not_claimed_ready(tmp_path) -> None:
    prereg = _write_lock(tmp_path)
    _write_archive(tmp_path, rows=[])
    _write_book(
        tmp_path,
        [
            _book_row(FLOOR_MS - 10),
            _book_row(GENERATED_AT_MS - 50),
        ],
    )

    report = build_post_fill_forward_report(
        prereg,
        project_root=tmp_path,
        generated_at_ms=GENERATED_AT_MS,
        credentials_present=True,
    )

    assert report["decision"] == "no_authoritative_fills_observed"
    assert report["checks"]["authoritative_coverage_complete"] is True
    assert report["checks"]["minimum_evaluated_fills_reached"] is False
    assert report["can_trade"] is False


def test_partial_reference_coverage_fails_closed(tmp_path) -> None:
    prereg = _write_lock(tmp_path)
    fill_time = FLOOR_MS + 1_000
    _write_archive(
        tmp_path,
        rows=[
            {
                "id": 1,
                "symbol": "BTCUSDT",
                "time": fill_time,
                "side": "BUY",
                "price": "101",
                "qty": "1",
            }
        ],
    )
    _write_book(
        tmp_path,
        [
            _book_row(FLOOR_MS - 10),
            _book_row(fill_time - 50),
            _book_row(fill_time + 1_000),
            _book_row(GENERATED_AT_MS - 50),
        ],
    )

    report = build_post_fill_forward_report(
        prereg,
        project_root=tmp_path,
        generated_at_ms=GENERATED_AT_MS,
        credentials_present=True,
    )

    assert report["decision"] == "markout_reference_coverage_incomplete"
    assert report["checks"]["evaluation_coverage_reached"] is False
    assert report["can_trade"] is False


def test_lock_rejects_any_trading_permission(tmp_path) -> None:
    prereg = _write_lock(tmp_path, can_trade=True)

    with pytest.raises(ValueError, match="must_disable_trading"):
        build_post_fill_forward_report(
            prereg,
            project_root=tmp_path,
            generated_at_ms=GENERATED_AT_MS,
            credentials_present=False,
        )

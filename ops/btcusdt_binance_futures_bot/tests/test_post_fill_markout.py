import json
from decimal import Decimal

import pytest

from btcusdt_bot.authoritative.archive import AuthoritativeArchive, USER_TRADES_DATASET
from btcusdt_bot.monitoring.post_fill_markout import (
    BOOK_MID,
    MARK_PRICE,
    PostFillMarkoutConfig,
    analyze_post_fill_markout,
)


DAY_START_MS = 1_700_000_000_000
DAY_END_MS = DAY_START_MS + 10_000


def _write_references(tmp_path, rows, *, source=BOOK_MID):
    bucket = "2023-11-14"
    if source == BOOK_MID:
        path = tmp_path / "capture" / "public" / bucket / "btcusdt_bookTicker.jsonl"
    else:
        path = tmp_path / "capture" / "market" / bucket / "btcusdt_markPrice_1s.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _book_row(timestamp_ms, bid, ask):
    return {
        "received_at_ms": timestamp_ms + 1,
        "payload": {
            "e": "bookTicker",
            "E": timestamp_ms,
            "s": "BTCUSDT",
            "b": str(bid),
            "a": str(ask),
        },
    }


def _mark_row(timestamp_ms, price):
    return {
        "received_at_ms": timestamp_ms + 1,
        "payload": {
            "e": "markPriceUpdate",
            "E": timestamp_ms,
            "s": "BTCUSDT",
            "p": str(price),
        },
    }


def _archive_fill(tmp_path, *, side="BUY", price="101", maker=False, coverage=True):
    archive = AuthoritativeArchive(tmp_path / "archive", symbol="BTCUSDT")
    coverage_intervals = [(DAY_START_MS, DAY_END_MS)] if coverage else [(DAY_START_MS, DAY_START_MS + 500)]
    archive.upsert_rows(
        USER_TRADES_DATASET,
        [
            {
                "id": 11,
                "orderId": 22,
                "symbol": "BTCUSDT",
                "time": DAY_START_MS + 1_000,
                "side": side,
                "price": price,
                "qty": "1",
                "quoteQty": price,
                "maker": maker,
            }
        ],
        coverage_intervals=coverage_intervals,
        updated_at_ms=DAY_END_MS,
    )


def _config(tmp_path, *, source=BOOK_MID, max_pre=200, max_post=200):
    return PostFillMarkoutConfig(
        archive_root=tmp_path / "archive",
        market_root=tmp_path / "capture",
        symbol="BTCUSDT",
        start_ms=DAY_START_MS,
        end_ms=DAY_END_MS,
        horizon_ms=2_000,
        max_pre_fill_age_ms=max_pre,
        max_post_horizon_delay_ms=max_post,
        reference_source=source,
    )


def test_book_mid_markout_uses_last_pre_and_first_post_reference(tmp_path) -> None:
    _archive_fill(tmp_path)
    _write_references(
        tmp_path,
        [
            _book_row(DAY_START_MS + 900, "99", "101"),
            _book_row(DAY_START_MS + 3_100, "101", "103"),
        ],
    )

    report = analyze_post_fill_markout(_config(tmp_path), generated_at_ms=123)

    assert report.decision == "book_mid_markout_ready_research_only"
    assert report.evaluated_fill_count == 1
    assert report.evaluation_coverage_ratio == Decimal("1")
    assert report.causality_mode == "exchange_event_time"
    observation = report.observations[0]
    assert observation.pre_reference_age_ms == 100
    assert observation.post_reference_delay_ms == 100
    assert observation.effective_spread_bps == Decimal("200")
    assert observation.realized_spread_bps == Decimal("-200")
    assert observation.price_impact_bps == Decimal("400")
    assert observation.effective_spread_bps == (
        observation.realized_spread_bps + observation.price_impact_bps
    )
    assert observation.signed_markout_bps > 0
    assert observation.markout_class == "favorable"
    assert report.can_trade is False


def test_sell_markout_sign_is_favorable_when_price_falls(tmp_path) -> None:
    _archive_fill(tmp_path, side="SELL", price="99", maker=True)
    _write_references(
        tmp_path,
        [
            _book_row(DAY_START_MS + 900, "99", "101"),
            _book_row(DAY_START_MS + 3_000, "97", "99"),
        ],
    )

    report = analyze_post_fill_markout(_config(tmp_path), generated_at_ms=123)

    observation = report.observations[0]
    assert observation.signed_markout_bps > 0
    assert report.maker_quote_weighted_signed_markout_bps == observation.signed_markout_bps
    assert report.taker_quote_weighted_signed_markout_bps is None


def test_missing_liquidity_role_is_not_misclassified_as_taker(tmp_path) -> None:
    archive = AuthoritativeArchive(tmp_path / "archive", symbol="BTCUSDT")
    archive.upsert_rows(
        USER_TRADES_DATASET,
        [
            {
                "id": 12,
                "symbol": "BTCUSDT",
                "time": DAY_START_MS + 1_000,
                "side": "BUY",
                "price": "101",
                "qty": "1",
            }
        ],
        coverage_intervals=[(DAY_START_MS, DAY_END_MS)],
        updated_at_ms=DAY_END_MS,
    )
    _write_references(
        tmp_path,
        [
            _book_row(DAY_START_MS + 900, "99", "101"),
            _book_row(DAY_START_MS + 3_100, "101", "103"),
        ],
    )

    report = analyze_post_fill_markout(_config(tmp_path), generated_at_ms=123)

    assert report.unknown_liquidity_role_count == 1
    assert report.maker_quote_weighted_signed_markout_bps is None
    assert report.taker_quote_weighted_signed_markout_bps is None


def test_reference_freshness_budgets_fail_closed(tmp_path) -> None:
    _archive_fill(tmp_path)
    _write_references(
        tmp_path,
        [
            _book_row(DAY_START_MS + 700, "99", "101"),
            _book_row(DAY_START_MS + 3_500, "101", "103"),
        ],
    )

    report = analyze_post_fill_markout(
        _config(tmp_path, max_pre=200, max_post=200),
        generated_at_ms=123,
    )

    assert report.decision == "insufficient_reference_coverage"
    assert report.stale_pre_reference_count == 1
    assert report.evaluated_fill_count == 0


def test_late_post_horizon_reference_is_reported_separately(tmp_path) -> None:
    _archive_fill(tmp_path)
    _write_references(
        tmp_path,
        [
            _book_row(DAY_START_MS + 900, "99", "101"),
            _book_row(DAY_START_MS + 3_500, "101", "103"),
        ],
    )

    report = analyze_post_fill_markout(
        _config(tmp_path, max_pre=200, max_post=200),
        generated_at_ms=123,
    )

    assert report.decision == "insufficient_reference_coverage"
    assert report.stale_pre_reference_count == 0
    assert report.late_post_reference_count == 1
    assert report.evaluated_fill_count == 0


def test_mark_price_source_is_explicit_proxy_without_spread_claims(tmp_path) -> None:
    _archive_fill(tmp_path)
    _write_references(
        tmp_path,
        [
            _mark_row(DAY_START_MS + 900, "100"),
            _mark_row(DAY_START_MS + 3_100, "102"),
        ],
        source=MARK_PRICE,
    )

    report = analyze_post_fill_markout(_config(tmp_path, source=MARK_PRICE), generated_at_ms=123)

    assert report.decision == "mark_price_proxy_ready_research_only"
    assert report.metric_kind == "mark_price_proxy"
    assert report.quote_weighted_signed_markout_bps is not None
    assert report.quote_weighted_effective_spread_bps is None
    assert report.quote_weighted_realized_spread_bps is None
    assert report.quote_weighted_price_impact_bps is None


def test_partial_authoritative_coverage_is_never_reported_ready(tmp_path) -> None:
    _archive_fill(tmp_path, coverage=False)
    _write_references(
        tmp_path,
        [
            _book_row(DAY_START_MS + 900, "99", "101"),
            _book_row(DAY_START_MS + 3_100, "101", "103"),
        ],
    )

    report = analyze_post_fill_markout(_config(tmp_path), generated_at_ms=123)

    assert report.archive_coverage_ratio < Decimal("1")
    assert report.decision == "partial_authoritative_coverage"
    assert report.can_trade is False


def test_markout_requires_explicit_positive_horizon(tmp_path) -> None:
    config = _config(tmp_path)
    invalid = PostFillMarkoutConfig(
        archive_root=config.archive_root,
        market_root=config.market_root,
        symbol=config.symbol,
        start_ms=config.start_ms,
        end_ms=config.end_ms,
        horizon_ms=0,
        max_pre_fill_age_ms=config.max_pre_fill_age_ms,
        max_post_horizon_delay_ms=config.max_post_horizon_delay_ms,
        reference_source=config.reference_source,
    )

    with pytest.raises(ValueError, match="horizon_ms_must_be_positive"):
        analyze_post_fill_markout(invalid, generated_at_ms=123)

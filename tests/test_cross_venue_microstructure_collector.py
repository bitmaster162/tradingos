from __future__ import annotations

from tools.cross_venue_microstructure_collector import (
    backfill_trade_id_gaps,
    coverage_summary,
    merge_books,
    merge_trades,
    minute_features,
    parse_binance_trade,
    parse_coinbase_trade,
    summarize_book,
    trade_id_gaps,
)


def test_binance_trade_derives_aggressor_from_buyer_is_maker() -> None:
    sell = parse_binance_trade({"a": 1, "p": "100", "q": "2", "T": 60_000, "m": True})
    buy = parse_binance_trade({"a": 2, "p": "101", "q": "1", "T": 61_000, "m": False})
    assert sell["aggressor_side"] == "SELL"
    assert buy["aggressor_side"] == "BUY"
    assert sell["notional"] == 200.0


def test_coinbase_reported_side_is_not_promoted_to_aggressor() -> None:
    row = parse_coinbase_trade(
        {"trade_id": 1, "price": "100", "size": "2", "time": "1970-01-01T00:01:00Z", "side": "buy"}
    )
    assert row["reported_side"] == "BUY"
    assert row["aggressor_side"] == ""
    assert "do_not_use" in row["side_semantics"]


def test_book_summary_rejects_crossed_book_and_computes_imbalance() -> None:
    row = summarize_book(
        {"lastUpdateId": 7, "bids": [["100", "3"]], "asks": [["101", "1"]]},
        venue="binance",
        product="BTCUSDT",
        collected_ms=60_000,
    )
    assert row["spread_bps"] > 0
    assert row["top_imbalance"] == 0.5
    assert row["sequence"] == "7"


def test_trade_and_book_merge_deduplicate_overlapping_polls() -> None:
    trade = {"venue": "binance", "trade_id": "1", "time_ms": 60_000}
    trades, duplicates = merge_trades([trade], [{**trade, "price": 2}], cutoff_ms=0)
    assert len(trades) == 1
    assert trades[0]["price"] == 2
    assert duplicates == 1
    book = {"venue": "binance", "collected_ms": 60_000}
    books, book_duplicates = merge_books([book], [{**book, "bid": 2}], cutoff_ms=0)
    assert len(books) == 1
    assert books[0]["bid"] == 2
    assert book_duplicates == 1


def test_minute_features_keep_coinbase_delta_blank() -> None:
    binance = parse_binance_trade({"a": 1, "p": "100", "q": "2", "T": 60_000, "m": False})
    coinbase = parse_coinbase_trade(
        {"trade_id": 2, "price": "100", "size": "1", "time": "1970-01-01T00:01:01Z", "side": "sell"}
    )
    books = [
        summarize_book({"bids": [["99", "1"]], "asks": [["101", "1"]]}, venue="binance", product="BTCUSDT", collected_ms=61_000),
        summarize_book({"bids": [["99", "1"]], "asks": [["101", "1"]]}, venue="coinbase", product="BTC-USD", collected_ms=61_000),
    ]
    rows = minute_features([binance, coinbase], books, completed_before_ms=120_000)
    by_venue = {row["venue"]: row for row in rows}
    assert by_venue["binance"]["delta_notional"] == 200.0
    assert by_venue["coinbase"]["delta_notional"] == ""
    assert by_venue["coinbase"]["aggressor_side_usable"] == "false"


def test_coverage_requires_both_venues_in_same_minute() -> None:
    rows = [
        {"minute_ms": 0, "venue": "binance", "trades": 2, "book_snapshots": 1},
        {"minute_ms": 0, "venue": "coinbase", "trades": 1, "book_snapshots": 1},
        {"minute_ms": 60_000, "venue": "binance", "trades": 1, "book_snapshots": 1},
    ]
    coverage = coverage_summary(rows)
    assert coverage["expected_minutes"] == 2
    assert coverage["both_trade_coverage_pct"] == 50.0
    assert coverage["both_book_coverage_pct"] == 50.0


def test_trade_id_gap_audit_and_bounded_backfill() -> None:
    existing = [
        {"venue": "binance", "trade_id": "10"},
        {"venue": "binance", "trade_id": "13"},
        {"venue": "coinbase", "trade_id": "20"},
        {"venue": "coinbase", "trade_id": "23"},
    ]

    def fake_fetch(url: str):
        if "binance.com" in url:
            return [
                {"a": 11, "p": "100", "q": "1", "T": 1_000, "m": False},
                {"a": 12, "p": "100", "q": "1", "T": 2_000, "m": True},
            ]
        return [
            {"trade_id": 22, "price": "100", "size": "1", "time": "1970-01-01T00:00:02Z", "side": "buy"},
            {"trade_id": 21, "price": "100", "size": "1", "time": "1970-01-01T00:00:01Z", "side": "sell"},
        ]

    assert trade_id_gaps(existing, "binance") == [(10, 13, 2)]
    recovered, audit = backfill_trade_id_gaps(
        existing,
        binance_product="BTCUSDT",
        coinbase_product="BTC-USD",
        max_pages=4,
        fetcher=fake_fetch,
    )
    assert audit["pages_used"] == 2
    assert audit["rows_recovered"] == 4
    assert {int(row["trade_id"]) for row in recovered} == {11, 12, 21, 22}

from __future__ import annotations

import inspect

from tools import cross_venue_microstructure_sqlite_collector as collector

from tools.cross_venue_microstructure_sqlite_collector import (
    backfill_legacy_minutes,
    connect_db,
    database_gap_sentinels,
    incremental_rows,
    insert_books,
    insert_trades,
    integrity_summary,
    rebuild_minutes,
)


def test_sqlite_unique_trade_key_and_minute_rebuild(tmp_path) -> None:
    conn = connect_db(tmp_path / "micro.sqlite3")
    trades = [
        {"venue": "binance", "product": "BTCUSDT", "trade_id": "1", "time_ms": 60_000, "price": 100, "quantity": 1, "notional": 100, "reported_side": "SELL_MAKER", "aggressor_side": "BUY", "side_semantics": "x"},
        {"venue": "binance", "product": "BTCUSDT", "trade_id": "2", "time_ms": 61_000, "price": 101, "quantity": 1, "notional": 101, "reported_side": "BUY_MAKER", "aggressor_side": "SELL", "side_semantics": "x"},
    ]
    assert insert_trades(conn, trades) == 2
    assert insert_trades(conn, trades) == 0
    books = [{"venue": "binance", "product": "BTCUSDT", "collected_ms": 62_000, "bid": 100, "ask": 101, "bid_size": 2, "ask_size": 1, "mid": 100.5, "spread_bps": 99.5, "top_imbalance": 1 / 3, "sequence": "1"}]
    assert insert_books(conn, books) == 1
    rebuild_minutes(conn, {60_000})
    row = conn.execute("SELECT * FROM minute_features").fetchone()
    assert row["trades"] == 2
    assert row["delta_notional"] == -1
    assert integrity_summary(conn)["binance"]["missing_ids"] == 0
    conn.close()


def test_incremental_rows_only_returns_ids_after_checkpoint(monkeypatch) -> None:
    latest = [
        {"venue": "binance", "trade_id": "10", "time_ms": 1},
        {"venue": "binance", "trade_id": "11", "time_ms": 2},
        {"venue": "coinbase", "trade_id": "20", "time_ms": 1},
        {"venue": "coinbase", "trade_id": "21", "time_ms": 2},
    ]
    monkeypatch.setattr("tools.cross_venue_microstructure_sqlite_collector.backfill_trade_id_gaps", lambda *args, **kwargs: ([], {"pages_used": 0}))
    rows, _ = incremental_rows(latest, {"binance": 10, "coinbase": 20}, binance_product="BTCUSDT", coinbase_product="BTC-USD", max_backfill_pages=2)
    assert {(row["venue"], int(row["trade_id"])) for row in rows} == {("binance", 11), ("coinbase", 21)}


def test_legacy_feature_backfill_runs_once(tmp_path) -> None:
    conn = connect_db(tmp_path / "micro.sqlite3")
    insert_trades(conn, [{"venue": "binance", "product": "BTCUSDT", "trade_id": "1", "time_ms": 60_000, "price": 100, "quantity": 1, "notional": 100, "reported_side": "x", "aggressor_side": "BUY", "side_semantics": "x"}])
    first = backfill_legacy_minutes(conn)
    second = backfill_legacy_minutes(conn)
    assert first == {"performed": True, "minutes": 1}
    assert second["performed"] is False
    assert conn.execute("SELECT COUNT(*) FROM minute_features").fetchone()[0] == 1
    conn.close()


def test_database_gap_sentinels_finds_internal_gaps(tmp_path) -> None:
    conn = connect_db(tmp_path / "micro.sqlite3")
    rows = []
    for venue, product, ids in (
        ("binance", "BTCUSDT", (1, 2, 5)),
        ("coinbase", "BTC-USD", (10, 13)),
    ):
        rows.extend(
            {
                "venue": venue,
                "product": product,
                "trade_id": str(trade_id),
                "time_ms": trade_id * 1_000,
                "price": 100,
                "quantity": 1,
                "notional": 100,
                "reported_side": "x",
                "aggressor_side": "BUY",
                "side_semantics": "x",
            }
            for trade_id in ids
        )
    insert_trades(conn, rows)
    assert database_gap_sentinels(conn) == [
        {"venue": "binance", "trade_id": "2"},
        {"venue": "binance", "trade_id": "5"},
        {"venue": "coinbase", "trade_id": "10"},
        {"venue": "coinbase", "trade_id": "13"},
    ]
    conn.close()


def test_main_releases_sqlite_writer_before_remote_gap_backfill() -> None:
    source = inspect.getsource(collector.main)
    insert_at = source.index("inserted_trades = insert_trades")
    commit_at = source.index("conn.commit()", insert_at)
    remote_backfill_at = source.index("backfill_trade_id_gaps", insert_at)

    assert insert_at < commit_at < remote_backfill_at

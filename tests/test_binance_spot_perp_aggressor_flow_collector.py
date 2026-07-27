from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from tools.binance_spot_perp_aggressor_flow_collector import (
    collect_incremental_market,
    connect_db,
    coverage_summary,
    derive_contiguous_cursor,
    evaluate_readiness,
    insert_trades,
    integrity_summary,
    load_collection_cursors,
    parse_agg_trade,
    rebuild_minutes,
    store_collection_cursor,
)


def raw(trade_id: int, timestamp: int, *, maker: bool, price: str = "100", quantity: str = "1") -> dict:
    return {"a": trade_id, "T": timestamp, "m": maker, "p": price, "q": quantity}


def test_aggressor_side_is_derived_identically_for_spot_and_perpetual() -> None:
    for market in ("spot", "perpetual"):
        sell = parse_agg_trade(raw(1, 60_001, maker=True), market=market, symbol="BTCUSDT")
        buy = parse_agg_trade(raw(2, 60_002, maker=False), market=market, symbol="BTCUSDT")
        assert sell["aggressor_side"] == "SELL"
        assert buy["aggressor_side"] == "BUY"
        assert sell["buyer_is_maker"] == 1
        assert buy["buyer_is_maker"] == 0


def test_incremental_collection_repairs_gap_before_latest_tail() -> None:
    calls: list[int | None] = []

    def fetcher(url: str):
        query = parse_qs(urlparse(url).query)
        from_id = int(query["fromId"][0]) if "fromId" in query else None
        calls.append(from_id)
        if from_id is None:
            return [raw(item, 120_000 + item, maker=False) for item in range(10, 13)]
        return [raw(item, 60_000 + item, maker=True) for item in range(from_id, min(from_id + 3, 10))]

    rows, meta = collect_incremental_market(
        market="spot",
        symbol="BTCUSDT",
        limit=3,
        last_id=4,
        max_backfill_pages=3,
        fetcher=fetcher,
    )
    assert [row["agg_trade_id"] for row in rows] == list(range(5, 13))
    assert calls == [None, 5, 8]
    assert meta["unresolved_ids"] == 0
    assert meta["page_budget_exhausted"] is False
    assert meta["contiguous_last_id"] == 12


def test_bounded_backfill_never_advances_cursor_across_unrepaired_gap() -> None:
    def fetcher(url: str):
        query = parse_qs(urlparse(url).query)
        from_id = int(query["fromId"][0]) if "fromId" in query else None
        if from_id is None:
            return [raw(item, 120_000 + item, maker=False) for item in range(10, 13)]
        return [raw(item, 60_000 + item, maker=True) for item in range(from_id, from_id + 3)]

    rows, meta = collect_incremental_market(
        market="spot",
        symbol="BTCUSDT",
        limit=3,
        last_id=4,
        max_backfill_pages=1,
        fetcher=fetcher,
    )

    assert [row["agg_trade_id"] for row in rows] == [5, 6, 7, 10, 11, 12]
    assert meta["contiguous_last_id"] == 7
    assert meta["unresolved_ids"] == 5
    assert meta["page_budget_exhausted"] is True


def test_contiguous_cursor_persists_until_large_internal_gap_is_repaired(tmp_path) -> None:
    conn = connect_db(tmp_path / "flow.sqlite3")
    insert_trades(
        conn,
        [
            parse_agg_trade(raw(item, 60_000 + item, maker=False), market="spot", symbol="BTCUSDT")
            for item in [1, 2, 3, 4, 10, 11, 12]
        ],
    )

    # Legacy migration must stop before the hole instead of trusting MAX(id)=12.
    assert load_collection_cursors(conn)["spot"] == 4
    store_collection_cursor(conn, "spot", 4)
    conn.commit()

    insert_trades(
        conn,
        [
            parse_agg_trade(raw(item, 60_000 + item, maker=False), market="spot", symbol="BTCUSDT")
            for item in [5, 6]
        ],
    )
    cursor = derive_contiguous_cursor(conn, "spot", start_at=4)
    assert cursor == 6
    store_collection_cursor(conn, "spot", cursor)
    conn.commit()
    assert load_collection_cursors(conn)["spot"] == 6

    insert_trades(
        conn,
        [
            parse_agg_trade(raw(item, 60_000 + item, maker=False), market="spot", symbol="BTCUSDT")
            for item in [7, 8]
        ],
    )
    cursor = derive_contiguous_cursor(conn, "spot", start_at=6)
    assert cursor == 8
    store_collection_cursor(conn, "spot", cursor)
    conn.commit()
    assert load_collection_cursors(conn)["spot"] == 8

    # Filling the final missing ID joins the previously stored fresh tail.
    insert_trades(
        conn,
        [parse_agg_trade(raw(9, 60_009, maker=False), market="spot", symbol="BTCUSDT")],
    )
    assert derive_contiguous_cursor(conn, "spot", start_at=8) == 12
    conn.close()


def test_sqlite_dedup_and_completed_minute_flow_features(tmp_path) -> None:
    conn = connect_db(tmp_path / "flow.sqlite3")
    rows = [
        parse_agg_trade(raw(1, 60_001, maker=False, price="100", quantity="2"), market="spot", symbol="BTCUSDT"),
        parse_agg_trade(raw(2, 60_002, maker=True, price="101", quantity="1"), market="spot", symbol="BTCUSDT"),
        parse_agg_trade(raw(11, 60_003, maker=False, price="100", quantity="1"), market="perpetual", symbol="BTCUSDT"),
        parse_agg_trade(raw(12, 60_004, maker=True, price="99", quantity="1"), market="perpetual", symbol="BTCUSDT"),
    ]
    assert insert_trades(conn, rows) == 4
    assert insert_trades(conn, rows) == 0
    rebuild_minutes(conn, {60_000})
    spot = conn.execute("SELECT * FROM minute_features WHERE market='spot'").fetchone()
    perpetual = conn.execute("SELECT * FROM minute_features WHERE market='perpetual'").fetchone()
    assert spot["trades"] == 2
    assert spot["delta_notional"] == 99
    assert perpetual["delta_notional"] == 1
    coverage = coverage_summary(conn, now_ms=180_000)
    assert coverage["common_complete_minutes"] == 1
    assert coverage["dual_market_coverage_pct"] == 100.0
    assert coverage["aggressor_side_semantics_valid"] is True
    conn.close()


def test_research_gate_stays_closed_until_forward_span_is_complete(tmp_path) -> None:
    conn = connect_db(tmp_path / "flow.sqlite3")
    for market, base_id in (("spot", 1), ("perpetual", 100)):
        insert_trades(
            conn,
            [
                parse_agg_trade(
                    raw(base_id, 60_001, maker=False), market=market, symbol="BTCUSDT"
                )
            ],
        )
    rebuild_minutes(conn, {60_000})
    coverage = coverage_summary(conn, now_ms=180_000)
    integrity = integrity_summary(conn)
    classification, ready, blockers = evaluate_readiness(
        coverage=coverage,
        integrity=integrity,
        fetch_errors={},
        min_research_hours=168.0,
        min_coverage_pct=95.0,
        max_fresh_lag_seconds=120.0,
    )
    assert classification == "binance_spot_perp_aggressor_flow_forward_collecting"
    assert ready is False
    assert "minimum_forward_span_not_reached" in blockers
    conn.close()


def test_coverage_is_zero_when_only_one_market_has_completed_minutes(tmp_path) -> None:
    conn = connect_db(tmp_path / "flow.sqlite3")
    insert_trades(
        conn,
        [
            parse_agg_trade(
                raw(1, 60_001, maker=False), market="spot", symbol="BTCUSDT"
            )
        ],
    )
    rebuild_minutes(conn, {60_000})
    coverage = coverage_summary(conn, now_ms=180_000)
    assert coverage["complete_spot_minutes"] == 1
    assert coverage["complete_perpetual_minutes"] == 0
    assert coverage["expected_overlap_minutes"] == 0
    assert coverage["dual_market_coverage_pct"] == 0.0
    assert coverage["overlap_start"] is None
    assert coverage["overlap_end"] is None
    conn.close()

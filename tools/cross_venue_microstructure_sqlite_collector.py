#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cross_venue_microstructure_collector import (  # noqa: E402
    BOOK_FIELDS,
    FEATURE_FIELDS,
    MINUTE_MS,
    TRADE_FIELDS,
    backfill_trade_id_gaps,
    coverage_summary,
    fetch_cycle,
    iso_from_ms,
    now_iso,
    portable_path,
    resolve_path,
    sha256_file,
    write_csv,
)


SCHEMA_VERSION = 1


def connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS trades (
            venue TEXT NOT NULL,
            product TEXT NOT NULL,
            trade_id INTEGER NOT NULL,
            time_ms INTEGER NOT NULL,
            price REAL NOT NULL,
            quantity REAL NOT NULL,
            notional REAL NOT NULL,
            reported_side TEXT NOT NULL,
            aggressor_side TEXT NOT NULL,
            side_semantics TEXT NOT NULL,
            PRIMARY KEY (venue, trade_id)
        );
        CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(time_ms);
        CREATE INDEX IF NOT EXISTS idx_trades_venue_time ON trades(venue,time_ms);
        CREATE TABLE IF NOT EXISTS book_snapshots (
            venue TEXT NOT NULL,
            product TEXT NOT NULL,
            collected_ms INTEGER NOT NULL,
            bid REAL NOT NULL,
            ask REAL NOT NULL,
            bid_size REAL NOT NULL,
            ask_size REAL NOT NULL,
            mid REAL NOT NULL,
            spread_bps REAL NOT NULL,
            top_imbalance REAL NOT NULL,
            sequence TEXT NOT NULL,
            PRIMARY KEY (venue, collected_ms)
        );
        CREATE INDEX IF NOT EXISTS idx_books_time ON book_snapshots(collected_ms);
        CREATE INDEX IF NOT EXISTS idx_books_venue_time ON book_snapshots(venue,collected_ms);
        CREATE TABLE IF NOT EXISTS minute_features (
            venue TEXT NOT NULL,
            product TEXT NOT NULL,
            minute_ms INTEGER NOT NULL,
            trades INTEGER NOT NULL,
            notional REAL NOT NULL,
            price_first REAL,
            price_last REAL,
            return_bps REAL,
            buy_notional REAL,
            sell_notional REAL,
            delta_notional REAL,
            aggressor_side_usable INTEGER NOT NULL,
            book_snapshots INTEGER NOT NULL,
            avg_spread_bps REAL,
            avg_top_imbalance REAL,
            PRIMARY KEY (venue, minute_ms)
        );
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    return conn


def metadata_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else None


def metadata_set(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def trade_values(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(row["venue"]), str(row["product"]), int(row["trade_id"]), int(row["time_ms"]),
        float(row["price"]), float(row["quantity"]), float(row["notional"]),
        str(row.get("reported_side") or ""), str(row.get("aggressor_side") or ""),
        str(row.get("side_semantics") or ""),
    )


def book_values(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(row["venue"]), str(row["product"]), int(row["collected_ms"]), float(row["bid"]),
        float(row["ask"]), float(row["bid_size"]), float(row["ask_size"]), float(row["mid"]),
        float(row["spread_bps"]), float(row["top_imbalance"]), str(row.get("sequence") or ""),
    )


def insert_trades(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO trades VALUES(?,?,?,?,?,?,?,?,?,?)",
        (trade_values(row) for row in rows),
    )
    return conn.total_changes - before


def insert_books(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO book_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (book_values(row) for row in rows),
    )
    return conn.total_changes - before


def migrate_legacy_csv(conn: sqlite3.Connection, out_dir: Path, batch_size: int = 10_000) -> dict[str, Any]:
    if metadata_get(conn, "legacy_migration_complete") == "true":
        return {"performed": False, "reason": "already_complete", "trades": 0, "books": 0}
    trade_path = out_dir / "trades.csv"
    book_path = out_dir / "top_of_book.csv"
    migrated_trades = migrated_books = 0
    if trade_path.is_file():
        batch: list[dict[str, Any]] = []
        with trade_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                batch.append(row)
                if len(batch) >= batch_size:
                    migrated_trades += insert_trades(conn, batch)
                    batch.clear()
            if batch:
                migrated_trades += insert_trades(conn, batch)
    if book_path.is_file():
        batch = []
        with book_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                batch.append(row)
                if len(batch) >= batch_size:
                    migrated_books += insert_books(conn, batch)
                    batch.clear()
            if batch:
                migrated_books += insert_books(conn, batch)
    metadata_set(conn, "legacy_migration_complete", "true")
    metadata_set(conn, "legacy_migration_at", now_iso())
    conn.commit()
    return {
        "performed": True,
        "legacy_trades_path": str(trade_path),
        "legacy_books_path": str(book_path),
        "legacy_files_preserved": True,
        "trades": migrated_trades,
        "books": migrated_books,
    }


def last_trade_ids(conn: sqlite3.Connection) -> dict[str, int | None]:
    output: dict[str, int | None] = {}
    for venue in ("binance", "coinbase"):
        row = conn.execute("SELECT MAX(trade_id) FROM trades WHERE venue=?", (venue,)).fetchone()
        output[venue] = int(row[0]) if row and row[0] is not None else None
    return output


def incremental_rows(
    latest: list[dict[str, Any]], last_ids: dict[str, int | None], *, binance_product: str,
    coinbase_product: str, max_backfill_pages: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sentinels = [
        {"venue": venue, "trade_id": str(last_id)}
        for venue, last_id in last_ids.items() if last_id is not None
    ]
    recovered, backfill = backfill_trade_id_gaps(
        sentinels + latest,
        binance_product=binance_product,
        coinbase_product=coinbase_product,
        max_pages=max_backfill_pages,
    )
    rows: dict[tuple[str, int], dict[str, Any]] = {}
    for row in latest + recovered:
        venue = str(row["venue"])
        trade_id = int(row["trade_id"])
        previous = last_ids.get(venue)
        if previous is None or trade_id > previous:
            rows[(venue, trade_id)] = row
    return sorted(rows.values(), key=lambda row: (int(row["time_ms"]), str(row["venue"]), int(row["trade_id"]))), backfill


def rebuild_minutes(conn: sqlite3.Connection, minutes: set[int]) -> None:
    for minute_ms in sorted(minutes):
        end_ms = minute_ms + MINUTE_MS
        for venue in ("binance", "coinbase"):
            trades = conn.execute(
                "SELECT * FROM trades WHERE venue=? AND time_ms>=? AND time_ms<? ORDER BY time_ms,trade_id",
                (venue, minute_ms, end_ms),
            ).fetchall()
            books = conn.execute(
                "SELECT * FROM book_snapshots WHERE venue=? AND collected_ms>=? AND collected_ms<? ORDER BY collected_ms",
                (venue, minute_ms, end_ms),
            ).fetchall()
            if not trades and not books:
                continue
            product = str((trades[0] if trades else books[0])["product"])
            notionals = [float(row["notional"]) for row in trades]
            prices = [float(row["price"]) for row in trades]
            side_usable = bool(trades) and all(str(row["aggressor_side"]) in {"BUY", "SELL"} for row in trades)
            buy = sum(float(row["notional"]) for row in trades if row["aggressor_side"] == "BUY") if side_usable else None
            sell = sum(float(row["notional"]) for row in trades if row["aggressor_side"] == "SELL") if side_usable else None
            spreads = [float(row["spread_bps"]) for row in books]
            imbalances = [float(row["top_imbalance"]) for row in books]
            values = (
                venue, product, minute_ms, len(trades), sum(notionals), prices[0] if prices else None,
                prices[-1] if prices else None,
                (prices[-1] / prices[0] - 1.0) * 10_000 if prices else None,
                buy, sell, buy - sell if buy is not None and sell is not None else None,
                int(side_usable), len(books), sum(spreads) / len(spreads) if spreads else None,
                sum(imbalances) / len(imbalances) if imbalances else None,
            )
            conn.execute(
                """INSERT INTO minute_features VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(venue,minute_ms) DO UPDATE SET
                product=excluded.product,trades=excluded.trades,notional=excluded.notional,
                price_first=excluded.price_first,price_last=excluded.price_last,return_bps=excluded.return_bps,
                buy_notional=excluded.buy_notional,sell_notional=excluded.sell_notional,
                delta_notional=excluded.delta_notional,aggressor_side_usable=excluded.aggressor_side_usable,
                book_snapshots=excluded.book_snapshots,avg_spread_bps=excluded.avg_spread_bps,
                avg_top_imbalance=excluded.avg_top_imbalance""",
                values,
            )


def backfill_legacy_minutes(conn: sqlite3.Connection) -> dict[str, Any]:
    if metadata_get(conn, "legacy_feature_backfill_complete") == "true":
        return {"performed": False, "reason": "already_complete", "minutes": 0}
    minutes = {
        int(row[0]) * MINUTE_MS
        for row in conn.execute(
            "SELECT DISTINCT CAST(time_ms / 60000 AS INTEGER) FROM trades "
            "UNION SELECT DISTINCT CAST(collected_ms / 60000 AS INTEGER) FROM book_snapshots"
        )
    }
    rebuild_minutes(conn, minutes)
    metadata_set(conn, "legacy_feature_backfill_complete", "true")
    metadata_set(conn, "legacy_feature_backfill_at", now_iso())
    conn.commit()
    return {"performed": True, "minutes": len(minutes)}


def integrity_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for venue in ("binance", "coinbase"):
        row = conn.execute(
            "SELECT MIN(trade_id),MAX(trade_id),COUNT(*) FROM trades WHERE venue=?", (venue,)
        ).fetchone()
        minimum, maximum, count = row if row else (None, None, 0)
        missing = int(maximum - minimum + 1 - count) if minimum is not None and maximum is not None else 0
        output[venue] = {"first_id": minimum, "last_id": maximum, "rows": count, "missing_ids": missing}
    return output


def database_gap_sentinels(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    sentinels: list[dict[str, Any]] = []
    for venue in ("binance", "coinbase"):
        rows = conn.execute(
            """WITH ordered AS (
                   SELECT trade_id, LAG(trade_id) OVER (ORDER BY trade_id) AS previous_id
                   FROM trades WHERE venue=?
               )
               SELECT previous_id,trade_id FROM ordered
               WHERE previous_id IS NOT NULL AND trade_id>previous_id+1
               ORDER BY previous_id""",
            (venue,),
        ).fetchall()
        for row in rows:
            sentinels.extend(
                [
                    {"venue": venue, "trade_id": str(int(row["previous_id"]))},
                    {"venue": venue, "trade_id": str(int(row["trade_id"]))},
                ]
            )
    return sentinels


def merge_backfill(primary: dict[str, Any], repair: dict[str, Any]) -> dict[str, Any]:
    requested_primary = primary.get("requested_missing_ids") or {}
    requested_repair = repair.get("requested_missing_ids") or {}
    max_pages = int(primary.get("max_pages", 0))
    pages_used = int(primary.get("pages_used", 0)) + int(repair.get("pages_used", 0))
    return {
        "max_pages": max_pages,
        "pages_used": pages_used,
        "requested_missing_ids": {
            venue: int(requested_primary.get(venue, 0)) + int(requested_repair.get(venue, 0))
            for venue in ("binance", "coinbase")
        },
        "rows_recovered": int(primary.get("rows_recovered", 0)) + int(repair.get("rows_recovered", 0)),
        "page_budget_exhausted": pages_used >= max_pages,
        "internal_gap_repair": True,
    }


def export_features(conn: sqlite3.Connection, path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in conn.execute("SELECT * FROM minute_features ORDER BY minute_ms,venue"):
        rows.append(
            {
                "minute": iso_from_ms(int(row["minute_ms"])), "minute_ms": row["minute_ms"],
                "venue": row["venue"], "product": row["product"], "trades": row["trades"],
                "notional": row["notional"], "price_first": row["price_first"] or "",
                "price_last": row["price_last"] or "", "return_bps": row["return_bps"] if row["return_bps"] is not None else "",
                "buy_notional": row["buy_notional"] if row["buy_notional"] is not None else "",
                "sell_notional": row["sell_notional"] if row["sell_notional"] is not None else "",
                "delta_notional": row["delta_notional"] if row["delta_notional"] is not None else "",
                "aggressor_side_usable": str(bool(row["aggressor_side_usable"])).lower(),
                "book_snapshots": row["book_snapshots"],
                "avg_spread_bps": row["avg_spread_bps"] if row["avg_spread_bps"] is not None else "",
                "avg_top_imbalance": row["avg_top_imbalance"] if row["avg_top_imbalance"] is not None else "",
            }
        )
    write_csv(path, FEATURE_FIELDS, rows)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite/WAL forward microstructure collector")
    parser.add_argument("--binance-product", default="BTCUSDT")
    parser.add_argument("--coinbase-product", default="BTC-USD")
    parser.add_argument("--trade-limit", type=int, default=1000)
    parser.add_argument("--retention-hours", type=int, default=168)
    parser.add_argument("--min-research-hours", type=int, default=168)
    parser.add_argument("--max-backfill-pages", type=int, default=20)
    parser.add_argument("--out-dir", default="data/cross_venue_microstructure")
    parser.add_argument("--report-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_DATA_QUALITY_2026-06-24")
    args = parser.parse_args()
    out_dir = resolve_path(args.out_dir)
    db_path = out_dir / "microstructure.sqlite3"
    features_path = out_dir / "minute_features_v2.csv"
    state_path = out_dir / "COLLECTION_STATE.json"
    conn = connect_db(db_path)
    migration = migrate_legacy_csv(conn, out_dir)
    feature_backfill = backfill_legacy_minutes(conn)
    last_ids = last_trade_ids(conn)
    latest, books = fetch_cycle(
        binance_product=args.binance_product, coinbase_product=args.coinbase_product,
        trade_limit=args.trade_limit,
    )
    new_trades, backfill = incremental_rows(
        latest, last_ids, binance_product=args.binance_product,
        coinbase_product=args.coinbase_product, max_backfill_pages=args.max_backfill_pages,
    )
    inserted_trades = insert_trades(conn, new_trades)
    # Do not hold the SQLite writer lock while remote gap backfill is in flight.
    conn.commit()
    remaining_pages = max(0, args.max_backfill_pages - int(backfill.get("pages_used", 0)))
    if remaining_pages and any(row["missing_ids"] for row in integrity_summary(conn).values()):
        gap_sentinels = database_gap_sentinels(conn)
        recovered, repair = backfill_trade_id_gaps(
            gap_sentinels,
            binance_product=args.binance_product,
            coinbase_product=args.coinbase_product,
            max_pages=remaining_pages,
        )
        inserted_trades += insert_trades(conn, recovered)
        new_trades.extend(recovered)
        backfill = merge_backfill(backfill, repair)
    inserted_books = insert_books(conn, books)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    affected = {int(row["time_ms"]) // MINUTE_MS * MINUTE_MS for row in new_trades}
    affected.update(int(row["collected_ms"]) // MINUTE_MS * MINUTE_MS for row in books)
    rebuild_minutes(conn, affected)
    cutoff_ms = now_ms - args.retention_hours * 3_600_000
    conn.execute("DELETE FROM trades WHERE time_ms<?", (cutoff_ms,))
    conn.execute("DELETE FROM book_snapshots WHERE collected_ms<?", (cutoff_ms,))
    conn.execute("DELETE FROM minute_features WHERE minute_ms<?", (cutoff_ms // MINUTE_MS * MINUTE_MS,))
    metadata_set(conn, "schema_version", SCHEMA_VERSION)
    metadata_set(conn, "last_cycle_at", now_iso())
    conn.commit()
    integrity = integrity_summary(conn)
    features = export_features(conn, features_path)
    coverage = coverage_summary([row for row in features if int(row["minute_ms"]) + MINUTE_MS <= now_ms])
    counts = {
        "trades": conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0],
        "binance_trades": conn.execute("SELECT COUNT(*) FROM trades WHERE venue='binance'").fetchone()[0],
        "coinbase_trades": conn.execute("SELECT COUNT(*) FROM trades WHERE venue='coinbase'").fetchone()[0],
        "book_snapshots": conn.execute("SELECT COUNT(*) FROM book_snapshots").fetchone()[0],
        "minute_feature_rows": conn.execute("SELECT COUNT(*) FROM minute_features").fetchone()[0],
    }
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    no_gaps = all(int(row["missing_ids"]) == 0 for row in integrity.values())
    ready = bool(
        coverage["span_hours"] >= args.min_research_hours
        and coverage["both_trade_coverage_pct"] >= 95.0
        and coverage["both_book_coverage_pct"] >= 95.0
        and no_gaps
    )
    classification = (
        "cross_venue_microstructure_ready_for_preregistered_research" if ready
        else "cross_venue_microstructure_forward_collecting" if no_gaps
        else "cross_venue_microstructure_forward_collecting_with_gaps"
    )
    state = {
        "schema_version": SCHEMA_VERSION, "updated_at": now_iso(), "storage": "sqlite_wal",
        "last_trade_ids": {venue: row["last_id"] for venue, row in integrity.items()},
        "legacy_files_preserved": True, "can_trade": False,
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_path = out_dir / "COLLECTION_MANIFEST.json"
    manifest = {
        "schema_version": SCHEMA_VERSION, "generated_at": now_iso(),
        "collection": "BTC_CROSS_VENUE_MICROSTRUCTURE_SQLITE_V2",
        "files": [
            {"path": portable_path(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
            for path in (db_path, features_path, state_path)
        ],
        "legacy_files_preserved_not_authoritative": [portable_path(out_dir / "trades.csv"), portable_path(out_dir / "top_of_book.csv")],
        "can_trade": False,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "generated_at": now_iso(), "classification": classification,
        "collection": manifest["collection"], "storage": {"engine": "sqlite", "journal_mode": "WAL", "full_archive_rewrite_per_cycle": False},
        "current_cycle": {"latest_rows": len(latest), "new_rows": len(new_trades), "inserted_trades": inserted_trades, "inserted_books": inserted_books},
        "migration": migration, "feature_backfill": feature_backfill,
        "archive": {**counts, "retention_hours": args.retention_hours},
        "coverage": coverage, "trade_id_integrity": integrity, "gap_backfill": backfill,
        "research_readiness": {"minimum_hours": args.min_research_hours, "minimum_dual_venue_coverage_pct": 95.0, "requires_zero_unresolved_trade_id_gaps": True, "ready": ready, "new_preregistration_required_before_any_strategy_search": True},
        "outputs": {"database": portable_path(db_path), "minute_features": portable_path(features_path), "collection_state": portable_path(state_path), "collection_manifest": portable_path(manifest_path)},
        "runtime_boundary": {"public_data_only": True, "hypothesis_registered": False, "signals_allowed": False, "orders_allowed": False, "can_trade": False},
        "can_trade": False,
    }
    prefix = resolve_path(args.report_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    prefix.with_suffix(".md").write_text("\n".join([
        "# Cross-Venue Microstructure SQLite Data Quality", "",
        f"- Generated: `{report['generated_at']}`.", f"- Classification: `{classification}`.",
        f"- Storage: `SQLite/WAL`; full archive rewrite per cycle: `false`.",
        f"- Rows trades/books/features: `{counts['trades']}` / `{counts['book_snapshots']}` / `{counts['minute_feature_rows']}`.",
        f"- Coverage span/trades/books: `{coverage['span_hours']}h` / `{coverage['both_trade_coverage_pct']}%` / `{coverage['both_book_coverage_pct']}%`.",
        f"- Missing IDs Binance/Coinbase: `{integrity['binance']['missing_ids']}` / `{integrity['coinbase']['missing_ids']}`.",
        "- Legacy CSV files are preserved but no longer authoritative.", "- No signals, no orders, `can_trade=false`.", "",
    ]), encoding="utf-8")
    print(json.dumps({"classification": classification, "storage": "sqlite_wal", "migration": migration, "archive": counts, "coverage": coverage, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

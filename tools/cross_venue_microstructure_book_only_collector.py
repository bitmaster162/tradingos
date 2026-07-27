#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cross_venue_microstructure_collector import (  # noqa: E402
    BINANCE_BASE,
    COINBASE_BASE,
    MINUTE_MS,
    fetch_json,
    iso_from_ms,
    portable_path,
    summarize_book,
)
from tools.cross_venue_microstructure_sqlite_collector import (  # noqa: E402
    SCHEMA_VERSION,
    connect_db,
    insert_books,
    metadata_set,
    rebuild_minutes,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_books(binance_product: str, coinbase_product: str) -> list[dict[str, Any]]:
    binance_depth_url = f"{BINANCE_BASE}/api/v3/depth?{urlencode({'symbol': binance_product, 'limit': 20})}"
    coinbase_book_url = f"{COINBASE_BASE}/products/{coinbase_product}/book?level=1"
    binance_book = fetch_json(binance_depth_url)
    coinbase_book = fetch_json(coinbase_book_url)
    if not isinstance(binance_book, dict) or not isinstance(coinbase_book, dict):
        raise RuntimeError("invalid_public_book_response")
    collected_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return [
        summarize_book(binance_book, venue="binance", product=binance_product, collected_ms=collected_ms),
        summarize_book(coinbase_book, venue="coinbase", product=coinbase_product, collected_ms=collected_ms),
    ]


def table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def current_minute_book_status(conn: sqlite3.Connection, minute_ms: int) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT venue,book_snapshots,avg_spread_bps,avg_top_imbalance
        FROM minute_features
        WHERE minute_ms=?
        ORDER BY venue
        """,
        (minute_ms,),
    ).fetchall()
    by_venue = {
        str(row["venue"]): {
            "book_snapshots": int(row["book_snapshots"] or 0),
            "avg_spread_bps": row["avg_spread_bps"],
            "avg_top_imbalance": row["avg_top_imbalance"],
        }
        for row in rows
    }
    return {
        "minute": iso_from_ms(minute_ms),
        "minute_ms": minute_ms,
        "venues": by_venue,
        "dual_book_present": all(by_venue.get(venue, {}).get("book_snapshots", 0) > 0 for venue in ("binance", "coinbase")),
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = resolve_path(args.out_dir)
    db_path = out_dir / "microstructure.sqlite3"
    conn = connect_db(db_path)
    before_books = table_count(conn, "book_snapshots")
    books = fetch_books(args.binance_product, args.coinbase_product)
    inserted_books = insert_books(conn, books)
    affected_minutes = {int(row["collected_ms"]) // MINUTE_MS * MINUTE_MS for row in books}
    rebuild_minutes(conn, affected_minutes)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cutoff_ms = now_ms - args.retention_hours * 3_600_000
    conn.execute("DELETE FROM book_snapshots WHERE collected_ms<?", (cutoff_ms,))
    conn.execute("DELETE FROM minute_features WHERE minute_ms<?", (cutoff_ms // MINUTE_MS * MINUTE_MS,))
    metadata_set(conn, "schema_version", SCHEMA_VERSION)
    metadata_set(conn, "last_book_only_cycle_at", now_iso())
    conn.commit()
    after_books = table_count(conn, "book_snapshots")
    statuses = [current_minute_book_status(conn, minute) for minute in sorted(affected_minutes)]
    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    conn.close()

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": "microstructure_book_only_collector_cycle_completed",
        "storage": "sqlite_wal",
        "db_path": portable_path(db_path),
        "current_cycle": {
            "fetched_books": len(books),
            "inserted_books": inserted_books,
            "affected_minutes": len(affected_minutes),
            "book_snapshots_before": before_books,
            "book_snapshots_after": after_books,
        },
        "minute_status": statuses,
        "retention_hours": args.retention_hours,
        "runtime_boundary": {
            "public_data_only": True,
            "book_snapshots_only": True,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    cycle = report.get("current_cycle") if isinstance(report.get("current_cycle"), dict) else {}
    return "\n".join([
        "# Cross-Venue Microstructure Book-Only Collector",
        "",
        f"- Generated: `{report.get('generated_at')}`.",
        f"- Decision: `{report.get('decision')}`.",
        f"- Fetched / inserted books: `{cycle.get('fetched_books')}` / `{cycle.get('inserted_books')}`.",
        f"- Affected minutes: `{cycle.get('affected_minutes')}`.",
        f"- Book snapshots before/after: `{cycle.get('book_snapshots_before')}` / `{cycle.get('book_snapshots_after')}`.",
        f"- Minute status: `{report.get('minute_status')}`.",
        "- Public top-of-book collection only.",
        "- No signals, no paper entries, no orders.",
        "- `can_trade=false`.",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Lightweight public top-of-book collector for cross-venue microstructure coverage.")
    parser.add_argument("--binance-product", default="BTCUSDT")
    parser.add_argument("--coinbase-product", default="BTC-USD")
    parser.add_argument("--retention-hours", type=int, default=168)
    parser.add_argument("--out-dir", default="data/cross_venue_microstructure")
    parser.add_argument("--report-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_BOOK_ONLY_COLLECTOR_2026-07-03")
    args = parser.parse_args()

    if args.retention_hours < 1 or args.retention_hours > 744:
        raise SystemExit("retention-hours must be within [1, 744]")

    report = build_report(args)
    prefix = resolve_path(args.report_prefix)
    write_json(prefix.with_suffix(".json"), report)
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({
        "decision": report["decision"],
        "inserted_books": report["current_cycle"]["inserted_books"],
        "dual_book_present": all(item.get("dual_book_present") for item in report.get("minute_status", [])),
        "out": str(prefix.with_suffix(".json").relative_to(ROOT)),
        "can_trade": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

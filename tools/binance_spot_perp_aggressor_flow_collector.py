#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cross_venue_spot_data_collector import (  # noqa: E402
    fetch_json,
    iso_from_ms,
    now_iso,
    portable_path,
    resolve_path,
)


MINUTE_MS = 60_000
SCHEMA_VERSION = 1
CURSOR_METADATA_PREFIX = "contiguous_cursor_"
MARKET_SPECS = {
    "spot": {
        "base": "https://data-api.binance.vision",
        "path": "/api/v3/aggTrades",
    },
    "perpetual": {
        "base": "https://fapi.binance.com",
        "path": "/fapi/v1/aggTrades",
    },
}
FEATURE_FIELDS = (
    "minute",
    "minute_ms",
    "market",
    "symbol",
    "trades",
    "notional",
    "buy_notional",
    "sell_notional",
    "delta_notional",
    "delta_ratio",
    "price_first",
    "price_last",
    "return_bps",
)


def connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS trades (
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            agg_trade_id INTEGER NOT NULL,
            event_time_ms INTEGER NOT NULL,
            price REAL NOT NULL,
            quantity REAL NOT NULL,
            notional REAL NOT NULL,
            buyer_is_maker INTEGER NOT NULL,
            aggressor_side TEXT NOT NULL,
            PRIMARY KEY (market, agg_trade_id)
        );
        CREATE INDEX IF NOT EXISTS idx_spot_perp_flow_time
            ON trades(event_time_ms);
        CREATE INDEX IF NOT EXISTS idx_spot_perp_flow_market_time
            ON trades(market, event_time_ms);
        CREATE TABLE IF NOT EXISTS minute_features (
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            minute_ms INTEGER NOT NULL,
            trades INTEGER NOT NULL,
            notional REAL NOT NULL,
            buy_notional REAL NOT NULL,
            sell_notional REAL NOT NULL,
            delta_notional REAL NOT NULL,
            delta_ratio REAL NOT NULL,
            price_first REAL NOT NULL,
            price_last REAL NOT NULL,
            return_bps REAL NOT NULL,
            PRIMARY KEY (market, minute_ms)
        );
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    return conn


def build_url(market: str, symbol: str, limit: int, from_id: int | None = None) -> str:
    if market not in MARKET_SPECS:
        raise ValueError(f"unsupported_market:{market}")
    query: dict[str, Any] = {"symbol": symbol, "limit": limit}
    if from_id is not None:
        query["fromId"] = from_id
    spec = MARKET_SPECS[market]
    return f"{spec['base']}{spec['path']}?{urlencode(query)}"


def parse_agg_trade(row: dict[str, Any], *, market: str, symbol: str) -> dict[str, Any]:
    timestamp = int(row["T"])
    price = float(row["p"])
    quantity = float(row["q"])
    buyer_is_maker = bool(row["m"])
    if timestamp <= 0 or price <= 0 or quantity <= 0:
        raise ValueError("invalid_aggregate_trade_values")
    return {
        "market": market,
        "symbol": symbol,
        "agg_trade_id": int(row["a"]),
        "event_time_ms": timestamp,
        "price": price,
        "quantity": quantity,
        "notional": price * quantity,
        "buyer_is_maker": int(buyer_is_maker),
        "aggressor_side": "SELL" if buyer_is_maker else "BUY",
    }


def fetch_page(
    *,
    market: str,
    symbol: str,
    limit: int,
    from_id: int | None = None,
    fetcher: Callable[..., Any] = fetch_json,
) -> list[dict[str, Any]]:
    payload = fetcher(build_url(market, symbol, limit, from_id))
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, list):
        raise RuntimeError(f"invalid_{market}_aggregate_trade_response")
    rows = [
        parse_agg_trade(item, market=market, symbol=symbol)
        for item in payload
        if isinstance(item, dict)
    ]
    return sorted(rows, key=lambda item: int(item["agg_trade_id"]))


def collect_incremental_market(
    *,
    market: str,
    symbol: str,
    limit: int,
    last_id: int | None,
    max_backfill_pages: int,
    fetcher: Callable[..., Any] = fetch_json,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    latest = fetch_page(market=market, symbol=symbol, limit=limit, fetcher=fetcher)
    if not latest:
        raise RuntimeError(f"empty_{market}_aggregate_trade_response")
    latest_first = int(latest[0]["agg_trade_id"])
    latest_last = int(latest[-1]["agg_trade_id"])
    if last_id is None:
        return latest, {
            "market": market,
            "initial_tail_only": True,
            "latest_first_id": latest_first,
            "latest_last_id": latest_last,
            "pages_used": 1,
            "unresolved_ids": 0,
            "page_budget_exhausted": False,
            "contiguous_last_id": latest_last,
        }
    if latest_last <= last_id:
        return [], {
            "market": market,
            "initial_tail_only": False,
            "latest_first_id": latest_first,
            "latest_last_id": latest_last,
            "pages_used": 1,
            "unresolved_ids": 0,
            "page_budget_exhausted": False,
            "contiguous_last_id": last_id,
        }

    collected: dict[int, dict[str, Any]] = {
        int(row["agg_trade_id"]): row
        for row in latest
        if int(row["agg_trade_id"]) > last_id
    }
    backfill_pages = 0
    next_id = last_id + 1
    while next_id < latest_first and backfill_pages < max_backfill_pages:
        page = fetch_page(
            market=market,
            symbol=symbol,
            limit=limit,
            from_id=next_id,
            fetcher=fetcher,
        )
        backfill_pages += 1
        if not page:
            break
        page_max = max(int(row["agg_trade_id"]) for row in page)
        for row in page:
            trade_id = int(row["agg_trade_id"])
            if last_id < trade_id <= latest_last:
                collected[trade_id] = row
        if page_max < next_id:
            break
        next_id = page_max + 1

    contiguous_last_id = last_id
    for trade_id in sorted(collected):
        if trade_id == contiguous_last_id + 1:
            contiguous_last_id = trade_id
        elif trade_id > contiguous_last_id + 1:
            break
    unresolved = max(0, latest_last - contiguous_last_id)
    return [collected[key] for key in sorted(collected)], {
        "market": market,
        "initial_tail_only": False,
        "latest_first_id": latest_first,
        "latest_last_id": latest_last,
        "pages_used": 1 + backfill_pages,
        "unresolved_ids": unresolved,
        "page_budget_exhausted": unresolved > 0 and backfill_pages >= max_backfill_pages,
        "contiguous_last_id": contiguous_last_id,
    }


def last_trade_ids(conn: sqlite3.Connection) -> dict[str, int | None]:
    output: dict[str, int | None] = {}
    for market in MARKET_SPECS:
        row = conn.execute(
            "SELECT MAX(agg_trade_id) FROM trades WHERE market=?", (market,)
        ).fetchone()
        output[market] = int(row[0]) if row and row[0] is not None else None
    return output


def derive_contiguous_cursor(
    conn: sqlite3.Connection,
    market: str,
    *,
    start_at: int | None = None,
) -> int | None:
    """Return the highest trade ID before the first internal gap.

    ``MAX(id)`` is not a safe continuation cursor when a bounded backfill also
    stores the fresh tail. Starting from the previous cursor keeps this query
    bounded during normal collection while the no-cursor path repairs legacy
    databases that already contain an internal gap.
    """
    bounds = conn.execute(
        "SELECT MIN(agg_trade_id),MAX(agg_trade_id) FROM trades WHERE market=?",
        (market,),
    ).fetchone()
    if not bounds or bounds[0] is None or bounds[1] is None:
        return None
    minimum = int(bounds[0])
    maximum = int(bounds[1])
    lower_bound = minimum if start_at is None else max(minimum, int(start_at))
    if lower_bound >= maximum:
        return maximum
    row = conn.execute(
        """
        SELECT current.agg_trade_id
        FROM trades AS current
        WHERE current.market=?
          AND current.agg_trade_id>=?
          AND current.agg_trade_id<?
          AND NOT EXISTS (
              SELECT 1
              FROM trades AS following
              WHERE following.market=current.market
                AND following.agg_trade_id=current.agg_trade_id+1
          )
        ORDER BY current.agg_trade_id
        LIMIT 1
        """,
        (market, lower_bound, maximum),
    ).fetchone()
    return int(row[0]) if row else maximum


def load_collection_cursors(conn: sqlite3.Connection) -> dict[str, int | None]:
    cursors: dict[str, int | None] = {}
    for market in MARKET_SPECS:
        key = f"{CURSOR_METADATA_PREFIX}{market}"
        row = conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        cursor: int | None = None
        if row:
            try:
                candidate = int(row[0])
                present = conn.execute(
                    "SELECT 1 FROM trades WHERE market=? AND agg_trade_id=?",
                    (market, candidate),
                ).fetchone()
                if present:
                    cursor = candidate
            except (TypeError, ValueError):
                cursor = None
        if cursor is None:
            cursor = derive_contiguous_cursor(conn, market)
        cursors[market] = cursor
    return cursors


def store_collection_cursor(
    conn: sqlite3.Connection,
    market: str,
    cursor: int | None,
) -> None:
    if cursor is None:
        return
    conn.execute(
        "INSERT INTO metadata(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (f"{CURSOR_METADATA_PREFIX}{market}", str(int(cursor))),
    )


def insert_trades(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO trades VALUES(?,?,?,?,?,?,?,?,?)",
        (
            (
                str(row["market"]),
                str(row["symbol"]),
                int(row["agg_trade_id"]),
                int(row["event_time_ms"]),
                float(row["price"]),
                float(row["quantity"]),
                float(row["notional"]),
                int(row["buyer_is_maker"]),
                str(row["aggressor_side"]),
            )
            for row in rows
        ),
    )
    return conn.total_changes - before


def rebuild_minutes(conn: sqlite3.Connection, minutes: set[int]) -> None:
    for minute_ms in sorted(minutes):
        end_ms = minute_ms + MINUTE_MS
        for market in MARKET_SPECS:
            rows = conn.execute(
                "SELECT * FROM trades WHERE market=? AND event_time_ms>=? AND event_time_ms<? "
                "ORDER BY event_time_ms,agg_trade_id",
                (market, minute_ms, end_ms),
            ).fetchall()
            if not rows:
                continue
            symbol = str(rows[0]["symbol"])
            buy = sum(float(row["notional"]) for row in rows if row["aggressor_side"] == "BUY")
            sell = sum(float(row["notional"]) for row in rows if row["aggressor_side"] == "SELL")
            total = buy + sell
            prices = [float(row["price"]) for row in rows]
            values = (
                market,
                symbol,
                minute_ms,
                len(rows),
                total,
                buy,
                sell,
                buy - sell,
                (buy - sell) / total if total > 0 else 0.0,
                prices[0],
                prices[-1],
                (prices[-1] / prices[0] - 1.0) * 10_000,
            )
            conn.execute(
                """INSERT INTO minute_features VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(market,minute_ms) DO UPDATE SET
                symbol=excluded.symbol,trades=excluded.trades,notional=excluded.notional,
                buy_notional=excluded.buy_notional,sell_notional=excluded.sell_notional,
                delta_notional=excluded.delta_notional,delta_ratio=excluded.delta_ratio,
                price_first=excluded.price_first,price_last=excluded.price_last,
                return_bps=excluded.return_bps""",
                values,
            )


def integrity_summary(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for market in MARKET_SPECS:
        row = conn.execute(
            "SELECT MIN(agg_trade_id),MAX(agg_trade_id),COUNT(*),MIN(event_time_ms),MAX(event_time_ms) "
            "FROM trades WHERE market=?",
            (market,),
        ).fetchone()
        minimum, maximum, count, first_ms, last_ms = row if row else (None, None, 0, None, None)
        missing = int(maximum - minimum + 1 - count) if minimum is not None and maximum is not None else 0
        output[market] = {
            "first_id": minimum,
            "last_id": maximum,
            "rows": count,
            "missing_ids": missing,
            "first_event_time": iso_from_ms(first_ms) if first_ms is not None else None,
            "last_event_time": iso_from_ms(last_ms) if last_ms is not None else None,
        }
    return output


def coverage_summary(conn: sqlite3.Connection, *, now_ms: int) -> dict[str, Any]:
    complete_cutoff = (now_ms // MINUTE_MS) * MINUTE_MS
    minute_sets: dict[str, set[int]] = {}
    freshness: dict[str, float | None] = {}
    for market in MARKET_SPECS:
        minute_sets[market] = {
            int(row[0])
            for row in conn.execute(
                "SELECT minute_ms FROM minute_features WHERE market=? AND minute_ms<? AND trades>0",
                (market, complete_cutoff),
            )
        }
        row = conn.execute(
            "SELECT MAX(event_time_ms) FROM trades WHERE market=?", (market,)
        ).fetchone()
        freshness[market] = (
            round(max(0.0, (now_ms - int(row[0])) / 1000.0), 3)
            if row and row[0] is not None
            else None
        )
    common = minute_sets["spot"] & minute_sets["perpetual"]
    both_markets_present = all(bool(minute_sets[market]) for market in MARKET_SPECS)
    overlap_start = (
        max(min(minute_sets[market]) for market in MARKET_SPECS)
        if both_markets_present
        else None
    )
    overlap_end = (
        min(max(minute_sets[market]) for market in MARKET_SPECS)
        if both_markets_present
        else None
    )
    expected = (
        int((overlap_end - overlap_start) // MINUTE_MS + 1)
        if overlap_start is not None and overlap_end is not None and overlap_end >= overlap_start
        else 0
    )
    common_in_window = (
        sum(1 for minute in common if overlap_start <= minute <= overlap_end)
        if expected and overlap_start is not None and overlap_end is not None
        else 0
    )
    coverage_pct = round(common_in_window / expected * 100.0, 6) if expected else 0.0
    invalid_sides = int(
        conn.execute(
            "SELECT COUNT(*) FROM trades WHERE aggressor_side NOT IN ('BUY','SELL')"
        ).fetchone()[0]
    )
    return {
        "complete_spot_minutes": len(minute_sets["spot"]),
        "complete_perpetual_minutes": len(minute_sets["perpetual"]),
        "common_complete_minutes": common_in_window,
        "expected_overlap_minutes": expected,
        "span_hours": round(expected / 60.0, 6),
        "dual_market_coverage_pct": coverage_pct,
        "overlap_start": iso_from_ms(overlap_start) if overlap_start is not None else None,
        "overlap_end": iso_from_ms(overlap_end) if overlap_end is not None else None,
        "fresh_lag_seconds": freshness,
        "invalid_aggressor_side_rows": invalid_sides,
        "aggressor_side_semantics_valid": invalid_sides == 0,
    }


def export_features(conn: sqlite3.Connection, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FEATURE_FIELDS)
        writer.writeheader()
        for row in conn.execute("SELECT * FROM minute_features ORDER BY minute_ms,market"):
            writer.writerow(
                {
                    "minute": iso_from_ms(int(row["minute_ms"])),
                    **{field: row[field] for field in FEATURE_FIELDS if field != "minute"},
                }
            )
            count += 1
    return count


def evaluate_readiness(
    *,
    coverage: dict[str, Any],
    integrity: dict[str, dict[str, Any]],
    fetch_errors: dict[str, str],
    min_research_hours: float,
    min_coverage_pct: float,
    max_fresh_lag_seconds: float,
) -> tuple[str, bool, list[str]]:
    blockers: list[str] = []
    if fetch_errors:
        blockers.append("public_input_fetch_failed")
    if float(coverage["span_hours"]) < min_research_hours:
        blockers.append("minimum_forward_span_not_reached")
    if float(coverage["dual_market_coverage_pct"]) < min_coverage_pct:
        blockers.append("dual_market_minute_coverage_below_gate")
    if any(int(item["missing_ids"]) > 0 for item in integrity.values()):
        blockers.append("aggregate_trade_id_gaps_present")
    if not coverage["aggressor_side_semantics_valid"]:
        blockers.append("aggressor_side_semantics_invalid")
    lags = coverage.get("fresh_lag_seconds") or {}
    if any(lags.get(market) is None or float(lags[market]) > max_fresh_lag_seconds for market in MARKET_SPECS):
        blockers.append("market_input_stale")
    ready = not blockers
    if fetch_errors:
        classification = "binance_spot_perp_aggressor_flow_input_failure"
    elif ready:
        classification = "binance_spot_perp_aggressor_flow_ready_for_seal_review"
    elif "aggregate_trade_id_gaps_present" in blockers:
        classification = "binance_spot_perp_aggressor_flow_collecting_with_gaps"
    else:
        classification = "binance_spot_perp_aggressor_flow_forward_collecting"
    return classification, ready, blockers


def render_markdown(report: dict[str, Any]) -> str:
    coverage = report["coverage"]
    return "\n".join(
        [
            "# Binance Spot/Perpetual Aggressor Flow Data Quality",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Classification: `{report['classification']}`",
            f"- Forward span: `{coverage['span_hours']}h`",
            f"- Dual-market minute coverage: `{coverage['dual_market_coverage_pct']}%`",
            f"- Spot/perpetual complete minutes: `{coverage['complete_spot_minutes']}` / `{coverage['complete_perpetual_minutes']}`",
            f"- Spot/perpetual missing aggregate IDs: `{report['integrity']['spot']['missing_ids']}` / `{report['integrity']['perpetual']['missing_ids']}`",
            f"- Research data gate ready: `{str(report['research_readiness']['ready']).lower()}`",
            f"- Blockers: `{', '.join(report['research_readiness']['blockers']) or 'none'}`",
            "",
            "## Boundary",
            "",
            "- Public market data only; no credentials.",
            "- This collector does not define or test a trading strategy.",
            "- No signals, paper entries, Telegram messages or orders.",
            "- A separate preregistration and sealed snapshot are required before research.",
            "- `can_trade=false`.",
            "",
        ]
    )


def run_cycle(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "flow.sqlite3"
    features_path = out_dir / "minute_features.csv"
    state_path = out_dir / "COLLECTION_STATE.json"
    conn = connect_db(db_path)
    collection_cursors = load_collection_cursors(conn)
    cycle: dict[str, Any] = {}
    fetch_errors: dict[str, str] = {}
    affected: set[int] = set()
    for market in MARKET_SPECS:
        cursor_before = collection_cursors[market]
        try:
            rows, fetch_meta = collect_incremental_market(
                market=market,
                symbol=args.symbol,
                limit=args.trade_limit,
                last_id=cursor_before,
                max_backfill_pages=args.max_backfill_pages,
            )
            inserted = insert_trades(conn, rows)
            affected.update(int(row["event_time_ms"]) // MINUTE_MS * MINUTE_MS for row in rows)
            cursor_after = derive_contiguous_cursor(conn, market, start_at=cursor_before)
            store_collection_cursor(conn, market, cursor_after)
            collection_cursors[market] = cursor_after
            cycle[market] = {
                **fetch_meta,
                "cursor_before": cursor_before,
                "cursor_after": cursor_after,
                "cursor_advanced_ids": (
                    max(0, int(cursor_after) - int(cursor_before))
                    if cursor_before is not None and cursor_after is not None
                    else 0
                ),
                "received_rows": len(rows),
                "inserted_rows": inserted,
            }
        except Exception as exc:  # public network boundary; report remains fail-closed
            fetch_errors[market] = f"{type(exc).__name__}:{exc}"
            cycle[market] = {
                "cursor_before": cursor_before,
                "cursor_after": cursor_before,
                "cursor_advanced_ids": 0,
                "received_rows": 0,
                "inserted_rows": 0,
                "error": fetch_errors[market],
            }
    rebuild_minutes(conn, affected)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cutoff_ms = now_ms - int(args.retention_hours * 3_600_000)
    conn.execute("DELETE FROM trades WHERE event_time_ms<?", (cutoff_ms,))
    conn.execute(
        "DELETE FROM minute_features WHERE minute_ms<?",
        ((cutoff_ms // MINUTE_MS) * MINUTE_MS,),
    )
    conn.execute(
        "INSERT INTO metadata(key,value) VALUES('schema_version',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.execute(
        "INSERT INTO metadata(key,value) VALUES('last_cycle_at',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (now_iso(),),
    )
    conn.commit()
    integrity = integrity_summary(conn)
    for market in MARKET_SPECS:
        cycle[market]["remaining_internal_missing_ids"] = int(
            integrity[market]["missing_ids"]
        )
        cycle[market]["gap_repair_complete"] = (
            int(integrity[market]["missing_ids"]) == 0
        )
    coverage = coverage_summary(conn, now_ms=now_ms)
    feature_rows = export_features(conn, features_path)
    classification, ready, blockers = evaluate_readiness(
        coverage=coverage,
        integrity=integrity,
        fetch_errors=fetch_errors,
        min_research_hours=args.min_research_hours,
        min_coverage_pct=args.min_dual_market_coverage_pct,
        max_fresh_lag_seconds=args.max_fresh_lag_seconds,
    )
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    state = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": now_iso(),
        "classification": classification,
        "last_trade_ids": {market: item["last_id"] for market, item in integrity.items()},
        "contiguous_trade_ids": collection_cursors,
        "collector_only": True,
        "credentials_allowed": False,
        "signals_allowed": False,
        "orders_allowed": False,
        "can_trade": False,
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "tool": "tools/binance_spot_perp_aggressor_flow_collector.py",
        "classification": classification,
        "collection": "BINANCE_SPOT_PERP_AGGRESSOR_FLOW_FORWARD_V1",
        "contract": args.contract,
        "current_cycle": cycle,
        "fetch_errors": fetch_errors,
        "archive": {
            "database": portable_path(db_path),
            "database_bytes": db_path.stat().st_size,
            "minute_features": portable_path(features_path),
            "minute_feature_rows": feature_rows,
            "retention_hours": args.retention_hours,
        },
        "coverage": coverage,
        "integrity": integrity,
        "research_readiness": {
            "ready": ready,
            "blockers": blockers,
            "minimum_forward_hours": args.min_research_hours,
            "minimum_dual_market_coverage_pct": args.min_dual_market_coverage_pct,
            "maximum_fresh_lag_seconds": args.max_fresh_lag_seconds,
            "requires_zero_internal_trade_id_gaps": True,
            "requires_sealed_snapshot": True,
            "requires_new_preregistration_before_strategy_search": True,
        },
        "runtime_boundary": {
            "public_data_only": True,
            "credentials_allowed": False,
            "hypothesis_registered": False,
            "strategy_search_allowed": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "telegram_send_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    prefix = resolve_path(args.report_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "classification": classification,
                "cycle": cycle,
                "coverage": coverage,
                "research_ready": ready,
                "report": portable_path(prefix.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return report, 1 if fetch_errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect Binance spot/perpetual signed aggressor flow into SQLite/WAL."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--trade-limit", type=int, default=1000)
    parser.add_argument("--max-backfill-pages", type=int, default=20)
    parser.add_argument("--retention-hours", type=float, default=336.0)
    parser.add_argument("--min-research-hours", type=float, default=168.0)
    parser.add_argument("--min-dual-market-coverage-pct", type=float, default=95.0)
    parser.add_argument("--max-fresh-lag-seconds", type=float, default=120.0)
    parser.add_argument("--out-dir", default="data/binance_spot_perp_aggressor_flow")
    parser.add_argument(
        "--report-prefix",
        default="docs/BINANCE_SPOT_PERP_AGGRESSOR_FLOW_DATA_QUALITY_2026-07-15",
    )
    parser.add_argument(
        "--contract",
        default="configs/BINANCE_SPOT_PERP_AGGRESSOR_FLOW_COLLECTION_CONTRACT_2026-07-15.json",
    )
    args = parser.parse_args()
    if not 1 <= args.trade_limit <= 1000:
        parser.error("--trade-limit must be between 1 and 1000")
    if args.max_backfill_pages < 0:
        parser.error("--max-backfill-pages must be non-negative")
    if args.retention_hours < args.min_research_hours:
        parser.error("--retention-hours must be at least --min-research-hours")
    _, exit_code = run_cycle(args)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

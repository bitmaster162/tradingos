#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cross_venue_spot_data_collector import (  # noqa: E402
    BINANCE_BASE,
    COINBASE_BASE,
    fetch_json,
    iso_from_ms,
    now_iso,
    portable_path,
    resolve_path,
)


MINUTE_MS = 60_000
TRADE_FIELDS = (
    "time",
    "time_ms",
    "venue",
    "product",
    "trade_id",
    "price",
    "quantity",
    "notional",
    "reported_side",
    "aggressor_side",
    "side_semantics",
)
BOOK_FIELDS = (
    "collected_at",
    "collected_ms",
    "venue",
    "product",
    "bid",
    "ask",
    "bid_size",
    "ask_size",
    "mid",
    "spread_bps",
    "top_imbalance",
    "sequence",
)
FEATURE_FIELDS = (
    "minute",
    "minute_ms",
    "venue",
    "product",
    "trades",
    "notional",
    "price_first",
    "price_last",
    "return_bps",
    "buy_notional",
    "sell_notional",
    "delta_notional",
    "aggressor_side_usable",
    "book_snapshots",
    "avg_spread_bps",
    "avg_top_imbalance",
)


def parse_time_ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def parse_binance_trade(row: dict[str, Any], product: str = "BTCUSDT") -> dict[str, Any]:
    timestamp = int(row["T"])
    price = float(row["p"])
    quantity = float(row["q"])
    buyer_is_maker = bool(row["m"])
    return {
        "time": iso_from_ms(timestamp),
        "time_ms": timestamp,
        "venue": "binance",
        "product": product,
        "trade_id": str(row["a"]),
        "price": price,
        "quantity": quantity,
        "notional": price * quantity,
        "reported_side": "BUY_MAKER" if buyer_is_maker else "SELL_MAKER",
        "aggressor_side": "SELL" if buyer_is_maker else "BUY",
        "side_semantics": "aggressor_from_buyer_is_maker",
    }


def parse_coinbase_trade(row: dict[str, Any], product: str = "BTC-USD") -> dict[str, Any]:
    timestamp = parse_time_ms(str(row["time"]))
    price = float(row["price"])
    quantity = float(row["size"])
    return {
        "time": iso_from_ms(timestamp),
        "time_ms": timestamp,
        "venue": "coinbase",
        "product": product,
        "trade_id": str(row["trade_id"]),
        "price": price,
        "quantity": quantity,
        "notional": price * quantity,
        "reported_side": str(row.get("side") or "").upper(),
        "aggressor_side": "",
        "side_semantics": "venue_reported_side_unverified_do_not_use_as_aggressor",
    }


def summarize_book(
    payload: dict[str, Any], *, venue: str, product: str, collected_ms: int
) -> dict[str, Any]:
    bids = payload.get("bids")
    asks = payload.get("asks")
    if not isinstance(bids, list) or not bids or not isinstance(asks, list) or not asks:
        raise ValueError(f"invalid_{venue}_book_shape")
    bid = float(bids[0][0])
    ask = float(asks[0][0])
    bid_size = float(bids[0][1])
    ask_size = float(asks[0][1])
    if bid <= 0 or ask <= bid or bid_size < 0 or ask_size < 0:
        raise ValueError(f"invalid_{venue}_top_of_book")
    mid = (bid + ask) / 2.0
    size_total = bid_size + ask_size
    sequence = payload.get("lastUpdateId") if venue == "binance" else payload.get("sequence")
    return {
        "collected_at": iso_from_ms(collected_ms),
        "collected_ms": collected_ms,
        "venue": venue,
        "product": product,
        "bid": bid,
        "ask": ask,
        "bid_size": bid_size,
        "ask_size": ask_size,
        "mid": mid,
        "spread_bps": (ask - bid) / mid * 10_000,
        "top_imbalance": (bid_size - ask_size) / size_total if size_total > 0 else 0.0,
        "sequence": "" if sequence is None else str(sequence),
    }


def fetch_cycle(
    *,
    binance_product: str,
    coinbase_product: str,
    trade_limit: int,
    fetcher: Callable[..., Any] = fetch_json,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    binance_depth_url = f"{BINANCE_BASE}/api/v3/depth?{urlencode({'symbol': binance_product, 'limit': 20})}"
    binance_trade_url = f"{BINANCE_BASE}/api/v3/aggTrades?{urlencode({'symbol': binance_product, 'limit': trade_limit})}"
    coinbase_book_url = f"{COINBASE_BASE}/products/{coinbase_product}/book?level=1"
    coinbase_trade_url = f"{COINBASE_BASE}/products/{coinbase_product}/trades?{urlencode({'limit': trade_limit})}"
    binance_book = fetcher(binance_depth_url)
    binance_trades = fetcher(binance_trade_url)
    coinbase_book = fetcher(coinbase_book_url)
    coinbase_trades = fetcher(coinbase_trade_url)
    if not isinstance(binance_book, dict) or not isinstance(coinbase_book, dict):
        raise RuntimeError("invalid_public_book_response")
    if not isinstance(binance_trades, list) or not isinstance(coinbase_trades, list):
        raise RuntimeError("invalid_public_trade_response")
    collected_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    books = [
        summarize_book(binance_book, venue="binance", product=binance_product, collected_ms=collected_ms),
        summarize_book(coinbase_book, venue="coinbase", product=coinbase_product, collected_ms=collected_ms),
    ]
    trades = [
        *(parse_binance_trade(row, binance_product) for row in binance_trades if isinstance(row, dict)),
        *(parse_coinbase_trade(row, coinbase_product) for row in coinbase_trades if isinstance(row, dict)),
    ]
    return trades, books


def read_csv(path: Path, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if all(field in row for field in fields):
                rows.append(dict(row))
    return rows


def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def merge_trades(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]], *, cutoff_ms: int
) -> tuple[list[dict[str, Any]], int]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    duplicates = 0
    for row in existing + incoming:
        try:
            timestamp = int(row["time_ms"])
            key = (str(row["venue"]), str(row["trade_id"]))
        except (KeyError, TypeError, ValueError):
            continue
        if timestamp < cutoff_ms:
            continue
        if key in by_key:
            duplicates += 1
        by_key[key] = row
    return sorted(by_key.values(), key=lambda row: (int(row["time_ms"]), str(row["venue"]), str(row["trade_id"]))), duplicates


def merge_books(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]], *, cutoff_ms: int
) -> tuple[list[dict[str, Any]], int]:
    by_key: dict[tuple[str, int], dict[str, Any]] = {}
    duplicates = 0
    for row in existing + incoming:
        try:
            timestamp = int(row["collected_ms"])
            key = (str(row["venue"]), timestamp)
        except (KeyError, TypeError, ValueError):
            continue
        if timestamp < cutoff_ms:
            continue
        if key in by_key:
            duplicates += 1
        by_key[key] = row
    return sorted(by_key.values(), key=lambda row: (int(row["collected_ms"]), str(row["venue"]))), duplicates


def trade_id_gaps(trades: list[dict[str, Any]], venue: str) -> list[tuple[int, int, int]]:
    ids = sorted({int(row["trade_id"]) for row in trades if row.get("venue") == venue})
    return [(left, right, right - left - 1) for left, right in zip(ids, ids[1:]) if right - left > 1]


def backfill_trade_id_gaps(
    trades: list[dict[str, Any]],
    *,
    binance_product: str,
    coinbase_product: str,
    max_pages: int,
    fetcher: Callable[..., Any] = fetch_json,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    added: list[dict[str, Any]] = []
    pages = 0
    requested_missing = {venue: sum(gap[2] for gap in trade_id_gaps(trades, venue)) for venue in ("binance", "coinbase")}
    for left, right, _ in trade_id_gaps(trades, "binance"):
        cursor = left + 1
        while cursor < right and pages < max_pages:
            limit = min(1000, right - cursor)
            query = urlencode({"symbol": binance_product, "fromId": cursor, "limit": limit})
            payload = fetcher(f"{BINANCE_BASE}/api/v3/aggTrades?{query}")
            pages += 1
            if not isinstance(payload, list) or not payload:
                break
            parsed = [parse_binance_trade(row, binance_product) for row in payload if isinstance(row, dict)]
            bounded = [row for row in parsed if cursor <= int(row["trade_id"]) < right]
            if not bounded:
                break
            added.extend(bounded)
            next_cursor = max(int(row["trade_id"]) for row in bounded) + 1
            if next_cursor <= cursor:
                break
            cursor = next_cursor
    for left, right, _ in trade_id_gaps(trades, "coinbase"):
        cursor_after = right
        while cursor_after > left + 1 and pages < max_pages:
            limit = min(1000, cursor_after - left - 1)
            query = urlencode({"after": cursor_after, "limit": limit})
            payload = fetcher(f"{COINBASE_BASE}/products/{coinbase_product}/trades?{query}")
            pages += 1
            if not isinstance(payload, list) or not payload:
                break
            parsed = [parse_coinbase_trade(row, coinbase_product) for row in payload if isinstance(row, dict)]
            bounded = [row for row in parsed if left < int(row["trade_id"]) < right]
            if not bounded:
                break
            added.extend(bounded)
            next_cursor = min(int(row["trade_id"]) for row in bounded)
            if next_cursor >= cursor_after:
                break
            cursor_after = next_cursor
    return added, {
        "max_pages": max_pages,
        "pages_used": pages,
        "requested_missing_ids": requested_missing,
        "rows_recovered": len(added),
        "page_budget_exhausted": pages >= max_pages,
    }


def minute_features(
    trades: list[dict[str, Any]], books: list[dict[str, Any]], *, completed_before_ms: int
) -> list[dict[str, Any]]:
    trade_groups: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    book_groups: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in trades:
        minute_ms = int(row["time_ms"]) // MINUTE_MS * MINUTE_MS
        if minute_ms + MINUTE_MS <= completed_before_ms:
            trade_groups[(str(row["venue"]), str(row["product"]), minute_ms)].append(row)
    for row in books:
        minute_ms = int(row["collected_ms"]) // MINUTE_MS * MINUTE_MS
        if minute_ms + MINUTE_MS <= completed_before_ms:
            book_groups[(str(row["venue"]), str(row["product"]), minute_ms)].append(row)
    keys = sorted(set(trade_groups) | set(book_groups), key=lambda key: (key[2], key[0], key[1]))
    output: list[dict[str, Any]] = []
    for venue, product, minute_ms in keys:
        venue_trades = sorted(trade_groups.get((venue, product, minute_ms), []), key=lambda row: int(row["time_ms"]))
        venue_books = book_groups.get((venue, product, minute_ms), [])
        prices = [float(row["price"]) for row in venue_trades]
        notionals = [float(row["notional"]) for row in venue_trades]
        side_usable = bool(venue_trades) and all(
            str(row.get("aggressor_side")) in {"BUY", "SELL"} for row in venue_trades
        )
        buy_notional = sum(
            float(row["notional"]) for row in venue_trades if row.get("aggressor_side") == "BUY"
        ) if side_usable else None
        sell_notional = sum(
            float(row["notional"]) for row in venue_trades if row.get("aggressor_side") == "SELL"
        ) if side_usable else None
        spreads = [float(row["spread_bps"]) for row in venue_books]
        imbalances = [float(row["top_imbalance"]) for row in venue_books]
        output.append(
            {
                "minute": iso_from_ms(minute_ms),
                "minute_ms": minute_ms,
                "venue": venue,
                "product": product,
                "trades": len(venue_trades),
                "notional": round(sum(notionals), 8),
                "price_first": prices[0] if prices else "",
                "price_last": prices[-1] if prices else "",
                "return_bps": round((prices[-1] / prices[0] - 1.0) * 10_000, 8) if prices else "",
                "buy_notional": round(float(buy_notional), 8) if buy_notional is not None else "",
                "sell_notional": round(float(sell_notional), 8) if sell_notional is not None else "",
                "delta_notional": round(float(buy_notional - sell_notional), 8)
                if buy_notional is not None and sell_notional is not None
                else "",
                "aggressor_side_usable": str(side_usable).lower(),
                "book_snapshots": len(venue_books),
                "avg_spread_bps": round(sum(spreads) / len(spreads), 8) if spreads else "",
                "avg_top_imbalance": round(sum(imbalances) / len(imbalances), 8) if imbalances else "",
            }
        )
    return output


def coverage_summary(features: list[dict[str, Any]]) -> dict[str, Any]:
    if not features:
        return {"minutes": 0, "span_hours": 0.0, "both_trade_coverage_pct": 0.0, "both_book_coverage_pct": 0.0}
    by_minute: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in features:
        by_minute[int(row["minute_ms"])][str(row["venue"])] = row
    minute_ids = sorted(by_minute)
    expected = max(1, (minute_ids[-1] - minute_ids[0]) // MINUTE_MS + 1)
    both_trade = sum(
        1
        for rows in by_minute.values()
        if int(rows.get("binance", {}).get("trades", 0)) > 0
        and int(rows.get("coinbase", {}).get("trades", 0)) > 0
    )
    both_book = sum(
        1
        for rows in by_minute.values()
        if int(rows.get("binance", {}).get("book_snapshots", 0)) > 0
        and int(rows.get("coinbase", {}).get("book_snapshots", 0)) > 0
    )
    return {
        "minutes": len(minute_ids),
        "expected_minutes": expected,
        "span_hours": round(expected / 60.0, 3),
        "both_trade_minutes": both_trade,
        "both_trade_coverage_pct": round(both_trade / expected * 100.0, 6),
        "both_book_minutes": both_book,
        "both_book_coverage_pct": round(both_book / expected * 100.0, 6),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_markdown(report: dict[str, Any]) -> str:
    coverage = report["coverage"]
    integrity = report["trade_id_integrity"]
    backfill = report["gap_backfill"]
    return "\n".join(
        [
            "# Cross-Venue Microstructure Data Quality",
            "",
            f"Generated: `{report['generated_at']}`",
            "",
            f"- Classification: `{report['classification']}`.",
            f"- Trade archive rows Binance/Coinbase: `{report['archive']['binance_trades']}` / `{report['archive']['coinbase_trades']}`.",
            f"- Top-of-book snapshots: `{report['archive']['book_snapshots']}`.",
            f"- Feature span: `{coverage['span_hours']}` hours; dual-trade coverage `{coverage['both_trade_coverage_pct']}%`; dual-book coverage `{coverage['both_book_coverage_pct']}%`.",
            f"- Remaining trade-ID gaps Binance/Coinbase: `{integrity['binance']['missing_ids']}` / `{integrity['coinbase']['missing_ids']}`; recovered this cycle `{backfill['rows_recovered']}` rows in `{backfill['pages_used']}` pages.",
            "- Coinbase reported side is retained but is not used as aggressor delta until its semantics are separately verified.",
            "- This collector creates data only. It does not register a hypothesis, emit a signal or send an order.",
            "- `can_trade=false`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Public forward collector for Binance/Coinbase trades and top of book")
    parser.add_argument("--binance-product", default="BTCUSDT")
    parser.add_argument("--coinbase-product", default="BTC-USD")
    parser.add_argument("--trade-limit", type=int, default=1000)
    parser.add_argument("--retention-hours", type=int, default=168)
    parser.add_argument("--min-research-hours", type=int, default=168)
    parser.add_argument("--max-backfill-pages", type=int, default=20)
    parser.add_argument("--out-dir", default="data/cross_venue_microstructure")
    parser.add_argument("--report-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_DATA_QUALITY_2026-06-24")
    args = parser.parse_args()
    if not 1 <= args.trade_limit <= 1000:
        raise SystemExit("trade_limit_must_be_1_to_1000")
    if not 1 <= args.retention_hours <= 744:
        raise SystemExit("retention_hours_must_be_1_to_744")
    if not 0 <= args.max_backfill_pages <= 100:
        raise SystemExit("max_backfill_pages_must_be_0_to_100")

    incoming_trades, incoming_books = fetch_cycle(
        binance_product=args.binance_product,
        coinbase_product=args.coinbase_product,
        trade_limit=args.trade_limit,
    )
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cutoff_ms = now_ms - args.retention_hours * 3_600_000
    out_dir = resolve_path(args.out_dir)
    trades_path = out_dir / "trades.csv"
    books_path = out_dir / "top_of_book.csv"
    features_path = out_dir / "minute_features.csv"
    existing_trades = read_csv(trades_path, TRADE_FIELDS)
    pre_backfill_trades, _ = merge_trades(existing_trades, incoming_trades, cutoff_ms=cutoff_ms)
    recovered_trades, backfill = backfill_trade_id_gaps(
        pre_backfill_trades,
        binance_product=args.binance_product,
        coinbase_product=args.coinbase_product,
        max_pages=args.max_backfill_pages,
    )
    trades, trade_duplicates = merge_trades(
        existing_trades, incoming_trades + recovered_trades, cutoff_ms=cutoff_ms
    )
    books, book_duplicates = merge_books(
        read_csv(books_path, BOOK_FIELDS), incoming_books, cutoff_ms=cutoff_ms
    )
    completed_before_ms = now_ms // MINUTE_MS * MINUTE_MS
    features = minute_features(trades, books, completed_before_ms=completed_before_ms)
    write_csv(trades_path, TRADE_FIELDS, trades)
    write_csv(books_path, BOOK_FIELDS, books)
    write_csv(features_path, FEATURE_FIELDS, features)

    coverage = coverage_summary(features)
    archive_binance = sum(row.get("venue") == "binance" for row in trades)
    archive_coinbase = sum(row.get("venue") == "coinbase" for row in trades)
    gaps = {venue: trade_id_gaps(trades, venue) for venue in ("binance", "coinbase")}
    integrity = {
        venue: {
            "gap_count": len(rows),
            "missing_ids": sum(row[2] for row in rows),
            "max_gap": max((row[2] for row in rows), default=0),
            "sample": rows[:5],
        }
        for venue, rows in gaps.items()
    }
    no_unresolved_gaps = all(not rows for rows in gaps.values())
    checks = {
        "binance_trades_received": any(row.get("venue") == "binance" for row in incoming_trades),
        "coinbase_trades_received": any(row.get("venue") == "coinbase" for row in incoming_trades),
        "both_books_valid": len(incoming_books) == 2,
        "positive_spreads": all(float(row["spread_bps"]) > 0 for row in incoming_books),
        "coinbase_aggressor_side_blocked": all(
            row.get("aggressor_side") == "" for row in incoming_trades if row.get("venue") == "coinbase"
        ),
        "can_trade_false": True,
    }
    ready = bool(
        coverage["span_hours"] >= args.min_research_hours
        and coverage["both_trade_coverage_pct"] >= 95.0
        and coverage["both_book_coverage_pct"] >= 95.0
        and no_unresolved_gaps
    )
    classification = (
        "cross_venue_microstructure_ready_for_preregistered_research"
        if all(checks.values()) and ready
        else "cross_venue_microstructure_forward_collecting_with_gaps"
        if all(checks.values()) and not no_unresolved_gaps
        else "cross_venue_microstructure_forward_collecting"
        if all(checks.values())
        else "cross_venue_microstructure_data_quality_blocked"
    )
    report = {
        "generated_at": now_iso(),
        "classification": classification,
        "collection": "BTC_CROSS_VENUE_MICROSTRUCTURE_FORWARD_V1",
        "products": {"binance": args.binance_product, "coinbase": args.coinbase_product},
        "current_cycle": {
            "trades": len(incoming_trades),
            "books": len(incoming_books),
            "trade_limit_per_venue": args.trade_limit,
        },
        "archive": {
            "trades": len(trades),
            "binance_trades": archive_binance,
            "coinbase_trades": archive_coinbase,
            "book_snapshots": len(books),
            "minute_feature_rows": len(features),
            "retention_hours": args.retention_hours,
            "trade_duplicates_merged": trade_duplicates,
            "book_duplicates_merged": book_duplicates,
        },
        "trade_id_integrity": integrity,
        "gap_backfill": backfill,
        "coverage": coverage,
        "checks": checks,
        "research_readiness": {
            "minimum_hours": args.min_research_hours,
            "minimum_dual_venue_coverage_pct": 95.0,
            "requires_zero_unresolved_trade_id_gaps": True,
            "ready": ready,
            "new_preregistration_required_before_any_strategy_search": True,
        },
        "side_policy": {
            "binance": "aggressor side derived from buyer-is-maker",
            "coinbase": "reported side stored; aggressor side intentionally blank until separately verified",
        },
        "outputs": {
            "trades": portable_path(trades_path),
            "top_of_book": portable_path(books_path),
            "minute_features": portable_path(features_path),
        },
        "runtime_boundary": {
            "public_data_only": True,
            "hypothesis_registered": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    manifest = {
        "schema_version": 1,
        "generated_at": report["generated_at"],
        "collection": report["collection"],
        "files": [
            {"path": portable_path(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
            for path in (trades_path, books_path, features_path)
        ],
        "can_trade": False,
    }
    manifest_path = out_dir / "COLLECTION_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report["outputs"]["collection_manifest"] = portable_path(manifest_path)
    prefix = resolve_path(args.report_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"classification": classification, "archive": report["archive"], "coverage": coverage, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if classification != "cross_venue_microstructure_data_quality_blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())

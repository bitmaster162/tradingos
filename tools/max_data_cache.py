from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.max_backtest import (  # noqa: E402
    align_derivatives,
    fetch_binance_klines,
    fetch_funding_history,
    fetch_open_interest_history,
    ms_to_iso,
    read_dict_csv,
    read_ohlcv_csv,
    write_ohlcv_csv,
    write_oi_csv,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_float(value: Any, default: float = math.nan) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def merge_by_key(existing: list[dict[str, Any]], incoming: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in existing + incoming:
        value = row.get(key)
        if value is None or value == "":
            continue
        merged[str(value)] = row
    return [merged[item] for item in sorted(merged, key=lambda raw: int(float(raw)))]


def read_records_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    previous_limit = csv.field_size_limit()
    csv.field_size_limit(max(previous_limit, 16 * 1024 * 1024))
    try:
        rows = read_dict_csv(path)
    finally:
        csv.field_size_limit(previous_limit)
    normalized: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            if item.get("timestamp") in {None, ""}:
                continue
            item["timestamp"] = int(float(str(item["timestamp"])))
        except (TypeError, ValueError, OverflowError):
            continue
        for name in ("open_interest", "funding", "price"):
            if name in item:
                item[name] = parse_float(item.get(name))
        normalized.append(item)
    return normalized


def write_records_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def kline_cache_path(cache_dir: Path, market: str, symbol: str, interval: str) -> Path:
    return cache_dir / market / symbol.upper() / f"{interval}_klines.csv"


def raw_oi_cache_path(cache_dir: Path, symbol: str, interval: str) -> Path:
    return cache_dir / "futures" / symbol.upper() / f"{interval}_open_interest_raw.csv"


def funding_cache_path(cache_dir: Path, symbol: str) -> Path:
    return cache_dir / "futures" / symbol.upper() / "funding_raw.csv"


def aligned_oi_cache_path(cache_dir: Path, symbol: str, interval: str) -> Path:
    return cache_dir / "futures" / symbol.upper() / f"{interval}_oi_aligned.csv"


def row_range(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    if not rows:
        return {"rows": 0, "first": None, "last": None}
    values = [int(float(str(row[key]))) for row in rows if row.get(key) not in {None, ""}]
    if not values:
        return {"rows": len(rows), "first": None, "last": None}
    return {
        "rows": len(rows),
        "first": ms_to_iso(min(values)),
        "last": ms_to_iso(max(values)),
    }


def update_klines(
    *,
    cache_dir: Path,
    symbol: str,
    interval: str,
    market: str,
    limit: int,
    pages: int,
) -> dict[str, Any]:
    path = kline_cache_path(cache_dir, market, symbol, interval)
    existing = read_ohlcv_csv(path) if path.exists() else []
    incoming = fetch_binance_klines(symbol, interval, limit, market, pages=pages)
    merged = merge_by_key(existing, incoming, "time_ms")
    write_ohlcv_csv(path, merged)
    return {
        "kind": "klines",
        "market": market,
        "interval": interval,
        "path": str(path),
        "existing_rows": len(existing),
        "incoming_rows": len(incoming),
        "merged_rows": len(merged),
        "range": row_range(merged, "time_ms"),
    }


def update_derivatives(
    *,
    cache_dir: Path,
    symbol: str,
    interval: str,
    limit: int,
    pages: int,
) -> dict[str, Any]:
    oi_path = raw_oi_cache_path(cache_dir, symbol, interval)
    funding_path = funding_cache_path(cache_dir, symbol)
    existing_oi = read_records_csv(oi_path)
    existing_funding = read_records_csv(funding_path)
    incoming_oi = fetch_open_interest_history(symbol, interval, limit, pages=pages)
    incoming_funding = fetch_funding_history(symbol, pages=pages)
    merged_oi = merge_by_key(existing_oi, incoming_oi, "timestamp")
    merged_funding = merge_by_key(existing_funding, incoming_funding, "timestamp")
    write_records_csv(oi_path, merged_oi, ["timestamp", "open_interest"])
    write_records_csv(funding_path, merged_funding, ["timestamp", "funding", "price"])

    futures_klines_path = kline_cache_path(cache_dir, "futures", symbol, interval)
    aligned_path = aligned_oi_cache_path(cache_dir, symbol, interval)
    aligned_rows: list[dict[str, str]] = []
    if futures_klines_path.exists():
        futures_rows = read_ohlcv_csv(futures_klines_path)
        aligned_rows = align_derivatives(futures_rows, interval=interval, oi_records=merged_oi, funding_records=merged_funding)
        write_oi_csv(aligned_path, aligned_rows)

    return {
        "kind": "derivatives",
        "interval": interval,
        "oi_path": str(oi_path),
        "funding_path": str(funding_path),
        "aligned_path": str(aligned_path),
        "incoming_oi_rows": len(incoming_oi),
        "merged_oi_rows": len(merged_oi),
        "incoming_funding_rows": len(incoming_funding),
        "merged_funding_rows": len(merged_funding),
        "aligned_rows": len(aligned_rows),
        "oi_range": row_range(merged_oi, "timestamp"),
        "funding_range": row_range(merged_funding, "timestamp"),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MAX Core Lite Data Cache",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Symbol: `{report['symbol']}`",
        f"- Cache dir: `{report['cache_dir']}`",
        f"- Pages: `{report['pages']}`",
        f"- Limit: `{report['limit']}`",
        "",
        "## Artifacts",
        "",
        "| Kind | Market | Interval | Rows | First | Last | Path |",
        "| --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for item in report["artifacts"]:
        if item.get("kind") == "klines":
            rng = item.get("range", {})
            lines.append(
                f"| klines | `{item.get('market')}` | `{item.get('interval')}` | {item.get('merged_rows')} | "
                f"`{rng.get('first')}` | `{rng.get('last')}` | `{item.get('path')}` |"
            )
        else:
            rng = item.get("oi_range", {})
            lines.append(
                f"| derivatives | `futures` | `{item.get('interval')}` | {item.get('aligned_rows')} | "
                f"`{rng.get('first')}` | `{rng.get('last')}` | `{item.get('aligned_path')}` |"
            )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            report["runtime_boundary"],
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="MAX Core Lite public Binance data cache builder")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--intervals", default="15m,1h,4h,1d")
    parser.add_argument("--markets", default="futures,spot")
    parser.add_argument("--pages", type=int, default=8)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--cache-dir", default="data/cache/binance")
    parser.add_argument("--out-prefix", default="_dl/cache/MAX_DATA_CACHE")
    parser.add_argument("--skip-derivatives", action="store_true")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    if not cache_dir.is_absolute():
        cache_dir = ROOT / cache_dir
    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    intervals = parse_csv_list(args.intervals)
    markets = parse_csv_list(args.markets)
    artifacts: list[dict[str, Any]] = []
    for interval in intervals:
        for market in markets:
            artifacts.append(
                update_klines(
                    cache_dir=cache_dir,
                    symbol=args.symbol,
                    interval=interval,
                    market=market,
                    limit=args.limit,
                    pages=args.pages,
                )
            )
        if not args.skip_derivatives and "futures" in markets:
            artifacts.append(
                update_derivatives(
                    cache_dir=cache_dir,
                    symbol=args.symbol,
                    interval=interval,
                    limit=args.limit,
                    pages=args.pages,
                )
            )

    report = {
        "generated_at": now_iso(),
        "engine": "MAX_CORE_LITE_DATA_CACHE",
        "engine_version": "1.1.0",
        "symbol": args.symbol.upper(),
        "intervals": intervals,
        "markets": markets,
        "pages": args.pages,
        "limit": args.limit,
        "cache_dir": str(cache_dir),
        "artifacts": artifacts,
        "files": {
            "json": str(out_prefix.with_suffix(".json")),
            "md": str(out_prefix.with_suffix(".md")),
        },
        "runtime_boundary": (
            "Public-data cache only. It fetches Binance public market data, writes local CSV cache files, "
            "does not use API keys, and does not place orders."
        ),
    }
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    manifest_path = cache_dir / "cache_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"json": report["files"]["json"], "md": report["files"]["md"], "artifacts": len(artifacts)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

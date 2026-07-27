#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
BINANCE_BASE = "https://api.binance.com"
COINBASE_BASE = "https://api.exchange.coinbase.com"
INTERVALS = {"1m": 60, "5m": 300}
CSV_FIELDS = ("time", "time_ms", "open", "high", "low", "close", "volume", "venue", "product")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso_ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def fetch_json(url: str, *, timeout: int = 20, attempts: int = 3) -> Any:
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = Request(url, headers={"User-Agent": "TradingOS-CrossVenue/1.0", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # network boundary; caller receives final typed failure
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"public_fetch_failed:{type(last_error).__name__}") from last_error


def parse_binance_candle(row: list[Any], product: str = "BTCUSDT") -> dict[str, Any]:
    if len(row) < 6:
        raise ValueError("invalid_binance_kline_shape")
    timestamp = int(row[0])
    return {
        "time": iso_from_ms(timestamp),
        "time_ms": timestamp,
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "volume": float(row[5]),
        "venue": "binance",
        "product": product,
    }


def parse_coinbase_candle(row: list[Any], product: str = "BTC-USDT") -> dict[str, Any]:
    if len(row) < 6:
        raise ValueError("invalid_coinbase_candle_shape")
    timestamp = int(row[0]) * 1000
    return {
        "time": iso_from_ms(timestamp),
        "time_ms": timestamp,
        "open": float(row[3]),
        "high": float(row[2]),
        "low": float(row[1]),
        "close": float(row[4]),
        "volume": float(row[5]),
        "venue": "coinbase",
        "product": product,
    }


def normalize_rows(
    rows: list[dict[str, Any]], *, start_ms: int, end_exclusive_ms: int, interval_ms: int
) -> tuple[list[dict[str, Any]], int]:
    by_time: dict[int, dict[str, Any]] = {}
    duplicate_count = 0
    for row in rows:
        timestamp = int(row["time_ms"])
        if timestamp < start_ms or timestamp >= end_exclusive_ms or timestamp % interval_ms:
            continue
        if timestamp in by_time:
            duplicate_count += 1
        by_time[timestamp] = row
    return [by_time[key] for key in sorted(by_time)], duplicate_count


def read_candle_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                rows.append(
                    {
                        "time": row["time"],
                        "time_ms": int(row["time_ms"]),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                        "venue": row["venue"],
                        "product": row["product"],
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
    return rows


def merge_archive(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    *,
    cutoff_ms: int,
    end_exclusive_ms: int,
) -> list[dict[str, Any]]:
    by_time: dict[int, dict[str, Any]] = {}
    for row in existing + incoming:
        timestamp = int(row["time_ms"])
        if cutoff_ms <= timestamp < end_exclusive_ms:
            by_time[timestamp] = row
    return [by_time[key] for key in sorted(by_time)]


def fetch_binance_candles(
    *,
    product: str,
    interval: str,
    start_ms: int,
    end_exclusive_ms: int,
    request_delay: float,
    fetcher: Callable[..., Any] = fetch_json,
) -> list[dict[str, Any]]:
    interval_ms = INTERVALS[interval] * 1000
    cursor = start_ms
    output: list[dict[str, Any]] = []
    while cursor < end_exclusive_ms:
        chunk_end = min(end_exclusive_ms - 1, cursor + interval_ms * 999)
        query = urlencode(
            {
                "symbol": product,
                "interval": interval,
                "startTime": cursor,
                "endTime": chunk_end,
                "limit": 1000,
            }
        )
        payload = fetcher(f"{BINANCE_BASE}/api/v3/klines?{query}")
        if not isinstance(payload, list):
            raise RuntimeError("invalid_binance_response")
        output.extend(parse_binance_candle(row, product) for row in payload if isinstance(row, list))
        cursor = chunk_end + 1
        if request_delay > 0 and cursor < end_exclusive_ms:
            time.sleep(request_delay)
    return output


def fetch_coinbase_candles(
    *,
    product: str,
    interval: str,
    start_ms: int,
    end_exclusive_ms: int,
    request_delay: float,
    fetcher: Callable[..., Any] = fetch_json,
) -> list[dict[str, Any]]:
    interval_ms = INTERVALS[interval] * 1000
    cursor = start_ms
    output: list[dict[str, Any]] = []
    while cursor < end_exclusive_ms:
        chunk_end = min(end_exclusive_ms, cursor + interval_ms * 300)
        query_end = chunk_end - interval_ms
        query = urlencode(
            {
                "granularity": INTERVALS[interval],
                "start": iso_from_ms(cursor),
                "end": iso_from_ms(query_end),
            }
        )
        payload = fetcher(f"{COINBASE_BASE}/products/{product}/candles?{query}")
        if not isinstance(payload, list):
            raise RuntimeError("invalid_coinbase_response")
        output.extend(parse_coinbase_candle(row, product) for row in payload if isinstance(row, list))
        cursor = chunk_end
        if request_delay > 0 and cursor < end_exclusive_ms:
            time.sleep(request_delay)
    return output


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3:
        return None
    mean_left = statistics.mean(left)
    mean_right = statistics.mean(right)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    denominator = math.sqrt(
        sum((a - mean_left) ** 2 for a in left) * sum((b - mean_right) ** 2 for b in right)
    )
    return numerator / denominator if denominator > 0 else None


def align_rows(
    binance: list[dict[str, Any]], coinbase: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    left = {int(row["time_ms"]): row for row in binance}
    right = {int(row["time_ms"]): row for row in coinbase}
    aligned: list[dict[str, Any]] = []
    previous_binance: float | None = None
    previous_coinbase: float | None = None
    for timestamp in sorted(set(left) & set(right)):
        binance_close = float(left[timestamp]["close"])
        coinbase_close = float(right[timestamp]["close"])
        midpoint = (binance_close + coinbase_close) / 2.0
        binance_return = (
            (binance_close / previous_binance - 1.0) * 10_000 if previous_binance and previous_binance > 0 else None
        )
        coinbase_return = (
            (coinbase_close / previous_coinbase - 1.0) * 10_000 if previous_coinbase and previous_coinbase > 0 else None
        )
        aligned.append(
            {
                "time": iso_from_ms(timestamp),
                "time_ms": timestamp,
                "binance_close": binance_close,
                "coinbase_close": coinbase_close,
                "binance_volume_btc": float(left[timestamp]["volume"]),
                "coinbase_volume_btc": float(right[timestamp]["volume"]),
                "close_spread_bps": (binance_close - coinbase_close) / midpoint * 10_000 if midpoint else None,
                "binance_return_bps": binance_return,
                "coinbase_return_bps": coinbase_return,
                "return_diff_bps": (
                    binance_return - coinbase_return
                    if binance_return is not None and coinbase_return is not None
                    else None
                ),
            }
        )
        previous_binance = binance_close
        previous_coinbase = coinbase_close
    return aligned


def quality_report(
    *,
    binance: list[dict[str, Any]],
    coinbase: list[dict[str, Any]],
    aligned: list[dict[str, Any]],
    requested_bars: int,
    binance_duplicates: int,
    coinbase_duplicates: int,
    interval: str,
    start_ms: int,
    end_exclusive_ms: int,
    binance_product: str = "BTCUSDT",
    coinbase_product: str = "BTC-USD",
) -> dict[str, Any]:
    overlap = len(aligned)
    binance_coverage = len(binance) / requested_bars * 100 if requested_bars else 0.0
    coinbase_coverage = len(coinbase) / requested_bars * 100 if requested_bars else 0.0
    overlap_coverage = overlap / requested_bars * 100 if requested_bars else 0.0
    spreads = [abs(float(row["close_spread_bps"])) for row in aligned if row["close_spread_bps"] is not None]
    paired_returns = [
        (float(row["binance_return_bps"]), float(row["coinbase_return_bps"]))
        for row in aligned
        if row["binance_return_bps"] is not None and row["coinbase_return_bps"] is not None
    ]
    return_correlation = pearson(
        [item[0] for item in paired_returns], [item[1] for item in paired_returns]
    )
    same_quote_asset = binance_product.endswith("USDT") and coinbase_product.endswith("USDT")
    return_comparison_allowed = binance_product.startswith("BTC") and coinbase_product.startswith("BTC-")
    checks = {
        "binance_coverage_ge_95pct": binance_coverage >= 95.0,
        "coinbase_coverage_ge_95pct": coinbase_coverage >= 95.0,
        "overlap_coverage_ge_95pct": overlap_coverage >= 95.0,
        "no_duplicate_timestamps": binance_duplicates == 0 and coinbase_duplicates == 0,
        "same_base_asset": True,
        "return_comparison_allowed": return_comparison_allowed,
        "return_correlation_ge_0_90": return_correlation is not None and return_correlation >= 0.90,
    }
    classification = "cross_venue_collection_ready" if all(checks.values()) else "cross_venue_data_quality_blocked"
    return {
        "generated_at": now_iso(),
        "classification": classification,
        "window": {
            "interval": interval,
            "start": iso_from_ms(start_ms),
            "end_exclusive": iso_from_ms(end_exclusive_ms),
            "requested_bars": requested_bars,
            "closed_bars_only": True,
        },
        "venues": {
            "binance": {
                "product": binance_product,
                "rows": len(binance),
                "coverage_pct": round(binance_coverage, 6),
                "duplicates": binance_duplicates,
            },
            "coinbase": {
                "product": coinbase_product,
                "rows": len(coinbase),
                "coverage_pct": round(coinbase_coverage, 6),
                "duplicates": coinbase_duplicates,
            },
        },
        "alignment": {
            "rows": overlap,
            "coverage_pct": round(overlap_coverage, 6),
            "median_abs_close_spread_bps": round(statistics.median(spreads), 6) if spreads else None,
            "p95_abs_close_spread_bps": round(float(percentile(spreads, 0.95)), 6) if spreads else None,
            "return_correlation": round(float(return_correlation), 9) if return_correlation is not None else None,
            "quote_assets_same": same_quote_asset,
            "level_spread_comparison_allowed": same_quote_asset,
            "spread_interpretation": (
                "direct_same_quote_spread"
                if same_quote_asset
                else "confounded_by_usd_usdt_basis_use_returns_only"
            ),
        },
        "checks": checks,
        "source_contract": {
            "binance": "GET /api/v3/klines; public market data; max 1000 rows requested per page",
            "coinbase": "GET /products/{product_id}/candles; public market data; 300 rows requested per page; no-trade intervals may be absent",
            "credentials": False,
            "orders": False,
        },
        "runtime_boundary": {
            "data_collection_only": True,
            "research_hypothesis_opened": False,
            "observer_registration_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def atomic_write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def file_record(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": portable_path(path),
        "size": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def render_markdown(report: dict[str, Any]) -> str:
    venues = report["venues"]
    alignment = report["alignment"]
    return "\n".join(
        [
            "# Cross-Venue BTC Spot Data Quality",
            "",
            f"Generated: `{report['generated_at']}`",
            "",
            f"- Classification: `{report['classification']}`.",
            f"- Window: `{report['window']['start']}` to `{report['window']['end_exclusive']}` at `{report['window']['interval']}`.",
            f"- Binance: `{venues['binance']['rows']}` rows, `{venues['binance']['coverage_pct']}`% coverage.",
            f"- Coinbase: `{venues['coinbase']['rows']}` rows, `{venues['coinbase']['coverage_pct']}`% coverage.",
            f"- Aligned: `{alignment['rows']}` rows, `{alignment['coverage_pct']}`% coverage.",
            f"- Return correlation: `{alignment['return_correlation']}`.",
            f"- Median / p95 absolute close spread: `{alignment['median_abs_close_spread_bps']}` / `{alignment['p95_abs_close_spread_bps']}` bps.",
            f"- Spread interpretation: `{alignment['spread_interpretation']}`.",
            "- This proves public data collection and alignment only. It is not evidence of lead-lag edge.",
            "- `can_trade=false`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect and align public Binance/Coinbase BTC-USDT spot candles")
    parser.add_argument("--interval", choices=sorted(INTERVALS), default="1m")
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--coinbase-product", choices=("BTC-USD", "BTC-USDT"), default="BTC-USD")
    parser.add_argument("--end", help="Optional exclusive UTC end; defaults to current closed-bar boundary")
    parser.add_argument("--request-delay", type=float, default=0.12)
    parser.add_argument("--retention-hours", type=float, default=744.0)
    parser.add_argument("--replace", action="store_true", help="Replace retained archive instead of merging existing candles")
    parser.add_argument("--out-dir", default="data/cross_venue_spot")
    parser.add_argument("--report-prefix", default="docs/CROSS_VENUE_SPOT_DATA_QUALITY_2026-06-24")
    args = parser.parse_args()

    if args.hours <= 0 or args.hours > 24 * 31:
        raise SystemExit("--hours must be within (0, 744]")
    if args.retention_hours < args.hours or args.retention_hours > 24 * 31:
        raise SystemExit("--retention-hours must be within [hours, 744]")
    interval_ms = INTERVALS[args.interval] * 1000
    raw_end = parse_iso_ms(args.end) if args.end else int(datetime.now(timezone.utc).timestamp() * 1000)
    end_exclusive_ms = raw_end - raw_end % interval_ms
    requested_bars = int(args.hours * 3_600_000 // interval_ms)
    if requested_bars < 3:
        raise SystemExit("window must contain at least three bars")
    start_ms = end_exclusive_ms - requested_bars * interval_ms

    binance_raw = fetch_binance_candles(
        product="BTCUSDT",
        interval=args.interval,
        start_ms=start_ms,
        end_exclusive_ms=end_exclusive_ms,
        request_delay=args.request_delay,
    )
    coinbase_raw = fetch_coinbase_candles(
        product=args.coinbase_product,
        interval=args.interval,
        start_ms=start_ms,
        end_exclusive_ms=end_exclusive_ms,
        request_delay=args.request_delay,
    )
    binance, binance_duplicates = normalize_rows(
        binance_raw, start_ms=start_ms, end_exclusive_ms=end_exclusive_ms, interval_ms=interval_ms
    )
    coinbase, coinbase_duplicates = normalize_rows(
        coinbase_raw, start_ms=start_ms, end_exclusive_ms=end_exclusive_ms, interval_ms=interval_ms
    )
    aligned_pull = align_rows(binance, coinbase)
    report = quality_report(
        binance=binance,
        coinbase=coinbase,
        aligned=aligned_pull,
        requested_bars=requested_bars,
        binance_duplicates=binance_duplicates,
        coinbase_duplicates=coinbase_duplicates,
        interval=args.interval,
        start_ms=start_ms,
        end_exclusive_ms=end_exclusive_ms,
        binance_product="BTCUSDT",
        coinbase_product=args.coinbase_product,
    )

    out_dir = resolve_path(args.out_dir)
    binance_path = out_dir / "binance" / "BTCUSDT" / f"{args.interval}_candles.csv"
    coinbase_path = out_dir / "coinbase" / args.coinbase_product / f"{args.interval}_candles.csv"
    aligned_path = out_dir / "aligned" / f"BTCUSDT__{args.coinbase_product}" / f"{args.interval}_candles.csv"
    existing_binance = [] if args.replace else read_candle_csv(binance_path)
    existing_coinbase = [] if args.replace else read_candle_csv(coinbase_path)
    retention_cutoff_ms = end_exclusive_ms - int(args.retention_hours * 3_600_000)
    archive_binance = merge_archive(
        existing_binance,
        binance,
        cutoff_ms=retention_cutoff_ms,
        end_exclusive_ms=end_exclusive_ms,
    )
    archive_coinbase = merge_archive(
        existing_coinbase,
        coinbase,
        cutoff_ms=retention_cutoff_ms,
        end_exclusive_ms=end_exclusive_ms,
    )
    aligned_archive = align_rows(archive_binance, archive_coinbase)
    report["archive"] = {
        "merge_existing": not args.replace,
        "retention_hours": args.retention_hours,
        "binance_rows": len(archive_binance),
        "coinbase_rows": len(archive_coinbase),
        "aligned_rows": len(aligned_archive),
        "first": aligned_archive[0]["time"] if aligned_archive else None,
        "last": aligned_archive[-1]["time"] if aligned_archive else None,
    }
    aligned_fields = (
        "time",
        "time_ms",
        "binance_close",
        "coinbase_close",
        "binance_volume_btc",
        "coinbase_volume_btc",
        "close_spread_bps",
        "binance_return_bps",
        "coinbase_return_bps",
        "return_diff_bps",
    )
    atomic_write_csv(binance_path, archive_binance, CSV_FIELDS)
    atomic_write_csv(coinbase_path, archive_coinbase, CSV_FIELDS)
    atomic_write_csv(aligned_path, aligned_archive, aligned_fields)
    manifest_path = out_dir / "COLLECTION_MANIFEST.json"
    manifest = {
        "schema_version": 1,
        "generated_at": report["generated_at"],
        "collection": "BTC_CROSS_VENUE_SPOT_V1",
        "window": report["window"],
        "quality_classification": report["classification"],
        "files": [file_record(path) for path in (binance_path, coinbase_path, aligned_path)],
        "credentials": False,
        "orders": False,
        "can_trade": False,
    }
    atomic_write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))
    report["outputs"] = {
        "binance_csv": portable_path(binance_path),
        "coinbase_csv": portable_path(coinbase_path),
        "aligned_csv": portable_path(aligned_path),
        "collection_manifest": portable_path(manifest_path),
    }
    report_prefix = resolve_path(args.report_prefix)
    atomic_write_text(report_prefix.with_suffix(".json"), json.dumps(report, ensure_ascii=False, indent=2))
    atomic_write_text(report_prefix.with_suffix(".md"), render_markdown(report))
    print(
        json.dumps(
            {
                "classification": report["classification"],
                "interval": args.interval,
                "requested_bars": requested_bars,
                "binance_rows": len(binance),
                "coinbase_rows": len(coinbase),
                "pull_aligned_rows": len(aligned_pull),
                "archive_aligned_rows": len(aligned_archive),
                "return_correlation": report["alignment"]["return_correlation"],
                "report": portable_path(report_prefix.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["classification"] == "cross_venue_collection_ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())

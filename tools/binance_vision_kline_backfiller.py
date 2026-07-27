#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from zipfile import BadZipFile, ZipFile


ROOT = Path(__file__).resolve().parents[1]
FUTURES_BASE_URL = "https://data.binance.vision/data/futures/um/monthly/klines"
SPOT_BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"
FIELDNAMES = ["time", "time_ms", "open", "high", "low", "close", "volume"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def display(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def normalize_timestamp(value: str) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed > 10_000_000_000_000:
        parsed //= 1000
    return parsed if 0 < parsed < 10_000_000_000_000 else None


def ms_to_iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat()


def month_range(start: str, end: str) -> list[str]:
    start_dt = datetime.strptime(start, "%Y-%m")
    end_dt = datetime.strptime(end, "%Y-%m")
    months: list[str] = []
    current = start_dt
    while current <= end_dt:
        months.append(current.strftime("%Y-%m"))
        current = current.replace(year=current.year + (1 if current.month == 12 else 0), month=1 if current.month == 12 else current.month + 1)
    return months


def archive_name(symbol: str, interval: str, month: str) -> str:
    return f"{symbol.upper()}-{interval}-{month}.zip"


def archive_base_url(market: str) -> str:
    if market == "futures":
        return FUTURES_BASE_URL
    if market == "spot":
        return SPOT_BASE_URL
    raise ValueError(f"unsupported market: {market}")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def download_bytes(url: str, timeout: int) -> bytes:
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed official Binance archive host.
        return response.read()


def fetch_archive(symbol: str, interval: str, month: str, cache_root: Path, timeout: int, base_url: str = FUTURES_BASE_URL) -> dict[str, Any]:
    name = archive_name(symbol, interval, month)
    path = cache_root / symbol.upper() / interval / name
    checksum_path = path.with_suffix(path.suffix + ".CHECKSUM")
    base = f"{base_url}/{symbol.upper()}/{interval}/{name}"
    try:
        if path.exists() and path.stat().st_size > 0:
            payload = path.read_bytes()
            source = "cached"
        else:
            payload = download_bytes(base, timeout)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            source = "downloaded"
        if checksum_path.exists() and checksum_path.stat().st_size > 0:
            checksum_text = checksum_path.read_text(encoding="utf-8-sig")
        else:
            checksum_text = download_bytes(base + ".CHECKSUM", timeout).decode("utf-8-sig")
            checksum_path.write_text(checksum_text, encoding="utf-8")
        expected = checksum_text.strip().split()[0].lower()
        actual = sha256_bytes(payload)
        return {
            "month": month,
            "status": source if expected == actual else "checksum_mismatch",
            "path": str(path),
            "bytes": len(payload),
            "expected_sha256": expected,
            "actual_sha256": actual,
            "checksum_ok": expected == actual,
        }
    except HTTPError as exc:
        return {"month": month, "status": "missing", "url": base, "error": f"HTTP {exc.code}", "checksum_ok": False}
    except (URLError, TimeoutError, OSError, UnicodeDecodeError) as exc:
        return {"month": month, "status": "error", "url": base, "error": repr(exc), "checksum_ok": False}


def parse_archive(path: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    rows: dict[int, dict[str, str]] = {}
    stats: dict[str, Any] = {"path": display(path), "rows_seen": 0, "rows_valid": 0, "error": None}
    try:
        with ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.endswith(".csv")]
            if not names:
                stats["error"] = "no_csv_in_zip"
                return [], stats
            with archive.open(names[0]) as binary:
                reader = csv.reader(io.TextIOWrapper(binary, encoding="utf-8-sig", newline=""))
                for source in reader:
                    stats["rows_seen"] += 1
                    if len(source) < 6:
                        continue
                    timestamp = normalize_timestamp(source[0])
                    if timestamp is None:
                        continue
                    try:
                        values = [float(source[index]) for index in range(1, 6)]
                    except ValueError:
                        continue
                    rows[timestamp] = {
                        "time": ms_to_iso(timestamp),
                        "time_ms": str(timestamp),
                        "open": str(values[0]),
                        "high": str(values[1]),
                        "low": str(values[2]),
                        "close": str(values[3]),
                        "volume": str(values[4]),
                    }
                    stats["rows_valid"] += 1
    except (BadZipFile, OSError, UnicodeDecodeError, csv.Error) as exc:
        stats["error"] = repr(exc)
    return [rows[key] for key in sorted(rows)], stats


def read_existing(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def merge_rows(historical: list[dict[str, str]], existing: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    merged: dict[int, dict[str, str]] = {}
    for row in historical:
        timestamp = normalize_timestamp(row.get("time_ms", ""))
        if timestamp is not None:
            merged[timestamp] = {field: row.get(field, "") for field in FIELDNAMES}
    overlap = 0
    for row in existing:
        timestamp = normalize_timestamp(row.get("time_ms", ""))
        if timestamp is None:
            continue
        overlap += int(timestamp in merged)
        merged[timestamp] = {field: row.get(field, "") for field in FIELDNAMES}
    return [merged[key] for key in sorted(merged)], overlap


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Binance spot or USD-M futures klines from checksum-verified monthly Vision archives.")
    parser.add_argument("--market", choices=["futures", "spot"], default="futures")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--start", default="2021-01")
    parser.add_argument("--end", default="2025-01")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--zip-cache-dir", default="data/cache/binance_vision_klines/monthly/um")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out-prefix", default="docs/BINANCE_VISION_KLINE_BACKFILL_2026-06-23")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    base_url = archive_base_url(args.market)
    months = month_range(args.start, args.end)
    zip_root = resolve_path(args.zip_cache_dir)
    if args.market == "spot" and args.zip_cache_dir == "data/cache/binance_vision_klines/monthly/um":
        zip_root = resolve_path("data/cache/binance_vision_klines/monthly/spot")
    fetch_results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(fetch_archive, symbol, args.interval, month, zip_root, args.timeout, base_url) for month in months]
        for future in as_completed(futures):
            fetch_results.append(future.result())
    fetch_results.sort(key=lambda item: str(item.get("month")))

    verified = [item for item in fetch_results if item.get("checksum_ok") is True]
    failures = [item for item in fetch_results if item.get("checksum_ok") is not True]
    historical: list[dict[str, str]] = []
    parse_stats: list[dict[str, Any]] = []
    for item in verified:
        rows, stats = parse_archive(Path(str(item["path"])))
        historical.extend(rows)
        parse_stats.append(stats)
    historical, _ = merge_rows(historical, [])
    output_path = resolve_path(args.cache_dir) / args.market / symbol / f"{args.interval}_klines.csv"
    existing = read_existing(output_path)
    merged, overlap = merge_rows(historical, existing)
    backup_path: Path | None = None
    if not args.dry_run and not failures and historical:
        if output_path.exists():
            backup_root = output_path.parent / "_backfill_backup" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_root.mkdir(parents=True, exist_ok=True)
            backup_path = backup_root / output_path.name
            shutil.copy2(output_path, backup_path)
        write_rows(output_path, merged)

    first = merged[0].get("time") if merged else None
    last = merged[-1].get("time") if merged else None
    decision = "dry_run_ready_for_import" if args.dry_run and not failures and historical else "backfill_import_completed_no_orders"
    if failures or not historical:
        decision = "backfill_blocked_archive_validation_failed"
    report = {
        "generated_at": now_iso(),
        "engine": "BINANCE_VISION_KLINE_BACKFILLER",
        "engine_version": "1.0.0",
        "inputs": {"market": args.market, "symbol": symbol, "interval": args.interval, "start": args.start, "end": args.end, "months": len(months)},
        "summary": {
            "archives_verified": len(verified),
            "archives_failed": len(failures),
            "historical_rows": len(historical),
            "existing_rows": len(existing),
            "overlap_rows_existing_preferred": overlap,
            "merged_rows": len(merged),
            "first": first,
            "last": last,
            "written": bool(not args.dry_run and not failures and historical),
            "output": display(output_path),
            "existing_backup": display(backup_path) if backup_path else None,
        },
        "failure_preview": failures[:20],
        "parse_errors": [item for item in parse_stats if item.get("error")][:20],
        "decision": decision,
        "boundaries": {
            "source": base_url,
            "checksums_required": True,
            "uses_private_credentials": False,
            "changes_strategy_parameters": False,
            "paper_execution_allowed": False,
            "live_execution_allowed": False,
            "sends_orders": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(
        "\n".join(
            [
                "# Binance Vision Kline Backfill",
                "",
                f"- Decision: `{decision}`",
                f"- Verified archives: `{len(verified)}/{len(months)}`",
                f"- Historical/existing/merged rows: `{len(historical)}/{len(existing)}/{len(merged)}`",
                f"- Range: `{first}` to `{last}`",
                f"- Written: `{report['summary']['written']}`",
                "- Existing API rows override archive rows on overlap.",
                "- No strategy changes, credentials or orders.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps({"decision": decision, "summary": report["summary"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if decision != "backfill_blocked_archive_validation_failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())

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
BASE_URL = "https://data.binance.vision/data/futures/um/monthly/fundingRate"
FIELDNAMES = ["timestamp", "funding", "price"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def display(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def month_range(start: str, end: str) -> list[str]:
    current = datetime.strptime(start, "%Y-%m")
    end_dt = datetime.strptime(end, "%Y-%m")
    months: list[str] = []
    while current <= end_dt:
        months.append(current.strftime("%Y-%m"))
        current = current.replace(year=current.year + (current.month == 12), month=1 if current.month == 12 else current.month + 1)
    return months


def normalize_timestamp(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed > 10_000_000_000_000:
        parsed //= 1000
    return parsed if 0 < parsed < 10_000_000_000_000 else None


def archive_name(symbol: str, month: str) -> str:
    return f"{symbol.upper()}-fundingRate-{month}.zip"


def download_bytes(url: str, timeout: int) -> bytes:
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed official Binance archive host.
        return response.read()


def fetch_archive(symbol: str, month: str, cache_root: Path, timeout: int) -> dict[str, Any]:
    name = archive_name(symbol, month)
    path = cache_root / symbol.upper() / name
    checksum_path = path.with_suffix(path.suffix + ".CHECKSUM")
    url = f"{BASE_URL}/{symbol.upper()}/{name}"
    try:
        if path.exists() and path.stat().st_size > 0:
            payload = path.read_bytes()
            source = "cached"
        else:
            payload = download_bytes(url, timeout)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            source = "downloaded"
        if checksum_path.exists() and checksum_path.stat().st_size > 0:
            checksum_text = checksum_path.read_text(encoding="utf-8-sig")
        else:
            checksum_text = download_bytes(url + ".CHECKSUM", timeout).decode("utf-8-sig")
            checksum_path.write_text(checksum_text, encoding="utf-8")
        expected = checksum_text.strip().split()[0].lower()
        actual = hashlib.sha256(payload).hexdigest()
        return {"month": month, "status": source if expected == actual else "checksum_mismatch", "path": str(path), "checksum_ok": expected == actual}
    except HTTPError as exc:
        return {"month": month, "status": "missing", "url": url, "error": f"HTTP {exc.code}", "checksum_ok": False}
    except (URLError, TimeoutError, OSError, UnicodeDecodeError) as exc:
        return {"month": month, "status": "error", "url": url, "error": repr(exc), "checksum_ok": False}


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
                reader = csv.DictReader(io.TextIOWrapper(binary, encoding="utf-8-sig", newline=""))
                for source in reader:
                    stats["rows_seen"] += 1
                    timestamp = normalize_timestamp(source.get("calc_time"))
                    try:
                        funding = float(str(source.get("last_funding_rate")))
                    except ValueError:
                        continue
                    if timestamp is None:
                        continue
                    rows[timestamp] = {"timestamp": str(timestamp), "funding": str(funding), "price": "nan"}
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
        timestamp = normalize_timestamp(row.get("timestamp"))
        if timestamp is not None:
            merged[timestamp] = {field: str(row.get(field, "")) for field in FIELDNAMES}
    overlap = 0
    for row in existing:
        timestamp = normalize_timestamp(row.get("timestamp"))
        if timestamp is None:
            continue
        overlap += int(timestamp in merged)
        merged[timestamp] = {field: str(row.get(field, "")) for field in FIELDNAMES}
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
    parser = argparse.ArgumentParser(description="Backfill Binance USD-M funding settlements from checksum-verified monthly Vision archives")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start", default="2021-01")
    parser.add_argument("--end", default="2026-05")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--zip-cache-dir", default="data/cache/binance_vision_funding/monthly/um")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out-prefix", default="docs/BINANCE_VISION_FUNDING_BACKFILL_2026-06-23")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    months = month_range(args.start, args.end)
    zip_root = resolve_path(args.zip_cache_dir)
    fetched: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(fetch_archive, symbol, month, zip_root, args.timeout) for month in months]
        for future in as_completed(futures):
            fetched.append(future.result())
    fetched.sort(key=lambda item: item["month"])
    verified = [item for item in fetched if item.get("checksum_ok") is True]
    failures = [item for item in fetched if item.get("checksum_ok") is not True]
    historical: list[dict[str, str]] = []
    parse_stats: list[dict[str, Any]] = []
    for item in verified:
        rows, stats = parse_archive(Path(item["path"]))
        historical.extend(rows)
        parse_stats.append(stats)
    historical, _ = merge_rows(historical, [])
    output_path = resolve_path(args.cache_dir) / "futures" / symbol / "funding_raw.csv"
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
    decision = "dry_run_ready_for_import" if args.dry_run and not failures and historical else "funding_backfill_completed_no_orders"
    if failures or not historical:
        decision = "funding_backfill_blocked_archive_validation_failed"
    first = merged[0]["timestamp"] if merged else None
    last = merged[-1]["timestamp"] if merged else None
    report = {
        "generated_at": now_iso(),
        "engine": "BINANCE_VISION_FUNDING_BACKFILLER",
        "inputs": {"symbol": symbol, "start": args.start, "end": args.end, "months": len(months)},
        "summary": {
            "archives_verified": len(verified),
            "archives_failed": len(failures),
            "historical_rows": len(historical),
            "existing_rows": len(existing),
            "overlap_rows_existing_preferred": overlap,
            "merged_rows": len(merged),
            "first_timestamp": first,
            "last_timestamp": last,
            "written": bool(not args.dry_run and not failures and historical),
            "output": display(output_path),
            "existing_backup": display(backup_path),
        },
        "failures": failures[:20],
        "parse_errors": [item for item in parse_stats if item.get("error")][:20],
        "decision": decision,
        "boundaries": {"source": BASE_URL, "checksums_required": True, "uses_private_credentials": False, "sends_orders": False, "can_trade": False},
        "can_trade": False,
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(
        "\n".join([
            "# Binance Vision Funding Backfill",
            "",
            f"- Decision: `{decision}`",
            f"- Verified archives: `{len(verified)}/{len(months)}`",
            f"- Historical/existing/merged rows: `{len(historical)}/{len(existing)}/{len(merged)}`",
            f"- Written: `{report['summary']['written']}`",
            "- Existing API rows override archive rows on overlap.",
            "- No credentials, strategy changes, or orders.",
            "",
        ]),
        encoding="utf-8",
    )
    print(json.dumps({"decision": decision, "summary": report["summary"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if not failures and historical else 2


if __name__ == "__main__":
    raise SystemExit(main())

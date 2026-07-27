#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.historical_oi_importer import (  # noqa: E402
    merge_records,
    normalize_oi_rows,
    simulated_post_import_coverage,
    write_oi_csv,
)

BASE_URL = "https://data.binance.vision/data/futures/um/daily/metrics"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_create_time_ms(value: str) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        pass
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp() * 1000)
    except ValueError:
        return None


def ms_to_iso(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def date_range(start: date, end: date, max_days: int | None = None) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        if max_days is not None and len(days) >= max_days:
            break
        current += timedelta(days=1)
    return days


def zip_url(symbol: str, day: date) -> str:
    stamp = day.isoformat()
    upper = symbol.upper()
    return f"{BASE_URL}/{upper}/{upper}-metrics-{stamp}.zip"


def zip_cache_path(cache_dir: Path, symbol: str, day: date) -> Path:
    stamp = day.isoformat()
    upper = symbol.upper()
    return cache_dir / upper / f"{upper}-metrics-{stamp}.zip"


def read_zip_bytes(path: Path) -> bytes:
    return path.read_bytes()


def fetch_zip(symbol: str, day: date, cache_dir: Path, timeout: int) -> dict[str, Any]:
    path = zip_cache_path(cache_dir, symbol, day)
    if path.exists() and path.stat().st_size > 0:
        return {"date": day.isoformat(), "status": "cached", "path": rel(path), "bytes": path.stat().st_size}
    url = zip_url(symbol, day)
    try:
        with urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed public Binance data endpoint.
            data = response.read()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"date": day.isoformat(), "status": "downloaded", "path": rel(path), "bytes": len(data)}
    except HTTPError as exc:
        return {"date": day.isoformat(), "status": "missing", "url": url, "error": f"HTTP {exc.code}"}
    except (URLError, TimeoutError, OSError) as exc:
        return {"date": day.isoformat(), "status": "error", "url": url, "error": repr(exc)}


def parse_metrics_zip(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records_by_ts: dict[int, dict[str, Any]] = {}
    stats = {"path": rel(path), "rows_seen": 0, "rows_valid": 0, "first": None, "last": None}
    try:
        with ZipFile(path) as zf:
            names = [name for name in zf.namelist() if name.endswith(".csv")]
            if not names:
                stats["error"] = "no_csv_in_zip"
                return [], stats
            with zf.open(names[0]) as handle:
                text = io.TextIOWrapper(handle, encoding="utf-8-sig", newline="")
                reader = csv.DictReader(text)
                for row in reader:
                    stats["rows_seen"] += 1
                    ts = parse_create_time_ms(row.get("create_time", ""))
                    oi = safe_float(row.get("sum_open_interest"))
                    if ts is None or oi is None:
                        continue
                    records_by_ts[ts] = {"timestamp": ts, "open_interest": oi}
    except (BadZipFile, OSError, UnicodeDecodeError, csv.Error) as exc:
        stats["error"] = repr(exc)
        return [], stats
    records = [records_by_ts[key] for key in sorted(records_by_ts)]
    stats["rows_valid"] = len(records)
    if records:
        stats["first"] = ms_to_iso(int(records[0]["timestamp"]))
        stats["last"] = ms_to_iso(int(records[-1]["timestamp"]))
    return records, stats


def run_quality_collector(out_prefix: str, interval: str) -> dict[str, Any]:
    command = [
        sys.executable,
        str(ROOT / "tools" / "oi_funding_data_quality_collector.py"),
        "--no-fetch",
        "--interval",
        interval,
        "--out-prefix",
        out_prefix,
    ]
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# Binance Vision OI Backfill",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Public Binance Data Vision archive only.",
        "- No private credentials, no account access, no orders.",
        "- Cache write happens only when `dry_run=false`.",
        "",
        "## Summary",
        "",
    ]
    for key, value in summary.items():
        lines.append(f"- {key}: `{value}`.")
    simulated = report.get("simulated_post_import_coverage")
    if isinstance(simulated, dict):
        lines.extend(["", "## Simulated Post-Import Coverage", ""])
        for key in [
            "classification",
            "aligned_oi_coverage_pct",
            "trades",
            "minimum_full_context",
            "full_context_available",
            "full_context_coverage_pct",
            "oi_context_available",
            "oi_context_coverage_pct",
        ]:
            if key in simulated:
                lines.append(f"- {key}: `{simulated.get(key)}`.")
    lines.extend(["", "## Decision", "", f"- `{report.get('decision')}`.", ""])
    lines.extend(["## Next Action", "", f"- `{report.get('next_action')}`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill BTCUSDT open interest from Binance Vision daily metrics ZIPs")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="4h")
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2026-05-05")
    parser.add_argument("--max-days", type=int)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--zip-cache-dir", default="data/cache/binance_vision_metrics/daily/metrics")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--replay-trades-csv", default="docs/STRATEGY_MIX_PAPER_REPLAY_2026-06-08_trades.csv")
    parser.add_argument("--oi-lookback", type=int, default=12)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out-prefix", default="docs/BINANCE_VISION_OI_BACKFILL_2026-06-15")
    parser.add_argument("--quality-out-prefix", default="docs/OI_FUNDING_DATA_QUALITY_2026-06-15")
    args = parser.parse_args()

    start = parse_date(args.start)
    end = parse_date(args.end)
    days = date_range(start, end, args.max_days)
    zip_cache_dir = resolve_path(args.zip_cache_dir)
    cache_dir = resolve_path(args.cache_dir)
    replay_path = resolve_path(args.replay_trades_csv)
    out_prefix = resolve_path(args.out_prefix)
    raw_path = cache_dir / "futures" / args.symbol.upper() / f"{args.interval}_open_interest_raw.csv"

    fetch_results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(fetch_zip, args.symbol, day, zip_cache_dir, args.timeout): day for day in days}
        for future in as_completed(futures):
            fetch_results.append(future.result())
    fetch_results.sort(key=lambda item: item["date"])

    incoming: list[dict[str, Any]] = []
    parse_stats: list[dict[str, Any]] = []
    for item in fetch_results:
        if item.get("status") not in {"cached", "downloaded"}:
            continue
        path = resolve_path(str(item["path"]))
        records, stats = parse_metrics_zip(path)
        incoming.extend(records)
        parse_stats.append(stats)

    # Deduplicate across daily files and existing local cache.
    incoming = merge_records([], incoming)
    existing, existing_stats = normalize_oi_rows(raw_path, "timestamp", "open_interest") if raw_path.exists() else ([], {})
    merged = merge_records(existing, incoming)
    simulated = simulated_post_import_coverage(
        cache_dir=cache_dir,
        symbol=args.symbol,
        interval=args.interval,
        merged_oi=merged,
        replay_trades_csv=replay_path,
        oi_lookback=args.oi_lookback,
    )

    quality = None
    backup_path: Path | None = None
    if not args.dry_run and incoming:
        if raw_path.exists():
            backup_root = raw_path.parent / "_backfill_backup" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_root.mkdir(parents=True, exist_ok=True)
            backup_path = backup_root / raw_path.name
            shutil.copy2(raw_path, backup_path)
        write_oi_csv(raw_path, merged)
        quality = run_quality_collector(args.quality_out_prefix, args.interval)

    downloaded = sum(1 for item in fetch_results if item.get("status") == "downloaded")
    cached = sum(1 for item in fetch_results if item.get("status") == "cached")
    missing = sum(1 for item in fetch_results if item.get("status") == "missing")
    errors = sum(1 for item in fetch_results if item.get("status") == "error")
    import_ready = simulated.get("classification") == "post_import_candidate_coverage_ready"
    decision = "dry_run_ready_for_import" if args.dry_run and import_ready else "dry_run_or_import_not_ready"
    if not args.dry_run and incoming:
        decision = "backfill_import_completed_no_orders"

    report = {
        "generated_at": now_iso(),
        "boundary": {
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "source": "https://data.binance.vision/data/futures/um/daily/metrics/",
            "dry_run": bool(args.dry_run),
        },
        "inputs": {
            "symbol": args.symbol,
            "interval": args.interval,
            "start": args.start,
            "end": args.end,
            "max_days": args.max_days,
            "workers": args.workers,
            "zip_cache_dir": rel(zip_cache_dir),
            "cache_dir": rel(cache_dir),
            "raw_cache": rel(raw_path),
            "replay_trades_csv": rel(replay_path),
            "oi_lookback": args.oi_lookback,
        },
        "summary": {
            "symbol": args.symbol,
            "interval": args.interval,
            "dry_run": bool(args.dry_run),
            "days_requested": len(days),
            "days_downloaded": downloaded,
            "days_cached": cached,
            "days_missing": missing,
            "days_error": errors,
            "incoming_oi_rows": len(incoming),
            "existing_oi_rows": len(existing),
            "merged_oi_rows": len(merged),
            "cache_written": bool((not args.dry_run) and incoming),
            "existing_backup": rel(backup_path) if backup_path else None,
            "incoming_first": ms_to_iso(int(incoming[0]["timestamp"])) if incoming else None,
            "incoming_last": ms_to_iso(int(incoming[-1]["timestamp"])) if incoming else None,
            "existing_first": existing_stats.get("first"),
            "existing_last": existing_stats.get("last"),
            "simulated_classification": simulated.get("classification"),
            "simulated_full_context_available": simulated.get("full_context_available"),
            "simulated_trades": simulated.get("trades"),
            "import_ready_for_guard_retest": import_ready,
        },
        "simulated_post_import_coverage": simulated,
        "fetch_results_preview": fetch_results[:20],
        "parse_stats_preview": parse_stats[:20],
        "quality_collector": quality,
        "decision": decision,
        "next_action": "rerun without --dry-run to write canonical OI cache, then rerun replay auditor/guard validation"
        if args.dry_run and import_ready
        else "inspect missing/errors or widen date coverage before guard retest",
    }

    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "md": str(md_path), "summary": report["summary"], "decision": decision, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

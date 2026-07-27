#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from binance_crowd_positioning_collector import (
    FIELDNAMES,
    format_float,
    ms_to_iso,
    read_csv_rows,
    write_csv_rows,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_NAME = "binance_vision_daily_metrics"
RATIO_COLUMNS = {
    "global": "count_long_short_ratio",
    "top_account": "count_toptrader_long_short_ratio",
    "top_position": "sum_toptrader_long_short_ratio",
}
INTERVAL_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}


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


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def parse_time_ms(value: str) -> int | None:
    try:
        parsed = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


def parse_zip_date(path: Path, symbol: str) -> date | None:
    prefix = f"{symbol.upper()}-metrics-"
    if not path.stem.startswith(prefix):
        return None
    try:
        return datetime.strptime(path.stem[len(prefix) :], "%Y-%m-%d").date()
    except ValueError:
        return None


def ratio_shares(ratio: float) -> tuple[float, float]:
    short_share = 1.0 / (1.0 + ratio)
    return ratio * short_share, short_share


def metric_to_row(timestamp: int, ratios: dict[str, float]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "time": ms_to_iso(timestamp),
        "timestamp": str(timestamp),
        "source": SOURCE_NAME,
    }
    for prefix, ratio in ratios.items():
        long_share, short_share = ratio_shares(ratio)
        row[f"{prefix}_long_account"] = format_float(long_share)
        row[f"{prefix}_short_account"] = format_float(short_share)
        row[f"{prefix}_long_short_ratio"] = format_float(ratio)
    return row


def parse_metrics_zip(path: Path, buckets: dict[str, dict[int, dict[str, Any]]]) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "path": display(path),
        "date": path.stem[-10:],
        "rows_seen": 0,
        "rows_valid": 0,
        "error": None,
    }
    try:
        with ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.endswith(".csv")]
            if not names:
                stats["error"] = "no_csv_in_zip"
                return stats
            with archive.open(names[0]) as binary:
                reader = csv.DictReader(io.TextIOWrapper(binary, encoding="utf-8-sig", newline=""))
                for source in reader:
                    stats["rows_seen"] += 1
                    timestamp = parse_time_ms(str(source.get("create_time") or ""))
                    ratios = {name: safe_float(source.get(column)) for name, column in RATIO_COLUMNS.items()}
                    if timestamp is None or any(value is None for value in ratios.values()):
                        continue
                    stats["rows_valid"] += 1
                    clean_ratios = {name: float(value) for name, value in ratios.items() if value is not None}
                    for interval, interval_buckets in buckets.items():
                        bucket = timestamp - timestamp % INTERVAL_MS[interval]
                        # The earliest snapshot in the bar is point-in-time safe for that bar.
                        if bucket not in interval_buckets:
                            interval_buckets[bucket] = metric_to_row(bucket, clean_ratios)
    except (BadZipFile, OSError, UnicodeDecodeError, csv.Error) as exc:
        stats["error"] = repr(exc)
    return stats


def merge_rows(existing: list[dict[str, str]], historical: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    merged: dict[int, dict[str, Any]] = {}
    for row in historical:
        merged[int(row["timestamp"])] = {field: row.get(field, "") for field in FIELDNAMES}
    overlap = 0
    for row in existing:
        try:
            timestamp = int(row.get("timestamp") or 0)
        except ValueError:
            continue
        if timestamp <= 0:
            continue
        if timestamp in merged:
            overlap += 1
        preserved = {field: row.get(field, "") for field in FIELDNAMES}
        if not preserved.get("source"):
            preserved["source"] = "binance_futures_data_api"
        merged[timestamp] = preserved
    return [merged[key] for key in sorted(merged)], overlap


def coverage(rows: list[dict[str, Any]], interval: str) -> dict[str, Any]:
    timestamps = sorted(int(row["timestamp"]) for row in rows if row.get("timestamp"))
    if not timestamps:
        return {"rows": 0, "first": None, "last": None, "expected_rows": 0, "missing_bars": 0, "coverage_pct": 0.0}
    step = INTERVAL_MS[interval]
    expected = (timestamps[-1] - timestamps[0]) // step + 1
    missing = max(0, expected - len(set(timestamps)))
    return {
        "rows": len(timestamps),
        "first": ms_to_iso(timestamps[0]),
        "last": ms_to_iso(timestamps[-1]),
        "expected_rows": expected,
        "missing_bars": missing,
        "coverage_pct": round(100.0 * len(set(timestamps)) / expected, 6) if expected else 0.0,
    }


def yearly_quality(parse_stats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, int]] = {}
    for item in parse_stats:
        year = str(item.get("date") or "unknown")[:4]
        target = grouped.setdefault(year, {"zip_files": 0, "rows_seen": 0, "rows_valid": 0, "empty_ratio_files": 0})
        target["zip_files"] += 1
        target["rows_seen"] += int(item.get("rows_seen") or 0)
        target["rows_valid"] += int(item.get("rows_valid") or 0)
        if int(item.get("rows_valid") or 0) == 0:
            target["empty_ratio_files"] += 1
    result: list[dict[str, Any]] = []
    for year in sorted(grouped):
        item = grouped[year]
        seen = item["rows_seen"]
        result.append(
            {
                "year": year,
                **item,
                "valid_ratio_pct": round(100.0 * item["rows_valid"] / seen, 6) if seen else 0.0,
            }
        )
    return result


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Binance Vision Crowd Backfill",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Dry run: `{report.get('dry_run')}`",
        "- Source: official Binance Vision daily futures metrics archives.",
        "- Selection rule: earliest metric snapshot at each bar open; no future snapshot is used.",
        "- Trading permission: `false`.",
        "",
        "| TF | Historical | Existing | Merged | First | Last | Coverage |",
        "|---|---:|---:|---:|---|---|---:|",
    ]
    for artifact in report.get("artifacts", []):
        item = artifact.get("coverage", {})
        lines.append(
            f"| `{artifact.get('interval')}` | {artifact.get('historical_rows')} | {artifact.get('existing_rows')} | "
            f"{artifact.get('merged_rows')} | {item.get('first')} | {item.get('last')} | {item.get('coverage_pct')}% |"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Existing API rows override archive rows on overlapping timestamps.",
            "- Output writes are atomic and do not alter strategy parameters or permissions.",
            "- A longer history permits research diagnostics; it does not prove forward edge.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill crowd-positioning ratios from cached official Binance Vision metrics ZIPs.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--intervals", default="15m,1h,4h")
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2026-05-06")
    parser.add_argument("--zip-cache-dir", default="data/cache/binance_vision_metrics/daily/metrics")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out-prefix", default="docs/BINANCE_VISION_CROWD_BACKFILL_2026-06-23")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    intervals = [value.strip() for value in args.intervals.split(",") if value.strip()]
    unsupported = [value for value in intervals if value not in INTERVAL_MS]
    if unsupported:
        raise SystemExit(f"unsupported intervals: {unsupported}")
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    zip_root = resolve_path(args.zip_cache_dir) / symbol
    cache_root = resolve_path(args.cache_dir) / "futures" / symbol
    zip_paths = []
    for path in sorted(zip_root.glob(f"{symbol}-metrics-*.zip")):
        stamp = parse_zip_date(path, symbol)
        if stamp is not None and start <= stamp <= end:
            zip_paths.append(path)

    buckets: dict[str, dict[int, dict[str, Any]]] = {interval: {} for interval in intervals}
    parse_stats = [parse_metrics_zip(path, buckets) for path in zip_paths]
    parse_errors = [item for item in parse_stats if item.get("error")]
    artifacts: list[dict[str, Any]] = []
    backup_root = cache_root / "_backfill_backup" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for interval in intervals:
        historical = [buckets[interval][key] for key in sorted(buckets[interval])]
        output_path = cache_root / f"{interval}_crowd_positioning.csv"
        existing = read_csv_rows(output_path)
        merged, overlap = merge_rows(existing, historical)
        if not args.dry_run:
            if output_path.exists():
                backup_root.mkdir(parents=True, exist_ok=True)
                shutil.copy2(output_path, backup_root / output_path.name)
            write_csv_rows(output_path, merged)
        artifacts.append(
            {
                "interval": interval,
                "path": display(output_path),
                "historical_rows": len(historical),
                "existing_rows": len(existing),
                "overlap_rows_existing_preferred": overlap,
                "merged_rows": len(merged),
                "coverage": coverage(merged, interval),
                "written": not args.dry_run,
                "existing_backup": display(backup_root / output_path.name) if (not args.dry_run and existing) else None,
            }
        )

    enough_history = bool(artifacts) and all(
        int(item["historical_rows"]) >= 5_000 and float(item["coverage"].get("coverage_pct") or 0.0) >= 80.0
        for item in artifacts
    )
    decision = "dry_run_ready_for_import" if args.dry_run and enough_history and not parse_errors else "backfill_import_completed_no_orders"
    if parse_errors or not enough_history:
        decision = "backfill_blocked_insufficient_or_invalid_archives"
    report = {
        "generated_at": now_iso(),
        "engine": "BINANCE_VISION_CROWD_BACKFILLER",
        "engine_version": "1.0.0",
        "dry_run": bool(args.dry_run),
        "inputs": {
            "symbol": symbol,
            "intervals": intervals,
            "start": args.start,
            "end": args.end,
            "zip_cache_dir": display(zip_root),
            "cache_dir": display(cache_root),
            "backup_dir": display(backup_root),
        },
        "archive_provenance": {
            "source": "https://data.binance.vision/data/futures/um/daily/metrics/",
            "zip_files": len(zip_paths),
            "zip_first": zip_paths[0].name if zip_paths else None,
            "zip_last": zip_paths[-1].name if zip_paths else None,
            "parse_errors": len(parse_errors),
            "ratio_columns": RATIO_COLUMNS,
            "year_quality": yearly_quality(parse_stats),
        },
        "artifacts": artifacts,
        "parse_error_preview": parse_errors[:20],
        "decision": decision,
        "boundaries": {
            "network_used": False,
            "uses_private_credentials": False,
            "changes_strategy_parameters": False,
            "paper_execution_allowed": False,
            "live_execution_allowed": False,
            "sends_orders": False,
            "can_trade": False,
        },
        "next_action": "run without --dry-run, rerun crowd diagnostic, then keep forward lifecycle unchanged"
        if decision == "dry_run_ready_for_import"
        else "inspect archive errors or imported coverage before any research use",
        "can_trade": False,
    }
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": decision, "archives": len(zip_paths), "artifacts": artifacts, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if decision != "backfill_blocked_insufficient_or_invalid_archives" else 2


if __name__ == "__main__":
    raise SystemExit(main())

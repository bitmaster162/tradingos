#!/usr/bin/env python3
from __future__ import annotations

import argparse
from bisect import bisect_right
import csv
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.max_backtest import align_derivatives, read_ohlcv_csv  # noqa: E402
from tools.oi_funding_data_quality_collector import (  # noqa: E402
    coverage_by_year,
    latest_row_at_or_before,
    pct,
    pct_delta_available,
)


TIME_COLUMNS = (
    "timestamp",
    "time_ms",
    "open_time",
    "openTime",
    "createTime",
    "close_time",
    "closeTime",
    "date",
    "datetime",
    "time",
)
OI_COLUMNS = (
    "open_interest",
    "openInterest",
    "sumOpenInterest",
    "sum_open_interest",
    "oi",
    "open_interest_contracts",
    "openInterestAmount",
)

INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


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


def parse_timestamp_ms(value: Any) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    numeric = safe_float(raw)
    if numeric is not None:
        # Binance exports may use seconds, milliseconds or microseconds.
        if numeric > 10_000_000_000_000:
            numeric = numeric / 1000.0
        elif numeric < 10_000_000_000:
            numeric = numeric * 1000.0
        return int(numeric)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y"):
        try:
            return int(datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc).timestamp() * 1000)
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


def latest_fresh_record(
    rows: list[dict[str, Any]],
    target_ms: int,
    *,
    max_staleness_ms: int,
    timestamps: list[int] | None = None,
) -> dict[str, Any] | None:
    if timestamps is not None:
        index = bisect_right(timestamps, target_ms) - 1
        if index < 0:
            return None
        best = rows[index]
        best_ts = timestamps[index]
        return best if target_ms - best_ts <= max_staleness_ms else None
    best: dict[str, Any] | None = None
    best_ts: int | None = None
    for row in rows:
        ts = parse_timestamp_ms(row.get("timestamp"))
        if ts is None or ts > target_ms:
            continue
        if best_ts is None or ts > best_ts:
            best = row
            best_ts = ts
    if best is None or best_ts is None:
        return None
    if target_ms - best_ts > max_staleness_ms:
        return None
    return best


def raw_oi_delta_context_available(
    rows: list[dict[str, Any]],
    target_ms: int,
    *,
    interval: str,
    lookback: int,
    max_staleness_bars: float,
    timestamps: list[int] | None = None,
) -> bool:
    step_ms = INTERVAL_MS.get(interval, 14_400_000)
    max_staleness_ms = int(step_ms * max_staleness_bars)
    current = latest_fresh_record(rows, target_ms, max_staleness_ms=max_staleness_ms, timestamps=timestamps)
    previous_target = target_ms - lookback * step_ms
    previous = latest_fresh_record(rows, previous_target, max_staleness_ms=max_staleness_ms, timestamps=timestamps)
    if current is None or previous is None:
        return False
    current_value = safe_float(current.get("open_interest"))
    previous_value = safe_float(previous.get("open_interest"))
    return current_value is not None and previous_value is not None and previous_value != 0


def read_header(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            return next(reader, [])
    except (OSError, UnicodeDecodeError, csv.Error):
        return []


def read_dict_rows(path: Path, limit: int | None = None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(row)
            if limit is not None and len(rows) >= limit:
                break
    return rows


def detect_column(header: list[str], candidates: tuple[str, ...]) -> str | None:
    direct = {item.lower(): item for item in header}
    for candidate in candidates:
        if candidate.lower() in direct:
            return direct[candidate.lower()]
    for column in header:
        lowered = column.lower().replace(" ", "_").replace("-", "_")
        for candidate in candidates:
            if lowered == candidate.lower():
                return column
    return None


def normalize_oi_rows(
    path: Path,
    time_column: str | None = None,
    oi_column: str | None = None,
    max_rows: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    header = read_header(path)
    detected_time = time_column or detect_column(header, TIME_COLUMNS)
    detected_oi = oi_column or detect_column(header, OI_COLUMNS)
    stats = {
        "path": rel(path),
        "header": header,
        "time_column": detected_time,
        "oi_column": detected_oi,
        "rows_seen": 0,
        "rows_valid": 0,
        "rows_invalid_time": 0,
        "rows_invalid_oi": 0,
        "first": None,
        "last": None,
    }
    if not detected_time or not detected_oi:
        return [], stats

    rows: list[dict[str, Any]] = []
    timestamps: list[int] = []
    for row in read_dict_rows(path, max_rows):
        stats["rows_seen"] += 1
        ts = parse_timestamp_ms(row.get(detected_time))
        oi = safe_float(row.get(detected_oi))
        if ts is None:
            stats["rows_invalid_time"] += 1
            continue
        if oi is None:
            stats["rows_invalid_oi"] += 1
            continue
        rows.append({"timestamp": ts, "open_interest": oi})
        timestamps.append(ts)
    stats["rows_valid"] = len(rows)
    if timestamps:
        stats["first"] = ms_to_iso(min(timestamps))
        stats["last"] = ms_to_iso(max(timestamps))
    return rows, stats


def merge_records(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for row in existing + incoming:
        ts = parse_timestamp_ms(row.get("timestamp"))
        oi = safe_float(row.get("open_interest"))
        if ts is None or oi is None:
            continue
        merged[ts] = {"timestamp": ts, "open_interest": oi}
    return [merged[key] for key in sorted(merged)]


def write_oi_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "open_interest"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def normalize_funding_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for row in read_dict_rows(path):
        ts = parse_timestamp_ms(row.get("timestamp") or row.get("fundingTime") or row.get("time"))
        funding = safe_float(row.get("funding") or row.get("fundingRate"))
        price = safe_float(row.get("price") or row.get("markPrice"))
        if ts is None or funding is None:
            continue
        item: dict[str, Any] = {"timestamp": ts, "funding": funding}
        if price is not None:
            item["price"] = price
        rows.append(item)
    return sorted(rows, key=lambda item: item["timestamp"])


def simulated_post_import_coverage(
    *,
    cache_dir: Path,
    symbol: str,
    interval: str,
    merged_oi: list[dict[str, Any]],
    replay_trades_csv: Path,
    oi_lookback: int,
    max_oi_staleness_bars: float = 1.0,
) -> dict[str, Any]:
    symbol_dir = cache_dir / "futures" / symbol.upper()
    kline_path = symbol_dir / f"{interval}_klines.csv"
    funding_path = symbol_dir / "funding_raw.csv"
    if not kline_path.exists():
        return {
            "available": False,
            "reason": f"missing_kline_cache:{rel(kline_path)}",
        }
    kline_rows = read_ohlcv_csv(kline_path)
    funding_rows = normalize_funding_rows(funding_path)
    aligned_rows = align_derivatives(kline_rows, interval=interval, oi_records=merged_oi, funding_records=funding_rows)
    aligned_oi_rows = sum(1 for row in aligned_rows if safe_float(row.get("open_interest")) is not None)
    aligned_funding_rows = sum(1 for row in aligned_rows if safe_float(row.get("funding")) is not None)

    trades = read_dict_rows(replay_trades_csv) if replay_trades_csv.exists() else []
    oi_timestamps = [int(row["timestamp"]) for row in merged_oi]
    funding_available = 0
    oi_raw_available = 0
    oi_context_available = 0
    full_context_available = 0
    for trade in trades:
        row = latest_row_at_or_before(aligned_rows, trade.get("entry_ts", ""))
        has_funding = bool(row and safe_float(row.get("funding")) is not None)
        has_oi_raw = bool(row and safe_float(row.get("open_interest")) is not None)
        entry_ms = parse_timestamp_ms(trade.get("entry_ts"))
        has_oi_context = bool(
            entry_ms is not None
            and raw_oi_delta_context_available(
                merged_oi,
                entry_ms,
                interval=interval,
                lookback=oi_lookback,
                max_staleness_bars=max_oi_staleness_bars,
                timestamps=oi_timestamps,
            )
        )
        funding_available += int(has_funding)
        oi_raw_available += int(has_oi_raw)
        oi_context_available += int(has_oi_context)
        full_context_available += int(has_funding and has_oi_context)

    minimum_full_context = max(30, int(len(trades) * 0.5)) if trades else 30
    minimum_aligned_oi = max(100, int(len(aligned_rows) * 0.1)) if aligned_rows else 100
    classification = "post_import_candidate_coverage_ready"
    if full_context_available < minimum_full_context:
        classification = "post_import_blocked_insufficient_replay_context"
    if aligned_oi_rows < minimum_aligned_oi:
        classification = "post_import_blocked_sparse_aligned_oi"

    return {
        "available": True,
        "classification": classification,
        "kline_rows": len(kline_rows),
        "funding_rows": len(funding_rows),
        "aligned_rows": len(aligned_rows),
        "aligned_oi_rows": aligned_oi_rows,
        "aligned_funding_rows": aligned_funding_rows,
        "aligned_oi_coverage_pct": pct(aligned_oi_rows, len(aligned_rows)),
        "aligned_funding_coverage_pct": pct(aligned_funding_rows, len(aligned_rows)),
        "trades": len(trades),
        "minimum_full_context": minimum_full_context,
        "max_oi_staleness_bars": max_oi_staleness_bars,
        "full_context_available": full_context_available,
        "full_context_coverage_pct": pct(full_context_available, len(trades)),
        "funding_available": funding_available,
        "oi_raw_available": oi_raw_available,
        "oi_context_available": oi_context_available,
        "funding_coverage_pct": pct(funding_available, len(trades)),
        "oi_raw_coverage_pct": pct(oi_raw_available, len(trades)),
        "oi_context_coverage_pct": pct(oi_context_available, len(trades)),
        "coverage_by_year": coverage_by_year(trades, aligned_rows, oi_lookback),
    }


def scan_csv_files(directories: list[Path], max_files: int, sample_rows: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for directory in directories:
        if not directory.exists():
            continue
        for path in directory.rglob("*.csv"):
            if len(candidates) >= max_files:
                return candidates
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            header = read_header(path)
            if not header:
                continue
            time_column = detect_column(header, TIME_COLUMNS)
            oi_column = detect_column(header, OI_COLUMNS)
            keyword_hit = any(token in path.name.lower() for token in ("oi", "open", "interest", "funding"))
            if not oi_column and not keyword_hit:
                continue
            rows, stats = normalize_oi_rows(path, time_column=time_column, oi_column=oi_column, max_rows=sample_rows)
            stats["candidate"] = bool(time_column and oi_column and rows)
            stats["length_bytes"] = path.stat().st_size
            candidates.append(stats)
    return candidates


def run_quality_collector(out_prefix: str) -> dict[str, Any]:
    command = [
        sys.executable,
        str(ROOT / "tools" / "oi_funding_data_quality_collector.py"),
        "--no-fetch",
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
    lines = [
        "# Historical OI Importer",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Public/local CSV data only.",
        "- No private credentials, no account access, no orders.",
        "- Import mode only writes canonical raw OI cache after explicit `--input`.",
        "",
        "## Summary",
        "",
    ]
    for key, value in report.get("summary", {}).items():
        lines.append(f"- {key}: `{value}`.")
    if report.get("scan"):
        lines.extend(["", "## Scan Candidates", ""])
        lines.append("| candidate | rows_valid | first | last | path |")
        lines.append("|---|---:|---|---|---|")
        for item in report["scan"][:50]:
            lines.append(
                f"| {item.get('candidate')} | {item.get('rows_valid')} | {item.get('first')} | "
                f"{item.get('last')} | `{item.get('path')}` |"
            )
    if report.get("import"):
        imp = report["import"]
        lines.extend(["", "## Import", ""])
        for key, value in imp.items():
            if key not in {"quality_collector", "simulated_post_import_coverage"}:
                lines.append(f"- {key}: `{value}`.")
        simulated = imp.get("simulated_post_import_coverage")
        if isinstance(simulated, dict):
            lines.extend(["", "## Simulated Post-Import Coverage", ""])
            for key in [
                "classification",
                "aligned_oi_coverage_pct",
                "aligned_funding_coverage_pct",
                "trades",
                "minimum_full_context",
                "full_context_available",
                "full_context_coverage_pct",
                "oi_context_available",
                "oi_context_coverage_pct",
            ]:
                if key in simulated:
                    lines.append(f"- {key}: `{simulated.get(key)}`.")
            if simulated.get("coverage_by_year"):
                lines.extend(["", "### Simulated Coverage By Year", ""])
                lines.append("| year | trades | funding | oi_raw | oi_context | oi_context_% |")
                lines.append("|---|---:|---:|---:|---:|---:|")
                for row in simulated["coverage_by_year"]:
                    lines.append(
                        f"| {row.get('year')} | {row.get('trades')} | {row.get('funding_available')} | "
                        f"{row.get('oi_raw_available')} | {row.get('oi_context_available')} | {row.get('oi_context_coverage_pct')} |"
                    )
    lines.extend(["", "## Decision", "", f"- `{report.get('decision')}`.", ""])
    lines.extend(["## Next Action", "", f"- `{report.get('next_action')}`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan or import historical OI CSV into canonical BTCUSDT cache")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    scan = subparsers.add_parser("scan", help="Scan local folders for plausible historical OI CSV files")
    scan.add_argument("--scan-dir", action="append", default=["data", str(Path.home() / "Downloads")])
    scan.add_argument("--max-files", type=int, default=120)
    scan.add_argument("--sample-rows", type=int, default=500)
    scan.add_argument("--out-prefix", default="docs/HISTORICAL_OI_IMPORT_SCAN_2026-06-15")

    imp = subparsers.add_parser("import", help="Import one explicit CSV into canonical raw OI cache")
    imp.add_argument("--input", required=True)
    imp.add_argument("--symbol", default="BTCUSDT")
    imp.add_argument("--interval", default="4h")
    imp.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    imp.add_argument("--time-column")
    imp.add_argument("--oi-column")
    imp.add_argument("--oi-lookback", type=int, default=12)
    imp.add_argument("--max-oi-staleness-bars", type=float, default=1.0)
    imp.add_argument("--replay-trades-csv", default="docs/STRATEGY_MIX_PAPER_REPLAY_2026-06-08_trades.csv")
    imp.add_argument("--dry-run", action="store_true")
    imp.add_argument("--out-prefix", default="docs/HISTORICAL_OI_IMPORT_2026-06-15")
    imp.add_argument("--quality-out-prefix", default="docs/OI_FUNDING_DATA_QUALITY_2026-06-15")
    args = parser.parse_args()

    if args.mode == "scan":
        scan_dirs = [resolve_path(value) for value in args.scan_dir]
        candidates = scan_csv_files(scan_dirs, args.max_files, args.sample_rows)
        good = [item for item in candidates if item.get("candidate")]
        report = {
            "generated_at": now_iso(),
            "boundary": {"can_trade": False, "sends_orders": False, "uses_private_credentials": False},
            "summary": {
                "mode": "scan",
                "scan_dirs": [rel(path) for path in scan_dirs],
                "files_reported": len(candidates),
                "valid_candidates": len(good),
                "dry_run": True,
            },
            "scan": candidates,
            "decision": "scan_only_no_cache_changes_no_orders",
            "next_action": "choose a valid candidate and run import with explicit --input, or provide a vendor OI CSV",
        }
        out_prefix = resolve_path(args.out_prefix)
    else:
        input_path = resolve_path(args.input)
        incoming, stats = normalize_oi_rows(input_path, args.time_column, args.oi_column)
        cache_dir = resolve_path(args.cache_dir)
        raw_path = cache_dir / "futures" / args.symbol.upper() / f"{args.interval}_open_interest_raw.csv"
        replay_trades_csv = resolve_path(args.replay_trades_csv)
        existing, existing_stats = normalize_oi_rows(raw_path, "timestamp", "open_interest") if raw_path.exists() else ([], {})
        merged = merge_records(existing, incoming)
        simulated = simulated_post_import_coverage(
            cache_dir=cache_dir,
            symbol=args.symbol,
            interval=args.interval,
            merged_oi=merged,
            replay_trades_csv=replay_trades_csv,
            oi_lookback=args.oi_lookback,
            max_oi_staleness_bars=args.max_oi_staleness_bars,
        )
        quality = None
        if not args.dry_run and incoming:
            write_oi_csv(raw_path, merged)
            quality = run_quality_collector(args.quality_out_prefix)
        simulated_classification = simulated.get("classification") if isinstance(simulated, dict) else None
        import_ready = simulated_classification == "post_import_candidate_coverage_ready"
        report = {
            "generated_at": now_iso(),
            "boundary": {"can_trade": False, "sends_orders": False, "uses_private_credentials": False},
            "summary": {
                "mode": "import",
                "dry_run": bool(args.dry_run),
                "incoming_valid_rows": len(incoming),
                "existing_valid_rows": len(existing),
                "merged_rows": len(merged),
                "cache_written": bool((not args.dry_run) and incoming),
                "simulated_classification": simulated_classification,
                "simulated_full_context_available": simulated.get("full_context_available") if isinstance(simulated, dict) else None,
                "simulated_trades": simulated.get("trades") if isinstance(simulated, dict) else None,
                "import_ready_for_guard_retest": import_ready,
            },
            "import": {
                "input": rel(input_path),
                "raw_cache": rel(raw_path),
                "input_time_column": stats.get("time_column"),
                "input_oi_column": stats.get("oi_column"),
                "input_first": stats.get("first"),
                "input_last": stats.get("last"),
                "existing_first": existing_stats.get("first"),
                "existing_last": existing_stats.get("last"),
                "replay_trades_csv": rel(replay_trades_csv),
                "oi_lookback": args.oi_lookback,
                "max_oi_staleness_bars": args.max_oi_staleness_bars,
                "simulated_post_import_coverage": simulated,
                "quality_collector": quality,
            },
            "decision": "import_completed_no_orders" if (not args.dry_run and incoming) else "dry_run_or_no_valid_rows_no_cache_changes",
            "next_action": "safe to retest OI guard after explicit import" if import_ready else "do not import/promote for guard yet; provide wider historical OI coverage",
        }
        out_prefix = resolve_path(args.out_prefix)

    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "md": str(md_path), "summary": report["summary"], "decision": report["decision"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

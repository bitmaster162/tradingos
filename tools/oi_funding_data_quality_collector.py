#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from bisect import bisect_right
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
    read_ohlcv_csv,
    write_ohlcv_csv,
    write_oi_csv,
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


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def parse_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def iso_to_ms(value: str) -> int | None:
    parsed = parse_time(value)
    if parsed is None:
        return None
    return int(parsed.timestamp() * 1000)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_records_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def normalize_records(rows: list[dict[str, Any]], numeric_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if item.get("timestamp") not in {None, ""}:
            try:
                item["timestamp"] = int(float(str(item["timestamp"])))
            except (TypeError, ValueError):
                continue
        for field in numeric_fields:
            if field in item:
                value = safe_float(item.get(field))
                item[field] = value if value is not None else ""
        if item.get("timestamp") not in {None, ""}:
            normalized.append(item)
    return normalized


def merge_records(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for row in existing + incoming:
        try:
            ts = int(float(str(row.get("timestamp"))))
        except (TypeError, ValueError):
            continue
        merged[ts] = {**row, "timestamp": ts}
    return [merged[key] for key in sorted(merged)]


def merge_klines(existing: list[dict[str, str]], incoming: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: dict[int, dict[str, str]] = {}
    for row in existing + incoming:
        try:
            ts = int(float(str(row.get("time_ms"))))
        except (TypeError, ValueError):
            continue
        merged[ts] = row
    return [merged[key] for key in sorted(merged)]


def row_range_ms(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values: list[int] = []
    for row in rows:
        try:
            values.append(int(float(str(row.get(key)))))
        except (TypeError, ValueError):
            continue
    if not values:
        return {"rows": len(rows), "first": None, "last": None}
    return {
        "rows": len(rows),
        "first": ms_to_iso(min(values)),
        "last": ms_to_iso(max(values)),
        "first_ms": min(values),
        "last_ms": max(values),
    }


def pct(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 3) if denominator else 0.0


def latest_row_at_or_before(rows: list[dict[str, str]], target_ts: str) -> dict[str, str] | None:
    target = parse_time(target_ts)
    if target is None:
        return None
    best: dict[str, str] | None = None
    best_time: datetime | None = None
    for row in rows:
        row_time = parse_time(row.get("time") or row.get("timestamp"))
        if row_time is None or row_time > target:
            continue
        if best_time is None or row_time >= best_time:
            best = row
            best_time = row_time
    return best


def pct_delta_available(rows: list[dict[str, str]], target_ts: str, field: str, lookback: int) -> bool:
    target = parse_time(target_ts)
    if target is None:
        return False
    best_index = -1
    best_time: datetime | None = None
    for index, row in enumerate(rows):
        row_time = parse_time(row.get("time") or row.get("timestamp"))
        if row_time is None or row_time > target:
            continue
        if best_time is None or row_time >= best_time:
            best_index = index
            best_time = row_time
    if best_index < lookback:
        return False
    current = safe_float(rows[best_index].get(field))
    previous = safe_float(rows[best_index - lookback].get(field))
    return current is not None and previous is not None and previous != 0


def latest_fresh_raw_oi(
    rows: list[dict[str, Any]],
    target_ms: int,
    *,
    max_staleness_ms: int,
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_ts: int | None = None
    for row in rows:
        try:
            ts = int(float(str(row.get("timestamp"))))
        except (TypeError, ValueError):
            continue
        if ts > target_ms:
            continue
        if best_ts is None or ts > best_ts:
            best = row
            best_ts = ts
    if best is None or best_ts is None:
        return None
    if target_ms - best_ts > max_staleness_ms:
        return None
    return best


def build_raw_oi_index(rows: list[dict[str, Any]]) -> list[tuple[int, float]]:
    index: dict[int, float] = {}
    for row in rows:
        try:
            ts = int(float(str(row.get("timestamp"))))
        except (TypeError, ValueError):
            continue
        value = safe_float(row.get("open_interest"))
        if value is None:
            continue
        index[ts] = value
    return sorted(index.items())


def latest_fresh_raw_oi_index(
    index: list[tuple[int, float]],
    target_ms: int,
    *,
    max_staleness_ms: int,
    timestamps: list[int] | None = None,
) -> tuple[int, float] | None:
    if not index:
        return None
    ts_index = timestamps if timestamps is not None else [item[0] for item in index]
    position = bisect_right(ts_index, target_ms) - 1
    if position < 0:
        return None
    ts, value = index[position]
    if target_ms - ts > max_staleness_ms:
        return None
    return ts, value


def raw_oi_delta_context_available(
    rows: list[dict[str, Any]],
    target_ts: str,
    *,
    interval: str,
    lookback: int,
    max_staleness_bars: float,
    timestamps: list[int] | None = None,
) -> bool:
    target = parse_time(target_ts)
    if target is None:
        return False
    target_ms = int(target.timestamp() * 1000)
    step_ms = INTERVAL_MS.get(interval, 14_400_000)
    max_staleness_ms = int(step_ms * max_staleness_bars)
    current = latest_fresh_raw_oi(rows, target_ms, max_staleness_ms=max_staleness_ms)
    previous = latest_fresh_raw_oi(rows, target_ms - lookback * step_ms, max_staleness_ms=max_staleness_ms)
    if current is None or previous is None:
        return False
    current_value = safe_float(current.get("open_interest"))
    previous_value = safe_float(previous.get("open_interest"))
    return current_value is not None and previous_value is not None and previous_value != 0


def raw_oi_delta_context_available_index(
    index: list[tuple[int, float]],
    target_ts: str,
    *,
    interval: str,
    lookback: int,
    max_staleness_bars: float,
    timestamps: list[int] | None = None,
) -> bool:
    target = parse_time(target_ts)
    if target is None:
        return False
    target_ms = int(target.timestamp() * 1000)
    step_ms = INTERVAL_MS.get(interval, 14_400_000)
    max_staleness_ms = int(step_ms * max_staleness_bars)
    ts_index = timestamps if timestamps is not None else [item[0] for item in index]
    current = latest_fresh_raw_oi_index(index, target_ms, max_staleness_ms=max_staleness_ms, timestamps=ts_index)
    previous = latest_fresh_raw_oi_index(index, target_ms - lookback * step_ms, max_staleness_ms=max_staleness_ms, timestamps=ts_index)
    return bool(current and previous and previous[1] != 0)


def coverage_by_year(trades: list[dict[str, str]], aligned_rows: list[dict[str, str]], oi_lookback: int) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, str]]] = {}
    for trade in trades:
        year = str(trade.get("entry_ts", ""))[:4] or "unknown"
        groups.setdefault(year, []).append(trade)
    output: list[dict[str, Any]] = []
    for year, items in sorted(groups.items()):
        funding = 0
        oi_raw = 0
        oi_context = 0
        for trade in items:
            row = latest_row_at_or_before(aligned_rows, trade.get("entry_ts", ""))
            if row and safe_float(row.get("funding")) is not None:
                funding += 1
            if row and safe_float(row.get("open_interest")) is not None:
                oi_raw += 1
            if pct_delta_available(aligned_rows, trade.get("entry_ts", ""), "open_interest", oi_lookback):
                oi_context += 1
        output.append(
            {
                "year": year,
                "trades": len(items),
                "funding_available": funding,
                "oi_raw_available": oi_raw,
                "oi_context_available": oi_context,
                "oi_context_coverage_pct": pct(oi_context, len(items)),
            }
        )
    return output


def coverage_by_year_raw(
    trades: list[dict[str, str]],
    aligned_rows: list[dict[str, str]],
    raw_oi_rows: list[dict[str, Any]],
    *,
    interval: str,
    oi_lookback: int,
    max_oi_staleness_bars: float,
    raw_oi_index: list[tuple[int, float]] | None = None,
) -> list[dict[str, Any]]:
    index = raw_oi_index if raw_oi_index is not None else build_raw_oi_index(raw_oi_rows)
    timestamps = [item[0] for item in index]
    groups: dict[str, list[dict[str, str]]] = {}
    for trade in trades:
        year = str(trade.get("entry_ts", ""))[:4] or "unknown"
        groups.setdefault(year, []).append(trade)
    output: list[dict[str, Any]] = []
    for year, items in sorted(groups.items()):
        funding = 0
        oi_raw = 0
        oi_context = 0
        for trade in items:
            row = latest_row_at_or_before(aligned_rows, trade.get("entry_ts", ""))
            if row and safe_float(row.get("funding")) is not None:
                funding += 1
            target = parse_time(trade.get("entry_ts", ""))
            if target is not None:
                step_ms = INTERVAL_MS.get(interval, 14_400_000)
                fresh = latest_fresh_raw_oi_index(
                    index,
                    int(target.timestamp() * 1000),
                    max_staleness_ms=int(step_ms * max_oi_staleness_bars),
                    timestamps=timestamps,
                )
                if fresh:
                    oi_raw += 1
            if raw_oi_delta_context_available_index(
                index,
                trade.get("entry_ts", ""),
                interval=interval,
                lookback=oi_lookback,
                max_staleness_bars=max_oi_staleness_bars,
                timestamps=timestamps,
            ):
                oi_context += 1
        output.append(
            {
                "year": year,
                "trades": len(items),
                "funding_available": funding,
                "oi_raw_available": oi_raw,
                "oi_context_available": oi_context,
                "oi_context_coverage_pct": pct(oi_context, len(items)),
            }
        )
    return output


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    endpoint = report.get("endpoint_window", {})
    replay = report.get("replay_trade_coverage", {})
    lines = [
        "# OI/Funding Data Quality Collector",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Public Binance data only.",
        "- Updates local derivatives cache and aligned CSV.",
        "- No private credentials, no account access, no orders.",
        "",
        "## Summary",
        "",
    ]
    for key in [
        "classification",
        "symbol",
        "interval",
        "kline_rows",
        "aligned_rows",
        "aligned_oi_rows",
        "aligned_funding_rows",
        "aligned_oi_coverage_pct",
        "aligned_funding_coverage_pct",
    ]:
        lines.append(f"- {key}: `{summary.get(key)}`.")
    lines.extend(["", "## Endpoint Window", ""])
    for key, value in endpoint.items():
        lines.append(f"- {key}: `{value}`.")
    lines.extend(["", "## Replay Trade Coverage", ""])
    for key in [
        "trades",
        "funding_available",
        "oi_raw_available",
        "oi_context_available",
        "full_context_available",
        "funding_coverage_pct",
        "oi_raw_coverage_pct",
        "oi_context_coverage_pct",
        "full_context_coverage_pct",
    ]:
        lines.append(f"- {key}: `{replay.get(key)}`.")
    lines.extend(["", "### Coverage By Year", ""])
    lines.append("| year | trades | funding | oi_raw | oi_context | oi_context_% |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for row in report.get("coverage_by_year", []):
        lines.append(
            f"| {row.get('year')} | {row.get('trades')} | {row.get('funding_available')} | "
            f"{row.get('oi_raw_available')} | {row.get('oi_context_available')} | {row.get('oi_context_coverage_pct')} |"
        )
    lines.extend(["", "## Decision", "", f"- `{report.get('decision')}`.", ""])
    lines.extend(["## Next Action", "", f"- `{report.get('next_action')}`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="OI/funding data-quality collector and cache backfill")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="4h")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--pages", type=int, default=20)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--funding-pages", type=int, default=20)
    parser.add_argument("--kline-pages", type=int, default=3)
    parser.add_argument("--oi-lookback", type=int, default=12)
    parser.add_argument("--max-oi-staleness-bars", type=float, default=1.0)
    parser.add_argument("--replay-trades-csv", default="docs/STRATEGY_MIX_PAPER_REPLAY_2026-06-08_trades.csv")
    parser.add_argument("--out-prefix", default="docs/OI_FUNDING_DATA_QUALITY_2026-06-15")
    parser.add_argument("--no-fetch", action="store_true", help="Only audit existing local cache")
    parser.add_argument("--no-refresh-klines", action="store_true", help="Do not update futures kline cache before alignment")
    args = parser.parse_args()

    cache_dir = resolve_path(args.cache_dir)
    symbol_dir = cache_dir / "futures" / args.symbol.upper()
    kline_path = symbol_dir / f"{args.interval}_klines.csv"
    raw_oi_path = symbol_dir / f"{args.interval}_open_interest_raw.csv"
    funding_path = symbol_dir / "funding_raw.csv"
    aligned_path = symbol_dir / f"{args.interval}_oi_aligned.csv"
    replay_path = resolve_path(args.replay_trades_csv)
    out_prefix = resolve_path(args.out_prefix)

    kline_rows = read_ohlcv_csv(kline_path) if kline_path.exists() else []
    incoming_klines: list[dict[str, str]] = []
    existing_oi = normalize_records(read_csv_rows(raw_oi_path), ("open_interest",))
    existing_funding = normalize_records(read_csv_rows(funding_path), ("funding", "price"))
    incoming_oi: list[dict[str, Any]] = []
    incoming_funding: list[dict[str, Any]] = []

    if not args.no_fetch and not args.no_refresh_klines:
        incoming_klines = fetch_binance_klines(args.symbol, args.interval, args.limit, "futures", pages=args.kline_pages)
        kline_rows = merge_klines(kline_rows, incoming_klines)
        write_ohlcv_csv(kline_path, kline_rows)

    if not args.no_fetch:
        incoming_oi = fetch_open_interest_history(args.symbol, args.interval, args.limit, pages=args.pages)
        incoming_funding = fetch_funding_history(args.symbol, limit=1000, pages=args.funding_pages)

    merged_oi = merge_records(existing_oi, incoming_oi)
    merged_funding = merge_records(existing_funding, incoming_funding)
    write_records_csv(raw_oi_path, merged_oi, ["timestamp", "open_interest"])
    write_records_csv(funding_path, merged_funding, ["timestamp", "funding", "price"])

    aligned_rows: list[dict[str, str]] = []
    if kline_rows:
        aligned_rows = align_derivatives(kline_rows, interval=args.interval, oi_records=merged_oi, funding_records=merged_funding)
        write_oi_csv(aligned_path, aligned_rows)

    aligned_oi_rows = sum(1 for row in aligned_rows if safe_float(row.get("open_interest")) is not None)
    aligned_funding_rows = sum(1 for row in aligned_rows if safe_float(row.get("funding")) is not None)
    raw_oi_index = build_raw_oi_index(merged_oi)
    raw_oi_timestamps = [item[0] for item in raw_oi_index]

    trades = read_csv_rows(replay_path)
    funding_available = 0
    oi_raw_available = 0
    oi_context_available = 0
    full_context_available = 0
    for trade in trades:
        row = latest_row_at_or_before(aligned_rows, trade.get("entry_ts", ""))
        has_funding = bool(row and safe_float(row.get("funding")) is not None)
        target = parse_time(trade.get("entry_ts", ""))
        fresh_oi = None
        if target is not None:
            fresh_oi = latest_fresh_raw_oi_index(
                raw_oi_index,
                int(target.timestamp() * 1000),
                max_staleness_ms=int(INTERVAL_MS.get(args.interval, 14_400_000) * args.max_oi_staleness_bars),
                timestamps=raw_oi_timestamps,
            )
        has_oi_raw = bool(fresh_oi)
        has_oi_context = raw_oi_delta_context_available_index(
            raw_oi_index,
            trade.get("entry_ts", ""),
            interval=args.interval,
            lookback=args.oi_lookback,
            max_staleness_bars=args.max_oi_staleness_bars,
            timestamps=raw_oi_timestamps,
        )
        funding_available += int(has_funding)
        oi_raw_available += int(has_oi_raw)
        oi_context_available += int(has_oi_context)
        full_context_available += int(has_funding and has_oi_context)

    kline_range = row_range_ms(kline_rows, "time_ms")
    oi_range = row_range_ms(merged_oi, "timestamp")
    funding_range = row_range_ms(merged_funding, "timestamp")
    endpoint_days = None
    if oi_range.get("first_ms") and oi_range.get("last_ms"):
        endpoint_days = round((int(oi_range["last_ms"]) - int(oi_range["first_ms"])) / 86_400_000, 3)

    classification = "oi_guard_data_ready"
    if not trades or full_context_available < max(30, int(len(trades) * 0.5)):
        classification = "oi_guard_blocked_insufficient_trade_context"
    if aligned_oi_rows < max(100, int(len(aligned_rows) * 0.1)):
        classification = "oi_guard_blocked_sparse_historical_oi"

    decision = "observe_only_no_guard_promotion_no_orders"
    next_action = "use forward collector until enough OI-context entry outcomes exist"
    if classification == "oi_guard_blocked_sparse_historical_oi":
        next_action = "standard Binance OI endpoint covers only the recent window in this cache; use forward OI collection or add a separate historical OI vendor/source before guard promotion"
    elif classification == "oi_guard_data_ready":
        next_action = "historical OI/funding context is ready for offline guard validation and forward observation; no orders are permitted"

    report = {
        "generated_at": now_iso(),
        "boundary": {
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "network_required_unless_no_fetch": not args.no_fetch,
        },
        "inputs": {
            "symbol": args.symbol,
            "interval": args.interval,
            "cache_dir": str(cache_dir.relative_to(ROOT) if cache_dir.is_relative_to(ROOT) else cache_dir),
            "pages": args.pages,
            "limit": args.limit,
            "funding_pages": args.funding_pages,
            "kline_pages": args.kline_pages,
            "oi_lookback": args.oi_lookback,
            "max_oi_staleness_bars": args.max_oi_staleness_bars,
            "replay_trades_csv": str(replay_path.relative_to(ROOT) if replay_path.is_relative_to(ROOT) else replay_path),
            "no_fetch": args.no_fetch,
            "refresh_klines": not args.no_refresh_klines,
        },
        "artifacts": {
            "raw_oi_csv": str(raw_oi_path.relative_to(ROOT) if raw_oi_path.is_relative_to(ROOT) else raw_oi_path),
            "funding_csv": str(funding_path.relative_to(ROOT) if funding_path.is_relative_to(ROOT) else funding_path),
            "aligned_csv": str(aligned_path.relative_to(ROOT) if aligned_path.is_relative_to(ROOT) else aligned_path),
        },
        "summary": {
            "classification": classification,
            "symbol": args.symbol,
            "interval": args.interval,
            "kline_rows": len(kline_rows),
            "incoming_kline_rows": len(incoming_klines),
            "incoming_oi_rows": len(incoming_oi),
            "merged_oi_rows": len(merged_oi),
            "incoming_funding_rows": len(incoming_funding),
            "merged_funding_rows": len(merged_funding),
            "aligned_rows": len(aligned_rows),
            "aligned_oi_rows": aligned_oi_rows,
            "aligned_funding_rows": aligned_funding_rows,
            "aligned_oi_coverage_pct": pct(aligned_oi_rows, len(aligned_rows)),
            "aligned_funding_coverage_pct": pct(aligned_funding_rows, len(aligned_rows)),
        },
        "endpoint_window": {
            "kline_first": kline_range.get("first"),
            "kline_last": kline_range.get("last"),
            "oi_first": oi_range.get("first"),
            "oi_last": oi_range.get("last"),
            "oi_window_days": endpoint_days,
            "funding_first": funding_range.get("first"),
            "funding_last": funding_range.get("last"),
        },
        "replay_trade_coverage": {
            "trades": len(trades),
            "funding_available": funding_available,
            "oi_raw_available": oi_raw_available,
            "oi_context_available": oi_context_available,
            "full_context_available": full_context_available,
            "funding_coverage_pct": pct(funding_available, len(trades)),
            "oi_raw_coverage_pct": pct(oi_raw_available, len(trades)),
            "oi_context_coverage_pct": pct(oi_context_available, len(trades)),
            "full_context_coverage_pct": pct(full_context_available, len(trades)),
        },
        "coverage_by_year": coverage_by_year_raw(
            trades,
            aligned_rows,
            merged_oi,
            interval=args.interval,
            oi_lookback=args.oi_lookback,
            max_oi_staleness_bars=args.max_oi_staleness_bars,
            raw_oi_index=raw_oi_index,
        ),
        "decision": decision,
        "next_action": next_action,
    }

    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "md": str(md_path), "summary": report["summary"], "replay_trade_coverage": report["replay_trade_coverage"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

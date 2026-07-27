#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


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


def parse_time_ms(value: Any) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    numeric = safe_float(raw)
    if numeric is not None:
        if numeric > 10_000_000_000_000:
            numeric = numeric / 1000.0
        elif numeric < 10_000_000_000:
            numeric = numeric * 1000.0
        return int(numeric)
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp() * 1000)
    except ValueError:
        return None


def ms_to_iso(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def pct(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 3) if denominator else 0.0


def interval_ms(interval: str) -> int:
    table = {
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
    return table.get(interval, 14_400_000)


def load_bars(kline_path: Path, aligned_path: Path, interval: str) -> list[dict[str, Any]]:
    klines = read_csv_rows(kline_path)
    aligned = read_csv_rows(aligned_path)
    step_ms = interval_ms(interval)
    bars: list[dict[str, Any]] = []
    for index, row in enumerate(klines):
        open_ms = parse_time_ms(row.get("time_ms") or row.get("open_time") or row.get("time"))
        if open_ms is None:
            continue
        aligned_row = aligned[index] if index < len(aligned) else {}
        oi = safe_float(aligned_row.get("open_interest"))
        funding = safe_float(aligned_row.get("funding"))
        bars.append(
            {
                "index": len(bars),
                "time": row.get("time") or ms_to_iso(open_ms),
                "open_ms": open_ms,
                "close_ms": open_ms + step_ms - 1,
                "open_interest": oi,
                "funding": funding,
                "oi_present": oi is not None,
                "funding_present": funding is not None,
            }
        )
    return bars


def contiguous_gaps(bars: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    start: dict[str, Any] | None = None
    last: dict[str, Any] | None = None
    count = 0
    for bar in bars:
        present = bool(bar.get(field))
        if not present:
            if start is None:
                start = bar
                count = 0
            last = bar
            count += 1
        elif start is not None and last is not None:
            gaps.append(
                {
                    "start": start["time"],
                    "end": last["time"],
                    "start_ms": start["open_ms"],
                    "end_ms": last["close_ms"],
                    "bars": count,
                }
            )
            start = None
            last = None
            count = 0
    if start is not None and last is not None:
        gaps.append(
            {
                "start": start["time"],
                "end": last["time"],
                "start_ms": start["open_ms"],
                "end_ms": last["close_ms"],
                "bars": count,
            }
        )
    return gaps


def merge_intervals(intervals: list[dict[str, Any]], *, step_ms: int) -> list[dict[str, Any]]:
    if not intervals:
        return []
    sorted_items = sorted(intervals, key=lambda item: (int(item["start_ms"]), int(item["end_ms"])))
    merged: list[dict[str, Any]] = []
    current = dict(sorted_items[0])
    trade_ids: set[str] = set(current.get("trade_ids", []))
    for item in sorted_items[1:]:
        if int(item["start_ms"]) <= int(current["end_ms"]) + step_ms:
            current["end_ms"] = max(int(current["end_ms"]), int(item["end_ms"]))
            current["bars"] = int(round((int(current["end_ms"]) - int(current["start_ms"]) + 1) / step_ms))
            trade_ids.update(item.get("trade_ids", []))
        else:
            current["bars"] = int(round((int(current["end_ms"]) - int(current["start_ms"]) + 1) / step_ms))
            current["start"] = ms_to_iso(int(current["start_ms"]))
            current["end"] = ms_to_iso(int(current["end_ms"]) - step_ms + 1)
            current["trades_covered"] = len(trade_ids)
            merged.append(current)
            current = dict(item)
            trade_ids = set(current.get("trade_ids", []))
    current["bars"] = int(round((int(current["end_ms"]) - int(current["start_ms"]) + 1) / step_ms))
    current["start"] = ms_to_iso(int(current["start_ms"]))
    current["end"] = ms_to_iso(int(current["end_ms"]) - step_ms + 1)
    current["trades_covered"] = len(trade_ids)
    merged.append(current)
    return merged


def find_bar_index(open_times: list[int], ts_ms: int) -> int:
    return bisect_right(open_times, ts_ms) - 1


def replay_requirements(
    bars: list[dict[str, Any]],
    trades: list[dict[str, str]],
    *,
    lookback: int,
    step_ms: int,
) -> dict[str, Any]:
    open_times = [int(bar["open_ms"]) for bar in bars]
    rows: list[dict[str, Any]] = []
    delta_missing_intervals: list[dict[str, Any]] = []
    strict_missing_intervals: list[dict[str, Any]] = []

    for trade_index, trade in enumerate(trades):
        entry_ms = parse_time_ms(trade.get("entry_ts"))
        if entry_ms is None:
            continue
        idx = find_bar_index(open_times, entry_ms)
        if idx < 0 or idx >= len(bars):
            continue
        prev_idx = idx - lookback
        has_current = bool(bars[idx]["oi_present"])
        has_prev = prev_idx >= 0 and bool(bars[prev_idx]["oi_present"])
        delta_context = has_current and has_prev
        strict_start = max(0, prev_idx)
        strict_indices = list(range(strict_start, idx + 1))
        strict_context = prev_idx >= 0 and all(bool(bars[item]["oi_present"]) for item in strict_indices)
        missing_strict = [item for item in strict_indices if not bool(bars[item]["oi_present"])]

        trade_id = f"{trade_index}:{trade.get('entry_ts')}"
        if not delta_context:
            missing_indices = []
            if prev_idx >= 0 and not has_prev:
                missing_indices.append(prev_idx)
            if not has_current:
                missing_indices.append(idx)
            if missing_indices:
                delta_missing_intervals.append(
                    {
                        "start_ms": min(bars[item]["open_ms"] for item in missing_indices),
                        "end_ms": max(bars[item]["close_ms"] for item in missing_indices),
                        "bars": len(missing_indices),
                        "trade_ids": [trade_id],
                    }
                )
        if not strict_context and missing_strict:
            strict_missing_intervals.append(
                {
                    "start_ms": min(bars[item]["open_ms"] for item in missing_strict),
                    "end_ms": max(bars[item]["close_ms"] for item in missing_strict),
                    "bars": len(missing_strict),
                    "trade_ids": [trade_id],
                }
            )

        rows.append(
            {
                "entry_ts": trade.get("entry_ts"),
                "year": str(trade.get("entry_ts", ""))[:4],
                "bar_index": idx,
                "lookback_index": prev_idx,
                "delta_context_available": delta_context,
                "strict_context_available": strict_context,
                "missing_strict_bars": len(missing_strict),
            }
        )

    return {
        "rows": rows,
        "delta_missing_windows": merge_intervals(delta_missing_intervals, step_ms=step_ms),
        "strict_missing_windows": merge_intervals(strict_missing_intervals, step_ms=step_ms),
    }


def summarize_by_year(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("year") or "unknown"), []).append(row)
    output: list[dict[str, Any]] = []
    for year, items in sorted(grouped.items()):
        delta = sum(1 for item in items if item.get("delta_context_available"))
        strict = sum(1 for item in items if item.get("strict_context_available"))
        output.append(
            {
                "year": year,
                "trades": len(items),
                "delta_context_available": delta,
                "delta_context_pct": pct(delta, len(items)),
                "strict_context_available": strict,
                "strict_context_pct": pct(strict, len(items)),
            }
        )
    return output


def write_vendor_request(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["priority", "scope", "start", "end", "bars", "trades_covered", "reason"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# Historical OI Gap Plan",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Data planning only.",
        "- No private credentials, no account access, no orders.",
        "- Does not import data and does not promote OI/funding into a guard.",
        "",
        "## Summary",
        "",
    ]
    for key, value in summary.items():
        lines.append(f"- {key}: `{value}`.")
    lines.extend(["", "## Replay Coverage By Year", ""])
    lines.append("| year | trades | delta_context | delta_% | strict_context | strict_% |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for row in report.get("replay_coverage_by_year", []):
        lines.append(
            f"| {row.get('year')} | {row.get('trades')} | {row.get('delta_context_available')} | "
            f"{row.get('delta_context_pct')} | {row.get('strict_context_available')} | {row.get('strict_context_pct')} |"
        )
    lines.extend(["", "## Vendor Request Rows", ""])
    lines.append("| priority | scope | start | end | bars | trades | reason |")
    lines.append("|---:|---|---|---|---:|---:|---|")
    for row in report.get("vendor_request_preview", []):
        lines.append(
            f"| {row.get('priority')} | {row.get('scope')} | {row.get('start')} | {row.get('end')} | "
            f"{row.get('bars')} | {row.get('trades_covered')} | {row.get('reason')} |"
        )
    lines.extend(["", "## Decision", "", f"- `{report.get('decision')}`.", ""])
    lines.extend(["## Next Action", "", f"- `{report.get('next_action')}`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan historical OI gaps needed for replay/guard validation")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="4h")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--replay-trades-csv", default="docs/STRATEGY_MIX_PAPER_REPLAY_2026-06-08_trades.csv")
    parser.add_argument("--oi-lookback", type=int, default=12)
    parser.add_argument("--out-prefix", default="docs/HISTORICAL_OI_GAP_PLAN_2026-06-15")
    args = parser.parse_args()

    cache_dir = resolve_path(args.cache_dir)
    symbol_dir = cache_dir / "futures" / args.symbol.upper()
    kline_path = symbol_dir / f"{args.interval}_klines.csv"
    aligned_path = symbol_dir / f"{args.interval}_oi_aligned.csv"
    replay_path = resolve_path(args.replay_trades_csv)
    out_prefix = resolve_path(args.out_prefix)
    vendor_csv = out_prefix.with_name(out_prefix.name + "_vendor_request.csv")

    step_ms = interval_ms(args.interval)
    bars = load_bars(kline_path, aligned_path, args.interval)
    trades = read_csv_rows(replay_path)
    oi_present = sum(1 for bar in bars if bar["oi_present"])
    funding_present = sum(1 for bar in bars if bar["funding_present"])
    missing_gaps = contiguous_gaps(bars, "oi_present")
    replay = replay_requirements(bars, trades, lookback=args.oi_lookback, step_ms=step_ms)
    replay_rows = replay["rows"]
    delta_available = sum(1 for row in replay_rows if row.get("delta_context_available"))
    strict_available = sum(1 for row in replay_rows if row.get("strict_context_available"))
    minimum_full_context = max(30, int(len(replay_rows) * 0.5)) if replay_rows else 30

    vendor_rows: list[dict[str, Any]] = []
    if bars:
        first_replay_ms = parse_time_ms(trades[0].get("entry_ts")) if trades else None
        last_replay_ms = parse_time_ms(trades[-1].get("entry_ts")) if trades else None
        if first_replay_ms is not None and last_replay_ms is not None:
            first_idx = max(0, find_bar_index([bar["open_ms"] for bar in bars], first_replay_ms) - args.oi_lookback)
            last_idx = find_bar_index([bar["open_ms"] for bar in bars], last_replay_ms)
            if last_idx >= first_idx:
                vendor_rows.append(
                    {
                        "priority": 1,
                        "scope": "full_replay_window",
                        "start": bars[first_idx]["time"],
                        "end": bars[last_idx]["time"],
                        "bars": last_idx - first_idx + 1,
                        "trades_covered": len(replay_rows),
                        "reason": "preferred unbiased replay OI coverage",
                    }
                )
    for index, row in enumerate(replay["delta_missing_windows"][:20], start=2):
        vendor_rows.append(
            {
                "priority": index,
                "scope": "minimum_delta_context_window",
                "start": row.get("start"),
                "end": row.get("end"),
                "bars": row.get("bars"),
                "trades_covered": row.get("trades_covered"),
                "reason": "minimum current-vs-lookback OI context for replay entries",
            }
        )
    for index, row in enumerate(missing_gaps[:10], start=100):
        vendor_rows.append(
            {
                "priority": index,
                "scope": "full_cache_gap",
                "start": row.get("start"),
                "end": row.get("end"),
                "bars": row.get("bars"),
                "trades_covered": "",
                "reason": "full historical aligned OI cache gap",
            }
        )

    write_vendor_request(vendor_csv, vendor_rows)
    decision = "historical_oi_required_before_guard_retest"
    if delta_available >= minimum_full_context:
        decision = "replay_delta_context_ready_for_guard_retest"
    report = {
        "generated_at": now_iso(),
        "boundary": {
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "imports_data": False,
        },
        "inputs": {
            "symbol": args.symbol,
            "interval": args.interval,
            "cache_dir": rel(cache_dir),
            "kline_csv": rel(kline_path),
            "aligned_csv": rel(aligned_path),
            "replay_trades_csv": rel(replay_path),
            "oi_lookback": args.oi_lookback,
        },
        "artifacts": {
            "vendor_request_csv": rel(vendor_csv),
        },
        "summary": {
            "symbol": args.symbol,
            "interval": args.interval,
            "kline_bars": len(bars),
            "oi_present_bars": oi_present,
            "oi_coverage_pct": pct(oi_present, len(bars)),
            "funding_present_bars": funding_present,
            "funding_coverage_pct": pct(funding_present, len(bars)),
            "oi_missing_gaps": len(missing_gaps),
            "replay_trades": len(replay_rows),
            "minimum_full_context": minimum_full_context,
            "delta_context_available": delta_available,
            "delta_context_pct": pct(delta_available, len(replay_rows)),
            "strict_context_available": strict_available,
            "strict_context_pct": pct(strict_available, len(replay_rows)),
            "delta_missing_windows": len(replay["delta_missing_windows"]),
            "strict_missing_windows": len(replay["strict_missing_windows"]),
        },
        "oi_missing_gaps_preview": missing_gaps[:20],
        "replay_coverage_by_year": summarize_by_year(replay_rows),
        "delta_missing_windows_preview": replay["delta_missing_windows"][:20],
        "strict_missing_windows_preview": replay["strict_missing_windows"][:20],
        "vendor_request_preview": vendor_rows[:40],
        "decision": decision,
        "next_action": "obtain BTCUSDT USD-M futures OI CSV for full_replay_window first, dry-run it through historical_oi_importer, then import only if coverage improves",
    }

    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "md": str(md_path), "vendor_request_csv": str(vendor_csv), "summary": report["summary"], "decision": decision, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

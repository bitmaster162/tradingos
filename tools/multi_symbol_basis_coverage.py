#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}
PANEL_FIELDS = [
    "symbol",
    "time",
    "time_ms",
    "spot_open",
    "spot_close",
    "perp_open",
    "perp_close",
    "basis_open_bps",
    "basis_close_bps",
    "spot_volume",
    "perp_volume",
    "funding_event_bps",
]


@dataclass(frozen=True)
class CsvHealth:
    path: str
    exists: bool
    rows: int
    first_time: str | None
    last_time: str | None
    columns: list[str]
    error: str | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def ms_to_iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat()


def parse_time_ms(row: dict[str, str]) -> int | None:
    raw_ms = row.get("time_ms")
    if raw_ms not in (None, ""):
        try:
            parsed = int(float(raw_ms))
        except ValueError:
            parsed = 0
        if 0 < parsed < 10_000_000_000_000:
            return parsed
    raw_time = row.get("time")
    if raw_time:
        try:
            return int(parse_iso(raw_time).timestamp() * 1000)
        except ValueError:
            return None
    return None


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str], str | None]:
    if not path.exists():
        return [], [], "missing"
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            return list(reader), list(reader.fieldnames or []), None
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        return [], [], repr(exc)


def csv_health(path: Path) -> CsvHealth:
    rows, columns, error = read_csv(path)
    keyed = [(parse_time_ms(row), row) for row in rows]
    times = sorted(time for time, _ in keyed if time is not None)
    return CsvHealth(
        path=portable_path(path),
        exists=path.exists(),
        rows=len(rows),
        first_time=ms_to_iso(times[0]) if times else None,
        last_time=ms_to_iso(times[-1]) if times else None,
        columns=columns,
        error=error,
    )


def keyed_klines(path: Path) -> tuple[dict[int, dict[str, str]], CsvHealth]:
    health = csv_health(path)
    rows, _, _ = read_csv(path)
    keyed: dict[int, dict[str, str]] = {}
    required = {"open", "close"}
    if not required.issubset(set(health.columns)):
        return keyed, CsvHealth(**{**health.__dict__, "error": health.error or "missing_required_ohlcv_columns"})
    for row in rows:
        time_ms = parse_time_ms(row)
        if time_ms is None:
            continue
        open_value = safe_float(row.get("open"))
        close_value = safe_float(row.get("close"))
        if open_value is None or close_value is None or min(open_value, close_value) <= 0:
            continue
        keyed[time_ms] = row
    return keyed, health


def funding_by_hour(path: Path) -> tuple[dict[int, float], CsvHealth]:
    health = csv_health(path)
    rows, _, _ = read_csv(path)
    keyed: dict[int, float] = {}
    if not {"timestamp", "funding"}.issubset(set(health.columns)):
        return keyed, CsvHealth(**{**health.__dict__, "error": health.error or "missing_required_funding_columns"})
    for row in rows:
        try:
            timestamp = int(float(str(row.get("timestamp", ""))))
        except ValueError:
            continue
        if timestamp > 10_000_000_000_000:
            timestamp //= 1000
        rate = safe_float(row.get("funding"))
        if timestamp > 0 and rate is not None:
            keyed[timestamp - timestamp % 3_600_000] = rate
    return keyed, health


def split_counts(times: list[int], train_end: str, validation_end: str) -> dict[str, int]:
    train_end_ms = int(parse_iso(train_end).timestamp() * 1000)
    validation_end_ms = int(parse_iso(validation_end).timestamp() * 1000)
    return {
        "train": sum(1 for item in times if item < train_end_ms),
        "validation": sum(1 for item in times if train_end_ms <= item < validation_end_ms),
        "oos": sum(1 for item in times if item >= validation_end_ms),
    }


def build_panel_rows(
    *,
    symbol: str,
    spot: dict[int, dict[str, str]],
    perp: dict[int, dict[str, str]],
    funding: dict[int, float],
) -> list[dict[str, str]]:
    panel: list[dict[str, str]] = []
    for time_ms in sorted(set(spot) & set(perp)):
        spot_row = spot[time_ms]
        perp_row = perp[time_ms]
        spot_open = float(str(spot_row["open"]))
        spot_close = float(str(spot_row["close"]))
        perp_open = float(str(perp_row["open"]))
        perp_close = float(str(perp_row["close"]))
        panel.append(
            {
                "symbol": symbol,
                "time": spot_row.get("time") or ms_to_iso(time_ms),
                "time_ms": str(time_ms),
                "spot_open": f"{spot_open:.12g}",
                "spot_close": f"{spot_close:.12g}",
                "perp_open": f"{perp_open:.12g}",
                "perp_close": f"{perp_close:.12g}",
                "basis_open_bps": f"{(perp_open / spot_open - 1.0) * 10_000.0:.8f}",
                "basis_close_bps": f"{(perp_close / spot_close - 1.0) * 10_000.0:.8f}",
                "spot_volume": str(spot_row.get("volume", "")),
                "perp_volume": str(perp_row.get("volume", "")),
                "funding_event_bps": "" if time_ms not in funding else f"{funding[time_ms] * 10_000.0:.8f}",
            }
        )
    return panel


def write_panel(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PANEL_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def backfill_commands(symbol: str, interval: str, start: str, end: str, cache_dir: str) -> list[str]:
    return [
        (
            "python tools/binance_vision_kline_backfiller.py "
            f"--market spot --symbol {symbol} --interval {interval} --start {start} --end {end} "
            f"--cache-dir {cache_dir} --out-prefix docs/BINANCE_VISION_KLINE_BACKFILL_{symbol}_SPOT_{interval}"
        ),
        (
            "python tools/binance_vision_kline_backfiller.py "
            f"--market futures --symbol {symbol} --interval {interval} --start {start} --end {end} "
            f"--cache-dir {cache_dir} --out-prefix docs/BINANCE_VISION_KLINE_BACKFILL_{symbol}_FUTURES_{interval}"
        ),
        (
            "python tools/binance_vision_funding_backfiller.py "
            f"--symbol {symbol} --start {start} --end {end} "
            f"--cache-dir {cache_dir} --out-prefix docs/BINANCE_VISION_FUNDING_BACKFILL_{symbol}"
        ),
    ]


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    cache = resolve_path(args.cache_dir)
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    interval_ms = INTERVAL_MS.get(args.interval)
    if interval_ms is None:
        raise ValueError(f"Unsupported interval: {args.interval}")
    all_panel_rows: list[dict[str, str]] = []
    symbol_reports: list[dict[str, Any]] = []
    for symbol in symbols:
        spot_path = cache / "spot" / symbol / f"{args.interval}_klines.csv"
        perp_path = cache / "futures" / symbol / f"{args.interval}_klines.csv"
        funding_path = cache / "futures" / symbol / "funding_raw.csv"
        spot_rows, spot_health = keyed_klines(spot_path)
        perp_rows, perp_health = keyed_klines(perp_path)
        funding_rows, funding_health = funding_by_hour(funding_path)
        panel_rows = build_panel_rows(symbol=symbol, spot=spot_rows, perp=perp_rows, funding=funding_rows)
        all_panel_rows.extend(panel_rows)
        matched_times = sorted(int(row["time_ms"]) for row in panel_rows)
        missing_inputs = [
            name
            for name, health in [("spot", spot_health), ("futures", perp_health), ("funding", funding_health)]
            if not health.exists or health.error
        ]
        funding_in_window = sum(1 for ts in funding_rows if matched_times and matched_times[0] <= ts <= matched_times[-1])
        symbol_reports.append(
            {
                "symbol": symbol,
                "status": "complete" if not missing_inputs and matched_times else "missing_or_incomplete",
                "missing_inputs": missing_inputs,
                "paths": {
                    "spot": spot_health.__dict__,
                    "futures": perp_health.__dict__,
                    "funding": funding_health.__dict__,
                },
                "coverage": {
                    "spot_rows_valid": len(spot_rows),
                    "futures_rows_valid": len(perp_rows),
                    "matched_rows": len(matched_times),
                    "coverage_pct_vs_futures": round(len(matched_times) / len(perp_rows) * 100.0, 4) if perp_rows else 0.0,
                    "first_time": ms_to_iso(matched_times[0]) if matched_times else None,
                    "last_time": ms_to_iso(matched_times[-1]) if matched_times else None,
                    "funding_events_total": len(funding_rows),
                    "funding_events_in_matched_window": funding_in_window,
                    "row_splits_shock": split_counts(matched_times, args.shock_train_end, args.shock_validation_end) if matched_times else {"train": 0, "validation": 0, "oos": 0},
                    "row_splits_carry": split_counts(matched_times, args.carry_train_end, args.carry_validation_end) if matched_times else {"train": 0, "validation": 0, "oos": 0},
                },
                "backfill_commands": backfill_commands(symbol, args.interval, args.start, args.end, args.cache_dir) if missing_inputs else [],
            }
        )
    complete = [item for item in symbol_reports if item["status"] == "complete"]
    panel_path = resolve_path(args.panel_out)
    if args.write_panel:
        write_panel(panel_path, all_panel_rows)
    min_required = max(1, int(args.min_complete_symbols))
    if len(complete) >= min_required and all(item["coverage"]["matched_rows"] >= args.min_rows_per_symbol for item in complete):
        decision = "basis_multi_symbol_input_ready_for_research"
    elif complete:
        decision = "basis_coverage_partial_more_symbols_needed"
    else:
        decision = "basis_coverage_blocked_no_complete_symbols"
    missing_symbols = [item["symbol"] for item in symbol_reports if item["status"] != "complete"]
    return {
        "generated_utc": now_iso(),
        "engine": "MULTI_SYMBOL_BASIS_COVERAGE",
        "decision": decision,
        "can_trade": False,
        "inputs": {
            "cache_dir": args.cache_dir,
            "symbols": symbols,
            "interval": args.interval,
            "start": args.start,
            "end": args.end,
            "min_complete_symbols": min_required,
            "min_rows_per_symbol": args.min_rows_per_symbol,
        },
        "summary": {
            "symbols_requested": len(symbols),
            "complete_symbols": len(complete),
            "missing_symbols": missing_symbols,
            "panel_rows": len(all_panel_rows),
            "panel_written": bool(args.write_panel),
            "panel_path": portable_path(panel_path),
        },
        "symbols": symbol_reports,
        "boundaries": {
            "uses_private_credentials": False,
            "downloads_data": False,
            "sends_orders": False,
            "changes_strategy_parameters": False,
            "paper_execution_allowed": False,
            "live_execution_allowed": False,
            "can_trade": False,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Multi-Symbol Basis Coverage",
        "",
        f"- Generated: `{report['generated_utc']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `{str(report['can_trade']).lower()}`",
        f"- Panel rows: `{report['summary']['panel_rows']}`",
        f"- Panel path: `{report['summary']['panel_path']}`",
        "",
        "## Symbols",
        "",
        "| Symbol | Status | Matched rows | Funding events in window | First | Last | Missing |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for item in report["symbols"]:
        cov = item["coverage"]
        lines.append(
            "| {symbol} | `{status}` | `{rows}` | `{funding}` | `{first}` | `{last}` | `{missing}` |".format(
                symbol=item["symbol"],
                status=item["status"],
                rows=cov["matched_rows"],
                funding=cov["funding_events_in_matched_window"],
                first=cov["first_time"],
                last=cov["last_time"],
                missing=",".join(item["missing_inputs"]) or "-",
            )
        )
    lines.extend(["", "## Backfill Commands", ""])
    any_commands = False
    for item in report["symbols"]:
        if item["backfill_commands"]:
            any_commands = True
            lines.append(f"### {item['symbol']}")
            lines.append("")
            lines.append("```powershell")
            lines.extend(item["backfill_commands"])
            lines.append("```")
            lines.append("")
    if not any_commands:
        lines.append("- No backfill commands needed for requested symbols.")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- This is data coverage and panel construction only.",
            "- It does not select parameters, run paper/live execution, or grant trade permission.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit and build a local multi-symbol spot/perp basis coverage panel.")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BCHUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--start", default="2021-01")
    parser.add_argument("--end", default="2026-05")
    parser.add_argument("--min-complete-symbols", type=int, default=3)
    parser.add_argument("--min-rows-per-symbol", type=int, default=20_000)
    parser.add_argument("--shock-train-end", default="2024-01-01T00:00:00+00:00")
    parser.add_argument("--shock-validation-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--carry-train-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--carry-validation-end", default="2026-01-01T00:00:00+00:00")
    parser.add_argument("--write-panel", action="store_true")
    parser.add_argument("--panel-out", default="data/research/basis_multi_symbol/1h_basis_panel.csv")
    parser.add_argument("--out-prefix", default="docs/MULTI_SYMBOL_BASIS_COVERAGE_2026-06-30")
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "complete_symbols": report["summary"]["complete_symbols"],
                "missing_symbols": report["summary"]["missing_symbols"],
                "panel_rows": report["summary"]["panel_rows"],
                "out": portable_path(out_prefix.with_suffix(".json")),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

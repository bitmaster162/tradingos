#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402


APPROVED_SOURCE = "binance_usdm_forceOrder_websocket"


@dataclass
class EventRow:
    event_time_ms: int
    event_time: str
    symbol: str
    side: str
    price: float
    quantity: float
    notional_usd: float
    source: str
    path: str
    line: int


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def parse_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def ms_to_dt(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def parse_interval(value: str) -> timedelta:
    text = value.strip().lower()
    if text.endswith("m"):
        return timedelta(minutes=int(text[:-1]))
    if text.endswith("h"):
        return timedelta(hours=int(text[:-1]))
    if text.endswith("d"):
        return timedelta(days=int(text[:-1]))
    raise ValueError(f"unsupported interval: {value}")


def floor_time(ts: datetime, interval: str) -> datetime:
    delta = parse_interval(interval)
    seconds = int(delta.total_seconds())
    epoch = int(ts.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % seconds), tz=timezone.utc)


def canonical_bar_ts(value: Any) -> str | None:
    parsed = parse_ts(value)
    if parsed is None:
        return None
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_symbols(symbols: str, fallback_symbol: str) -> list[str]:
    source = symbols.strip() or fallback_symbol
    return [item.strip().upper() for item in source.split(",") if item.strip()]


def read_jsonl_events(data_dir: Path, symbols: list[str], max_bad_lines: int) -> tuple[list[EventRow], list[dict[str, Any]], list[Path]]:
    rows: list[EventRow] = []
    bad: list[dict[str, Any]] = []
    files = sorted(data_dir.rglob("*.jsonl")) if data_dir.exists() else []
    symbol_set = set(symbol.upper() for symbol in symbols)
    accept_all = "ALL" in symbol_set or "*" in symbol_set
    for path in files:
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                for line_no, line in enumerate(handle, start=1):
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError as exc:
                        if len(bad) < max_bad_lines:
                            bad.append({"path": portable(path), "line": line_no, "error": f"json:{exc}"})
                        continue
                    if not isinstance(payload, dict):
                        if len(bad) < max_bad_lines:
                            bad.append({"path": portable(path), "line": line_no, "error": "row_not_object"})
                        continue
                    row_symbol = str(payload.get("symbol") or "").upper()
                    if not accept_all and row_symbol not in symbol_set:
                        continue
                    errors = validate_event_payload(payload)
                    if errors:
                        if len(bad) < max_bad_lines:
                            bad.append({"path": portable(path), "line": line_no, "error": ";".join(errors)})
                        continue
                    rows.append(
                        EventRow(
                            event_time_ms=int(payload["event_time_ms"]),
                            event_time=str(payload["event_time"]),
                            symbol=str(payload["symbol"]).upper(),
                            side=str(payload["side"]).upper(),
                            price=float(payload["price"]),
                            quantity=float(payload["quantity"]),
                            notional_usd=float(payload["notional_usd"]),
                            source=str(payload["source"]),
                            path=portable(path),
                            line=line_no,
                        )
                    )
        except OSError as exc:
            if len(bad) < max_bad_lines:
                bad.append({"path": portable(path), "line": None, "error": str(exc)})
    rows.sort(key=lambda item: item.event_time_ms)
    return rows, bad, files


def validate_event_payload(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = ("event_time_ms", "event_time", "symbol", "side", "price", "quantity", "notional_usd", "source", "is_real_liquidation_feed")
    for field in required:
        if row.get(field) in (None, ""):
            errors.append(f"missing:{field}")
    if row.get("source") != APPROVED_SOURCE:
        errors.append(f"source:{row.get('source')}")
    if row.get("is_real_liquidation_feed") is not True:
        errors.append("not_real_feed")
    if str(row.get("side") or "").upper() not in {"BUY", "SELL"}:
        errors.append(f"side:{row.get('side')}")
    event_dt = ms_to_dt(row.get("event_time_ms"))
    if event_dt is None:
        errors.append("event_time_ms")
    for field in ("price", "quantity", "notional_usd"):
        try:
            value = float(row.get(field))
        except (TypeError, ValueError):
            errors.append(f"number:{field}")
            continue
        if value <= 0:
            errors.append(f"non_positive:{field}")
    try:
        recomputed = float(row["price"]) * float(row["quantity"])
        if abs(recomputed - float(row["notional_usd"])) > max(1e-6, recomputed * 0.001):
            errors.append("notional_mismatch")
    except (KeyError, TypeError, ValueError):
        pass
    return errors


def load_bar_times(path: Path) -> set[str]:
    if not path.exists():
        return set()
    bars = load_ohlcv(path)
    return {canonical for bar in bars if (canonical := canonical_bar_ts(str(bar.ts)))}


def bar_time_coverage(bar_times_by_symbol: dict[str, set[str]]) -> dict[str, dict[str, Any]]:
    coverage: dict[str, dict[str, Any]] = {}
    for symbol, bar_times in sorted(bar_times_by_symbol.items()):
        ordered = sorted(bar_times)
        coverage[symbol] = {
            "bars": len(ordered),
            "first_bar_ts": ordered[0] if ordered else None,
            "last_bar_ts": ordered[-1] if ordered else None,
        }
    return coverage


def aggregate_events(events: list[EventRow], interval: str, bar_times_by_symbol: dict[str, set[str]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[EventRow]] = defaultdict(list)
    for event in events:
        event_dt = ms_to_dt(event.event_time_ms)
        if event_dt is None:
            continue
        bar_ts = floor_time(event_dt, interval).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        grouped[(event.symbol, bar_ts)].append(event)
    rows: list[dict[str, Any]] = []
    for (symbol, bar_ts), items in sorted(grouped.items()):
        buy_notional = sum(item.notional_usd for item in items if item.side == "BUY")
        sell_notional = sum(item.notional_usd for item in items if item.side == "SELL")
        total = buy_notional + sell_notional
        dominant = "mixed"
        if total > 0 and buy_notional / total >= 0.65:
            dominant = "short_liquidation_squeeze"
        elif total > 0 and sell_notional / total >= 0.65:
            dominant = "long_liquidation_flush"
        rows.append(
            {
                "symbol": symbol,
                "bar_ts": bar_ts,
                "matched_price_bar": bar_ts in bar_times_by_symbol.get(symbol, set()),
                "event_count": len(items),
                "first_event_time": min(item.event_time for item in items),
                "last_event_time": max(item.event_time for item in items),
                "total_notional_usd": round(total, 6),
                "buy_force_order_notional_usd": round(buy_notional, 6),
                "sell_force_order_notional_usd": round(sell_notional, 6),
                "net_buy_minus_sell_notional_usd": round(buy_notional - sell_notional, 6),
                "max_event_notional_usd": round(max(item.notional_usd for item in items), 6),
                "dominant_context": dominant,
                "source": APPROVED_SOURCE,
                "is_real_liquidation_feed": True,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "symbol",
        "bar_ts",
        "matched_price_bar",
        "event_count",
        "first_event_time",
        "last_event_time",
        "total_notional_usd",
        "buy_force_order_notional_usd",
        "sell_force_order_notional_usd",
        "net_buy_minus_sell_notional_usd",
        "max_event_notional_usd",
        "dominant_context",
        "source",
        "is_real_liquidation_feed",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    data_dir = resolve_path(args.data_dir)
    symbols = parse_symbols(args.symbols, args.symbol)
    events, bad_rows, files = read_jsonl_events(data_dir, symbols, args.max_bad_lines)
    events_before_start_filter = len(events)
    event_start_at = parse_ts(args.event_start_at) if args.event_start_at else None
    if event_start_at is not None:
        events = [
            event
            for event in events
            if (event_dt := ms_to_dt(event.event_time_ms)) is not None and event_dt >= event_start_at
        ]
    bar_times_by_symbol: dict[str, set[str]] = {}
    bar_paths: dict[str, str] = {}
    if args.bars_csv and len(symbols) == 1:
        path = resolve_path(args.bars_csv)
        bar_times_by_symbol[symbols[0]] = load_bar_times(path)
        bar_paths[symbols[0]] = portable(path)
    else:
        for symbol in sorted({event.symbol for event in events}.union(set(symbols) if "ALL" not in symbols and "*" not in symbols else set())):
            path = resolve_path(f"data/cache/binance_spot_perp_extended/futures/{symbol}/{args.interval}_klines.csv")
            if path.exists():
                bar_times_by_symbol[symbol] = load_bar_times(path)
                bar_paths[symbol] = portable(path)
            else:
                bar_times_by_symbol[symbol] = set()
                bar_paths[symbol] = portable(path)
    aggregates = aggregate_events(events, args.interval, bar_times_by_symbol)
    matched_event_bars = sum(1 for row in aggregates if row["matched_price_bar"])
    if not events:
        decision = "waiting_for_real_force_order_events"
        next_action = "keep forceOrder collector running; do not create a strategy consumer yet"
    elif len(events) < args.min_events_for_research:
        decision = "collecting_force_order_context_sample"
        next_action = "continue collecting until minimum event-level sample is reached"
    elif len(aggregates) < args.min_event_bars_for_research:
        decision = "collecting_force_order_context_bars"
        next_action = "continue collecting until enough distinct event bars exist"
    elif matched_event_bars < args.min_event_bars_for_research:
        decision = "blocked_force_order_context_no_matching_price_bars"
        next_action = "refresh matching OHLCV cache before preregistered research"
    else:
        decision = "force_order_context_ready_for_preregistered_research"
        next_action = "open a preregistered hypothesis design; do not run free-form parameter search"
    return {
        "generated_at": now_iso(),
        "tool": "tools/force_order_liquidation_context_intake.py",
        "decision": decision,
        "can_trade": False,
        "boundary": {
            "research_data_intake_only": True,
            "uses_real_force_order_rows_only": True,
            "rejects_proxy_rows": True,
            "sends_orders": False,
            "uses_private_credentials": False,
            "network_required": False,
        },
        "inputs": {
            "data_dir": portable(data_dir),
            "bars_csv_by_symbol": bar_paths,
            "symbols": symbols,
            "interval": args.interval,
            "event_start_at": event_start_at.isoformat(timespec="seconds").replace("+00:00", "Z") if event_start_at else None,
            "min_events_for_research": args.min_events_for_research,
            "min_event_bars_for_research": args.min_event_bars_for_research,
        },
        "summary": {
            "events": len(events),
            "events_excluded_before_start": events_before_start_filter - len(events),
            "jsonl_files": len(files),
            "bad_rows_sample": bad_rows,
            "event_bars": len(aggregates),
            "matched_event_bars": matched_event_bars,
            "unmatched_event_bars": len(aggregates) - matched_event_bars,
            "total_notional_usd": round(sum(item.notional_usd for item in events), 6),
            "first_event_time": events[0].event_time if events else None,
            "last_event_time": events[-1].event_time if events else None,
        },
        "by_symbol": {
            symbol: {
                "events": sum(1 for event in events if event.symbol == symbol),
                "event_bars": sum(1 for row in aggregates if row["symbol"] == symbol),
                "matched_event_bars": sum(1 for row in aggregates if row["symbol"] == symbol and row["matched_price_bar"]),
                "total_notional_usd": round(sum(event.notional_usd for event in events if event.symbol == symbol), 6),
            }
            for symbol in sorted({event.symbol for event in events}.union(set(symbols) if "ALL" not in symbols and "*" not in symbols else set()))
        },
        "context_counts": {
            name: sum(1 for row in aggregates if row["dominant_context"] == name)
            for name in ("long_liquidation_flush", "short_liquidation_squeeze", "mixed")
        },
        "price_bar_coverage_by_symbol": bar_time_coverage(bar_times_by_symbol),
        "aggregate_csv": None,
        "_aggregate_rows": aggregates,
        "top_event_bars": sorted(aggregates, key=lambda row: row["total_notional_usd"], reverse=True)[:20],
        "next_action": next_action,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# ForceOrder Liquidation Context Intake",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `false`",
        f"- Events: `{summary['events']}`",
        f"- Events excluded before start: `{summary['events_excluded_before_start']}`",
        f"- Event start at: `{report['inputs']['event_start_at']}`",
        f"- Event bars: `{summary['event_bars']}`",
        f"- Matched event bars: `{summary['matched_event_bars']}`",
        f"- Total notional USD: `{summary['total_notional_usd']}`",
        f"- Symbols: `{', '.join(report['inputs']['symbols'])}`",
        "",
        "## Boundary",
        "",
        "- Reads only event-level Binance USD-M `forceOrder` rows from the approved collector.",
        "- Rejects proxy rows by policy.",
        "- Builds context features only; it does not backtest, promote, notify, create intents or send orders.",
        "- `BUY` forceOrder is treated as short-liquidation pressure; `SELL` forceOrder is treated as long-liquidation pressure.",
        "",
        "## Context Counts",
        "",
    ]
    for name, count in report["context_counts"].items():
        lines.append(f"- `{name}`: `{count}`")
    lines.extend(["", "## By Symbol", ""])
    for symbol, values in report.get("by_symbol", {}).items():
        lines.append(f"- `{symbol}`: events=`{values['events']}`, bars=`{values['event_bars']}`, matched=`{values['matched_event_bars']}`, notional=`{values['total_notional_usd']}`")
    lines.extend(["", "## Top Event Bars", "", "| Symbol | Bar TS | Events | Total Notional | Buy Notional | Sell Notional | Context | Matched |", "|---|---|---:|---:|---:|---:|---|---:|"])
    for row in report.get("top_event_bars", []):
        lines.append(
            f"| `{row['symbol']}` | `{row['bar_ts']}` | `{row['event_count']}` | `{row['total_notional_usd']}` | `{row['buy_force_order_notional_usd']}` | `{row['sell_force_order_notional_usd']}` | `{row['dominant_context']}` | `{row['matched_price_bar']}` |"
        )
    lines.extend(["", "## Next Action", "", f"- {report['next_action']}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Real forceOrder liquidation context intake; data-quality/gating only")
    parser.add_argument("--data-dir", default="data/live/liquidations/binance_force_order")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols. Overrides --symbol when set; use ALL to accept every real forceOrder row.")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--event-start-at", default="")
    parser.add_argument("--bars-csv", default="")
    parser.add_argument("--min-events-for-research", type=int, default=500)
    parser.add_argument("--min-event-bars-for-research", type=int, default=50)
    parser.add_argument("--max-bad-lines", type=int, default=25)
    parser.add_argument("--out-prefix", default="docs/FORCE_ORDER_LIQUIDATION_CONTEXT_INTAKE_2026-07-01")
    args = parser.parse_args()
    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    aggregate_csv = out_prefix.with_name(out_prefix.name + "_bar_context.csv")
    aggregate_rows = report.pop("_aggregate_rows", [])
    if report["summary"]["event_bars"] > 0:
        write_csv(aggregate_csv, aggregate_rows)
        report["aggregate_csv"] = portable(aggregate_csv)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "events": report["summary"]["events"],
                "event_bars": report["summary"]["event_bars"],
                "matched_event_bars": report["summary"]["matched_event_bars"],
                "symbols": report["inputs"]["symbols"],
                "out": portable(out_prefix.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

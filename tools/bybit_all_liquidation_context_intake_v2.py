#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bybit_all_liquidation_context_intake as legacy
from tools.liquidation_side_semantics import (
    CANONICAL_SIDE_SCHEMA_VERSION,
    LIQUIDATED_POSITION_SIDE_MAP,
    dominant_liquidation_context,
    liquidated_position_side,
)


TOOL_PATH = "tools/bybit_all_liquidation_context_intake_v2.py"
DOMINANCE_THRESHOLD = 0.65
CSV_FIELDS = [
    "symbol",
    "bar_ts",
    "matched_price_bar",
    "event_count",
    "first_event_time",
    "last_event_time",
    "total_notional_usd",
    "long_liquidated_notional_usd",
    "short_liquidated_notional_usd",
    "raw_buy_notional_usd",
    "raw_sell_notional_usd",
    "net_long_minus_short_liquidated_notional_usd",
    "max_event_notional_usd",
    "dominant_context",
    "side_semantics_version",
    "source",
    "is_real_liquidation_feed",
]


def aggregate_events(
    events: list[legacy.EventRow],
    interval: str,
    bar_times_by_symbol: dict[str, set[str]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[legacy.EventRow]] = defaultdict(list)
    for event in events:
        event_dt = legacy.ms_to_dt(event.liquidation_time_ms)
        if event_dt is None:
            continue
        bar_ts = legacy.floor_time(event_dt, interval).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        grouped[(event.symbol, bar_ts)].append(event)

    rows: list[dict[str, Any]] = []
    for (symbol, bar_ts), items in sorted(grouped.items()):
        long_notional = sum(
            item.notional_usd
            for item in items
            if liquidated_position_side("bybit_all_liquidation", item.side) == "LONG"
        )
        short_notional = sum(
            item.notional_usd
            for item in items
            if liquidated_position_side("bybit_all_liquidation", item.side) == "SHORT"
        )
        raw_buy = sum(item.notional_usd for item in items if item.side == "BUY")
        raw_sell = sum(item.notional_usd for item in items if item.side == "SELL")
        total = long_notional + short_notional
        rows.append(
            {
                "symbol": symbol,
                "bar_ts": bar_ts,
                "matched_price_bar": bar_ts in bar_times_by_symbol.get(symbol, set()),
                "event_count": len(items),
                "first_event_time": min(item.liquidation_time for item in items),
                "last_event_time": max(item.liquidation_time for item in items),
                "total_notional_usd": round(total, 6),
                "long_liquidated_notional_usd": round(long_notional, 6),
                "short_liquidated_notional_usd": round(short_notional, 6),
                "raw_buy_notional_usd": round(raw_buy, 6),
                "raw_sell_notional_usd": round(raw_sell, 6),
                "net_long_minus_short_liquidated_notional_usd": round(long_notional - short_notional, 6),
                "max_event_notional_usd": round(max(item.notional_usd for item in items), 6),
                "dominant_context": dominant_liquidation_context(
                    long_notional,
                    short_notional,
                    dominance_threshold=DOMINANCE_THRESHOLD,
                ),
                "side_semantics_version": CANONICAL_SIDE_SCHEMA_VERSION,
                "source": legacy.APPROVED_SOURCE,
                "is_real_liquidation_feed": True,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="raise", lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row.get(field) for field in CSV_FIELDS} for row in rows)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    data_dir = legacy.resolve_path(args.data_dir)
    symbols = legacy.parse_symbols(args.symbols, args.symbol)
    events, bad_rows, files = legacy.read_jsonl_events(data_dir, symbols, args.max_bad_lines)
    bar_times_by_symbol: dict[str, set[str]] = {}
    bar_paths: dict[str, str] = {}
    if args.bars_csv and len(symbols) == 1:
        path = legacy.resolve_path(args.bars_csv)
        bar_times_by_symbol[symbols[0]] = legacy.load_bar_times(path)
        bar_paths[symbols[0]] = legacy.portable(path)
    else:
        for symbol in symbols:
            if symbol in {"ALL", "*"}:
                continue
            path = legacy.resolve_path(
                f"data/cache/binance_spot_perp_extended/futures/{symbol}/{args.interval}_klines.csv"
            )
            if path.exists():
                bar_times_by_symbol[symbol] = legacy.load_bar_times(path)
                bar_paths[symbol] = legacy.portable(path)

    aggregates = aggregate_events(events, args.interval, bar_times_by_symbol)
    matched = sum(1 for row in aggregates if row["matched_price_bar"])
    contexts = {
        context: sum(1 for row in aggregates if row["dominant_context"] == context)
        for context in ("long_liquidation_flush", "short_liquidation_squeeze", "mixed")
    }
    if not events:
        decision = "waiting_for_real_bybit_liquidation_events"
        next_action = "keep the public Bybit collector running; no strategy consumer is allowed"
    elif len(events) < args.min_events_for_research:
        decision = "collecting_bybit_liquidation_context_sample"
        next_action = "continue collecting until the fixed event-level sample gate is reached"
    elif len(aggregates) < args.min_event_bars_for_research:
        decision = "collecting_bybit_liquidation_context_bars"
        next_action = "continue collecting until the fixed distinct-bar gate is reached"
    elif matched == 0:
        decision = "blocked_bybit_liquidation_context_no_matching_price_bars"
        next_action = "refresh matching OHLCV cache before corrected-label discovery"
    else:
        decision = "bybit_liquidation_canonical_context_ready_for_discovery_review"
        next_action = "run one fixed-grid corrected-label discovery; any candidate needs a new future-floor lock"

    return {
        "generated_at": legacy.now_iso(),
        "tool": TOOL_PATH,
        "decision": decision,
        "can_trade": False,
        "side_contract": {
            "schema_version": CANONICAL_SIDE_SCHEMA_VERSION,
            "source": "bybit_all_liquidation",
            "raw_field_meaning": "liquidated position side",
            "mapping": LIQUIDATED_POSITION_SIDE_MAP["bybit_all_liquidation"],
            "dominance_threshold": DOMINANCE_THRESHOLD,
            "legacy_v1_rows_compatible": False,
            "reference": "https://bybit-exchange.github.io/docs/v5/websocket/public/all-liquidation",
        },
        "boundary": {
            "research_only": True,
            "corrected_discovery_input_only": True,
            "old_locks_may_consume_output": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "uses_real_liquidation_rows_only": True,
            "can_trade": False,
        },
        "inputs": {
            "data_dir": legacy.portable(data_dir),
            "symbols": symbols,
            "interval": args.interval,
            "bars_csv_by_symbol": bar_paths,
            "min_events_for_research": args.min_events_for_research,
            "min_event_bars_for_research": args.min_event_bars_for_research,
        },
        "summary": {
            "jsonl_files": len(files),
            "events": len(events),
            "bad_rows_sample": bad_rows,
            "aggregate_rows": len(aggregates),
            "matched_price_bars": matched,
            "contexts": contexts,
        },
        "aggregate_csv": None,
        "_aggregate_rows": aggregates,
        "next_action": next_action,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Bybit allLiquidation Canonical Context Intake V2",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        f"- Events: `{report['summary']['events']}`",
        f"- Aggregate bars: `{report['summary']['aggregate_rows']}`",
        f"- Matched price bars: `{report['summary']['matched_price_bars']}`",
        "",
        "## Side Contract",
        "",
        "- Bybit `BUY` means a liquidated long position and maps to `long_liquidation_flush`.",
        "- Bybit `SELL` means a liquidated short position and maps to `short_liquidation_squeeze`.",
        "- This V2 output is discovery-only and must never be supplied to an old V1 lock.",
        "",
        "## Context Counts",
        "",
        "| Context | Bars |",
        "|---|---:|",
    ]
    for context, count in report["summary"]["contexts"].items():
        lines.append(f"| `{context}` | `{count}` |")
    lines.extend(["", "## Next Action", "", f"- {report['next_action']}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Canonical Bybit allLiquidation context intake V2; research-only")
    parser.add_argument("--data-dir", default="data/live/liquidations/bybit_all_liquidation")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--bars-csv", default="")
    parser.add_argument("--min-events-for-research", type=int, default=500)
    parser.add_argument("--min-event-bars-for-research", type=int, default=50)
    parser.add_argument("--max-bad-lines", type=int, default=25)
    parser.add_argument("--out-prefix", default="docs/BYBIT_ALL_LIQUIDATION_CANONICAL_CONTEXT_INTAKE_V2_2026-07-13")
    args = parser.parse_args()
    report = build_report(args)
    out_prefix = legacy.resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    aggregate_csv = out_prefix.with_name(out_prefix.name + "_bar_context.csv")
    aggregate_rows = report.pop("_aggregate_rows", [])
    if aggregate_rows:
        write_csv(aggregate_csv, aggregate_rows)
        report["aggregate_csv"] = legacy.portable(aggregate_csv)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "events": report["summary"]["events"],
                "aggregate_rows": report["summary"]["aggregate_rows"],
                "contexts": report["summary"]["contexts"],
                "aggregate_csv": report["aggregate_csv"],
                "can_trade": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

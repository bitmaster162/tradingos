#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402


APPROVED_SOURCE = "binance_usdm_forceOrder_websocket"
CONTEXTS = ("long_liquidation_flush", "short_liquidation_squeeze", "mixed")
EVENT_RECORD_FIELDS = (
    "symbol",
    "bar_ts",
    "independent_4h_block",
    "signal_time",
    "entry_time",
    "entry_model",
    "entry_price",
    "event_bar_close",
    "exit_time",
    "exit_price",
    "horizon_bars",
    "dominant_context",
    "total_notional_usd",
    "raw_return_bps",
    "continuation_return_bps",
    "reversal_return_bps",
)


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def canonical_bar_ts(value: Any) -> str | None:
    parsed = parse_ts(value)
    if parsed is None:
        return None
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def independent_4h_block_id(value: Any) -> str | None:
    parsed = parse_ts(value)
    if parsed is None:
        return None
    block = parsed.replace(hour=(parsed.hour // 4) * 4, minute=0, second=0, microsecond=0)
    return block.isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def parse_symbols(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def parse_horizons(value: str) -> list[int]:
    horizons = sorted({int(item.strip()) for item in value.split(",") if item.strip()})
    if not horizons or any(item <= 0 for item in horizons):
        raise ValueError("horizons must be positive integers")
    return horizons


def parse_sources(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def read_context_rows(path: Path, symbols: list[str], allowed_sources: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.exists():
        return [], [f"context_csv_missing:{portable(path)}"]
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    symbol_set = set(symbols)
    accept_all = not symbol_set or "ALL" in symbol_set or "*" in symbol_set
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "symbol",
            "bar_ts",
            "matched_price_bar",
            "total_notional_usd",
            "dominant_context",
            "source",
            "is_real_liquidation_feed",
        }
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            return [], [f"context_csv_missing_columns:{','.join(missing)}"]
        for row_no, row in enumerate(reader, start=2):
            symbol = str(row.get("symbol") or "").upper()
            if not accept_all and symbol not in symbol_set:
                continue
            row_errors: list[str] = []
            if row.get("source") not in allowed_sources:
                row_errors.append("bad_source")
            if not parse_bool(row.get("is_real_liquidation_feed")):
                row_errors.append("not_real_feed")
            if not parse_bool(row.get("matched_price_bar")):
                row_errors.append("unmatched_price_bar")
            if row.get("dominant_context") not in CONTEXTS:
                row_errors.append("bad_context")
            if canonical_bar_ts(row.get("bar_ts")) is None:
                row_errors.append("bad_bar_ts")
            try:
                total_notional = float(row.get("total_notional_usd") or 0)
            except ValueError:
                total_notional = 0.0
                row_errors.append("bad_notional")
            if total_notional <= 0:
                row_errors.append("non_positive_notional")
            if row_errors:
                if len(errors) < 25:
                    errors.append(f"row_{row_no}:{';'.join(row_errors)}")
                continue
            normalized = dict(row)
            normalized["symbol"] = symbol
            normalized["bar_ts"] = canonical_bar_ts(row.get("bar_ts"))
            normalized["total_notional_usd"] = total_notional
            rows.append(normalized)
    return rows, errors


def load_bars_by_symbol(symbols: list[str], interval: str, bars_root: Path) -> tuple[dict[str, list[Any]], dict[str, str]]:
    bars: dict[str, list[Any]] = {}
    paths: dict[str, str] = {}
    for symbol in sorted(set(symbols)):
        path = bars_root / symbol / f"{interval}_klines.csv"
        paths[symbol] = portable(path)
        if path.exists():
            bars[symbol] = load_ohlcv(path)
        else:
            bars[symbol] = []
    return bars, paths


def build_bar_index(bars: list[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for index, bar in enumerate(bars):
        key = canonical_bar_ts(bar.ts)
        if key is not None:
            out[key] = index
    return out


def continuation_sign(context: str) -> int:
    if context == "short_liquidation_squeeze":
        return 1
    if context == "long_liquidation_flush":
        return -1
    return 0


def summarize(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "n": 0,
            "mean_bps": None,
            "median_bps": None,
            "winrate_positive_pct": None,
            "min_bps": None,
            "max_bps": None,
        }
    return {
        "n": len(values),
        "mean_bps": round(statistics.fmean(values), 6),
        "median_bps": round(statistics.median(values), 6),
        "winrate_positive_pct": round(100.0 * sum(1 for value in values if value > 0) / len(values), 3),
        "min_bps": round(min(values), 6),
        "max_bps": round(max(values), 6),
    }


def build_event_records(
    rows: list[dict[str, Any]],
    bars_by_symbol: dict[str, list[Any]],
    horizons: list[int],
) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    indexes_by_symbol = {symbol: build_bar_index(bars) for symbol, bars in bars_by_symbol.items()}
    for row in rows:
        symbol = row["symbol"]
        bars = bars_by_symbol.get(symbol, [])
        index = indexes_by_symbol.get(symbol, {}).get(row["bar_ts"])
        if index is None:
            if len(errors) < 25:
                errors.append(f"missing_bar:{symbol}:{row['bar_ts']}")
            continue
        entry_index = index + 1
        if entry_index >= len(bars):
            continue
        event_bar_close = bars[index].close
        entry_price = bars[entry_index].open
        if entry_price <= 0:
            continue
        context = str(row["dominant_context"])
        csign = continuation_sign(context)
        for horizon in horizons:
            exit_index = index + horizon
            if exit_index < entry_index or exit_index >= len(bars):
                continue
            exit_close = bars[exit_index].close
            raw_return_bps = ((exit_close / entry_price) - 1.0) * 10000.0
            record = {
                "symbol": symbol,
                "bar_ts": row["bar_ts"],
                "independent_4h_block": independent_4h_block_id(row["bar_ts"]),
                "signal_time": "event_bar_close",
                "entry_time": canonical_bar_ts(bars[entry_index].ts),
                "entry_model": "next_bar_open",
                "entry_price": round(entry_price, 12),
                "event_bar_close": round(event_bar_close, 12),
                "exit_time": canonical_bar_ts(bars[exit_index].ts),
                "exit_price": round(exit_close, 12),
                "horizon_bars": horizon,
                "dominant_context": context,
                "total_notional_usd": row["total_notional_usd"],
                "raw_return_bps": round(raw_return_bps, 6),
                "continuation_return_bps": round(raw_return_bps * csign, 6) if csign else None,
                "reversal_return_bps": round(raw_return_bps * -csign, 6) if csign else None,
            }
            records.append(record)
    return records, errors


def write_event_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records, key=lambda row: (str(row["bar_ts"]), str(row["symbol"]), int(row["horizon_bars"])))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EVENT_RECORD_FIELDS, extrasaction="raise", lineterminator="\n")
        writer.writeheader()
        writer.writerows(ordered)


def summarize_records(records: list[dict[str, Any]], min_context_bars: int) -> dict[str, Any]:
    by_horizon: dict[str, Any] = {}
    for horizon in sorted({int(row["horizon_bars"]) for row in records}):
        horizon_rows = [row for row in records if int(row["horizon_bars"]) == horizon]
        by_context: dict[str, Any] = {}
        for context in CONTEXTS:
            context_rows = [row for row in horizon_rows if row["dominant_context"] == context]
            raw_values = [float(row["raw_return_bps"]) for row in context_rows]
            continuation_values = [
                float(row["continuation_return_bps"])
                for row in context_rows
                if row.get("continuation_return_bps") is not None
            ]
            reversal_values = [
                float(row["reversal_return_bps"])
                for row in context_rows
                if row.get("reversal_return_bps") is not None
            ]
            by_context[context] = {
                "sample_ready": len(context_rows) >= min_context_bars,
                "raw": summarize(raw_values),
                "continuation": summarize(continuation_values),
                "reversal": summarize(reversal_values),
            }
        by_horizon[str(horizon)] = by_context
    return by_horizon


def classify(rows: list[dict[str, Any]], records: list[dict[str, Any]], errors: list[str], min_event_bars: int, min_context_bars: int) -> str:
    if errors and not rows:
        return "waiting_for_context_csv"
    if len(rows) < min_event_bars:
        return "collecting_force_order_event_bars"
    if not records:
        return "blocked_force_order_event_study_no_forward_bars"
    directional_contexts = [context for context in ("long_liquidation_flush", "short_liquidation_squeeze")]
    for context in directional_contexts:
        if sum(1 for row in rows if row["dominant_context"] == context) < min_context_bars:
            return "collecting_force_order_context_balance"
    return "force_order_event_study_ready_for_review"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['inputs']['source_label']} Liquidation Event Study",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `false`",
        f"- Context rows: `{report['summary']['context_rows']}`",
        f"- Event-study records: `{report['summary']['event_study_records']}`",
        f"- Horizons: `{', '.join(str(item) for item in report['inputs']['horizons_bars'])}` bars",
        "",
        "## Boundary",
        "",
        "- Research-only event study; no intents, alerts, paper entries or orders.",
        "- Context is known only at event-bar close; modeled entry is the next bar open, never the event-bar close.",
        f"- Uses only approved real liquidation context rows from the intake output: `{', '.join(report['inputs']['allowed_sources'])}`.",
        "- Synthetic fixture runs are allowed only to prove plumbing and must not be used as edge evidence.",
        "- `short_liquidation_squeeze` continuation means future upside; `long_liquidation_flush` continuation means future downside.",
        "",
        "## Summary By Horizon",
        "",
    ]
    for horizon, contexts in report.get("by_horizon", {}).items():
        lines.extend([f"### Horizon {horizon} bars", "", "| Context | Ready | Raw n | Raw mean bps | Continuation n | Continuation mean bps | Reversal mean bps |", "|---|---:|---:|---:|---:|---:|---:|"])
        for context, values in contexts.items():
            raw = values["raw"]
            cont = values["continuation"]
            rev = values["reversal"]
            lines.append(
                f"| `{context}` | `{values['sample_ready']}` | `{raw['n']}` | `{raw['mean_bps']}` | `{cont['n']}` | `{cont['mean_bps']}` | `{rev['mean_bps']}` |"
            )
        lines.append("")
    if report.get("errors"):
        lines.extend(["## Errors / Waiting Reasons", ""])
        for item in report["errors"][:25]:
            lines.append(f"- `{item}`")
        lines.append("")
    lines.extend(["## Next Action", "", f"- {report['next_action']}", ""])
    return "\n".join(lines)


def build_study(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    context_csv = resolve_path(args.context_csv)
    symbols = parse_symbols(args.symbols)
    horizons = parse_horizons(args.horizons)
    allowed_sources = parse_sources(args.allowed_sources)
    rows, row_errors = read_context_rows(context_csv, symbols, allowed_sources)
    context_symbols = sorted({row["symbol"] for row in rows})
    bars_by_symbol, bar_paths = load_bars_by_symbol(context_symbols, args.interval, resolve_path(args.bars_root))
    records, record_errors = build_event_records(rows, bars_by_symbol, horizons)
    errors = row_errors + record_errors
    decision = classify(rows, records, errors, args.min_event_bars, args.min_context_bars)
    if decision == "force_order_event_study_ready_for_review":
        next_action = "review fixed-horizon continuation/reversal behavior; if stable, preregister an untouched forward validation gate"
    elif decision == "waiting_for_context_csv":
        next_action = "run force_order_liquidation_context_intake after real forceOrder rows are collected"
    elif decision == "blocked_force_order_event_study_no_forward_bars":
        next_action = "refresh matching OHLCV cache so each event has forward bars"
    else:
        next_action = "keep collecting real forceOrder rows until the preregistered minimum sample is reached"
    report = {
        "generated_at": now_iso(),
        "tool": "tools/force_order_liquidation_event_study.py",
        "decision": decision,
        "can_trade": False,
        "boundary": {
            "research_only": True,
            "fixed_horizons_only": True,
            "signal_at_event_bar_close": True,
            "entry_at_next_bar_open": True,
            "event_bar_close_fill_forbidden": True,
            "raw_records_persisted": True,
            "parameter_search": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "inputs": {
            "context_csv": portable(context_csv),
            "symbols": symbols,
            "allowed_sources": sorted(allowed_sources),
            "source_label": args.source_label,
            "interval": args.interval,
            "horizons_bars": horizons,
            "bars_by_symbol": bar_paths,
            "min_event_bars": args.min_event_bars,
            "min_context_bars": args.min_context_bars,
        },
        "summary": {
            "context_rows": len(rows),
            "event_study_records": len(records),
            "symbols": context_symbols,
            "contexts": {context: sum(1 for row in rows if row["dominant_context"] == context) for context in CONTEXTS},
        },
        "by_horizon": summarize_records(records, args.min_context_bars),
        "errors": errors[:25],
        "next_action": next_action,
    }
    return report, records


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    report, _records = build_study(args)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Preregistered research-only event study for forceOrder liquidation context")
    parser.add_argument("--context-csv", default="docs/FORCE_ORDER_LIQUIDATION_CONTEXT_INTAKE_MULTISYMBOL_2026-07-01_bar_context.csv")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,LINKUSDT,LTCUSDT")
    parser.add_argument("--allowed-sources", default=APPROVED_SOURCE)
    parser.add_argument("--source-label", default="ForceOrder")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--horizons", default="1,2,4,8")
    parser.add_argument("--bars-root", default="data/cache/binance_spot_perp_extended/futures")
    parser.add_argument("--min-event-bars", type=int, default=50)
    parser.add_argument("--min-context-bars", type=int, default=10)
    parser.add_argument("--out-prefix", default="docs/FORCE_ORDER_LIQUIDATION_EVENT_STUDY_2026-07-01")
    args = parser.parse_args()
    report, records = build_study(args)
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    records_path = out_prefix.with_name(out_prefix.name + "_records.csv")
    write_event_records(records_path, records)
    report["artifacts"] = {
        "records_csv": portable(records_path),
        "records_csv_sha256": sha256_file(records_path),
        "records": len(records),
    }
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "context_rows": report["summary"]["context_rows"],
                "event_study_records": report["summary"]["event_study_records"],
                "records_csv": portable(records_path),
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

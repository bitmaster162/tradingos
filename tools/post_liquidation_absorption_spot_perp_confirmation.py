#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_detector import OhlcvBar, load_ohlcv  # noqa: E402


DIRECTIONAL_CONTEXTS = {"long_liquidation_flush", "short_liquidation_squeeze"}
APPROVED_SOURCES = {"bybit_v5_allLiquidation_websocket", "binance_usdm_forceOrder_websocket"}


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


def canonical_ts(value: Any) -> str | None:
    parsed = parse_ts(value)
    if parsed is None:
        return None
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def parse_csv_list(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def parse_int_list(value: str) -> list[int]:
    items = sorted({int(item.strip()) for item in value.split(",") if item.strip()})
    if not items or any(item <= 0 for item in items):
        raise ValueError("horizons must be positive integers")
    return items


def read_context_rows(path: Path, symbols: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.exists():
        return [], [f"context_csv_missing:{portable(path)}"]
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "symbol",
            "bar_ts",
            "matched_price_bar",
            "total_notional_usd",
            "net_buy_minus_sell_notional_usd",
            "dominant_context",
            "source",
            "is_real_liquidation_feed",
        }
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            return [], [f"context_csv_missing_columns:{','.join(missing)}"]
        for line_no, row in enumerate(reader, start=2):
            symbol = str(row.get("symbol") or "").upper()
            if symbols and symbol not in symbols:
                continue
            row_errors: list[str] = []
            source = str(row.get("source") or "")
            context = str(row.get("dominant_context") or "")
            bar_ts = canonical_ts(row.get("bar_ts"))
            total_notional = safe_float(row.get("total_notional_usd"), 0.0)
            if source not in APPROVED_SOURCES:
                row_errors.append("unapproved_source")
            if not parse_bool(row.get("is_real_liquidation_feed")):
                row_errors.append("not_real_feed")
            if not parse_bool(row.get("matched_price_bar")):
                row_errors.append("unmatched_price_bar")
            if context not in DIRECTIONAL_CONTEXTS:
                row_errors.append("non_directional_context")
            if bar_ts is None:
                row_errors.append("bad_bar_ts")
            if total_notional <= 0:
                row_errors.append("non_positive_notional")
            if row_errors:
                if len(errors) < 50:
                    errors.append(f"row_{line_no}:{';'.join(row_errors)}")
                continue
            normalized = dict(row)
            normalized["symbol"] = symbol
            normalized["bar_ts"] = bar_ts
            normalized["total_notional_usd"] = total_notional
            normalized["net_buy_minus_sell_notional_usd"] = safe_float(row.get("net_buy_minus_sell_notional_usd"), 0.0)
            rows.append(normalized)
    return rows, errors


def load_symbol_bars(cache_dir: Path, symbols: set[str], interval: str) -> tuple[dict[str, dict[str, list[OhlcvBar]]], list[str]]:
    loaded: dict[str, dict[str, list[OhlcvBar]]] = {}
    errors: list[str] = []
    for symbol in sorted(symbols):
        loaded[symbol] = {}
        for market in ("spot", "futures"):
            path = cache_dir / market / symbol / f"{interval}_klines.csv"
            try:
                loaded[symbol][market] = load_ohlcv(path)
            except (FileNotFoundError, ValueError) as exc:
                loaded[symbol][market] = []
                errors.append(f"{market}_bars_unavailable:{symbol}:{portable(path)}:{exc}")
    return loaded, errors


def index_bars(bars: list[OhlcvBar]) -> dict[str, int]:
    out: dict[str, int] = {}
    for index, bar in enumerate(bars):
        key = canonical_ts(bar.ts)
        if key is not None:
            out[key] = index
    return out


def close_location(bar: OhlcvBar) -> float:
    width = max(bar.high - bar.low, 1e-12)
    return (bar.close - bar.low) / width


def return_bps(now: float, prev: float) -> float:
    if prev <= 0:
        return 0.0
    return (now / prev - 1.0) * 10_000.0


def side_adjusted_return(raw_bps: float, side: str) -> float:
    return raw_bps if side == "LONG" else -raw_bps


def setup_from_event(
    *,
    context: str,
    close_loc: float,
    divergence_bps: float,
    absorption_close_location: float,
    spot_confirm_min_bps: float,
) -> dict[str, Any] | None:
    upper = absorption_close_location
    lower = 1.0 - absorption_close_location

    if context == "long_liquidation_flush":
        if close_loc >= upper:
            side = "LONG"
            return {
                "setup": "long_flush_absorption_reversal_long",
                "mode": "absorption_reversal",
                "side": side,
                "spot_confirmed": divergence_bps >= spot_confirm_min_bps,
            }
        if close_loc <= lower:
            side = "SHORT"
            return {
                "setup": "long_flush_acceptance_continuation_short",
                "mode": "acceptance_continuation",
                "side": side,
                "spot_confirmed": divergence_bps <= -spot_confirm_min_bps,
            }
        return None

    if context == "short_liquidation_squeeze":
        if close_loc <= lower:
            side = "SHORT"
            return {
                "setup": "short_squeeze_absorption_reversal_short",
                "mode": "absorption_reversal",
                "side": side,
                "spot_confirmed": divergence_bps <= -spot_confirm_min_bps,
            }
        if close_loc >= upper:
            side = "LONG"
            return {
                "setup": "short_squeeze_acceptance_continuation_long",
                "mode": "acceptance_continuation",
                "side": side,
                "spot_confirmed": divergence_bps >= spot_confirm_min_bps,
            }
        return None

    return None


def build_records(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    context_csv = resolve_path(args.context_csv)
    cache_dir = resolve_path(args.cache_dir)
    symbols = set(parse_csv_list(args.symbols))
    horizons = parse_int_list(args.horizons)
    context_rows, errors = read_context_rows(context_csv, symbols)
    after_bar_ts = canonical_ts(args.after_bar_ts) if args.after_bar_ts else None
    if after_bar_ts is not None:
        context_rows = [row for row in context_rows if str(row["bar_ts"]) > after_bar_ts]
    actual_symbols = {row["symbol"] for row in context_rows}
    bars_by_symbol, bar_errors = load_symbol_bars(cache_dir, actual_symbols, args.interval)
    errors.extend(bar_errors[:50])

    records: list[dict[str, Any]] = []
    for symbol, markets in bars_by_symbol.items():
        futures = markets.get("futures", [])
        spot = markets.get("spot", [])
        futures_index = index_bars(futures)
        spot_index = index_bars(spot)
        symbol_rows = [row for row in context_rows if row["symbol"] == symbol]
        for row in symbol_rows:
            event_index = futures_index.get(row["bar_ts"])
            spot_event_index = spot_index.get(row["bar_ts"])
            if event_index is None or spot_event_index is None:
                if len(errors) < 50:
                    errors.append(f"missing_aligned_bar:{symbol}:{row['bar_ts']}")
                continue
            if event_index <= 0 or spot_event_index <= 0:
                continue
            event_bar = futures[event_index]
            spot_event_bar = spot[spot_event_index]
            perp_event_ret_bps = return_bps(event_bar.close, event_bar.open)
            spot_event_ret_bps = return_bps(spot_event_bar.close, spot_event_bar.open)
            divergence_bps = spot_event_ret_bps - perp_event_ret_bps
            close_loc = close_location(event_bar)
            setup = setup_from_event(
                context=str(row["dominant_context"]),
                close_loc=close_loc,
                divergence_bps=divergence_bps,
                absorption_close_location=args.absorption_close_location,
                spot_confirm_min_bps=args.spot_confirm_min_bps,
            )
            if setup is None:
                continue
            base = {
                "symbol": symbol,
                "bar_ts": row["bar_ts"],
                "context": row["dominant_context"],
                "source": row["source"],
                "setup": setup["setup"],
                "mode": setup["mode"],
                "side": setup["side"],
                "spot_confirmed": bool(setup["spot_confirmed"]),
                "total_notional_usd": round(float(row["total_notional_usd"]), 6),
                "net_buy_minus_sell_notional_usd": round(float(row["net_buy_minus_sell_notional_usd"]), 6),
                "close_location": round(close_loc, 6),
                "perp_event_ret_bps": round(perp_event_ret_bps, 6),
                "spot_event_ret_bps": round(spot_event_ret_bps, 6),
                "spot_minus_perp_event_ret_bps": round(divergence_bps, 6),
            }
            for horizon in horizons:
                future_index = event_index + horizon
                if future_index >= len(futures):
                    continue
                raw_forward_bps = return_bps(futures[future_index].close, event_bar.close)
                records.append(
                    {
                        **base,
                        "horizon_bars": horizon,
                        "raw_forward_bps": round(raw_forward_bps, 6),
                        "side_forward_bps": round(side_adjusted_return(raw_forward_bps, str(setup["side"])), 6),
                    }
                )
    inputs = {
        "context_csv": portable(context_csv),
        "cache_dir": portable(cache_dir),
        "symbols": sorted(symbols),
        "actual_symbols": sorted(actual_symbols),
        "interval": args.interval,
        "horizons_bars": horizons,
        "after_bar_ts": after_bar_ts,
        "absorption_close_location": args.absorption_close_location,
        "spot_confirm_min_bps": args.spot_confirm_min_bps,
        "min_events_per_bucket": args.min_events_per_bucket,
        "min_mean_bps": args.min_mean_bps,
        "min_winrate_pct": args.min_winrate_pct,
    }
    return records, errors, inputs


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


def grouped_summary(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, bool, int], list[dict[str, Any]]] = {}
    for row in records:
        key = (str(row["setup"]), bool(row["spot_confirmed"]), int(row["horizon_bars"]))
        groups.setdefault(key, []).append(row)
    out: list[dict[str, Any]] = []
    for (setup, spot_confirmed, horizon), rows in sorted(groups.items()):
        values = [float(row["side_forward_bps"]) for row in rows]
        out.append(
            {
                "setup": setup,
                "spot_confirmed": spot_confirmed,
                "horizon_bars": horizon,
                "summary": summarize(values),
                "symbols": sorted({row["symbol"] for row in rows}),
                "sample_events": rows[:5],
            }
        )
    out.sort(
        key=lambda item: (
            item["spot_confirmed"],
            item["summary"]["n"],
            item["summary"]["mean_bps"] if item["summary"]["mean_bps"] is not None else -999999.0,
        ),
        reverse=True,
    )
    return out


def select_candidates(groups: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in groups:
        summary = row["summary"]
        if not row["spot_confirmed"]:
            continue
        if int(summary["n"]) < args.min_events_per_bucket:
            continue
        if float(summary["mean_bps"] or -999999.0) < args.min_mean_bps:
            continue
        if float(summary["winrate_positive_pct"] or 0.0) < args.min_winrate_pct:
            continue
        candidates.append(
            {
                "setup": row["setup"],
                "horizon_bars": row["horizon_bars"],
                "summary": summary,
                "forward_status": "observer_candidate_needs_untouched_forward",
            }
        )
    return candidates


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    records, errors, inputs = build_records(args)
    groups = grouped_summary(records)
    candidates = select_candidates(groups, args)
    if not records and inputs.get("after_bar_ts"):
        decision = "post_liq_absorption_forward_observer_waiting_new_events"
        next_action = "keep collecting real liquidation context rows after the forward lock timestamp"
    elif not records:
        decision = "post_liq_absorption_blocked_no_records"
        next_action = "verify context CSV and spot/perp OHLCV alignment before interpreting this mechanism"
    elif candidates:
        decision = "post_liq_absorption_observer_candidate_needs_forward"
        next_action = "freeze the selected bucket and observe untouched future liquidation events; do not retune thresholds"
    else:
        decision = "post_liq_absorption_research_only_no_confirmed_candidate"
        next_action = "keep collecting real liquidation events; do not widen parameters on the opened sample"
    return {
        "generated_at": now_iso(),
        "tool": "tools/post_liquidation_absorption_spot_perp_confirmation.py",
        "decision": decision,
        "can_trade": False,
        "orders_allowed": False,
        "runtime_boundary": {
            "research_only": True,
            "fixed_rules": True,
            "parameter_search": False,
            "paper_entries_allowed": False,
            "live_entries_allowed": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "inputs": inputs,
        "summary": {
            "records": len(records),
            "unique_event_bars": len({(row["symbol"], row["bar_ts"], row["setup"]) for row in records}),
            "groups": len(groups),
            "observer_candidates": len(candidates),
            "errors_sample": errors[:25],
        },
        "groups": groups,
        "observer_candidates": candidates,
        "next_action": next_action,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Post-Liquidation Absorption + Spot/Perp Confirmation",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `false`",
        "",
        "## Boundary",
        "",
        "- Research-only diagnostic; no alerts, no paper entries, no orders.",
        "- Uses fixed rules from the command/config; no free parameter search.",
        "- Any selected bucket is only an observer candidate and needs untouched forward evidence.",
        "",
        "## Inputs",
        "",
        f"- Context CSV: `{report['inputs']['context_csv']}`",
        f"- Symbols: `{', '.join(report['inputs']['actual_symbols'])}`",
        f"- Horizons: `{', '.join(str(item) for item in report['inputs']['horizons_bars'])}` bars",
        f"- After bar timestamp: `{report['inputs'].get('after_bar_ts')}`",
        f"- Absorption close-location: `{report['inputs']['absorption_close_location']}`",
        f"- Spot confirmation min bps: `{report['inputs']['spot_confirm_min_bps']}`",
        "",
        "## Summary",
        "",
        f"- Records: `{report['summary']['records']}`",
        f"- Unique setup-event bars: `{report['summary']['unique_event_bars']}`",
        f"- Observer candidates: `{report['summary']['observer_candidates']}`",
        "",
        "## Group Results",
        "",
        "| Setup | Spot confirmed | Horizon | N | Mean bps | Median bps | Winrate |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["groups"][:30]:
        summary = row["summary"]
        lines.append(
            f"| `{row['setup']}` | `{row['spot_confirmed']}` | `{row['horizon_bars']}` | "
            f"`{summary['n']}` | `{summary['mean_bps']}` | `{summary['median_bps']}` | "
            f"`{summary['winrate_positive_pct']}` |"
        )
    lines.extend(["", "## Observer Candidates", ""])
    if report["observer_candidates"]:
        for item in report["observer_candidates"]:
            lines.append(
                f"- `{item['setup']}` horizon `{item['horizon_bars']}`: "
                f"n=`{item['summary']['n']}`, mean=`{item['summary']['mean_bps']}` bps, "
                f"winrate=`{item['summary']['winrate_positive_pct']}`. Status: `{item['forward_status']}`."
            )
    else:
        lines.append("- `none`")
    if report["summary"].get("errors_sample"):
        lines.extend(["", "## Errors / Skipped Rows", ""])
        for item in report["summary"]["errors_sample"]:
            lines.append(f"- `{item}`")
    lines.extend(["", "## Next Action", "", f"- {report['next_action']}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fixed-rule research diagnostic: post-liquidation absorption plus spot/perp confirmation")
    parser.add_argument("--context-csv", default="docs/BYBIT_ALL_LIQUIDATION_CONTEXT_INTAKE_2026-07-02_AFTER_PRICE_GAP_FILL_bar_context.csv")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--horizons", default="1,2,4")
    parser.add_argument("--after-bar-ts", default="")
    parser.add_argument("--absorption-close-location", type=float, default=0.60)
    parser.add_argument("--spot-confirm-min-bps", type=float, default=0.0)
    parser.add_argument("--min-events-per-bucket", type=int, default=10)
    parser.add_argument("--min-mean-bps", type=float, default=10.0)
    parser.add_argument("--min-winrate-pct", type=float, default=55.0)
    parser.add_argument("--out-prefix", default="docs/POST_LIQUIDATION_ABSORPTION_SPOT_PERP_CONFIRMATION_2026-07-03")
    args = parser.parse_args()
    if not 0.5 < args.absorption_close_location < 1.0:
        raise ValueError("--absorption-close-location must be >0.5 and <1.0")
    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "records": report["summary"]["records"],
                "observer_candidates": report["summary"]["observer_candidates"],
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

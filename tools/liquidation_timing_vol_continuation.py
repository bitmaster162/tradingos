#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
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


APPROVED_SOURCE = "bybit_v5_allLiquidation_websocket"
CONTEXT_TO_SIGN = {
    "short_liquidation_squeeze": 1,
    "long_liquidation_flush": -1,
}


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


def parse_symbols(value: str) -> list[str]:
    symbols = [item.strip().upper() for item in value.split(",") if item.strip()]
    return symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def parse_horizons(value: str) -> list[int]:
    horizons = sorted({int(item.strip()) for item in value.split(",") if item.strip()})
    if not horizons or any(item <= 0 for item in horizons):
        raise ValueError("horizons must be positive integers")
    return horizons


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def timing_bucket(last_event_time: str, bar_ts: str) -> tuple[str, float | None]:
    event_dt = parse_ts(last_event_time)
    bar_dt = parse_ts(bar_ts)
    if event_dt is None or bar_dt is None:
        return "unknown", None
    offset = (event_dt - bar_dt).total_seconds() / 60.0
    if offset < 20:
        return "early_0_20m", round(offset, 3)
    if offset < 40:
        return "mid_20_40m", round(offset, 3)
    return "late_40_60m", round(offset, 3)


def vol_bucket(vol_ratio: float | None) -> str:
    if vol_ratio is None:
        return "vol_unknown"
    if vol_ratio >= 2.0:
        return "vol_shock_ge_2x"
    if vol_ratio >= 1.2:
        return "vol_expansion_ge_1p2x"
    if vol_ratio < 0.8:
        return "vol_contraction_lt_0p8x"
    return "vol_normal_0p8_1p2x"


def read_context_rows(path: Path, symbols: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.is_file():
        return [], [f"context_csv_missing:{portable(path)}"]
    symbol_set = set(symbols)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "symbol",
            "bar_ts",
            "matched_price_bar",
            "event_count",
            "last_event_time",
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
            if symbol not in symbol_set:
                continue
            row_errors: list[str] = []
            if row.get("source") != APPROVED_SOURCE:
                row_errors.append("bad_source")
            if not as_bool(row.get("is_real_liquidation_feed")):
                row_errors.append("not_real_feed")
            if not as_bool(row.get("matched_price_bar")):
                row_errors.append("unmatched_price_bar")
            if row.get("dominant_context") not in CONTEXT_TO_SIGN:
                row_errors.append("unsupported_context")
            if canonical_ts(row.get("bar_ts")) is None:
                row_errors.append("bad_bar_ts")
            if parse_ts(row.get("last_event_time")) is None:
                row_errors.append("bad_last_event_time")
            if safe_float(row.get("total_notional_usd")) <= 0:
                row_errors.append("non_positive_notional")
            if row_errors:
                if len(errors) < 30:
                    errors.append(f"row_{row_no}:{';'.join(row_errors)}")
                continue
            normalized = dict(row)
            normalized["symbol"] = symbol
            normalized["bar_ts"] = canonical_ts(row.get("bar_ts"))
            normalized["total_notional_usd"] = safe_float(row.get("total_notional_usd"))
            normalized["event_count"] = int(safe_float(row.get("event_count")))
            rows.append(normalized)
    return rows, errors


def load_bars(symbols: list[str], interval: str, bars_root: Path) -> tuple[dict[str, list[Any]], dict[str, str]]:
    bars: dict[str, list[Any]] = {}
    paths: dict[str, str] = {}
    for symbol in symbols:
        path = bars_root / symbol / f"{interval}_klines.csv"
        paths[symbol] = portable(path)
        bars[symbol] = load_ohlcv(path) if path.is_file() else []
    return bars, paths


def build_index(bars: list[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for idx, bar in enumerate(bars):
        key = canonical_ts(bar.ts)
        if key:
            out[key] = idx
    return out


def range_bps(bar: Any) -> float | None:
    if bar.close <= 0:
        return None
    return ((bar.high - bar.low) / bar.close) * 10000.0


def median_prior_range_bps(bars: list[Any], index: int, lookback: int) -> float | None:
    start = max(0, index - lookback)
    values = [value for bar in bars[start:index] if (value := range_bps(bar)) is not None and value > 0]
    if len(values) < max(6, min(lookback, 12)):
        return None
    return statistics.median(values)


def close_location(bar: Any) -> float | None:
    spread = bar.high - bar.low
    if spread <= 0:
        return None
    return (bar.close - bar.low) / spread


def aligned_close(context: str, location: float | None) -> bool | None:
    if location is None:
        return None
    if context == "short_liquidation_squeeze":
        return location >= 0.60
    if context == "long_liquidation_flush":
        return location <= 0.40
    return None


def build_records(
    rows: list[dict[str, Any]],
    bars_by_symbol: dict[str, list[Any]],
    horizons: list[int],
    lookback: int,
    cost_bps: float,
    after_bar_ts: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    after_dt = parse_ts(after_bar_ts) if after_bar_ts else None
    index_by_symbol = {symbol: build_index(bars) for symbol, bars in bars_by_symbol.items()}
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for row in rows:
        row_dt = parse_ts(row["bar_ts"])
        if after_dt is not None and row_dt is not None and row_dt <= after_dt:
            continue
        symbol = row["symbol"]
        bars = bars_by_symbol.get(symbol, [])
        index = index_by_symbol.get(symbol, {}).get(row["bar_ts"])
        if index is None:
            if len(errors) < 30:
                errors.append(f"missing_price_bar:{symbol}:{row['bar_ts']}")
            continue
        bar = bars[index]
        prior_range = median_prior_range_bps(bars, index, lookback)
        current_range = range_bps(bar)
        vol_ratio = None if prior_range in (None, 0) or current_range is None else current_range / prior_range
        location = close_location(bar)
        context = str(row["dominant_context"])
        sign = CONTEXT_TO_SIGN[context]
        bucket, last_offset = timing_bucket(str(row["last_event_time"]), str(row["bar_ts"]))
        aligned = aligned_close(context, location)
        close_alignment = "close_aligned" if aligned is True else "close_misaligned"
        setup = f"{context}__{bucket}__{vol_bucket(vol_ratio)}__{close_alignment}"
        for horizon in horizons:
            future_index = index + horizon
            if future_index >= len(bars):
                continue
            entry = bar.close
            future_close = bars[future_index].close
            raw_return_bps = ((future_close / entry) - 1.0) * 10000.0
            continuation_return_bps = raw_return_bps * sign
            reversal_return_bps = raw_return_bps * -sign
            records.append(
                {
                    "symbol": symbol,
                    "bar_ts": row["bar_ts"],
                    "horizon_bars": horizon,
                    "dominant_context": context,
                    "setup": setup,
                    "timing_bucket": bucket,
                    "last_event_offset_min": last_offset,
                    "vol_bucket": vol_bucket(vol_ratio),
                    "vol_ratio": round(vol_ratio, 6) if vol_ratio is not None else None,
                    "close_location": round(location, 6) if location is not None else None,
                    "close_aligned": aligned,
                    "event_count": row["event_count"],
                    "total_notional_usd": row["total_notional_usd"],
                    "raw_return_bps": round(raw_return_bps, 6),
                    "continuation_after_cost_bps": round(continuation_return_bps - cost_bps, 6),
                    "reversal_after_cost_bps": round(reversal_return_bps - cost_bps, 6),
                }
            )
    return records, errors


def summarize(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean_bps": None, "median_bps": None, "winrate_positive_pct": None, "min_bps": None, "max_bps": None}
    return {
        "n": len(values),
        "mean_bps": round(statistics.fmean(values), 6),
        "median_bps": round(statistics.median(values), 6),
        "winrate_positive_pct": round(100.0 * sum(1 for value in values if value > 0) / len(values), 3),
        "min_bps": round(min(values), 6),
        "max_bps": round(max(values), 6),
    }


def summarize_groups(records: list[dict[str, Any]], min_n: int) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    keys = sorted({(row["setup"], int(row["horizon_bars"])) for row in records})
    for setup, horizon in keys:
        subset = [row for row in records if row["setup"] == setup and int(row["horizon_bars"]) == horizon]
        continuation = summarize([float(row["continuation_after_cost_bps"]) for row in subset])
        reversal = summarize([float(row["reversal_after_cost_bps"]) for row in subset])
        symbols = sorted({str(row["symbol"]) for row in subset})
        timing = sorted({str(row["timing_bucket"]) for row in subset})
        vol = sorted({str(row["vol_bucket"]) for row in subset})
        context = sorted({str(row["dominant_context"]) for row in subset})
        groups.append(
            {
                "setup": setup,
                "horizon_bars": horizon,
                "sample_ready": continuation["n"] >= min_n,
                "symbols": symbols,
                "dominant_context": context,
                "timing_bucket": timing,
                "vol_bucket": vol,
                "continuation": continuation,
                "reversal": reversal,
            }
        )
    groups.sort(
        key=lambda item: (
            item["sample_ready"],
            item["continuation"]["mean_bps"] if item["continuation"]["mean_bps"] is not None else -999999,
            item["continuation"]["n"],
        ),
        reverse=True,
    )
    return groups


def classify(records: list[dict[str, Any]], groups: list[dict[str, Any]], min_total: int, min_group_n: int) -> tuple[str, str, list[str]]:
    blockers: list[str] = []
    if len(records) < min_total:
        blockers.append("minimum_total_records")
        return (
            "liquidation_timing_vol_continuation_collecting_sample",
            "keep collecting real liquidation context; do not select a bucket yet",
            blockers,
        )
    sample_ready = [group for group in groups if group["sample_ready"]]
    if not sample_ready:
        blockers.append(f"minimum_group_n_{min_group_n}")
        return (
            "liquidation_timing_vol_continuation_no_sample_ready_bucket",
            "keep as research-only; current fixed buckets are too sparse",
            blockers,
        )
    positive = [
        group
        for group in sample_ready
        if (group["continuation"]["mean_bps"] or -999999) > 0
        and (group["continuation"]["winrate_positive_pct"] or 0) >= 55
    ]
    if positive:
        return (
            "liquidation_timing_vol_continuation_observer_candidate_needs_forward_lock",
            "create a forward-only lock for the fixed bucket before any promotion discussion",
            [],
        )
    blockers.append("no_positive_continuation_bucket_after_cost")
    return (
        "liquidation_timing_vol_continuation_rejected_first_pass",
        "do not retune opened sample; try a materially different feature class",
        blockers,
    )


def classify_selected(
    groups: list[dict[str, Any]],
    selected_setup: str,
    selected_horizons: list[int],
    min_events: int,
    min_symbols: int,
    min_positive_horizons: int,
    min_mean_bps: float,
    min_winrate_pct: float,
) -> tuple[str, str, list[str], dict[str, Any], list[dict[str, Any]]]:
    selected = [
        group
        for group in groups
        if group["setup"] == selected_setup and int(group["horizon_bars"]) in set(selected_horizons)
    ]
    selected.sort(key=lambda item: int(item["horizon_bars"]))
    blockers: list[str] = []
    if not selected:
        return (
            "liquidation_timing_vol_continuation_forward_waiting_new_events",
            "keep collecting real post-lock liquidation context rows for the locked setup",
            ["no_selected_bucket_records_after_lock"],
            {
                "selected_bucket_min_n": 0,
                "selected_symbols": [],
                "positive_horizons": 0,
                "required_new_events": min_events,
                "required_new_symbols": min_symbols,
                "required_positive_horizons": min_positive_horizons,
            },
            selected,
        )
    min_n = min(int(group["continuation"]["n"]) for group in selected)
    symbols = sorted({symbol for group in selected for symbol in group.get("symbols", []) if isinstance(symbol, str)})
    positive_horizons = 0
    for group in selected:
        cont = group["continuation"]
        if (
            int(cont["n"]) >= min_events
            and (cont["mean_bps"] or -999999) >= min_mean_bps
            and (cont["winrate_positive_pct"] or 0) >= min_winrate_pct
        ):
            positive_horizons += 1
    if min_n < min_events:
        blockers.append("minimum_new_events")
    if len(symbols) < min_symbols:
        blockers.append("minimum_new_symbols")
    if positive_horizons < min_positive_horizons:
        blockers.append("minimum_positive_horizons")
    evidence = {
        "selected_bucket_min_n": min_n,
        "selected_symbols": symbols,
        "positive_horizons": positive_horizons,
        "required_new_events": min_events,
        "required_new_symbols": min_symbols,
        "required_positive_horizons": min_positive_horizons,
        "required_mean_bps": min_mean_bps,
        "required_winrate_pct": min_winrate_pct,
    }
    if "minimum_new_events" in blockers or "minimum_new_symbols" in blockers:
        return (
            "liquidation_timing_vol_continuation_forward_collecting_sample",
            "keep observing untouched post-lock events until locked sample thresholds are met",
            blockers,
            evidence,
            selected,
        )
    if blockers:
        return (
            "liquidation_timing_vol_continuation_forward_failed_gate_for_tombstone_review",
            "forward gate failed after sample threshold; manual tombstone review before any retest",
            blockers,
            evidence,
            selected,
        )
    return (
        "liquidation_timing_vol_continuation_forward_passed_for_manual_review",
        "manual review required; this is still not paper/live permission",
        [],
        evidence,
        selected,
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Liquidation Timing + Volatility Continuation",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        "- Orders allowed: `false`",
        f"- Context rows: `{report['evidence']['context_rows']}`",
        f"- Records: `{report['evidence']['records']}`",
        f"- Groups: `{report['evidence']['groups']}`",
        f"- Sample-ready groups: `{report['evidence']['sample_ready_groups']}`",
        "",
        "## Fixed Design",
        "",
        "- Direction: short-liquidation squeeze = continuation long; long-liquidation flush = continuation short.",
        "- Timing: last event inside bar split into early 0-20m, mid 20-40m, late 40-60m.",
        "- Volatility: event-bar range divided by rolling prior median range.",
        "- Close alignment: bullish continuation needs close in upper 40% of event bar; bearish continuation needs close in lower 40%.",
        "- Result metric: continuation/reversal bps after fixed cost buffer.",
        "",
        "## Top Groups",
        "",
        "| Setup | H | N | Mean bps | Median bps | Winrate % | Reversal mean | Symbols |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for group in report["top_groups"]:
        cont = group["continuation"]
        rev = group["reversal"]
        lines.append(
            f"| `{group['setup']}` | `{group['horizon_bars']}` | `{cont['n']}` | `{cont['mean_bps']}` | "
            f"`{cont['median_bps']}` | `{cont['winrate_positive_pct']}` | `{rev['mean_bps']}` | "
            f"`{','.join(group['symbols'])}` |"
        )
    lines.extend(["", "## Blockers", ""])
    for blocker in report.get("blockers") or ["none"]:
        lines.append(f"- `{blocker}`")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- This is a fixed-rule research diagnostic.",
            "- It does not optimize thresholds on the opened sample.",
            "- It does not emit trading signals, paper intents or orders.",
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fixed-rule liquidation timing + volatility continuation research diagnostic.")
    parser.add_argument("--context-csv", default="docs/BYBIT_ALL_LIQUIDATION_CONTEXT_INTAKE_2026-07-02_AFTER_PRICE_GAP_FILL_bar_context.csv")
    parser.add_argument("--bars-root", default="data/cache/binance_spot_perp_extended/futures")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--horizons", default="1,2,4")
    parser.add_argument("--lookback-bars", type=int, default=24)
    parser.add_argument("--cost-bps", type=float, default=4.0)
    parser.add_argument("--min-total-records", type=int, default=60)
    parser.add_argument("--min-group-n", type=int, default=8)
    parser.add_argument("--after-bar-ts", default="")
    parser.add_argument("--selected-setup", default="")
    parser.add_argument("--selected-horizons", default="1,2,4")
    parser.add_argument("--min-selected-events", type=int, default=30)
    parser.add_argument("--min-selected-symbols", type=int, default=2)
    parser.add_argument("--min-positive-horizons", type=int, default=1)
    parser.add_argument("--minimum-mean-bps", type=float, default=15.0)
    parser.add_argument("--minimum-winrate-pct", type=float, default=55.0)
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_TIMING_VOL_CONTINUATION_2026-07-03_FIRST_PASS")
    args = parser.parse_args()

    symbols = parse_symbols(args.symbols)
    horizons = parse_horizons(args.horizons)
    context_path = resolve_path(args.context_csv)
    bars_root = resolve_path(args.bars_root)
    rows, row_errors = read_context_rows(context_path, symbols)
    bars_by_symbol, bar_paths = load_bars(symbols, args.interval, bars_root)
    records, record_errors = build_records(
        rows=rows,
        bars_by_symbol=bars_by_symbol,
        horizons=horizons,
        lookback=args.lookback_bars,
        cost_bps=args.cost_bps,
        after_bar_ts=args.after_bar_ts.strip() or None,
    )
    groups = summarize_groups(records, args.min_group_n)
    sample_ready = [group for group in groups if group["sample_ready"]]
    selected_setup = args.selected_setup.strip()
    selected_groups: list[dict[str, Any]] = []
    selected_evidence: dict[str, Any] = {}
    if selected_setup:
        selected_horizons = parse_horizons(args.selected_horizons)
        decision, next_action, blockers, selected_evidence, selected_groups = classify_selected(
            groups=groups,
            selected_setup=selected_setup,
            selected_horizons=selected_horizons,
            min_events=args.min_selected_events,
            min_symbols=args.min_selected_symbols,
            min_positive_horizons=args.min_positive_horizons,
            min_mean_bps=args.minimum_mean_bps,
            min_winrate_pct=args.minimum_winrate_pct,
        )
    else:
        decision, next_action, blockers = classify(records, groups, args.min_total_records, args.min_group_n)
    report = {
        "generated_at": now_iso(),
        "tool": "tools/liquidation_timing_vol_continuation.py",
        "decision": decision,
        "next_action": next_action,
        "can_trade": False,
        "orders_allowed": False,
        "inputs": {
            "context_csv": portable(context_path),
            "bars_root": portable(bars_root),
            "bar_paths": bar_paths,
            "symbols": symbols,
            "interval": args.interval,
            "horizons": horizons,
            "lookback_bars": args.lookback_bars,
            "cost_bps": args.cost_bps,
            "after_bar_ts": args.after_bar_ts.strip() or None,
            "selected_setup": selected_setup or None,
            "selected_horizons": parse_horizons(args.selected_horizons) if selected_setup else None,
        },
        "evidence": {
            "context_rows": len(rows),
            "records": len(records),
            "groups": len(groups),
            "sample_ready_groups": len(sample_ready),
            "row_errors_sample": row_errors[:20],
            "record_errors_sample": record_errors[:20],
            **selected_evidence,
        },
        "selected_groups": selected_groups,
        "top_groups": selected_groups[:15] if selected_setup else groups[:15],
        "blockers": blockers,
        "boundary": {
            "fixed_rule_diagnostic": True,
            "optimizes_thresholds": False,
            "emits_trade_signals": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "can_trade": False,
        },
    }
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": decision, "evidence": report["evidence"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

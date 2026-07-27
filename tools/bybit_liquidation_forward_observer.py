#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402


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


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


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


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def interval_delta(interval: str) -> timedelta:
    text = interval.strip().lower()
    if text.endswith("m"):
        return timedelta(minutes=int(text[:-1]))
    if text.endswith("h"):
        return timedelta(hours=int(text[:-1]))
    if text.endswith("d"):
        return timedelta(days=int(text[:-1]))
    raise ValueError(f"unsupported interval: {interval}")


def floor_dt(ts: datetime, interval: str) -> datetime:
    seconds = int(interval_delta(interval).total_seconds())
    epoch = int(ts.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % seconds), tz=timezone.utc)


def next_complete_bar_ts(ts: datetime, interval: str) -> str:
    start = floor_dt(ts, interval) + interval_delta(interval)
    return start.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def read_context_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            normalized = dict(row)
            normalized["bar_ts"] = canonical_bar_ts(row.get("bar_ts"))
            try:
                normalized["event_count"] = int(float(row.get("event_count") or 0))
                normalized["total_notional_usd"] = float(row.get("total_notional_usd") or 0)
            except ValueError:
                normalized["event_count"] = 0
                normalized["total_notional_usd"] = 0.0
            normalized["matched_price_bar"] = parse_bool(row.get("matched_price_bar"))
            normalized["is_real_liquidation_feed"] = parse_bool(row.get("is_real_liquidation_feed"))
            rows.append(normalized)
    return rows


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
            "mean_after_cost_bps": None,
            "median_bps": None,
            "winrate_positive_pct": None,
            "min_bps": None,
            "max_bps": None,
        }
    return {
        "n": len(values),
        "mean_bps": round(statistics.fmean(values), 6),
        "median_bps": round(statistics.median(values), 6),
        "winrate_positive_pct": round(100.0 * sum(value > 0 for value in values) / len(values), 3),
        "min_bps": round(min(values), 6),
        "max_bps": round(max(values), 6),
    }


def load_bars(symbols: list[str], interval: str, bars_root: Path) -> tuple[dict[str, list[Any]], dict[str, str]]:
    bars: dict[str, list[Any]] = {}
    paths: dict[str, str] = {}
    for symbol in sorted(set(symbols)):
        path = bars_root / symbol / f"{interval}_klines.csv"
        paths[symbol] = portable(path)
        bars[symbol] = load_ohlcv(path) if path.exists() else []
    return bars, paths


def forward_records(rows: list[dict[str, Any]], bars_by_symbol: dict[str, list[Any]], horizons: list[int], context: str, direction: str) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    indexes_by_symbol = {symbol: build_bar_index(bars) for symbol, bars in bars_by_symbol.items()}
    sign = continuation_sign(context)
    if direction == "reversal":
        sign *= -1
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        bars = bars_by_symbol.get(symbol, [])
        index = indexes_by_symbol.get(symbol, {}).get(row.get("bar_ts"))
        if index is None:
            if len(errors) < 25:
                errors.append(f"missing_bar:{symbol}:{row.get('bar_ts')}")
            continue
        close = float(bars[index].close)
        for horizon in horizons:
            future_index = index + horizon
            if future_index >= len(bars):
                continue
            future_close = float(bars[future_index].close)
            raw_bps = ((future_close / close) - 1.0) * 10000.0
            records.append(
                {
                    "symbol": symbol,
                    "bar_ts": row["bar_ts"],
                    "horizon_bars": horizon,
                    "context": context,
                    "direction": direction,
                    "event_count": row.get("event_count"),
                    "total_notional_usd": round(float(row.get("total_notional_usd") or 0.0), 6),
                    "raw_return_bps": round(raw_bps, 6),
                    "hypothesis_return_bps": round(raw_bps * sign, 6),
                }
            )
    return records, errors


def score_by_horizon(records: list[dict[str, Any]], horizons: list[int], cost_buffer_bps: float) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for horizon in horizons:
        values = [float(row["hypothesis_return_bps"]) for row in records if int(row["horizon_bars"]) == horizon]
        summary = summarize(values)
        mean_bps = summary.get("mean_bps")
        summary["mean_after_cost_bps"] = round(float(mean_bps) - cost_buffer_bps, 6) if mean_bps is not None else None
        summary["passes_cost_buffer"] = summary["mean_after_cost_bps"] is not None and summary["mean_after_cost_bps"] >= 0.0
        out[str(horizon)] = summary
    return out


def classify(
    *,
    event_bars: int,
    liquidation_events: int,
    resolved_records: int,
    positive_horizons: int,
    resolved_horizons_with_min_event_bars: int,
    horizon_count: int,
    lock: dict[str, Any],
) -> tuple[str, list[str], str]:
    gate = lock.get("forward_gate_required") if isinstance(lock.get("forward_gate_required"), dict) else {}
    sample_blockers: list[str] = []
    resolution_blockers: list[str] = []
    outcome_blockers: list[str] = []
    minimum_event_bars = int(gate.get("minimum_new_event_bars") or 0)
    minimum_liquidation_events = int(gate.get("minimum_new_liquidation_events") or 0)
    minimum_positive_horizons = int(gate.get("minimum_positive_horizons") or 0)
    if event_bars < minimum_event_bars:
        sample_blockers.append("minimum_new_event_bars")
    if liquidation_events < minimum_liquidation_events:
        sample_blockers.append("minimum_new_liquidation_events")
    if event_bars > 0 and resolved_records == 0:
        resolution_blockers.append("waiting_future_price_bars")
    if horizon_count > 0 and resolved_horizons_with_min_event_bars < horizon_count:
        resolution_blockers.append("minimum_resolved_event_bars_per_horizon")
    if positive_horizons < minimum_positive_horizons:
        outcome_blockers.append("minimum_positive_horizons")

    if event_bars == 0:
        return "bybit_liquidation_forward_observer_waiting_new_bars", sample_blockers, "keep collecting; no complete post-lock liquidation bars yet"
    if resolution_blockers:
        return (
            "bybit_liquidation_forward_observer_pending_resolution",
            sorted(set(sample_blockers + resolution_blockers)),
            "keep observing until enough future price bars close for the locked horizons; do not score outcome gates yet",
        )
    if sample_blockers:
        return "bybit_liquidation_forward_observer_collecting_sample", sample_blockers, "keep observing until the locked minimum sample thresholds are met"
    if outcome_blockers:
        return (
            "bybit_liquidation_forward_observer_failed_gate_for_tombstone_review",
            outcome_blockers,
            "forward gate failed after sample and horizon resolution; tombstone candidate unless manual review finds a data issue",
        )
    return "bybit_liquidation_forward_observer_passed_for_manual_review", [], "manual review required before any paper-design discussion"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Bybit Liquidation Forward Observer",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        "",
        "## Lock",
        "",
        f"- Lock ID: `{report['lock'].get('lock_id')}`",
        f"- Context: `{report['hypothesis'].get('context')}`",
        f"- Direction: `{report['hypothesis'].get('direction')}`",
        f"- Forward start bar: `{report['forward_start_bar_ts']}`",
        "",
        "## Evidence",
        "",
        f"- New event bars: `{report['evidence']['new_event_bars']}`",
        f"- New liquidation events: `{report['evidence']['new_liquidation_events']}`",
        f"- Resolved records: `{report['evidence']['resolved_records']}`",
        f"- Resolved horizons with minimum event bars: `{report['evidence'].get('resolved_horizons_with_min_event_bars')}` / `{report['evidence'].get('horizon_count')}`",
        f"- Minimum resolved event bars per horizon: `{report['evidence'].get('minimum_resolved_event_bars_per_horizon')}`",
        f"- Positive horizons after cost buffer: `{report['evidence']['positive_horizons_after_cost_buffer']}`",
        "",
        "## By Horizon",
        "",
        "| Horizon | N | Mean bps | Mean after cost | Winrate | Pass |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for horizon, summary in report.get("by_horizon", {}).items():
        lines.append(
            f"| {horizon} | `{summary.get('n')}` | `{summary.get('mean_bps')}` | `{summary.get('mean_after_cost_bps')}` | "
            f"`{summary.get('winrate_positive_pct')}` | `{summary.get('passes_cost_buffer')}` |"
        )
    lines.extend(["", "## Blockers", ""])
    for blocker in report.get("blockers") or ["none"]:
        lines.append(f"- `{blocker}`")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Observer-only forward scorer.",
            "- No alerts, no paper entries, no live entries, no orders.",
            "- The lock-hour partial bar is excluded to avoid leakage.",
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Observer-only scorer for the accepted Bybit liquidation forward lock.")
    parser.add_argument("--lock", default="configs/BYBIT_LIQUIDATION_FORWARD_OBSERVER_LOCK_2026-07-02.json")
    parser.add_argument("--context-csv", default="")
    parser.add_argument("--bars-root", default="data/cache/binance_spot_perp_extended/futures")
    parser.add_argument("--out-prefix", default="docs/BYBIT_LIQUIDATION_FORWARD_OBSERVER_2026-07-02")
    args = parser.parse_args()

    lock_path = resolve_path(args.lock)
    lock = read_json(lock_path)
    if lock.get("status") != "accepted_forward_observer_only":
        raise SystemExit(f"lock is not accepted_forward_observer_only: {portable(lock_path)}")
    if lock.get("can_trade") is not False or lock.get("orders_allowed") is not False:
        raise SystemExit("lock must keep can_trade=false and orders_allowed=false")

    hypothesis = lock.get("hypothesis") if isinstance(lock.get("hypothesis"), dict) else {}
    interval = str(hypothesis.get("interval") or "1h")
    start_ts = parse_ts(lock.get("forward_start_at"))
    if start_ts is None:
        raise SystemExit("forward_start_at is invalid")
    forward_start_bar = next_complete_bar_ts(start_ts, interval)
    context_path = resolve_path(args.context_csv or str(lock.get("source_study") or ""))
    all_rows = read_context_rows(context_path)
    symbols = [str(item).upper() for item in hypothesis.get("symbols", [])]
    context = str(hypothesis.get("context") or "")
    direction = str(hypothesis.get("direction") or "continuation")
    selected_rows = [
        row
        for row in all_rows
        if str(row.get("symbol") or "").upper() in symbols
        and row.get("bar_ts") is not None
        and str(row.get("bar_ts")) >= forward_start_bar
        and row.get("matched_price_bar") is True
        and row.get("is_real_liquidation_feed") is True
        and row.get("dominant_context") == context
        and row.get("source") == "bybit_v5_allLiquidation_websocket"
    ]
    horizons = [int(item) for item in hypothesis.get("candidate_horizons_bars", [])]
    bars_by_symbol, bar_paths = load_bars(symbols, interval, resolve_path(args.bars_root))
    records, errors = forward_records(selected_rows, bars_by_symbol, horizons, context, direction)
    gate = lock.get("forward_gate_required") if isinstance(lock.get("forward_gate_required"), dict) else {}
    cost_buffer = float(gate.get("cost_buffer_bps") or 0.0)
    by_horizon = score_by_horizon(records, horizons, cost_buffer)
    positive_horizons = sum(1 for item in by_horizon.values() if item.get("passes_cost_buffer"))
    minimum_event_bars = int(gate.get("minimum_new_event_bars") or 0)
    resolved_horizons_with_min_event_bars = sum(
        1 for item in by_horizon.values() if int(item.get("n") or 0) >= minimum_event_bars
    )
    event_bars = len(selected_rows)
    liquidation_events = sum(int(row.get("event_count") or 0) for row in selected_rows)
    decision, blockers, next_action = classify(
        event_bars=event_bars,
        liquidation_events=liquidation_events,
        resolved_records=len(records),
        positive_horizons=positive_horizons,
        resolved_horizons_with_min_event_bars=resolved_horizons_with_min_event_bars,
        horizon_count=len(horizons),
        lock=lock,
    )
    report = {
        "generated_at": now_iso(),
        "tool": "tools/bybit_liquidation_forward_observer.py",
        "decision": decision,
        "can_trade": False,
        "orders_allowed": False,
        "lock_path": portable(lock_path),
        "lock": {"lock_id": lock.get("lock_id"), "status": lock.get("status")},
        "hypothesis": hypothesis,
        "forward_start_at": lock.get("forward_start_at"),
        "forward_start_bar_ts": forward_start_bar,
        "context_csv": portable(context_path),
        "bars_by_symbol": bar_paths,
        "evidence": {
            "all_context_rows": len(all_rows),
            "new_event_bars": event_bars,
            "new_liquidation_events": liquidation_events,
            "resolved_records": len(records),
            "positive_horizons_after_cost_buffer": positive_horizons,
            "resolved_horizons_with_min_event_bars": resolved_horizons_with_min_event_bars,
            "horizon_count": len(horizons),
            "minimum_resolved_event_bars_per_horizon": minimum_event_bars,
        },
        "by_horizon": by_horizon,
        "sample_records": records[:10],
        "errors": errors[:25],
        "blockers": blockers,
        "next_action": next_action,
        "boundary": {
            "observer_only": True,
            "emits_alerts": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": decision,
                "new_event_bars": event_bars,
                "new_liquidation_events": liquidation_events,
                "positive_horizons": positive_horizons,
                "blockers": blockers,
                "out": portable(out.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

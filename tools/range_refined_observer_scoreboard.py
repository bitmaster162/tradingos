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


@dataclass(frozen=True)
class Outcome:
    status: str
    r: float | None
    entry_ts: str | None
    entry_price: float | None
    stop: float | None
    take: float | None
    exit_ts: str | None
    exit_price: float | None
    bars_held: int | None
    reason: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def expected_strategy_id(args: argparse.Namespace) -> str | None:
    if args.strategy_id:
        return str(args.strategy_id)
    if not args.refiner_report:
        return None
    payload = read_json(resolve_path(args.refiner_report))
    selected = payload.get("selected_candidate") if isinstance(payload.get("selected_candidate"), dict) else {}
    return str(selected.get("strategy_id")) if selected.get("strategy_id") else None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_bars(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    bars: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                bar = {
                    "time": str(row.get("time") or row.get("ts") or row.get("datetime") or "").strip(),
                    "open": float(row.get("open", "nan")),
                    "high": float(row.get("high", "nan")),
                    "low": float(row.get("low", "nan")),
                    "close": float(row.get("close", "nan")),
                }
            except (TypeError, ValueError):
                continue
            if bar["time"] and all(math.isfinite(float(bar[key])) for key in ("open", "high", "low", "close")):
                bars.append(bar)
    return bars


def parse_rr(value: Any) -> tuple[float, float]:
    raw = str(value or "").replace("x", ":")
    if ":" not in raw:
        raise ValueError(f"invalid_rr:{value}")
    left, right = raw.split(":", 1)
    return float(left), float(right)


def max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return round(worst, 6)


def max_losing_streak(values: list[float]) -> int:
    longest = 0
    current = 0
    for value in values:
        if value <= 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def resolve_outcome(event: dict[str, Any], bars: list[dict[str, Any]], same_bar_policy: str) -> Outcome:
    signal_ts = str(event.get("bar_ts") or "")
    side = str(event.get("side") or "").lower()
    try:
        atr = float(event.get("atr"))
        stop_atr, take_atr = parse_rr(event.get("rr"))
        max_hold_bars = int(event.get("max_hold_bars") or 0)
    except (TypeError, ValueError):
        return Outcome("unresolved", None, None, None, None, None, None, None, None, "invalid_signal_payload")
    if side not in {"long", "short"}:
        return Outcome("unresolved", None, None, None, None, None, None, None, None, "unsupported_side")
    if not math.isfinite(atr) or atr <= 0:
        return Outcome("unresolved", None, None, None, None, None, None, None, None, "invalid_atr")
    if stop_atr <= 0 or take_atr <= 0:
        return Outcome("unresolved", None, None, None, None, None, None, None, None, "invalid_rr")
    index_by_ts = {str(bar["time"]): idx for idx, bar in enumerate(bars)}
    signal_idx = index_by_ts.get(signal_ts)
    if signal_idx is None:
        return Outcome("unresolved", None, None, None, None, None, None, None, None, "signal_bar_not_in_cache")
    entry_idx = signal_idx + 1
    if entry_idx >= len(bars):
        return Outcome("unresolved", None, None, None, None, None, None, None, None, "entry_bar_not_closed_yet")
    entry_bar = bars[entry_idx]
    entry = float(entry_bar["open"])
    risk = stop_atr * atr
    reward = take_atr * atr
    if side == "long":
        stop = entry - risk
        take = entry + reward
    else:
        stop = entry + risk
        take = entry - reward
    if max_hold_bars <= 0:
        max_hold_bars = 1
    end_idx = min(len(bars) - 1, entry_idx + max_hold_bars - 1)
    for idx in range(entry_idx, end_idx + 1):
        bar = bars[idx]
        high = float(bar["high"])
        low = float(bar["low"])
        if side == "long":
            hit_stop = low <= stop
            hit_take = high >= take
            if hit_stop and hit_take:
                if same_bar_policy == "ignore_ambiguous":
                    return Outcome("ambiguous", None, str(entry_bar["time"]), round(entry, 8), round(stop, 8), round(take, 8), str(bar["time"]), None, idx - entry_idx + 1, "same_bar_stop_and_take")
                return Outcome("loss", -1.0, str(entry_bar["time"]), round(entry, 8), round(stop, 8), round(take, 8), str(bar["time"]), round(stop, 8), idx - entry_idx + 1, "same_bar_conservative_stop")
            if hit_stop:
                return Outcome("loss", -1.0, str(entry_bar["time"]), round(entry, 8), round(stop, 8), round(take, 8), str(bar["time"]), round(stop, 8), idx - entry_idx + 1, "stop")
            if hit_take:
                return Outcome("win", round(take_atr / stop_atr, 6), str(entry_bar["time"]), round(entry, 8), round(stop, 8), round(take, 8), str(bar["time"]), round(take, 8), idx - entry_idx + 1, "take")
        else:
            hit_stop = high >= stop
            hit_take = low <= take
            if hit_stop and hit_take:
                if same_bar_policy == "ignore_ambiguous":
                    return Outcome("ambiguous", None, str(entry_bar["time"]), round(entry, 8), round(stop, 8), round(take, 8), str(bar["time"]), None, idx - entry_idx + 1, "same_bar_stop_and_take")
                return Outcome("loss", -1.0, str(entry_bar["time"]), round(entry, 8), round(stop, 8), round(take, 8), str(bar["time"]), round(stop, 8), idx - entry_idx + 1, "same_bar_conservative_stop")
            if hit_stop:
                return Outcome("loss", -1.0, str(entry_bar["time"]), round(entry, 8), round(stop, 8), round(take, 8), str(bar["time"]), round(stop, 8), idx - entry_idx + 1, "stop")
            if hit_take:
                return Outcome("win", round(take_atr / stop_atr, 6), str(entry_bar["time"]), round(entry, 8), round(stop, 8), round(take, 8), str(bar["time"]), round(take, 8), idx - entry_idx + 1, "take")
    if len(bars) - entry_idx < max_hold_bars:
        return Outcome("unresolved", None, str(entry_bar["time"]), round(entry, 8), round(stop, 8), round(take, 8), None, None, None, "max_hold_not_reached_yet")
    exit_bar = bars[end_idx]
    exit_price = float(exit_bar["close"])
    r_value = (exit_price - entry) / risk if side == "long" else (entry - exit_price) / risk
    return Outcome("time_exit", round(r_value, 6), str(entry_bar["time"]), round(entry, 8), round(stop, 8), round(take, 8), str(exit_bar["time"]), round(exit_price, 8), end_idx - entry_idx + 1, "time_exit")


def summarize(outcomes: list[dict[str, Any]], signal_events: int, filtered_events: int, no_signal_events: int, args: argparse.Namespace) -> dict[str, Any]:
    resolved = [item for item in outcomes if isinstance(item.get("r"), (int, float))]
    values = [float(item["r"]) for item in resolved]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    expectancy = round(sum(values) / len(values), 6) if values else None
    winrate = round(100.0 * len(wins) / len(values), 3) if values else None
    avg_rr = None
    rr_values = []
    for item in outcomes:
        try:
            stop_atr, take_atr = parse_rr(item.get("rr"))
            rr_values.append(take_atr / stop_atr)
        except (TypeError, ValueError):
            pass
    if rr_values:
        avg_rr = round(sum(rr_values) / len(rr_values), 6)
    breakeven = round(100.0 / (1.0 + avg_rr), 3) if avg_rr and avg_rr > 0 else None
    if signal_events == 0:
        classification = "no_observer_signals_yet"
    elif not values:
        classification = "pending_only"
    elif len(values) < args.min_resolved:
        classification = "watchlist_positive_insufficient_sample" if expectancy is not None and expectancy >= args.min_expectancy_r else "insufficient_forward_sample"
    elif expectancy is not None and expectancy >= args.min_expectancy_r and (breakeven is None or (winrate or 0.0) >= breakeven):
        classification = "candidate_for_forward_review"
    else:
        classification = "negative_or_mixed"
    return {
        "classification": classification,
        "observer_signal_events": signal_events,
        "filtered_out_events": filtered_events,
        "no_signal_events": no_signal_events,
        "resolved": len(values),
        "unresolved": len(outcomes) - len(values),
        "wins": len(wins),
        "losses_or_nonpositive": len(losses),
        "winrate_pct": winrate,
        "expectancy_r": expectancy,
        "net_r_total": round(sum(values), 6) if values else None,
        "avg_win_r": round(sum(wins) / len(wins), 6) if wins else None,
        "avg_loss_r": round(sum(losses) / len(losses), 6) if losses else None,
        "max_drawdown_r": max_drawdown(values) if values else None,
        "max_losing_streak": max_losing_streak(values) if values else None,
        "avg_planned_rr": avg_rr,
        "breakeven_winrate_pct": breakeven,
        "min_resolved_required": args.min_resolved,
        "min_expectancy_r_required": args.min_expectancy_r,
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# Range Refined Observer Scoreboard",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Scores observer-only `range_refined_signal_observed` events.",
        "- Does not read private data, does not send orders and does not create paper-entry intents.",
        "- A positive scoreboard is not live permission.",
        "",
        "## Summary",
        "",
        f"- Classification: `{summary.get('classification')}`.",
        f"- Observer signals: `{summary.get('observer_signal_events')}`.",
        f"- Filtered out / no signal: `{summary.get('filtered_out_events')}` / `{summary.get('no_signal_events')}`.",
        f"- Resolved / unresolved: `{summary.get('resolved')}` / `{summary.get('unresolved')}`.",
        f"- Winrate: `{summary.get('winrate_pct')}`%.",
        f"- Expectancy: `{summary.get('expectancy_r')}` R.",
        f"- Net R: `{summary.get('net_r_total')}`.",
        f"- Max DD: `{summary.get('max_drawdown_r')}` R.",
        f"- Breakeven WR from planned RR: `{summary.get('breakeven_winrate_pct')}`%.",
        "",
        "## Files",
        "",
        f"- Journal: `{report.get('journal_path')}`.",
        f"- Cache CSV: `{report.get('cache_csv')}`.",
        "",
    ]
    outcomes = report.get("outcomes") if isinstance(report.get("outcomes"), list) else []
    if outcomes:
        lines.extend(["## Latest Outcomes", ""])
        for item in outcomes[-10:]:
            lines.append(
                f"- `{item.get('signal_key')}` status `{item.get('status')}` R `{item.get('r')}` entry `{item.get('entry_ts')}` exit `{item.get('exit_ts')}` reason `{item.get('reason')}`."
            )
        lines.append("")
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    journal_path = resolve_path(args.journal_path)
    cache_csv = resolve_path(args.cache_csv)
    all_events = read_jsonl(journal_path)
    strategy_id = expected_strategy_id(args)
    events = [event for event in all_events if event.get("strategy_id") == strategy_id] if strategy_id else all_events
    bars = load_bars(cache_csv)
    signal_events_by_key: dict[str, dict[str, Any]] = {}
    filtered_events = [event for event in events if event.get("event_type") == "range_refined_filtered_out"]
    no_signal_events = [event for event in events if event.get("event_type") == "range_refined_no_signal"]
    for event in events:
        if event.get("event_type") != "range_refined_signal_observed":
            continue
        key = str(event.get("signal_key") or f"{event.get('strategy_id')}|{event.get('bar_ts')}")
        signal_events_by_key[key] = event
    outcomes: list[dict[str, Any]] = []
    for key, event in sorted(signal_events_by_key.items(), key=lambda item: str(item[1].get("bar_ts") or "")):
        outcome = resolve_outcome(event, bars, args.same_bar_policy)
        outcomes.append(
            {
                "signal_key": key,
                "strategy_id": event.get("strategy_id"),
                "filter_mode": event.get("filter_mode"),
                "side": event.get("side"),
                "rr": event.get("rr"),
                "signal_bar_ts": event.get("bar_ts"),
                "status": outcome.status,
                "r": outcome.r,
                "entry_ts": outcome.entry_ts,
                "entry_price": outcome.entry_price,
                "stop": outcome.stop,
                "take": outcome.take,
                "exit_ts": outcome.exit_ts,
                "exit_price": outcome.exit_price,
                "bars_held": outcome.bars_held,
                "reason": outcome.reason,
                "data_degraded": event.get("data_degraded"),
                "missing_filter_inputs": event.get("missing_filter_inputs"),
            }
        )
    summary = summarize(outcomes, len(signal_events_by_key), len(filtered_events), len(no_signal_events), args)
    return {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "range_refined_observer_scoreboard_public_cache_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
        },
        "journal_path": rel_path(journal_path),
        "cache_csv": rel_path(cache_csv),
        "expected_strategy_id": strategy_id,
        "total_journal_events": len(all_events),
        "relevant_strategy_events": len(events),
        "ignored_legacy_strategy_events": len(all_events) - len(events),
        "summary": summary,
        "outcomes": outcomes,
        "decision": "observer_scoreboard_no_trade_permission",
        "next_action": "wait_for_range_refined_signal_observations",
        "can_trade": False,
    }


def main() -> int:
    if hasattr(__import__("sys").stdout, "reconfigure"):
        __import__("sys").stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Score observer-only outcomes for selected refined RANGE candidate")
    parser.add_argument("--journal-path", default="logs/forward_paper_feed/range_refined_forward_observer.jsonl")
    parser.add_argument("--refiner-report", default="")
    parser.add_argument("--strategy-id", default="")
    parser.add_argument("--cache-csv", default="_dl/forward_paper_feed/cache/futures/BTCUSDT/4h_klines.csv")
    parser.add_argument("--same-bar-policy", choices=["conservative_stop", "ignore_ambiguous"], default="conservative_stop")
    parser.add_argument("--min-resolved", type=int, default=30)
    parser.add_argument("--min-expectancy-r", type=float, default=0.05)
    parser.add_argument("--out-prefix", default="docs/RANGE_REFINED_OBSERVER_SCOREBOARD_2026-06-16")
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "classification": report["summary"].get("classification"),
                "observer_signal_events": report["summary"].get("observer_signal_events"),
                "resolved": report["summary"].get("resolved"),
                "expectancy_r": report["summary"].get("expectancy_r"),
                "json": rel_path(out_prefix.with_suffix(".json")),
                "md": rel_path(out_prefix.with_suffix(".md")),
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

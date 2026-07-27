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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


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


def load_bars(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    bars: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                bars.append(
                    {
                        "time": row.get("time") or row.get("ts") or row.get("datetime"),
                        "open": float(row.get("open", "nan")),
                        "high": float(row.get("high", "nan")),
                        "low": float(row.get("low", "nan")),
                        "close": float(row.get("close", "nan")),
                    }
                )
            except (TypeError, ValueError):
                continue
    return [
        bar
        for bar in bars
        if bar.get("time")
        and all(math.isfinite(float(bar[key])) for key in ("open", "high", "low", "close"))
    ]


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


@dataclass(frozen=True)
class Outcome:
    status: str
    r: float | None
    exit_ts: str | None
    exit_price: float | None
    bars_held: int | None
    reason: str


def resolve_outcome(event: dict[str, Any], bars: list[dict[str, Any]], same_bar_policy: str) -> Outcome:
    entry_ts = str(event.get("entry_bar_ts") or "")
    side = str(event.get("side") or "").lower()
    try:
        entry = float(event.get("entry"))
        stop = float(event.get("stop"))
        take = float(event.get("take"))
        max_hold_bars = int(event.get("max_hold_bars") or 0)
    except (TypeError, ValueError):
        return Outcome("unresolved", None, None, None, None, "invalid_entry_payload")
    if side not in {"long", "short"}:
        return Outcome("unresolved", None, None, None, None, "unsupported_side")
    risk = abs(entry - stop)
    if not math.isfinite(risk) or risk <= 0:
        return Outcome("unresolved", None, None, None, None, "invalid_risk_distance")
    index_by_ts = {str(bar["time"]): idx for idx, bar in enumerate(bars)}
    entry_idx = index_by_ts.get(entry_ts)
    if entry_idx is None:
        return Outcome("unresolved", None, None, None, None, "entry_bar_not_closed_yet")
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
                    return Outcome("ambiguous", None, str(bar["time"]), None, idx - entry_idx + 1, "same_bar_stop_and_take")
                return Outcome("loss", -1.0, str(bar["time"]), stop, idx - entry_idx + 1, "same_bar_conservative_stop")
            if hit_stop:
                return Outcome("loss", -1.0, str(bar["time"]), stop, idx - entry_idx + 1, "stop")
            if hit_take:
                return Outcome("win", round((take - entry) / risk, 6), str(bar["time"]), take, idx - entry_idx + 1, "take")
        else:
            hit_stop = high >= stop
            hit_take = low <= take
            if hit_stop and hit_take:
                if same_bar_policy == "ignore_ambiguous":
                    return Outcome("ambiguous", None, str(bar["time"]), None, idx - entry_idx + 1, "same_bar_stop_and_take")
                return Outcome("loss", -1.0, str(bar["time"]), stop, idx - entry_idx + 1, "same_bar_conservative_stop")
            if hit_stop:
                return Outcome("loss", -1.0, str(bar["time"]), stop, idx - entry_idx + 1, "stop")
            if hit_take:
                return Outcome("win", round((entry - take) / risk, 6), str(bar["time"]), take, idx - entry_idx + 1, "take")
    if len(bars) - entry_idx < max_hold_bars:
        return Outcome("unresolved", None, None, None, None, "max_hold_not_reached_yet")
    exit_bar = bars[end_idx]
    exit_price = float(exit_bar["close"])
    r_value = (exit_price - entry) / risk if side == "long" else (entry - exit_price) / risk
    return Outcome("time_exit", round(r_value, 6), str(exit_bar["time"]), round(exit_price, 8), end_idx - entry_idx + 1, "time_exit")


def summarize_outcomes(outcomes: list[dict[str, Any]], planned_rr_values: list[float], args: argparse.Namespace) -> dict[str, Any]:
    resolved = [item for item in outcomes if isinstance(item.get("r"), (int, float))]
    values = [float(item["r"]) for item in resolved]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    avg_rr = round(sum(planned_rr_values) / len(planned_rr_values), 6) if planned_rr_values else None
    breakeven = round(100.0 / (1.0 + avg_rr), 3) if avg_rr and avg_rr > 0 else None
    expectancy = round(sum(values) / len(values), 6) if values else None
    winrate = round(100.0 * len(wins) / len(values), 3) if values else None
    if not outcomes:
        classification = "no_forward_entries_yet"
    elif not values:
        classification = "pending_only"
    elif len(values) < args.min_resolved:
        classification = "watchlist_positive_insufficient_sample" if expectancy is not None and expectancy >= args.min_expectancy_r else "insufficient_forward_sample"
    elif expectancy is not None and expectancy >= args.min_expectancy_r and (breakeven is None or (winrate or 0) >= breakeven):
        classification = "candidate_for_execution_design"
    else:
        classification = "negative_or_mixed"
    return {
        "classification": classification,
        "entry_intents": len(outcomes),
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
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# Strategy Mix Forward Scoreboard",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Public/cache data only.",
        "- Scores paper-entry intents from the forward journal.",
        "- No private credentials, no exchange account, no orders.",
        "",
        "## Summary",
        "",
        f"- Classification: `{summary.get('classification')}`.",
        f"- Entry intents: `{summary.get('entry_intents')}`.",
        f"- Resolved / unresolved: `{summary.get('resolved')}` / `{summary.get('unresolved')}`.",
        f"- Winrate: `{summary.get('winrate_pct')}`%.",
        f"- Expectancy: `{summary.get('expectancy_r')}` R.",
        f"- Net R: `{summary.get('net_r_total')}`.",
        f"- Max DD: `{summary.get('max_drawdown_r')}` R.",
        f"- Breakeven WR from planned RR: `{summary.get('breakeven_winrate_pct')}`%.",
        "",
        "## Gate",
        "",
        f"- Required resolved forward trades: `{summary.get('min_resolved_required')}`.",
        f"- Required expectancy: `{summary.get('min_expectancy_r_required')}` R.",
        "- `candidate_for_execution_design` is still not live permission; it only allows designing an execution gate.",
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
                f"- `{item.get('signal_key')}` status `{item.get('status')}` R `{item.get('r')}` exit `{item.get('exit_ts')}` reason `{item.get('reason')}`."
            )
        lines.append("")
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    journal_path = resolve_path(args.journal_path)
    cache_csv = resolve_path(args.cache_csv)
    events = read_jsonl(journal_path)
    bars = load_bars(cache_csv)
    no_signal_events = [event for event in events if event.get("event_type") == "forward_no_signal"]
    signal_events = [event for event in events if event.get("event_type") == "forward_signal"]
    raw_entry_events = [event for event in events if event.get("event_type") == "forward_paper_entry_intent"]
    entry_events_by_key: dict[str, dict[str, Any]] = {}
    for event in raw_entry_events:
        key = str(event.get("signal_key") or f"{event.get('strategy_id')}|{event.get('signal_bar_ts')}")
        entry_events_by_key[key] = event
    outcomes: list[dict[str, Any]] = []
    planned_rr_values: list[float] = []
    for key, event in sorted(entry_events_by_key.items(), key=lambda item: str(item[1].get("signal_bar_ts") or "")):
        try:
            entry = float(event.get("entry"))
            stop = float(event.get("stop"))
            take = float(event.get("take"))
            risk = abs(entry - stop)
            reward = abs(take - entry)
            if risk > 0:
                planned_rr_values.append(round(reward / risk, 6))
        except (TypeError, ValueError):
            pass
        outcome = resolve_outcome(event, bars, args.same_bar_policy)
        outcomes.append(
            {
                "signal_key": key,
                "strategy_id": event.get("strategy_id"),
                "side": event.get("side"),
                "signal_bar_ts": event.get("signal_bar_ts"),
                "entry_bar_ts": event.get("entry_bar_ts"),
                "entry": event.get("entry"),
                "stop": event.get("stop"),
                "take": event.get("take"),
                "status": outcome.status,
                "r": outcome.r,
                "exit_ts": outcome.exit_ts,
                "exit_price": outcome.exit_price,
                "bars_held": outcome.bars_held,
                "reason": outcome.reason,
            }
        )
    summary = summarize_outcomes(outcomes, planned_rr_values, args)
    unique_checked_bars = sorted({str(event.get("latest_closed_bar_ts")) for event in no_signal_events if event.get("latest_closed_bar_ts")})
    return {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "forward_paper_scoreboard_public_cache_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "journal_path": str(journal_path),
        "cache_csv": str(cache_csv),
        "journal_events": len(events),
        "forward_signal_events": len(signal_events),
        "forward_no_signal_events": len(no_signal_events),
        "unique_no_signal_bars_checked": len(unique_checked_bars),
        "latest_no_signal_bar": unique_checked_bars[-1] if unique_checked_bars else None,
        "summary": summary,
        "outcomes": outcomes,
        "can_trade": False,
        "decision": "forward_evidence_only_no_orders",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Score forward paper entry intents for strategy mix candidate")
    parser.add_argument("--journal-path", default="logs/forward_paper_feed/strategy_mix_forward_paper_feed.jsonl")
    parser.add_argument("--cache-csv", default="_dl/forward_paper_feed/cache/futures/BTCUSDT/4h_klines.csv")
    parser.add_argument("--same-bar-policy", choices=["conservative_loss", "ignore_ambiguous"], default="conservative_loss")
    parser.add_argument("--min-resolved", type=int, default=30)
    parser.add_argument("--min-expectancy-r", type=float, default=0.05)
    parser.add_argument("--out-prefix", default="docs/STRATEGY_MIX_FORWARD_SCOREBOARD_2026-06-08")
    args = parser.parse_args()
    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(out_prefix.with_suffix(".json")), "md": str(out_prefix.with_suffix(".md")), "summary": report["summary"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

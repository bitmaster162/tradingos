#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_CACHE_DIR = ROOT / "data" / "cache" / "binance_spot_perp_extended"
DEFAULT_JOURNAL = ROOT / "logs" / "forward_paper_feed" / "crowd_fade_positioning_shadow_observer.jsonl"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "CROWD_FADE_POSITIONING_SHADOW_SCOREBOARD_2026-06-19"


@dataclass(frozen=True)
class Bar:
    ts: str
    open: float
    high: float
    low: float
    close: float


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str | Path) -> Path:
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


def load_bars(path: Path) -> list[Bar]:
    if not path.exists():
        return []
    bars: list[Bar] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                bar = Bar(
                    ts=str(row.get("time") or "").strip(),
                    open=float(row.get("open", "nan")),
                    high=float(row.get("high", "nan")),
                    low=float(row.get("low", "nan")),
                    close=float(row.get("close", "nan")),
                )
            except (TypeError, ValueError):
                continue
            if bar.ts and all(math.isfinite(value) for value in (bar.open, bar.high, bar.low, bar.close)):
                bars.append(bar)
    return bars


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


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
    current = 0
    longest = 0
    for value in values:
        if value <= 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def dedupe_events(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for event in events:
        key = str(event.get("signal_key") or "")
        if not key:
            strategy_id = str(event.get("strategy_id") or "")
            signal_time = str(event.get("signal_time") or "")
            side = str(event.get("side_hint") or "")
            key = f"{strategy_id}|{signal_time}|{side}"
        if key.strip("|"):
            unique[key] = event
    return unique


def resolve_outcome(event: dict[str, Any], bars: list[Bar], same_bar_policy: str) -> dict[str, Any]:
    signal_time = str(event.get("signal_time") or "").strip()
    side = str(event.get("side_hint") or "").upper()
    atr = safe_float(event.get("atr"))
    stop_atr = safe_float(event.get("stop_atr"))
    take_atr = safe_float(event.get("take_atr"))
    try:
        hold = int(float(str(event.get("hold") or 0)))
    except (TypeError, ValueError):
        hold = 0
    if side not in {"LONG", "SHORT"}:
        return {"status": "unresolved", "r": None, "reason": "unsupported_side"}
    if atr is None or atr <= 0 or stop_atr is None or take_atr is None or stop_atr <= 0 or take_atr <= 0:
        return {"status": "unresolved", "r": None, "reason": "invalid_risk_payload"}
    if hold <= 0:
        return {"status": "unresolved", "r": None, "reason": "invalid_hold"}

    index_by_ts = {bar.ts: index for index, bar in enumerate(bars)}
    signal_idx = index_by_ts.get(signal_time)
    if signal_idx is None:
        return {"status": "unresolved", "r": None, "reason": "signal_bar_not_in_cache"}
    entry_idx = signal_idx + 1
    if entry_idx >= len(bars):
        return {"status": "unresolved", "r": None, "reason": "entry_bar_not_closed_yet"}

    entry_bar = bars[entry_idx]
    entry = entry_bar.open
    risk = stop_atr * atr
    reward = take_atr * atr
    if side == "SHORT":
        stop = entry + risk
        take = entry - reward
    else:
        stop = entry - risk
        take = entry + reward

    end_idx = min(len(bars) - 1, entry_idx + hold)
    if len(bars) - 1 < entry_idx + hold:
        final_bar = bars[-1]
        return {
            "status": "unresolved",
            "r": None,
            "reason": "max_hold_not_reached_yet",
            "entry_ts": entry_bar.ts,
            "entry_price": round(entry, 8),
            "stop": round(stop, 8),
            "take": round(take, 8),
            "latest_ts": final_bar.ts,
        }

    for index in range(entry_idx, end_idx + 1):
        bar = bars[index]
        if side == "SHORT":
            stop_hit = bar.high >= stop
            take_hit = bar.low <= take
        else:
            stop_hit = bar.low <= stop
            take_hit = bar.high >= take
        if stop_hit and take_hit:
            if same_bar_policy == "ignore_ambiguous":
                return {
                    "status": "ambiguous",
                    "r": None,
                    "reason": "same_bar_stop_and_take",
                    "entry_ts": entry_bar.ts,
                    "entry_price": round(entry, 8),
                    "stop": round(stop, 8),
                    "take": round(take, 8),
                    "exit_ts": bar.ts,
                    "bars_held": index - entry_idx + 1,
                }
            return {
                "status": "loss",
                "r": -1.0,
                "reason": "same_bar_conservative_stop",
                "entry_ts": entry_bar.ts,
                "entry_price": round(entry, 8),
                "stop": round(stop, 8),
                "take": round(take, 8),
                "exit_ts": bar.ts,
                "exit_price": round(stop, 8),
                "bars_held": index - entry_idx + 1,
            }
        if take_hit:
            return {
                "status": "win",
                "r": round(take_atr / stop_atr, 6),
                "reason": "take_profit",
                "entry_ts": entry_bar.ts,
                "entry_price": round(entry, 8),
                "stop": round(stop, 8),
                "take": round(take, 8),
                "exit_ts": bar.ts,
                "exit_price": round(take, 8),
                "bars_held": index - entry_idx + 1,
            }
        if stop_hit:
            return {
                "status": "loss",
                "r": -1.0,
                "reason": "stop_loss",
                "entry_ts": entry_bar.ts,
                "entry_price": round(entry, 8),
                "stop": round(stop, 8),
                "take": round(take, 8),
                "exit_ts": bar.ts,
                "exit_price": round(stop, 8),
                "bars_held": index - entry_idx + 1,
            }

    exit_bar = bars[end_idx]
    if side == "SHORT":
        r_value = (entry - exit_bar.close) / risk
    else:
        r_value = (exit_bar.close - entry) / risk
    return {
        "status": "time_exit",
        "r": round(r_value, 6),
        "reason": "time_exit",
        "entry_ts": entry_bar.ts,
        "entry_price": round(entry, 8),
        "stop": round(stop, 8),
        "take": round(take, 8),
        "exit_ts": exit_bar.ts,
        "exit_price": round(exit_bar.close, 8),
        "bars_held": end_idx - entry_idx + 1,
    }


def summarize(outcomes: list[dict[str, Any]], signal_events: int, min_resolved: int, min_expectancy_r: float) -> dict[str, Any]:
    resolved = [item for item in outcomes if isinstance(item.get("r"), (int, float))]
    values = [float(item["r"]) for item in resolved]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    expectancy = round(sum(values) / len(values), 6) if values else None
    winrate = round(len(wins) / len(values) * 100.0, 3) if values else None
    rr_values: list[float] = []
    for item in outcomes:
        stop_atr = safe_float(item.get("stop_atr"))
        take_atr = safe_float(item.get("take_atr"))
        if stop_atr and take_atr and stop_atr > 0:
            rr_values.append(take_atr / stop_atr)
    avg_rr = round(sum(rr_values) / len(rr_values), 6) if rr_values else None
    breakeven = round(100.0 / (1.0 + avg_rr), 3) if avg_rr and avg_rr > 0 else None

    if signal_events == 0:
        classification = "no_observer_signals_yet"
    elif not values:
        classification = "pending_only"
    elif len(values) < min_resolved:
        classification = "watchlist_positive_insufficient_forward_sample" if expectancy is not None and expectancy >= min_expectancy_r else "insufficient_forward_sample"
    elif expectancy is not None and expectancy >= min_expectancy_r and (breakeven is None or (winrate or 0.0) >= breakeven):
        classification = "candidate_for_forward_review"
    else:
        classification = "negative_or_mixed"

    return {
        "classification": classification,
        "observer_signal_events": signal_events,
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
        "min_resolved_required": min_resolved,
        "min_expectancy_r_required": min_expectancy_r,
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Crowd-Fade Positioning Shadow Scoreboard",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Classification: `{summary['classification']}`",
        f"- Can trade: `{report['can_trade']}`",
        "",
        "## Summary",
        "",
        f"- Observer signal events: `{summary['observer_signal_events']}`",
        f"- Raw unique signals: `{report.get('raw_unique_signal_events')}`",
        f"- Overlap suppressed: `{report.get('overlap_suppressed_events')}`",
        f"- Resolved: `{summary['resolved']}`",
        f"- Unresolved: `{summary['unresolved']}`",
        f"- Winrate: `{summary['winrate_pct']}`",
        f"- Expectancy R: `{summary['expectancy_r']}`",
        f"- Max drawdown R: `{summary['max_drawdown_r']}`",
        "",
        "## Latest Outcomes",
        "",
    ]
    if not report["outcomes"]:
        lines.append("- No observer signals have been logged yet.")
    for item in report["outcomes"][-10:]:
        lines.append(
            f"- `{item.get('signal_time')}` `{item.get('side_hint')}` status `{item.get('status')}` "
            f"R `{item.get('r')}` reason `{item.get('reason')}`"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Observer scoreboard only.",
            "- No paper entry intent and no orders.",
            "- Promotion requires enough resolved forward outcomes; historical diagnostic is not enough.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Score crowd-fade positioning shadow observer outcomes.")
    parser.add_argument("--journal-path", default=str(DEFAULT_JOURNAL))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    parser.add_argument("--same-bar-policy", choices=["conservative_stop", "ignore_ambiguous"], default="conservative_stop")
    parser.add_argument("--min-resolved", type=int, default=20)
    parser.add_argument("--min-expectancy-r", type=float, default=0.10)
    args = parser.parse_args()

    journal_path = resolve_path(args.journal_path)
    cache_dir = resolve_path(args.cache_dir)
    events = read_jsonl(journal_path)
    unique_events = dedupe_events(events)

    bars_by_interval: dict[str, list[Bar]] = {}
    index_by_interval: dict[str, dict[str, int]] = {}
    blocked_until_by_stream: dict[tuple[str, str, str], int] = {}
    outcomes: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for key, event in sorted(unique_events.items(), key=lambda item: str(item[1].get("signal_time") or "")):
        interval = str(event.get("interval") or "1h")
        if interval not in bars_by_interval:
            bars_by_interval[interval] = load_bars(cache_dir / "futures" / args.symbol.upper() / f"{interval}_klines.csv")
            index_by_interval[interval] = {bar.ts: index for index, bar in enumerate(bars_by_interval[interval])}
        bars = bars_by_interval[interval]
        index_by_ts = index_by_interval[interval]
        signal_time = str(event.get("signal_time") or "")
        signal_index = index_by_ts.get(signal_time)
        stream = (str(event.get("strategy_id") or ""), interval, str(event.get("side_hint") or ""))
        blocked_until = blocked_until_by_stream.get(stream, -1)
        if signal_index is not None and signal_index <= blocked_until:
            suppressed.append(
                {
                    "signal_key": key,
                    "signal_time": signal_time,
                    "strategy_id": event.get("strategy_id"),
                    "interval": interval,
                    "side_hint": event.get("side_hint"),
                    "reason": "overlapping_forward_position",
                    "blocked_until_index": blocked_until,
                    "can_trade": False,
                }
            )
            continue

        outcome = resolve_outcome(event, bars, args.same_bar_policy)
        row = {
            "signal_key": key,
            "strategy_id": event.get("strategy_id"),
            "interval": interval,
            "signal_time": event.get("signal_time"),
            "side_hint": event.get("side_hint"),
            "ratio_field": event.get("ratio_field"),
            "ratio": event.get("ratio"),
            "ratio_z": event.get("ratio_z"),
            "atr": event.get("atr"),
            "stop_atr": event.get("stop_atr"),
            "take_atr": event.get("take_atr"),
            "hold": event.get("hold"),
            **outcome,
        }
        outcomes.append(row)

        if signal_index is not None:
            exit_index = index_by_ts.get(str(outcome.get("exit_ts") or ""))
            if exit_index is None:
                try:
                    hold = int(float(str(event.get("hold") or 0)))
                except (TypeError, ValueError):
                    hold = 0
                exit_index = signal_index + 1 + max(0, hold)
            blocked_until_by_stream[stream] = max(blocked_until, exit_index)

    summary = summarize(outcomes, len(outcomes), args.min_resolved, args.min_expectancy_r)
    accepted_keys = {str(item.get("signal_key") or "") for item in outcomes}
    suppressed_keys = [str(item.get("signal_key") or "") for item in suppressed]
    latest_raw_key = next(reversed(unique_events), None) if unique_events else None
    report = {
        "generated_at": now_iso(),
        "engine": "CROWD_FADE_POSITIONING_SHADOW_SCOREBOARD",
        "engine_version": "1.1.0",
        "journal_path": rel_path(journal_path),
        "cache_dir": rel_path(cache_dir),
        "total_journal_events": len(events),
        "unique_signal_events": len(unique_events),
        "raw_unique_signal_events": len(unique_events),
        "independent_signal_events": len(outcomes),
        "overlap_suppressed_events": len(suppressed),
        "suppressed_signal_keys": suppressed_keys,
        "suppressed_events": suppressed,
        "latest_raw_signal_key": latest_raw_key,
        "latest_signal_eligible": latest_raw_key in accepted_keys if latest_raw_key else None,
        "summary": summary,
        "outcomes": outcomes,
        "decision": "crowd_fade_shadow_scoreboard_observer_only",
        "next_action": "accumulate resolved forward outcomes; do not promote without forward sample",
        "can_trade": False,
    }
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "classification": summary["classification"],
                "signals": summary["observer_signal_events"],
                "raw_signals": report["raw_unique_signal_events"],
                "overlap_suppressed": report["overlap_suppressed_events"],
                "resolved": summary["resolved"],
                "expectancy_r": summary["expectancy_r"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.derivatives_event_forward_observer import selected_config  # noqa: E402


@dataclass(frozen=True)
class Outcome:
    status: str
    net_r: float | None
    entry_time: str | None
    entry: float | None
    stop: float | None
    take: float | None
    exit_time: str | None
    exit: float | None
    bars_held: int | None
    exit_reason: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


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


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def load_bars(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                item = {
                    "time": str(row.get("time") or row.get("ts") or row.get("datetime") or "").strip(),
                    "open": float(row.get("open", "nan")),
                    "high": float(row.get("high", "nan")),
                    "low": float(row.get("low", "nan")),
                    "close": float(row.get("close", "nan")),
                }
            except (TypeError, ValueError):
                continue
            if item["time"] and all(math.isfinite(float(item[key])) for key in ("open", "high", "low", "close")):
                rows.append(item)
    return rows


def data_klines_path(miner_report: dict[str, Any], interval: str) -> Path | None:
    for row in miner_report.get("data", []):
        if not isinstance(row, dict) or row.get("interval") != interval:
            continue
        path = row.get("klines_path")
        if isinstance(path, str) and path:
            return resolve_path(path)
    return None


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


def resolve_outcome(event: dict[str, Any], bars: list[dict[str, Any]], cost_bps_per_side: float) -> Outcome:
    bar_ts = str(event.get("bar_ts") or "")
    side = str(event.get("side") or "").upper()
    atr = safe_float(event.get("atr"))
    stop_atr = safe_float(event.get("stop_atr"))
    take_atr = safe_float(event.get("take_atr"))
    try:
        max_hold_bars = int(event.get("max_hold_bars") or 0)
    except (TypeError, ValueError):
        max_hold_bars = 0
    if side not in {"LONG", "SHORT"}:
        return Outcome("unresolved", None, None, None, None, None, None, None, None, "unsupported_side")
    if atr is None or atr <= 0 or stop_atr is None or stop_atr <= 0 or take_atr is None or take_atr <= 0:
        return Outcome("unresolved", None, None, None, None, None, None, None, None, "invalid_risk_payload")
    index_by_time = {str(row["time"]): index for index, row in enumerate(bars)}
    signal_index = index_by_time.get(bar_ts)
    if signal_index is None:
        return Outcome("unresolved", None, None, None, None, None, None, None, None, "signal_bar_not_in_cache")
    entry_index = signal_index + 1
    if entry_index >= len(bars):
        return Outcome("unresolved", None, None, None, None, None, None, None, None, "entry_bar_not_closed_yet")
    entry = float(bars[entry_index]["open"])
    risk = stop_atr * atr
    reward = take_atr * atr
    if side == "LONG":
        stop = entry - risk
        take = entry + reward
    else:
        stop = entry + risk
        take = entry - reward
    end_index = min(len(bars) - 1, entry_index + max(1, max_hold_bars))
    exit_price = entry
    exit_index = entry_index
    exit_reason = "time"
    gross_r = 0.0
    for index in range(entry_index, end_index + 1):
        high = float(bars[index]["high"])
        low = float(bars[index]["low"])
        close = float(bars[index]["close"])
        if side == "LONG":
            stop_hit = low <= stop
            take_hit = high >= take
            if stop_hit and take_hit:
                exit_price, exit_index, exit_reason, gross_r = stop, index, "same_bar_stop_first", -1.0
                break
            if stop_hit:
                exit_price, exit_index, exit_reason, gross_r = stop, index, "stop", -1.0
                break
            if take_hit:
                exit_price, exit_index, exit_reason, gross_r = take, index, "take", take_atr / stop_atr
                break
            exit_price, exit_index = close, index
            gross_r = (exit_price - entry) / risk
        else:
            stop_hit = high >= stop
            take_hit = low <= take
            if stop_hit and take_hit:
                exit_price, exit_index, exit_reason, gross_r = stop, index, "same_bar_stop_first", -1.0
                break
            if stop_hit:
                exit_price, exit_index, exit_reason, gross_r = stop, index, "stop", -1.0
                break
            if take_hit:
                exit_price, exit_index, exit_reason, gross_r = take, index, "take", take_atr / stop_atr
                break
            exit_price, exit_index = close, index
            gross_r = (entry - exit_price) / risk
    if len(bars) - entry_index < max(1, max_hold_bars) and exit_reason == "time":
        return Outcome("unresolved", None, str(bars[entry_index]["time"]), round(entry, 8), round(stop, 8), round(take, 8), None, None, None, "max_hold_not_reached_yet")
    fee_r = ((entry + exit_price) * cost_bps_per_side / 10_000.0) / risk
    status = "win" if gross_r - fee_r > 0 else "loss"
    return Outcome(
        status,
        round(gross_r - fee_r, 6),
        str(bars[entry_index]["time"]),
        round(entry, 8),
        round(stop, 8),
        round(take, 8),
        str(bars[exit_index]["time"]),
        round(exit_price, 8),
        exit_index - entry_index + 1,
        exit_reason,
    )


def summarize(outcomes: list[dict[str, Any]], total_events: int, args: argparse.Namespace) -> dict[str, Any]:
    resolved = [row for row in outcomes if isinstance(row.get("net_r"), (int, float))]
    values = [float(row["net_r"]) for row in resolved]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    expectancy = round(sum(values) / len(values), 6) if values else None
    winrate = round(len(wins) / len(values) * 100.0, 3) if values else None
    if total_events == 0:
        classification = "no_observer_signals_yet"
    elif not values:
        classification = "pending_only"
    elif len(values) < args.min_resolved:
        classification = "watchlist_positive_insufficient_sample" if expectancy is not None and expectancy >= args.min_expectancy_r else "insufficient_forward_sample"
    elif expectancy is not None and expectancy >= args.min_expectancy_r and max_drawdown(values) >= -abs(args.max_drawdown_r):
        classification = "candidate_for_promotion_review"
    else:
        classification = "negative_or_mixed_forward_evidence"
    return {
        "classification": classification,
        "observer_signal_events": total_events,
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
        "min_resolved_required": args.min_resolved,
        "min_expectancy_r_required": args.min_expectancy_r,
        "max_drawdown_r_required": -abs(args.max_drawdown_r),
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    return "\n".join(
        [
            "# Derivatives Event Forward Scoreboard",
            "",
            f"Generated: `{report.get('generated_at')}`",
            "",
            "## Boundary",
            "",
            "- Scores observer-only derivatives-event signals.",
            "- Does not create paper-entry intents and does not send orders.",
            "- Positive forward evidence is review input, not trading permission.",
            "",
            "## Summary",
            "",
            f"- Classification: `{summary.get('classification')}`.",
            f"- Signals / resolved / unresolved: `{summary.get('observer_signal_events')}` / `{summary.get('resolved')}` / `{summary.get('unresolved')}`.",
            f"- Winrate: `{summary.get('winrate_pct')}`%.",
            f"- Expectancy: `{summary.get('expectancy_r')}`R.",
            f"- Net R: `{summary.get('net_r_total')}`R.",
            f"- Max DD: `{summary.get('max_drawdown_r')}`R.",
            "",
            "## Decision",
            "",
            f"- `{report.get('decision')}`.",
            f"- Next: `{report.get('next_action')}`.",
            "",
        ]
    )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    miner_path = resolve_path(args.miner_report)
    journal_path = resolve_path(args.journal_path)
    miner = read_json(miner_path)
    config = selected_config(miner)
    if config is None:
        summary = summarize([], 0, args)
        return {
            "schema_version": 1,
            "generated_at": now_iso(),
            "decision": "blocked_no_selected_derivatives_candidate",
            "summary": summary,
            "outcomes": [],
            "can_trade": False,
        }
    klines_path = data_klines_path(miner, config.interval)
    bars = load_bars(klines_path) if klines_path is not None else []
    events = [row for row in read_jsonl(journal_path) if row.get("strategy_id") == config.strategy_id]
    outcomes: list[dict[str, Any]] = []
    for event in events:
        outcome = resolve_outcome(event, bars, args.cost_bps_per_side)
        payload = dict(event)
        payload.update(asdict(outcome))
        outcomes.append(payload)
    summary = summarize(outcomes, len(events), args)
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "observer_id": "derivatives_event_forward_observer",
        "miner_report": rel_path(miner_path),
        "journal_path": rel_path(journal_path),
        "klines_path": rel_path(klines_path) if klines_path else None,
        "selected_config": config.__dict__,
        "cost_bps_per_side": args.cost_bps_per_side,
        "summary": summary,
        "outcomes": outcomes[-args.max_outcomes :],
        "decision": summary["classification"],
        "next_action": "keep collecting forward outcomes until promotion gate has enough resolved evidence",
        "runtime_boundary": {
            "observer_only": True,
            "paper_allowed": False,
            "live_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Score observer-only derivatives-event forward outcomes")
    parser.add_argument("--miner-report", default="docs/DERIVATIVES_EVENT_EDGE_MINER_REGIME_2026-06-26.json")
    parser.add_argument("--journal-path", default="logs/forward_paper_feed/derivatives_event_forward_observer.jsonl")
    parser.add_argument("--cost-bps-per-side", type=float, default=7.0)
    parser.add_argument("--min-resolved", type=int, default=30)
    parser.add_argument("--min-expectancy-r", type=float, default=0.03)
    parser.add_argument("--max-drawdown-r", type=float, default=12.0)
    parser.add_argument("--max-outcomes", type=int, default=200)
    parser.add_argument("--out-prefix", default="docs/DERIVATIVES_EVENT_FORWARD_SCOREBOARD_2026-06-26")
    args = parser.parse_args()
    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "summary": report["summary"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

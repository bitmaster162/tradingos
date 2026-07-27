#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.range_refined_observer_scoreboard import (  # noqa: E402
    load_bars,
    max_drawdown,
    max_losing_streak,
    parse_rr,
    resolve_outcome,
)


DEFAULT_JOURNAL = ROOT / "logs" / "forward_paper_feed" / "edge_compression_guard_shadow_observer.jsonl"
DEFAULT_CACHE_CSV = ROOT / "_dl" / "forward_paper_feed" / "cache" / "futures" / "BTCUSDT" / "4h_klines.csv"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "EDGE_COMPRESSION_GUARD_SHADOW_SCOREBOARD_2026-06-19"


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


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def dedupe_events(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("event_type") != "edge_compression_guard_shadow_state":
            continue
        strategy_id = str(event.get("strategy_id") or "")
        guard_id = str(event.get("guard_id") or "")
        bar_ts = str(event.get("bar_ts") or "")
        if not strategy_id or not guard_id or not bar_ts:
            continue
        out[f"{strategy_id}|{guard_id}|{bar_ts}"] = event
    return out


def signal_event_from_shadow(event: dict[str, Any]) -> dict[str, Any] | None:
    latest_signal = event.get("latest_signal")
    if not isinstance(latest_signal, dict):
        return None
    try:
        atr = float(latest_signal.get("atr"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(atr) or atr <= 0:
        return None
    return {
        "bar_ts": latest_signal.get("bar_ts") or event.get("bar_ts"),
        "side": str(event.get("side") or "").lower(),
        "atr": atr,
        "rr": event.get("rr"),
        "max_hold_bars": event.get("max_hold_bars"),
    }


def summarize(outcomes: list[dict[str, Any]], *, signal_events: int, min_resolved: int) -> dict[str, Any]:
    resolved = [item for item in outcomes if isinstance(item.get("r"), (int, float))]
    values = [float(item["r"]) for item in resolved]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    expectancy = round(sum(values) / len(values), 6) if values else None
    winrate = round(100.0 * len(wins) / len(values), 3) if values else None
    rr_values: list[float] = []
    for item in outcomes:
        try:
            stop_atr, take_atr = parse_rr(item.get("rr"))
            rr_values.append(take_atr / stop_atr)
        except (TypeError, ValueError):
            pass
    avg_rr = round(sum(rr_values) / len(rr_values), 6) if rr_values else None
    breakeven = round(100.0 / (1.0 + avg_rr), 3) if avg_rr and avg_rr > 0 else None
    if signal_events == 0:
        classification = "no_shadow_events_yet"
    elif not values:
        classification = "shadow_pending_only"
    elif len(values) < min_resolved:
        classification = "shadow_insufficient_forward_sample"
    elif expectancy is not None and expectancy > 0:
        classification = "shadow_positive_sample"
    else:
        classification = "shadow_negative_sample"
    return {
        "classification": classification,
        "signal_events": signal_events,
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
        "can_trade": False,
    }


def gate_summary(keep: dict[str, Any], veto: dict[str, Any]) -> dict[str, Any]:
    keep_resolved = int(keep.get("resolved") or 0)
    veto_resolved = int(veto.get("resolved") or 0)
    keep_exp = keep.get("expectancy_r")
    veto_exp = veto.get("expectancy_r")
    if keep_resolved == 0 and veto_resolved == 0:
        decision = "waiting_for_first_guard_shadow_outcome"
    elif veto_resolved < 5:
        decision = "guard_shadow_needs_more_veto_outcomes"
    elif veto_exp is not None and veto_exp < 0 and (keep_exp is None or keep_exp >= 0):
        decision = "guard_shadow_promising_veto_bucket_negative"
    else:
        decision = "guard_shadow_not_proven"
    return {
        "decision": decision,
        "keep_expectancy_r": keep_exp,
        "veto_expectancy_r": veto_exp,
        "keep_resolved": keep_resolved,
        "veto_resolved": veto_resolved,
        "can_trade": False,
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    journal_path = resolve_path(args.journal_path)
    cache_csv = resolve_path(args.cache_csv)
    events = read_jsonl(journal_path)
    unique_events = dedupe_events(events)
    bars = load_bars(cache_csv)
    outcomes: list[dict[str, Any]] = []
    bucket_outcomes: dict[str, list[dict[str, Any]]] = {"keep": [], "veto": []}
    status_counts: dict[str, int] = {}
    action_counts: dict[str, int] = {"keep": 0, "veto": 0}
    for key, event in sorted(unique_events.items(), key=lambda item: str(item[1].get("bar_ts") or "")):
        status = str(event.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        action = str(event.get("guard_action") or "")
        if action not in {"keep", "veto"}:
            continue
        action_counts[action] += 1
        signal_event = signal_event_from_shadow(event)
        if signal_event is None:
            outcome_row = {
                "signal_key": key,
                "strategy_id": event.get("strategy_id"),
                "guard_id": event.get("guard_id"),
                "guard_action": action,
                "rr": event.get("rr"),
                "signal_bar_ts": event.get("bar_ts"),
                "status": "unresolved",
                "r": None,
                "reason": "invalid_shadow_signal_payload",
            }
        else:
            outcome = resolve_outcome(signal_event, bars, args.same_bar_policy)
            outcome_row = {
                "signal_key": key,
                "strategy_id": event.get("strategy_id"),
                "guard_id": event.get("guard_id"),
                "guard_action": action,
                "side": signal_event.get("side"),
                "rr": signal_event.get("rr"),
                "max_hold_bars": signal_event.get("max_hold_bars"),
                "signal_bar_ts": signal_event.get("bar_ts"),
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
            }
        outcomes.append(outcome_row)
        bucket_outcomes[action].append(outcome_row)
    keep_summary = summarize(bucket_outcomes["keep"], signal_events=action_counts["keep"], min_resolved=args.min_resolved)
    veto_summary = summarize(bucket_outcomes["veto"], signal_events=action_counts["veto"], min_resolved=args.min_resolved)
    overall = summarize(outcomes, signal_events=action_counts["keep"] + action_counts["veto"], min_resolved=args.min_resolved)
    gate = gate_summary(keep_summary, veto_summary)
    return {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "edge_compression_guard_shadow_scoreboard_public_cache_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
            "changes_active_strategy": False,
        },
        "journal_path": rel_path(journal_path),
        "cache_csv": rel_path(cache_csv),
        "total_journal_events": len(events),
        "unique_strategy_guard_bar_events": len(unique_events),
        "status_counts": status_counts,
        "action_counts": action_counts,
        "summary": overall,
        "keep_bucket": keep_summary,
        "veto_bucket": veto_summary,
        "guard_shadow_gate": gate,
        "outcomes": outcomes,
        "decision": "compression_guard_shadow_scoreboard_no_trade_permission",
        "next_action": "accumulate keep/veto forward outcomes; do not promote guard without negative veto bucket evidence",
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    gate = report.get("guard_shadow_gate") if isinstance(report.get("guard_shadow_gate"), dict) else {}
    keep = report.get("keep_bucket") if isinstance(report.get("keep_bucket"), dict) else {}
    veto = report.get("veto_bucket") if isinstance(report.get("veto_bucket"), dict) else {}
    lines = [
        "# Edge Compression Guard Shadow Scoreboard",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Scores observer-only compression guard keep/veto outcomes.",
        "- Does not change the active edge candidate.",
        "- Does not create paper-entry intents or send orders.",
        "",
        "## Decision",
        "",
        f"- Gate decision: `{gate.get('decision')}`.",
        f"- Keep resolved/exp: `{gate.get('keep_resolved')}` / `{gate.get('keep_expectancy_r')}`.",
        f"- Veto resolved/exp: `{gate.get('veto_resolved')}` / `{gate.get('veto_expectancy_r')}`.",
        f"- Can trade: `{report.get('can_trade')}`.",
        "",
        "## Buckets",
        "",
        "| Bucket | Events | Resolved | Winrate | Exp R | Max DD | Classification |",
        "|---|---:|---:|---:|---:|---:|---|",
        f"| keep | `{keep.get('signal_events')}` | `{keep.get('resolved')}` | `{keep.get('winrate_pct')}` | `{keep.get('expectancy_r')}` | `{keep.get('max_drawdown_r')}` | `{keep.get('classification')}` |",
        f"| veto | `{veto.get('signal_events')}` | `{veto.get('resolved')}` | `{veto.get('winrate_pct')}` | `{veto.get('expectancy_r')}` | `{veto.get('max_drawdown_r')}` | `{veto.get('classification')}` |",
        "",
        "## Status Counts",
        "",
        f"- `{report.get('status_counts')}`",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Score observer-only compression guard shadow outcomes.")
    parser.add_argument("--journal-path", default=str(DEFAULT_JOURNAL))
    parser.add_argument("--cache-csv", default=str(DEFAULT_CACHE_CSV))
    parser.add_argument("--same-bar-policy", choices=["conservative_stop", "ignore_ambiguous"], default="conservative_stop")
    parser.add_argument("--min-resolved", type=int, default=30)
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    args = parser.parse_args()
    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    write_text(out_prefix.with_suffix(".md"), render_markdown(report))
    print(
        json.dumps(
            {
                "status": "ok",
                "gate_decision": report["guard_shadow_gate"].get("decision"),
                "keep_events": report["keep_bucket"].get("signal_events"),
                "veto_events": report["veto_bucket"].get("signal_events"),
                "keep_resolved": report["keep_bucket"].get("resolved"),
                "veto_resolved": report["veto_bucket"].get("resolved"),
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

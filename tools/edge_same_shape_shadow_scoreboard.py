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
        if event.get("event_type") != "edge_same_shape_shadow_variant_state":
            continue
        strategy_id = str(event.get("strategy_id") or "")
        bar_ts = str(event.get("bar_ts") or "")
        if not strategy_id or not bar_ts:
            continue
        out[f"{strategy_id}|{bar_ts}"] = event
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


def summarize_outcomes(
    *,
    outcomes: list[dict[str, Any]],
    signal_events: int,
    filtered_events: int,
    no_base_events: int,
    no_data_events: int,
    min_resolved: int,
    min_expectancy_r: float,
) -> dict[str, Any]:
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
        classification = "no_shadow_variant_signals_yet"
    elif not values:
        classification = "shadow_pending_only"
    elif len(values) < min_resolved:
        classification = (
            "shadow_watchlist_positive_insufficient_sample"
            if expectancy is not None and expectancy >= min_expectancy_r
            else "shadow_insufficient_forward_sample"
        )
    elif expectancy is not None and expectancy >= min_expectancy_r and (breakeven is None or (winrate or 0.0) >= breakeven):
        classification = "shadow_candidate_for_gate_review"
    else:
        classification = "shadow_negative_or_mixed"
    return {
        "classification": classification,
        "shadow_signal_events": signal_events,
        "shadow_filtered_out_events": filtered_events,
        "shadow_no_base_events": no_base_events,
        "shadow_no_data_events": no_data_events,
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


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    journal_path = resolve_path(args.journal_path)
    cache_csv = resolve_path(args.cache_csv)
    events = read_jsonl(journal_path)
    unique_events = dedupe_events(events)
    bars = load_bars(cache_csv)

    outcomes: list[dict[str, Any]] = []
    variants: dict[str, dict[str, Any]] = {}
    for key, event in sorted(unique_events.items(), key=lambda item: str(item[1].get("bar_ts") or "")):
        strategy_id = str(event.get("strategy_id") or "unknown")
        bucket = variants.setdefault(
            strategy_id,
            {
                "strategy_id": strategy_id,
                "base_strategy_id": event.get("base_strategy_id"),
                "historical_evidence": event.get("historical_evidence") if isinstance(event.get("historical_evidence"), dict) else {},
                "shadow_signal_events": 0,
                "shadow_filtered_out_events": 0,
                "shadow_no_base_events": 0,
                "shadow_no_data_events": 0,
                "outcomes": [],
            },
        )
        status = str(event.get("status") or "unknown")
        if status == "shadow_variant_signal_observed":
            bucket["shadow_signal_events"] += 1
            signal_event = signal_event_from_shadow(event)
            if signal_event is None:
                outcome_row = {
                    "signal_key": key,
                    "strategy_id": strategy_id,
                    "side": event.get("side"),
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
                    "strategy_id": strategy_id,
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
                    "historical_evidence": event.get("historical_evidence") if isinstance(event.get("historical_evidence"), dict) else {},
                }
            bucket["outcomes"].append(outcome_row)
            outcomes.append(outcome_row)
        elif status == "shadow_variant_filtered_out":
            bucket["shadow_filtered_out_events"] += 1
        elif status == "shadow_no_base_signal":
            bucket["shadow_no_base_events"] += 1
        elif status == "shadow_no_data":
            bucket["shadow_no_data_events"] += 1

    variant_rows: list[dict[str, Any]] = []
    for strategy_id, bucket in sorted(variants.items()):
        summary = summarize_outcomes(
            outcomes=bucket["outcomes"],
            signal_events=int(bucket["shadow_signal_events"]),
            filtered_events=int(bucket["shadow_filtered_out_events"]),
            no_base_events=int(bucket["shadow_no_base_events"]),
            no_data_events=int(bucket["shadow_no_data_events"]),
            min_resolved=args.min_resolved,
            min_expectancy_r=args.min_expectancy_r,
        )
        variant_rows.append(
            {
                "strategy_id": strategy_id,
                "base_strategy_id": bucket.get("base_strategy_id"),
                "historical_evidence": bucket.get("historical_evidence"),
                "summary": summary,
                "outcomes": bucket["outcomes"],
            }
        )

    overall = summarize_outcomes(
        outcomes=outcomes,
        signal_events=sum(int(row["summary"]["shadow_signal_events"]) for row in variant_rows),
        filtered_events=sum(int(row["summary"]["shadow_filtered_out_events"]) for row in variant_rows),
        no_base_events=sum(int(row["summary"]["shadow_no_base_events"]) for row in variant_rows),
        no_data_events=sum(int(row["summary"]["shadow_no_data_events"]) for row in variant_rows),
        min_resolved=args.min_resolved,
        min_expectancy_r=args.min_expectancy_r,
    )
    return {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "edge_same_shape_shadow_scoreboard_public_cache_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
            "changes_active_strategy": False,
        },
        "journal_path": rel_path(journal_path),
        "cache_csv": rel_path(cache_csv),
        "total_journal_events": len(events),
        "unique_strategy_bar_events": len(unique_events),
        "summary": overall,
        "variants": variant_rows,
        "outcomes": outcomes,
        "decision": "same_shape_shadow_scoreboard_no_trade_permission",
        "next_action": "accumulate resolved forward outcomes before considering a separate promotion review",
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Edge Same-Shape Shadow Scoreboard",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Scores observer-only same-shape shadow variant events.",
        "- Uses each variant's own side, RR and max-hold fields from the observer journal.",
        "- Does not change the active candidate.",
        "- Does not create paper-entry intents or send orders.",
        "",
        "## Overall Summary",
        "",
        f"- Classification: `{summary.get('classification')}`.",
        f"- Shadow signals: `{summary.get('shadow_signal_events')}`.",
        f"- Filtered / no-base / no-data: `{summary.get('shadow_filtered_out_events')}` / `{summary.get('shadow_no_base_events')}` / `{summary.get('shadow_no_data_events')}`.",
        f"- Resolved / unresolved: `{summary.get('resolved')}` / `{summary.get('unresolved')}`.",
        f"- Winrate: `{summary.get('winrate_pct')}`%.",
        f"- Expectancy: `{summary.get('expectancy_r')}` R.",
        f"- Breakeven WR: `{summary.get('breakeven_winrate_pct')}`%.",
        "",
        "## Variants",
        "",
        "| Variant | Classification | Signals | Resolved | Winrate | Exp R | Hist Score | Cost +10 Exp |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report.get("variants", []):
        if not isinstance(row, dict):
            continue
        row_summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        evidence = row.get("historical_evidence") if isinstance(row.get("historical_evidence"), dict) else {}
        lines.append(
            f"| `{row.get('strategy_id')}` | `{row_summary.get('classification')}` | "
            f"`{row_summary.get('shadow_signal_events')}` | `{row_summary.get('resolved')}` | "
            f"`{row_summary.get('winrate_pct')}` | `{row_summary.get('expectancy_r')}` | "
            f"`{evidence.get('hard_score')}` | `{evidence.get('cost10_expectancy_r')}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- No shadow signals yet means there is no forward evidence for replacing the active candidate.",
            "- A prettier historical variant must still beat the active candidate on resolved forward events.",
            "- `can_trade` remains false by design.",
            "",
            "## Files",
            "",
            f"- Journal: `{report.get('journal_path')}`.",
            f"- Cache CSV: `{report.get('cache_csv')}`.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Score observer-only outcomes for edge same-shape shadow variants.")
    parser.add_argument("--journal-path", default="logs/forward_paper_feed/edge_same_shape_shadow_observer.jsonl")
    parser.add_argument("--cache-csv", default="_dl/forward_paper_feed/cache/futures/BTCUSDT/4h_klines.csv")
    parser.add_argument("--same-bar-policy", choices=["conservative_stop", "ignore_ambiguous"], default="conservative_stop")
    parser.add_argument("--min-resolved", type=int, default=30)
    parser.add_argument("--min-expectancy-r", type=float, default=0.05)
    parser.add_argument("--out-prefix", default="docs/EDGE_SAME_SHAPE_SHADOW_SCOREBOARD_2026-06-19")
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    write_text(out_prefix.with_suffix(".md"), render_markdown(report))
    print(
        json.dumps(
            {
                "status": "ok",
                "classification": report["summary"].get("classification"),
                "shadow_signal_events": report["summary"].get("shadow_signal_events"),
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

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidation_impulse_reversal_nested_holdout import parse_ts  # noqa: E402
from tools.range_refined_observer_scoreboard import load_bars, read_jsonl, resolve_outcome  # noqa: E402


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def nearest_context(signal: dict[str, Any], contexts: list[dict[str, Any]]) -> dict[str, Any] | None:
    signal_bar = parse_ts(str(signal.get("bar_ts")))
    signal_end = signal_bar + timedelta(hours=4)
    eligible = []
    for position, context in enumerate(contexts):
        try:
            timestamp = parse_ts(str(context.get("bar_ts")))
        except ValueError:
            continue
        if signal_bar <= timestamp < signal_end:
            eligible.append((timestamp, position, context))
    return max(eligible, key=lambda item: (item[0], item[1]))[2] if eligible else None


def context_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(item["r"]) for item in rows if isinstance(item.get("r"), (int, float))]
    return {
        "signals": len(rows),
        "resolved": len(values),
        "winrate_pct": round(sum(value > 0 for value in values) / len(values) * 100.0, 3) if values else None,
        "expectancy_r": round(sum(values) / len(values), 6) if values else None,
        "net_r_total": round(sum(values), 6) if values else None,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Edge Liquidation Context Shadow Scoreboard",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "- Labels frozen Edge observer outcomes by liquidation/OI context.",
        "- Does not filter, veto, promote, notify, create intents, or send orders.",
        f"- Classification: `{report.get('classification')}`.",
        f"- Edge signals / context-labelled: `{report.get('edge_signal_events')}` / `{report.get('context_labelled_signals')}`.",
        "",
        "| Context | Signals | Resolved | Winrate | Expectancy R | Net R |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for context, summary in report.get("by_context", {}).items():
        lines.append(f"| `{context}` | `{summary['signals']}` | `{summary['resolved']}` | `{summary['winrate_pct']}` | `{summary['expectancy_r']}` | `{summary['net_r_total']}` |")
    lines.extend(["", "## Frozen Train-only Score Bins", "", "| Bin | Signals | Resolved | Winrate | Expectancy R | Net R |", "|---|---:|---:|---:|---:|---:|"])
    for name, summary in report.get("by_score_bin", {}).items():
        lines.append(f"| `{name}` | `{summary['signals']}` | `{summary['resolved']}` | `{summary['winrate_pct']}` | `{summary['expectancy_r']}` | `{summary['net_r_total']}` |")
    lines.extend(["", "- `recommended_filter_change=false`.", "- `can_trade=false`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Score frozen Edge outcomes by liquidation context without changing signals")
    parser.add_argument("--edge-journal", default="logs/forward_paper_feed/edge_forward_range_observer.jsonl")
    parser.add_argument("--context-journal", default="logs/forward_paper_feed/edge_liquidation_context_shadow.jsonl")
    parser.add_argument("--cache-csv", default="_dl/forward_paper_feed/cache/futures/BTCUSDT/4h_klines.csv")
    parser.add_argument("--out-prefix", default="docs/EDGE_LIQUIDATION_CONTEXT_SHADOW_SCOREBOARD_2026-06-23")
    args = parser.parse_args()

    edge_events = read_jsonl(resolve_path(args.edge_journal))
    contexts = read_jsonl(resolve_path(args.context_journal))
    bars = load_bars(resolve_path(args.cache_csv))
    signals_by_key: dict[str, dict[str, Any]] = {}
    for event in edge_events:
        if event.get("event_type") != "range_refined_signal_observed":
            continue
        key = str(event.get("signal_key") or f"{event.get('strategy_id')}|{event.get('bar_ts')}")
        signals_by_key[key] = event
    labelled: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    score_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for key, signal in sorted(signals_by_key.items(), key=lambda item: str(item[1].get("bar_ts"))):
        context = nearest_context(signal, contexts)
        outcome = resolve_outcome(signal, bars, "conservative_stop")
        row = {
            "signal_key": key,
            "signal_bar_ts": signal.get("bar_ts"),
            "context": context.get("context") if context else "unknown",
            "context_bar_ts": context.get("bar_ts") if context else None,
            "continuous_score": context.get("continuous_score") if context else None,
            "score_bin": context.get("score_bin") if context else "unknown",
            "status": outcome.status,
            "r": outcome.r,
        }
        labelled.append(row)
        groups[row["context"]].append(row)
        score_groups[row["score_bin"]].append(row)
    if not labelled:
        classification = "no_edge_signals_for_context_scoring"
    elif not any(isinstance(item.get("r"), (int, float)) for item in labelled):
        classification = "edge_context_labels_pending_outcomes"
    else:
        classification = "edge_context_shadow_evidence_collecting"
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {"classification": "edge_context_shadow_scoreboard_only", "changes_edge_signal": False, "applies_filter": False, "recommended_filter_change": False, "sends_orders": False, "can_trade": False},
        "paths": {"edge_journal": rel_path(resolve_path(args.edge_journal)), "context_journal": rel_path(resolve_path(args.context_journal)), "cache_csv": rel_path(resolve_path(args.cache_csv))},
        "classification": classification,
        "edge_signal_events": len(signals_by_key),
        "context_events": len(contexts),
        "context_labelled_signals": sum(item["context"] != "unknown" for item in labelled),
        "by_context": {name: context_summary(rows) for name, rows in sorted(groups.items())},
        "by_score_bin": {name: context_summary(rows) for name, rows in sorted(score_groups.items())},
        "labelled_outcomes": labelled,
        "recommended_filter_change": False,
        "decision": "edge_context_shadow_scoreboard_no_trade_permission",
        "can_trade": False,
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"classification": classification, "edge_signals": len(signals_by_key), "labelled": report["context_labelled_signals"], "recommended_filter_change": False, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

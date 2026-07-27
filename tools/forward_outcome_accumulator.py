#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"_read_error": "invalid_json", "_path": str(path)}
    return payload if isinstance(payload, dict) else {"_read_error": "not_object", "_path": str(path)}


def read_jsonl_count(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            count += 1
    return count


def number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed


def integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def progress(value: int, required: int) -> dict[str, Any]:
    pct = 100.0 if required <= 0 else min(100.0, value / required * 100.0)
    return {
        "value": value,
        "required": required,
        "deficit": max(0, required - value),
        "progress_pct": round(pct, 3),
        "passed": value >= required,
    }


def classify(report: dict[str, Any]) -> tuple[str, str]:
    gates = report["gates"]
    metrics = report["metrics"]
    if metrics["forward_entry_intents"] <= 0:
        return "collecting_context_no_forward_entries", "wait_for_first_forward_paper_entry_intent"
    if not gates["forward_resolved_entries"]["passed"]:
        return "collecting_forward_outcomes", "wait_for_more_resolved_forward_paper_trades"
    if not gates["oi_guard_resolved_contexts"]["passed"]:
        return "collecting_oi_guard_outcomes", "wait_for_more_resolved_oi_guard_forward_contexts"
    if not gates["positive_forward_expectancy"]["passed"]:
        return "blocked_forward_expectancy_not_positive_enough", "do_not_promote_guard_or_execution"
    if not gates["positive_oi_guard_expectancy"]["passed"]:
        return "blocked_oi_guard_lift_not_positive_enough", "keep_oi_guard_shadow_only"
    return "ready_for_guard_evaluation_review", "manual_review_before_any_paper_or_execution_design"


def render_gate(name: str, gate: dict[str, Any]) -> str:
    return (
        f"| {name} | `{gate.get('passed')}` | `{gate.get('value')}` | "
        f"`{gate.get('required')}` | `{gate.get('deficit')}` | `{gate.get('progress_pct')}`% |"
    )


def display_path(value: Any) -> str:
    if value is None:
        return "None"
    path = Path(str(value))
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report.get("metrics", {})
    gates = report.get("gates", {})
    return "\n".join(
        [
            "# Forward Outcome Accumulator",
            "",
            f"Generated: `{report.get('generated_at')}`",
            "",
            "## Boundary",
            "",
            "- Evidence accumulator only.",
            "- Reads local forward scoreboards/journals.",
            "- No private credentials, no exchange account, no orders.",
            "- Does not grant paper or live trading permission.",
            "",
            "## Decision",
            "",
            f"- Classification: `{report.get('classification')}`.",
            f"- Next action: `{report.get('next_action')}`.",
            f"- Can trade: `{report.get('can_trade')}`.",
            "",
            "## Metrics",
            "",
            f"- Forward journal events: `{metrics.get('forward_journal_events')}`.",
            f"- Forward no-signal events: `{metrics.get('forward_no_signal_events')}`.",
            f"- Unique no-signal bars checked: `{metrics.get('unique_no_signal_bars_checked')}`.",
            f"- Context observations: `{metrics.get('context_observations')}`.",
            f"- Unique context bars: `{metrics.get('unique_context_bars')}`.",
            f"- Forward entry intents: `{metrics.get('forward_entry_intents')}`.",
            f"- Forward resolved entries: `{metrics.get('forward_resolved_entries')}`.",
            f"- OI guard entry contexts: `{metrics.get('oi_guard_entry_contexts')}`.",
            f"- OI guard resolved contexts: `{metrics.get('oi_guard_resolved_contexts')}`.",
            f"- Forward expectancy: `{metrics.get('forward_expectancy_r')}` R.",
            f"- OI guard expectancy: `{metrics.get('oi_guard_expectancy_r')}` R.",
            "",
            "## Evidence Gates",
            "",
            "| gate | pass | value | required | deficit | progress |",
            "|---|---:|---:|---:|---:|---:|",
            render_gate("unique_context_bars", gates.get("unique_context_bars", {})),
            render_gate("forward_entry_intents", gates.get("forward_entry_intents", {})),
            render_gate("forward_resolved_entries", gates.get("forward_resolved_entries", {})),
            render_gate("oi_guard_entry_contexts", gates.get("oi_guard_entry_contexts", {})),
            render_gate("oi_guard_resolved_contexts", gates.get("oi_guard_resolved_contexts", {})),
            render_gate("positive_forward_expectancy", gates.get("positive_forward_expectancy", {})),
            render_gate("positive_oi_guard_expectancy", gates.get("positive_oi_guard_expectancy", {})),
            "",
            "## Interpretation",
            "",
            "- `collecting_context_no_forward_entries`: observers are running, but no trade setup has appeared yet.",
            "- `collecting_forward_outcomes`: entries exist, but too few have resolved.",
            "- `collecting_oi_guard_outcomes`: strategy outcomes exist, but OI guard-specific outcomes are still insufficient.",
            "- `ready_for_guard_evaluation_review`: enough evidence exists for manual review, not for live trading.",
            "",
            "## Inputs",
            "",
            f"- Forward scoreboard: `{display_path(report.get('inputs', {}).get('forward_scoreboard'))}`.",
            f"- OI/funding context scoreboard: `{display_path(report.get('inputs', {}).get('oi_funding_scoreboard'))}`.",
            f"- Promotion gate: `{display_path(report.get('inputs', {}).get('promotion_gate'))}`.",
            "",
        ]
    )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    forward_scoreboard_path = resolve_path(args.forward_scoreboard)
    oi_funding_scoreboard_path = resolve_path(args.oi_funding_scoreboard)
    promotion_gate_path = resolve_path(args.promotion_gate)
    forward_journal_path = resolve_path(args.forward_journal)
    context_journal_path = resolve_path(args.context_journal)

    forward_scoreboard = read_json(forward_scoreboard_path)
    oi_funding_scoreboard = read_json(oi_funding_scoreboard_path)
    promotion_gate = read_json(promotion_gate_path)

    forward_summary = forward_scoreboard.get("summary") if isinstance(forward_scoreboard.get("summary"), dict) else {}
    context_summary = oi_funding_scoreboard.get("summary") if isinstance(oi_funding_scoreboard.get("summary"), dict) else {}

    forward_entry_intents = integer(forward_summary.get("entry_intents"))
    forward_resolved = integer(forward_summary.get("resolved"))
    context_observations = integer(context_summary.get("context_observations"))
    unique_context_bars = integer(context_summary.get("unique_context_bars"))
    oi_guard_entry_contexts = integer(context_summary.get("oi_guard_entry_contexts"))
    oi_guard_resolved_contexts = integer(context_summary.get("oi_guard_resolved_contexts"))
    forward_expectancy = number(forward_summary.get("expectancy_r"), default=0.0)
    oi_guard_expectancy = number(context_summary.get("oi_guard_expectancy_r"), default=0.0)

    metrics = {
        "forward_journal_events": read_jsonl_count(forward_journal_path),
        "context_journal_events": read_jsonl_count(context_journal_path),
        "forward_no_signal_events": integer(forward_scoreboard.get("forward_no_signal_events")),
        "unique_no_signal_bars_checked": integer(forward_scoreboard.get("unique_no_signal_bars_checked")),
        "context_observations": context_observations,
        "unique_context_bars": unique_context_bars,
        "forward_entry_intents": forward_entry_intents,
        "forward_resolved_entries": forward_resolved,
        "forward_unresolved_entries": integer(forward_summary.get("unresolved")),
        "oi_guard_entry_contexts": oi_guard_entry_contexts,
        "oi_guard_resolved_contexts": oi_guard_resolved_contexts,
        "forward_expectancy_r": forward_summary.get("expectancy_r"),
        "oi_guard_expectancy_r": context_summary.get("oi_guard_expectancy_r"),
        "promotion_decision": promotion_gate.get("decision"),
        "promotion_active_filter_allowed": (
            promotion_gate.get("promotion", {}).get("active_filter_allowed")
            if isinstance(promotion_gate.get("promotion"), dict)
            else None
        ),
        "promotion_live_execution_allowed": (
            promotion_gate.get("promotion", {}).get("live_execution_allowed")
            if isinstance(promotion_gate.get("promotion"), dict)
            else None
        ),
    }

    gates = {
        "unique_context_bars": progress(unique_context_bars, args.min_unique_context_bars),
        "forward_entry_intents": progress(forward_entry_intents, args.min_forward_entry_intents),
        "forward_resolved_entries": progress(forward_resolved, args.min_forward_resolved),
        "oi_guard_entry_contexts": progress(oi_guard_entry_contexts, args.min_oi_guard_entry_contexts),
        "oi_guard_resolved_contexts": progress(oi_guard_resolved_contexts, args.min_oi_guard_resolved),
        "positive_forward_expectancy": {
            "value": forward_summary.get("expectancy_r"),
            "required": args.min_forward_expectancy_r,
            "deficit": None if forward_summary.get("expectancy_r") is None else round(max(0.0, args.min_forward_expectancy_r - forward_expectancy), 6),
            "progress_pct": 0.0 if forward_summary.get("expectancy_r") is None else 100.0 if forward_expectancy >= args.min_forward_expectancy_r else 0.0,
            "passed": forward_summary.get("expectancy_r") is not None and forward_expectancy >= args.min_forward_expectancy_r,
        },
        "positive_oi_guard_expectancy": {
            "value": context_summary.get("oi_guard_expectancy_r"),
            "required": args.min_oi_guard_expectancy_r,
            "deficit": None if context_summary.get("oi_guard_expectancy_r") is None else round(max(0.0, args.min_oi_guard_expectancy_r - oi_guard_expectancy), 6),
            "progress_pct": 0.0 if context_summary.get("oi_guard_expectancy_r") is None else 100.0 if oi_guard_expectancy >= args.min_oi_guard_expectancy_r else 0.0,
            "passed": context_summary.get("oi_guard_expectancy_r") is not None and oi_guard_expectancy >= args.min_oi_guard_expectancy_r,
        },
    }

    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "forward_outcome_accumulator_observe_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "inputs": {
            "forward_scoreboard": str(forward_scoreboard_path),
            "oi_funding_scoreboard": str(oi_funding_scoreboard_path),
            "promotion_gate": str(promotion_gate_path),
            "forward_journal": str(forward_journal_path),
            "context_journal": str(context_journal_path),
        },
        "thresholds": {
            "min_unique_context_bars": args.min_unique_context_bars,
            "min_forward_entry_intents": args.min_forward_entry_intents,
            "min_forward_resolved": args.min_forward_resolved,
            "min_oi_guard_entry_contexts": args.min_oi_guard_entry_contexts,
            "min_oi_guard_resolved": args.min_oi_guard_resolved,
            "min_forward_expectancy_r": args.min_forward_expectancy_r,
            "min_oi_guard_expectancy_r": args.min_oi_guard_expectancy_r,
        },
        "metrics": metrics,
        "gates": gates,
        "can_trade": False,
    }
    classification, next_action = classify(report)
    report["classification"] = classification
    report["decision"] = classification
    report["next_action"] = next_action
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Accumulate forward evidence for strategy/OI-guard promotion readiness")
    parser.add_argument("--forward-scoreboard", default="docs/STRATEGY_MIX_FORWARD_SCOREBOARD_2026-06-08.json")
    parser.add_argument("--oi-funding-scoreboard", default="docs/OI_FUNDING_FORWARD_CONTEXT_SCOREBOARD_2026-06-15.json")
    parser.add_argument("--promotion-gate", default="docs/OI_GUARD_PROMOTION_GATE_2026-06-15.json")
    parser.add_argument("--forward-journal", default="logs/forward_paper_feed/strategy_mix_forward_paper_feed.jsonl")
    parser.add_argument("--context-journal", default="logs/forward_paper_feed/oi_funding_forward_context_observer.jsonl")
    parser.add_argument("--min-unique-context-bars", type=int, default=50)
    parser.add_argument("--min-forward-entry-intents", type=int, default=30)
    parser.add_argument("--min-forward-resolved", type=int, default=30)
    parser.add_argument("--min-oi-guard-entry-contexts", type=int, default=20)
    parser.add_argument("--min-oi-guard-resolved", type=int, default=20)
    parser.add_argument("--min-forward-expectancy-r", type=float, default=0.05)
    parser.add_argument("--min-oi-guard-expectancy-r", type=float, default=0.05)
    parser.add_argument("--out-prefix", default="docs/FORWARD_OUTCOME_ACCUMULATOR_2026-06-16")
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "classification": report["classification"],
                "next_action": report["next_action"],
                "forward_entry_intents": report["metrics"]["forward_entry_intents"],
                "forward_resolved_entries": report["metrics"]["forward_resolved_entries"],
                "oi_guard_resolved_contexts": report["metrics"]["oi_guard_resolved_contexts"],
                "json": str(json_path),
                "md": str(md_path),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


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


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def key_for_context(row: dict[str, Any]) -> str:
    card = row.get("forward_card") if isinstance(row.get("forward_card"), dict) else {}
    return f"{card.get('strategy_id')}|{card.get('latest_closed_bar_ts')}"


def key_for_outcome(row: dict[str, Any]) -> str:
    return f"{row.get('strategy_id')}|{row.get('signal_bar_ts')}"


def outcome_summary(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [item for item in outcomes if isinstance(item.get("r"), (int, float))]
    values = [float(item["r"]) for item in resolved]
    wins = [value for value in values if value > 0]
    if not outcomes:
        classification = "no_entry_contexts"
    elif not values:
        classification = "pending_only"
    elif len(values) < 30:
        classification = "insufficient_sample_positive" if sum(values) / len(values) > 0 else "insufficient_sample_negative_or_mixed"
    elif sum(values) / len(values) > 0.05:
        classification = "candidate_context_for_guard_research"
    else:
        classification = "negative_or_mixed"
    return {
        "classification": classification,
        "entry_contexts": len(outcomes),
        "resolved": len(values),
        "unresolved": len(outcomes) - len(values),
        "wins": len(wins),
        "winrate_pct": round(len(wins) / len(values) * 100, 3) if values else None,
        "expectancy_r": round(sum(values) / len(values), 6) if values else None,
        "net_r_total": round(sum(values), 6) if values else None,
    }


def grouped_outcome_summary(contexts: list[dict[str, Any]], outcomes_by_key: dict[str, dict[str, Any]], group_field: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in contexts:
        context = row.get("context") if isinstance(row.get("context"), dict) else {}
        value = str(context.get(group_field) or "unknown")
        outcome = outcomes_by_key.get(key_for_context(row))
        if outcome is not None:
            groups[value].append(outcome)
    rows: list[dict[str, Any]] = []
    for value, outcomes in sorted(groups.items()):
        rows.append({"group": value, **outcome_summary(outcomes)})
    return rows


def guard_state(row: dict[str, Any]) -> str:
    guard = row.get("oi_guard_candidate") if isinstance(row.get("oi_guard_candidate"), dict) else {}
    return str(guard.get("state") or "unknown")


def guard_would_keep(row: dict[str, Any]) -> str:
    guard = row.get("oi_guard_candidate") if isinstance(row.get("oi_guard_candidate"), dict) else {}
    value = guard.get("would_keep_long_signal")
    if value is True:
        return "would_keep"
    if value is False:
        return "would_block"
    return "unknown"


def grouped_outcome_summary_custom(
    contexts: list[dict[str, Any]],
    outcomes_by_key: dict[str, dict[str, Any]],
    group_fn,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in contexts:
        value = str(group_fn(row) or "unknown")
        outcome = outcomes_by_key.get(key_for_context(row))
        if outcome is not None:
            groups[value].append(outcome)
    rows: list[dict[str, Any]] = []
    for value, outcomes in sorted(groups.items()):
        rows.append({"group": value, **outcome_summary(outcomes)})
    return rows


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    counts = report.get("counts") if isinstance(report.get("counts"), dict) else {}
    latest = report.get("latest_context") if isinstance(report.get("latest_context"), dict) else {}
    latest_guard = report.get("latest_oi_guard_candidate") if isinstance(report.get("latest_oi_guard_candidate"), dict) else {}
    lines = [
        "# OI/Funding Forward Context Scoreboard",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Public/local evidence only.",
        "- Scores OI/funding context observations against forward paper outcomes when outcomes exist.",
        "- No private credentials, no exchange account, no orders, no guard promotion by itself.",
        "",
        "## Summary",
        "",
        f"- Classification: `{summary.get('classification')}`.",
        f"- Context observations: `{summary.get('context_observations')}`.",
        f"- Unique context bars: `{summary.get('unique_context_bars')}`.",
        f"- Entry contexts: `{summary.get('entry_contexts')}`.",
        f"- Resolved entry contexts: `{summary.get('resolved')}`.",
        f"- Expectancy: `{summary.get('expectancy_r')}` R.",
        f"- Data degraded observations: `{summary.get('data_degraded_observations')}`.",
        f"- OI guard entry contexts: `{summary.get('oi_guard_entry_contexts')}`.",
        f"- OI guard resolved contexts: `{summary.get('oi_guard_resolved_contexts')}`.",
        "",
        "## Latest Context",
        "",
        f"- Bar: `{latest.get('bar')}`.",
        f"- Card status: `{latest.get('card_status')}`.",
        f"- Context bias: `{latest.get('context_bias')}`.",
        f"- Funding state: `{latest.get('funding_state')}`.",
        f"- OI state: `{latest.get('oi_state')}`.",
        f"- Data degraded: `{latest.get('data_degraded')}`.",
        f"- OI guard state: `{latest_guard.get('state')}`.",
        f"- OI guard would keep long: `{latest_guard.get('would_keep_long_signal')}`.",
        f"- OI guard can filter now: `{latest_guard.get('can_filter_now')}`.",
        "",
        "## Observation Counts",
        "",
        f"- Context bias counts: `{counts.get('context_bias')}`.",
        f"- Funding state counts: `{counts.get('funding_state')}`.",
        f"- OI state counts: `{counts.get('oi_state')}`.",
        f"- OI guard state counts: `{counts.get('oi_guard_state')}`.",
        f"- OI guard would-keep counts: `{counts.get('oi_guard_would_keep')}`.",
        f"- Card status counts: `{counts.get('card_status')}`.",
        "",
    ]
    group_rows = report.get("outcome_by_context_bias") if isinstance(report.get("outcome_by_context_bias"), list) else []
    if group_rows:
        lines.extend(["## Outcome By Context Bias", ""])
        for item in group_rows:
            lines.append(
                f"- `{item.get('group')}`: resolved `{item.get('resolved')}`, expectancy `{item.get('expectancy_r')}` R, classification `{item.get('classification')}`."
            )
        lines.append("")
    guard_rows = report.get("outcome_by_oi_guard_state") if isinstance(report.get("outcome_by_oi_guard_state"), list) else []
    if guard_rows:
        lines.extend(["## Outcome By OI Guard State", ""])
        for item in guard_rows:
            lines.append(
                f"- `{item.get('group')}`: resolved `{item.get('resolved')}`, expectancy `{item.get('expectancy_r')}` R, classification `{item.get('classification')}`."
            )
        lines.append("")
    lines.extend(
        [
            "## Decision",
            "",
            f"- `{report.get('decision')}`.",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    context_journal = resolve_path(args.context_journal)
    forward_scoreboard_path = resolve_path(args.forward_scoreboard)
    contexts = read_jsonl(context_journal)
    scoreboard = read_json(forward_scoreboard_path)
    outcomes = scoreboard.get("outcomes") if isinstance(scoreboard, dict) and isinstance(scoreboard.get("outcomes"), list) else []
    outcomes_by_key = {key_for_outcome(item): item for item in outcomes if item.get("strategy_id") and item.get("signal_bar_ts")}
    unique_context_keys = {key_for_context(item) for item in contexts if key_for_context(item) != "None|None"}
    entry_contexts = [item for item in contexts if (item.get("forward_card") or {}).get("status") == "paper_entry_intent"]
    joined_outcomes = [outcomes_by_key[key_for_context(item)] for item in entry_contexts if key_for_context(item) in outcomes_by_key]
    degraded = [item for item in contexts if item.get("data_degraded")]
    bias_counter: Counter[str] = Counter()
    funding_counter: Counter[str] = Counter()
    oi_counter: Counter[str] = Counter()
    guard_state_counter: Counter[str] = Counter()
    guard_keep_counter: Counter[str] = Counter()
    card_counter: Counter[str] = Counter()
    for item in contexts:
        context = item.get("context") if isinstance(item.get("context"), dict) else {}
        card = item.get("forward_card") if isinstance(item.get("forward_card"), dict) else {}
        bias_counter[str(context.get("context_bias") or "unknown")] += 1
        funding_counter[str(context.get("funding_state") or "unknown")] += 1
        oi_counter[str(context.get("oi_state") or "unknown")] += 1
        guard_state_counter[guard_state(item)] += 1
        guard_keep_counter[guard_would_keep(item)] += 1
        card_counter[str(card.get("status") or "unknown")] += 1
    outcome_base = outcome_summary(joined_outcomes)
    if not contexts:
        classification = "no_context_observations"
    elif not entry_contexts:
        classification = "context_observing_no_entries_yet"
    else:
        classification = outcome_base["classification"]
    latest = contexts[-1] if contexts else {}
    latest_context = latest.get("context") if isinstance(latest.get("context"), dict) else {}
    latest_card = latest.get("forward_card") if isinstance(latest.get("forward_card"), dict) else {}
    latest_guard = latest.get("oi_guard_candidate") if isinstance(latest.get("oi_guard_candidate"), dict) else {}
    guard_entry_contexts = [
        item
        for item in entry_contexts
        if isinstance(item.get("oi_guard_candidate"), dict)
    ]
    guard_joined_outcomes = [
        outcomes_by_key[key_for_context(item)]
        for item in guard_entry_contexts
        if key_for_context(item) in outcomes_by_key
    ]
    guard_outcome_base = outcome_summary(guard_joined_outcomes)
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "oi_funding_forward_context_scoreboard_public_local_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "context_journal": str(context_journal),
        "forward_scoreboard": str(forward_scoreboard_path),
        "summary": {
            **outcome_base,
            "classification": classification,
            "context_observations": len(contexts),
            "unique_context_bars": len(unique_context_keys),
            "data_degraded_observations": len(degraded),
            "oi_guard_entry_contexts": len(guard_entry_contexts),
            "oi_guard_resolved_contexts": guard_outcome_base.get("resolved"),
            "oi_guard_expectancy_r": guard_outcome_base.get("expectancy_r"),
            "oi_guard_classification": guard_outcome_base.get("classification"),
        },
        "latest_context": {
            "bar": latest_card.get("latest_closed_bar_ts"),
            "card_status": latest_card.get("status"),
            "context_bias": latest_context.get("context_bias"),
            "funding_state": latest_context.get("funding_state"),
            "oi_state": latest_context.get("oi_state"),
            "data_degraded": latest.get("data_degraded"),
        },
        "latest_oi_guard_candidate": {
            "name": latest_guard.get("name"),
            "state": latest_guard.get("state"),
            "would_keep_long_signal": latest_guard.get("would_keep_long_signal"),
            "can_filter_now": latest_guard.get("can_filter_now"),
            "live_permission": latest_guard.get("live_permission"),
            "mode": latest_guard.get("mode"),
        },
        "counts": {
            "context_bias": dict(bias_counter),
            "funding_state": dict(funding_counter),
            "oi_state": dict(oi_counter),
            "oi_guard_state": dict(guard_state_counter),
            "oi_guard_would_keep": dict(guard_keep_counter),
            "card_status": dict(card_counter),
        },
        "outcome_by_context_bias": grouped_outcome_summary(contexts, outcomes_by_key, "context_bias"),
        "outcome_by_funding_state": grouped_outcome_summary(contexts, outcomes_by_key, "funding_state"),
        "outcome_by_oi_state": grouped_outcome_summary(contexts, outcomes_by_key, "oi_state"),
        "outcome_by_oi_guard_state": grouped_outcome_summary_custom(contexts, outcomes_by_key, guard_state),
        "outcome_by_oi_guard_would_keep": grouped_outcome_summary_custom(contexts, outcomes_by_key, guard_would_keep),
        "decision": "context_scoreboard_observe_only_no_orders",
        "can_trade": False,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Score OI/funding context observations against forward paper outcomes")
    parser.add_argument("--context-journal", default="logs/forward_paper_feed/oi_funding_forward_context_observer.jsonl")
    parser.add_argument("--forward-scoreboard", default="docs/STRATEGY_MIX_FORWARD_SCOREBOARD_2026-06-08.json")
    parser.add_argument("--out-prefix", default="docs/OI_FUNDING_FORWARD_CONTEXT_SCOREBOARD_2026-06-15")
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
                "classification": report["summary"]["classification"],
                "context_observations": report["summary"]["context_observations"],
                "entry_contexts": report["summary"]["entry_contexts"],
                "resolved": report["summary"]["resolved"],
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

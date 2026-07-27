#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else {}


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def bootstrap_delta_lower(
    candidate: list[float],
    baseline: list[float],
    *,
    iterations: int = 2_000,
    seed: int = 20260623,
) -> float | None:
    if not candidate or not baseline:
        return None
    rng = random.Random(seed)
    deltas = []
    for _ in range(iterations):
        candidate_mean = statistics.mean(rng.choice(candidate) for _ in candidate)
        baseline_mean = statistics.mean(rng.choice(baseline) for _ in baseline)
        deltas.append(candidate_mean - baseline_mean)
    value = percentile(deltas, 0.10)
    return round(float(value), 6) if value is not None else None


def evaluate_gate(
    rows: list[dict[str, Any]],
    *,
    min_total_resolved: int,
    min_baseline_resolved: int,
    min_bin_resolved: int,
    min_bin_expectancy_r: float,
    min_delta_r: float,
    bootstrap_iterations: int = 2_000,
) -> dict[str, Any]:
    resolved = [row for row in rows if isinstance(row.get("r"), (int, float))]
    groups: dict[str, list[float]] = defaultdict(list)
    for row in resolved:
        groups[str(row.get("score_bin") or "unknown")].append(float(row["r"]))
    baseline = groups.get("inactive", [])
    baseline_expectancy = statistics.mean(baseline) if baseline else None
    bins: dict[str, Any] = {}
    qualifying: list[str] = []
    eligible: list[str] = []
    for name, values in sorted(groups.items()):
        if name in {"inactive", "unknown"}:
            continue
        expectancy = statistics.mean(values)
        delta = expectancy - baseline_expectancy if baseline_expectancy is not None else None
        enough = len(values) >= min_bin_resolved
        if enough:
            eligible.append(name)
        lower = (
            bootstrap_delta_lower(values, baseline, iterations=bootstrap_iterations)
            if enough and len(baseline) >= min_baseline_resolved
            else None
        )
        checks = {
            "min_resolved": enough,
            "min_expectancy_r": expectancy >= min_bin_expectancy_r,
            "min_delta_r": isinstance(delta, float) and delta >= min_delta_r,
            "bootstrap_p10_delta_positive": isinstance(lower, float) and lower > 0.0,
        }
        if all(checks.values()):
            qualifying.append(name)
        bins[name] = {
            "resolved": len(values),
            "expectancy_r": round(expectancy, 6),
            "delta_vs_inactive_r": round(delta, 6) if delta is not None else None,
            "bootstrap_p10_delta_r": lower,
            "checks": checks,
        }
    sample_checks = {
        "min_total_resolved": len(resolved) >= min_total_resolved,
        "min_inactive_resolved": len(baseline) >= min_baseline_resolved,
        "eligible_non_inactive_bin": bool(eligible),
    }
    if not sample_checks["min_total_resolved"]:
        classification = "collecting_total_forward_outcomes"
    elif not sample_checks["min_inactive_resolved"]:
        classification = "collecting_inactive_baseline_outcomes"
    elif not sample_checks["eligible_non_inactive_bin"]:
        classification = "collecting_non_inactive_bin_outcomes"
    elif qualifying:
        classification = "independent_forward_score_evidence_ready_for_research_review"
    else:
        classification = "forward_score_evidence_no_stable_improvement"
    review_allowed = all(sample_checks.values()) and bool(qualifying)
    return {
        "classification": classification,
        "resolved_total": len(resolved),
        "inactive_resolved": len(baseline),
        "inactive_expectancy_r": round(baseline_expectancy, 6) if baseline_expectancy is not None else None,
        "resolved_by_bin": {name: len(values) for name, values in sorted(groups.items())},
        "sample_checks": sample_checks,
        "eligible_bins": eligible,
        "qualifying_bins": qualifying,
        "bin_evidence": bins,
        "research_review_allowed": review_allowed,
        "filter_change_allowed": False,
        "veto_allowed": False,
        "paper_execution_allowed": False,
        "live_execution_allowed": False,
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    evidence = report["evidence"]
    lines = [
        "# Edge Liquidation Continuous Score Evidence Gate",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        f"- Classification: `{evidence['classification']}`.",
        f"- Resolved total/inactive: `{evidence['resolved_total']}` / `{evidence['inactive_resolved']}`.",
        f"- Eligible/qualifying bins: `{evidence['eligible_bins']}` / `{evidence['qualifying_bins']}`.",
        f"- Research review allowed: `{evidence['research_review_allowed']}`.",
        "- Filter, veto, paper and live execution remain disabled regardless of result.",
        "- `can_trade=false`.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate future Edge continuous-score evidence without enabling a filter")
    parser.add_argument("--scoreboard", default="docs/EDGE_LIQUIDATION_CONTEXT_SHADOW_SCOREBOARD_2026-06-23.json")
    parser.add_argument("--score-lock", default="configs/EDGE_LIQUIDATION_SCORE_SHADOW_LOCK.json")
    parser.add_argument("--min-total-resolved", type=int, default=30)
    parser.add_argument("--min-baseline-resolved", type=int, default=8)
    parser.add_argument("--min-bin-resolved", type=int, default=8)
    parser.add_argument("--min-bin-expectancy-r", type=float, default=0.05)
    parser.add_argument("--min-delta-r", type=float, default=0.10)
    parser.add_argument("--bootstrap-iterations", type=int, default=2_000)
    parser.add_argument("--out-prefix", default="docs/EDGE_LIQUIDATION_SCORE_EVIDENCE_GATE_2026-06-23")
    args = parser.parse_args()

    scoreboard_path = resolve_path(args.scoreboard)
    lock_path = resolve_path(args.score_lock)
    scoreboard = read_json(scoreboard_path)
    lock = read_json(lock_path)
    lock_valid = bool(
        lock.get("status") == "frozen_train_only_forward_shadow"
        and lock.get("source", {}).get("oos_used_for_thresholds") is False
        and lock.get("boundaries", {}).get("allow_filter") is False
        and lock.get("boundaries", {}).get("can_trade") is False
    )
    rows = scoreboard.get("labelled_outcomes") if isinstance(scoreboard.get("labelled_outcomes"), list) else []
    evidence = evaluate_gate(
        rows,
        min_total_resolved=args.min_total_resolved,
        min_baseline_resolved=args.min_baseline_resolved,
        min_bin_resolved=args.min_bin_resolved,
        min_bin_expectancy_r=args.min_bin_expectancy_r,
        min_delta_r=args.min_delta_r,
        bootstrap_iterations=args.bootstrap_iterations,
    )
    if not lock_valid:
        evidence["classification"] = "blocked_invalid_or_missing_train_only_score_lock"
        evidence["research_review_allowed"] = False
    report = {
        "generated_at": now_iso(),
        "inputs": {"scoreboard": rel(scoreboard_path), "score_lock": rel(lock_path), "score_lock_valid": lock_valid},
        "requirements": {
            "min_total_resolved": args.min_total_resolved,
            "min_baseline_resolved": args.min_baseline_resolved,
            "min_bin_resolved": args.min_bin_resolved,
            "min_bin_expectancy_r": args.min_bin_expectancy_r,
            "min_delta_r": args.min_delta_r,
            "bootstrap_iterations": args.bootstrap_iterations,
            "bootstrap_delta_percentile": 0.10,
        },
        "evidence": evidence,
        "runtime_boundary": {
            "research_review_only": True,
            "changes_edge_signal": False,
            "filter_change_allowed": False,
            "veto_allowed": False,
            "paper_execution_allowed": False,
            "live_execution_allowed": False,
            "sends_orders": False,
            "can_trade": False,
        },
        "decision": evidence["classification"],
        "next_action": "continue independent forward collection" if not evidence["research_review_allowed"] else "open a new precommitted research design; do not change runtime filter",
        "can_trade": False,
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "resolved": evidence["resolved_total"], "qualifying_bins": evidence["qualifying_bins"], "research_review_allowed": evidence["research_review_allowed"], "filter_change_allowed": False, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

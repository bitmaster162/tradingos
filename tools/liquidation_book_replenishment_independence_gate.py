#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
MINUTE_MS = 60_000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    yield payload
    except OSError:
        return


def parse_ts_ms(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def validate_policy(policy: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    boundary = policy.get("runtime_boundary") if isinstance(policy.get("runtime_boundary"), dict) else {}
    governance = policy.get("governance") if isinstance(policy.get("governance"), dict) else {}
    requirements = (
        policy.get("independence_requirements")
        if isinstance(policy.get("independence_requirements"), dict)
        else {}
    )
    if policy.get("status") != "prospective_governance_gate_before_forward_outcomes":
        failures.append("policy_status")
    if policy.get("can_trade") is not False:
        failures.append("policy_can_trade")
    if governance.get("retuning_allowed") is not False or governance.get("automatic_promotion_allowed") is not False:
        failures.append("governance_boundary")
    for key in ("paper_entries_allowed", "live_entries_allowed", "orders_allowed", "uses_private_credentials", "can_trade"):
        if boundary.get(key) is not False:
            failures.append(f"runtime_boundary_{key}")
    positive_integer_fields = (
        "independent_block_minutes",
        "minimum_independent_blocks",
        "minimum_unique_utc_days",
        "generic_event_overlap_window_minutes",
        "minimum_matched_outcomes_for_correlation",
    )
    for key in positive_integer_fields:
        if as_int(requirements.get(key)) <= 0:
            failures.append(key)
    for key in ("maximum_single_day_event_share", "maximum_generic_event_overlap_rate"):
        if not 0.0 < as_float(requirements.get(key), -1.0) <= 1.0:
            failures.append(key)
    if not 0.0 < as_float(requirements.get("maximum_absolute_matched_outcome_correlation"), -1.0) <= 1.0:
        failures.append("maximum_absolute_matched_outcome_correlation")
    return sorted(set(failures))


def candidate_events(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        if row.get("can_trade") is not False:
            continue
        event_id = str(row.get("event_id") or "")
        event_ms = parse_ts_ms(row.get("event_time"))
        if event_id and event_ms is not None:
            result[event_id] = event_ms
    return result


def generic_events(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        if row.get("can_trade") is not False:
            continue
        event_id = str(row.get("event_id") or "")
        event_ms = as_int(row.get("shock_minute_ms"), -1)
        if event_id and event_ms >= 0:
            result[event_id] = event_ms
    return result


def nearest_generic_matches(
    candidate: dict[str, int],
    generic: dict[str, int],
    window_ms: int,
) -> dict[str, str]:
    matches: dict[str, str] = {}
    generic_items = sorted(generic.items(), key=lambda item: item[1])
    for candidate_id, candidate_ms in candidate.items():
        eligible = [item for item in generic_items if abs(item[1] - candidate_ms) <= window_ms]
        if eligible:
            matches[candidate_id] = min(eligible, key=lambda item: abs(item[1] - candidate_ms))[0]
    return matches


def pearson(values_a: list[float], values_b: list[float]) -> float | None:
    if len(values_a) != len(values_b) or len(values_a) < 2:
        return None
    mean_a = statistics.fmean(values_a)
    mean_b = statistics.fmean(values_b)
    centered_a = [value - mean_a for value in values_a]
    centered_b = [value - mean_b for value in values_b]
    denominator = math.sqrt(sum(value * value for value in centered_a) * sum(value * value for value in centered_b))
    if denominator <= 0:
        return None
    return sum(a * b for a, b in zip(centered_a, centered_b)) / denominator


def matched_outcome_correlation(
    candidate_rows: Iterable[dict[str, Any]],
    generic_rows: Iterable[dict[str, Any]],
    matches: dict[str, str],
) -> tuple[int, float | None]:
    candidate_values: dict[tuple[str, int], float] = {}
    for row in candidate_rows:
        event_id = str(row.get("event_id") or "")
        horizon = as_int(row.get("horizon_minutes"), -1)
        if event_id and horizon > 0 and row.get("can_trade") is False:
            candidate_values[(event_id, horizon)] = as_float(row.get("net_bps"))
    generic_values: dict[tuple[str, int], float] = {}
    for row in generic_rows:
        event_id = str(row.get("event_id") or "")
        horizon = as_int(row.get("horizon_minutes"), -1)
        if event_id and horizon > 0 and row.get("can_trade") is False:
            generic_values[(event_id, horizon)] = as_float(row.get("net_base_bps"))
    left: list[float] = []
    right: list[float] = []
    for candidate_id, generic_id in matches.items():
        horizons = sorted(
            {horizon for event_id, horizon in candidate_values if event_id == candidate_id}
            & {horizon for event_id, horizon in generic_values if event_id == generic_id}
        )
        for horizon in horizons:
            left.append(candidate_values[(candidate_id, horizon)])
            right.append(generic_values[(generic_id, horizon)])
    return len(left), pearson(left, right)


def structural_metrics(events: dict[str, int], block_minutes: int) -> dict[str, Any]:
    block_ms = block_minutes * MINUTE_MS
    blocks = {event_ms - (event_ms % block_ms) for event_ms in events.values()}
    days = [datetime.fromtimestamp(event_ms / 1000, tz=timezone.utc).date().isoformat() for event_ms in events.values()]
    day_counts = Counter(days)
    maximum_day_events = max(day_counts.values()) if day_counts else 0
    share = maximum_day_events / len(events) if events else 0.0
    return {
        "events": len(events),
        "independent_blocks": len(blocks),
        "unique_utc_days": len(day_counts),
        "maximum_single_day_events": maximum_day_events,
        "maximum_single_day_event_share": round(share, 8),
    }


def classify(
    *,
    base_report: dict[str, Any],
    policy: dict[str, Any],
    metrics: dict[str, Any],
    overlap_rate: float,
    matched_outcomes: int,
    correlation: float | None,
) -> tuple[str, list[str], str]:
    base_decision = str(base_report.get("decision") or "missing")
    required_base = str(policy.get("base_pass_decision") or "")
    requirements = policy["independence_requirements"]
    if base_report.get("can_trade") is not False:
        return (
            "liquidation_book_replenishment_independence_gate_blocked_unsafe_base",
            ["base_can_trade_boundary"],
            "repair the base observer boundary before using any result",
        )
    if "failed_gate_for_tombstone_review" in base_decision:
        return (
            "liquidation_book_replenishment_independence_gate_blocked_base_failed",
            ["base_statistical_gate_failed"],
            "tombstone review; independence cannot rescue a failed base edge",
        )
    if base_decision != required_base:
        return (
            "liquidation_book_replenishment_independence_gate_collecting_base_sample",
            ["base_statistical_gate_not_passed"],
            "keep collecting untouched outcomes; do not interpret independence before the base gate passes",
        )

    blockers: list[str] = []
    if as_int(metrics.get("independent_blocks")) < as_int(requirements.get("minimum_independent_blocks")):
        blockers.append("minimum_independent_blocks")
    if as_int(metrics.get("unique_utc_days")) < as_int(requirements.get("minimum_unique_utc_days")):
        blockers.append("minimum_unique_utc_days")
    if as_float(metrics.get("maximum_single_day_event_share")) > as_float(
        requirements.get("maximum_single_day_event_share")
    ):
        blockers.append("maximum_single_day_event_share")
    if blockers:
        return (
            "liquidation_book_replenishment_independence_gate_collecting_independent_sample",
            blockers,
            "collect more independent time blocks and UTC days without retuning",
        )

    if overlap_rate > as_float(requirements.get("maximum_generic_event_overlap_rate")):
        return (
            "liquidation_book_replenishment_independence_gate_same_sleeve_overlap",
            ["generic_event_overlap_rate"],
            "treat as the same portfolio sleeve; do not claim diversification",
        )
    minimum_pairs = as_int(requirements.get("minimum_matched_outcomes_for_correlation"))
    maximum_correlation = as_float(requirements.get("maximum_absolute_matched_outcome_correlation"))
    if matched_outcomes >= minimum_pairs and correlation is not None and abs(correlation) > maximum_correlation:
        return (
            "liquidation_book_replenishment_independence_gate_same_sleeve_correlation",
            ["matched_outcome_correlation"],
            "treat as the same portfolio sleeve; do not claim diversification",
        )
    return (
        "liquidation_book_replenishment_independence_gate_passed_manual_review_only",
        [],
        "manual research review only; this does not permit paper or live execution",
    )


def render_markdown(report: dict[str, Any]) -> str:
    evidence = report["evidence"]
    lines = [
        "# Liquidation Book Replenishment Independence Gate",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        f"- Base decision: `{evidence['base_decision']}`",
        "",
        "## Independent Sample",
        "",
        f"- Candidate events: `{evidence['candidate_events']}`",
        f"- Independent 30-minute blocks: `{evidence['independent_blocks']}`",
        f"- Unique UTC days: `{evidence['unique_utc_days']}`",
        f"- Maximum single-day share: `{evidence['maximum_single_day_event_share']}`",
        "",
        "## Generic Observer Overlap",
        "",
        f"- Generic events: `{evidence['generic_events']}`",
        f"- Matched candidate events: `{evidence['matched_candidate_events']}`",
        f"- Event overlap rate: `{evidence['generic_event_overlap_rate']}`",
        f"- Matched outcome pairs: `{evidence['matched_outcome_pairs']}`",
        f"- Matched outcome correlation: `{evidence['matched_outcome_correlation']}`",
        "",
        "## Blockers",
        "",
    ]
    for blocker in report.get("blockers") or ["none"]:
        lines.append(f"- `{blocker}`")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- The base statistical gate must pass before independence can pass.",
            "- Clustered events cannot inflate the independent sample count.",
            "- High overlap or correlation means same sleeve, not diversification.",
            "- No paper entries, live entries or orders are permitted.",
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(
    *,
    policy_path: Path,
    base_report_path: Path,
    candidate_ledger_path: Path,
    generic_events_path: Path,
    generic_outcomes_path: Path,
) -> dict[str, Any]:
    policy = read_json(policy_path)
    failures = validate_policy(policy)
    if failures:
        raise ValueError("invalid independence policy: " + ",".join(failures))
    base_report = read_json(base_report_path)
    candidate_rows = list(iter_jsonl(candidate_ledger_path))
    generic_event_rows = list(iter_jsonl(generic_events_path))
    generic_outcome_rows = list(iter_jsonl(generic_outcomes_path))
    candidate = candidate_events(candidate_rows)
    generic = generic_events(generic_event_rows)
    requirements = policy["independence_requirements"]
    window_ms = as_int(requirements.get("generic_event_overlap_window_minutes")) * MINUTE_MS
    matches = nearest_generic_matches(candidate, generic, window_ms)
    overlap_rate = len(matches) / len(candidate) if candidate else 0.0
    matched_pairs, correlation = matched_outcome_correlation(candidate_rows, generic_outcome_rows, matches)
    metrics = structural_metrics(candidate, as_int(requirements.get("independent_block_minutes")))
    decision, blockers, next_action = classify(
        base_report=base_report,
        policy=policy,
        metrics=metrics,
        overlap_rate=overlap_rate,
        matched_outcomes=matched_pairs,
        correlation=correlation,
    )
    return {
        "generated_at": now_iso(),
        "tool": "tools/liquidation_book_replenishment_independence_gate.py",
        "decision": decision,
        "can_trade": False,
        "automatic_promotion_allowed": False,
        "policy": {
            "path": portable(policy_path),
            "sha256": sha256_file(policy_path),
            "policy_id": policy.get("policy_id"),
            "created_at": policy.get("created_at"),
        },
        "sources": {
            "base_report": portable(base_report_path),
            "candidate_ledger": portable(candidate_ledger_path),
            "generic_events": portable(generic_events_path),
            "generic_outcomes": portable(generic_outcomes_path),
        },
        "evidence": {
            "base_decision": base_report.get("decision"),
            "candidate_events": metrics["events"],
            "independent_blocks": metrics["independent_blocks"],
            "unique_utc_days": metrics["unique_utc_days"],
            "maximum_single_day_event_share": metrics["maximum_single_day_event_share"],
            "generic_events": len(generic),
            "matched_candidate_events": len(matches),
            "generic_event_overlap_rate": round(overlap_rate, 8),
            "matched_outcome_pairs": matched_pairs,
            "matched_outcome_correlation": round(correlation, 8) if correlation is not None else None,
        },
        "requirements": requirements,
        "blockers": blockers,
        "next_action": next_action,
        "runtime_boundary": {
            "audit_only": True,
            "paper_entries_allowed": False,
            "live_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed independence gate for liquidation book replenishment")
    parser.add_argument("--policy", default="configs/LIQUIDATION_BOOK_REPLENISHMENT_INDEPENDENCE_POLICY_2026-07-12.json")
    parser.add_argument("--base-report", default="docs/LIQUIDATION_BOOK_REPLENISHMENT_FORWARD_OBSERVER_2026-07-12.json")
    parser.add_argument("--candidate-ledger", default="logs/liquidation_book_replenishment/forward_outcomes.jsonl")
    parser.add_argument(
        "--generic-events",
        default="HANDOFF/INCOMING/codex/20260711_book_replenishment_forward/runtime/events.jsonl",
    )
    parser.add_argument(
        "--generic-outcomes",
        default="HANDOFF/INCOMING/codex/20260711_book_replenishment_forward/runtime/outcomes.jsonl",
    )
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_BOOK_REPLENISHMENT_INDEPENDENCE_GATE_2026-07-12")
    args = parser.parse_args()
    try:
        report = build_report(
            policy_path=resolve_path(args.policy),
            base_report_path=resolve_path(args.base_report),
            candidate_ledger_path=resolve_path(args.candidate_ledger),
            generic_events_path=resolve_path(args.generic_events),
            generic_outcomes_path=resolve_path(args.generic_outcomes),
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"decision": "liquidation_book_replenishment_independence_gate_error", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "candidate_events": report["evidence"]["candidate_events"],
                "independent_blocks": report["evidence"]["independent_blocks"],
                "event_overlap_rate": report["evidence"]["generic_event_overlap_rate"],
                "can_trade": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

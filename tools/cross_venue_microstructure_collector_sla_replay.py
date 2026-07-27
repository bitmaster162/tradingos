#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_jsonl(path: Path, *, max_lines: int = 10000) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []
    for line in lines[-max(1, max_lines) :]:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_degraded(row: dict[str, Any]) -> bool:
    return str(row.get("decision") or "").startswith("collector_sla_degraded")


def is_effectively_degraded(row: dict[str, Any], superseded_checks: set[str]) -> bool:
    if not is_degraded(row):
        return False
    failed = row.get("failed_checks") if isinstance(row.get("failed_checks"), list) else []
    failed_set = {str(item) for item in failed}
    return not (failed_set and failed_set.issubset(superseded_checks))


def numeric_values(rows: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = safe_float(row.get(key))
        if value is not None:
            values.append(value)
    return values


def round_or_none(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(value, digits)


def build_incidents(rows: list[dict[str, Any]], superseded_checks: set[str] | None = None) -> list[dict[str, Any]]:
    superseded_checks = superseded_checks or set()
    incidents: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for row in rows:
        if is_effectively_degraded(row, superseded_checks):
            failed = row.get("failed_checks") if isinstance(row.get("failed_checks"), list) else []
            if current is None:
                current = {
                    "start_generated_at": row.get("generated_at"),
                    "start_data_generated_at": row.get("data_generated_at"),
                    "end_generated_at": row.get("generated_at"),
                    "observations": 0,
                    "decisions": Counter(),
                    "failed_checks": Counter(),
                    "recovered": False,
                }
            current["end_generated_at"] = row.get("generated_at")
            current["observations"] += 1
            current["decisions"][str(row.get("decision") or "missing")] += 1
            for item in failed:
                current["failed_checks"][str(item)] += 1
        elif current is not None:
            current["recovered"] = True
            current["recovery_generated_at"] = row.get("generated_at")
            incidents.append(current)
            current = None
    if current is not None:
        incidents.append(current)

    normalized: list[dict[str, Any]] = []
    for incident in incidents:
        normalized.append(
            {
                "start_generated_at": incident.get("start_generated_at"),
                "start_data_generated_at": incident.get("start_data_generated_at"),
                "end_generated_at": incident.get("end_generated_at"),
                "recovery_generated_at": incident.get("recovery_generated_at"),
                "observations": incident.get("observations"),
                "recovered": incident.get("recovered") is True,
                "decisions": dict(incident.get("decisions") or {}),
                "failed_checks": dict(incident.get("failed_checks") or {}),
            }
        )
    return normalized


def build_replay_report(
    rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    lookback_hours: float = 6.0,
    max_incidents_allowed: int = 0,
    max_transitions_allowed: int = 2,
) -> dict[str, Any]:
    current = now or now_utc()
    cutoff = current - timedelta(hours=max(0.0, lookback_hours))
    filtered: list[dict[str, Any]] = []
    invalid_timestamps = 0
    for row in rows:
        generated = parse_utc(row.get("generated_at"))
        if generated is None:
            invalid_timestamps += 1
            continue
        if generated >= cutoff:
            filtered.append(row)
    filtered.sort(key=lambda item: parse_utc(item.get("generated_at")) or datetime.min.replace(tzinfo=timezone.utc))

    decisions = Counter(str(row.get("decision") or "missing") for row in filtered)
    latest = filtered[-1] if filtered else {}
    superseded_checks = set()
    if latest.get("legacy_gap_recent_coverage_verified") is True:
        values = latest.get("superseded_legacy_failure_checks")
        if isinstance(values, list):
            superseded_checks = {str(item) for item in values}
    raw_degraded_rows = [row for row in filtered if is_degraded(row)]
    degraded_rows = [row for row in filtered if is_effectively_degraded(row, superseded_checks)]
    incidents = build_incidents(filtered, superseded_checks)
    latest_degraded = is_effectively_degraded(latest, superseded_checks)
    state_sequence = ["degraded" if is_effectively_degraded(row, superseded_checks) else "ok" for row in filtered]
    transitions = sum(1 for prev, cur in zip(state_sequence, state_sequence[1:]) if prev != cur)
    degraded_timestamps = [parsed for parsed in (parse_utc(row.get("generated_at")) for row in degraded_rows) if parsed is not None]
    latest_degraded_at = max(degraded_timestamps) if degraded_timestamps else None
    cooldown_until = latest_degraded_at + timedelta(hours=max(0.0, lookback_hours)) if latest_degraded_at else None
    cooldown_remaining_minutes = None
    if cooldown_until is not None:
        cooldown_remaining_minutes = max(0.0, (cooldown_until - current).total_seconds() / 60.0)

    inserted_trades_values = numeric_values(filtered, "inserted_trades")
    inserted_books_values = numeric_values(filtered, "inserted_books")
    trade_coverage_values = numeric_values(filtered, "trade_coverage_pct")
    book_coverage_values = numeric_values(filtered, "book_coverage_pct")
    archive_trade_delta_values = numeric_values(filtered, "archive_trades_delta")
    archive_book_delta_values = numeric_values(filtered, "archive_books_delta")

    failed_counter: Counter[str] = Counter()
    for row in degraded_rows:
        failed = row.get("failed_checks") if isinstance(row.get("failed_checks"), list) else []
        for item in failed:
            failed_counter[str(item)] += 1

    if not filtered:
        decision = "collector_sla_replay_missing_history"
        next_action = "wait_for_collector_sla_history_or_run_sla_guard"
        stability_blocker = "missing_history"
    elif latest_degraded:
        decision = "collector_sla_replay_currently_degraded"
        next_action = "repair_current_collector_sla_failure"
        stability_blocker = "open_degradation_requires_recovery"
    elif len(incidents) > max_incidents_allowed and transitions > max_transitions_allowed:
        decision = "collector_sla_replay_flapping"
        next_action = "inspect_repeated_sla_transitions_before_trusting_snapshot"
        stability_blocker = "flapping_cooldown"
    elif len(incidents) > max_incidents_allowed:
        decision = "collector_sla_replay_recent_degradation"
        next_action = "review_recent_sla_incident_before_research_snapshot"
        stability_blocker = "recent_degradation_cooldown"
    else:
        decision = "collector_sla_replay_stable"
        next_action = "continue_microstructure_collection"
        stability_blocker = "none"

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "lookback_hours": lookback_hours,
        "observations": len(filtered),
        "raw_rows_scanned": len(rows),
        "invalid_timestamp_rows": invalid_timestamps,
        "first_generated_at": filtered[0].get("generated_at") if filtered else None,
        "last_generated_at": filtered[-1].get("generated_at") if filtered else None,
        "latest_decision": latest.get("decision") if filtered else None,
        "latest_degraded": latest_degraded if filtered else None,
        "latest_degraded_generated_at": latest_degraded_at.isoformat(timespec="seconds") if latest_degraded_at else None,
        "stability_blocker": stability_blocker,
        "stability_cooldown_until_utc": cooldown_until.isoformat(timespec="seconds") if cooldown_until else None,
        "stability_cooldown_remaining_minutes": round_or_none(cooldown_remaining_minutes),
        "decisions": dict(decisions),
        "degraded_observations": len(degraded_rows),
        "raw_degraded_observations": len(raw_degraded_rows),
        "superseded_degraded_observations": len(raw_degraded_rows) - len(degraded_rows),
        "superseded_failure_checks": sorted(superseded_checks),
        "incident_count": len(incidents),
        "open_incident": bool(incidents and incidents[-1].get("recovered") is False),
        "state_transitions": transitions,
        "incidents": incidents[-20:],
        "failed_checks": dict(failed_counter),
        "avg_inserted_trades": round_or_none(mean(inserted_trades_values) if inserted_trades_values else None),
        "min_inserted_trades": round_or_none(min(inserted_trades_values) if inserted_trades_values else None),
        "avg_inserted_books": round_or_none(mean(inserted_books_values) if inserted_books_values else None),
        "min_inserted_books": round_or_none(min(inserted_books_values) if inserted_books_values else None),
        "min_trade_coverage_pct": round_or_none(min(trade_coverage_values) if trade_coverage_values else None),
        "min_book_coverage_pct": round_or_none(min(book_coverage_values) if book_coverage_values else None),
        "avg_archive_trades_delta": round_or_none(mean(archive_trade_delta_values) if archive_trade_delta_values else None),
        "min_archive_trades_delta": round_or_none(min(archive_trade_delta_values) if archive_trade_delta_values else None),
        "avg_archive_books_delta": round_or_none(mean(archive_book_delta_values) if archive_book_delta_values else None),
        "min_archive_books_delta": round_or_none(min(archive_book_delta_values) if archive_book_delta_values else None),
        "next_action": next_action,
        "runtime_boundary": {
            "replay_log_only": True,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Cross-Venue Microstructure Collector SLA Replay",
            "",
            f"- Generated: `{report['generated_at']}`.",
            f"- Decision: `{report['decision']}`.",
            f"- Lookback hours: `{report.get('lookback_hours')}`.",
            f"- Observations: `{report.get('observations')}`.",
            f"- Latest decision: `{report.get('latest_decision')}`.",
            f"- Degraded observations: `{report.get('degraded_observations')}`.",
            f"- Superseded legacy false positives: `{report.get('superseded_degraded_observations')}`.",
            f"- Incidents: `{report.get('incident_count')}`; open `{report.get('open_incident')}`.",
            f"- State transitions: `{report.get('state_transitions')}`.",
            f"- Stability blocker: `{report.get('stability_blocker')}`.",
            f"- Stability cooldown until UTC: `{report.get('stability_cooldown_until_utc')}`.",
            f"- Stability cooldown remaining minutes: `{report.get('stability_cooldown_remaining_minutes')}`.",
            f"- Min coverage trade/book: `{report.get('min_trade_coverage_pct')}` / `{report.get('min_book_coverage_pct')}`.",
            f"- Avg inserts trades/books: `{report.get('avg_inserted_trades')}` / `{report.get('avg_inserted_books')}`.",
            f"- Failed checks: `{json.dumps(report.get('failed_checks') or {}, ensure_ascii=False)}`.",
            f"- Next action: `{report.get('next_action')}`.",
            "- Replay log only; no signals and no orders.",
            "- `can_trade=false`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay and summarize collector SLA history for recent instability")
    parser.add_argument("--history", default="logs/cross_venue_microstructure/collector_sla_guard_history.jsonl")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_COLLECTOR_SLA_REPLAY_2026-06-25")
    parser.add_argument("--lookback-hours", type=float, default=6.0)
    parser.add_argument("--max-lines", type=int, default=10000)
    parser.add_argument("--max-incidents-allowed", type=int, default=0)
    parser.add_argument("--max-transitions-allowed", type=int, default=2)
    args = parser.parse_args()

    report = build_replay_report(
        read_jsonl(resolve_path(args.history), max_lines=max(1, args.max_lines)),
        lookback_hours=max(0.0, args.lookback_hours),
        max_incidents_allowed=max(0, args.max_incidents_allowed),
        max_transitions_allowed=max(0, args.max_transitions_allowed),
    )
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "observations": report["observations"],
                "incidents": report["incident_count"],
                "transitions": report["state_transitions"],
                "cooldown_remaining_minutes": report["stability_cooldown_remaining_minutes"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["decision"] in {"collector_sla_replay_stable", "collector_sla_replay_missing_history"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

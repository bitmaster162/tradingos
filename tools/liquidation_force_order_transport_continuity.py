#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LIVENESS_STATUSES = {"transport_liveness_ok"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso(value: datetime | None = None) -> str:
    return (value or now_utc()).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_ledger(path: Path) -> tuple[list[tuple[datetime, int, dict[str, Any]]], dict[str, int]]:
    counters = {"lines": 0, "invalid_lines": 0, "duplicate_ids": 0, "out_of_order": 0}
    if not path.is_file():
        return [], counters
    rows: list[tuple[datetime, int, dict[str, Any]]] = []
    identities: set[str] = set()
    previous_key: tuple[datetime, int] | None = None
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            counters["lines"] += 1
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                counters["invalid_lines"] += 1
                continue
            if not isinstance(row, dict):
                counters["invalid_lines"] += 1
                continue
            timestamp = parse_ts(row.get("ts"))
            try:
                recorded_at_ns = int(row.get("recorded_at_ns") or 0)
            except (TypeError, ValueError):
                recorded_at_ns = 0
            identity = str(row.get("heartbeat_id") or "")
            if timestamp is None or recorded_at_ns <= 0 or not identity:
                counters["invalid_lines"] += 1
                continue
            if identity in identities:
                counters["duplicate_ids"] += 1
            identities.add(identity)
            key = (timestamp, recorded_at_ns)
            if previous_key is not None and key <= previous_key:
                counters["out_of_order"] += 1
            previous_key = key
            rows.append((timestamp, recorded_at_ns, row))
    return rows, counters


def is_liveness_proof(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "")
    if status == "transport_liveness_ok":
        return int(row.get("liveness_messages_seen") or 0) > 0
    return False


def build_report(
    ledger_path: Path,
    *,
    as_of: datetime | None = None,
    lookback_hours: float = 48.0,
    minimum_observation_hours: float = 24.0,
    maximum_gap_seconds: float = 180.0,
    maximum_freshness_seconds: float = 180.0,
) -> dict[str, Any]:
    observed_at = (as_of or now_utc()).astimezone(timezone.utc)
    rows, integrity = read_ledger(ledger_path)
    cutoff = observed_at - timedelta(hours=lookback_hours)
    window = [item for item in rows if cutoff <= item[0] <= observed_at]
    evidence = [item for item in window if is_liveness_proof(item[2])]
    gaps: list[dict[str, Any]] = []
    for previous, current in zip(evidence, evidence[1:]):
        gap_seconds = (current[0] - previous[0]).total_seconds()
        if gap_seconds > maximum_gap_seconds:
            gaps.append(
                {
                    "from": now_iso(previous[0]),
                    "to": now_iso(current[0]),
                    "seconds": round(gap_seconds, 3),
                }
            )
    first_ts = evidence[0][0] if evidence else None
    last_ts = evidence[-1][0] if evidence else None
    observation_hours = (last_ts - first_ts).total_seconds() / 3600.0 if first_ts and last_ts else 0.0
    freshness_seconds = max(0.0, (observed_at - last_ts).total_seconds()) if last_ts else None
    parse_error_evidence = [item for item in window if item[2].get("status") == "parse_error"]
    invalid_liveness_evidence = [item for item in window if item[2].get("status") == "transport_liveness_invalid"]
    parse_error_rows = len(parse_error_evidence)
    invalid_liveness_rows = len(invalid_liveness_evidence)
    transition_rows = sum(1 for _, _, row in window if row.get("status") == "transport_liveness_transition")
    blockers: list[str] = []
    if integrity["invalid_lines"] or integrity["duplicate_ids"] or integrity["out_of_order"]:
        blockers.append("ledger_integrity")
        decision = "force_order_transport_continuity_integrity_blocked"
    elif not evidence:
        blockers.append("waiting_first_liveness_proof")
        decision = "force_order_transport_continuity_collecting_baseline"
    elif invalid_liveness_rows:
        blockers.append("invalid_liveness_proofs_observed")
        decision = "force_order_transport_continuity_degraded_invalid_proofs"
    elif parse_error_rows:
        blockers.append("parse_errors_observed")
        decision = "force_order_transport_continuity_degraded_parse_errors"
    elif freshness_seconds is None or freshness_seconds > maximum_freshness_seconds:
        blockers.append("latest_liveness_stale")
        decision = "force_order_transport_continuity_degraded_stale"
    elif gaps:
        blockers.append("liveness_gaps_over_threshold")
        decision = "force_order_transport_continuity_degraded_gaps"
    elif observation_hours < minimum_observation_hours:
        blockers.append("minimum_observation_window")
        decision = "force_order_transport_continuity_collecting_baseline"
    else:
        decision = "force_order_transport_continuity_observed"

    recovery_candidates: list[tuple[datetime, str]] = []
    expiry_margin = timedelta(seconds=1)
    if invalid_liveness_evidence:
        recovery_candidates.append(
            (invalid_liveness_evidence[-1][0] + timedelta(hours=lookback_hours) + expiry_margin, "invalid_proof_expires")
        )
    if parse_error_evidence:
        recovery_candidates.append(
            (parse_error_evidence[-1][0] + timedelta(hours=lookback_hours) + expiry_margin, "parse_error_expires")
        )
    for gap in gaps:
        gap_start = parse_ts(gap.get("from"))
        if gap_start is not None:
            recovery_candidates.append(
                (gap_start + timedelta(hours=lookback_hours) + expiry_margin, "gap_start_expires")
            )
    continuous_segment_start = parse_ts(gaps[-1].get("to")) if gaps else first_ts
    if (continuous_segment_start is not None and observation_hours < minimum_observation_hours) or gaps:
        if continuous_segment_start is not None:
            recovery_candidates.append(
                (
                    continuous_segment_start + timedelta(hours=minimum_observation_hours),
                    "minimum_clean_observation_window",
                )
            )

    recovery_status = "not_required"
    earliest_recheck: datetime | None = None
    if decision == "force_order_transport_continuity_integrity_blocked":
        recovery_status = "manual_integrity_review_required"
    elif not evidence:
        recovery_status = "waiting_first_liveness_proof"
    elif freshness_seconds is None or freshness_seconds > maximum_freshness_seconds:
        recovery_status = "requires_fresh_liveness_then_recalculate"
    elif decision != "force_order_transport_continuity_observed":
        recovery_status = "rolling_window_recovery_estimate"
        if recovery_candidates:
            earliest_recheck = max(item[0] for item in recovery_candidates)
    return {
        "schema_version": 1,
        "generated_at": now_iso(observed_at),
        "tool": "tools/liquidation_force_order_transport_continuity.py",
        "decision": decision,
        "continuity_observed": decision == "force_order_transport_continuity_observed",
        "inputs": {
            "ledger": portable(ledger_path),
            "lookback_hours": lookback_hours,
            "minimum_observation_hours": minimum_observation_hours,
            "maximum_gap_seconds": maximum_gap_seconds,
            "maximum_freshness_seconds": maximum_freshness_seconds,
        },
        "sample": {
            "ledger_rows": len(rows),
            "window_rows": len(window),
            "liveness_proofs": len(evidence),
            "first_liveness_at": now_iso(first_ts) if first_ts else None,
            "last_liveness_at": now_iso(last_ts) if last_ts else None,
            "observation_hours": round(observation_hours, 6),
            "freshness_seconds": round(freshness_seconds, 3) if freshness_seconds is not None else None,
            "collector_pids": sorted({int(row.get("collector_pid") or 0) for _, _, row in window if row.get("collector_pid")}),
            "parse_error_rows": parse_error_rows,
            "invalid_liveness_rows": invalid_liveness_rows,
            "transition_rows": transition_rows,
        },
        "integrity": integrity,
        "gaps_over_threshold": gaps,
        "blockers": blockers,
        "recovery": {
            "status": recovery_status,
            "earliest_recheck_at_utc": now_iso(earliest_recheck) if earliest_recheck else None,
            "estimate_basis": sorted({item[1] for item in recovery_candidates}),
            "assumes_no_new_gap_or_invalid_proof": True,
            "estimate_is_not_gate_pass": True,
        },
        "boundary": {
            "audit_only": True,
            "changes_preregistered_rules": False,
            "runs_event_study": False,
            "automatic_promotion": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    sample = report["sample"]
    return "\n".join(
        [
            "# ForceOrder Transport Continuity",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            "- Can trade: `false`",
            f"- Liveness proofs: `{sample['liveness_proofs']}`",
            f"- Observation hours: `{sample['observation_hours']}`",
            f"- Freshness seconds: `{sample['freshness_seconds']}`",
            f"- Gaps over threshold: `{len(report['gaps_over_threshold'])}`",
            f"- Blockers: `{report['blockers']}`",
            f"- Earliest bounded recheck: `{report['recovery']['earliest_recheck_at_utc']}`",
            "",
            "This is an operational, outcome-blind audit. It does not change the preregistered strategy or permit trading.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Outcome-blind continuity audit for Binance forceOrder transport")
    parser.add_argument(
        "--ledger",
        default="logs/liquidation_force_order/liquidation_force_order_collector_heartbeat.jsonl",
    )
    parser.add_argument("--lookback-hours", type=float, default=48.0)
    parser.add_argument("--minimum-observation-hours", type=float, default=24.0)
    parser.add_argument("--maximum-gap-seconds", type=float, default=180.0)
    parser.add_argument("--maximum-freshness-seconds", type=float, default=180.0)
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_FORCE_ORDER_TRANSPORT_CONTINUITY_2026-07-15")
    args = parser.parse_args()
    report = build_report(
        resolve_path(args.ledger),
        lookback_hours=args.lookback_hours,
        minimum_observation_hours=args.minimum_observation_hours,
        maximum_gap_seconds=args.maximum_gap_seconds,
        maximum_freshness_seconds=args.maximum_freshness_seconds,
    )
    out = resolve_path(args.out_prefix)
    write_json(out.with_suffix(".json"), report)
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "liveness_proofs": report["sample"]["liveness_proofs"],
                "observation_hours": report["sample"]["observation_hours"],
                "gaps": len(report["gaps_over_threshold"]),
                "can_trade": False,
            },
            indent=2,
        )
    )
    return 2 if "degraded" in report["decision"] or "blocked" in report["decision"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

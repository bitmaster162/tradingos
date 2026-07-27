#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRIMARY_CONTRACT = ROOT / "configs" / "CEX_DEX_FUNDING_LEAD_LAG_PREREG_2026-07-13.json"
DEFAULT_DIRECT_CONTRACT = ROOT / "configs" / "CEX_FUNDING_DIRECT_REPLICATION_PREREG_2026-07-13.json"
DEFAULT_ALIGNMENT_LOCK = ROOT / "configs" / "CEX_FUNDING_SOURCE_ALIGNMENT_LOCK_V3_2026-07-14.json"
DEFAULT_PRIMARY_REPORT = ROOT / "docs" / "CEX_DEX_FUNDING_LEAD_LAG_DATA_QUALITY_2026-07-13.json"
DEFAULT_DIRECT_REPORT = ROOT / "docs" / "CEX_FUNDING_DIRECT_REPLICATION_DATA_QUALITY_2026-07-13.json"
DEFAULT_ALIGNMENT_REPORT = ROOT / "docs" / "CEX_FUNDING_SOURCE_ALIGNMENT_V3_2026-07-14.json"
DEFAULT_FRESHNESS_REPORT = ROOT / "docs" / "CEX_FUNDING_FRESHNESS_WATCHDOG_2026-07-13.json"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "CEX_FUNDING_RESEARCH_READINESS_2026-07-13"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: str | Path) -> dict[str, Any]:
    target = resolve_path(path)
    if not target.is_file():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def as_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def iso_z(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z") if value else None


def gate_progress(sample: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
    required_span_days = as_float(gate.get("minimum_forward_span_days"))
    required_snapshots = as_int(gate.get("minimum_unique_minute_snapshots"))
    required_days = as_int(gate.get("minimum_independent_utc_days"))
    required_coverage = as_float(gate.get("minimum_required_point_coverage"))

    span_minutes = as_float(sample.get("span_minutes"))
    span_days = span_minutes / 1440.0
    snapshots = as_int(sample.get("unique_minute_buckets"))
    independent_days = as_int(sample.get("independent_utc_days"))
    coverage = as_float(sample.get("required_point_coverage"))
    checks = {
        "minimum_forward_span_days": span_days >= required_span_days,
        "minimum_unique_minute_snapshots": snapshots >= required_snapshots,
        "minimum_independent_utc_days": independent_days >= required_days,
        "minimum_required_point_coverage": coverage >= required_coverage,
    }

    first_bucket = parse_dt(sample.get("first_minute_bucket"))
    last_bucket = parse_dt(sample.get("last_minute_bucket"))
    candidates: list[datetime] = []
    if first_bucket and required_span_days > 0:
        candidates.append(first_bucket + timedelta(days=required_span_days))
    remaining_snapshots = max(0, required_snapshots - snapshots)
    if last_bucket and remaining_snapshots:
        candidates.append(last_bucket + timedelta(minutes=remaining_snapshots))
    if first_bucket and required_days > 0:
        first_day = datetime(first_bucket.year, first_bucket.month, first_bucket.day, tzinfo=timezone.utc)
        candidates.append(first_day + timedelta(days=max(0, required_days - 1)))

    return {
        "checks": checks,
        "ready": all(checks.values()),
        "current": {
            "span_days": round(span_days, 8),
            "unique_minute_snapshots": snapshots,
            "independent_utc_days": independent_days,
            "required_point_coverage": coverage,
        },
        "required": {
            "span_days": required_span_days,
            "unique_minute_snapshots": required_snapshots,
            "independent_utc_days": required_days,
            "required_point_coverage": required_coverage,
        },
        "remaining": {
            "span_days": round(max(0.0, required_span_days - span_days), 8),
            "unique_minute_snapshots": remaining_snapshots,
            "independent_utc_days": max(0, required_days - independent_days),
        },
        "theoretical_earliest_utc": iso_z(max(candidates)) if candidates else None,
        "eta_assumption": "No future collection gaps and required point coverage remains above the locked threshold.",
    }


def contract_failures(
    primary_contract: dict[str, Any],
    direct_contract: dict[str, Any],
    alignment_lock: dict[str, Any],
    primary_report: dict[str, Any],
    direct_report: dict[str, Any],
    alignment_report: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    primary_gate = primary_contract.get("future_research_lock") or {}
    direct_gate = direct_contract.get("replication_gate") or {}
    if primary_contract.get("status") != "fixed_forward_data_collection_contract":
        failures.append("primary_contract_status")
    if direct_contract.get("status") != "fixed_forward_data_collection_contract":
        failures.append("direct_contract_status")
    if alignment_lock.get("status") != "fixed_source_alignment_contract":
        failures.append("alignment_lock_status")
    if primary_gate.get("observer_creation_allowed_before_gate") is not False:
        failures.append("observer_creation_boundary")
    if primary_gate.get("parameter_search_allowed") is not False or direct_gate.get("parameter_search_allowed") is not False:
        failures.append("parameter_search_boundary")
    if direct_gate.get("paper_review_allowed") is not False:
        failures.append("paper_review_boundary")
    if direct_gate.get("primary_lock_id") != primary_contract.get("lock_id"):
        failures.append("direct_primary_lock_reference")
    if primary_report.get("lock_id") != primary_contract.get("lock_id"):
        failures.append("primary_report_lock_id")
    if direct_report.get("lock_id") != direct_contract.get("lock_id"):
        failures.append("direct_report_lock_id")
    if alignment_report.get("lock_id") != alignment_lock.get("lock_id"):
        failures.append("alignment_report_lock_id")
    for name, payload in (
        ("primary_contract", primary_contract),
        ("direct_contract", direct_contract),
        ("alignment_lock", alignment_lock),
    ):
        boundary = payload.get("runtime_boundary") if isinstance(payload.get("runtime_boundary"), dict) else {}
        if payload.get("can_trade") is not False or boundary.get("orders_allowed") is not False:
            failures.append(f"{name}_unsafe_boundary")
    return failures


def build_report(
    primary_contract: dict[str, Any],
    direct_contract: dict[str, Any],
    alignment_lock: dict[str, Any],
    primary_report: dict[str, Any],
    direct_report: dict[str, Any],
    alignment_report: dict[str, Any],
    freshness_report: dict[str, Any],
) -> dict[str, Any]:
    inputs = {
        "primary_contract": primary_contract,
        "direct_contract": direct_contract,
        "alignment_lock": alignment_lock,
        "primary_report": primary_report,
        "direct_report": direct_report,
        "alignment_report": alignment_report,
        "freshness_report": freshness_report,
    }
    missing = [name for name, payload in inputs.items() if not payload]
    failures = [] if missing else contract_failures(
        primary_contract,
        direct_contract,
        alignment_lock,
        primary_report,
        direct_report,
        alignment_report,
    )
    primary_gate = primary_contract.get("future_research_lock") if isinstance(primary_contract.get("future_research_lock"), dict) else {}
    direct_gate = direct_contract.get("replication_gate") if isinstance(direct_contract.get("replication_gate"), dict) else {}
    primary_sample = primary_report.get("sample") if isinstance(primary_report.get("sample"), dict) else {}
    direct_sample = direct_report.get("sample") if isinstance(direct_report.get("sample"), dict) else {}
    primary_progress = gate_progress(primary_sample, primary_gate)
    direct_progress = gate_progress(direct_sample, direct_gate)

    primary_quality = primary_report.get("snapshot_quality") if isinstance(primary_report.get("snapshot_quality"), dict) else {}
    direct_quality = direct_report.get("snapshot_quality") if isinstance(direct_report.get("snapshot_quality"), dict) else {}
    operational_blockers: list[str] = []
    if primary_quality.get("quality_pass") is not True or as_int(primary_sample.get("bad_lines")) > 0:
        operational_blockers.append("primary_data_quality")
    if direct_quality.get("quality_pass") is not True or as_int(direct_sample.get("bad_lines")) > 0:
        operational_blockers.append("direct_data_quality")
    if freshness_report.get("healthy") is not True:
        operational_blockers.append("freshness_watchdog")

    alignment_terminal = bool((alignment_report.get("terminal") or {}).get("reached"))
    alignment_ready = (
        alignment_report.get("decision") == "cex_funding_source_alignment_ready_for_manual_semantic_review"
        and not alignment_report.get("blockers")
        and alignment_report.get("edge_evaluated") is False
    )
    primary_ready = bool(primary_progress["ready"] and not operational_blockers and not missing and not failures)
    direct_ready = bool(direct_progress["ready"] and not operational_blockers and not missing and not failures)
    observer_creation_review_allowed = primary_ready and not alignment_terminal
    full_replication_stack_ready = observer_creation_review_allowed and direct_ready and alignment_ready

    if missing or failures:
        decision = "cex_funding_research_readiness_blocked_contract"
        next_action = "Repair missing or mismatched contracts/reports; do not create an observer."
    elif alignment_terminal:
        decision = "cex_funding_research_readiness_blocked_alignment_terminal"
        next_action = "Preserve the failed alignment lock and use only a reviewed parameter-identical future-floor successor."
    elif operational_blockers:
        decision = "cex_funding_research_readiness_blocked_operational_quality"
        next_action = "Restore healthy collection without changing research thresholds."
    elif not primary_ready:
        decision = "cex_funding_research_readiness_waiting_forward_gate"
        next_action = "Continue fixed forward collection; observer creation remains prohibited."
    elif not full_replication_stack_ready:
        decision = "cex_funding_primary_gate_ready_waiting_replication_review"
        next_action = "Manual observer-creation review is allowed, but paper review remains prohibited until direct replication and semantic alignment pass."
    else:
        decision = "cex_funding_research_stack_ready_for_manual_observer_creation_review"
        next_action = "Open a separate manual observer design review; do not infer edge or authorize paper trading."

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "tool": "tools/cex_funding_research_readiness_monitor.py",
        "decision": decision,
        "locks": {
            "primary": primary_contract.get("lock_id"),
            "direct": direct_contract.get("lock_id"),
            "alignment": alignment_lock.get("lock_id"),
            "alignment_forward_floor_utc": alignment_lock.get("forward_start_at") or alignment_lock.get("sealed_at"),
        },
        "primary_progress": primary_progress,
        "direct_progress": direct_progress,
        "alignment": {
            "decision": alignment_report.get("decision"),
            "ready_for_manual_semantic_review": alignment_ready,
            "terminal": alignment_terminal,
            "blockers": alignment_report.get("blockers") or [],
        },
        "freshness": {
            "decision": freshness_report.get("decision"),
            "healthy": freshness_report.get("healthy") is True,
            "blockers": freshness_report.get("blockers") or [],
        },
        "stages": {
            "primary_observer_creation_gate_ready": primary_ready,
            "direct_replication_gate_ready": direct_ready,
            "source_alignment_manual_review_ready": alignment_ready,
            "observer_creation_review_allowed": observer_creation_review_allowed,
            "full_replication_stack_ready": full_replication_stack_ready,
            "paper_review_allowed": False,
            "edge_evaluated": False,
        },
        "missing_inputs": missing,
        "contract_failures": failures,
        "operational_blockers": operational_blockers,
        "next_action": next_action,
        "runtime_boundary": {
            "readiness_monitor_only": True,
            "price_outcomes_read": False,
            "edge_evaluator": False,
            "observer_created": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    primary = report.get("primary_progress") or {}
    direct = report.get("direct_progress") or {}
    stages = report.get("stages") or {}
    return "\n".join([
        "# CEX Funding Research Readiness",
        "",
        f"- Generated: `{report.get('generated_at')}`.",
        f"- Decision: `{report.get('decision')}`.",
        f"- Primary progress: `{primary.get('current')}` / `{primary.get('required')}`.",
        f"- Primary theoretical earliest: `{primary.get('theoretical_earliest_utc')}`.",
        f"- Direct progress: `{direct.get('current')}` / `{direct.get('required')}`.",
        f"- Direct theoretical earliest: `{direct.get('theoretical_earliest_utc')}`.",
        f"- Alignment: `{report.get('alignment')}`.",
        f"- Freshness: `{report.get('freshness')}`.",
        f"- Observer-creation review allowed: `{stages.get('observer_creation_review_allowed')}`.",
        f"- Full replication stack ready: `{stages.get('full_replication_stack_ready')}`.",
        f"- Next action: {report.get('next_action')}",
        "",
        "## Boundary",
        "",
        "- Readiness only; no lag evaluation, price outcomes or parameter search.",
        "- No observer is created automatically.",
        "- Paper review, signals and orders remain disabled.",
        "- `can_trade=false`.",
        "",
    ])


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    report = build_report(
        read_json(args.primary_contract),
        read_json(args.direct_contract),
        read_json(args.alignment_lock),
        read_json(args.primary_report),
        read_json(args.direct_report),
        read_json(args.alignment_report),
        read_json(args.freshness_report),
    )
    prefix = resolve_path(args.out_prefix)
    write_json(prefix.with_suffix(".json"), report)
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    code = 2 if report["decision"] == "cex_funding_research_readiness_blocked_contract" else 0
    return code, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Outcome-blind readiness monitor for the locked CEX funding research stack")
    parser.add_argument("--primary-contract", default=str(DEFAULT_PRIMARY_CONTRACT))
    parser.add_argument("--direct-contract", default=str(DEFAULT_DIRECT_CONTRACT))
    parser.add_argument("--alignment-lock", default=str(DEFAULT_ALIGNMENT_LOCK))
    parser.add_argument("--primary-report", default=str(DEFAULT_PRIMARY_REPORT))
    parser.add_argument("--direct-report", default=str(DEFAULT_DIRECT_REPORT))
    parser.add_argument("--alignment-report", default=str(DEFAULT_ALIGNMENT_REPORT))
    parser.add_argument("--freshness-report", default=str(DEFAULT_FRESHNESS_REPORT))
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    args = parser.parse_args()
    code, report = run(args)
    print(json.dumps({
        "decision": report.get("decision"),
        "primary": (report.get("primary_progress") or {}).get("current"),
        "direct": (report.get("direct_progress") or {}).get("current"),
        "stages": report.get("stages"),
        "can_trade": False,
    }, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

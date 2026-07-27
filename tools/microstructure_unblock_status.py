#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Microstructure Unblock Status",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Can trade: `{report.get('can_trade')}`",
        "",
        "## Blockers",
    ]
    blockers = report.get("blockers") if isinstance(report.get("blockers"), list) else []
    if blockers:
        for item in blockers:
            lines.append(f"- `{item}`")
    else:
        lines.append("- none")

    coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
    lines.extend([
        "",
        "## Coverage",
        f"- Span hours: `{coverage.get('span_hours')}` / required `{coverage.get('required_hours')}`.",
        f"- Trade coverage: `{coverage.get('trade_coverage_pct')}%` / required `{coverage.get('required_trade_coverage_pct')}%`.",
        f"- Book coverage: `{coverage.get('book_coverage_pct')}%` / required `{coverage.get('required_book_coverage_pct')}%`.",
        f"- Book deficit pct: `{coverage.get('book_deficit_pct')}`.",
        f"- Book deficit minute-equivalent over window: `{coverage.get('book_deficit_minute_equivalent')}`.",
        f"- ETA note: {coverage.get('eta_note')}",
    ])

    sla = report.get("sla") if isinstance(report.get("sla"), dict) else {}
    lines.extend([
        "",
        "## SLA Replay",
        f"- Decision: `{sla.get('decision')}`.",
        f"- Open incident: `{sla.get('open_incident')}`.",
        f"- Cooldown until UTC: `{sla.get('cooldown_until_utc')}`.",
        f"- Cooldown remaining minutes: `{sla.get('cooldown_remaining_minutes')}`.",
        f"- Failed checks: `{sla.get('failed_checks')}`.",
    ])

    book_diag = report.get("book_diagnostic") if isinstance(report.get("book_diagnostic"), dict) else {}
    lines.extend([
        "",
        "## Book Coverage Diagnostic",
        f"- Decision: `{book_diag.get('decision')}`.",
        f"- Dual-book coverage: `{book_diag.get('dual_book_coverage_pct')}%`.",
        f"- Missing dual-book minutes: `{book_diag.get('missing_dual_book_minutes')}`.",
        f"- Recent 1h / 6h dual-book coverage: `{book_diag.get('recent_1h_dual_book_pct')}%` / `{book_diag.get('recent_6h_dual_book_pct')}%`.",
        f"- Perfect-future ETA UTC: `{book_diag.get('eta_utc')}`.",
        f"- Diagnostic next action: {book_diag.get('next_action')}",
    ])

    lines.extend([
        "",
        "## Next Action",
        f"- {report.get('next_action')}",
        "",
        "## Runtime Boundary",
        "- This is observability only.",
        "- It does not launch research validation, emit signals, create paper entries, or send orders.",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_step(args: list[str]) -> dict[str, Any]:
    proc = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=120)
    return {
        "cmd": args,
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }


def refresh_reports(prefix_suffix: str) -> list[dict[str, Any]]:
    py = sys.executable
    return [
        run_step([py, "tools/cross_venue_microstructure_health.py", "--out-prefix", f"docs/CROSS_VENUE_MICROSTRUCTURE_HEALTH_{prefix_suffix}"]),
        run_step([py, "tools/cross_venue_microstructure_collector_sla_replay.py", "--out-prefix", f"docs/CROSS_VENUE_MICROSTRUCTURE_COLLECTOR_SLA_REPLAY_{prefix_suffix}"]),
        run_step([py, "tools/cross_venue_microstructure_snapshot_gate.py", "--out-prefix", f"docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_{prefix_suffix}"]),
        run_step([py, "tools/cross_venue_microstructure_snapshot_transition_monitor.py", "--out-prefix", f"docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_TRANSITION_MONITOR_{prefix_suffix}"]),
        run_step([py, "tools/cross_venue_microstructure_book_coverage_diagnostic.py", "--out-prefix", f"docs/CROSS_VENUE_MICROSTRUCTURE_BOOK_COVERAGE_DIAGNOSTIC_{prefix_suffix}"]),
    ]


def latest_matching(pattern: str, *, exclude_contains: tuple[str, ...] = ()) -> Path | None:
    matches = [
        path
        for path in (ROOT / "docs").glob(pattern)
        if not any(token in path.name for token in exclude_contains)
    ]
    matches = sorted(matches, key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def build_report(args: argparse.Namespace, refresh_results: list[dict[str, Any]]) -> dict[str, Any]:
    skip_wrappers = ("TELEGRAM",)
    snapshot_path = Path(args.snapshot_gate) if args.snapshot_gate else latest_matching("CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_*.json", exclude_contains=skip_wrappers)
    sla_path = Path(args.sla_replay) if args.sla_replay else latest_matching("CROSS_VENUE_MICROSTRUCTURE_COLLECTOR_SLA_REPLAY_*.json", exclude_contains=skip_wrappers)
    health_path = Path(args.health) if args.health else latest_matching("CROSS_VENUE_MICROSTRUCTURE_HEALTH_*.json", exclude_contains=skip_wrappers)
    transition_path = Path(args.transition) if args.transition else latest_matching("CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_TRANSITION_MONITOR_*.json", exclude_contains=skip_wrappers)
    book_diag_path = Path(args.book_diagnostic) if args.book_diagnostic else latest_matching("CROSS_VENUE_MICROSTRUCTURE_BOOK_COVERAGE_DIAGNOSTIC_*.json", exclude_contains=skip_wrappers)

    snapshot = read_json(ROOT / snapshot_path if snapshot_path and not snapshot_path.is_absolute() else snapshot_path) if snapshot_path else {}
    sla = read_json(ROOT / sla_path if sla_path and not sla_path.is_absolute() else sla_path) if sla_path else {}
    health = read_json(ROOT / health_path if health_path and not health_path.is_absolute() else health_path) if health_path else {}
    transition = read_json(ROOT / transition_path if transition_path and not transition_path.is_absolute() else transition_path) if transition_path else {}
    book_diag = read_json(ROOT / book_diag_path if book_diag_path and not book_diag_path.is_absolute() else book_diag_path) if book_diag_path else {}

    diag = snapshot.get("readiness_diagnostics") if isinstance(snapshot.get("readiness_diagnostics"), dict) else {}
    failed = list(diag.get("failed_checks") or snapshot.get("summary", {}).get("failed") or [])

    span_hours = float(diag.get("span_hours") or 0.0)
    required_hours = float(diag.get("required_hours") or 0.0)
    trade_cov = float(diag.get("trade_coverage_pct") or 0.0)
    book_cov = float(diag.get("book_coverage_pct") or 0.0)
    req_trade = float(diag.get("required_trade_coverage_pct") or 0.0)
    req_book = float(diag.get("required_book_coverage_pct") or 0.0)
    book_deficit = max(0.0, req_book - book_cov)
    expected_minutes = required_hours * 60.0 if required_hours > 0 else span_hours * 60.0
    minute_equiv = expected_minutes * book_deficit / 100.0 if expected_minutes > 0 else None

    sla_decision = sla.get("decision")
    open_incident = bool(sla.get("open_incident"))
    cooldown_remaining = sla.get("stability_cooldown_remaining_minutes")
    snapshot_id = snapshot.get("snapshot_id") or transition.get("snapshot_id")

    blockers: list[str] = []
    blockers.extend(str(item) for item in failed)
    if sla_decision and sla_decision != "collector_sla_replay_stable":
        blockers.append(str(sla_decision))
    if transition.get("transition_state") and transition.get("transition_state") != "ready_to_launch_post_snapshot_flow":
        blockers.append(str(transition.get("transition_state")))
    book_diag_decision = str(book_diag.get("decision") or "")
    if book_diag_decision in {
        "microstructure_book_coverage_partial_recent_recovery",
        "microstructure_book_coverage_current_polling_degraded",
    }:
        blockers.append(book_diag_decision)
    blockers = sorted(set(blockers))

    if snapshot_id:
        decision = "microstructure_snapshot_available"
        next_action = "Run post-seal research guards only through existing fail-closed tooling."
    elif book_cov < req_book and open_incident:
        decision = "microstructure_wait_for_book_coverage_and_sla_recovery"
        next_action = "Keep collector running; do not lower coverage thresholds. Recheck after SLA cooldown and after old missing-book minutes roll out of the 168h window."
    elif open_incident:
        decision = "microstructure_wait_for_sla_recovery"
        next_action = "Keep collector running and wait for SLA replay to observe recovery before sealing."
    elif book_cov < req_book:
        decision = "microstructure_wait_for_book_coverage"
        next_action = "Keep collector running until dual-book coverage reaches the locked 95% threshold."
    elif failed:
        decision = "microstructure_unblock_requires_gate_investigation"
        next_action = "Inspect failed gates; do not seal or run validation until every locked gate is true."
    else:
        decision = "microstructure_ready_for_snapshot_gate_rerun"
        next_action = "Rerun snapshot gate; if it seals, proceed only with preregistered post-seal research."

    return {
        "generated_at": now_iso(),
        "decision": decision,
        "snapshot_id": snapshot_id,
        "blockers": blockers,
        "coverage": {
            "span_hours": round(span_hours, 6),
            "required_hours": required_hours,
            "trade_coverage_pct": round(trade_cov, 6),
            "required_trade_coverage_pct": req_trade,
            "book_coverage_pct": round(book_cov, 6),
            "required_book_coverage_pct": req_book,
            "book_deficit_pct": round(book_deficit, 6),
            "book_deficit_minute_equivalent": round(minute_equiv, 3) if minute_equiv is not None else None,
            "eta_note": "Minute-equivalent is not a promise; recovery depends on where missing book minutes sit in the rolling window.",
        },
        "sla": {
            "decision": sla_decision,
            "open_incident": open_incident,
            "cooldown_until_utc": sla.get("stability_cooldown_until_utc"),
            "cooldown_remaining_minutes": sla.get("stability_cooldown_remaining_minutes"),
            "failed_checks": sla.get("failed_checks"),
        },
        "book_diagnostic": {
            "decision": book_diag.get("decision"),
            "dual_book_coverage_pct": (book_diag.get("coverage") or {}).get("dual_book_coverage_pct") if isinstance(book_diag.get("coverage"), dict) else None,
            "missing_dual_book_minutes": (book_diag.get("coverage") or {}).get("missing_dual_book_minutes") if isinstance(book_diag.get("coverage"), dict) else None,
            "recent_1h_dual_book_pct": ((book_diag.get("recent_windows") or {}).get("1h") or {}).get("dual_book_coverage_pct") if isinstance(book_diag.get("recent_windows"), dict) else None,
            "recent_6h_dual_book_pct": ((book_diag.get("recent_windows") or {}).get("6h") or {}).get("dual_book_coverage_pct") if isinstance(book_diag.get("recent_windows"), dict) else None,
            "eta_utc": (book_diag.get("eta") or {}).get("eta_utc") if isinstance(book_diag.get("eta"), dict) else None,
            "minutes_from_latest": (book_diag.get("eta") or {}).get("minutes_from_latest") if isinstance(book_diag.get("eta"), dict) else None,
            "recovery": book_diag.get("recovery") if isinstance(book_diag.get("recovery"), dict) else {},
            "next_action": book_diag.get("next_action"),
        },
        "health": {
            "classification": health.get("classification"),
            "failed_hard_gates": health.get("failed_hard_gates"),
        },
        "transition": {
            "state": transition.get("transition_state"),
            "snapshot_id": transition.get("snapshot_id"),
        },
        "sources": {
            "snapshot_gate": str(snapshot_path) if snapshot_path else None,
            "sla_replay": str(sla_path) if sla_path else None,
            "health": str(health_path) if health_path else None,
            "transition": str(transition_path) if transition_path else None,
            "book_diagnostic": str(book_diag_path) if book_diag_path else None,
        },
        "refresh_results": refresh_results,
        "next_action": next_action,
        "runtime_boundary": {
            "observability_only": True,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="One-page blocker report for microstructure snapshot unblocking.")
    parser.add_argument("--refresh", action="store_true", help="Run safe health/SLA/snapshot/transition reports before summarizing.")
    parser.add_argument("--snapshot-gate")
    parser.add_argument("--sla-replay")
    parser.add_argument("--health")
    parser.add_argument("--transition")
    parser.add_argument("--book-diagnostic")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_UNBLOCK_STATUS_2026-07-01")
    args = parser.parse_args()

    refresh_results: list[dict[str, Any]] = []
    if args.refresh:
        refresh_suffix = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}_UNBLOCK_STATUS_REFRESH"
        refresh_results = refresh_reports(refresh_suffix)

    report = build_report(args, refresh_results)
    out_prefix = ROOT / args.out_prefix
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    write_json(json_path, report)
    write_md(md_path, report)
    print(json.dumps({
        "decision": report["decision"],
        "blockers": report["blockers"],
        "book_coverage_pct": report["coverage"]["book_coverage_pct"],
        "book_deficit_pct": report["coverage"]["book_deficit_pct"],
        "book_diagnostic": report["book_diagnostic"]["decision"],
        "cooldown_remaining_minutes": report["sla"]["cooldown_remaining_minutes"],
        "out": str(json_path.relative_to(ROOT)),
        "can_trade": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

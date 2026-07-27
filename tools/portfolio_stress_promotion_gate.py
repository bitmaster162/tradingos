#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc)}
    return payload if isinstance(payload, dict) else {"_read_error": "json_root_not_object"}


def sha256_file(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def bound_path(value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return resolve_path(value)


def build_report(stress_report_path: Path, max_snapshot_age_hours: float = 24.0) -> dict[str, Any]:
    stress = read_json(stress_report_path)
    inputs = stress.get("inputs") if isinstance(stress.get("inputs"), dict) else {}
    snapshot = stress.get("portfolio_snapshot") if isinstance(stress.get("portfolio_snapshot"), dict) else {}
    summary = stress.get("summary") if isinstance(stress.get("summary"), dict) else {}
    boundary = stress.get("model_boundary") if isinstance(stress.get("model_boundary"), dict) else {}
    snapshot_path = bound_path(inputs.get("portfolio"))
    snapshot_generated_at = parse_timestamp(snapshot.get("generated_at"))
    age_hours = (
        (now_utc() - snapshot_generated_at).total_seconds() / 3600.0
        if snapshot_generated_at is not None
        else None
    )
    future_skew_minutes = (
        max(0.0, (snapshot_generated_at - now_utc()).total_seconds() / 60.0)
        if snapshot_generated_at is not None
        else None
    )
    current_snapshot_hash = sha256_file(snapshot_path)
    checks = {
        "stress_report_readable": stress_report_path.is_file() and not stress.get("_read_error"),
        "stress_guard_passed": stress.get("decision") == "portfolio_scenario_stress_guard_passed_research_only",
        "stress_guard_can_trade_false": stress.get("can_trade") is False,
        "stress_guard_not_exchange_replica": boundary.get("exchange_wce_replica") is False,
        "stress_guard_no_paper_execution": boundary.get("paper_execution_allowed") is False,
        "snapshot_path_exists": snapshot_path is not None and snapshot_path.is_file(),
        "snapshot_hash_bound": isinstance(inputs.get("portfolio_sha256"), str)
        and len(str(inputs.get("portfolio_sha256"))) == 64,
        "snapshot_hash_matches": current_snapshot_hash is not None
        and current_snapshot_hash == inputs.get("portfolio_sha256"),
        "snapshot_kind_paper_account": snapshot.get("snapshot_kind") == "paper_account",
        "snapshot_source_local_paper_state": snapshot.get("source_mode") == "local_paper_state",
        "snapshot_not_synthetic": snapshot.get("synthetic") is False,
        "snapshot_can_trade_false": snapshot.get("can_trade") is False,
        "snapshot_timestamp_valid": snapshot_generated_at is not None,
        "snapshot_not_too_old": age_hours is not None and 0.0 <= age_hours <= max_snapshot_age_hours,
        "snapshot_not_materially_future": future_skew_minutes is not None and future_skew_minutes <= 5.0,
        "stress_scenarios_present": int(summary.get("scenarios") or 0) > 0,
        "stress_breaches_zero": int(summary.get("breached") or 0) == 0,
    }
    failed = [name for name, passed in checks.items() if not passed]
    passed = not failed
    return {
        "generated_at": now_iso(),
        "tool": "tools/portfolio_stress_promotion_gate.py",
        "decision": "portfolio_stress_promotion_gate_passed_manual_review_only"
        if passed
        else "portfolio_stress_promotion_gate_blocked",
        "can_trade": False,
        "inputs": {
            "stress_report": portable(stress_report_path),
            "stress_report_sha256": sha256_file(stress_report_path),
            "snapshot_path": portable(snapshot_path),
            "bound_snapshot_sha256": inputs.get("portfolio_sha256"),
            "current_snapshot_sha256": current_snapshot_hash,
        },
        "snapshot": {
            **snapshot,
            "age_hours": round(age_hours, 6) if age_hours is not None else None,
            "future_skew_minutes": round(future_skew_minutes, 6) if future_skew_minutes is not None else None,
        },
        "stress_summary": summary,
        "checks": checks,
        "failed_checks": failed,
        "promotion": {
            "portfolio_stress_gate_passed": passed,
            "paper_design_review_allowed": passed,
            "paper_execution_allowed": False,
            "live_execution_allowed": False,
        },
        "next_action": "manual paper-design review may inspect this risk evidence; execution remains blocked"
        if passed
        else "provide a fresh non-synthetic local paper-account snapshot and regenerate the stress report",
        "runtime_boundary": {
            "gate_only": True,
            "private_api_required": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Portfolio Stress Promotion Gate",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `false`",
        f"- Snapshot: `{report['inputs']['snapshot_path']}`",
        f"- Snapshot age hours: `{report['snapshot'].get('age_hours')}`",
        f"- Paper-design review allowed: `{report['promotion']['paper_design_review_allowed']}`",
        f"- Paper/live execution allowed: `false` / `false`",
        "",
        "## Checks",
        "",
    ]
    for name, passed in sorted(report["checks"].items()):
        lines.append(f"- `{name}`: `{passed}`")
    lines.extend(
        [
            "",
            "## Result",
            "",
            f"- Failed checks: `{report['failed_checks']}`",
            f"- Next action: {report['next_action']}",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(report: dict[str, Any], out_prefix: str | Path) -> None:
    prefix = resolve_path(out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bind portfolio stress evidence to a fresh local paper-account snapshot")
    parser.add_argument("--stress-report", default="docs/PORTFOLIO_SCENARIO_STRESS_GUARD_SMOKE_2026-07-12.json")
    parser.add_argument("--max-snapshot-age-hours", type=float, default=24.0)
    parser.add_argument("--expect-blocked", action="store_true", help="Return success only when a valid smoke remains blocked.")
    parser.add_argument("--out-prefix", default="docs/PORTFOLIO_STRESS_PROMOTION_GATE_2026-07-12")
    args = parser.parse_args()
    report = build_report(resolve_path(args.stress_report), args.max_snapshot_age_hours)
    write_outputs(report, args.out_prefix)
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "failed_checks": report["failed_checks"],
                "paper_design_review_allowed": report["promotion"]["paper_design_review_allowed"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if report["promotion"]["portfolio_stress_gate_passed"]:
        return 0
    return 0 if args.expect_blocked and report["decision"] == "portfolio_stress_promotion_gate_blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())

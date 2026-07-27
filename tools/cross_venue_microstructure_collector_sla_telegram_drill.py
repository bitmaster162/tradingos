#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


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


def sla_report(decision: str, *, failed: list[str] | None = None, inserted_trades: int = 500, inserted_books: int = 2) -> dict[str, Any]:
    failed = failed or []
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "data_generated_at": now_iso(),
        "report_age_minutes": 0.1,
        "classification": "cross_venue_microstructure_forward_collecting",
        "new_rows": inserted_trades,
        "inserted_trades": inserted_trades,
        "inserted_books": inserted_books,
        "archive_trades": 1000000,
        "archive_books": 2000,
        "archive_features": 1000,
        "archive_trades_delta": 500,
        "archive_books_delta": 2,
        "archive_features_delta": 1,
        "span_hours": 25.0,
        "trade_coverage_pct": 99.9,
        "book_coverage_pct": 98.0,
        "trade_coverage_delta_pct": 0.001,
        "book_coverage_delta_pct": 0.001,
        "binance_missing_ids": 0,
        "coinbase_missing_ids": 0,
        "failed_checks": failed,
        "next_action": "drill_next_action",
        "runtime_boundary": {
            "sla_guard_only": True,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def run_notify(report_path: Path, state_path: Path, out_prefix: Path, timeout_s: int) -> dict[str, Any]:
    env = os.environ.copy()
    env.setdefault("TELEGRAM_BOT_TOKEN", "DRILL_DRY_RUN_TOKEN")
    env.setdefault("TELEGRAM_CHAT_ID", "DRILL_DRY_RUN_CHAT")
    command = [
        sys.executable,
        "tools/cross_venue_microstructure_collector_sla_telegram_notify.py",
        "--sla-report",
        str(report_path),
        "--state",
        str(state_path),
        "--out-prefix",
        str(out_prefix),
        "--dry-run",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
    )
    notify_report = read_json(out_prefix.with_suffix(".json"))
    return {
        "command": command,
        "return_code": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
        "notify_report": notify_report,
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Cross-Venue Microstructure Collector SLA Telegram Drill",
            "",
            f"- Generated: `{report['generated_at']}`.",
            f"- Decision: `{report['decision']}`.",
            f"- Steps passed: `{report['steps_passed']}/{report['steps_total']}`.",
            f"- Healthy decision: `{report.get('healthy_decision')}`.",
            f"- Degraded first/same/changed: `{report.get('degraded_first_decision')}` / `{report.get('degraded_same_decision')}` / `{report.get('degraded_changed_decision')}`.",
            f"- Recovery decision: `{report.get('recovery_decision')}`.",
            "- Synthetic dry-run only; no Telegram message, no signals, no orders.",
            "- `can_trade=false`.",
            "",
        ]
    )


def run_drill(work_dir: Path, out_prefix: Path, timeout_s: int) -> tuple[int, dict[str, Any]]:
    work_dir.mkdir(parents=True, exist_ok=True)
    state_path = work_dir / "collector_sla_telegram_drill_state.json"
    if state_path.exists():
        state_path.unlink()

    scenarios = [
        ("healthy", sla_report("collector_sla_healthy"), "skipped_no_notification", "collector_sla_no_notification"),
        (
            "degraded_first",
            sla_report("collector_sla_degraded_no_trade_inserts", failed=["cycle_inserted_trades"], inserted_trades=0),
            "dry_run_ready",
            "collector_sla_degraded",
        ),
        (
            "degraded_same",
            sla_report("collector_sla_degraded_no_trade_inserts", failed=["cycle_inserted_trades"], inserted_trades=0),
            "skipped_no_notification",
            "collector_sla_no_notification",
        ),
        (
            "degraded_changed",
            sla_report("collector_sla_degraded_no_book_inserts", failed=["cycle_inserted_books"], inserted_books=0),
            "dry_run_ready",
            "collector_sla_degraded_changed",
        ),
        ("recovery", sla_report("collector_sla_healthy"), "dry_run_ready", "collector_sla_recovered"),
    ]
    steps: list[dict[str, Any]] = []
    for name, fixture, expected_decision, expected_kind in scenarios:
        fixture_path = work_dir / f"{name}_collector_sla_report.json"
        write_json(fixture_path, fixture)
        result = run_notify(fixture_path, state_path, work_dir / f"{name}_notify", timeout_s)
        actual_decision = result.get("notify_report", {}).get("decision")
        actual_kind = result.get("notify_report", {}).get("kind")
        result.update(
            {
                "name": name,
                "fixture_path": str(fixture_path),
                "expected_decision": expected_decision,
                "expected_kind": expected_kind,
                "actual_decision": actual_decision,
                "actual_kind": actual_kind,
                "passed": result.get("return_code") == 0 and actual_decision == expected_decision and actual_kind == expected_kind,
            }
        )
        steps.append(result)

    checks = {
        "all_steps_exit_zero": all(step.get("return_code") == 0 for step in steps),
        "healthy_skipped": steps[0].get("passed") is True,
        "degraded_first_dry_run": steps[1].get("passed") is True,
        "same_degradation_suppressed": steps[2].get("passed") is True,
        "changed_degradation_dry_run": steps[3].get("passed") is True,
        "recovery_dry_run": steps[4].get("passed") is True,
        "all_can_trade_false": all(step.get("notify_report", {}).get("can_trade") is False for step in steps),
    }
    decision = "collector_sla_telegram_drill_passed" if all(checks.values()) else "collector_sla_telegram_drill_failed"
    report = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "work_dir": str(work_dir),
        "state_path": str(state_path),
        "checks": checks,
        "steps_passed": sum(1 for step in steps if step.get("passed")),
        "steps_total": len(steps),
        "steps": steps,
        "healthy_decision": steps[0].get("actual_decision"),
        "degraded_first_decision": steps[1].get("actual_decision"),
        "degraded_same_decision": steps[2].get("actual_decision"),
        "degraded_changed_decision": steps[3].get("actual_decision"),
        "recovery_decision": steps[4].get("actual_decision"),
        "healthy_kind": steps[0].get("actual_kind"),
        "degraded_first_kind": steps[1].get("actual_kind"),
        "degraded_same_kind": steps[2].get("actual_kind"),
        "degraded_changed_kind": steps[3].get("actual_kind"),
        "recovery_kind": steps[4].get("actual_kind"),
        "runtime_boundary": {
            "synthetic_drill_only": True,
            "sends_telegram": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    return (0 if decision.endswith("_passed") else 1), report


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic dry-run drill for microstructure collector SLA Telegram notifier")
    parser.add_argument("--work-dir")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_COLLECTOR_SLA_TELEGRAM_DRILL_2026-06-25")
    parser.add_argument("--timeout-seconds", type=int, default=60)
    args = parser.parse_args()

    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    if args.work_dir:
        code, report = run_drill(Path(args.work_dir).resolve(), out_prefix, max(1, args.timeout_seconds))
    else:
        with tempfile.TemporaryDirectory(prefix="microstructure-collector-sla-telegram-drill-") as temp_name:
            code, report = run_drill(Path(temp_name).resolve(), out_prefix, max(1, args.timeout_seconds))
    print(json.dumps({"decision": report["decision"], "steps": f"{report['steps_passed']}/{report['steps_total']}", "can_trade": False}, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

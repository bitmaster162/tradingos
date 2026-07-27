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


def transition_report(state: str, *, snapshot_id: str | None = None, changed: bool = True) -> dict[str, Any]:
    waiting = state == "waiting_for_minimum_time_window"
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "transition_state": state,
        "previous_transition_state": "waiting_for_minimum_time_window" if changed else state,
        "transition_changed": changed,
        "gate_decision": "microstructure_snapshot_sealed" if snapshot_id else "waiting_for_microstructure_readiness",
        "runner_decision": "blocked_waiting_for_sealed_snapshot",
        "snapshot_id": snapshot_id,
        "runner_snapshot_id": None,
        "primary_blocker": "minimum_time_window" if waiting else "none",
        "remaining_hours": 12.0 if waiting else 0.0,
        "earliest_time_gate_at_utc": "2026-07-01T12:00:00+00:00" if waiting else None,
        "failed_checks": ["minimum_hours"] if waiting else [],
        "checks_passed": 7 if waiting else 11,
        "checks_total": 11,
        "trade_coverage_pct": 99.9,
        "book_coverage_pct": 98.8,
        "binance_missing_ids": 0,
        "coinbase_missing_ids": 0,
        "research_runner_can_attempt_now": state == "sealed_snapshot_ready_for_train_research_batch",
        "next_action": "continue_collecting_until_time_gate" if waiting else "drill_next_action",
        "runtime_boundary": {
            "monitor_only": True,
            "runs_research_batch": False,
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
        "tools/cross_venue_microstructure_snapshot_transition_telegram_notify.py",
        "--transition-report",
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
            "# Cross-Venue Microstructure Snapshot Transition Telegram Drill",
            "",
            f"- Generated: `{report['generated_at']}`.",
            f"- Decision: `{report['decision']}`.",
            f"- Steps passed: `{report['steps_passed']}/{report['steps_total']}`.",
            f"- Waiting decision: `{report.get('waiting_decision')}`.",
            f"- Ready first/duplicate: `{report.get('ready_first_decision')}` / `{report.get('ready_duplicate_decision')}`.",
            f"- Blocked decision: `{report.get('blocked_decision')}`.",
            f"- Done decision: `{report.get('done_decision')}`.",
            "- Synthetic dry-run only; no Telegram message, no research run, no signals, no orders.",
            "- `can_trade=false`.",
            "",
        ]
    )


def run_drill(work_dir: Path, out_prefix: Path, timeout_s: int) -> tuple[int, dict[str, Any]]:
    work_dir.mkdir(parents=True, exist_ok=True)
    state_path = work_dir / "snapshot_transition_telegram_drill_state.json"
    if state_path.exists():
        state_path.unlink()

    scenarios = [
        ("waiting", transition_report("waiting_for_minimum_time_window", changed=False), "skipped_waiting"),
        ("ready_first", transition_report("sealed_snapshot_ready_for_train_research_batch", snapshot_id="drill-ready"), "dry_run_ready"),
        ("ready_duplicate", transition_report("sealed_snapshot_ready_for_train_research_batch", snapshot_id="drill-ready"), "skipped_duplicate"),
        ("blocked", transition_report("blocked_after_time_window"), "dry_run_ready"),
        ("done", transition_report("sealed_snapshot_research_batch_already_completed", snapshot_id="drill-done"), "dry_run_ready"),
    ]
    steps: list[dict[str, Any]] = []
    for name, fixture, expected_decision in scenarios:
        fixture_path = work_dir / f"{name}_transition_report.json"
        write_json(fixture_path, fixture)
        result = run_notify(
            fixture_path,
            state_path,
            work_dir / f"{name}_notify",
            timeout_s,
        )
        actual_decision = result.get("notify_report", {}).get("decision")
        actual_kind = result.get("notify_report", {}).get("kind")
        result.update(
            {
                "name": name,
                "fixture_path": str(fixture_path),
                "expected_decision": expected_decision,
                "actual_decision": actual_decision,
                "actual_kind": actual_kind,
                "passed": result.get("return_code") == 0 and actual_decision == expected_decision,
            }
        )
        steps.append(result)

    checks = {
        "all_steps_exit_zero": all(step.get("return_code") == 0 for step in steps),
        "waiting_skipped": steps[0].get("actual_decision") == "skipped_waiting",
        "ready_first_dry_run": steps[1].get("actual_decision") == "dry_run_ready",
        "ready_duplicate_suppressed": steps[2].get("actual_decision") == "skipped_duplicate",
        "blocked_dry_run": steps[3].get("actual_decision") == "dry_run_ready",
        "done_dry_run": steps[4].get("actual_decision") == "dry_run_ready",
        "all_can_trade_false": all(step.get("notify_report", {}).get("can_trade") is False for step in steps),
    }
    decision = "snapshot_transition_telegram_drill_passed" if all(checks.values()) else "snapshot_transition_telegram_drill_failed"
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
        "waiting_decision": steps[0].get("actual_decision"),
        "ready_first_decision": steps[1].get("actual_decision"),
        "ready_duplicate_decision": steps[2].get("actual_decision"),
        "blocked_decision": steps[3].get("actual_decision"),
        "done_decision": steps[4].get("actual_decision"),
        "runtime_boundary": {
            "synthetic_drill_only": True,
            "sends_telegram": False,
            "runs_research_batch": False,
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
    parser = argparse.ArgumentParser(description="Synthetic dry-run drill for microstructure snapshot transition Telegram notifier")
    parser.add_argument("--work-dir")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_TRANSITION_TELEGRAM_DRILL_2026-06-25")
    parser.add_argument("--timeout-seconds", type=int, default=60)
    args = parser.parse_args()

    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    if args.work_dir:
        code, report = run_drill(Path(args.work_dir).resolve(), out_prefix, max(1, args.timeout_seconds))
    else:
        with tempfile.TemporaryDirectory(prefix="microstructure-transition-telegram-drill-") as temp_name:
            code, report = run_drill(Path(temp_name).resolve(), out_prefix, max(1, args.timeout_seconds))
    print(json.dumps({"decision": report["decision"], "steps": f"{report['steps_passed']}/{report['steps_total']}", "can_trade": False}, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

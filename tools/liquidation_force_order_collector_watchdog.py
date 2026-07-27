#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATUS_PATH = ROOT / "logs" / "liquidation_force_order" / "liquidation_force_order_loop_status.json"
START_SCRIPT = ROOT / "ops" / "autostart" / "Start-LiquidationForceOrderCollectorLoop.ps1"
DQ_SCRIPT = ROOT / "tools" / "liquidation_force_order_data_quality.py"
FIRST_EVENT_GUARD_SCRIPT = ROOT / "tools" / "liquidation_force_order_first_event_auto_run_guard.py"
PREREGISTERED_SAMPLE_GUARD_SCRIPT = ROOT / "tools" / "liquidation_force_order_preregistered_sample_guard.py"
PREREGISTERED_PROGRESS_SCRIPT = ROOT / "tools" / "liquidation_force_order_preregistered_progress.py"
MAJOR_CACHE_REFRESH_SCRIPT = ROOT / "tools" / "liquidation_force_order_major_cache_refresh.py"
SUPERVISOR_SCRIPT = ROOT / "tools" / "liquidation_force_order_supervisor_summary.py"
TERMINAL_TELEGRAM_SCRIPT = ROOT / "tools" / "liquidation_force_order_terminal_telegram_notify.py"
DEFAULT_OUT_PREFIX = "docs/LIQUIDATION_FORCE_ORDER_COLLECTOR_WATCHDOG_2026-06-30"
DEFAULT_DQ_PREFIX = "docs/LIQUIDATION_FORCE_ORDER_DATA_QUALITY_2026-06-30"
DEFAULT_FIRST_EVENT_GUARD_PREFIX = "docs/LIQUIDATION_FORCE_ORDER_FIRST_EVENT_AUTO_RUN_GUARD_2026-07-01"
DEFAULT_PREREGISTERED_SAMPLE_GUARD_PREFIX = "docs/LIQUIDATION_FORCE_ORDER_PREREGISTERED_SAMPLE_GUARD_2026-07-12"
DEFAULT_PREREGISTERED_PROGRESS_PREFIX = "docs/LIQUIDATION_FORCE_ORDER_PREREGISTERED_PROGRESS_2026-07-12"
DEFAULT_MAJOR_CACHE_REFRESH_PREFIX = "docs/LIQUIDATION_FORCE_ORDER_MAJOR_CACHE_REFRESH_2026-07-12"
DEFAULT_SUPERVISOR_PREFIX = "docs/LIQUIDATION_FORCE_ORDER_SUPERVISOR_SUMMARY_2026-07-01"
DEFAULT_TERMINAL_TELEGRAM_PREFIX = "docs/LIQUIDATION_FORCE_ORDER_TERMINAL_TELEGRAM_2026-07-12"
DEFAULT_TERMINAL_CARD_PREFIX = "docs/LIQUIDATION_FORCE_ORDER_TERMINAL_REVIEW_CARD_2026-07-12"

HEALTHY_STATUSES = {
    "already_running",
    "ran_collector_cycle",
    "running",
    "running_collector_cycle",
    "skipped_existing_liquidation_force_order_loop",
    "sleeping_initial",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds").replace("+00:00", "Z")


def portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc), "_path": str(path)}
    return value if isinstance(value, dict) else {"_read_error": "not_object", "_path": str(path)}


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_minutes(value: Any) -> float | None:
    parsed = parse_ts(value)
    if parsed is None:
        return None
    return round((now_utc() - parsed).total_seconds() / 60.0, 3)


def process_alive(pid_value: Any) -> bool:
    try:
        pid = int(pid_value)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ 'alive' }}",
        ]
        try:
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return "alive" in result.stdout
    try:
        import os

        os.kill(pid, 0)
    except OSError:
        return False
    return True


def status_health(status: dict[str, Any], stale_minutes: float) -> dict[str, Any]:
    status_text = status.get("status")
    pid = status.get("pid")
    alive = process_alive(pid)
    status_age = age_minutes(status.get("ts"))
    recent = status_age is not None and status_age <= stale_minutes
    healthy = bool(status_text in HEALTHY_STATUSES and alive and recent)
    reasons: list[str] = []
    if not status:
        reasons.append("status_missing")
    if status_text not in HEALTHY_STATUSES:
        reasons.append("status_not_healthy")
    if not alive:
        reasons.append("pid_not_alive")
    if not recent:
        reasons.append("status_stale")
    return {
        "healthy": healthy,
        "status": status_text,
        "pid": pid,
        "pid_alive": alive,
        "status_age_minutes": status_age,
        "reasons": sorted(set(reasons)),
    }


def run_command(command: list[str], timeout_s: int) -> dict[str, Any]:
    started = time.time()
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return {
            "command": command,
            "exit_code": result.returncode,
            "duration_s": round(time.time() - started, 3),
            "stdout": result.stdout[-12000:],
            "stderr": result.stderr[-12000:],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "exit_code": None,
            "duration_s": round(time.time() - started, 3),
            "stdout": (exc.stdout or "")[-12000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-12000:] if isinstance(exc.stderr, str) else "",
            "timed_out": True,
        }


def start_collector(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(START_SCRIPT),
        "-Symbols",
        args.symbols,
        "-StreamMode",
        args.stream_mode,
        "-CycleSeconds",
        str(args.cycle_seconds),
        "-MaxEventsPerCycle",
        str(args.max_events_per_cycle),
        "-SleepSeconds",
        str(args.sleep_seconds),
    ]
    if args.python_path:
        command.extend(["-PythonPath", args.python_path])
    return run_command(command, timeout_s=args.start_timeout_seconds)


def run_data_quality(out_prefix: str) -> dict[str, Any]:
    command = [sys.executable, str(DQ_SCRIPT), "--out-prefix", out_prefix]
    run = run_command(command, timeout_s=60)
    report = read_json(resolve_path(out_prefix).with_suffix(".json"))
    return {"run": run, "report": report}


def run_first_event_guard(out_prefix: str, data_quality_prefix: str) -> dict[str, Any]:
    command = [
        sys.executable,
        str(FIRST_EVENT_GUARD_SCRIPT),
        "--data-quality-prefix",
        data_quality_prefix,
        "--out-prefix",
        out_prefix,
    ]
    run = run_command(command, timeout_s=180)
    report = read_json(resolve_path(out_prefix).with_suffix(".json"))
    return {"run": run, "report": report}


def run_preregistered_progress(out_prefix: str) -> dict[str, Any]:
    command = [sys.executable, str(PREREGISTERED_PROGRESS_SCRIPT), "--out-prefix", out_prefix]
    run = run_command(command, timeout_s=120)
    report = read_json(resolve_path(out_prefix).with_suffix(".json"))
    return {"run": run, "report": report}


def run_major_cache_refresh(out_prefix: str) -> dict[str, Any]:
    command = [sys.executable, str(MAJOR_CACHE_REFRESH_SCRIPT), "--out-prefix", out_prefix]
    run = run_command(command, timeout_s=180)
    report = read_json(resolve_path(out_prefix).with_suffix(".json"))
    return {"run": run, "report": report}


def run_preregistered_sample_guard(out_prefix: str, data_quality_prefix: str, progress_prefix: str) -> dict[str, Any]:
    command = [
        sys.executable,
        str(PREREGISTERED_SAMPLE_GUARD_SCRIPT),
        "--data-quality",
        str(resolve_path(data_quality_prefix).with_suffix(".json")),
        "--progress",
        str(resolve_path(progress_prefix).with_suffix(".json")),
        "--out-prefix",
        out_prefix,
    ]
    run = run_command(command, timeout_s=240)
    report = read_json(resolve_path(out_prefix).with_suffix(".json"))
    return {"run": run, "report": report}


def run_supervisor_summary(out_prefix: str) -> dict[str, Any]:
    command = [sys.executable, str(SUPERVISOR_SCRIPT), "--out-prefix", out_prefix]
    run = run_command(command, timeout_s=60)
    report = read_json(resolve_path(out_prefix).with_suffix(".json"))
    return {"run": run, "report": report}


def run_terminal_telegram(guard_prefix: str, out_prefix: str, card_prefix: str) -> dict[str, Any]:
    command = [
        sys.executable,
        str(TERMINAL_TELEGRAM_SCRIPT),
        "--guard-report",
        str(resolve_path(guard_prefix).with_suffix(".json")),
        "--out-prefix",
        out_prefix,
        "--card-prefix",
        card_prefix,
        "--send",
    ]
    run = run_command(command, timeout_s=60)
    report = read_json(resolve_path(out_prefix).with_suffix(".json"))
    return {"run": run, "report": report}


def classify(report: dict[str, Any]) -> str:
    after = report.get("after", {})
    dq = report.get("data_quality", {}).get("report", {})
    hard_failures = [
        gate.get("name")
        for gate in dq.get("gates", [])
        if isinstance(gate, dict) and gate.get("severity") == "hard" and not gate.get("passed")
    ]
    if after.get("healthy") and not hard_failures:
        return "liquidation_force_order_collector_healthy_waiting_events"
    if after.get("healthy"):
        return "liquidation_force_order_collector_running_but_quality_gate_failed"
    if report.get("restart_attempted"):
        return "liquidation_force_order_collector_restart_failed"
    return "liquidation_force_order_collector_unhealthy_not_restarted"


def render_markdown(report: dict[str, Any]) -> str:
    dq = report.get("data_quality", {}).get("report", {})
    sample_wrapper = report.get("preregistered_sample_guard") if isinstance(report.get("preregistered_sample_guard"), dict) else {}
    sample_guard = sample_wrapper.get("report") if isinstance(sample_wrapper.get("report"), dict) else {}
    sample_pipeline = sample_guard.get("pipeline") if isinstance(sample_guard.get("pipeline"), dict) else {}
    progress_wrapper = report.get("preregistered_progress") if isinstance(report.get("preregistered_progress"), dict) else {}
    progress_report = progress_wrapper.get("report") if isinstance(progress_wrapper.get("report"), dict) else {}
    cache_wrapper = report.get("major_cache_refresh") if isinstance(report.get("major_cache_refresh"), dict) else {}
    cache_report = cache_wrapper.get("report") if isinstance(cache_wrapper.get("report"), dict) else {}
    telegram_wrapper = report.get("terminal_telegram") if isinstance(report.get("terminal_telegram"), dict) else {}
    telegram_report = telegram_wrapper.get("report") if isinstance(telegram_wrapper.get("report"), dict) else {}
    events = dq.get("events", {}).get("events")
    hard_failures = [
        gate.get("name")
        for gate in dq.get("gates", [])
        if isinstance(gate, dict) and gate.get("severity") == "hard" and not gate.get("passed")
    ]
    soft_failures = [
        gate.get("name")
        for gate in dq.get("gates", [])
        if isinstance(gate, dict) and gate.get("severity") == "soft" and not gate.get("passed")
    ]
    lines = [
        "# Liquidation ForceOrder Collector Watchdog",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `{str(report['can_trade']).lower()}`",
        f"- Restart attempted: `{str(report['restart_attempted']).lower()}`",
        "",
        "## Status",
        "",
        "| Phase | Status | PID alive | Age min | Reasons |",
        "| --- | --- | --- | --- | --- |",
        f"| Before | `{report['before'].get('status')}` | `{report['before'].get('pid_alive')}` | `{report['before'].get('status_age_minutes')}` | `{', '.join(report['before'].get('reasons', []))}` |",
        f"| After | `{report['after'].get('status')}` | `{report['after'].get('pid_alive')}` | `{report['after'].get('status_age_minutes')}` | `{', '.join(report['after'].get('reasons', []))}` |",
        "",
        "## Data quality",
        "",
        f"- Decision: `{dq.get('decision')}`",
        f"- Events: `{events}`",
        f"- Hard failures: `{', '.join(hard_failures) if hard_failures else 'none'}`",
        f"- Soft failures: `{', '.join(soft_failures) if soft_failures else 'none'}`",
        "",
        "## First-event auto-run guard",
        "",
        f"- Decision: `{report.get('first_event_guard', {}).get('report', {}).get('decision')}`",
        f"- Events: `{report.get('first_event_guard', {}).get('report', {}).get('events')}`",
        f"- Pipeline ran: `{report.get('first_event_guard', {}).get('report', {}).get('pipeline_ran')}`",
        "",
        "## Preregistered sample guard",
        "",
        f"- Decision: `{sample_guard.get('decision')}`",
        f"- Events: `{sample_guard.get('events')}` / `{sample_guard.get('required_events')}`",
        f"- Pipeline decision: `{sample_pipeline.get('decision')}`",
        f"- Terminal receipt: `{(sample_guard.get('terminal_receipt') or {}).get('decision')}`",
        "",
        "## Terminal Telegram",
        "",
        f"- Decision: `{telegram_report.get('decision')}`",
        f"- Kind: `{telegram_report.get('kind')}`",
        f"- Send requested: `{telegram_report.get('send_requested')}`",
        f"- Telegram response ok: `{telegram_report.get('telegram_response_ok')}`",
        "",
        "## Preregistered progress",
        "",
        f"- Decision: `{progress_report.get('decision')}`",
        f"- Ready: `{progress_report.get('ready_for_pipeline')}`",
        f"- Blockers: `{progress_report.get('blockers')}`",
        "",
        "## Major futures cache refresh",
        "",
        f"- Decision: `{cache_report.get('decision')}`",
        f"- Run attempted: `{cache_report.get('run_attempted')}`",
        f"- Stale symbols: `{cache_report.get('stale_symbols')}`",
        "",
        "## Supervisor summary",
        "",
        f"- Decision: `{report.get('supervisor_summary', {}).get('report', {}).get('decision')}`",
        f"- Events: `{report.get('supervisor_summary', {}).get('report', {}).get('current', {}).get('event_storage', {}).get('events')}`",
        f"- History snapshots: `{report.get('supervisor_summary', {}).get('report', {}).get('history_summary', {}).get('snapshots')}`",
        "",
        "## Boundary",
        "- Public Binance USD-M websocket collector only.",
        "- No private credentials.",
        "- No paper entries.",
        "- No orders.",
    ]
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watchdog for Binance forceOrder collector loop.")
    parser.add_argument("--symbols", default="ALL")
    parser.add_argument("--stream-mode", choices=["symbols", "all_market", "both"], default="all_market")
    parser.add_argument("--cycle-seconds", type=int, default=300)
    parser.add_argument("--max-events-per-cycle", type=int, default=100)
    parser.add_argument("--sleep-seconds", type=int, default=5)
    parser.add_argument("--stale-minutes", type=float, default=10.0)
    parser.add_argument("--wait-after-start-seconds", type=int, default=8)
    parser.add_argument("--start-timeout-seconds", type=int, default=30)
    parser.add_argument("--python-path", default="")
    parser.add_argument("--out-prefix", default=DEFAULT_OUT_PREFIX)
    parser.add_argument("--data-quality-prefix", default=DEFAULT_DQ_PREFIX)
    parser.add_argument("--first-event-guard-prefix", default=DEFAULT_FIRST_EVENT_GUARD_PREFIX)
    parser.add_argument("--preregistered-sample-guard-prefix", default=DEFAULT_PREREGISTERED_SAMPLE_GUARD_PREFIX)
    parser.add_argument("--preregistered-progress-prefix", default=DEFAULT_PREREGISTERED_PROGRESS_PREFIX)
    parser.add_argument("--major-cache-refresh-prefix", default=DEFAULT_MAJOR_CACHE_REFRESH_PREFIX)
    parser.add_argument("--supervisor-prefix", default=DEFAULT_SUPERVISOR_PREFIX)
    parser.add_argument("--terminal-telegram-prefix", default=DEFAULT_TERMINAL_TELEGRAM_PREFIX)
    parser.add_argument("--terminal-card-prefix", default=DEFAULT_TERMINAL_CARD_PREFIX)
    parser.add_argument("--skip-first-event-guard", action="store_true")
    parser.add_argument("--skip-preregistered-sample-guard", action="store_true")
    parser.add_argument("--skip-preregistered-progress", action="store_true")
    parser.add_argument("--skip-major-cache-refresh", action="store_true")
    parser.add_argument("--skip-supervisor-summary", action="store_true")
    parser.add_argument("--skip-terminal-telegram", action="store_true")
    parser.add_argument("--no-restart", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    before_status = read_json(STATUS_PATH)
    before = status_health(before_status, stale_minutes=args.stale_minutes)
    restart_attempted = False
    restart = None
    if not before["healthy"] and not args.no_restart:
        restart_attempted = True
        restart = start_collector(args)
        time.sleep(max(0, args.wait_after_start_seconds))
    after_status = read_json(STATUS_PATH)
    after = status_health(after_status, stale_minutes=args.stale_minutes)
    data_quality = run_data_quality(args.data_quality_prefix)
    first_event_guard = None if args.skip_first_event_guard else run_first_event_guard(args.first_event_guard_prefix, args.data_quality_prefix)
    major_cache_refresh = None if args.skip_major_cache_refresh else run_major_cache_refresh(args.major_cache_refresh_prefix)
    preregistered_progress = None if args.skip_preregistered_progress else run_preregistered_progress(args.preregistered_progress_prefix)
    preregistered_sample_guard = (
        None
        if args.skip_preregistered_sample_guard
        else run_preregistered_sample_guard(
            args.preregistered_sample_guard_prefix,
            args.data_quality_prefix,
            args.preregistered_progress_prefix,
        )
    )
    terminal_telegram = (
        None
        if args.skip_terminal_telegram or preregistered_sample_guard is None
        else run_terminal_telegram(
            args.preregistered_sample_guard_prefix,
            args.terminal_telegram_prefix,
            args.terminal_card_prefix,
        )
    )
    supervisor_summary = None if args.skip_supervisor_summary else run_supervisor_summary(args.supervisor_prefix)
    report = {
        "generated_at": now_iso(),
        "tool": "tools/liquidation_force_order_collector_watchdog.py",
        "decision": "",
        "can_trade": False,
        "restart_attempted": restart_attempted,
        "before": before,
        "after": after,
        "restart": restart,
        "data_quality": data_quality,
        "first_event_guard": first_event_guard,
        "major_cache_refresh": major_cache_refresh,
        "preregistered_progress": preregistered_progress,
        "preregistered_sample_guard": preregistered_sample_guard,
        "terminal_telegram": terminal_telegram,
        "supervisor_summary": supervisor_summary,
        "boundary": {
            "data_collector_only": True,
            "public_websocket_only": True,
            "sends_orders": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "paths": {
            "status": portable_path(STATUS_PATH),
            "data_quality_json": portable_path(resolve_path(args.data_quality_prefix).with_suffix(".json")),
            "first_event_guard_json": portable_path(resolve_path(args.first_event_guard_prefix).with_suffix(".json")),
            "preregistered_sample_guard_json": portable_path(resolve_path(args.preregistered_sample_guard_prefix).with_suffix(".json")),
            "preregistered_progress_json": portable_path(resolve_path(args.preregistered_progress_prefix).with_suffix(".json")),
            "major_cache_refresh_json": portable_path(resolve_path(args.major_cache_refresh_prefix).with_suffix(".json")),
            "supervisor_summary_json": portable_path(resolve_path(args.supervisor_prefix).with_suffix(".json")),
            "terminal_telegram_json": portable_path(resolve_path(args.terminal_telegram_prefix).with_suffix(".json")),
            "terminal_review_card_json": portable_path(resolve_path(args.terminal_card_prefix).with_suffix(".json")),
        },
        "collector_config": {
            "symbols": args.symbols,
            "stream_mode": args.stream_mode,
            "cycle_seconds": args.cycle_seconds,
            "max_events_per_cycle": args.max_events_per_cycle,
        },
    }
    report["decision"] = classify(report)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "restart_attempted": restart_attempted,
                "after_healthy": after["healthy"],
                "data_quality": data_quality.get("report", {}).get("decision"),
                "first_event_guard": (first_event_guard or {}).get("report", {}).get("decision"),
                "preregistered_sample_guard": (preregistered_sample_guard or {}).get("report", {}).get("decision"),
                "preregistered_progress": (preregistered_progress or {}).get("report", {}).get("decision"),
                "major_cache_refresh": (major_cache_refresh or {}).get("report", {}).get("decision"),
                "supervisor_summary": (supervisor_summary or {}).get("report", {}).get("decision"),
                "terminal_telegram": (terminal_telegram or {}).get("report", {}).get("decision"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if after["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

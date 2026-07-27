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
STATUS_PATH = ROOT / "logs" / "liquidation_bybit" / "bybit_all_liquidation_loop_status.json"
START_SCRIPT = ROOT / "ops" / "autostart" / "Start-BybitAllLiquidationCollectorLoop.ps1"
SAMPLE_GATE_SCRIPT = ROOT / "tools" / "bybit_all_liquidation_sample_gate.py"
DEFAULT_OUT_PREFIX = "docs/BYBIT_ALL_LIQUIDATION_COLLECTOR_WATCHDOG_2026-07-01"
DEFAULT_SAMPLE_GATE_PREFIX = "docs/BYBIT_ALL_LIQUIDATION_SAMPLE_GATE_2026-07-02_AFTER_PRICE_GAP_FILL_EXPLICIT"

HEALTHY_STATUSES = {
    "already_running",
    "ran_collector_cycle",
    "running",
    "running_collector_cycle",
    "skipped_existing_bybit_all_liquidation_loop",
    "sleeping_initial",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc), "_path": portable(path)}
    return payload if isinstance(payload, dict) else {"_read_error": "not_object", "_path": portable(path)}


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


def run_command(command: list[str], timeout_s: int) -> dict[str, Any]:
    started = time.time()
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "exit_code": None,
            "duration_s": round(time.time() - started, 3),
            "stdout_tail": (exc.stdout or "")[-8000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-8000:] if isinstance(exc.stderr, str) else "",
            "timed_out": True,
        }
    return {
        "command": command,
        "exit_code": result.returncode,
        "duration_s": round(time.time() - started, 3),
        "stdout_tail": result.stdout[-8000:],
        "stderr_tail": result.stderr[-8000:],
        "timed_out": False,
    }


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


def run_sample_gate(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        sys.executable,
        str(SAMPLE_GATE_SCRIPT),
        "--refresh",
        "--symbols",
        args.research_symbols,
        "--interval",
        args.interval,
        "--horizons",
        args.horizons,
        "--min-events-for-research",
        str(args.min_events_for_research),
        "--min-event-bars-for-research",
        str(args.min_event_bars_for_research),
        "--min-context-bars",
        str(args.min_context_bars),
        "--out-prefix",
        args.sample_gate_prefix,
    ]
    run = run_command(command, timeout_s=args.sample_gate_timeout_seconds)
    report = read_json(resolve_path(args.sample_gate_prefix).with_suffix(".json"))
    return {"run": run, "report": report}


def classify(report: dict[str, Any]) -> str:
    after = report.get("after", {})
    sample_gate = report.get("sample_gate", {}).get("report", {})
    if after.get("healthy") and sample_gate.get("decision") == "bybit_liquidation_sample_ready_for_manual_review":
        return "bybit_all_liquidation_watchdog_ready_for_manual_review"
    if after.get("healthy") and sample_gate.get("decision") == "bybit_liquidation_sample_collecting":
        return "bybit_all_liquidation_watchdog_healthy_collecting_sample"
    if after.get("healthy") and sample_gate.get("decision") == "bybit_liquidation_sample_gate_hard_fail":
        return "bybit_all_liquidation_watchdog_running_but_quality_gate_failed"
    if after.get("healthy"):
        return "bybit_all_liquidation_watchdog_healthy_check_sample_gate"
    if report.get("restart_attempted"):
        return "bybit_all_liquidation_watchdog_restart_failed"
    return "bybit_all_liquidation_watchdog_unhealthy_not_restarted"


def render_markdown(report: dict[str, Any]) -> str:
    gate = report.get("sample_gate", {}).get("report", {})
    evidence = gate.get("evidence") if isinstance(gate.get("evidence"), dict) else {}
    lines = [
        "# Bybit allLiquidation Collector Watchdog",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        f"- Restart attempted: `{str(report['restart_attempted']).lower()}`",
        "",
        "## Status",
        "",
        "| Phase | Status | PID alive | Age min | Reasons |",
        "|---|---|---:|---:|---|",
        f"| Before | `{report['before'].get('status')}` | `{report['before'].get('pid_alive')}` | `{report['before'].get('status_age_minutes')}` | `{', '.join(report['before'].get('reasons', []))}` |",
        f"| After | `{report['after'].get('status')}` | `{report['after'].get('pid_alive')}` | `{report['after'].get('status_age_minutes')}` | `{', '.join(report['after'].get('reasons', []))}` |",
        "",
        "## Sample Gate",
        "",
        f"- Decision: `{gate.get('decision')}`",
        f"- Events: `{evidence.get('events')}`",
        f"- Event bars: `{evidence.get('aggregate_rows')}`",
        f"- Matched bars: `{evidence.get('matched_price_bars')}`",
        f"- Contexts: `{evidence.get('contexts')}`",
        f"- Blockers: `{', '.join(gate.get('blockers') or []) or 'none'}`",
        "",
        "## Boundary",
        "",
        "- Public Bybit V5 websocket collector only.",
        "- No private credentials, no paper entries, no orders.",
        "- Sample gate is allowed to run research-only diagnostics, not trading logic.",
    ]
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watchdog for Bybit allLiquidation collector loop and sample gate.")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,LINKUSDT,LTCUSDT")
    parser.add_argument("--research-symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BCHUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--horizons", default="1,2,4")
    parser.add_argument("--cycle-seconds", type=int, default=300)
    parser.add_argument("--max-events-per-cycle", type=int, default=100)
    parser.add_argument("--sleep-seconds", type=int, default=5)
    parser.add_argument("--stale-minutes", type=float, default=10.0)
    parser.add_argument("--wait-after-start-seconds", type=int, default=8)
    parser.add_argument("--start-timeout-seconds", type=int, default=30)
    parser.add_argument("--sample-gate-timeout-seconds", type=int, default=180)
    parser.add_argument("--min-events-for-research", type=int, default=500)
    parser.add_argument("--min-event-bars-for-research", type=int, default=50)
    parser.add_argument("--min-context-bars", type=int, default=10)
    parser.add_argument("--python-path", default="")
    parser.add_argument("--out-prefix", default=DEFAULT_OUT_PREFIX)
    parser.add_argument("--sample-gate-prefix", default=DEFAULT_SAMPLE_GATE_PREFIX)
    parser.add_argument("--no-restart", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_prefix = resolve_path(args.out_prefix)
    before = status_health(read_json(STATUS_PATH), args.stale_minutes)
    restart_run = None
    restart_attempted = False
    if not before["healthy"] and not args.no_restart:
        restart_attempted = True
        restart_run = start_collector(args)
        time.sleep(args.wait_after_start_seconds)
    after = status_health(read_json(STATUS_PATH), args.stale_minutes)
    sample_gate = run_sample_gate(args)
    report = {
        "generated_at": now_iso(),
        "tool": "tools/bybit_all_liquidation_collector_watchdog.py",
        "decision": "",
        "can_trade": False,
        "boundary": {
            "watchdog_only": True,
            "data_collector_only": True,
            "research_gate_only": True,
            "sends_orders": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "before": before,
        "after": after,
        "restart_attempted": restart_attempted,
        "restart_run": restart_run,
        "sample_gate": sample_gate,
    }
    report["decision"] = classify(report)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "before": before["status"],
                "after": after["status"],
                "restart_attempted": restart_attempted,
                "sample_gate": sample_gate.get("report", {}).get("decision"),
                "out": portable(out_prefix.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

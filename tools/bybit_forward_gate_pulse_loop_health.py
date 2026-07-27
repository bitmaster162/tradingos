#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


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


def read_json(path: str | Path) -> dict[str, Any]:
    p = resolve_path(path)
    if not p.is_file():
        return {}
    try:
        payload = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_minutes(value: Any) -> float | None:
    parsed = parse_ts(value)
    if parsed is None:
        return None
    return round((datetime.now(timezone.utc) - parsed).total_seconds() / 60.0, 6)


def process_alive(pid: Any) -> bool:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid_int)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) == 0:
                    return False
                return code.value == 259
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid_int, 0)
    except OSError:
        return False
    return True


def write_md(path: Path, report: dict[str, Any]) -> None:
    sample = report.get("sample_progress", {})
    checks = report.get("checks", {})
    lines = [
        "# Bybit Gate Pulse Loop Health",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Failed checks: `{len(report.get('failed_checks') or [])}`",
        f"- can_trade: `{str(report.get('can_trade')).lower()}`",
        "",
        "## Checks",
        "",
    ]
    for key, value in checks.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Observed",
            "",
            f"- Loop status: `{report.get('loop_status')}`",
            f"- Loop PID: `{report.get('loop_pid')}`",
            f"- PID alive: `{report.get('loop_pid_alive')}`",
            f"- Status age minutes: `{report.get('status_age_minutes')}`",
            f"- Sleep seconds: `{report.get('sleep_seconds')}`",
            f"- Last pulse decision: `{report.get('pulse_decision')}`",
            f"- Review action: `{report.get('review_action')}`",
            f"- Event bars: `{sample.get('event_bars_current')}/{sample.get('event_bars_required')}`",
            f"- Resolved records: `{sample.get('resolved_records')}`",
            "",
            "## Boundary",
            "",
            "Health check only. No Telegram, no signals, no paper entries, no orders.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Health audit for the Bybit gate pulse loop.")
    parser.add_argument("--loop-status", default="logs/liquidation_bybit/bybit_forward_gate_pulse_loop_status.json")
    parser.add_argument("--loop-lock", default="logs/liquidation_bybit/bybit_forward_gate_pulse_loop.lock.json")
    parser.add_argument("--pulse-report", default="docs/BYBIT_LIQUIDATION_FORWARD_GATE_PULSE_2026-07-03.json")
    parser.add_argument("--max-status-age-minutes", type=float, default=30.0)
    parser.add_argument("--out-prefix", default="docs/BYBIT_FORWARD_GATE_PULSE_LOOP_HEALTH_2026-07-03")
    args = parser.parse_args()

    loop = read_json(args.loop_status)
    lock = read_json(args.loop_lock)
    pulse = read_json(args.pulse_report)
    sample = pulse.get("sample_progress") if isinstance(pulse.get("sample_progress"), dict) else {}
    status_age = age_minutes(loop.get("ts"))
    pid_alive = process_alive(loop.get("pid"))
    checks = {
        "loop_status_readable": bool(loop),
        "loop_status_expected": loop.get("status") in {"running", "running_pulse_cycle", "ran_pulse_cycle", "sleeping_initial"},
        "loop_exit_code_ok": loop.get("exit_code") in {0, None},
        "loop_pid_alive": pid_alive,
        "loop_status_fresh": status_age is not None and status_age <= args.max_status_age_minutes,
        "lock_exists": bool(lock),
        "lock_pid_matches_status": bool(lock) and lock.get("pid") == loop.get("pid"),
        "live_trading_locked": loop.get("live_trading_locked") is True,
        "signals_not_allowed": loop.get("signals_allowed") is False,
        "paper_entries_not_allowed": loop.get("paper_entries_allowed") is False,
        "orders_not_allowed": loop.get("orders_allowed") is False,
        "pulse_report_readable": bool(pulse),
        "pulse_can_trade_false": pulse.get("can_trade") is False,
        "pulse_orders_allowed_false": pulse.get("orders_allowed") is False,
    }
    failed = [name for name, passed in checks.items() if not passed]
    report = {
        "generated_at": now_iso(),
        "tool": "tools/bybit_forward_gate_pulse_loop_health.py",
        "decision": "bybit_gate_pulse_loop_healthy" if not failed else "bybit_gate_pulse_loop_unhealthy",
        "can_trade": False,
        "orders_allowed": False,
        "checks": checks,
        "failed_checks": failed,
        "loop_status": loop.get("status"),
        "loop_exit_code": loop.get("exit_code"),
        "loop_pid": loop.get("pid"),
        "loop_pid_alive": pid_alive,
        "status_age_minutes": status_age,
        "sleep_seconds": loop.get("sleep_seconds"),
        "pulse_decision": pulse.get("decision"),
        "review_action": pulse.get("review_action"),
        "sample_progress": sample,
        "source_reports": {
            "loop_status": args.loop_status,
            "loop_lock": args.loop_lock,
            "pulse_report": args.pulse_report,
        },
        "boundary": {
            "health_check_only": True,
            "sends_telegram": False,
            "emits_trade_signals": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "can_trade": False,
        },
    }

    out_json = resolve_path(f"{args.out_prefix}.json")
    out_md = resolve_path(f"{args.out_prefix}.md")
    report["out"] = portable(out_json)
    report["md"] = portable(out_md)
    write_json(out_json, report)
    write_md(out_md, report)
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "failed_checks": failed,
                "loop_status": report["loop_status"],
                "pid_alive": pid_alive,
                "pulse_decision": report["pulse_decision"],
                "event_bars": sample.get("event_bars_current"),
                "out": report["out"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())


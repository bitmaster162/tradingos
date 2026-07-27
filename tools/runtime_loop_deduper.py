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

REAL_EDGE_STATUS_PATH = ROOT / "logs" / "real_edge_observer" / "real_edge_observer_pulse_loop_status.json"
REAL_EDGE_LOCK_PATH = ROOT / "logs" / "real_edge_observer" / "real_edge_observer_pulse_loop.lock.json"
BYBIT_STATUS_PATH = ROOT / "logs" / "liquidation_bybit" / "bybit_forward_gate_pulse_loop_status.json"
BYBIT_LOCK_PATH = ROOT / "logs" / "liquidation_bybit" / "bybit_forward_gate_pulse_loop.lock.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        raw = path.read_bytes()
        for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
            try:
                return json.loads(raw.decode(encoding))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        return json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        return {"_read_error": str(exc), "_path": str(path)}


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def process_alive(pid: Any) -> bool:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    if sys.platform.startswith("win"):
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"if (Get-Process -Id {pid_int} -ErrorAction SilentlyContinue) {{ 'alive' }} else {{ 'dead' }}",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        return proc.stdout.strip() == "alive"
    try:
        import os

        os.kill(pid_int, 0)
    except OSError:
        return False
    return True


def process_commandline(pid: Any) -> str | None:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return None
    if pid_int <= 0:
        return None
    if not sys.platform.startswith("win"):
        return None
    proc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "$p = Get-CimInstance Win32_Process "
                f"-Filter \"ProcessId = {pid_int}\" -ErrorAction SilentlyContinue; "
                "if ($p) { $p.CommandLine }"
            ),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    value = proc.stdout.strip()
    return value or None


def stop_process(pid: int) -> dict[str, Any]:
    if not sys.platform.startswith("win"):
        return {"attempted": False, "ok": False, "error": "stop_process_only_implemented_for_windows"}
    proc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"Stop-Process -Id {pid} -Force -ErrorAction Stop",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    return {
        "attempted": True,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def healthy_real_edge_loop(status: dict[str, Any] | None, lock: dict[str, Any] | None) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    if not isinstance(status, dict):
        blockers.append("missing_real_edge_status")
        return False, blockers
    if status.get("status") != "ran_observer_pulse_cycle":
        blockers.append("real_edge_loop_not_in_expected_status")
    if status.get("exit_code") not in (0, None):
        blockers.append("real_edge_loop_nonzero_exit")
    if not process_alive(status.get("pid")):
        blockers.append("real_edge_loop_pid_not_alive")
    if not status.get("live_trading_locked"):
        blockers.append("real_edge_loop_live_lock_missing")
    for flag in ("signals_allowed", "paper_entries_allowed", "orders_allowed", "telegram_send_allowed"):
        if status.get(flag) is not False:
            blockers.append(f"real_edge_loop_{flag}_not_false")
    if not isinstance(lock, dict):
        blockers.append("missing_real_edge_lock")
    elif str(lock.get("pid")) != str(status.get("pid")):
        blockers.append("real_edge_lock_pid_mismatch")
    return not blockers, blockers


def render_md(report: dict[str, Any]) -> str:
    redundant = report.get("redundant_bybit_loop", {})
    replacement = report.get("replacement_real_edge_loop", {})
    safety = report.get("safety", {})
    lines = [
        "# Runtime Loop Deduper",
        "",
        f"- generated_at: `{report.get('generated_at')}`",
        f"- decision: `{report.get('decision')}`",
        f"- apply: `{report.get('apply')}`",
        f"- can_trade: `{report.get('can_trade')}`",
        f"- orders_allowed: `{report.get('orders_allowed')}`",
        "",
        "## Replacement Loop",
        "",
        f"- status: `{replacement.get('status')}`",
        f"- pid: `{replacement.get('pid')}`",
        f"- pid_alive: `{replacement.get('pid_alive')}`",
        f"- healthy: `{replacement.get('healthy')}`",
        "",
        "## Redundant Bybit-Only Loop",
        "",
        f"- status_before: `{redundant.get('status_before')}`",
        f"- pid: `{redundant.get('pid')}`",
        f"- pid_alive_before: `{redundant.get('pid_alive_before')}`",
        f"- commandline_matches_bybit_loop: `{redundant.get('commandline_matches_bybit_loop')}`",
        f"- stop_attempted: `{redundant.get('stop_attempted')}`",
        f"- stop_ok: `{redundant.get('stop_ok')}`",
        f"- pid_alive_after: `{redundant.get('pid_alive_after')}`",
        f"- lock_removed: `{redundant.get('lock_removed')}`",
        "",
        "## Blockers",
        "",
    ]
    blockers = report.get("blockers") or []
    if blockers:
        lines.extend(f"- `{item}`" for item in blockers)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Safety Boundary",
            "",
            f"- does_not_start_strategy: `{safety.get('does_not_start_strategy')}`",
            f"- sends_telegram: `{safety.get('sends_telegram')}`",
            f"- emits_signals: `{safety.get('emits_signals')}`",
            f"- opens_paper_entries: `{safety.get('opens_paper_entries')}`",
            f"- sends_orders: `{safety.get('sends_orders')}`",
            "",
            "## Note",
            "",
            "The old Bybit-only loop is redundant when the aggregate real-edge observer pulse loop is healthy, because the aggregate loop already runs the Bybit gate pulse plus post-liq, timing/vol, tombstone, frontier, waiting-board and transition checks.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Safely dedupe redundant runtime observer loops")
    parser.add_argument("--apply", action="store_true", help="Stop the redundant Bybit-only loop when the replacement loop is healthy")
    parser.add_argument("--out-prefix", default="docs/RUNTIME_LOOP_DEDUPER_2026-07-03")
    args = parser.parse_args()

    real_status = read_json(REAL_EDGE_STATUS_PATH)
    real_lock = read_json(REAL_EDGE_LOCK_PATH)
    bybit_status = read_json(BYBIT_STATUS_PATH)

    replacement_healthy, replacement_blockers = healthy_real_edge_loop(real_status, real_lock)
    bybit_pid = bybit_status.get("pid") if isinstance(bybit_status, dict) else None
    bybit_alive_before = process_alive(bybit_pid)
    commandline = process_commandline(bybit_pid)
    commandline_matches = bool(commandline and "Run-BybitForwardGatePulseLoop.ps1" in commandline)

    blockers: list[str] = list(replacement_blockers)
    if not isinstance(bybit_status, dict):
        blockers.append("missing_bybit_loop_status")
    if bybit_alive_before and not commandline_matches:
        blockers.append("bybit_pid_commandline_mismatch")

    stop_result: dict[str, Any] = {"attempted": False, "ok": None}
    lock_removed = False
    status_rewritten = False

    decision = "runtime_loop_deduper_dry_run"
    if args.apply:
        if blockers:
            decision = "runtime_loop_deduper_blocked"
        elif not bybit_alive_before:
            decision = "runtime_loop_deduper_no_running_redundant_loop"
        else:
            stop_result = stop_process(int(bybit_pid))
            pid_alive_after_stop = process_alive(bybit_pid)
            if stop_result.get("ok") and not pid_alive_after_stop:
                decision = "runtime_loop_deduper_stopped_redundant_bybit_loop"
                if BYBIT_LOCK_PATH.exists():
                    BYBIT_LOCK_PATH.unlink()
                    lock_removed = True
                if isinstance(bybit_status, dict):
                    updated = dict(bybit_status)
                    updated.update(
                        {
                            "ts": now_iso(),
                            "status": "stopped_replaced_by_real_edge_observer_pulse_loop",
                            "exit_code": 0,
                            "pid_alive": False,
                            "previous_pid": bybit_pid,
                            "replacement_loop": str(REAL_EDGE_STATUS_PATH.relative_to(ROOT)),
                            "replacement_pid": real_status.get("pid") if isinstance(real_status, dict) else None,
                            "live_trading_locked": True,
                            "signals_allowed": False,
                            "paper_entries_allowed": False,
                            "orders_allowed": False,
                        }
                    )
                    write_json(BYBIT_STATUS_PATH, updated)
                    status_rewritten = True
            else:
                decision = "runtime_loop_deduper_stop_failed"
                blockers.append("stop_process_failed_or_pid_still_alive")

    bybit_alive_after = process_alive(bybit_pid)
    report = {
        "generated_at": now_iso(),
        "tool": "tools/runtime_loop_deduper.py",
        "decision": decision,
        "apply": bool(args.apply),
        "can_trade": False,
        "orders_allowed": False,
        "replacement_real_edge_loop": {
            "status": real_status.get("status") if isinstance(real_status, dict) else None,
            "pid": real_status.get("pid") if isinstance(real_status, dict) else None,
            "pid_alive": process_alive(real_status.get("pid")) if isinstance(real_status, dict) else False,
            "healthy": replacement_healthy,
            "blockers": replacement_blockers,
        },
        "redundant_bybit_loop": {
            "status_before": bybit_status.get("status") if isinstance(bybit_status, dict) else None,
            "pid": bybit_pid,
            "pid_alive_before": bybit_alive_before,
            "pid_alive_after": bybit_alive_after,
            "commandline": commandline,
            "commandline_matches_bybit_loop": commandline_matches,
            "stop_attempted": stop_result.get("attempted"),
            "stop_ok": stop_result.get("ok"),
            "stop_result": stop_result,
            "lock_removed": lock_removed,
            "status_rewritten": status_rewritten,
        },
        "blockers": blockers,
        "safety": {
            "does_not_start_strategy": True,
            "sends_telegram": False,
            "emits_signals": False,
            "opens_paper_entries": False,
            "sends_orders": False,
        },
    }

    out_prefix = ROOT / args.out_prefix
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_md(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "replacement_healthy": replacement_healthy,
                "bybit_pid_alive_before": bybit_alive_before,
                "bybit_pid_alive_after": bybit_alive_after,
                "blockers": blockers,
                "out": str(out_prefix.with_suffix(".json").relative_to(ROOT)),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not blockers and report["decision"] != "runtime_loop_deduper_stop_failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())

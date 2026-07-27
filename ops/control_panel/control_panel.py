from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "_dl" / "control_panel"
JOBS_DIR = OUT_DIR / "jobs"
SMOKE_DIR = ROOT / "_dl" / "smoke"
FUTURES_DIR = ROOT / "ops" / "btcusdt_binance_futures_bot"
FUTURES_SRC = FUTURES_DIR / "src"
RESEARCH_RUNTIME_SUPERVISOR_DIR = (
    ROOT / "HANDOFF" / "INCOMING" / "codex" / "20260712_research_runtime_supervisor"
)

SAFE_ENV_SCRUB = {
    "BINANCE_API_KEY": "",
    "BINANCE_API_SECRET": "",
    "PRIVATE_KEY": "",
    "WALLET_PRIVATE_KEY": "",
    "WALLET_MNEMONIC": "",
}

_RUNTIME_JOB_HANDLE: int | None = None
_WINDOWS_PROCESS_API: tuple[Any, Any] | None = None
RUNTIME_STARTUP_SNAPSHOT_STALE_AFTER_MINUTES = 30.0


def join_windows_runtime_job(component_id: str) -> None:
    """Contain this process and every future child in a named killable job."""
    global _RUNTIME_JOB_HANDLE
    if os.name != "nt" or _RUNTIME_JOB_HANDLE is not None:
        return

    from ctypes import wintypes

    class JobBasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JobExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JobBasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.IsProcessInJob.argtypes = (wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
    kernel32.IsProcessInJob.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    root_hash = hashlib.sha256(str(ROOT).lower().encode("utf-8")).digest()[:8].hex()
    job_name = f"Local\\TradingOS_Runtime_Job_{root_hash}_{component_id}"
    job = kernel32.CreateJobObjectW(None, job_name)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    try:
        limits = JobExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        current = kernel32.GetCurrentProcess()
        if not kernel32.AssignProcessToJobObject(job, current):
            in_this_job = wintypes.BOOL(False)
            if not kernel32.IsProcessInJob(current, job, ctypes.byref(in_this_job)) or not in_this_job.value:
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        _RUNTIME_JOB_HANDLE = int(job)
        job = None
    finally:
        if job:
            kernel32.CloseHandle(job)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> Any | None:
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


def tail_text(value: str, limit: int = 12000) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]


def _windows_process_api() -> tuple[Any, Any]:
    """Return explicitly typed process APIs; never rely on ctypes' implicit int ABI."""
    global _WINDOWS_PROCESS_API
    if _WINDOWS_PROCESS_API is not None:
        return _WINDOWS_PROCESS_API

    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    )
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    _WINDOWS_PROCESS_API = (kernel32, wintypes)
    return _WINDOWS_PROCESS_API


def _parse_process_creation_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _filetime_matches_creation(file_time: Any, expected: datetime) -> bool:
    # FILETIME uses 100 ns ticks since 1601-01-01. Python retains microseconds,
    # so allow at most one microsecond for a seventh fractional digit in .NET JSON.
    windows_ticks = (int(file_time.dwHighDateTime) << 32) | int(file_time.dwLowDateTime)
    actual_unix_ticks = windows_ticks - 116_444_736_000_000_000
    unix_epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = expected - unix_epoch
    expected_unix_ticks = (
        ((delta.days * 86_400) + delta.seconds) * 10_000_000
        + delta.microseconds * 10
    )
    return abs(actual_unix_ticks - expected_unix_ticks) <= 10


def process_alive(pid: Any, *, expected_creation_utc: Any | None = None) -> bool:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0 or pid_int > 0xFFFFFFFF:
        return False
    if os.name == "nt":
        handle = None
        close_ok = False
        alive_and_owned = False
        try:
            still_active = 259
            query_limited_information = 0x1000
            kernel32, wintypes = _windows_process_api()
            handle = kernel32.OpenProcess(query_limited_information, False, pid_int)
            if not handle:
                return False
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            if exit_code.value != still_active:
                return False

            if expected_creation_utc is not None:
                expected_creation = _parse_process_creation_utc(expected_creation_utc)
                if expected_creation is None:
                    return False
                creation = wintypes.FILETIME()
                exited = wintypes.FILETIME()
                kernel = wintypes.FILETIME()
                user = wintypes.FILETIME()
                if not kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(creation),
                    ctypes.byref(exited),
                    ctypes.byref(kernel),
                    ctypes.byref(user),
                ):
                    return False
                if not _filetime_matches_creation(creation, expected_creation):
                    return False
            alive_and_owned = True
        except Exception:  # noqa: BLE001 - health checks must fail closed on any native-call fault
            return False
        finally:
            if handle:
                try:
                    close_ok = bool(kernel32.CloseHandle(handle))
                except Exception:  # noqa: BLE001 - a failed close invalidates a positive health result
                    close_ok = False
        return alive_and_owned and close_ok

    # Creation-token verification is Windows-specific. Refuse to silently
    # downgrade an identity-bound query to a PID-only result on another OS.
    if expected_creation_utc is not None:
        return False
    try:
        os.kill(pid_int, 0)
    except OSError:
        return False
    return True


def _runtime_root_matches(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        candidate = os.path.normcase(str(Path(value).resolve(strict=False)))
        expected = os.path.normcase(str(ROOT.resolve(strict=False)))
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
    return candidate == expected


def runtime_report_process_alive(component_id: str, report: Any, lock: Any) -> bool:
    """Bind displayed health to one launcher-owned PID + creation identity."""
    if (
        not isinstance(component_id, str)
        or not component_id
        or Path(component_id).name != component_id
        or not isinstance(report, dict)
        or not isinstance(lock, dict)
    ):
        return False
    try:
        report_pid = int(report.get("pid"))
        lock_pid = int(lock.get("pid"))
    except (TypeError, ValueError):
        return False
    if report_pid <= 0 or report_pid != lock_pid:
        return False
    if not _runtime_root_matches(report.get("root")) or not _runtime_root_matches(lock.get("root")):
        return False

    receipt = read_json(ROOT / "logs" / "runtime_jobs" / f"{component_id}.json")
    if not isinstance(receipt, dict):
        return False
    try:
        receipt_pid = int(receipt.get("pid"))
    except (TypeError, ValueError):
        return False
    receipt_schema = receipt.get("schema_version")
    if (
        receipt_schema not in {1, 2}
        or receipt.get("component") != component_id
        or receipt_pid != report_pid
        or not _runtime_root_matches(receipt.get("root"))
        or receipt.get("live_trading_locked") is not True
        or receipt.get("can_trade") is not False
    ):
        return False
    if receipt_schema == 2:
        session_id = receipt.get("session_id")
        if (
            not isinstance(session_id, int)
            or isinstance(session_id, bool)
            or session_id < 0
            or receipt.get("launch_state") != "running"
        ):
            return False
    return process_alive(
        report_pid,
        expected_creation_utc=receipt.get("process_creation_utc"),
    )


def runtime_receipt_identity_summary(component_id: Any) -> dict[str, Any]:
    """Report current receipt identity without claiming native job containment."""
    result: dict[str, Any] = {
        "receipt_exists": False,
        "receipt_valid": False,
        "receipt_pid": None,
        "receipt_generated_at": None,
        "receipt_identity_alive": False,
    }
    if not isinstance(component_id, str) or not component_id or Path(component_id).name != component_id:
        return result

    receipt = read_json(ROOT / "logs" / "runtime_jobs" / f"{component_id}.json")
    if not isinstance(receipt, dict):
        return result
    result["receipt_exists"] = True
    result["receipt_generated_at"] = receipt.get("generated_at")
    try:
        receipt_pid = int(receipt.get("pid"))
    except (TypeError, ValueError):
        return result

    creation_utc = receipt.get("process_creation_utc")
    schema_version = receipt.get("schema_version")
    valid = (
        schema_version in {1, 2}
        and receipt.get("component") == component_id
        and receipt_pid > 0
        and _runtime_root_matches(receipt.get("root"))
        and receipt.get("live_trading_locked") is True
        and receipt.get("can_trade") is False
        and _parse_process_creation_utc(creation_utc) is not None
    )
    if schema_version == 2:
        valid = (
            valid
            and isinstance(receipt.get("session_id"), int)
            and not isinstance(receipt.get("session_id"), bool)
            and receipt.get("session_id") >= 0
            and receipt.get("launch_state") == "running"
        )

    result["receipt_pid"] = receipt_pid
    result["receipt_valid"] = bool(valid)
    result["receipt_identity_alive"] = bool(
        valid and process_alive(receipt_pid, expected_creation_utc=creation_utc)
    )
    return result


def ensure_dirs() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class TaskSpec:
    title: str
    description: str
    command: list[str]
    cwd: Path
    timeout_s: int = 180
    expected_exit_codes: tuple[int, ...] = (0,)
    env: dict[str, str] | None = None
    network_note: str = "local-only"


def python_cmd(*args: str) -> list[str]:
    return [sys.executable, *args]


TASKS: dict[str, TaskSpec] = {
    "smoke_pack": TaskSpec(
        title="Bounded smoke-pack",
        description="Проверяет preflight, BitEvo validator, v7 rule engine и risk-of-ruin.",
        command=[
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "run_bounded_smoke_pack.ps1"),
        ],
        cwd=ROOT,
        timeout_s=180,
    ),
    "trading_os_autostart_status": TaskSpec(
        title="Trading OS autostart status",
        description="Checks Startup-folder autostart, control panel status and forward 4H loop status.",
        command=[
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "ops" / "autostart" / "Get-TradingOSAutostartStatus.ps1"),
        ],
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local Windows/runtime status only; no orders; no private credentials",
    ),
    "cex_funding_freshness_watchdog": TaskSpec(
        title="CEX funding freshness watchdog",
        description="Fail-closed check of the funding collector PID, status, exits, aggregate/direct journals and stderr growth.",
        command=python_cmd(
            "tools/cex_funding_freshness_watchdog.py",
            "--contract",
            "configs/CEX_FUNDING_FRESHNESS_WATCHDOG_LOCK_2026-07-13.json",
            "--out-prefix",
            "docs/CEX_FUNDING_FRESHNESS_WATCHDOG_2026-07-13",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local operational health only; no restart, signals, orders or private credentials",
    ),
    "cex_funding_freshness_incident_alert": TaskSpec(
        title="CEX funding incident transition check",
        description="Records only healthy-to-blocked or blocked-to-recovered funding data incidents; manual run does not request Telegram delivery.",
        command=python_cmd(
            "tools/cex_funding_freshness_incident_alert.py",
            "--contract",
            "configs/CEX_FUNDING_FRESHNESS_INCIDENT_ALERT_LOCK_2026-07-13.json",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local transition record only; no Telegram request, restart, signals or orders",
    ),
    "cex_funding_freshness_incident_alert_drill": TaskSpec(
        title="CEX funding incident synthetic drill",
        description="Simulates baseline, blocked, duplicate suppression and recovery without touching Telegram or trading runtime.",
        command=python_cmd(
            "tools/cex_funding_freshness_incident_alert_drill.py",
            "--out",
            "docs/CEX_FUNDING_FRESHNESS_INCIDENT_ALERT_DRILL_2026-07-13.json",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="synthetic local drill only; no network, signals or orders",
    ),
    "trading_os_runtime_optimizer": TaskSpec(
        title="Trading OS runtime optimizer",
        description="Repairs safe autostart wiring, disables broken legacy scheduler and ensures panel/4H loop are running once.",
        command=[
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "ops" / "autostart" / "Optimize-TradingOSRuntime.ps1"),
        ],
        cwd=ROOT,
        timeout_s=90,
        env={"BOT_ENV": "demo"},
        network_note="local runtime maintenance only; no orders; no private credentials",
    ),
    "forward_runtime_health_check": TaskSpec(
        title="Forward runtime health check",
        description="Checks 4H loop freshness, latest scheduler exits, panel port, promotion gate and data-quality state.",
        command=python_cmd(
            "tools/forward_runtime_health_check.py",
            "--out-prefix",
            "docs/FORWARD_RUNTIME_HEALTH_2026-06-16",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local runtime health only; no orders; no private credentials",
    ),
    "forward_runtime_health_telegram_notify": TaskSpec(
        title="Forward runtime health Telegram notify",
        description="Checks whether runtime health needs a Telegram alert. Healthy state is skipped; missing Telegram env is non-fatal.",
        command=python_cmd(
            "tools/forward_runtime_health_telegram_notify.py",
            "--out-prefix",
            "docs/FORWARD_RUNTIME_HEALTH_TELEGRAM_NOTIFY_2026-06-16",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="Telegram only when configured; no orders; no private credentials",
    ),
    "forward_runtime_health_incident_drill": TaskSpec(
        title="Forward runtime health incident drill",
        description="Dry-run degraded/recovered Telegram alert drill using a synthetic health fixture. Sends nothing.",
        command=python_cmd(
            "tools/forward_runtime_health_incident_drill.py",
            "--out-prefix",
            "docs/FORWARD_RUNTIME_HEALTH_INCIDENT_DRILL_2026-06-16",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="dry-run only; no Telegram send; no orders; no private credentials",
    ),
    "telegram_transport_smoke": TaskSpec(
        title="Telegram transport smoke",
        description="Checks Telegram config and transport readiness. Default mode sends nothing; real send requires CLI --send.",
        command=python_cmd(
            "tools/telegram_transport_smoke.py",
            "--out-prefix",
            "docs/TELEGRAM_TRANSPORT_SMOKE_2026-06-16",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="config/transport check only; panel action does not send Telegram; no orders",
    ),
    "telegram_config_audit": TaskSpec(
        title="Telegram config audit",
        description="Audits Telegram config, bot identity and manifest secret exclusion without sending messages.",
        command=python_cmd(
            "tools/telegram_config_audit.py",
            "--out-prefix",
            "docs/TELEGRAM_CONFIG_AUDIT_2026-06-18",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="Telegram getMe check only; no message send; no orders; token/chat id are redacted",
    ),
    "tradingos_core_readiness_edge_report": TaskSpec(
        title="Core readiness / edge report",
        description="Aggregates data readiness, strategy observers, polygon state and next actions without adding Telegram work.",
        command=python_cmd(
            "tools/tradingos_core_readiness_edge_report.py",
            "--out-prefix",
            "docs/TRADINGOS_CORE_READINESS_EDGE_REPORT_2026-06-25",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local readiness aggregation only; no Telegram send; no orders; no private credentials",
    ),
    "unified_readiness_matrix": TaskSpec(
        title="Unified readiness matrix",
        description="One source-of-truth matrix for runtime, microstructure, liquidation feed, OI/funding, basis and strategy frontier.",
        command=python_cmd(
            "tools/unified_readiness_matrix.py",
            "--out-prefix",
            "docs/UNIFIED_READINESS_MATRIX_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local status aggregation only; no orders; no private credentials",
    ),
    "real_edge_readiness_matrix": TaskSpec(
        title="Real edge readiness matrix",
        description="Fail-closed status matrix for the real-edge classes: cross-venue microstructure and real liquidation feeds.",
        command=python_cmd(
            "tools/real_edge_readiness_matrix.py",
            "--out-prefix",
            "docs/REAL_EDGE_READINESS_MATRIX_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local status aggregation only; no signals, no paper entry intents, no orders, no private credentials",
    ),
    "real_edge_autopilot_guard": TaskSpec(
        title="Real edge autopilot guard",
        description="Idempotent fail-closed guard that waits for sealed microstructure snapshot or real liquidation events before research-only runners.",
        command=python_cmd(
            "tools/real_edge_autopilot_guard.py",
            "--refresh-matrix",
            "--out-prefix",
            "docs/REAL_EDGE_AUTOPILOT_GUARD_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={"BOT_ENV": "demo"},
        network_note="local guard only; does not pass --execute-ready, no signals, no paper entry intents, no orders, no private credentials",
    ),
    "real_edge_autopilot_guard_start_loop": TaskSpec(
        title="Start real edge autopilot guard loop",
        description="Starts the background fail-closed real-edge guard loop without execute-ready.",
        command=[
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "ops" / "autostart" / "Start-RealEdgeAutopilotGuardLoop.ps1"),
        ],
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local guard loop only; no --execute-ready, no signals, no paper entry intents, no orders, no private credentials",
    ),
    "tradingos_readiness_pulse": TaskSpec(
        title="TradingOS readiness pulse",
        description="Runs all current safe readiness/data-quality gates and writes one GO/NO-GO pulse report.",
        command=python_cmd(
            "tools/tradingos_readiness_pulse.py",
            "--out-prefix",
            "docs/TRADINGOS_READINESS_PULSE_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="local observability pulse only; no orders; no private credentials",
    ),
    "liquidation_force_order_data_quality": TaskSpec(
        title="Liquidation forceOrder data quality",
        description="Audits Binance USD-M forceOrder collector status and event-level JSONL rows. Empty feed is not-ready, not a failure.",
        command=python_cmd(
            "tools/liquidation_force_order_data_quality.py",
            "--out-prefix",
            "docs/LIQUIDATION_FORCE_ORDER_DATA_QUALITY_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local data-quality report only; no orders; no private credentials",
    ),
    "liquidation_force_order_start_collector_loop": TaskSpec(
        title="Start liquidation forceOrder collector loop",
        description="Starts the Binance USD-M forceOrder websocket collector loop as data-collector-only.",
        command=[
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "ops" / "autostart" / "Start-LiquidationForceOrderCollectorLoop.ps1"),
        ],
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="public Binance websocket data collector only; no orders; no private credentials",
    ),
    "liquidation_force_order_collector_watchdog": TaskSpec(
        title="Liquidation collector watchdog",
        description="Restarts stale/stopped Binance USD-M forceOrder collector loop and immediately reruns data-quality gates.",
        command=python_cmd(
            "tools/liquidation_force_order_collector_watchdog.py",
            "--out-prefix",
            "docs/LIQUIDATION_FORCE_ORDER_COLLECTOR_WATCHDOG_2026-06-30",
            "--data-quality-prefix",
            "docs/LIQUIDATION_FORCE_ORDER_DATA_QUALITY_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=90,
        env={"BOT_ENV": "demo"},
        network_note="public Binance websocket collector watchdog only; no orders; no private credentials",
    ),
    "liquidation_force_order_supervisor_summary": TaskSpec(
        title="Liquidation forceOrder supervisor summary",
        description="Summarizes forceOrder collector health, heartbeat freshness, stored events and rolling supervisor history.",
        command=python_cmd(
            "tools/liquidation_force_order_supervisor_summary.py",
            "--out-prefix",
            "docs/LIQUIDATION_FORCE_ORDER_SUPERVISOR_SUMMARY_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local supervisor report only; no alerts, no paper entry intents, no orders, no private credentials",
    ),
    "bybit_all_liquidation_start_collector_loop": TaskSpec(
        title="Bybit allLiquidation collector loop",
        description="Starts public Bybit V5 allLiquidation websocket collector loop for selected linear symbols.",
        command=[
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "ops" / "autostart" / "Start-BybitAllLiquidationCollectorLoop.ps1"),
        ],
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="public Bybit websocket data collector only; no orders; no private credentials",
    ),
    "bybit_all_liquidation_data_quality": TaskSpec(
        title="Bybit allLiquidation data quality",
        description="Validates Bybit V5 allLiquidation rows, heartbeat, parser errors and minimum research sample.",
        command=python_cmd(
            "tools/bybit_all_liquidation_data_quality.py",
            "--out-prefix",
            "docs/BYBIT_ALL_LIQUIDATION_DATA_QUALITY_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local Bybit data-quality report only; no orders; no private credentials",
    ),
    "bybit_all_liquidation_sample_gate": TaskSpec(
        title="Bybit allLiquidation sample gate",
        description="Refreshes Bybit data-quality, context intake and fixed-horizon event study, then gates sample size and context balance.",
        command=python_cmd(
            "tools/bybit_all_liquidation_sample_gate.py",
            "--refresh",
            "--data-quality-prefix",
            "docs/BYBIT_ALL_LIQUIDATION_DATA_QUALITY_2026-07-01",
            "--intake-prefix",
            "docs/BYBIT_ALL_LIQUIDATION_CONTEXT_INTAKE_2026-07-02_AFTER_PRICE_GAP_FILL",
            "--study-prefix",
            "docs/BYBIT_ALL_LIQUIDATION_EVENT_STUDY_2026-07-02_AFTER_PRICE_GAP_FILL",
            "--out-prefix",
            "docs/BYBIT_ALL_LIQUIDATION_SAMPLE_GATE_2026-07-02_AFTER_PRICE_GAP_FILL_EXPLICIT",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={"BOT_ENV": "demo"},
        network_note="local sample gate only; no alerts, no paper entry intents, no orders, no private credentials",
    ),
    "bybit_all_liquidation_collector_watchdog": TaskSpec(
        title="Bybit allLiquidation collector watchdog",
        description="Restarts stale/stopped Bybit allLiquidation collector loop and runs the sample gate.",
        command=python_cmd(
            "tools/bybit_all_liquidation_collector_watchdog.py",
            "--out-prefix",
            "docs/BYBIT_ALL_LIQUIDATION_COLLECTOR_WATCHDOG_2026-07-01",
            "--sample-gate-prefix",
            "docs/BYBIT_ALL_LIQUIDATION_SAMPLE_GATE_2026-07-02_AFTER_PRICE_GAP_FILL_EXPLICIT",
        ),
        cwd=ROOT,
        timeout_s=240,
        env={"BOT_ENV": "demo"},
        network_note="collector watchdog and research gate only; no alerts, no paper entry intents, no orders, no private credentials",
    ),
    "bybit_all_liquidation_watchdog_start_loop": TaskSpec(
        title="Start Bybit allLiquidation watchdog loop",
        description="Starts the background Bybit collector watchdog/sample-gate loop.",
        command=[
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "ops" / "autostart" / "Start-BybitAllLiquidationWatchdogLoop.ps1"),
        ],
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="starts local watchdog loop only; no orders; no private credentials",
    ),
    "bybit_all_liquidation_context_intake": TaskSpec(
        title="Bybit allLiquidation context intake",
        description="Aggregates real Bybit allLiquidation rows into symbol+bar context features for research-only event study.",
        command=python_cmd(
            "tools/bybit_all_liquidation_context_intake.py",
            "--symbols",
            "BTCUSDT,ETHUSDT,SOLUSDT,BCHUSDT",
            "--interval",
            "1h",
            "--out-prefix",
            "docs/BYBIT_ALL_LIQUIDATION_CONTEXT_INTAKE_2026-07-02_AFTER_PRICE_GAP_FILL",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local real Bybit liquidation intake only; no proxy rows, no paper entry intents, no orders, no private credentials",
    ),
    "bybit_all_liquidation_event_study": TaskSpec(
        title="Bybit allLiquidation event study",
        description="Runs fixed-horizon research-only study over Bybit allLiquidation context rows.",
        command=python_cmd(
            "tools/force_order_liquidation_event_study.py",
            "--context-csv",
            "docs/BYBIT_ALL_LIQUIDATION_CONTEXT_INTAKE_2026-07-02_AFTER_PRICE_GAP_FILL_bar_context.csv",
            "--allowed-sources",
            "bybit_v5_allLiquidation_websocket",
            "--source-label",
            "Bybit allLiquidation",
            "--symbols",
            "BTCUSDT,ETHUSDT,SOLUSDT,BCHUSDT",
            "--interval",
            "1h",
            "--horizons",
            "1,2,4",
            "--out-prefix",
            "docs/BYBIT_ALL_LIQUIDATION_EVENT_STUDY_2026-07-02_AFTER_PRICE_GAP_FILL",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local fixed-horizon Bybit liquidation research only; no optimization, no paper entry intents, no orders, no private credentials",
    ),
    "bybit_liquidation_event_study_review": TaskSpec(
        title="Bybit liquidation event-study review",
        description="Reviews the fixed-horizon Bybit event study and drafts one forward-only observer lock if a candidate is strong enough.",
        command=python_cmd(
            "tools/bybit_liquidation_event_study_review.py",
            "--event-study",
            "docs/BYBIT_ALL_LIQUIDATION_EVENT_STUDY_2026-07-02_AFTER_PRICE_GAP_FILL.json",
            "--out-prefix",
            "docs/BYBIT_LIQUIDATION_EVENT_STUDY_REVIEW_2026-07-02",
            "--lock-draft",
            "configs/BYBIT_LIQUIDATION_FORWARD_LOCK_DRAFT_2026-07-02.json",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local review only; drafts forward observer lock; no signals, paper intents, orders or private credentials",
    ),
    "bybit_liquidation_forward_observer": TaskSpec(
        title="Bybit liquidation forward observer",
        description="Scores post-lock Bybit liquidation events against the accepted observer-only forward lock.",
        command=python_cmd(
            "tools/bybit_liquidation_forward_observer.py",
            "--lock",
            "configs/BYBIT_LIQUIDATION_FORWARD_OBSERVER_LOCK_2026-07-02.json",
            "--out-prefix",
            "docs/BYBIT_LIQUIDATION_FORWARD_OBSERVER_2026-07-02",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local observer-only scoring; no alerts, no paper intents, no orders or private credentials",
    ),
    "post_liq_absorption_forward_observer_runner": TaskSpec(
        title="Post-liq absorption forward observer",
        description="Refreshes real Bybit liquidation context and scores the locked post-liquidation absorption + spot/perp bucket.",
        command=python_cmd(
            "tools/post_liquidation_absorption_forward_observer_runner.py",
            "--out-prefix",
            "docs/POST_LIQUIDATION_ABSORPTION_FORWARD_OBSERVER_RUNNER_2026-07-03",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={"BOT_ENV": "demo"},
        network_note="local observer-only refresh; no alerts, no paper intents, no orders or private credentials",
    ),
    "liquidation_timing_vol_forward_observer_runner": TaskSpec(
        title="Liquidation timing/vol forward observer",
        description="Refreshes real Bybit liquidation context and scores the locked timing + volatility continuation bucket.",
        command=python_cmd(
            "tools/liquidation_timing_vol_forward_observer_runner.py",
            "--out-prefix",
            "docs/LIQUIDATION_TIMING_VOL_FORWARD_OBSERVER_RUNNER_2026-07-03",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={"BOT_ENV": "demo"},
        network_note="local observer-only refresh; no alerts, no paper intents, no orders or private credentials",
    ),
    "bybit_liquidation_forward_gate_runner": TaskSpec(
        title="Bybit liquidation gate runner",
        description="Runs the accepted Bybit liquidation observer, progress monitor, review pack and live-data focus summary as one safe gate refresh.",
        command=python_cmd(
            "tools/bybit_liquidation_forward_gate_runner.py",
            "--out-prefix",
            "docs/BYBIT_LIQUIDATION_FORWARD_GATE_RUNNER_2026-07-03",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="local gate refresh only; no Telegram, no signals, no paper intents, no orders or private credentials",
    ),
    "bybit_liquidation_forward_gate_watcher": TaskSpec(
        title="Bybit liquidation gate watcher",
        description="Compares the latest Bybit gate runner state with the previous state and flags only real wait/pass/tombstone transitions.",
        command=python_cmd(
            "tools/bybit_liquidation_forward_gate_watcher.py",
            "--out-prefix",
            "docs/BYBIT_LIQUIDATION_FORWARD_GATE_WATCHER_2026-07-03",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local transition watcher only; no Telegram, no signals, no paper intents, no orders or private credentials",
    ),
    "bybit_liquidation_forward_gate_pulse": TaskSpec(
        title="Bybit liquidation gate pulse",
        description="One-click safe pulse: refreshes the Bybit gate runner, then runs the transition watcher and writes a compact status.",
        command=python_cmd(
            "tools/bybit_liquidation_forward_gate_pulse.py",
            "--out-prefix",
            "docs/BYBIT_LIQUIDATION_FORWARD_GATE_PULSE_2026-07-03",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={"BOT_ENV": "demo"},
        network_note="local pulse only; no Telegram, no signals, no paper intents, no orders or private credentials",
    ),
    "bybit_liquidation_forward_gate_pulse_start_loop": TaskSpec(
        title="Start Bybit gate pulse loop",
        description="Starts the background Bybit gate pulse loop that refreshes readiness every 15 minutes.",
        command=[
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "ops" / "autostart" / "Start-BybitForwardGatePulseLoop.ps1"),
            "-SleepSeconds",
            "900",
        ],
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="starts local readiness pulse loop only; no Telegram, no signals, no paper intents, no orders or private credentials",
    ),
    "bybit_forward_gate_pulse_loop_health": TaskSpec(
        title="Bybit gate pulse loop health",
        description="Audits the background Bybit gate pulse loop PID, freshness, safety flags and latest pulse report.",
        command=python_cmd(
            "tools/bybit_forward_gate_pulse_loop_health.py",
            "--out-prefix",
            "docs/BYBIT_FORWARD_GATE_PULSE_LOOP_HEALTH_2026-07-03",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local loop health audit only; no Telegram, no signals, no paper intents, no orders or private credentials",
    ),
    "real_edge_observer_pulse_start_loop": TaskSpec(
        title="Start real-edge observer pulse loop",
        description="Starts the background observer pulse loop that refreshes all current real-edge observer gates every 15 minutes.",
        command=[
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "ops" / "autostart" / "Start-RealEdgeObserverPulseLoop.ps1"),
            "-SleepSeconds",
            "900",
        ],
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="starts local observer pulse loop only; no Telegram, no signals, no paper intents, no orders or private credentials",
    ),
    "real_edge_observer_pulse_loop_health": TaskSpec(
        title="Real-edge observer pulse loop health",
        description="Audits the background real-edge observer pulse loop PID, freshness, safety flags and latest aggregate pulse report.",
        command=python_cmd(
            "tools/real_edge_observer_pulse_loop_health.py",
            "--out-prefix",
            "docs/REAL_EDGE_OBSERVER_PULSE_LOOP_HEALTH_2026-07-03",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local loop health audit only; no Telegram, no signals, no paper intents, no orders or private credentials",
    ),
    "runtime_loop_deduper": TaskSpec(
        title="Runtime loop deduper",
        description="Stops the redundant Bybit-only pulse loop only when the aggregate real-edge observer pulse loop is healthy.",
        command=python_cmd(
            "tools/runtime_loop_deduper.py",
            "--apply",
            "--out-prefix",
            "docs/RUNTIME_LOOP_DEDUPER_2026-07-03",
        ),
        cwd=ROOT,
        timeout_s=45,
        env={"BOT_ENV": "demo"},
        network_note="local process maintenance only; no Telegram, no signals, no paper intents, no orders or private credentials",
    ),
    "real_edge_transition_alert_monitor": TaskSpec(
        title="Real-edge transition alert monitor",
        description="Deduplicated observer-only monitor for Bybit, post-liq absorption, liquidation timing/vol and microstructure review transitions.",
        command=python_cmd(
            "tools/real_edge_transition_alert_monitor.py",
            "--out-prefix",
            "docs/REAL_EDGE_TRANSITION_ALERT_MONITOR_2026-07-03_LIQ_TIMING_VOL_INTEGRATED",
            "--tombstone-registry",
            "docs/EDGE_TOMBSTONE_REGISTRY_2026-07-03_AFTER_BYBIT_FORWARD_REVIEW.json",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local transition monitor only; Telegram send disabled unless CLI --send-telegram is used; no signals, no paper intents, no orders",
    ),
    "real_edge_transition_alert_monitor_drill": TaskSpec(
        title="Real-edge transition alert drill",
        description="Synthetic regression drill for wait/rerun/pass/tombstone/snapshot/post-liq/timing-vol transition routing. Sends nothing.",
        command=python_cmd(
            "tools/real_edge_transition_alert_monitor_drill.py",
            "--out-prefix",
            "docs/REAL_EDGE_TRANSITION_ALERT_MONITOR_DRILL_2026-07-03_LIQ_TIMING_VOL",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local synthetic fixture drill only; no Telegram, no signals, no paper intents, no orders or private credentials",
    ),
    "live_data_edge_focus_summary": TaskSpec(
        title="Live-data edge focus summary",
        description="Ranks current canonical Bybit V5R1, Binance forceOrder, microstructure, Deribit and funding gates without opening interim outcomes.",
        command=python_cmd(
            "tools/live_data_edge_focus_summary.py",
            "--out-prefix",
            "docs/LIVE_DATA_EDGE_FOCUS_SUMMARY_2026-07-03",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local readiness summary only; no Telegram, no paper intents, no orders or private credentials",
    ),
    "edge_waiting_board": TaskSpec(
        title="Edge waiting board",
        description="One read-only board showing current edge candidates, blockers, unlock conditions and next actions.",
        command=python_cmd(
            "tools/edge_waiting_board.py",
            "--out-prefix",
            "docs/EDGE_WAITING_BOARD_2026-07-03",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local read-only status board; no research run, no signals, no paper intents, no orders",
    ),
    "real_edge_observer_pulse": TaskSpec(
        title="Real-edge observer pulse",
        description="One safe pulse for liquidation, book-independence and exogenous-liquidity observers plus tombstones, frontier and waiting board.",
        command=python_cmd(
            "tools/real_edge_observer_pulse.py",
            "--out-prefix",
            "docs/REAL_EDGE_OBSERVER_PULSE_2026-07-03",
        ),
        cwd=ROOT,
        timeout_s=300,
        env={"BOT_ENV": "demo"},
        network_note="local observer pulse only; no Telegram, no signals, no paper intents, no orders",
    ),
    "bybit_liquidation_forward_progress_monitor": TaskSpec(
        title="Bybit liquidation forward progress",
        description="Tracks post-lock sample, horizon-resolution deficits and ETA for the accepted Bybit liquidation observer.",
        command=python_cmd(
            "tools/bybit_liquidation_forward_progress_monitor.py",
            "--out-prefix",
            "docs/BYBIT_LIQUIDATION_FORWARD_PROGRESS_2026-07-02",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local progress monitor only; no alerts, no paper intents, no orders or private credentials",
    ),
    "bybit_liquidation_forward_review_pack": TaskSpec(
        title="Bybit liquidation forward review pack",
        description="Builds the manual wait/pass/tombstone review pack for the accepted Bybit liquidation observer.",
        command=python_cmd(
            "tools/bybit_liquidation_forward_review_pack.py",
            "--out-prefix",
            "docs/BYBIT_LIQUIDATION_FORWARD_REVIEW_PACK_2026-07-02",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local review pack only; no signals, no paper intents, no orders or private credentials",
    ),
    "liquidation_multi_venue_coverage_summary": TaskSpec(
        title="Liquidation multi-venue coverage summary",
        description="Summarizes Binance forceOrder and Bybit allLiquidation feed coverage into one fail-closed report.",
        command=python_cmd(
            "tools/liquidation_multi_venue_coverage_summary.py",
            "--out-prefix",
            "docs/LIQUIDATION_MULTI_VENUE_COVERAGE_SUMMARY_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local coverage summary only; no alerts, no paper entry intents, no orders, no private credentials",
    ),
    "binance_rest_spot_tail_gap_fill_alt_1h": TaskSpec(
        title="Binance REST spot tail gap fill alt 1H",
        description="Fills recent ETH/SOL/BCH spot 1H kline cache gaps from public Binance REST before monthly Vision archives exist.",
        command=python_cmd(
            "tools/binance_rest_kline_tail_gap_filler.py",
            "--market",
            "spot",
            "--symbols",
            "ETHUSDT,SOLUSDT,BCHUSDT",
            "--interval",
            "1h",
            "--out-prefix",
            "docs/BINANCE_REST_SPOT_TAIL_GAP_FILL_2026-07-02",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="public Binance REST market-data only; no account endpoints, no private credentials, no orders",
    ),
    "binance_rest_futures_tail_gap_fill_alt_1h": TaskSpec(
        title="Binance REST futures tail gap fill alt 1H",
        description="Fills recent ETH/SOL/BCH USD-M futures 1H kline cache gaps from public Binance REST before monthly Vision archives exist.",
        command=python_cmd(
            "tools/binance_rest_kline_tail_gap_filler.py",
            "--market",
            "futures",
            "--symbols",
            "ETHUSDT,SOLUSDT,BCHUSDT",
            "--interval",
            "1h",
            "--out-prefix",
            "docs/BINANCE_REST_FUTURES_TAIL_GAP_FILL_2026-07-02",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="public Binance REST market-data only; no account endpoints, no private credentials, no orders",
    ),
    "liquidation_real_feed_status_refresh": TaskSpec(
        title="Liquidation real-feed status refresh",
        description="Refreshes Binance/Bybit liquidation data-quality, Bybit sample gate, multi-venue coverage, real-edge matrix and autopilot guard.",
        command=python_cmd(
            "tools/liquidation_real_feed_status_refresh.py",
            "--refresh-collectors",
            "--refresh-price-tail",
            "--out-prefix",
            "docs/LIQUIDATION_REAL_FEED_STATUS_REFRESH_2026-07-02_FORWARD_OBSERVER",
        ),
        cwd=ROOT,
        timeout_s=300,
        env={"BOT_ENV": "demo"},
        network_note="status refresh only; no alerts, no paper entry intents, no orders, no private credentials",
    ),
    "liquidation_sample_progress_monitor": TaskSpec(
        title="Liquidation sample progress monitor",
        description="Tracks real liquidation sample deficits, context balance and collection velocity against locked research gates.",
        command=python_cmd(
            "tools/liquidation_sample_progress_monitor.py",
            "--out-prefix",
            "docs/LIQUIDATION_SAMPLE_PROGRESS_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local progress monitor only; no alerts, no paper entry intents, no orders, no private credentials",
    ),
    "liquidation_sample_ready_trigger": TaskSpec(
        title="Liquidation sample ready trigger",
        description="State-only trigger that records the first time real liquidation sample gates become ready for manual review.",
        command=python_cmd(
            "tools/liquidation_sample_ready_trigger.py",
            "--out-prefix",
            "docs/LIQUIDATION_SAMPLE_READY_TRIGGER_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="state-only trigger; no alerts, no paper entry intents, no orders, no private credentials",
    ),
    "liquidation_real_feed_status_refresh_start_loop": TaskSpec(
        title="Start liquidation real-feed status refresh loop",
        description="Starts the background liquidation real-feed status refresh loop.",
        command=[
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "ops" / "autostart" / "Start-LiquidationRealFeedStatusRefreshLoop.ps1"),
        ],
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="starts local status-refresh loop only; no orders; no private credentials",
    ),
    "liquidation_force_order_first_event_trigger": TaskSpec(
        title="Liquidation first-event trigger",
        description="Checks whether the first real Binance forceOrder event has appeared and records a one-time state.",
        command=python_cmd(
            "tools/liquidation_force_order_first_event_trigger.py",
            "--out-prefix",
            "docs/LIQUIDATION_FORCE_ORDER_FIRST_EVENT_TRIGGER_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local trigger/state check only; no Telegram send; no orders",
    ),
    "liquidation_force_order_first_event_auto_run_guard": TaskSpec(
        title="Liquidation first-event auto-run guard",
        description="Runs data-quality and idempotently launches the forceOrder research pipeline once after the first real event.",
        command=python_cmd(
            "tools/liquidation_force_order_first_event_auto_run_guard.py",
            "--run-data-quality",
            "--out-prefix",
            "docs/LIQUIDATION_FORCE_ORDER_FIRST_EVENT_AUTO_RUN_GUARD_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={"BOT_ENV": "demo"},
        network_note="local first-event research guard only; no alerts, no paper entry intents, no orders, no private credentials",
    ),
    "force_order_liquidation_context_intake": TaskSpec(
        title="ForceOrder liquidation context intake",
        description="Aggregates real Binance forceOrder event rows into bar-level context features; waits fail-closed when feed is empty.",
        command=python_cmd(
            "tools/force_order_liquidation_context_intake.py",
            "--interval",
            "1h",
            "--out-prefix",
            "docs/FORCE_ORDER_LIQUIDATION_CONTEXT_INTAKE_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local real-feed intake only; no proxy rows, no paper entry intents, no orders, no private credentials",
    ),
    "force_order_liquidation_context_intake_multisymbol": TaskSpec(
        title="ForceOrder liquidation context intake multi-symbol",
        description="Aggregates real Binance forceOrder rows across the collector breadth into symbol+bar context features.",
        command=python_cmd(
            "tools/force_order_liquidation_context_intake.py",
            "--interval",
            "1h",
            "--symbols",
            "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,LINKUSDT,LTCUSDT",
            "--out-prefix",
            "docs/FORCE_ORDER_LIQUIDATION_CONTEXT_INTAKE_MULTISYMBOL_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local real-feed breadth intake only; no proxy rows, no paper entry intents, no orders, no private credentials",
    ),
    "force_order_liquidation_context_intake_drill": TaskSpec(
        title="ForceOrder context intake drill",
        description="Synthetic fixture drill for the forceOrder context intake plumbing; writes only under _dl/runtime_drills.",
        command=python_cmd(
            "tools/force_order_liquidation_context_intake_drill.py",
            "--out-prefix",
            "docs/FORCE_ORDER_CONTEXT_INTAKE_DRILL_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=90,
        env={"BOT_ENV": "demo"},
        network_note="synthetic fixture plumbing drill only; no live feed writes, no paper entry intents, no orders, no private credentials",
    ),
    "force_order_liquidation_event_study": TaskSpec(
        title="ForceOrder liquidation event study",
        description="Research-only fixed-horizon event study over forceOrder context rows; waits fail-closed until real context CSV exists.",
        command=python_cmd(
            "tools/force_order_liquidation_event_study.py",
            "--out-prefix",
            "docs/FORCE_ORDER_LIQUIDATION_EVENT_STUDY_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local fixed-horizon research only; no optimization, no paper entry intents, no orders, no private credentials",
    ),
    "force_order_liquidation_research_pipeline": TaskSpec(
        title="ForceOrder liquidation research pipeline",
        description="Runs forceOrder intake and fixed-horizon event study as one research-only pipeline with a single status report.",
        command=python_cmd(
            "tools/force_order_liquidation_research_pipeline.py",
            "--out-prefix",
            "docs/FORCE_ORDER_LIQUIDATION_RESEARCH_PIPELINE_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="local research orchestrator only; no optimization, no paper entry intents, no orders, no private credentials",
    ),
    "microstructure_sealed_snapshot_pack": TaskSpec(
        title="Microstructure sealed snapshot pack",
        description="Builds a sealed snapshot pack when the microstructure snapshot is ready; otherwise writes a waiting report.",
        command=python_cmd(
            "tools/microstructure_sealed_snapshot_pack.py",
            "--out-prefix",
            "docs/CROSS_VENUE_MICROSTRUCTURE_SEALED_SNAPSHOT_PACK_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local snapshot metadata only; no orders; no private credentials",
    ),
    "microstructure_book_coverage_diagnostic": TaskSpec(
        title="Microstructure book coverage diagnostic",
        description="Diagnoses dual-venue top-of-book minute coverage and ETA before snapshot sealing.",
        command=python_cmd(
            "tools/cross_venue_microstructure_book_coverage_diagnostic.py",
            "--out-prefix",
            "docs/CROSS_VENUE_MICROSTRUCTURE_BOOK_COVERAGE_DIAGNOSTIC_2026-07-03_PANEL",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local SQLite observability only; no signals, no paper entry intents, no orders, no private credentials",
    ),
    "microstructure_book_only_collector": TaskSpec(
        title="Microstructure book-only collector",
        description="Runs one lightweight public top-of-book collection cycle for Binance/Coinbase BTC books.",
        command=python_cmd(
            "tools/cross_venue_microstructure_book_only_collector.py",
            "--report-prefix",
            "docs/CROSS_VENUE_MICROSTRUCTURE_BOOK_ONLY_COLLECTOR_2026-07-03_PANEL",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="public top-of-book collection only; no signals, no paper entry intents, no orders, no private credentials",
    ),
    "microstructure_book_loop_start": TaskSpec(
        title="Start microstructure book loop",
        description="Starts a lightweight background loop that collects Binance/Coinbase top-of-book snapshots every 20 seconds.",
        command=[
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "ops" / "autostart" / "Start-CrossVenueMicrostructureBookLoop.ps1"),
            "-SleepSeconds",
            "20",
        ],
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="starts public book-snapshot collector only; no signals, no paper entry intents, no orders, no private credentials",
    ),
    "microstructure_unblock_status": TaskSpec(
        title="Microstructure unblock status",
        description="Refreshes safe microstructure health/SLA/snapshot gates and explains what still blocks snapshot sealing.",
        command=python_cmd(
            "tools/microstructure_unblock_status.py",
            "--refresh",
            "--out-prefix",
            "docs/MICROSTRUCTURE_UNBLOCK_STATUS_2026-07-03",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={"BOT_ENV": "demo"},
        network_note="local observability only; no signals, no paper entry intents, no orders, no private credentials",
    ),
    "microstructure_unblock_status_start_loop": TaskSpec(
        title="Start microstructure unblock status loop",
        description="Starts a background observability loop that refreshes microstructure unblock status every 15 minutes.",
        command=[
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "ops" / "autostart" / "Start-MicrostructureUnblockStatusLoop.ps1"),
        ],
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="starts local observability loop only; no signals, no paper entry intents, no orders, no private credentials",
    ),
    "edge_tombstone_registry": TaskSpec(
        title="Edge tombstone registry",
        description="Registers rejected strategy families so they cannot be accidentally retuned or promoted.",
        command=python_cmd(
            "tools/edge_tombstone_registry.py",
            "--out-prefix",
            "docs/EDGE_TOMBSTONE_REGISTRY_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local registry only; no orders; no private credentials",
    ),
    "anti_loop_state_map": TaskSpec(
        title="Anti-loop state map + Hermes prompt",
        description="Builds a compact built/tested/rejected/waiting map and a prompt that prevents repeated bot work.",
        command=python_cmd(
            "tools/anti_loop_state_map.py",
            "--out-prefix",
            "docs/ANTI_LOOP_STATE_MAP_2026-06-30",
            "--prompt-out",
            "docs/HERMES_TRADING_BOT_ANTI_LOOP_PROMPT_2026-06-30.md",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local governance report only; no orders; no private credentials",
    ),
    "blocker_transition_monitor": TaskSpec(
        title="Blocker transition monitor",
        description="Compares source-of-truth blocker state against the previous run and highlights real transitions.",
        command=python_cmd(
            "tools/blocker_transition_monitor.py",
            "--out-prefix",
            "docs/BLOCKER_TRANSITION_MONITOR_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local observability report only; no orders; no private credentials",
    ),
    "derivatives_event_edge_miner": TaskSpec(
        title="Derivatives-event edge miner",
        description="Tests predeclared OI/funding event hypotheses with train/validation/OOS gates.",
        command=python_cmd(
            "tools/derivatives_event_edge_miner.py",
            "--intervals",
            "1h,4h",
            "--max-configs-per-interval",
            "300",
            "--validation-top",
            "25",
            "--out-prefix",
            "docs/DERIVATIVES_EVENT_EDGE_MINER_2026-06-25",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="local historical research only; no Telegram send; no orders; no private credentials",
    ),
    "derivatives_event_regime_filter_test": TaskSpec(
        title="Derivatives-event regime filter test",
        description="Tests the 4H LONG OI-build continuation branch with predeclared EMA regime filters.",
        command=python_cmd(
            "tools/derivatives_event_edge_miner.py",
            "--intervals",
            "4h",
            "--families",
            "oi_build_continuation",
            "--sides",
            "LONG",
            "--regime-filters",
            "ema200_slope,ema50_stack",
            "--lookbacks",
            "6",
            "--price-atr",
            "0.8",
            "--oi-pct",
            "0.25",
            "--funding-abs",
            "0.0002",
            "--volume-z",
            "0,1.0,1.8",
            "--close-location",
            "0.55,0.65",
            "--take-atr",
            "3.0",
            "--max-hold-bars",
            "8,16",
            "--max-configs-per-interval",
            "100",
            "--validation-top",
            "20",
            "--out-prefix",
            "docs/DERIVATIVES_EVENT_EDGE_MINER_REGIME_2026-06-26",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local regime-filter research only; no Telegram send; no orders; no private credentials",
    ),
    "derivatives_event_runtime_drift_audit": TaskSpec(
        title="Derivatives-event runtime drift audit",
        description="Compares source package and Active runtime data/report drift before accepting any derivatives-event candidate.",
        command=python_cmd(
            "tools/derivatives_event_runtime_drift_audit.py",
            "--runtime-root",
            r"C:\Users\coins\TradingOS\Active",
            "--out-prefix",
            "docs/DERIVATIVES_EVENT_RUNTIME_DRIFT_AUDIT_2026-06-26",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local file comparison only; no Telegram send; no orders; no private credentials",
    ),
    "derivatives_event_forward_observer": TaskSpec(
        title="Derivatives-event forward observer",
        description="Observer-only latest-bar check for the selected OI/funding derivatives-event candidate.",
        command=python_cmd(
            "tools/derivatives_event_forward_observer.py",
            "--miner-report",
            "docs/DERIVATIVES_EVENT_EDGE_MINER_REGIME_2026-06-26.json",
            "--journal-path",
            "logs/forward_paper_feed/derivatives_event_forward_observer.jsonl",
            "--state-path",
            "logs/forward_paper_feed/derivatives_event_forward_observer_state.json",
            "--latest-card-json",
            "logs/forward_paper_feed/latest_derivatives_event_card.json",
            "--latest-card-md",
            "logs/forward_paper_feed/latest_derivatives_event_card.md",
            "--out-prefix",
            "docs/DERIVATIVES_EVENT_FORWARD_OBSERVER_2026-06-26",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="observer-only latest-bar check; no Telegram send; no paper entry intents; no orders; no private credentials",
    ),
    "derivatives_event_pending_watch": TaskSpec(
        title="Derivatives-event pending watch",
        description="Explains latest-bar readiness and blockers for the selected derivatives-event candidate without writing signals.",
        command=python_cmd(
            "tools/derivatives_event_pending_watch.py",
            "--miner-report",
            "docs/DERIVATIVES_EVENT_EDGE_MINER_REGIME_2026-06-26.json",
            "--out-prefix",
            "docs/DERIVATIVES_EVENT_PENDING_WATCH_2026-06-27",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local pending-watch diagnostics only; no Telegram send; no paper entry intents; no orders; no private credentials",
    ),
    "derivatives_event_forward_scoreboard": TaskSpec(
        title="Derivatives-event forward scoreboard",
        description="Scores resolved observer-only outcomes for the selected derivatives-event candidate.",
        command=python_cmd(
            "tools/derivatives_event_forward_scoreboard.py",
            "--miner-report",
            "docs/DERIVATIVES_EVENT_EDGE_MINER_REGIME_2026-06-26.json",
            "--journal-path",
            "logs/forward_paper_feed/derivatives_event_forward_observer.jsonl",
            "--out-prefix",
            "docs/DERIVATIVES_EVENT_FORWARD_SCOREBOARD_2026-06-26",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local observer outcome scoring only; no Telegram send; no paper entry intents; no orders; no private credentials",
    ),
    "derivatives_event_promotion_gate": TaskSpec(
        title="Derivatives-event promotion gate",
        description="Blocks promotion until enough independent derivatives-event forward outcomes are resolved.",
        command=python_cmd(
            "tools/derivatives_event_promotion_gate.py",
            "--miner-report",
            "docs/DERIVATIVES_EVENT_EDGE_MINER_REGIME_2026-06-26.json",
            "--observer",
            "docs/DERIVATIVES_EVENT_FORWARD_OBSERVER_2026-06-26.json",
            "--scoreboard",
            "docs/DERIVATIVES_EVENT_FORWARD_SCOREBOARD_2026-06-26.json",
            "--out-prefix",
            "docs/DERIVATIVES_EVENT_PROMOTION_GATE_2026-06-26",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local promotion gate only; no Telegram send; no paper entry intents; no orders; no private credentials",
    ),
    "derivatives_event_telegram_notify": TaskSpec(
        title="Derivatives-event Telegram notify",
        description="Sends a watch-only Telegram alert only when the derivatives-event observer wrote a new signal.",
        command=python_cmd(
            "tools/derivatives_event_telegram_notify.py",
            "--observer-json-path",
            "docs/DERIVATIVES_EVENT_FORWARD_OBSERVER_2026-06-26.json",
            "--scoreboard-json-path",
            "docs/DERIVATIVES_EVENT_FORWARD_SCOREBOARD_2026-06-26.json",
            "--gate-json-path",
            "docs/DERIVATIVES_EVENT_PROMOTION_GATE_2026-06-26.json",
            "--state-path",
            "logs/forward_paper_feed/derivatives_event_telegram_notify_state.json",
            "--card-json-path",
            "logs/forward_paper_feed/latest_derivatives_event_watch_card.json",
            "--card-md-path",
            "logs/forward_paper_feed/latest_derivatives_event_watch_card.md",
            "--out-prefix",
            "docs/DERIVATIVES_EVENT_TELEGRAM_NOTIFY_2026-06-26",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="Telegram watch alert only on a newly written observer signal; no paper entry intents; no orders; no exchange credentials",
    ),
    "derivatives_event_signal_alert_drill": TaskSpec(
        title="Derivatives-event signal alert drill",
        description="Synthetic dry-run proof that derivatives-event Telegram watch alerts render and dedupe correctly.",
        command=python_cmd(
            "tools/derivatives_event_signal_alert_drill.py",
            "--drill-dir",
            "_dl/runtime_drills/derivatives_event_signal_alert_drill",
            "--out-prefix",
            "docs/DERIVATIVES_EVENT_SIGNAL_ALERT_DRILL_2026-06-26",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="synthetic dry-run only; no Telegram send; no paper entry intents; no orders; no private credentials",
    ),
    "derivatives_event_research_matrix": TaskSpec(
        title="Derivatives-event research matrix",
        description="Aggregates fresh/focused derivatives-event miner reports and shows whether any OOS-promotable edge exists.",
        command=python_cmd(
            "tools/derivatives_event_research_matrix.py",
            "--out-prefix",
            "docs/DERIVATIVES_EVENT_RESEARCH_MATRIX_2026-06-29",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local research summary only; no Telegram send; no paper entry intents; no orders; no private credentials",
    ),
    "context_evidence_matrix": TaskSpec(
        title="Context evidence matrix",
        description="Aggregates liquidation, spot/perp and spot-led context evidence before allowing any derivatives-event integration.",
        command=python_cmd(
            "tools/context_evidence_matrix.py",
            "--out-prefix",
            "docs/CONTEXT_EVIDENCE_MATRIX_2026-06-29",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local context evidence summary only; no Telegram send; no paper entry intents; no orders; no private credentials",
    ),
    "derivatives_context_composite_miner_4h": TaskSpec(
        title="Derivatives context composite miner 4H",
        description="Runs a bounded precommitted 4H derivatives + spot/perp + sweep/liquidation-proxy nested holdout.",
        command=python_cmd(
            "tools/derivatives_context_composite_miner.py",
            "--intervals",
            "4h",
            "--families",
            "oi_build_continuation,funding_extreme_fade,deleveraging_reversal,squeeze_exhaustion_fade",
            "--sides",
            "LONG,SHORT",
            "--regime-filters",
            "none,ema200_slope,ema50_stack",
            "--context-modes",
            "spot_confirm,spot_volume_confirm,sweep_confirm,liq_proxy,composite2",
            "--lookbacks",
            "6,12",
            "--price-atr",
            "0.4,0.6",
            "--oi-pct",
            "0.15,0.25",
            "--funding-abs",
            "0.0001,0.0002",
            "--close-location",
            "0.55,0.65",
            "--spot-divergence-pct",
            "0,0.02",
            "--spot-volume-ratio",
            "0.2,0.5",
            "--sweep-lookback",
            "12,24",
            "--take-atr",
            "1.5,2.0,3.0",
            "--max-hold-bars",
            "8,16",
            "--max-configs-per-interval",
            "400",
            "--validation-top",
            "80",
            "--out-prefix",
            "docs/DERIVATIVES_CONTEXT_COMPOSITE_MINER_4H_BOUNDED_2026-06-29",
        ),
        cwd=ROOT,
        timeout_s=90,
        env={"BOT_ENV": "demo"},
        network_note="local precommitted nested holdout only; no Telegram send; no paper entry intents; no orders; no private credentials",
    ),
    "update_manifest": TaskSpec(
        title="Update MANIFEST safely",
        description="Rebuilds MANIFEST.json while excluding runtime outputs and local secret env files.",
        command=python_cmd("tools/update_manifest.py"),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local file hash manifest only; excludes configs/telegram.env; no orders",
    ),
    "futures_tests": TaskSpec(
        title="Futures unit tests",
        description="Полный локальный pytest futures-модуля. Без сети и без ордеров.",
        command=python_cmd("-m", "pytest"),
        cwd=FUTURES_DIR,
        timeout_s=180,
        env={"PYTHONPATH": str(FUTURES_SRC), "BOT_ENV": "demo"},
    ),
    "futures_market_manifest": TaskSpec(
        title="Futures market manifest",
        description="Показывает planned Binance futures streams и локальный маршрут данных.",
        command=python_cmd("-m", "btcusdt_bot", "market-manifest"),
        cwd=FUTURES_DIR,
        timeout_s=60,
        env={"PYTHONPATH": str(FUTURES_SRC), "BOT_ENV": "demo"},
        network_note="no orders; no private credentials",
    ),
    "futures_plan_example": TaskSpec(
        title="Futures order plan example",
        description="Рендерит пример entry + stop/take-profit плана. Ничего не отправляет.",
        command=python_cmd(
            "-m",
            "btcusdt_bot",
            "plan-example",
            "--side",
            "BUY",
            "--mark-price",
            "65000",
            "--qty",
            "0.001",
            "--atr",
            "450",
        ),
        cwd=FUTURES_DIR,
        timeout_s=60,
        env={"PYTHONPATH": str(FUTURES_SRC), "BOT_ENV": "demo"},
    ),
    "futures_backtest_smoke": TaskSpec(
        title="Futures backtest smoke",
        description="Прогоняет маленький deterministic backtest по bundled markPrice fixture.",
        command=python_cmd(
            "-m",
            "btcusdt_bot",
            "backtest-breakout",
            "--lookback",
            "3",
            "--atr-window",
            "2",
            "--position-notional",
            "100",
            "--spread-bps",
            "0",
            "--taker-slippage-bps",
            "0",
            "--maker-fee-bps",
            "0",
            "--taker-fee-bps",
            "0",
        ),
        cwd=FUTURES_DIR,
        timeout_s=90,
        env={"PYTHONPATH": str(FUTURES_SRC), "BOT_ENV": "demo"},
        network_note="uses local fixture; may fetch exchange filters when available",
    ),
    "futures_backtest_readiness": TaskSpec(
        title="Futures backtest readiness",
        description="Audits local futures JSONL coverage and recommends mark-only or multistream backtest mode.",
        command=python_cmd(
            "-m",
            "btcusdt_bot",
            "backtest-readiness",
            "--mark-only",
            "--ignore-contract-status",
        ),
        cwd=FUTURES_DIR,
        timeout_s=60,
        env={"PYTHONPATH": str(FUTURES_SRC), "BOT_ENV": "demo"},
        network_note="local fixture audit only; no orders; no private credentials",
    ),
    "futures_walkforward_smoke": TaskSpec(
        title="Futures walk-forward smoke",
        description="Runs a tiny mark-only walk-forward grid to prove the optimizer path is callable.",
        command=python_cmd(
            "-m",
            "btcusdt_bot",
            "walkforward-breakout",
            "--mark-only",
            "--ignore-contract-status",
            "--train-days",
            "2",
            "--test-days",
            "1",
            "--step-days",
            "1",
            "--max-folds",
            "2",
            "--max-candidates",
            "4",
            "--lookback-grid",
            "3,5",
            "--hold-seconds-grid",
            "60,300",
            "--position-notional",
            "100",
            "--spread-bps",
            "0",
            "--taker-slippage-bps",
            "0",
            "--maker-fee-bps",
            "0",
            "--taker-fee-bps",
            "0",
        ),
        cwd=FUTURES_DIR,
        timeout_s=120,
        env={"PYTHONPATH": str(FUTURES_SRC), "BOT_ENV": "demo"},
        network_note="local walk-forward smoke only; no orders; no private credentials",
    ),
    "flow_toxicity_demo": TaskSpec(
        title="Flow toxicity demo",
        description="Builds a research-only order-flow toxicity feature report from synthetic data.",
        command=python_cmd(
            "tools/flow_toxicity_feature_report.py",
            "--demo",
            "--out-prefix",
            "docs/FLOW_TOXICITY_FEATURE_REPORT_2026-06-08",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="synthetic data only; no orders; no private credentials",
    ),
    "futures_public_cache_smoke": TaskSpec(
        title="Futures public cache smoke",
        description="Collects bounded public futures streams and builds a real flow-toxicity report.",
        command=python_cmd(
            "tools/futures_public_cache_smoke.py",
            "--market-messages",
            "20",
            "--book-messages",
            "20",
            "--depth-messages",
            "50",
            "--out-prefix",
            "docs/FUTURES_PUBLIC_CACHE_SMOKE_2026-06-08",
            "--flow-out-prefix",
            "docs/FLOW_TOXICITY_FEATURE_REPORT_REAL_2026-06-08",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={
            "BOT_ENV": "demo",
            "BOT_REST_BASE_URL": "https://fapi.binance.com",
            "BOT_WS_PUBLIC_BASE_URL": "wss://fstream.binance.com/public",
            "BOT_WS_MARKET_BASE_URL": "wss://fstream.binance.com/market",
            "DATA_DIR": "data/public_live_smoke",
        },
        network_note="public Binance futures data only; no orders; no private credentials",
    ),
    "futures_public_capture_2m": TaskSpec(
        title="Futures public capture 2m",
        description="Runs a 2-minute public-only capture session into isolated public_live_capture data.",
        command=python_cmd(
            "tools/futures_public_capture_session.py",
            "--duration-seconds",
            "120",
            "--data-dir",
            "data/public_live_capture",
            "--crowding-interval-seconds",
            "30",
            "--min-mark-lines",
            "20",
            "--min-agg-trade-lines",
            "20",
            "--min-book-ticker-lines",
            "20",
            "--min-local-depth-lines",
            "20",
            "--min-crowding-lines",
            "1",
            "--out-prefix",
            "docs/FUTURES_PUBLIC_CAPTURE_SESSION_2026-06-08",
            "--flow-out-prefix",
            "docs/FLOW_TOXICITY_FEATURE_REPORT_CAPTURE_2026-06-08",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={
            "BOT_ENV": "demo",
            "BOT_REST_BASE_URL": "https://fapi.binance.com",
            "BOT_WS_PUBLIC_BASE_URL": "wss://fstream.binance.com/public",
            "BOT_WS_MARKET_BASE_URL": "wss://fstream.binance.com/market",
            "DATA_DIR": "data/public_live_capture",
        },
        network_note="public Binance futures data only; no orders; no private credentials",
    ),
    "futures_public_capture_30m": TaskSpec(
        title="Futures public capture 30m",
        description="Runs a 30-minute public-only capture session for better order-flow sample size.",
        command=python_cmd(
            "tools/futures_public_capture_session.py",
            "--duration-seconds",
            "1800",
            "--data-dir",
            "data/public_live_capture",
            "--crowding-interval-seconds",
            "30",
            "--min-mark-lines",
            "600",
            "--min-agg-trade-lines",
            "600",
            "--min-book-ticker-lines",
            "600",
            "--min-local-depth-lines",
            "600",
            "--min-crowding-lines",
            "20",
            "--out-prefix",
            "docs/FUTURES_PUBLIC_CAPTURE_SESSION_30M_2026-06-08",
            "--flow-out-prefix",
            "docs/FLOW_TOXICITY_FEATURE_REPORT_CAPTURE_30M_2026-06-08",
        ),
        cwd=ROOT,
        timeout_s=2100,
        env={
            "BOT_ENV": "demo",
            "BOT_REST_BASE_URL": "https://fapi.binance.com",
            "BOT_WS_PUBLIC_BASE_URL": "wss://fstream.binance.com/public",
            "BOT_WS_MARKET_BASE_URL": "wss://fstream.binance.com/market",
            "DATA_DIR": "data/public_live_capture",
        },
        network_note="public Binance futures data only; long-running; no orders; no private credentials",
    ),
    "strategy_discovery_pipeline_5": TaskSpec(
        title="Strategy discovery pipeline",
        description="Processes 5 unhandled workspace/Downloads candidates into the plus-EV strategy backlog.",
        command=python_cmd(
            "tools/strategy_discovery_pipeline.py",
            "--limit",
            "5",
            "--out-prefix",
            "docs/STRATEGY_DISCOVERY_PIPELINE_2026-06-09_PANEL",
            "--backlog",
            "docs/STRATEGY_DISCOVERY_BACKLOG_2026-06-08.md",
            "--registry",
            "docs/STRATEGY_DISCOVERY_REGISTRY_2026-06-08.json",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={"BOT_ENV": "demo"},
        network_note="local document processing only; no orders; no private credentials",
    ),
    "strategy_hypothesis_compiler": TaskSpec(
        title="Strategy hypothesis compiler",
        description="Compiles processed strategy documents into ranked coding/backtest hypotheses.",
        command=python_cmd(
            "tools/strategy_hypothesis_compiler.py",
            "--registry",
            "docs/STRATEGY_DISCOVERY_REGISTRY_2026-06-08.json",
            "--out-prefix",
            "docs/STRATEGY_HYPOTHESIS_QUEUE_2026-06-09",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local research queue only; no orders; no private credentials",
    ),
    "targeted_strategy_rule_extractor": TaskSpec(
        title="Targeted rule extractor",
        description="Extracts codable rule cards from high-value strategy docs and separates alpha, guard and process-only items.",
        command=python_cmd(
            "tools/targeted_strategy_rule_extractor.py",
            "--registry",
            "docs/STRATEGY_DISCOVERY_REGISTRY_2026-06-08.json",
            "--out-prefix",
            "docs/TARGETED_STRATEGY_RULE_EXTRACTOR_2026-06-18",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local research extraction only; no paper entry intents; no orders; no private credentials",
    ),
    "parallel_edge_search_pass": TaskSpec(
        title="Parallel edge search pass",
        description="Runs bounded extraction, top-N rule-card subset creation and RR 1:3 batch testing as one research-only pass.",
        command=python_cmd(
            "tools/parallel_edge_search_pass.py",
            "--skip-discovery",
            "--tag",
            "PANEL_PARALLEL_EDGE_SEARCH_2026-07-01",
            "--top-cards",
            "3",
            "--workers",
            "2",
            "--batch-timeout-s",
            "180",
            "--out-prefix",
            "docs/PARALLEL_EDGE_SEARCH_PASS_PANEL_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=240,
        env={"BOT_ENV": "demo"},
        network_note="local research orchestration only; no Telegram send, no paper entry intents, no orders, no private credentials",
    ),
    "document_rule_card_batch_tester_rr1x3": TaskSpec(
        title="Document rule-card batch tester RR 1:3",
        description="Tests codable document-derived rule cards as a deterministic RR 1:3 research batch against local BTCUSDT cache.",
        command=python_cmd(
            "tools/document_rule_card_batch_tester.py",
            "--rule-cards",
            "docs/TARGETED_STRATEGY_RULE_EXTRACTOR_PARALLEL_SEARCH_2026-06-30.json",
            "--out-prefix",
            "docs/DOCUMENT_RULE_CARD_BATCH_TEST_RR1X3_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=900,
        env={"BOT_ENV": "demo"},
        network_note="local deterministic research batch only; no paper entry intents; no orders; no private credentials",
    ),
    "document_rule_candidate_diagnostics_rr1x3": TaskSpec(
        title="Document rule candidate diagnostics RR 1:3",
        description="Rebuilds the best document-derived RR 1:3 candidate, exports all trades and explains fold/regime failure modes.",
        command=python_cmd(
            "tools/document_rule_candidate_diagnostics.py",
            "--strategy-id",
            "doc_rule_ad70abbc50_spot_confirm_1h",
            "--out-prefix",
            "docs/DOCUMENT_RULE_CANDIDATE_DIAGNOSTICS_RR1X3_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={"BOT_ENV": "demo"},
        network_note="local research diagnostics only; no paper entry intents; no orders; no private credentials",
    ),
    "document_rule_filter_probe_rr1x3": TaskSpec(
        title="Document rule filter probe RR 1:3",
        description="Post-hoc diagnostic probe for fixed regime filters on the best document-derived RR 1:3 candidate.",
        command=python_cmd(
            "tools/document_rule_filter_probe.py",
            "--out-prefix",
            "docs/DOCUMENT_RULE_FILTER_PROBE_RR1X3_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local post-hoc diagnostics only; no paper entry intents; no orders; no private credentials",
    ),
    "document_rule_preregistered_validation_rr1x3": TaskSpec(
        title="Document rule prereg validation RR 1:3",
        description="Runs the frozen spot-confirm + volume-active RR 1:3 validation split. Design-review only, not live permission.",
        command=python_cmd(
            "tools/document_rule_preregistered_validation.py",
            "--out-prefix",
            "docs/DOCUMENT_RULE_PREREG_VALIDATION_VOLUME_ACTIVE_RR1X3_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={"BOT_ENV": "demo"},
        network_note="local preregistered research validation only; no paper entry intents; no orders; no private credentials",
    ),
    "document_rule_forward_observer_rr1x3": TaskSpec(
        title="Document rule forward observer RR 1:3",
        description="Watch-only latest-bar observer for the frozen spot-confirm + volume-active RR 1:3 hypothesis.",
        command=python_cmd(
            "tools/document_rule_forward_observer.py",
            "--out-prefix",
            "docs/DOCUMENT_RULE_FORWARD_OBSERVER_VOLUME_ACTIVE_RR1X3_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local/watch-only forward observer; no paper entry intents; no orders; no private credentials",
    ),
    "document_rule_forward_observer_volz_oi_rr1x3": TaskSpec(
        title="Document rule forward observer volume+OI RR 1:3",
        description="Watch-only observer for the no-leakage guard candidate: volume_z>=0.5 and oi_delta_pct>=1.0.",
        command=python_cmd(
            "tools/document_rule_forward_observer.py",
            "--guard-profile",
            "volume_z_oi_delta",
            "--journal-path",
            "logs/document_rule_forward_observer/signals_volume_z_oi_delta.jsonl",
            "--latest-card-path",
            "logs/document_rule_forward_observer/latest_signal_card_volume_z_oi_delta.json",
            "--state-path",
            "logs/document_rule_forward_observer/state_volume_z_oi_delta.json",
            "--out-prefix",
            "docs/DOCUMENT_RULE_FORWARD_OBSERVER_VOLZ05_OI1_RR1X3_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local/watch-only forward observer; no paper entry intents; no orders; no private credentials",
    ),
    "document_rule_forward_telegram_notify": TaskSpec(
        title="Document rule forward Telegram notify",
        description="Sends a watch-only Telegram notification only when the document-rule forward observer emits a new watch signal.",
        command=python_cmd(
            "tools/document_rule_forward_telegram_notify.py",
            "--out-prefix",
            "docs/DOCUMENT_RULE_FORWARD_TELEGRAM_NOTIFY_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="Telegram watch notification only; no paper entry intents; no orders; no private credentials",
    ),
    "document_rule_forward_telegram_notify_volz_oi": TaskSpec(
        title="Document rule forward Telegram notify volume+OI",
        description="Sends a watch-only Telegram notification only when the volume+OI observer emits a new watch signal.",
        command=python_cmd(
            "tools/document_rule_forward_telegram_notify.py",
            "--card-path",
            "logs/document_rule_forward_observer/latest_signal_card_volume_z_oi_delta.json",
            "--state-path",
            "logs/document_rule_forward_observer/telegram_notify_state_volume_z_oi_delta.json",
            "--out-prefix",
            "docs/DOCUMENT_RULE_FORWARD_TELEGRAM_NOTIFY_VOLZ05_OI1_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="Telegram watch notification only; no paper entry intents; no orders; no private credentials",
    ),
    "document_rule_forward_signal_drill_volz_oi": TaskSpec(
        title="Document rule signal drill volume+OI",
        description="Synthetic watch-signal drill for the volume+OI observer notify path. Dry-run only; sends nothing.",
        command=python_cmd(
            "tools/document_rule_forward_signal_drill.py",
            "--profile",
            "volume_z_oi_delta",
            "--out-prefix",
            "docs/DOCUMENT_RULE_FORWARD_SIGNAL_DRILL_VOLZ05_OI1_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="synthetic dry-run notify drill only; no Telegram send; no paper entry intents; no orders; no private credentials",
    ),
    "document_rule_forward_scoreboard": TaskSpec(
        title="Document rule forward scoreboard",
        description="Scores resolved outcomes from the document-rule watch-only forward observer journal.",
        command=python_cmd(
            "tools/document_rule_forward_scoreboard.py",
            "--out-prefix",
            "docs/DOCUMENT_RULE_FORWARD_SCOREBOARD_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local forward outcome scoring only; no paper entry intents; no orders; no private credentials",
    ),
    "document_rule_forward_scoreboard_volz_oi": TaskSpec(
        title="Document rule forward scoreboard volume+OI",
        description="Scores resolved outcomes from the volume_z>=0.5 and oi_delta_pct>=1.0 watch-only observer journal.",
        command=python_cmd(
            "tools/document_rule_forward_scoreboard.py",
            "--journal-path",
            "logs/document_rule_forward_observer/signals_volume_z_oi_delta.jsonl",
            "--out-prefix",
            "docs/DOCUMENT_RULE_FORWARD_SCOREBOARD_VOLZ05_OI1_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local forward outcome scoring only; no paper entry intents; no orders; no private credentials",
    ),
    "crypto_guides_web_ingest": TaskSpec(
        title="Crypto Guides web ingest",
        description="Ingests cryptoguidessite.vercel.app guides into a research/test queue without accepting claims as proven edge.",
        command=python_cmd(
            "tools/crypto_guides_web_ingest.py",
            "--out-prefix",
            "docs/CRYPTO_GUIDES_WEB_INGEST_2026-06-19",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="public website ingest only; no paper entry intents; no orders; no private credentials",
    ),
    "binance_crowd_positioning_collector": TaskSpec(
        title="Binance crowd positioning collector",
        description="Collects public Binance futures long/short ratio history for BTCUSDT crowd-fade research.",
        command=python_cmd(
            "tools/binance_crowd_positioning_collector.py",
            "--symbol",
            "BTCUSDT",
            "--intervals",
            "1h",
            "--pages",
            "6",
            "--limit",
            "500",
            "--out-prefix",
            "docs/BINANCE_CROWD_POSITIONING_COLLECTOR_2026-06-19",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="public Binance futures-data only; no keys; no orders; no private credentials",
    ),
    "crowd_fade_positioning_diagnostic": TaskSpec(
        title="Crowd-fade positioning diagnostic",
        description="Tests public long/short ratio fade hypotheses against cached BTCUSDT OHLCV/OI/funding.",
        command=python_cmd(
            "tools/crowd_fade_positioning_diagnostic.py",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--intervals",
            "15m,1h,4h",
            "--out-prefix",
            "docs/CROWD_FADE_POSITIONING_DIAGNOSTIC_2026-06-19",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={"BOT_ENV": "demo"},
        network_note="local/public-data research diagnostic only; no paper entry intents; no orders; no private credentials",
    ),
    "crowd_fade_nested_holdout": TaskSpec(
        title="Crowd Fade nested holdout",
        description="Selects parameters on pre-2025 train data only and validates one winner on untouched 2025+ OOS data.",
        command=python_cmd(
            "tools/crowd_fade_nested_holdout.py",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--split-time",
            "2025-01-01T00:00:00+00:00",
            "--out-prefix",
            "docs/CROWD_FADE_NESTED_HOLDOUT_2026-06-23",
        ),
        cwd=ROOT,
        timeout_s=300,
        env={"BOT_ENV": "demo"},
        network_note="local nested holdout research only; never changes candidate lock or sends orders",
    ),
    "crowd_fade_positioning_shadow_observer": TaskSpec(
        title="Crowd-fade positioning shadow observer",
        description="Fail-closed observer for the frozen Crowd candidate; currently paused by historical rejection lock.",
        command=python_cmd(
            "tools/crowd_fade_positioning_shadow_observer.py",
            "--diagnostic",
            "docs/CROWD_FADE_POSITIONING_DIAGNOSTIC_2026-06-19.json",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--out-prefix",
            "docs/CROWD_FADE_POSITIONING_SHADOW_OBSERVER_2026-06-19",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="observer-only latest-bar check; no paper entry intents; no orders; no private credentials",
    ),
    "crowd_fade_positioning_shadow_scoreboard": TaskSpec(
        title="Crowd-fade positioning shadow scoreboard",
        description="Scores resolved outcomes from the crowd-fade positioning shadow observer journal.",
        command=python_cmd(
            "tools/crowd_fade_positioning_shadow_scoreboard.py",
            "--journal-path",
            "logs/forward_paper_feed/crowd_fade_positioning_shadow_observer.jsonl",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--out-prefix",
            "docs/CROWD_FADE_POSITIONING_SHADOW_SCOREBOARD_2026-06-19",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local observer scoreboard only; no paper entry intents; no orders; no private credentials",
    ),
    "crowd_fade_positioning_telegram_notify": TaskSpec(
        title="Crowd-fade Telegram watch notify",
        description="Sends a Telegram watch-only alert if the crowd-fade observer has a new signal.",
        command=python_cmd(
            "tools/crowd_fade_positioning_telegram_notify.py",
            "--observer-json-path",
            "docs/CROWD_FADE_POSITIONING_SHADOW_OBSERVER_2026-06-19.json",
            "--scoreboard-json-path",
            "docs/CROWD_FADE_POSITIONING_SHADOW_SCOREBOARD_2026-06-19.json",
            "--out-prefix",
            "docs/CROWD_FADE_POSITIONING_TELEGRAM_NOTIFY_2026-06-19",
            "--message-prefix",
            "CROWD-FADE WATCH - observer-only. No entry, no paper intent, no orders.",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="Telegram watch alert only when observer has a signal; no paper entry intents; no orders; no exchange credentials",
    ),
    "crowd_fade_positioning_telegram_drill": TaskSpec(
        title="Crowd-fade Telegram drill",
        description="Dry-runs a synthetic crowd-fade observer signal through the Telegram watch notifier.",
        command=python_cmd(
            "tools/crowd_fade_positioning_telegram_drill.py",
            "--out-prefix",
            "docs/CROWD_FADE_POSITIONING_TELEGRAM_DRILL_2026-06-19",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="synthetic dry-run only; no real Telegram send; no paper entry intents; no orders; no exchange credentials",
    ),
    "crowd_fade_positioning_promotion_gate": TaskSpec(
        title="Crowd-fade promotion gate",
        description="Blocks crowd-fade promotion until enough resolved forward outcomes prove the observer signal.",
        command=python_cmd(
            "tools/crowd_fade_positioning_promotion_gate.py",
            "--out-prefix",
            "docs/CROWD_FADE_POSITIONING_PROMOTION_GATE_2026-06-19",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local evidence gate only; no paper entry intents; no orders; no exchange credentials",
    ),
    "crowd_fade_refresh_pack": TaskSpec(
        title="Crowd-fade refresh pack",
        description="Refreshes public BTCUSDT OHLCV/OI/funding, crowd-positioning cache and observer in one bounded pass.",
        command=python_cmd(
            "tools/crowd_fade_refresh_pack.py",
            "--symbol",
            "BTCUSDT",
            "--intervals",
            "15m,1h,4h",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--out-prefix",
            "docs/CROWD_FADE_REFRESH_PACK_2026-06-19",
        ),
        cwd=ROOT,
        timeout_s=540,
        env={"BOT_ENV": "demo"},
        network_note="public data refresh plus observer-only check; no paper entry intents; no orders; no private credentials",
    ),
    "active_strategy_runtime_inventory": TaskSpec(
        title="Active strategy runtime map",
        description="Shows the four independent observer families and verifies watchdog coverage of every executable scheduler step.",
        command=python_cmd(
            "tools/active_strategy_runtime_inventory.py",
            "--out-prefix",
            "docs/ACTIVE_STRATEGY_RUNTIME_MAP_2026-06-22",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local runtime inventory only; no paper entry intents; no orders; no private credentials",
    ),
    "four_family_forward_portfolio_scoreboard": TaskSpec(
        title="Four-family forward scoreboard",
        description="Compares independent resolved R, sample gates and promotion state across all four observer families.",
        command=python_cmd(
            "tools/four_family_forward_portfolio_scoreboard.py",
            "--out-prefix",
            "docs/FOUR_FAMILY_FORWARD_PORTFOLIO_SCOREBOARD_2026-06-22",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local evidence scoreboard only; no paper/live permission; no orders",
    ),
    "forward_evidence_lifecycle_controller": TaskSpec(
        title="Forward evidence lifecycle",
        description="Applies precommitted observe, pause, reject and paper-design-review states to all four strategy families.",
        command=python_cmd(
            "tools/forward_evidence_lifecycle_controller.py",
            "--scoreboard",
            "docs/FOUR_FAMILY_FORWARD_PORTFOLIO_SCOREBOARD_2026-06-22.json",
            "--policy",
            "configs/FORWARD_EVIDENCE_LIFECYCLE.json",
            "--out-prefix",
            "docs/FORWARD_EVIDENCE_LIFECYCLE_2026-06-23",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local lifecycle classification only; fixed thresholds; no entry intents; no orders",
    ),
    "edge_registry": TaskSpec(
        title="Edge registry",
        description="Aggregates research JSON reports into a ranked edge registry with holdout/fold/cost-stress gates.",
        command=python_cmd(
            "tools/edge_registry.py",
            "--out-prefix",
            "docs/EDGE_REGISTRY_2026-06-18",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="local research registry only; no paper entry intents; no orders; no active strategy changes",
    ),
    "edge_class_sweep_summary": TaskSpec(
        title="Edge class sweep summary",
        description="Summarizes the latest independent edge-class sweep and separates rejected classes from validation-starved candidates.",
        command=python_cmd(
            "tools/edge_class_sweep_summary.py",
            "--out-prefix",
            "docs/EDGE_CLASS_SWEEP_SUMMARY_2026-07-02_NEXT",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local research summary only; no paper entry intents, no orders, no private credentials",
    ),
    "basis_funding_carry_event_scarcity": TaskSpec(
        title="Basis/funding carry event scarcity",
        description="Diagnostic-only map showing whether the frozen basis/funding carry candidate has validation or recent forward events.",
        command=python_cmd(
            "tools/basis_funding_carry_event_scarcity_diagnostic.py",
            "--source-report",
            "docs/BASIS_FUNDING_CARRY_MULTI_SYMBOL_NESTED_HOLDOUT_2026-07-02_REFRESHED.json",
            "--out-prefix",
            "docs/BASIS_FUNDING_CARRY_EVENT_SCARCITY_2026-07-02",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="local diagnostic only; frozen config; no parameter optimization, no paper entry intents, no orders",
    ),
    "edge_forward_candidate_export": TaskSpec(
        title="Edge forward candidate export",
        description="Exports the top strict edge-registry candidate into a refiner-compatible observer input.",
        command=python_cmd(
            "tools/edge_forward_candidate_export.py",
            "--out-prefix",
            "docs/EDGE_FORWARD_CANDIDATE_REFINER_2026-06-18",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local export only; does not change active strategy; no paper entry intents; no orders",
    ),
    "edge_forward_range_observer": TaskSpec(
        title="Edge forward RANGE observer",
        description="Runs observer-only latest-bar check for the exported strict edge candidate.",
        command=python_cmd(
            "tools/range_refined_forward_observer.py",
            "--refiner-report",
            "docs/EDGE_FORWARD_CANDIDATE_REFINER_2026-06-18.json",
            "--journal-path",
            "logs/forward_paper_feed/edge_forward_range_observer.jsonl",
            "--state-path",
            "logs/forward_paper_feed/edge_forward_range_observer_state.json",
            "--out-prefix",
            "docs/EDGE_FORWARD_RANGE_OBSERVER_2026-06-18",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="observer-only forward proof; no paper entry intents; no orders",
    ),
    "edge_forward_pending_watch": TaskSpec(
        title="Edge forward pending watch",
        description="Shows proximity for the exported strict edge candidate without creating signals.",
        command=python_cmd(
            "tools/range_refined_pending_watch_monitor.py",
            "--refiner-report",
            "docs/EDGE_FORWARD_CANDIDATE_REFINER_2026-06-18.json",
            "--journal-path",
            "logs/forward_paper_feed/edge_forward_pending_watch.jsonl",
            "--out-prefix",
            "docs/EDGE_FORWARD_PENDING_WATCH_2026-06-18",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="observer-only proximity monitor; no paper entry intents; no orders",
    ),
    "edge_forward_range_scoreboard": TaskSpec(
        title="Edge forward RANGE scoreboard",
        description="Scores observer-only events for the exported strict edge candidate.",
        command=python_cmd(
            "tools/range_refined_observer_scoreboard.py",
            "--journal-path",
            "logs/forward_paper_feed/edge_forward_range_observer.jsonl",
            "--out-prefix",
            "docs/EDGE_FORWARD_RANGE_SCOREBOARD_2026-06-18",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local observer scoreboard only; no paper entry intents; no orders",
    ),
    "edge_forward_pending_watch_telegram_notify": TaskSpec(
        title="Edge forward pending-watch Telegram",
        description="Sends a warning-only Telegram pre-alert for the strict edge candidate only when proximity status is notifiable.",
        command=python_cmd(
            "tools/range_refined_pending_watch_telegram_notify.py",
            "--pending-watch-json-path",
            "docs/EDGE_FORWARD_PENDING_WATCH_2026-06-18.json",
            "--state-path",
            "logs/forward_paper_feed/edge_forward_pending_watch_telegram_state.json",
            "--card-json-path",
            "logs/forward_paper_feed/latest_edge_forward_pending_watch_card.json",
            "--card-md-path",
            "logs/forward_paper_feed/latest_edge_forward_pending_watch_card.md",
            "--out-prefix",
            "docs/EDGE_FORWARD_PENDING_WATCH_TELEGRAM_NOTIFY_2026-06-18",
            "--message-prefix",
            "EDGE FORWARD WATCH - observer-only strict candidate. No entry, no paper intent, no orders.",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="Telegram warning gate only; no paper entry intents; no orders",
    ),
    "edge_forward_promotion_gate": TaskSpec(
        title="Edge forward promotion gate",
        description="Blocks the strict edge candidate from paper-design review until enough real forward observer outcomes exist.",
        command=python_cmd(
            "tools/edge_forward_promotion_gate.py",
            "--out-prefix",
            "docs/EDGE_FORWARD_PROMOTION_GATE_2026-06-18",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local evidence gate only; no paper entry intents; no orders",
    ),
    "edge_candidate_hardening_diagnostic": TaskSpec(
        title="Edge candidate hardening diagnostic",
        description="Compares the current strict edge candidate against same-base and same-shape variants; keeps it observer-only.",
        command=python_cmd(
            "tools/edge_candidate_hardening_diagnostic.py",
            "--out-prefix",
            "docs/EDGE_CANDIDATE_HARDENING_DIAGNOSTIC_2026-06-19",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local research diagnostic only; no paper entry intents; no orders; no private credentials",
    ),
    "edge_same_shape_shadow_observer": TaskSpec(
        title="Edge same-shape shadow observer",
        description="Observer-only comparison of top same-shape RR/hold/filter variants beside the active edge candidate.",
        command=python_cmd(
            "tools/edge_same_shape_shadow_observer.py",
            "--top-n",
            "12",
            "--out-prefix",
            "docs/EDGE_SAME_SHAPE_SHADOW_OBSERVER_2026-06-19",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="observer-only shadow comparison; no paper entry intents; no orders; no private credentials",
    ),
    "edge_same_shape_shadow_scoreboard": TaskSpec(
        title="Edge same-shape shadow scoreboard",
        description="Scores resolved observer-only outcomes for same-shape shadow variants using their own RR/hold parameters.",
        command=python_cmd(
            "tools/edge_same_shape_shadow_scoreboard.py",
            "--out-prefix",
            "docs/EDGE_SAME_SHAPE_SHADOW_SCOREBOARD_2026-06-19",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local observer scoreboard only; no paper entry intents; no orders; no private credentials",
    ),
    "edge_compression_guard_diagnostic": TaskSpec(
        title="Edge compression guard diagnostic",
        description="Tests Compression / No-Man's-Land guard variants against the current edge candidate without changing active logic.",
        command=python_cmd(
            "tools/edge_compression_guard_diagnostic.py",
            "--out-prefix",
            "docs/EDGE_COMPRESSION_GUARD_DIAGNOSTIC_2026-06-19",
        ),
        cwd=ROOT,
        timeout_s=90,
        env={"BOT_ENV": "demo"},
        network_note="local research diagnostic only; no paper entry intents; no orders; no private credentials",
    ),
    "edge_compression_guard_shadow_observer": TaskSpec(
        title="Edge compression guard shadow observer",
        description="Observer-only keep/veto monitor for the low-sample compression guard on the active edge candidate.",
        command=python_cmd(
            "tools/edge_compression_guard_shadow_observer.py",
            "--out-prefix",
            "docs/EDGE_COMPRESSION_GUARD_SHADOW_OBSERVER_2026-06-19",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="observer-only guard shadow; no paper entry intents; no orders; no private credentials",
    ),
    "edge_compression_guard_shadow_scoreboard": TaskSpec(
        title="Edge compression guard shadow scoreboard",
        description="Scores keep/veto outcomes for the observer-only compression guard shadow.",
        command=python_cmd(
            "tools/edge_compression_guard_shadow_scoreboard.py",
            "--out-prefix",
            "docs/EDGE_COMPRESSION_GUARD_SHADOW_SCOREBOARD_2026-06-19",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local observer scoreboard only; no paper entry intents; no orders; no private credentials",
    ),
    "doc_h1_oi_funding_pressure": TaskSpec(
        title="DOC H1 OI+funding test",
        description="Tests the top document-derived OI/funding pressure hypothesis on cached BTCUSDT data.",
        command=python_cmd(
            "tools/max_v16_event_first_miner.py",
            "--use-cache",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--interval",
            "1h",
            "--htf-interval",
            "4h",
            "--out-prefix",
            "docs/DOC_H1_OI_FUNDING_PRESSURE_2026-06-09",
        ),
        cwd=ROOT,
        timeout_s=300,
        env={"BOT_ENV": "demo"},
        network_note="cached public BTCUSDT data only; no orders; no private credentials",
    ),
    "doc_h1_oi_funding_pressure_15m": TaskSpec(
        title="DOC H1 OI+funding 15m test",
        description="Cross-checks the document-derived OI/funding pressure hypothesis on 15m with 1h context.",
        command=python_cmd(
            "tools/max_v16_event_first_miner.py",
            "--use-cache",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--interval",
            "15m",
            "--htf-interval",
            "1h",
            "--out-prefix",
            "docs/DOC_H1_OI_FUNDING_PRESSURE_15m_2026-06-09",
        ),
        cwd=ROOT,
        timeout_s=300,
        env={"BOT_ENV": "demo"},
        network_note="cached public BTCUSDT data only; no orders; no private credentials",
    ),
    "doc_h1_oi_funding_pressure_4h": TaskSpec(
        title="DOC H1 OI+funding 4h test",
        description="Cross-checks the document-derived OI/funding pressure hypothesis on 4h context.",
        command=python_cmd(
            "tools/max_v16_event_first_miner.py",
            "--use-cache",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--interval",
            "4h",
            "--htf-interval",
            "4h",
            "--out-prefix",
            "docs/DOC_H1_OI_FUNDING_PRESSURE_4h_2026-06-09",
        ),
        cwd=ROOT,
        timeout_s=300,
        env={"BOT_ENV": "demo"},
        network_note="cached public BTCUSDT data only; no orders; no private credentials",
    ),
    "strategy_mix_combo_tester": TaskSpec(
        title="Strategy mix combo tester",
        description="Runs bounded mixed feature/strategy combo grid with RR variants including 1:3.",
        command=python_cmd(
            "tools/strategy_mix_combo_tester.py",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--intervals",
            "15m,1h,4h",
            "--rr",
            "1:1,1:1.5,1:2,1:3",
            "--max-holds",
            "8,12,16",
            "--max-combos-per-side",
            "80",
            "--workers",
            "8",
            "--out-prefix",
            "docs/STRATEGY_MIX_COMBO_TESTER_2026-06-08",
        ),
        cwd=ROOT,
        timeout_s=360,
        env={"BOT_ENV": "demo"},
        network_note="cached public BTCUSDT data only; no orders; no private credentials",
    ),
    "strategy_mix_holdout_validation": TaskSpec(
        title="Strategy mix holdout validation",
        description="Runs temporal holdout validation for mined mix-combo candidates.",
        command=python_cmd(
            "tools/strategy_mix_holdout_validator.py",
            "--source-report",
            "docs/STRATEGY_MIX_COMBO_TESTER_2026-06-08.json",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--top",
            "25",
            "--holdout-fraction",
            "0.25",
            "--out-prefix",
            "docs/STRATEGY_MIX_HOLDOUT_VALIDATION_2026-06-08",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={"BOT_ENV": "demo"},
        network_note="cached public BTCUSDT data only; no orders; no private credentials",
    ),
    "strategy_mix_deep_validation": TaskSpec(
        title="Strategy mix deep validation",
        description="Stress-tests holdout-positive mix strategies with segments, cost stress, perturbation and trade export.",
        command=python_cmd(
            "tools/strategy_mix_deep_validator.py",
            "--source-report",
            "docs/STRATEGY_MIX_HOLDOUT_VALIDATION_2026-06-08.json",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--top",
            "3",
            "--segments",
            "6",
            "--stress-bps",
            "0,5,10,15",
            "--out-prefix",
            "docs/STRATEGY_MIX_DEEP_VALIDATION_2026-06-08",
        ),
        cwd=ROOT,
        timeout_s=240,
        env={"BOT_ENV": "demo"},
        network_note="cached public BTCUSDT data only; no orders; no private credentials",
    ),
    "strategy_mix_guard_optimizer": TaskSpec(
        title="Strategy mix guard optimizer",
        description="Tests guard filters for 4H breakout candidates against holdout, cost stress and bad segments.",
        command=python_cmd(
            "tools/strategy_mix_guard_optimizer.py",
            "--source-report",
            "docs/STRATEGY_MIX_DEEP_VALIDATION_2026-06-08.json",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--top",
            "3",
            "--max-guard-size",
            "2",
            "--out-prefix",
            "docs/STRATEGY_MIX_GUARD_OPTIMIZER_2026-06-08",
        ),
        cwd=ROOT,
        timeout_s=240,
        env={"BOT_ENV": "demo"},
        network_note="cached public BTCUSDT data only; no orders; no private credentials",
    ),
    "strategy_mix_guard_deep_validation": TaskSpec(
        title="Strategy mix guard deep validation",
        description="Deep-validates guard optimizer winners before any paper-only replay work.",
        command=python_cmd(
            "tools/strategy_mix_deep_validator.py",
            "--source-report",
            "docs/STRATEGY_MIX_GUARD_OPTIMIZER_2026-06-08.json",
            "--candidate-verdicts",
            "guard_candidate_needs_deep",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--top",
            "5",
            "--segments",
            "6",
            "--stress-bps",
            "0,5,10,15",
            "--out-prefix",
            "docs/STRATEGY_MIX_GUARD_DEEP_VALIDATION_2026-06-08",
        ),
        cwd=ROOT,
        timeout_s=240,
        env={"BOT_ENV": "demo"},
        network_note="cached public BTCUSDT data only; no orders; no private credentials",
    ),
    "strategy_mix_paper_replay": TaskSpec(
        title="Strategy mix paper replay",
        description="Replays the locked 4H guarded breakout candidate as paper-only entry/exit intents with kill-switch journal.",
        command=python_cmd(
            "tools/strategy_mix_paper_replay.py",
            "--source-report",
            "docs/STRATEGY_MIX_FORWARD_LOCKED_CANDIDATE_2026-06-29_4H_GUARDED_SHORT.json",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--top",
            "1",
            "--max-daily-trades",
            "2",
            "--max-daily-loss-r",
            "3",
            "--max-drawdown-r",
            "8",
            "--max-consecutive-losses",
            "6",
            "--cooldown-bars-after-loss",
            "1",
            "--out-prefix",
            "docs/STRATEGY_MIX_PAPER_REPLAY_2026-06-08",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={"BOT_ENV": "demo"},
        network_note="cached public BTCUSDT data only; paper replay; no orders; no private credentials",
    ),
    "strategy_mix_oi_funding_replay_audit": TaskSpec(
        title="Strategy mix OI/funding replay audit",
        description="Audits whether local OI/funding context improves the locked paper replay trades.",
        command=python_cmd(
            "tools/strategy_mix_oi_funding_replay_auditor.py",
            "--out-prefix",
            "docs/STRATEGY_MIX_OI_FUNDING_REPLAY_AUDIT_2026-06-15",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local replay CSV and derivatives cache only; no orders; no private credentials",
    ),
    "strategy_mix_forward_paper_feed": TaskSpec(
        title="Strategy mix forward paper feed",
        description="Checks fresh public 4H BTCUSDT candles and writes signal/no-signal forward paper journal.",
        command=python_cmd(
            "tools/strategy_mix_forward_paper_feed.py",
            "--source-report",
            "docs/STRATEGY_MIX_FORWARD_LOCKED_CANDIDATE_2026-06-29_4H_GUARDED_SHORT.json",
            "--symbol",
            "BTCUSDT",
            "--interval",
            "4h",
            "--limit",
            "320",
            "--with-spot",
            "--out-prefix",
            "docs/STRATEGY_MIX_FORWARD_PAPER_FEED_2026-06-08",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="public Binance market data only; forward paper journal; no orders; no private credentials",
    ),
    "strategy_mix_guarded_1h_forward_observer": TaskSpec(
        title="Strategy mix guarded 1H forward observer",
        description="Checks the locked 1H LONG guarded breakout candidate on fresh public candles and writes a watch-only paper card.",
        command=python_cmd(
            "tools/strategy_mix_forward_paper_feed.py",
            "--source-report",
            "docs/STRATEGY_MIX_FORWARD_LOCKED_CANDIDATE_2026-07-01_1H_GUARDED_LONG.json",
            "--candidate-verdicts",
            "paper_replay_candidate_locked",
            "--symbol",
            "BTCUSDT",
            "--interval",
            "1h",
            "--limit",
            "420",
            "--min-closed-bars",
            "260",
            "--with-spot",
            "--journal-path",
            "logs/forward_paper_feed/strategy_mix_guarded_1h_forward_paper_feed.jsonl",
            "--state-path",
            "logs/forward_paper_feed/strategy_mix_guarded_1h_forward_paper_feed_state.json",
            "--signal-card-json-path",
            "logs/forward_paper_feed/latest_signal_card_guarded_1h.json",
            "--signal-card-md-path",
            "logs/forward_paper_feed/latest_signal_card_guarded_1h.md",
            "--out-prefix",
            "docs/STRATEGY_MIX_GUARDED_1H_FORWARD_PAPER_FEED_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="public Binance market data only; watch-only paper card; no orders; no private credentials",
    ),
    "strategy_mix_guarded_1h_forward_telegram_notify": TaskSpec(
        title="Strategy mix guarded 1H Telegram notify",
        description="Sends a watch-only Telegram alert only when the guarded 1H observer writes a notifiable card.",
        command=python_cmd(
            "tools/strategy_mix_forward_telegram_notify.py",
            "--card-json-path",
            "logs/forward_paper_feed/latest_signal_card_guarded_1h.json",
            "--state-path",
            "logs/forward_paper_feed/strategy_mix_guarded_1h_telegram_notify_state.json",
            "--notify-statuses",
            "paper_entry_intent,signal_entry_pending_next_bar",
            "--out-prefix",
            "docs/STRATEGY_MIX_GUARDED_1H_FORWARD_TELEGRAM_NOTIFY_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="Telegram watch notification only; no orders; no private credentials",
    ),
    "strategy_mix_guarded_1h_forward_start_loop": TaskSpec(
        title="Start guarded 1H forward observer loop",
        description="Starts the background guarded 1H observer loop: public feed check plus Telegram watch notifier.",
        command=[
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "ops" / "autostart" / "Start-StrategyMixGuarded1HForwardObserverLoop.ps1"),
        ],
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="public market data and Telegram watch notify only; no paper entry execution, no orders, no private credentials",
    ),
    "strategy_mix_guarded_1h_forward_scoreboard": TaskSpec(
        title="Strategy mix guarded 1H forward scoreboard",
        description="Scores resolved watch-only paper-entry intents for the locked guarded 1H candidate.",
        command=python_cmd(
            "tools/strategy_mix_forward_scoreboard.py",
            "--journal-path",
            "logs/forward_paper_feed/strategy_mix_guarded_1h_forward_paper_feed.jsonl",
            "--cache-csv",
            "_dl/forward_paper_feed/cache/futures/BTCUSDT/1h_klines.csv",
            "--min-resolved",
            "30",
            "--min-expectancy-r",
            "0.10",
            "--out-prefix",
            "docs/STRATEGY_MIX_GUARDED_1H_FORWARD_SCOREBOARD_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local forward journal and public candle cache only; scoreboard; no orders; no private credentials",
    ),
    "strategy_mix_forward_scoreboard": TaskSpec(
        title="Strategy mix forward scoreboard",
        description="Scores paper-entry intents from the forward journal against available closed 4H cache.",
        command=python_cmd(
            "tools/strategy_mix_forward_scoreboard.py",
            "--out-prefix",
            "docs/STRATEGY_MIX_FORWARD_SCOREBOARD_2026-06-08",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local journal/cache only; scoreboard; no orders; no private credentials",
    ),
    "canonical_regime_forward_observer": TaskSpec(
        title="Canonical regime forward observer",
        description="Computes Canonical Bot-Safe TREND/RANGE/SHOCK state on latest forward 4H cache.",
        command=python_cmd(
            "tools/canonical_regime_forward_observer.py",
            "--out-prefix",
            "docs/CANONICAL_REGIME_FORWARD_OBSERVER_2026-06-09",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local public-cache regime observer only; no orders; no private credentials",
    ),
    "oi_funding_forward_context_observer": TaskSpec(
        title="OI/funding forward context",
        description="Fetches/aligns public OI and funding context for the latest forward 4H card.",
        command=python_cmd(
            "tools/oi_funding_forward_context_observer.py",
            "--out-prefix",
            "docs/OI_FUNDING_FORWARD_CONTEXT_OBSERVER_2026-06-09",
        ),
        cwd=ROOT,
        timeout_s=90,
        env={"BOT_ENV": "demo"},
        network_note="public Binance derivatives context only; no orders; no private credentials",
    ),
    "oi_funding_forward_context_scoreboard": TaskSpec(
        title="OI/funding context scoreboard",
        description="Scores accumulated OI/funding context observations against forward paper outcomes when available.",
        command=python_cmd(
            "tools/oi_funding_forward_context_scoreboard.py",
            "--out-prefix",
            "docs/OI_FUNDING_FORWARD_CONTEXT_SCOREBOARD_2026-06-15",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local evidence scoreboard only; no orders; no private credentials",
    ),
    "oi_funding_data_quality_collector": TaskSpec(
        title="OI/funding data quality collector",
        description="Refreshes public OI/funding cache, rebuilds aligned CSV and reports guard-readiness coverage.",
        command=python_cmd(
            "tools/oi_funding_data_quality_collector.py",
            "--pages",
            "20",
            "--funding-pages",
            "10",
            "--kline-pages",
            "2",
            "--out-prefix",
            "docs/OI_FUNDING_DATA_QUALITY_2026-06-15",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="public Binance data only; cache/data-quality report; no orders; no private credentials",
    ),
    "oi_funding_data_quality_matrix": TaskSpec(
        title="OI/funding data quality matrix",
        description="Aggregates latest OI/funding quality reports by interval and prevents single-report ambiguity.",
        command=python_cmd(
            "tools/oi_funding_data_quality_matrix.py",
            "--out-prefix",
            "docs/OI_FUNDING_DATA_QUALITY_MATRIX_2026-06-29",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local report aggregation only; no network, orders or private credentials",
    ),
    "strategy_research_frontier_matrix": TaskSpec(
        title="Strategy research frontier matrix",
        description="Aggregates strategy-family reports and shows rejected, observer-only and promotable families.",
        command=python_cmd(
            "tools/strategy_research_frontier_matrix.py",
            "--out-prefix",
            "docs/STRATEGY_RESEARCH_FRONTIER_MATRIX_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local report aggregation only; no network, orders or private credentials",
    ),
    "execution_realism_metrics_smoke": TaskSpec(
        title="Execution realism metrics smoke",
        description="Stdlib-only smoke for queue penetration, OBI fill probability, James-Stein shrinkage and Fleet-CDaR.",
        command=python_cmd(
            "tools/execution_realism_metrics.py",
            "--smoke",
            "--out-prefix",
            "docs/EXECUTION_REALISM_METRICS_SMOKE_2026-07-03",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local metrics smoke only; no network, no alerts, no paper entry intents, no orders or private credentials",
    ),
    "execution_realism_shadow_overlay": TaskSpec(
        title="Execution realism shadow overlay",
        description="Applies maker-fill/OBI shadow execution realism to historical trade ledgers without changing strategy decisions.",
        command=python_cmd(
            "tools/execution_realism_shadow_overlay.py",
            "--out-prefix",
            "docs/EXECUTION_REALISM_SHADOW_OVERLAY_2026-07-11",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local shadow ledger analysis only; no network, no alerts, no paper entry intents, no orders or private credentials",
    ),
    "execution_realism_promotion_gate": TaskSpec(
        title="Execution realism promotion gate",
        description="Mandatory execution-realism gate before any future candidate can reach paper-design review.",
        command=python_cmd(
            "tools/execution_realism_promotion_gate.py",
            "--out-prefix",
            "docs/EXECUTION_REALISM_PROMOTION_GATE_2026-07-12_CURRENT_FRONTIER",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local evidence gate only; no network, no alerts, no paper entry intents, no orders or private credentials",
    ),
    "execution_realism_candidate_binding_drill": TaskSpec(
        title="Execution candidate-binding drill",
        description="Synthetic proof that a candidate/report/ledger SHA-256 binding passes once and blocks after tampering.",
        command=python_cmd("tools/execution_realism_candidate_binding_drill.py"),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="synthetic local contract drill only; no network, alerts, signals, paper entries, orders or credentials",
    ),
    "full_system_angel_audit": TaskSpec(
        title="Full system Angel audit",
        description="Evidence-bounded positive audit: counts only runnable strengths and reports their explicit limitations.",
        command=python_cmd("tools/full_system_angel_audit.py"),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local read-only audit; no network, alerts, signals, paper entries, orders or private credentials",
    ),
    "dialectic_synthesizer": TaskSpec(
        title="Devil + Angel dialectic synthesis",
        description="Resolves Devil and Angel evidence into one bounded promotion state, blockers and next strong move.",
        command=python_cmd("tools/dialectic_synthesizer.py"),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local synthesis only; no network, alerts, signals, paper entries, orders or private credentials",
    ),
    "dialectic_audit_pack": TaskSpec(
        title="Run Devil + Angel + Dialectic pack",
        description="One-click fresh Devil audit, Angel audit and dialectic synthesis with immutable no-trade boundaries.",
        command=python_cmd("tools/run_dialectic_audit_pack.py"),
        cwd=ROOT,
        timeout_s=240,
        env={"BOT_ENV": "demo"},
        network_note="read-only local audit pack; no network, alerts, signals, paper entries, orders or private credentials",
    ),
    "observer_loop_durability_drill": TaskSpec(
        title="Observer-loop durability drill",
        description="Synthetic dry-run proof that microstructure and real-edge observer failures enter bounded self-heal.",
        command=python_cmd("tools/observer_loop_durability_drill.py"),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="synthetic dry-run only; no restart, network, Telegram, signals, paper entries, orders or credentials",
    ),
    "microstructure_prereg_independence_audit": TaskSpec(
        title="Microstructure prereg independence audit",
        description="Proves whether the locked 4/4 queue contains distinct mechanisms instead of duplicate feature variants.",
        command=python_cmd("tools/microstructure_prereg_independence_audit.py"),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local preregistration audit only; no snapshot opening, network, signals, paper entries, orders or credentials",
    ),
    "microstructure_cost_model_audit": TaskSpec(
        title="Microstructure cost-model audit",
        description="Checks identical cost accounting, next-minute fills and stress gates across all four locked studies.",
        command=python_cmd("tools/microstructure_cost_model_audit.py"),
        cwd=ROOT,
        timeout_s=90,
        env={"BOT_ENV": "demo"},
        network_note="local audit against locked source and synthetic reports; no retuning, snapshot opening, signals, paper entries, orders or credentials",
    ),
    "microstructure_seal_pipeline_drill_current": TaskSpec(
        title="Microstructure sealed-pipeline drill",
        description="Runs the complete four-script research chain against a synthetic sealed snapshot without consuming real trial budget.",
        command=python_cmd(
            "tools/cross_venue_microstructure_seal_pipeline_drill.py",
            "--work-dir",
            "_dl/runtime_drills/microstructure_seal_pipeline_2026-07-10_panel",
            "--out-prefix",
            "docs/CROSS_VENUE_MICROSTRUCTURE_SEAL_PIPELINE_DRILL_2026-07-10",
            "--timeout-seconds",
            "180",
        ),
        cwd=ROOT,
        timeout_s=240,
        env={"BOT_ENV": "demo"},
        network_note="synthetic sealed snapshot only; dry-run notifications; no live data opening, signals, paper entries, orders or credentials",
    ),
    "microstructure_rollout_handoff_drill": TaskSpec(
        title="Microstructure rollout handoff drill",
        description="Proves rolling-gap wait -> sealed snapshot -> one locked research run -> duplicate block using synthetic data.",
        command=python_cmd(
            "tools/cross_venue_microstructure_rollout_handoff_drill.py",
            "--work-dir",
            "_dl/runtime_drills/microstructure_rollout_handoff_panel",
            "--out-prefix",
            "docs/CROSS_VENUE_MICROSTRUCTURE_ROLLOUT_HANDOFF_DRILL_2026-07-11",
            "--timeout-seconds",
            "180",
        ),
        cwd=ROOT,
        timeout_s=240,
        env={"BOT_ENV": "demo"},
        network_note="synthetic local handoff proof only; consumes no real trial budget and opens no validation, signals, paper entries or orders",
    ),
    "active_source_integrity_check": TaskSpec(
        title="Active source integrity check",
        description="Checks reviewed code/config hashes and fails closed on changed, missing or untracked source files.",
        command=python_cmd("tools/active_source_integrity_guard.py", "check"),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local hash audit only; no automatic restore, network, signals, paper entries, orders or credentials",
    ),
    "cross_stack_replication_audit": TaskSpec(
        title="Cross-stack replication audit",
        description="Audits Claude external Bybit replication lock without merging it into Codex forward sample.",
        command=python_cmd(
            "tools/cross_stack_replication_audit.py",
            "--lock",
            "HANDOFF/locks/BYBIT_REPLICATION_LOCK_20260703.json",
            "--handoff",
            "HANDOFF/CLAUDE_TO_CODEX_FILE8_2026-07-03.md",
            "--replication-report",
            "HANDOFF/CLAUDE_REPLICATION_REPORT_BYBIT_SQUEEZE.json",
            "--out-prefix",
            "docs/CROSS_STACK_REPLICATION_AUDIT_2026-07-03",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local handoff/lock audit only; no network, no alerts, no paper entry intents, no orders or private credentials",
    ),
    "cross_stack_replication_transition_monitor": TaskSpec(
        title="Cross-stack replication transition monitor",
        description="Detects non-zero/threshold transitions in Claude external replication report without merging it into Codex forward sample.",
        command=python_cmd(
            "tools/cross_stack_replication_transition_monitor.py",
            "--out-prefix",
            "docs/CROSS_STACK_REPLICATION_TRANSITION_MONITOR_2026-07-03",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local external-report transition monitor only; no network, no alerts, no paper entry intents, no orders or private credentials",
    ),
    "cross_stack_replication_transition_loop_start": TaskSpec(
        title="Start cross-stack replication transition loop",
        description="Starts a background loop that monitors Claude external replication report transitions every 15 minutes.",
        command=[
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "ops" / "autostart" / "Start-CrossStackReplicationTransitionMonitorLoop.ps1"),
            "-SleepSeconds",
            "900",
        ],
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="starts local external-report monitor loop only; no network, no alerts, no paper entry intents, no orders or private credentials",
    ),
    "oi_funding_reset_reversal_research": TaskSpec(
        title="OI/funding reset reversal research",
        description="Bounded research-only test of OI reset + funding compression reversal across BTCUSDT 15m/1h/4h.",
        command=python_cmd(
            "tools/oi_funding_reset_reversal_research.py",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--intervals",
            "15m,1h,4h",
            "--max-configs",
            "1200",
            "--out-prefix",
            "docs/OI_FUNDING_RESET_REVERSAL_RESEARCH_2026-07-03_COMBINED",
        ),
        cwd=ROOT,
        timeout_s=300,
        env={"BOT_ENV": "demo"},
        network_note=(
            "local public-data research only; no alerts, no paper entry intents, "
            "no orders or private credentials"
        ),
    ),
    "derivatives_squeeze_disagreement_research": TaskSpec(
        title="Derivatives squeeze disagreement research",
        description="Research-only BTCUSDT volatility squeeze + derivatives disagreement holdout scan.",
        command=python_cmd(
            "tools/derivatives_squeeze_disagreement_research.py",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--intervals",
            "15m,1h,4h",
            "--max-configs",
            "1200",
            "--out-prefix",
            "docs/DERIVATIVES_SQUEEZE_DISAGREEMENT_RESEARCH_2026-07-03_COMBINED",
        ),
        cwd=ROOT,
        timeout_s=300,
        env={"BOT_ENV": "demo"},
        network_note=(
            "local historical research only; no alerts, no paper entry intents, "
            "no orders or private credentials"
        ),
    ),
    "derivatives_squeeze_disagreement_forward_observer": TaskSpec(
        title="Derivatives squeeze disagreement forward observer",
        description="Observer-only forward runner for the locked BTCUSDT squeeze/disagreement candidate.",
        command=python_cmd(
            "tools/derivatives_squeeze_disagreement_forward_observer.py",
            "--lock",
            "configs/DERIVATIVES_SQUEEZE_DISAGREEMENT_FORWARD_LOCK_2026-07-03.json",
            "--out-prefix",
            "docs/DERIVATIVES_SQUEEZE_DISAGREEMENT_FORWARD_OBSERVER_2026-07-03",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note=(
            "local observer-only forward evidence collection; no alerts, "
            "no paper entry intents, no orders or private credentials"
        ),
    ),
    "alt_breadth_dislocation_research": TaskSpec(
        title="Alt breadth dislocation research",
        description="Research-only BTCUSDT 1H test using ETH/SOL/BCH breadth dislocation context.",
        command=python_cmd(
            "tools/alt_breadth_dislocation_research.py",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--max-configs",
            "60",
            "--out-prefix",
            "docs/ALT_BREADTH_DISLOCATION_RESEARCH_2026-07-03_BOUNDED60",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={"BOT_ENV": "demo"},
        network_note=(
            "local historical research only; no alerts, no paper entry intents, "
            "no orders or private credentials"
        ),
    ),
    "alt_breadth_dislocation_forward_observer": TaskSpec(
        title="Alt breadth dislocation forward observer",
        description="Observer-only forward runner for the locked BTCUSDT alt-breadth dislocation candidate.",
        command=python_cmd(
            "tools/alt_breadth_dislocation_forward_observer.py",
            "--lock",
            "configs/ALT_BREADTH_DISLOCATION_FORWARD_LOCK_2026-07-03.json",
            "--out-prefix",
            "docs/ALT_BREADTH_DISLOCATION_FORWARD_OBSERVER_2026-07-03",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note=(
            "local observer-only forward evidence collection; no alerts, "
            "no paper entry intents, no orders or private credentials"
        ),
    ),
    "basis_multi_symbol_research_batch_summary": TaskSpec(
        title="Basis multi-symbol batch summary",
        description="Summarizes the latest multi-symbol basis/funding research batch without rerunning heavy holdouts.",
        command=python_cmd(
            "tools/basis_multi_symbol_research_batch_summary.py",
            "--out-prefix",
            "docs/BASIS_MULTI_SYMBOL_RESEARCH_BATCH_SUMMARY_2026-07-02",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local research summary only; no network, no paper entry intents, no orders, no private credentials",
    ),
    "basis_shock_funding_alignment_multi_symbol": TaskSpec(
        title="Basis shock + funding alignment multi-symbol",
        description="Pre-registered research-only holdout: positive basis shock confirmed by recent positive funding alignment.",
        command=python_cmd(
            "tools/basis_shock_funding_alignment_multi_symbol_nested_holdout.py",
            "--out-prefix",
            "docs/BASIS_SHOCK_FUNDING_ALIGNMENT_MULTI_SYMBOL_NESTED_HOLDOUT_2026-07-02",
        ),
        cwd=ROOT,
        timeout_s=300,
        env={"BOT_ENV": "demo"},
        network_note="local historical research only; no network, no paper entry intents, no orders, no private credentials",
    ),
    "session_volatility_compression_breakout_1h": TaskSpec(
        title="Session volatility compression breakout 1H",
        description="Research-only nested holdout for session-aware volatility compression breakout on BTCUSDT 1H.",
        command=python_cmd(
            "tools/session_volatility_compression_breakout_nested_holdout.py",
            "--intervals",
            "1h",
            "--max-configs-per-interval",
            "500",
            "--out-prefix",
            "docs/SESSION_VOLATILITY_COMPRESSION_BREAKOUT_NESTED_HOLDOUT_1H_BOUNDED_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=300,
        env={"BOT_ENV": "demo"},
        network_note="local research-only nested holdout; no network, no paper entry intents, no orders, no private credentials",
    ),
    "session_volatility_compression_breakout_15m": TaskSpec(
        title="Session volatility compression breakout 15M",
        description="Research-only nested holdout for session-aware volatility compression breakout on BTCUSDT 15M.",
        command=python_cmd(
            "tools/session_volatility_compression_breakout_nested_holdout.py",
            "--intervals",
            "15m",
            "--max-configs-per-interval",
            "250",
            "--out-prefix",
            "docs/SESSION_VOLATILITY_COMPRESSION_BREAKOUT_NESTED_HOLDOUT_15M_BOUNDED_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={"BOT_ENV": "demo"},
        network_note="local research-only nested holdout; no network, no paper entry intents, no orders, no private credentials",
    ),
    "calendar_session_drift_1h_4h": TaskSpec(
        title="Calendar session drift 1H/4H",
        description="Research-only nested holdout for fixed UTC-hour/weekday/session drift on BTCUSDT 1H and 4H.",
        command=python_cmd(
            "tools/calendar_session_drift_nested_holdout.py",
            "--intervals",
            "1h,4h",
            "--max-configs-per-interval",
            "500",
            "--out-prefix",
            "docs/CALENDAR_SESSION_DRIFT_NESTED_HOLDOUT_2026-07-01_BOUNDED",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={"BOT_ENV": "demo"},
        network_note="local research-only nested holdout; no network, no paper entry intents, no orders, no private credentials",
    ),
    "calendar_session_drift_15m": TaskSpec(
        title="Calendar session drift 15M",
        description="Research-only nested holdout for fixed UTC-hour/weekday/session drift on BTCUSDT 15M.",
        command=python_cmd(
            "tools/calendar_session_drift_nested_holdout.py",
            "--intervals",
            "15m",
            "--max-configs-per-interval",
            "250",
            "--train-min-trades",
            "60",
            "--validation-min-trades",
            "40",
            "--oos-min-trades",
            "40",
            "--out-prefix",
            "docs/CALENDAR_SESSION_DRIFT_NESTED_HOLDOUT_15M_BOUNDED_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="local research-only nested holdout; no network, no paper entry intents, no orders, no private credentials",
    ),
    "relative_strength_rotation_1h": TaskSpec(
        title="Relative strength rotation 1H",
        description="Research-only BTCUSDT relative-strength rotation test using ETH/SOL/BCH as context inputs.",
        command=python_cmd(
            "tools/relative_strength_rotation_nested_holdout.py",
            "--interval",
            "1h",
            "--max-configs",
            "250",
            "--out-prefix",
            "docs/RELATIVE_STRENGTH_ROTATION_NESTED_HOLDOUT_1H_BOUNDED_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=360,
        env={"BOT_ENV": "demo"},
        network_note="local research-only nested holdout; no network, no paper entry intents, no orders, no private credentials",
    ),
    "volatility_regime_transition_1h_4h": TaskSpec(
        title="Volatility regime transition 1H/4H",
        description="Research-only test for low-volatility-to-expansion transitions on BTCUSDT futures.",
        command=python_cmd(
            "tools/volatility_regime_transition_nested_holdout.py",
            "--intervals",
            "1h,4h",
            "--max-configs",
            "250",
            "--out-prefix",
            "docs/VOLATILITY_REGIME_TRANSITION_NESTED_HOLDOUT_BOUNDED_2026-07-01",
        ),
        cwd=ROOT,
        timeout_s=240,
        env={"BOT_ENV": "demo"},
        network_note="local research-only nested holdout; no network, no paper entry intents, no orders, no private credentials",
    ),
    "trade_ledger_guard_matrix": TaskSpec(
        title="Trade ledger guard matrix",
        description="Train/OOS guard optimizer over existing trade ledgers using only pre-entry context fields.",
        command=python_cmd(
            "tools/trade_ledger_guard_matrix.py",
            "--out-prefix",
            "docs/TRADE_LEDGER_GUARD_MATRIX_2026-06-30_NO_LEAKAGE",
            "--max-files",
            "12",
            "--max-guard-size",
            "2",
            "--min-train-trades",
            "20",
            "--min-oos-trades",
            "8",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="local research-only guard matrix; no network, no paper entry intents, no orders, no private credentials",
    ),
    "composite_liquidity_derivatives_nested_holdout": TaskSpec(
        title="Composite liquidity derivatives nested holdout",
        description="Research-only test of sweep + spot/perp divergence + OI expansion + funding confluence.",
        command=python_cmd(
            "tools/composite_liquidity_derivatives_nested_holdout.py",
            "--lookbacks",
            "24,48",
            "--div-windows",
            "3,6",
            "--min-spot-perp-div-bps",
            "5,10,20",
            "--oi-windows",
            "3,6",
            "--min-oi-change-pct",
            "0.25,0.5,1",
            "--funding-modes",
            "none,contrarian,compressed",
            "--take-atr",
            "3",
            "--max-hold-bars",
            "24,48",
            "--out-prefix",
            "docs/COMPOSITE_LIQUIDITY_DERIVATIVES_NESTED_HOLDOUT_2026-06-30",
        ),
        cwd=ROOT,
        timeout_s=300,
        env={"BOT_ENV": "demo"},
        network_note="local research-only nested holdout; no network, no paper entry intents, no orders, no private credentials",
    ),
    "cross_venue_spot_data_collector": TaskSpec(
        title="Cross-venue BTC spot collector",
        description="Collects and aligns the latest 24h of closed 1m BTC spot candles from Binance and Coinbase.",
        command=python_cmd(
            "tools/cross_venue_spot_data_collector.py",
            "--interval",
            "1m",
            "--hours",
            "24",
            "--coinbase-product",
            "BTC-USD",
            "--out-dir",
            "data/cross_venue_spot",
            "--report-prefix",
            "docs/CROSS_VENUE_SPOT_DATA_QUALITY_2026-06-24",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={"BOT_ENV": "demo"},
        network_note="public Binance/Coinbase candles only; data quality output; no keys, hypotheses or orders",
    ),
    "microstructure_research_runner": TaskSpec(
        title="Microstructure research runner",
        description="Runs preregistered microstructure research only after an exact sealed SQLite snapshot exists; otherwise writes blocked status.",
        command=python_cmd(
            "tools/cross_venue_microstructure_research_runner.py",
            "run-if-ready",
        ),
        cwd=ROOT,
        timeout_s=900,
        env={"BOT_ENV": "demo"},
        network_note="local sealed-snapshot research only; no keys, no observer registration, no paper/live orders",
    ),
    "microstructure_post_seal_auto_run_guard": TaskSpec(
        title="Microstructure post-seal auto-run guard",
        description="Audits the one-shot post-seal runner guard; default panel action does not execute research.",
        command=python_cmd(
            "tools/cross_venue_microstructure_post_seal_auto_run_guard.py",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local guard preview only; no research execution from panel default, no validation/OOS, no signals, no orders",
    ),
    "microstructure_snapshot_transition_monitor": TaskSpec(
        title="Microstructure snapshot transition monitor",
        description="Classifies the handoff from collection/readiness into sealed train research batch readiness.",
        command=python_cmd(
            "tools/cross_venue_microstructure_snapshot_transition_monitor.py",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local transition monitor only; does not run research, open validation, send signals or orders",
    ),
    "microstructure_autopilot_audit": TaskSpec(
        title="Microstructure autopilot audit",
        description="Audits whether collector/watchdog are fresh, safe and wired to run the locked research runner only after a sealed snapshot.",
        command=python_cmd(
            "tools/cross_venue_microstructure_autopilot_audit.py",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local autopilot audit only; does not run research, open validation, send signals or orders",
    ),
    "microstructure_post_snapshot_launch_audit": TaskSpec(
        title="Microstructure post-snapshot launch audit",
        description="Checks that the full post-seal chain can launch the locked research/governance/validation-skeleton flow once a snapshot is ready.",
        command=python_cmd(
            "tools/cross_venue_microstructure_post_snapshot_launch_audit.py",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local launch-readiness audit only; does not run research, open validation, send signals or orders",
    ),
    "microstructure_collector_sla_guard": TaskSpec(
        title="Microstructure collector SLA guard",
        description="Checks each collector cycle for fresh reports, trade/book inserts, coverage regressions and trade-id gaps.",
        command=python_cmd(
            "tools/cross_venue_microstructure_collector_sla_guard.py",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local data SLA guard only; no research run, no validation, no signals, no orders",
    ),
    "microstructure_collector_sla_telegram_notify": TaskSpec(
        title="Microstructure collector SLA Telegram",
        description="Notifies Telegram only on collector SLA degraded/recovered events; normal healthy cycles are skipped.",
        command=python_cmd(
            "tools/cross_venue_microstructure_collector_sla_telegram_notify.py",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="Telegram notification only for data-SLA incidents; no research run, no validation, no signals, no orders",
    ),
    "microstructure_collector_sla_telegram_drill": TaskSpec(
        title="Microstructure collector SLA Telegram drill",
        description="Synthetic dry-run proof for healthy/degraded/suppressed/changed/recovered SLA notifications.",
        command=python_cmd(
            "tools/cross_venue_microstructure_collector_sla_telegram_drill.py",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="synthetic local dry-run only; no Telegram send, no signals, no orders",
    ),
    "microstructure_collector_sla_replay": TaskSpec(
        title="Microstructure collector SLA replay",
        description="Summarizes recent collector SLA history for incidents, flapping, coverage and insert stability.",
        command=python_cmd(
            "tools/cross_venue_microstructure_collector_sla_replay.py",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local SLA history replay only; no research run, no validation, no signals, no orders",
    ),
    "microstructure_readiness_progress_monitor": TaskSpec(
        title="Microstructure readiness progress",
        description="Tracks whether the 168h readiness window, coverage and ETA are progressing without collector stall.",
        command=python_cmd(
            "tools/cross_venue_microstructure_readiness_progress_monitor.py",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local progress monitor only; no research run, no validation, no signals, no orders",
    ),
    "microstructure_snapshot_transition_telegram_notify": TaskSpec(
        title="Microstructure transition Telegram notify",
        description="Deduplicated Telegram notifier for READY/BLOCKED/DONE snapshot transition milestones.",
        command=python_cmd(
            "tools/cross_venue_microstructure_snapshot_transition_telegram_notify.py",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="Telegram only on important transition milestones; no research run, no signals, no orders",
    ),
    "microstructure_snapshot_transition_telegram_drill": TaskSpec(
        title="Microstructure transition Telegram drill",
        description="Synthetic dry-run proof for WAITING/READY/duplicate/BLOCKED/DONE transition notifications.",
        command=python_cmd(
            "tools/cross_venue_microstructure_snapshot_transition_telegram_drill.py",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="synthetic local dry-run only; no Telegram send, no research run, no signals, no orders",
    ),
    "microstructure_candidate_governance": TaskSpec(
        title="Microstructure candidate governance",
        description="Audits the latest microstructure batch result and blocks automatic validation/observer/paper/live promotion.",
        command=python_cmd(
            "tools/cross_venue_microstructure_candidate_governance_gate.py",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local governance audit only; no signals, no validation opening, no orders",
    ),
    "microstructure_candidate_review_pack": TaskSpec(
        title="Microstructure candidate review pack",
        description="Builds a manual review packet for any future microstructure candidate without opening validation.",
        command=python_cmd(
            "tools/cross_venue_microstructure_candidate_review_pack.py",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local review packet only; no validation opening, no signals, no orders",
    ),
    "microstructure_validation_protocol": TaskSpec(
        title="Microstructure validation protocol",
        description="Builds a draft validation protocol for reviewed candidates without opening validation data.",
        command=python_cmd(
            "tools/cross_venue_microstructure_validation_protocol_builder.py",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local validation protocol draft only; no validation opening, no signals, no orders",
    ),
    "microstructure_validation_approval_audit": TaskSpec(
        title="Microstructure validation approval audit",
        description="Audits an explicit manual approval file for candidate/snapshot match and safe prohibitions.",
        command=python_cmd(
            "tools/cross_venue_microstructure_validation_approval_audit.py",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local approval audit only; creates no approval, opens no validation, sends no signals or orders",
    ),
    "microstructure_validation_runner_skeleton": TaskSpec(
        title="Microstructure validation runner skeleton",
        description="Checks validation-run preconditions but never opens validation data or executes strategy code.",
        command=python_cmd(
            "tools/cross_venue_microstructure_validation_runner_skeleton.py",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local fail-closed validation skeleton only; no validation opening, no signals, no orders",
    ),
    "microstructure_seal_pipeline_drill": TaskSpec(
        title="Microstructure seal pipeline drill",
        description="Synthetic end-to-end proof for seal notify -> research runner -> research notify. Synthetic data only.",
        command=python_cmd(
            "tools/cross_venue_microstructure_seal_pipeline_drill.py",
        ),
        cwd=ROOT,
        timeout_s=240,
        env={"BOT_ENV": "demo"},
        network_note="synthetic local drill only; Telegram dry-run uses dummy env; no signals or orders",
    ),
    "historical_oi_import_scan": TaskSpec(
        title="Historical OI import scan",
        description="Scans local data/Downloads CSV files for importable historical OI exports. Scan-only; cache is not changed.",
        command=python_cmd(
            "tools/historical_oi_importer.py",
            "scan",
            "--out-prefix",
            "docs/HISTORICAL_OI_IMPORT_SCAN_2026-06-15",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="local CSV scan only; no cache import; no orders; no private credentials",
    ),
    "historical_oi_gap_plan": TaskSpec(
        title="Historical OI gap plan",
        description="Builds exact BTCUSDT 4H OI gap map and vendor request CSV required for replay/guard validation.",
        command=python_cmd(
            "tools/historical_oi_gap_planner.py",
            "--out-prefix",
            "docs/HISTORICAL_OI_GAP_PLAN_2026-06-15",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local data planning only; no cache import; no orders; no private credentials",
    ),
    "binance_vision_oi_backfill_probe": TaskSpec(
        title="Binance Vision OI backfill probe",
        description="Dry-run probe against Binance Vision daily metrics archive for historical OI parsing and replay coverage simulation.",
        command=python_cmd(
            "tools/binance_vision_oi_backfiller.py",
            "--start",
            "2021-01-01",
            "--end",
            "2021-01-07",
            "--dry-run",
            "--out-prefix",
            "docs/BINANCE_VISION_OI_BACKFILL_PROBE_2026-06-15",
            "--workers",
            "4",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="public Binance Data Vision archive; dry-run only; no cache write; no orders",
    ),
    "strategy_mix_oi_guard_validation": TaskSpec(
        title="Strategy mix OI guard validation",
        description="Validates OI guard candidates across replay trades, years, folds, bootstrap and cost stress.",
        command=python_cmd(
            "tools/strategy_mix_oi_guard_validator.py",
            "--out-prefix",
            "docs/STRATEGY_MIX_OI_GUARD_VALIDATION_2026-06-15",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="offline replay validation only; no orders; no private credentials",
    ),
    "oi_guard_promotion_gate": TaskSpec(
        title="OI guard promotion gate",
        description="Checks whether the validated OI guard may move beyond shadow observation. Blocks until enough forward outcomes exist.",
        command=python_cmd(
            "tools/oi_guard_promotion_gate.py",
            "--out-prefix",
            "docs/OI_GUARD_PROMOTION_GATE_2026-06-15",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local evidence gate only; no orders; no private credentials; no live permission",
    ),
    "forward_outcome_accumulator": TaskSpec(
        title="Forward outcome accumulator",
        description="Accumulates forward evidence and shows deficits before OI guard evaluation can be reviewed.",
        command=python_cmd(
            "tools/forward_outcome_accumulator.py",
            "--out-prefix",
            "docs/FORWARD_OUTCOME_ACCUMULATOR_2026-06-16",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local evidence accumulator only; no orders; no private credentials; no permission grant",
    ),
    "forward_entry_scarcity_diagnostic": TaskSpec(
        title="Forward entry scarcity diagnostic",
        description="Explains which locked strategy conditions block current 4H forward entries.",
        command=python_cmd(
            "tools/forward_entry_scarcity_diagnostic.py",
            "--out-prefix",
            "docs/FORWARD_ENTRY_SCARCITY_DIAGNOSTIC_2026-06-16",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local cached-data diagnostic only; no orders; no private credentials; no parameter change",
    ),
    "forward_shadow_relaxation_validator": TaskSpec(
        title="Forward shadow relaxation validator",
        description="Research-only validation of relaxed locked-strategy variants before any forward observation.",
        command=python_cmd(
            "tools/forward_shadow_relaxation_validator.py",
            "--out-prefix",
            "docs/FORWARD_SHADOW_RELAXATION_VALIDATOR_2026-06-16",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="local cached-data validation only; no orders; no private credentials; no parameter change",
    ),
    "range_family_validator": TaskSpec(
        title="Range family validator",
        description="Research-only validation of BTCUSDT range mean-reversion families for RANGE regime.",
        command=python_cmd(
            "tools/range_family_validator.py",
            "--out-prefix",
            "docs/RANGE_FAMILY_VALIDATOR_2026-06-16",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={"BOT_ENV": "demo"},
        network_note="local cached-data validation only; no orders; no private credentials; no live permission",
    ),
    "range_watchlist_refiner": TaskSpec(
        title="Range watchlist refiner",
        description="Research-only refinement of RANGE watchlist variants with OI/funding/spot/volume filters.",
        command=python_cmd(
            "tools/range_watchlist_refiner.py",
            "--out-prefix",
            "docs/RANGE_WATCHLIST_REFINER_2026-06-16",
        ),
        cwd=ROOT,
        timeout_s=180,
        env={"BOT_ENV": "demo"},
        network_note="local cached-data refinement only; no orders; no private credentials; observer-only candidates",
    ),
    "range_refined_forward_observer": TaskSpec(
        title="Range refined forward observer",
        description="Observer-only latest-bar comparison for the selected refined RANGE candidate.",
        command=python_cmd(
            "tools/range_refined_forward_observer.py",
            "--out-prefix",
            "docs/RANGE_REFINED_FORWARD_OBSERVER_2026-06-16",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local forward-cache observer only; no paper entry intents; no orders; no private credentials",
    ),
    "range_refined_observer_scoreboard": TaskSpec(
        title="Range refined observer scoreboard",
        description="Scores observer-only outcomes for the selected refined RANGE candidate.",
        command=python_cmd(
            "tools/range_refined_observer_scoreboard.py",
            "--out-prefix",
            "docs/RANGE_REFINED_OBSERVER_SCOREBOARD_2026-06-16",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local observer scoreboard only; no paper entry intents; no orders; no private credentials",
    ),
    "range_refined_signal_scarcity_diagnostic": TaskSpec(
        title="Range refined signal scarcity diagnostic",
        description="Explains why the selected refined RANGE candidate has few/no latest observer signals.",
        command=python_cmd(
            "tools/range_refined_signal_scarcity_diagnostic.py",
            "--out-prefix",
            "docs/RANGE_REFINED_SIGNAL_SCARCITY_DIAGNOSTIC_2026-06-17",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local diagnostic only; does not relax parameters; no paper entry intents; no orders; no private credentials",
    ),
    "range_refined_pending_watch": TaskSpec(
        title="Range refined pending watch",
        description="Shows proximity to the selected RANGE trigger in price, ATR and percent without creating signals.",
        command=python_cmd(
            "tools/range_refined_pending_watch_monitor.py",
            "--out-prefix",
            "docs/RANGE_REFINED_PENDING_WATCH_2026-06-17",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="observer-only proximity monitor; no paper entry intents; no orders; no private credentials",
    ),
    "range_refined_pending_watch_telegram_notify": TaskSpec(
        title="Range pending-watch Telegram notify",
        description="Writes a RANGE pre-alert card and notifies Telegram only for notifiable pending-watch states.",
        command=python_cmd(
            "tools/range_refined_pending_watch_telegram_notify.py",
            "--out-prefix",
            "docs/RANGE_REFINED_PENDING_WATCH_TELEGRAM_NOTIFY_2026-06-18",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="Telegram warning only when configured and notifiable; no entry intents; no orders",
    ),
    "range_refined_pending_watch_telegram_drill": TaskSpec(
        title="Range pending-watch Telegram drill",
        description="Synthetic near-trigger dry-run proving the RANGE pre-alert path without sending Telegram.",
        command=python_cmd(
            "tools/range_refined_pending_watch_telegram_drill.py",
            "--out-prefix",
            "docs/RANGE_REFINED_PENDING_WATCH_TELEGRAM_DRILL_2026-06-18",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="synthetic dry-run only; no Telegram send; no entry intents; no orders",
    ),
    "range_refined_filter_shadow_ablation": TaskSpec(
        title="Range refined filter shadow ablation",
        description="Research-only test of relaxed RANGE filter variants such as dropping or softening OI expansion.",
        command=python_cmd(
            "tools/range_refined_filter_shadow_ablation.py",
            "--out-prefix",
            "docs/RANGE_REFINED_FILTER_SHADOW_ABLATION_2026-06-17",
        ),
        cwd=ROOT,
        timeout_s=120,
        env={"BOT_ENV": "demo"},
        network_note="historical shadow research only; does not change active observer; no paper entry intents; no orders",
    ),
    "range_refined_filter_shadow_forward_observer": TaskSpec(
        title="Range refined filter shadow forward observer",
        description="Observer-only latest-bar comparison for RANGE ablation variants against current forward cache.",
        command=python_cmd(
            "tools/range_refined_filter_shadow_forward_observer.py",
            "--out-prefix",
            "docs/RANGE_REFINED_FILTER_SHADOW_FORWARD_OBSERVER_2026-06-17",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="shadow observer only; does not change active strategy; no paper entry intents; no orders",
    ),
    "range_refined_filter_shadow_forward_scoreboard": TaskSpec(
        title="Range refined filter shadow forward scoreboard",
        description="Scores observer-only outcomes for RANGE ablation variants by variant_id.",
        command=python_cmd(
            "tools/range_refined_filter_shadow_forward_scoreboard.py",
            "--out-prefix",
            "docs/RANGE_REFINED_FILTER_SHADOW_FORWARD_SCOREBOARD_2026-06-17",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local shadow scoreboard only; no paper entry intents; no orders; no private credentials",
    ),
    "range_refined_filter_shadow_promotion_gate": TaskSpec(
        title="Range refined filter shadow promotion gate",
        description="Blocks relaxed RANGE filter variants from paper-design review until historical and forward gates pass.",
        command=python_cmd(
            "tools/range_refined_filter_shadow_promotion_gate.py",
            "--out-prefix",
            "docs/RANGE_REFINED_FILTER_SHADOW_PROMOTION_GATE_2026-06-17",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local evidence gate only; no active strategy change; no paper entry intents; no orders",
    ),
    "range_refined_promotion_gate": TaskSpec(
        title="Range refined promotion gate",
        description="Blocks RANGE candidate promotion until forward observer outcomes and alert plumbing pass hard gates.",
        command=python_cmd(
            "tools/range_refined_promotion_gate.py",
            "--out-prefix",
            "docs/RANGE_REFINED_PROMOTION_GATE_2026-06-17",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="local evidence gate only; no paper entry intents; no orders; no private credentials",
    ),
    "range_refined_signal_alert_guard": TaskSpec(
        title="Range refined signal alert guard",
        description="Writes the latest RANGE observer card and notifies Telegram only for non-duplicate observed signals.",
        command=python_cmd(
            "tools/range_refined_signal_alert_guard.py",
            "--out-prefix",
            "docs/RANGE_REFINED_SIGNAL_ALERT_GUARD_2026-06-16",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="observer alert only; Telegram only if configured and signal observed; no paper entry intents; no orders",
    ),
    "range_refined_signal_alert_drill": TaskSpec(
        title="Range refined signal alert drill",
        description="Synthetic observed-signal drill for RANGE alert guard dry-run and duplicate suppression.",
        command=python_cmd(
            "tools/range_refined_signal_alert_drill.py",
            "--out-prefix",
            "docs/RANGE_REFINED_SIGNAL_ALERT_DRILL_2026-06-17",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="synthetic dry-run only; no Telegram send; no paper entry intents; no orders; no private credentials",
    ),
    "strategy_mix_forward_scheduler_once": TaskSpec(
        title="Strategy mix forward scheduler once",
        description="Runs one public-data forward feed cycle, observers, scoreboards, OI guard promotion gate, outcome accumulator and Telegram notifier.",
        command=python_cmd(
            "tools/strategy_mix_forward_scheduler.py",
            "--cycles",
            "1",
            "--with-spot",
            "--out-prefix",
            "docs/STRATEGY_MIX_FORWARD_SCHEDULER_2026-06-08",
        ),
        cwd=ROOT,
        timeout_s=240,
        env={"BOT_ENV": "demo"},
        network_note="public Binance market data only; paper scheduler; no orders; no private credentials",
    ),
    "strategy_mix_forward_telegram_notify": TaskSpec(
        title="Strategy mix Telegram notify",
        description="Checks latest forward paper card and sends Telegram only for non-duplicate PAPER SIGNAL if env is configured.",
        command=python_cmd(
            "tools/strategy_mix_forward_telegram_notify.py",
            "--out-prefix",
            "docs/STRATEGY_MIX_FORWARD_TELEGRAM_NOTIFY_2026-06-08",
        ),
        cwd=ROOT,
        timeout_s=60,
        env={"BOT_ENV": "demo"},
        network_note="Telegram only if TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID exist; no orders; no private exchange credentials",
    ),
    "futures_public_tick_once": TaskSpec(
        title="Futures public tick once",
        description="Подключается только к public websocket и останавливается после 1 сообщения.",
        command=python_cmd(
            "-m",
            "btcusdt_bot",
            "run-breakout-loop",
            "--max-messages",
            "1",
            "--lookback",
            "3",
            "--position-notional",
            "100",
            "--no-with-private",
            "--no-with-reconcile",
        ),
        cwd=FUTURES_DIR,
        timeout_s=60,
        env={"PYTHONPATH": str(FUTURES_SRC), "BOT_ENV": "demo"},
        network_note="public websocket only; no orders",
    ),
    "dex_paper_smoke": TaskSpec(
        title="DEX paper range smoke",
        description="Два бумажных цикла DEX range bot: buy intent затем sell intent.",
        command=python_cmd(str(ROOT / "ops" / "control_panel" / "dex_paper_smoke.py")),
        cwd=ROOT,
        timeout_s=60,
    ),
    "delist_compile": TaskSpec(
        title="Delist EWS compile check",
        description="Проверяет синтаксис bundled Delist EWS файлов. Не запускает мониторинг.",
        command=python_cmd(
            "-m",
            "py_compile",
            "ops/delist_ews/database.py",
            "ops/delist_ews/pattern_engine.py",
            "ops/delist_ews/telegram_bot.py",
        ),
        cwd=ROOT,
        timeout_s=60,
    ),
    "max_core_lite_composite": TaskSpec(
        title="MAX Core Lite composite",
        description="Запускает repo-local MAX Core Lite composite с v1.8 alert-only short_continuation_pressure блоком.",
        command=python_cmd(
            "portable/run_max_pipeline.py",
            "--config",
            "configs/MAX_PIPELINE_CONFIG_SMOKE.json",
            "--out-prefix",
            "_dl/control_panel/MAX_CORE_LITE_COMPOSITE",
        ),
        cwd=ROOT,
        timeout_s=60,
    ),
    "max_core_lite_v19_alert_observability": TaskSpec(
        title="MAX Core Lite v1.9 alert observability",
        description="Logs market-state alerts to JSONL and updates forward outcome tracking. Observability only.",
        command=python_cmd(
            "tools/max_v19_alert_observability.py",
            "--composite",
            "_dl/control_panel/MAX_CORE_LITE_COMPOSITE.json",
            "--out-prefix",
            "_dl/control_panel/MAX_CORE_LITE_V19_ALERT_OBSERVABILITY",
        ),
        cwd=ROOT,
        timeout_s=60,
        network_note="local-only; no orders; no trade permission",
    ),
    "max_core_lite_v20_forward_evidence": TaskSpec(
        title="MAX Core Lite v2.0 forward evidence",
        description="Scores resolved alert observations: hit-rate, directional return and evidence classification. Evidence only.",
        command=python_cmd(
            "tools/max_v20_forward_evidence.py",
            "--out-prefix",
            "_dl/control_panel/MAX_CORE_LITE_V20_FORWARD_EVIDENCE",
        ),
        cwd=ROOT,
        timeout_s=60,
        network_note="local-only; no orders; no trade permission",
    ),
    "bitevo_contract_check": TaskSpec(
        title="BitEvo contract check",
        description="Checks BitEvo schemas, entry/cancel examples and Telegram template render contract.",
        command=python_cmd(
            "tools/bitevo_contract_checker.py",
            "--out-prefix",
            "docs/BITEVO_CONTRACT_CHECK_2026-06-02",
        ),
        cwd=ROOT,
        timeout_s=60,
        network_note="local-only contract check; no webhooks; no orders",
    ),
    "bitevo_registry_validation": TaskSpec(
        title="BitEvo registry validation",
        description="Checks BitEvo setup registry and SmartMoney alert presets for structural consistency.",
        command=python_cmd(
            "tools/bitevo_registry_validator.py",
            "--out-prefix",
            "docs/BITEVO_REGISTRY_VALIDATION_2026-06-02",
        ),
        cwd=ROOT,
        timeout_s=60,
        network_note="local-only registry validation; detectors not proven; no orders",
    ),
    "detector_gap_map": TaskSpec(
        title="Detector gap map",
        description="Maps BitEvo/SmartMoney setups to implemented, partial and missing detectors.",
        command=python_cmd(
            "tools/detector_gap_mapper.py",
            "--out-prefix",
            "docs/DETECTOR_GAP_MAP_2026-06-02",
        ),
        cwd=ROOT,
        timeout_s=60,
        network_note="local-only gap map; no detector promotion; no orders",
    ),
    "liquidity_sweep_detector_smoke": TaskSpec(
        title="Liquidity sweep EQ detector smoke",
        description="Detects crafted equal-high/equal-low sweep-return events. Alert-only proof; no trade permission.",
        command=python_cmd(
            "tools/liquidity_sweep_detector.py",
            "--csv",
            "smoke_tests/liquidity_sweep_eq_fixture.csv",
            "--lookback",
            "8",
            "--out-prefix",
            "docs/LIQUIDITY_SWEEP_DETECTOR_SMOKE_2026-06-03",
        ),
        cwd=ROOT,
        timeout_s=60,
        network_note="local-only crafted fixture; no orders; no trade permission",
    ),
    "liquidity_sweep_forward_eval": TaskSpec(
        title="Liquidity sweep forward eval",
        description="Evaluates forward outcomes after liquidity_sweep_eq events on local BTC futures cache.",
        command=python_cmd(
            "tools/liquidity_sweep_forward_eval.py",
            "--out-prefix",
            "docs/LIQUIDITY_SWEEP_FORWARD_EVAL_2026-06-03",
        ),
        cwd=ROOT,
        timeout_s=90,
        network_note="local cache only; research/evidence only; no orders; no trade permission",
    ),
    "liquidity_sweep_confluence_eval": TaskSpec(
        title="Liquidity sweep confluence eval",
        description="Tests sweep + OI/funding + HTF regime buckets on local BTC futures cache.",
        command=python_cmd(
            "tools/liquidity_sweep_confluence_eval.py",
            "--out-prefix",
            "docs/LIQUIDITY_SWEEP_CONFLUENCE_EVAL_2026-06-03",
        ),
        cwd=ROOT,
        timeout_s=90,
        network_note="local cache only; research/evidence only; no orders; no trade permission",
    ),
    "liquidity_sweep_hardening": TaskSpec(
        title="Liquidity sweep hardening",
        description="Runs next-bar entry hardening for short/funding-aligned sweep candidates with fees and folds.",
        command=python_cmd(
            "tools/liquidity_sweep_hardening.py",
            "--out-prefix",
            "docs/LIQUIDITY_SWEEP_HARDENING_2026-06-03",
        ),
        cwd=ROOT,
        timeout_s=120,
        network_note="local cache only; research hardening only; no orders; no trade permission",
    ),
    "liquidity_sweep_extended_cache": TaskSpec(
        title="Liquidity sweep extended cache",
        description="Fetches larger public Binance BTCUSDT futures cache for liquidity-sweep research.",
        command=python_cmd(
            "tools/max_data_cache.py",
            "--symbol",
            "BTCUSDT",
            "--intervals",
            "15m,1h,4h",
            "--markets",
            "futures",
            "--pages",
            "24",
            "--limit",
            "500",
            "--cache-dir",
            "data/cache/binance_liquidity_sweep_extended",
            "--out-prefix",
            "_dl/control_panel/LIQUIDITY_SWEEP_EXTENDED_DATA_CACHE",
        ),
        cwd=ROOT,
        timeout_s=360,
        network_note="public Binance data only; no keys; no orders",
    ),
    "liquidity_sweep_extended_hardening": TaskSpec(
        title="Liquidity sweep extended hardening",
        description="Runs hardening on the expanded liquidity-sweep cache to reject/confirm small-sample candidates.",
        command=python_cmd(
            "tools/liquidity_sweep_hardening.py",
            "--cache-dir",
            "data/cache/binance_liquidity_sweep_extended",
            "--out-prefix",
            "docs/LIQUIDITY_SWEEP_HARDENING_EXTENDED_2026-06-03",
        ),
        cwd=ROOT,
        timeout_s=180,
        network_note="local expanded cache only; research hardening only; no orders; no trade permission",
    ),
    "spot_perp_extended_cache": TaskSpec(
        title="Spot/perp extended cache",
        description="Fetches larger public Binance BTCUSDT spot+futures cache for divergence research.",
        command=python_cmd(
            "tools/max_data_cache.py",
            "--symbol",
            "BTCUSDT",
            "--intervals",
            "15m,1h,4h",
            "--markets",
            "futures,spot",
            "--pages",
            "24",
            "--limit",
            "500",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--out-prefix",
            "_dl/control_panel/SPOT_PERP_EXTENDED_DATA_CACHE",
        ),
        cwd=ROOT,
        timeout_s=480,
        network_note="public Binance data only; no keys; no orders",
    ),
    "spot_perp_divergence_hardening": TaskSpec(
        title="Spot/perp divergence hardening",
        description="Tests spot-lead momentum and perp-overextension reversion on public BTCUSDT cache.",
        command=python_cmd(
            "tools/spot_perp_divergence_hardening.py",
            "--intervals",
            "15m,1h,4h",
            "--families",
            "spot_lead_momentum,perp_overextension_reversion",
            "--funding-filters",
            "none",
            "--lookbacks",
            "12",
            "--z-thresholds",
            "2.0",
            "--stop-grid",
            "1.5",
            "--take-grid",
            "2.0",
            "--hold-grid",
            "12",
            "--out-prefix",
            "docs/SPOT_PERP_DIVERGENCE_HARDENING_2026-06-03",
        ),
        cwd=ROOT,
        timeout_s=90,
        network_note="local public-data cache only; research hardening only; no orders; no trade permission",
    ),
    "funding_oi_regime_hardening": TaskSpec(
        title="Funding/OI regime hardening",
        description="Tests funding extreme and OI-filter regime signals as standalone entries.",
        command=python_cmd(
            "tools/funding_oi_regime_hardening.py",
            "--out-prefix",
            "docs/FUNDING_OI_REGIME_HARDENING_2026-06-03",
        ),
        cwd=ROOT,
        timeout_s=120,
        network_note="local public-data cache only; research hardening only; no orders; no trade permission",
    ),
    "combined_regime_hardening": TaskSpec(
        title="Combined regime hardening",
        description="Tests structure/trend primary signals with funding, spot/perp and sweep filters on 1h cache.",
        command=python_cmd(
            "tools/combined_regime_hardening.py",
            "--intervals",
            "1h",
            "--families",
            "donchian_breakout,ema_pullback_continuation,short_continuation_pressure",
            "--filter-modes",
            "none,risk_filters,all_filters",
            "--stop-grid",
            "1.5",
            "--take-grid",
            "2.0",
            "--hold-grid",
            "12",
            "--out-prefix",
            "docs/COMBINED_REGIME_HARDENING_2026-06-03",
        ),
        cwd=ROOT,
        timeout_s=180,
        network_note="local public-data cache only; research hardening only; no orders; no trade permission",
    ),
    "research_gate_audit_combined": TaskSpec(
        title="Research gate audit: combined",
        description="Audits combined-regime JSON for sample risk, fold stability, OI coverage and overfit flags.",
        command=python_cmd(
            "tools/research_gate_auditor.py",
            "--input",
            "docs/COMBINED_REGIME_HARDENING_2026-06-03.json",
            "--out-prefix",
            "docs/RESEARCH_GATE_AUDIT_COMBINED_REGIME_2026-06-03",
        ),
        cwd=ROOT,
        timeout_s=60,
        network_note="local report audit only; no orders; no trade permission",
    ),
    "combined_regime_walkforward": TaskSpec(
        title="Combined regime walk-forward",
        description="Runs train/test walk-forward on 15m/1h/4h combined-regime candidates.",
        command=python_cmd(
            "tools/combined_regime_walkforward.py",
            "--intervals",
            "15m,1h,4h",
            "--families",
            "donchian_breakout,ema_pullback_continuation,short_continuation_pressure",
            "--filter-modes",
            "none,risk_filters,all_filters",
            "--stop-grid",
            "1.5",
            "--take-grid",
            "2.0",
            "--hold-grid",
            "12",
            "--out-prefix",
            "docs/COMBINED_REGIME_WALKFORWARD_2026-06-03",
        ),
        cwd=ROOT,
        timeout_s=120,
        network_note="local public-data cache only; OOS research only; no orders; no trade permission",
    ),
    "combined_regime_failure_diagnostics": TaskSpec(
        title="Combined regime failure diagnostics",
        description="Groups combined-regime failures by timeframe, family, filter, side and exit reason.",
        command=python_cmd(
            "tools/combined_regime_failure_diagnostics.py",
            "--intervals",
            "15m,1h,4h",
            "--families",
            "donchian_breakout,ema_pullback_continuation,short_continuation_pressure",
            "--filter-modes",
            "none,risk_filters,all_filters",
            "--stop-grid",
            "1.5",
            "--take-grid",
            "2.0",
            "--hold-grid",
            "12",
            "--out-prefix",
            "docs/COMBINED_REGIME_FAILURE_DIAGNOSTICS_2026-06-04",
        ),
        cwd=ROOT,
        timeout_s=120,
        network_note="local diagnostics only; no orders; no trade permission",
    ),
    "strategy_polygon_parallel": TaskSpec(
        title="Strategy polygon parallel",
        description="Runs 50 parallel range/event-first research strategies on local BTCUSDT cache.",
        command=python_cmd(
            "tools/strategy_polygon_parallel.py",
            "--max-strategies",
            "50",
            "--workers",
            "8",
            "--out-prefix",
            "docs/STRATEGY_POLYGON_PARALLEL_50_2026-06-09",
        ),
        cwd=ROOT,
        timeout_s=180,
        network_note="local research polygon only; no orders; no trade permission",
    ),
    "strategy_polygon_100_parallel": TaskSpec(
        title="Strategy polygon 100",
        description="Runs 100 parallel mixed strategy hypotheses on local BTCUSDT cache.",
        command=python_cmd(
            "tools/strategy_polygon_parallel.py",
            "--max-strategies",
            "100",
            "--workers",
            "8",
            "--out-prefix",
            "docs/STRATEGY_POLYGON_PARALLEL_2026-06-09",
        ),
        cwd=ROOT,
        timeout_s=240,
        network_note="local research polygon only; no orders; no trade permission",
    ),
    "process_next_workspace_document": TaskSpec(
        title="Process next workspace document",
        description="Processes one unhandled workspace document, writes a note, updates registry and moves it to processed_docs.",
        command=python_cmd(
            "tools/workspace_document_processor.py",
            "--limit",
            "1",
            "--include-archive",
        ),
        cwd=ROOT,
        timeout_s=60,
        network_note="local document curation only; no orders; no trade permission",
    ),
    "downloads_candidate_scan": TaskSpec(
        title="Downloads candidate scan",
        description="Scans Downloads filenames for relevant trading research/runtime candidates while excluding sensitive files.",
        command=python_cmd(
            "tools/downloads_candidate_scanner.py",
            "--out-prefix",
            "docs/DOWNLOADS_TRADING_CANDIDATE_SCAN_2026-06-07",
        ),
        cwd=ROOT,
        timeout_s=60,
        network_note="filename scan only; no file import; no orders; no trade permission",
    ),
    "event_feature_factory": TaskSpec(
        title="Event feature factory",
        description="Tests engineered compression, inside-bar, false-breakout, OI and spot/perp features on local BTCUSDT cache.",
        command=python_cmd(
            "tools/event_feature_factory.py",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--max-strategies",
            "72",
            "--workers",
            "8",
            "--out-prefix",
            "docs/EVENT_FEATURE_FACTORY_2026-06-09",
        ),
        cwd=ROOT,
        timeout_s=240,
        network_note="local research feature factory only; no orders; no trade permission",
    ),
    "event_feature_holdout_validation": TaskSpec(
        title="Event feature holdout validation",
        description="Rechecks event-feature watchlist on the last chronological fold before any OOS/paper discussion.",
        command=python_cmd(
            "tools/event_feature_holdout_validator.py",
            "--source-report",
            "docs/EVENT_FEATURE_FACTORY_2026-06-09.json",
            "--cache-dir",
            "data/cache/binance_spot_perp_extended",
            "--out-prefix",
            "docs/EVENT_FEATURE_HOLDOUT_VALIDATION_2026-06-09",
        ),
        cwd=ROOT,
        timeout_s=180,
        network_note="local research holdout validation only; no orders; no trade permission",
    ),
    "canonical_regime_gate_overlay": TaskSpec(
        title="Canonical regime gate overlay",
        description="Tests Canonical Bot-Safe TREND/RANGE/SHOCK regime gate against current strategy-mix paper replay trades.",
        command=python_cmd(
            "tools/canonical_regime_gate_overlay.py",
            "--out-prefix",
            "docs/CANONICAL_REGIME_GATE_OVERLAY_2026-06-09",
        ),
        cwd=ROOT,
        timeout_s=90,
        network_note="local trade-level guard test only; no orders; no trade permission",
    ),
    "event_feature_trade_export": TaskSpec(
        title="Event feature trade export",
        description="Exports full trade-level data for current event-feature watchlist candidates before any promotion discussion.",
        command=python_cmd(
            "tools/event_feature_trade_exporter.py",
            "--promotion-report",
            "docs/CANDIDATE_PROMOTION_GATE_2026-06-04.json",
            "--out-prefix",
            "docs/EVENT_FEATURE_TRADE_EXPORT_2026-06-07",
        ),
        cwd=ROOT,
        timeout_s=120,
        network_note="local trade-level research export only; no orders; no trade permission",
    ),
    "risk_reward_gate": TaskSpec(
        title="Risk/Reward gate",
        description="Scores current research candidates by expectancy, payoff ratio, breakeven winrate, drawdown, folds and holdout status.",
        command=python_cmd(
            "tools/risk_reward_gate.py",
            "--out-prefix",
            "docs/RISK_REWARD_GATE_2026-06-04",
        ),
        cwd=ROOT,
        timeout_s=60,
        network_note="local research quality gate only; no orders; no trade permission",
    ),
    "portfolio_scenario_stress_guard_smoke": TaskSpec(
        title="Portfolio scenario stress guard",
        description="Runs a synthetic offline BTC/ETH collateral and linear-derivatives stress grid; not a Bybit WCE replica.",
        command=python_cmd(
            "tools/portfolio_scenario_stress_guard.py",
            "--out-prefix",
            "docs/PORTFOLIO_SCENARIO_STRESS_GUARD_SMOKE_2026-07-12",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="synthetic local risk smoke only; no private API, signals, paper entries, orders or trade permission",
    ),
    "portfolio_stress_promotion_gate": TaskSpec(
        title="Portfolio stress promotion gate",
        description="Requires a fresh hash-bound non-synthetic local paper-account snapshot; the current synthetic smoke must remain blocked.",
        command=python_cmd(
            "tools/portfolio_stress_promotion_gate.py",
            "--expect-blocked",
            "--out-prefix",
            "docs/PORTFOLIO_STRESS_PROMOTION_GATE_2026-07-12",
        ),
        cwd=ROOT,
        timeout_s=30,
        env={"BOT_ENV": "demo"},
        network_note="local fail-closed risk evidence gate only; no private API, signals, paper entries, orders or trade permission",
    ),
    "pretrade_guardian_smoke": TaskSpec(
        title="Pretrade guardian smoke",
        description="Runs deterministic pre-trade policy checks: RR, confirmations, stop method, funding, restricted windows, futures leverage/liquidation buffer and size reduction.",
        command=python_cmd(
            "tools/pretrade_guardian.py",
            "--demo",
            "--out-prefix",
            "docs/PRETRADE_GUARDIAN_SMOKE_2026-06-04",
        ),
        cwd=ROOT,
        timeout_s=60,
        network_note="local pre-trade policy smoke only; no orders; no trade permission",
    ),
    "btcusdt_futures_trade_card_smoke": TaskSpec(
        title="BTCUSDT futures trade card smoke",
        description="Validates the BTCUSDT futures trade-card input contract and then runs the card through Pretrade Guardian.",
        command=python_cmd(
            "tools/btc_futures_trade_card.py",
            "--demo",
            "--out-prefix",
            "docs/BTCUSDT_FUTURES_TRADE_CARD_SMOKE_2026-06-04",
        ),
        cwd=ROOT,
        timeout_s=60,
        network_note="local trade-card contract smoke only; no orders; no trade permission",
    ),
    "candidate_promotion_gate": TaskSpec(
        title="Candidate promotion gate",
        description="Evaluates research candidates for promotion into the live-review card pipeline. Promotion still grants no trade permission.",
        command=python_cmd(
            "tools/candidate_promotion_gate.py",
            "--out-prefix",
            "docs/CANDIDATE_PROMOTION_GATE_2026-06-04",
        ),
        cwd=ROOT,
        timeout_s=60,
        network_note="local research promotion gate only; no orders; no trade permission",
    ),
    "research_candidate_trade_card_smoke": TaskSpec(
        title="Research candidate trade-card smoke",
        description="Converts the latest historical research candidate into a BTCUSDT futures card draft, then blocks it as replay-only unless live data and gates are satisfied.",
        command=python_cmd(
            "tools/research_candidate_trade_card_builder.py",
            "--source",
            "_dl/control_panel/MAX_CORE_LITE_V13_STRUCTURAL_CANDIDATE.json",
            "--promotion-report",
            "docs/CANDIDATE_PROMOTION_GATE_2026-06-04.json",
            "--out-prefix",
            "docs/RESEARCH_CANDIDATE_TRADE_CARD_SMOKE_2026-06-04",
        ),
        cwd=ROOT,
        timeout_s=60,
        network_note="local research-to-card draft only; no orders; no trade permission",
    ),
    "btcusdt_futures_card_live_enrichment": TaskSpec(
        title="BTCUSDT futures card live enrichment",
        description="Enriches a BTCUSDT futures card with public mark, funding, OI and ATR context, then keeps it review-only.",
        command=python_cmd(
            "tools/btc_futures_card_live_enricher.py",
            "--input",
            "docs/RESEARCH_CANDIDATE_TRADE_CARD_SMOKE_2026-06-04.card.json",
            "--recenter-to-mark",
            "--out-prefix",
            "docs/BTCUSDT_FUTURES_CARD_LIVE_ENRICHMENT_2026-06-04",
        ),
        cwd=ROOT,
        timeout_s=60,
        network_note="public Binance market data only; no keys; no orders; no trade permission",
    ),
    "active_reference_runtime_extraction": TaskSpec(
        title="Active reference runtime extraction",
        description="Maps active docs/configs to real consumers, planned validators or reference-only status.",
        command=python_cmd(
            "tools/active_reference_runtime_mapper.py",
            "--out-prefix",
            "docs/ACTIVE_REFERENCE_RUNTIME_EXTRACTION_2026-06-02",
        ),
        cwd=ROOT,
        timeout_s=60,
        network_note="local-only; no orders; no trade permission",
    ),
    "arbiter_cti_demo": TaskSpec(
        title="Arbiter CTI overlay demo",
        description="Computes CTI 0..100 from normalized ETHBTC, BTC.D, OI, funding and stablecoin inputs.",
        command=python_cmd(
            "tools/overlay_signal_evaluator.py",
            "cti",
            "--ethbtc-trend",
            "0.7",
            "--btcd",
            "-0.6",
            "--oi-mix",
            "0.2",
            "--funding-compression",
            "0.3",
            "--stablecoin-inflow",
            "0.5",
            "--confirm-h4",
            "--confirm-d1",
            "--out",
            "_dl/control_panel/ARBITER_CTI_DEMO.json",
        ),
        cwd=ROOT,
        timeout_s=60,
        network_note="local-only overlay metric; no orders; no trade permission",
    ),
    "ethbtc_core_hedge_demo": TaskSpec(
        title="ETHBTC core/hedge overlay demo",
        description="Classifies ETHBTC daily role as core, hedge or risk-off from SMA200 and 30-day range.",
        command=python_cmd(
            "tools/overlay_signal_evaluator.py",
            "ethbtc",
            "--demo",
            "--out",
            "_dl/control_panel/ETHBTC_CORE_HEDGE_DEMO.json",
        ),
        cwd=ROOT,
        timeout_s=60,
        network_note="local-only portfolio overlay; no orders; no trade permission",
    ),
    "max_data_cache_update": TaskSpec(
        title="MAX public data cache",
        description="Updates local Binance public-data cache for BTCUSDT 15m/1h/4h futures, spot, OI and funding.",
        command=python_cmd(
            "tools/max_data_cache.py",
            "--symbol",
            "BTCUSDT",
            "--intervals",
            "15m,1h,4h",
            "--markets",
            "futures,spot",
            "--pages",
            "4",
            "--limit",
            "500",
            "--out-prefix",
            "_dl/control_panel/MAX_DATA_CACHE",
        ),
        cwd=ROOT,
        timeout_s=360,
        network_note="public Binance market data only; no keys; no orders",
    ),
    "max_core_lite_backtest_1h": TaskSpec(
        title="MAX Core Lite v0.2 BTC 1h backtest",
        description="Research-only v0.2 backtest: public Binance futures klines + OI/funding, no keys, no orders.",
        command=python_cmd(
            "tools/max_backtest.py",
            "--fetch-binance",
            "--fetch-derivatives",
            "--market",
            "futures",
            "--symbol",
            "BTCUSDT",
            "--interval",
            "1h",
            "--tf",
            "1h",
            "--limit",
            "500",
            "--pages",
            "4",
            "--strategy",
            "v02",
            "--allow-price-only",
            "--max-hold-bars",
            "12",
            "--out-prefix",
            "_dl/control_panel/MAX_CORE_LITE_BTC_1H_BACKTEST",
        ),
        cwd=ROOT,
        timeout_s=180,
        network_note="public Binance market data only; no keys; no orders",
    ),
    "max_core_lite_leaderboard_v03": TaskSpec(
        title="MAX Core Lite v0.8 discovery",
        description="Walk-forward discovery for score/v02/v03/v04/v05/v08 candidates with spot/perp context. Public data only.",
        command=python_cmd(
            "tools/max_backtest.py",
            "--fetch-binance",
            "--fetch-derivatives",
            "--market",
            "futures",
            "--symbol",
            "BTCUSDT",
            "--interval",
            "1h",
            "--tf",
            "1h",
            "--limit",
            "500",
            "--pages",
            "2",
            "--leaderboard",
            "--folds",
            "3",
            "--allow-price-only",
            "--out-prefix",
            "_dl/control_panel/MAX_CORE_LITE_V08_DISCOVERY",
        ),
        cwd=ROOT,
        timeout_s=360,
        network_note="public Binance market data only; no keys; no orders",
    ),
    "max_core_lite_v08_mined_short": TaskSpec(
        title="MAX Core Lite v0.8 mined short",
        description="Tests the v0.7 top mined slice as a strict short strategy over a wider sample. Research only.",
        command=python_cmd(
            "tools/max_backtest.py",
            "--fetch-binance",
            "--fetch-derivatives",
            "--market",
            "futures",
            "--symbol",
            "BTCUSDT",
            "--interval",
            "1h",
            "--tf",
            "1h",
            "--limit",
            "500",
            "--pages",
            "4",
            "--strategy",
            "v08_mined_short",
            "--stop-atr",
            "1.0",
            "--take-atr",
            "1.5",
            "--max-hold-bars",
            "16",
            "--allow-price-only",
            "--out-prefix",
            "_dl/control_panel/MAX_CORE_LITE_V08_MINED_SHORT",
        ),
        cwd=ROOT,
        timeout_s=240,
        network_note="public Binance market data only; no keys; no orders",
    ),
    "max_core_lite_event_export_v06": TaskSpec(
        title="MAX Core Lite v0.6 event export",
        description="Exports labelled market-event rows for alpha discovery. Public data only; no orders.",
        command=python_cmd(
            "tools/max_backtest.py",
            "--fetch-binance",
            "--fetch-derivatives",
            "--market",
            "futures",
            "--symbol",
            "BTCUSDT",
            "--interval",
            "1h",
            "--tf",
            "1h",
            "--limit",
            "500",
            "--pages",
            "2",
            "--export-events",
            "--event-forward-bars",
            "12",
            "--allow-price-only",
            "--out-prefix",
            "_dl/control_panel/MAX_CORE_LITE_V06_EVENTS",
        ),
        cwd=ROOT,
        timeout_s=180,
        network_note="public Binance market data only; no keys; no orders",
    ),
    "max_core_lite_feature_miner_v07": TaskSpec(
        title="MAX Core Lite v0.7 feature miner",
        description="Mines v0.6 event CSV for stable multi-factor slices. Run v0.6 first. Research only.",
        command=python_cmd(
            "tools/max_event_miner.py",
            "--events",
            "_dl/control_panel/MAX_CORE_LITE_V06_EVENTS.csv",
            "--out-prefix",
            "_dl/control_panel/MAX_CORE_LITE_V07_MINER",
            "--max-conditions",
            "3",
            "--folds",
            "4",
            "--min-events",
            "20",
            "--min-fold-events",
            "4",
            "--min-hit-pct",
            "55",
            "--min-edge-pct",
            "7",
            "--top",
            "30",
        ),
        cwd=ROOT,
        timeout_s=240,
        network_note="local analysis of v0.6 CSV; no keys; no orders",
    ),
    "max_core_lite_research_grid_v09": TaskSpec(
        title="MAX Core Lite v0.9 research grid",
        description="Runs event export + feature miner across 15m, 1h and 4h. Research only.",
        command=python_cmd(
            "tools/max_research_grid.py",
            "--out-prefix",
            "_dl/control_panel/MAX_CORE_LITE_V09_GRID",
            "--top",
            "20",
            "--min-events",
            "20",
            "--min-fold-events",
            "4",
            "--min-hit-pct",
            "55",
            "--min-edge-pct",
            "7",
        ),
        cwd=ROOT,
        timeout_s=420,
        network_note="public Binance market data only; no keys; no orders",
    ),
    "max_core_lite_hardening_v10": TaskSpec(
        title="MAX Core Lite v1.0 hardening",
        description="Strict backtest pack for the best v0.9 candidates on 15m, 1h and 4h. Research only.",
        command=python_cmd(
            "tools/max_candidate_hardening.py",
            "--out-prefix",
            "_dl/control_panel/MAX_CORE_LITE_V10_HARDENING",
            "--min-trades",
            "100",
            "--use-cache",
        ),
        cwd=ROOT,
        timeout_s=520,
        network_note="public Binance market data only; no keys; no orders",
    ),
    "max_core_lite_v11_weak_bid_validation": TaskSpec(
        title="MAX Core Lite v1.1 weak-bid validation",
        description="Deep validation for v10_1h_weak_bid_short with 100+ trade gate, folds and bootstrap. Research only.",
        command=python_cmd(
            "tools/max_v11_candidate_validator.py",
            "--pages",
            "24",
            "--limit",
            "500",
            "--bootstrap-iterations",
            "5000",
            "--out-prefix",
            "_dl/control_panel/MAX_CORE_LITE_V11_1H_WEAK_BID",
        ),
        cwd=ROOT,
        timeout_s=240,
        network_note="public Binance market data only; no keys; no orders",
    ),
    "max_core_lite_v12_regime_isolation": TaskSpec(
        title="MAX Core Lite v1.2 regime isolation",
        description="Slice-mines v1.1 executed trades for structural pre-trade regimes. Research only.",
        command=python_cmd(
            "tools/max_v12_regime_isolation.py",
            "--source",
            "_dl/control_panel/MAX_CORE_LITE_V11_1H_WEAK_BID.json",
            "--out-prefix",
            "_dl/control_panel/MAX_CORE_LITE_V12_REGIME_ISOLATION",
            "--min-trades",
            "15",
            "--max-conditions",
            "2",
            "--top",
            "40",
        ),
        cwd=ROOT,
        timeout_s=90,
        network_note="local analysis of v1.1 report; no keys; no orders",
    ),
    "max_core_lite_v13_structural_candidate": TaskSpec(
        title="MAX Core Lite v1.3 structural candidate",
        description="Tests the best v1.2 structural lead as fresh raw-data candidates with folds and bootstrap. Research only.",
        command=python_cmd(
            "tools/max_v13_structural_candidate.py",
            "--pages",
            "24",
            "--limit",
            "500",
            "--bootstrap-iterations",
            "5000",
            "--out-prefix",
            "_dl/control_panel/MAX_CORE_LITE_V13_STRUCTURAL_CANDIDATE",
        ),
        cwd=ROOT,
        timeout_s=300,
        network_note="public Binance market data only; no keys; no orders",
    ),
    "max_core_lite_v14_long_expansion": TaskSpec(
        title="MAX Core Lite v1.4 LONG expansion",
        description="Runs larger-sample LONG/SHORT expansion for the v1.2/v1.3 structural lead. Research only.",
        command=python_cmd(
            "tools/max_v14_long_expansion.py",
            "--pages",
            "24",
            "--limit",
            "1000",
            "--bootstrap-iterations",
            "5000",
            "--out-prefix",
            "_dl/control_panel/MAX_CORE_LITE_V14_LONG_EXPANSION",
        ),
        cwd=ROOT,
        timeout_s=360,
        network_note="public Binance market data only; no keys; no orders",
    ),
    "max_core_lite_v15_state_filters": TaskSpec(
        title="MAX Core Lite v1.5 state filters",
        description="Tests OI/funding, liquidity sweep and HTF-regime filters over the v1.3/v1.4 structural lead. Research only.",
        command=python_cmd(
            "tools/max_v15_state_filters.py",
            "--pages",
            "24",
            "--limit",
            "1000",
            "--htf-pages",
            "8",
            "--derivatives-pages",
            "48",
            "--bootstrap-iterations",
            "3000",
            "--out-prefix",
            "_dl/control_panel/MAX_CORE_LITE_V15_STATE_FILTERS",
        ),
        cwd=ROOT,
        timeout_s=420,
        network_note="public Binance market data only; no keys; no orders",
    ),
    "max_core_lite_v16_event_first_miner": TaskSpec(
        title="MAX Core Lite v1.6 event-first miner",
        description="Mines OI/funding, liquidity sweep and HTF-regime as primary events, then validates top candidates. Research only.",
        command=python_cmd(
            "tools/max_v16_event_first_miner.py",
            "--pages",
            "24",
            "--limit",
            "1000",
            "--htf-pages",
            "8",
            "--derivatives-pages",
            "48",
            "--bootstrap-iterations",
            "3000",
            "--out-prefix",
            "_dl/control_panel/MAX_CORE_LITE_V16_EVENT_FIRST_MINER",
        ),
        cwd=ROOT,
        timeout_s=520,
        network_note="public Binance market data only; no keys; no orders",
    ),
    "max_core_lite_v17_short_continuation_hardening": TaskSpec(
        title="MAX Core Lite v1.7 short-continuation hardening",
        description="Targeted hardening for the v1.6 short-continuation lead across 30m/1h/2h and exit grids. Research only.",
        command=python_cmd(
            "tools/max_v17_short_continuation_hardening.py",
            "--intervals",
            "30m,1h,2h",
            "--pages",
            "24",
            "--limit",
            "1000",
            "--htf-pages",
            "8",
            "--derivatives-pages",
            "48",
            "--bootstrap-iterations",
            "3000",
            "--out-prefix",
            "_dl/control_panel/MAX_CORE_LITE_V17_SHORT_CONTINUATION_HARDENING",
        ),
        cwd=ROOT,
        timeout_s=720,
        network_note="public Binance market data only; no keys; no orders",
    ),
}


JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


def base_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update(SAFE_ENV_SCRUB)
    env["PYTHONIOENCODING"] = "utf-8"
    if extra:
        env.update(extra)
    return env


def save_job(job: dict[str, Any]) -> None:
    ensure_dirs()
    (JOBS_DIR / f"{job['id']}.json").write_text(
        json.dumps(job, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_recent_jobs(limit: int = 20) -> list[dict[str, Any]]:
    ensure_dirs()
    jobs: list[dict[str, Any]] = []
    for path in sorted(JOBS_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        payload = read_json(path)
        if isinstance(payload, dict):
            jobs.append(payload)
        if len(jobs) >= limit:
            break
    return jobs


def run_job(job_id: str, task_id: str) -> None:
    spec = TASKS[task_id]
    started = time.monotonic()
    with JOBS_LOCK:
        job = JOBS[job_id]
        job.update({"status": "running", "started_at": now_iso()})
        save_job(job)

    try:
        result = subprocess.run(
            spec.command,
            cwd=spec.cwd,
            env=base_env(spec.env),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=spec.timeout_s,
            check=False,
        )
        exit_code = int(result.returncode)
        ok = exit_code in spec.expected_exit_codes
        status = "success" if ok else "failed"
        if ok and spec.expected_exit_codes != (0,):
            status = "expected_fail"
        finished_job = {
            "status": status,
            "finished_at": now_iso(),
            "duration_s": round(time.monotonic() - started, 3),
            "exit_code": exit_code,
            "stdout": tail_text(result.stdout),
            "stderr": tail_text(result.stderr),
        }
    except subprocess.TimeoutExpired as exc:
        finished_job = {
            "status": "timeout",
            "finished_at": now_iso(),
            "duration_s": round(time.monotonic() - started, 3),
            "exit_code": None,
            "stdout": tail_text(exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")),
            "stderr": tail_text(exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")),
        }
    except Exception as exc:  # noqa: BLE001
        finished_job = {
            "status": "failed",
            "finished_at": now_iso(),
            "duration_s": round(time.monotonic() - started, 3),
            "exit_code": None,
            "stdout": "",
            "stderr": str(exc),
        }

    with JOBS_LOCK:
        JOBS[job_id].update(finished_job)
        save_job(JOBS[job_id])


def start_job(task_id: str) -> dict[str, Any]:
    if task_id not in TASKS:
        raise KeyError(task_id)
    spec = TASKS[task_id]
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "task_id": task_id,
        "title": spec.title,
        "description": spec.description,
        "status": "queued",
        "created_at": now_iso(),
        "started_at": None,
        "finished_at": None,
        "duration_s": None,
        "exit_code": None,
        "command": " ".join(spec.command),
        "cwd": str(spec.cwd),
        "stdout": "",
        "stderr": "",
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
        save_job(job)
    thread = threading.Thread(target=run_job, args=(job_id, task_id), daemon=True)
    thread.start()
    return job


def smoke_summary() -> dict[str, Any]:
    preflight = read_json(SMOKE_DIR / "MAX_OPS_PREFLIGHT.json")
    risk = read_json(SMOKE_DIR / "risk_of_ruin.json")
    rule_hits = read_json(SMOKE_DIR / "rule_hits.json")
    dex_state = read_json(SMOKE_DIR / "dex_state.json")
    return {
        "preflight_all_ok": bool(isinstance(preflight, dict) and preflight.get("all_ok")),
        "preflight": preflight,
        "risk_of_ruin_exists": (SMOKE_DIR / "risk_of_ruin.json").exists(),
        "rule_hits_exists": (SMOKE_DIR / "rule_hits.json").exists(),
        "dex_state_exists": (SMOKE_DIR / "dex_state.json").exists(),
        "risk_of_ruin": risk,
        "rule_hits_preview": rule_hits,
        "dex_state": dex_state,
    }


def dex_journal_summary() -> dict[str, Any]:
    path = SMOKE_DIR / "dex_journal.jsonl"
    if not path.exists():
        return {"exists": False, "events": 0, "last_event": None}
    lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    last_event = None
    if lines:
        try:
            last_event = json.loads(lines[-1])
        except json.JSONDecodeError:
            last_event = {"raw": lines[-1]}
    return {"exists": True, "events": len(lines), "last_event": last_event}


def latest_backtest_summary() -> dict[str, Any]:
    candidates = (
        list((ROOT / "_dl" / "max_backtest").glob("*.json"))
        + list(OUT_DIR.glob("*BACKTEST.json"))
        + list(OUT_DIR.glob("*MINED_SHORT.json"))
    )
    if not candidates:
        return {"exists": False}
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": True, "path": str(path), "read_error": True}
    return {
        "exists": True,
        "path": str(path),
        "summary": payload.get("summary"),
        "research_gate": payload.get("research_gate"),
        "params": payload.get("params"),
    }


def latest_data_cache_summary() -> dict[str, Any]:
    candidates = list((ROOT / "_dl" / "cache").glob("MAX_DATA_CACHE*.json")) + list(OUT_DIR.glob("MAX_DATA_CACHE.json"))
    if not candidates:
        return {"exists": False}
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": True, "path": str(path), "read_error": True}
    compact = [
        {
            "kind": item.get("kind"),
            "market": item.get("market", "futures"),
            "interval": item.get("interval"),
            "rows": item.get("merged_rows", item.get("aligned_rows")),
            "path": item.get("path", item.get("aligned_path")),
        }
        for item in payload.get("artifacts", [])
        if isinstance(item, dict)
    ]
    return {
        "exists": True,
        "path": str(path),
        "symbol": payload.get("symbol"),
        "cache_dir": payload.get("cache_dir"),
        "artifacts": compact,
    }


def latest_v11_candidate_summary() -> dict[str, Any]:
    candidates = list((ROOT / "_dl" / "v11").glob("*.json")) + list(OUT_DIR.glob("*V11*.json"))
    if not candidates:
        return {"exists": False}
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": True, "path": str(path), "read_error": True}
    return {
        "exists": True,
        "path": str(path),
        "candidate": payload.get("candidate"),
        "summary": payload.get("summary"),
        "research_gate": payload.get("research_gate"),
        "decision": payload.get("decision"),
        "data": payload.get("data"),
    }


def latest_v12_regime_summary() -> dict[str, Any]:
    candidates = list((ROOT / "_dl" / "v12").glob("*.json")) + list(OUT_DIR.glob("*V12*.json"))
    if not candidates:
        return {"exists": False}
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": True, "path": str(path), "read_error": True}
    top = payload.get("top_slices") if isinstance(payload.get("top_slices"), list) else []
    return {
        "exists": True,
        "path": str(path),
        "baseline": payload.get("baseline"),
        "top_slice": top[0] if top else None,
        "pass_slices": len(payload.get("pass_slices", [])) if isinstance(payload.get("pass_slices"), list) else None,
        "decision": payload.get("decision"),
    }


def latest_v13_candidate_summary() -> dict[str, Any]:
    candidates = list((ROOT / "_dl" / "v13").glob("*.json")) + list(OUT_DIR.glob("*V13*.json"))
    if not candidates:
        return {"exists": False}
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": True, "path": str(path), "read_error": True}
    best = payload.get("best_candidate") if isinstance(payload.get("best_candidate"), dict) else None
    return {
        "exists": True,
        "path": str(path),
        "best_candidate": {
            "id": best.get("id") if best else None,
            "summary": best.get("summary") if best else None,
            "research_gate": best.get("research_gate") if best else None,
        },
        "passed": len(payload.get("passed", [])) if isinstance(payload.get("passed"), list) else None,
        "decision": payload.get("decision"),
    }


def latest_v14_expansion_summary() -> dict[str, Any]:
    candidates = list((ROOT / "_dl" / "v14").glob("*.json")) + list(OUT_DIR.glob("*V14*.json"))
    if not candidates:
        return {"exists": False}
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": True, "path": str(path), "read_error": True}
    best = payload.get("best_candidate") if isinstance(payload.get("best_candidate"), dict) else None
    return {
        "exists": True,
        "path": str(path),
        "best_candidate": {
            "id": best.get("id") if best else None,
            "side": best.get("side") if best else None,
            "summary": best.get("summary") if best else None,
            "research_gate": best.get("research_gate") if best else None,
        },
        "passed": len(payload.get("passed", [])) if isinstance(payload.get("passed"), list) else None,
        "decision": payload.get("decision"),
    }


def latest_v15_state_summary() -> dict[str, Any]:
    candidates = list((ROOT / "_dl" / "v15").glob("*.json")) + list(OUT_DIR.glob("*V15*.json"))
    if not candidates:
        return {"exists": False}
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": True, "path": str(path), "read_error": True}
    best = payload.get("best_candidate") if isinstance(payload.get("best_candidate"), dict) else None
    return {
        "exists": True,
        "path": str(path),
        "best_candidate": {
            "id": best.get("id") if best else None,
            "side": best.get("side") if best else None,
            "summary": best.get("summary") if best else None,
            "research_gate": best.get("research_gate") if best else None,
        },
        "passed": len(payload.get("passed", [])) if isinstance(payload.get("passed"), list) else None,
        "decision": payload.get("decision"),
    }


def latest_v16_event_first_summary() -> dict[str, Any]:
    candidates = list((ROOT / "_dl" / "v16").glob("*.json")) + list(OUT_DIR.glob("*V16*.json"))
    if not candidates:
        return {"exists": False}
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": True, "path": str(path), "read_error": True}
    best = payload.get("best_candidate") if isinstance(payload.get("best_candidate"), dict) else None
    return {
        "exists": True,
        "path": str(path),
        "events": (payload.get("data") or {}).get("events") if isinstance(payload.get("data"), dict) else None,
        "best_candidate": {
            "id": best.get("id") if best else None,
            "side": best.get("side") if best else None,
            "summary": best.get("summary") if best else None,
            "research_gate": best.get("research_gate") if best else None,
        },
        "validated": len(payload.get("validated_candidates", [])) if isinstance(payload.get("validated_candidates"), list) else None,
        "passed": len(payload.get("passed", [])) if isinstance(payload.get("passed"), list) else None,
        "decision": payload.get("decision"),
    }


def latest_v17_short_continuation_summary() -> dict[str, Any]:
    candidates = list((ROOT / "_dl" / "v17").glob("*.json")) + list(OUT_DIR.glob("*V17*.json"))
    if not candidates:
        return {"exists": False}
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": True, "path": str(path), "read_error": True}
    best = payload.get("best_candidate") if isinstance(payload.get("best_candidate"), dict) else None
    return {
        "exists": True,
        "path": str(path),
        "tested": sum(len(report.get("results", [])) for report in payload.get("interval_reports", []) if isinstance(report, dict)),
        "best_candidate": {
            "id": best.get("id") if best else None,
            "interval": best.get("interval") if best else None,
            "summary": best.get("summary") if best else None,
            "research_gate": best.get("research_gate") if best else None,
        },
        "passed": len(payload.get("passed", [])) if isinstance(payload.get("passed"), list) else None,
        "decision": payload.get("decision"),
    }


def _read_jsonl_tail(path: Path, limit: int = 5) -> tuple[int, list[dict[str, Any]]]:
    if not path.exists():
        return 0, []
    rows: list[dict[str, Any]] = []
    count = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        count += 1
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
            rows = rows[-limit:]
    return count, rows


def latest_market_state_alerts_summary() -> dict[str, Any]:
    composite_candidates = (
        list(OUT_DIR.glob("*COMPOSITE.json"))
        + list((ROOT / "_dl" / "v18").glob("*.json"))
        + list((ROOT / "_dl" / "audit").glob("*COMPOSITE*.json"))
        + list((ROOT / "_dl").glob("LAST_COMPOSITE.json"))
    )
    composite_path = max(composite_candidates, key=lambda item: item.stat().st_mtime) if composite_candidates else None
    composite_payload = read_json(composite_path) if composite_path else None
    market_alerts = composite_payload.get("market_state_alerts") if isinstance(composite_payload, dict) else {}
    active_alerts = []
    if isinstance(market_alerts, dict):
        active = market_alerts.get("active")
        if isinstance(active, list):
            active_alerts = [item for item in active if isinstance(item, dict)]
    log_path = ROOT / "logs" / "market_state_alerts" / "market_state_alerts.jsonl"
    tracker_path = ROOT / "logs" / "market_state_alerts" / "forward_tracker.jsonl"
    log_events, log_tail = _read_jsonl_tail(log_path)
    tracker_rows, tracker_tail = _read_jsonl_tail(tracker_path)
    pending = sum(1 for item in tracker_tail if item.get("status") == "pending")
    resolved = sum(1 for item in tracker_tail if item.get("status") == "resolved")

    v19_candidates = list(OUT_DIR.glob("*V19_ALERT_OBSERVABILITY.json"))
    v19_path = max(v19_candidates, key=lambda item: item.stat().st_mtime) if v19_candidates else None
    v19_payload = read_json(v19_path) if v19_path else None

    return {
        "exists": composite_path is not None,
        "composite_path": str(composite_path) if composite_path else None,
        "composite_generated_at": composite_payload.get("generated_at") if isinstance(composite_payload, dict) else None,
        "active_count": len(active_alerts),
        "active_alerts": active_alerts,
        "entry_permission": market_alerts.get("entry_permission") if isinstance(market_alerts, dict) else None,
        "log_path": str(log_path),
        "log_events": log_events,
        "log_tail": log_tail,
        "tracker_path": str(tracker_path),
        "tracker_rows": tracker_rows,
        "tracker_tail_pending_in_sample": pending,
        "tracker_tail_resolved_in_sample": resolved,
        "latest_v19": {
            "exists": v19_path is not None,
            "path": str(v19_path) if v19_path else None,
            "generated_at": v19_payload.get("generated_at") if isinstance(v19_payload, dict) else None,
            "tracker": v19_payload.get("tracker") if isinstance(v19_payload, dict) else None,
            "log": v19_payload.get("log") if isinstance(v19_payload, dict) else None,
            "policy": v19_payload.get("policy") if isinstance(v19_payload, dict) else None,
        },
    }


def latest_forward_evidence_summary() -> dict[str, Any]:
    candidates = list(OUT_DIR.glob("*V20_FORWARD_EVIDENCE.json"))
    if not candidates:
        return {"exists": False}
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": True, "path": str(path), "read_error": True}
    overall = payload.get("overall") if isinstance(payload.get("overall"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "generated_at": payload.get("generated_at"),
        "classification": overall.get("classification"),
        "resolved": overall.get("resolved"),
        "pending": overall.get("pending"),
        "hit_rate_pct": overall.get("hit_rate_pct"),
        "avg_directional_return_pct": overall.get("avg_directional_return_pct"),
        "groups": payload.get("groups") if isinstance(payload.get("groups"), list) else [],
        "policy": payload.get("policy") if isinstance(payload.get("policy"), dict) else None,
    }


def latest_strategy_mix_forward_feed_summary() -> dict[str, Any]:
    state_path = ROOT / "logs" / "forward_paper_feed" / "strategy_mix_forward_paper_feed_state.json"
    journal_path = ROOT / "logs" / "forward_paper_feed" / "strategy_mix_forward_paper_feed.jsonl"
    card_json_path = ROOT / "logs" / "forward_paper_feed" / "latest_signal_card.json"
    card_md_path = ROOT / "logs" / "forward_paper_feed" / "latest_signal_card.md"
    report_path = ROOT / "docs" / "STRATEGY_MIX_FORWARD_PAPER_FEED_2026-06-08.json"
    scoreboard_path = ROOT / "docs" / "STRATEGY_MIX_FORWARD_SCOREBOARD_2026-06-08.json"
    scheduler_path = ROOT / "docs" / "STRATEGY_MIX_FORWARD_SCHEDULER_2026-06-08.json"
    telegram_path = ROOT / "docs" / "STRATEGY_MIX_FORWARD_TELEGRAM_NOTIFY_2026-06-08.json"
    regime_observer_path = ROOT / "docs" / "CANONICAL_REGIME_FORWARD_OBSERVER_2026-06-09.json"
    oi_funding_context_path = ROOT / "docs" / "OI_FUNDING_FORWARD_CONTEXT_OBSERVER_2026-06-09.json"
    oi_funding_scoreboard_path = ROOT / "docs" / "OI_FUNDING_FORWARD_CONTEXT_SCOREBOARD_2026-06-15.json"
    oi_funding_replay_audit_path = ROOT / "docs" / "STRATEGY_MIX_OI_FUNDING_REPLAY_AUDIT_2026-06-15.json"
    oi_guard_promotion_gate_path = ROOT / "docs" / "OI_GUARD_PROMOTION_GATE_2026-06-15.json"
    forward_outcome_accumulator_path = ROOT / "docs" / "FORWARD_OUTCOME_ACCUMULATOR_2026-06-16.json"
    entry_scarcity_diagnostic_path = ROOT / "docs" / "FORWARD_ENTRY_SCARCITY_DIAGNOSTIC_2026-06-16.json"
    shadow_relaxation_validator_path = ROOT / "docs" / "FORWARD_SHADOW_RELAXATION_VALIDATOR_2026-06-16.json"
    range_family_validator_path = ROOT / "docs" / "RANGE_FAMILY_VALIDATOR_2026-06-16.json"
    range_watchlist_refiner_path = ROOT / "docs" / "RANGE_WATCHLIST_REFINER_2026-06-16.json"
    range_refined_observer_path = ROOT / "docs" / "RANGE_REFINED_FORWARD_OBSERVER_2026-06-16.json"
    range_refined_scoreboard_path = ROOT / "docs" / "RANGE_REFINED_OBSERVER_SCOREBOARD_2026-06-16.json"
    range_refined_scarcity_path = ROOT / "docs" / "RANGE_REFINED_SIGNAL_SCARCITY_DIAGNOSTIC_2026-06-17.json"
    range_refined_pending_watch_path = ROOT / "docs" / "RANGE_REFINED_PENDING_WATCH_2026-06-17.json"
    range_refined_pending_watch_notify_path = ROOT / "docs" / "RANGE_REFINED_PENDING_WATCH_TELEGRAM_NOTIFY_2026-06-18.json"
    edge_forward_observer_path = ROOT / "docs" / "EDGE_FORWARD_RANGE_OBSERVER_2026-06-18.json"
    edge_forward_scoreboard_path = ROOT / "docs" / "EDGE_FORWARD_RANGE_SCOREBOARD_2026-06-18.json"
    edge_liquidation_context_path = ROOT / "docs" / "EDGE_LIQUIDATION_CONTEXT_SHADOW_OBSERVER_2026-06-23.json"
    edge_liquidation_context_scoreboard_path = ROOT / "docs" / "EDGE_LIQUIDATION_CONTEXT_SHADOW_SCOREBOARD_2026-06-23.json"
    edge_liquidation_score_evidence_gate_path = ROOT / "docs" / "EDGE_LIQUIDATION_SCORE_EVIDENCE_GATE_2026-06-23.json"
    edge_liquidation_context_replay_path = ROOT / "docs" / "EDGE_LIQUIDATION_CONTEXT_HISTORICAL_REPLAY_2026-06-23.json"
    edge_forward_pending_watch_path = ROOT / "docs" / "EDGE_FORWARD_PENDING_WATCH_2026-06-18.json"
    edge_forward_pending_watch_notify_path = ROOT / "docs" / "EDGE_FORWARD_PENDING_WATCH_TELEGRAM_NOTIFY_2026-06-18.json"
    edge_forward_promotion_gate_path = ROOT / "docs" / "EDGE_FORWARD_PROMOTION_GATE_2026-06-18.json"
    range_refined_filter_ablation_path = ROOT / "docs" / "RANGE_REFINED_FILTER_SHADOW_ABLATION_2026-06-17.json"
    range_refined_shadow_forward_path = ROOT / "docs" / "RANGE_REFINED_FILTER_SHADOW_FORWARD_OBSERVER_2026-06-17.json"
    range_refined_shadow_forward_scoreboard_path = ROOT / "docs" / "RANGE_REFINED_FILTER_SHADOW_FORWARD_SCOREBOARD_2026-06-17.json"
    range_refined_shadow_promotion_gate_path = ROOT / "docs" / "RANGE_REFINED_FILTER_SHADOW_PROMOTION_GATE_2026-06-17.json"
    range_refined_promotion_gate_path = ROOT / "docs" / "RANGE_REFINED_PROMOTION_GATE_2026-06-17.json"
    range_refined_alert_guard_path = ROOT / "docs" / "RANGE_REFINED_SIGNAL_ALERT_GUARD_2026-06-16.json"
    range_refined_alert_drill_path = ROOT / "docs" / "RANGE_REFINED_SIGNAL_ALERT_DRILL_2026-06-17.json"
    state = read_json(state_path) if state_path.exists() else None
    card = read_json(card_json_path) if card_json_path.exists() else None
    report = read_json(report_path) if report_path.exists() else None
    scoreboard = read_json(scoreboard_path) if scoreboard_path.exists() else None
    scheduler = read_json(scheduler_path) if scheduler_path.exists() else None
    telegram = read_json(telegram_path) if telegram_path.exists() else None
    regime_observer = read_json(regime_observer_path) if regime_observer_path.exists() else None
    oi_funding_context = read_json(oi_funding_context_path) if oi_funding_context_path.exists() else None
    oi_funding_scoreboard = read_json(oi_funding_scoreboard_path) if oi_funding_scoreboard_path.exists() else None
    oi_funding_replay_audit = read_json(oi_funding_replay_audit_path) if oi_funding_replay_audit_path.exists() else None
    oi_guard_promotion_gate = read_json(oi_guard_promotion_gate_path) if oi_guard_promotion_gate_path.exists() else None
    forward_outcome_accumulator = read_json(forward_outcome_accumulator_path) if forward_outcome_accumulator_path.exists() else None
    entry_scarcity_diagnostic = read_json(entry_scarcity_diagnostic_path) if entry_scarcity_diagnostic_path.exists() else None
    shadow_relaxation_validator = read_json(shadow_relaxation_validator_path) if shadow_relaxation_validator_path.exists() else None
    range_family_validator = read_json(range_family_validator_path) if range_family_validator_path.exists() else None
    range_watchlist_refiner = read_json(range_watchlist_refiner_path) if range_watchlist_refiner_path.exists() else None
    range_refined_observer = read_json(range_refined_observer_path) if range_refined_observer_path.exists() else None
    range_refined_scoreboard = read_json(range_refined_scoreboard_path) if range_refined_scoreboard_path.exists() else None
    range_refined_scarcity = read_json(range_refined_scarcity_path) if range_refined_scarcity_path.exists() else None
    range_refined_pending_watch = read_json(range_refined_pending_watch_path) if range_refined_pending_watch_path.exists() else None
    range_refined_pending_watch_notify = read_json(range_refined_pending_watch_notify_path) if range_refined_pending_watch_notify_path.exists() else None
    edge_forward_observer = read_json(edge_forward_observer_path) if edge_forward_observer_path.exists() else None
    edge_forward_scoreboard = read_json(edge_forward_scoreboard_path) if edge_forward_scoreboard_path.exists() else None
    edge_liquidation_context = read_json(edge_liquidation_context_path) if edge_liquidation_context_path.exists() else None
    edge_liquidation_context_scoreboard = (
        read_json(edge_liquidation_context_scoreboard_path) if edge_liquidation_context_scoreboard_path.exists() else None
    )
    edge_liquidation_score_evidence_gate = (
        read_json(edge_liquidation_score_evidence_gate_path) if edge_liquidation_score_evidence_gate_path.exists() else None
    )
    edge_liquidation_context_replay = (
        read_json(edge_liquidation_context_replay_path) if edge_liquidation_context_replay_path.exists() else None
    )
    edge_forward_pending_watch = read_json(edge_forward_pending_watch_path) if edge_forward_pending_watch_path.exists() else None
    edge_forward_pending_watch_notify = read_json(edge_forward_pending_watch_notify_path) if edge_forward_pending_watch_notify_path.exists() else None
    edge_forward_promotion_gate = read_json(edge_forward_promotion_gate_path) if edge_forward_promotion_gate_path.exists() else None
    range_refined_filter_ablation = read_json(range_refined_filter_ablation_path) if range_refined_filter_ablation_path.exists() else None
    range_refined_shadow_forward = read_json(range_refined_shadow_forward_path) if range_refined_shadow_forward_path.exists() else None
    range_refined_shadow_forward_scoreboard = read_json(range_refined_shadow_forward_scoreboard_path) if range_refined_shadow_forward_scoreboard_path.exists() else None
    range_refined_shadow_promotion_gate = read_json(range_refined_shadow_promotion_gate_path) if range_refined_shadow_promotion_gate_path.exists() else None
    range_refined_promotion_gate = read_json(range_refined_promotion_gate_path) if range_refined_promotion_gate_path.exists() else None
    range_refined_alert_guard = read_json(range_refined_alert_guard_path) if range_refined_alert_guard_path.exists() else None
    range_refined_alert_drill = read_json(range_refined_alert_drill_path) if range_refined_alert_drill_path.exists() else None
    range_refiner_selected = (
        range_watchlist_refiner.get("selected_candidate")
        if isinstance(range_watchlist_refiner, dict) and isinstance(range_watchlist_refiner.get("selected_candidate"), dict)
        else {}
    )
    range_refiner_selected_cost10 = (
        next(
            (
                row
                for row in range_refiner_selected.get("cost_stress", [])
                if isinstance(row, dict) and row.get("extra_bps_per_side") == 10.0
            ),
            None,
        )
        if isinstance(range_refiner_selected, dict)
        else None
    )
    range_refined_latest = (
        range_refined_observer.get("latest_result")
        if isinstance(range_refined_observer, dict) and isinstance(range_refined_observer.get("latest_result"), dict)
        else {}
    )
    range_refined_score_summary = (
        range_refined_scoreboard.get("summary")
        if isinstance(range_refined_scoreboard, dict) and isinstance(range_refined_scoreboard.get("summary"), dict)
        else {}
    )
    edge_forward_latest = (
        edge_forward_observer.get("latest_result")
        if isinstance(edge_forward_observer, dict) and isinstance(edge_forward_observer.get("latest_result"), dict)
        else {}
    )
    edge_forward_score_summary = (
        edge_forward_scoreboard.get("summary")
        if isinstance(edge_forward_scoreboard, dict) and isinstance(edge_forward_scoreboard.get("summary"), dict)
        else {}
    )
    edge_liquidation_latest = (
        edge_liquidation_context.get("latest")
        if isinstance(edge_liquidation_context, dict) and isinstance(edge_liquidation_context.get("latest"), dict)
        else {}
    )
    latest_regime = regime_observer.get("latest_regime") if isinstance(regime_observer, dict) and isinstance(regime_observer.get("latest_regime"), dict) else {}
    oi_context = oi_funding_context.get("context") if isinstance(oi_funding_context, dict) and isinstance(oi_funding_context.get("context"), dict) else {}
    oi_freshness = oi_funding_context.get("freshness") if isinstance(oi_funding_context, dict) and isinstance(oi_funding_context.get("freshness"), dict) else {}
    oi_guard_candidate = (
        oi_funding_context.get("oi_guard_candidate")
        if isinstance(oi_funding_context, dict) and isinstance(oi_funding_context.get("oi_guard_candidate"), dict)
        else {}
    )
    oi_score_summary = oi_funding_scoreboard.get("summary") if isinstance(oi_funding_scoreboard, dict) and isinstance(oi_funding_scoreboard.get("summary"), dict) else {}
    oi_score_counts = oi_funding_scoreboard.get("counts") if isinstance(oi_funding_scoreboard, dict) and isinstance(oi_funding_scoreboard.get("counts"), dict) else {}
    latest_oi_score_guard = (
        oi_funding_scoreboard.get("latest_oi_guard_candidate")
        if isinstance(oi_funding_scoreboard, dict) and isinstance(oi_funding_scoreboard.get("latest_oi_guard_candidate"), dict)
        else {}
    )
    oi_replay_audit_summary = oi_funding_replay_audit.get("summary") if isinstance(oi_funding_replay_audit, dict) and isinstance(oi_funding_replay_audit.get("summary"), dict) else {}
    accumulator_metrics = (
        forward_outcome_accumulator.get("metrics")
        if isinstance(forward_outcome_accumulator, dict) and isinstance(forward_outcome_accumulator.get("metrics"), dict)
        else {}
    )
    accumulator_gates = (
        forward_outcome_accumulator.get("gates")
        if isinstance(forward_outcome_accumulator, dict) and isinstance(forward_outcome_accumulator.get("gates"), dict)
        else {}
    )
    scoreboard_summary = scoreboard.get("summary") if isinstance(scoreboard, dict) and isinstance(scoreboard.get("summary"), dict) else {}
    journal_events, journal_tail = _read_jsonl_tail(journal_path, 20)
    event_counts: dict[str, int] = {}
    for item in journal_tail:
        event_type = str(item.get("event_type") or "unknown")
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
    latest_event = journal_tail[-1] if journal_tail else None
    latest_result = report.get("latest_result") if isinstance(report, dict) and isinstance(report.get("latest_result"), dict) else {}
    last_status = None
    if isinstance(card, dict):
        last_status = card.get("status")
    if last_status is None and isinstance(state, dict):
        last_status = state.get("last_status")
    if last_status is None:
        last_status = latest_result.get("status")
    return {
        "exists": state_path.exists() or journal_path.exists() or card_json_path.exists() or report_path.exists(),
        "state_path": str(state_path),
        "journal_path": str(journal_path),
        "card_json_path": str(card_json_path) if card_json_path.exists() else None,
        "card_md_path": str(card_md_path) if card_md_path.exists() else None,
        "report_path": str(report_path) if report_path.exists() else None,
        "scoreboard_path": str(scoreboard_path) if scoreboard_path.exists() else None,
        "scheduler_path": str(scheduler_path) if scheduler_path.exists() else None,
        "telegram_path": str(telegram_path) if telegram_path.exists() else None,
        "regime_observer_path": str(regime_observer_path) if regime_observer_path.exists() else None,
        "oi_funding_context_path": str(oi_funding_context_path) if oi_funding_context_path.exists() else None,
        "oi_funding_scoreboard_path": str(oi_funding_scoreboard_path) if oi_funding_scoreboard_path.exists() else None,
        "oi_funding_replay_audit_path": str(oi_funding_replay_audit_path) if oi_funding_replay_audit_path.exists() else None,
        "oi_guard_promotion_gate_path": str(oi_guard_promotion_gate_path) if oi_guard_promotion_gate_path.exists() else None,
        "forward_outcome_accumulator_path": str(forward_outcome_accumulator_path) if forward_outcome_accumulator_path.exists() else None,
        "entry_scarcity_diagnostic_path": str(entry_scarcity_diagnostic_path) if entry_scarcity_diagnostic_path.exists() else None,
        "shadow_relaxation_validator_path": str(shadow_relaxation_validator_path) if shadow_relaxation_validator_path.exists() else None,
        "range_family_validator_path": str(range_family_validator_path) if range_family_validator_path.exists() else None,
        "range_watchlist_refiner_path": str(range_watchlist_refiner_path) if range_watchlist_refiner_path.exists() else None,
        "range_refined_observer_path": str(range_refined_observer_path) if range_refined_observer_path.exists() else None,
        "range_refined_scoreboard_path": str(range_refined_scoreboard_path) if range_refined_scoreboard_path.exists() else None,
        "range_refined_scarcity_path": str(range_refined_scarcity_path) if range_refined_scarcity_path.exists() else None,
        "range_refined_pending_watch_path": str(range_refined_pending_watch_path) if range_refined_pending_watch_path.exists() else None,
        "range_refined_pending_watch_notify_path": str(range_refined_pending_watch_notify_path) if range_refined_pending_watch_notify_path.exists() else None,
        "range_refined_filter_ablation_path": str(range_refined_filter_ablation_path) if range_refined_filter_ablation_path.exists() else None,
        "range_refined_shadow_forward_path": str(range_refined_shadow_forward_path) if range_refined_shadow_forward_path.exists() else None,
        "range_refined_shadow_forward_scoreboard_path": str(range_refined_shadow_forward_scoreboard_path) if range_refined_shadow_forward_scoreboard_path.exists() else None,
        "range_refined_shadow_promotion_gate_path": str(range_refined_shadow_promotion_gate_path) if range_refined_shadow_promotion_gate_path.exists() else None,
        "range_refined_promotion_gate_path": str(range_refined_promotion_gate_path) if range_refined_promotion_gate_path.exists() else None,
        "range_refined_alert_guard_path": str(range_refined_alert_guard_path) if range_refined_alert_guard_path.exists() else None,
        "scoreboard": {
            "classification": scoreboard_summary.get("classification"),
            "entry_intents": scoreboard_summary.get("entry_intents"),
            "resolved": scoreboard_summary.get("resolved"),
            "unresolved": scoreboard_summary.get("unresolved"),
            "winrate_pct": scoreboard_summary.get("winrate_pct"),
            "expectancy_r": scoreboard_summary.get("expectancy_r"),
            "net_r_total": scoreboard_summary.get("net_r_total"),
            "breakeven_winrate_pct": scoreboard_summary.get("breakeven_winrate_pct"),
        },
        "scheduler_cycles": scheduler.get("cycles") if isinstance(scheduler, dict) else None,
        "telegram_notify": {
            "exists": isinstance(telegram, dict),
            "decision": telegram.get("decision") if isinstance(telegram, dict) else None,
            "status": telegram.get("status") if isinstance(telegram, dict) else None,
            "telegram_response_ok": telegram.get("telegram_response_ok") if isinstance(telegram, dict) else None,
        },
        "canonical_regime": {
            "exists": isinstance(regime_observer, dict),
            "regime": latest_regime.get("regime"),
            "bar": latest_regime.get("ts"),
            "trend_strength_score": latest_regime.get("trend_strength_score"),
            "adx14": latest_regime.get("adx14"),
            "range_atr": latest_regime.get("range_atr"),
            "shock_watch": regime_observer.get("shock_watch") if isinstance(regime_observer, dict) else None,
            "decision": regime_observer.get("decision") if isinstance(regime_observer, dict) else None,
        },
        "oi_funding_context": {
            "exists": isinstance(oi_funding_context, dict),
            "decision": oi_funding_context.get("decision") if isinstance(oi_funding_context, dict) else None,
            "data_degraded": oi_funding_context.get("data_degraded") if isinstance(oi_funding_context, dict) else None,
            "context_bias": oi_context.get("context_bias"),
            "funding_state": oi_context.get("funding_state"),
            "funding": oi_context.get("funding"),
            "oi_state": oi_context.get("oi_state"),
            "oi_delta_12_pct": oi_context.get("oi_delta_12_pct"),
            "oi_zscore_100": oi_context.get("oi_zscore_100"),
            "staleness_hours": oi_freshness.get("staleness_hours"),
            "oi_guard_name": oi_guard_candidate.get("name"),
            "oi_guard_state": oi_guard_candidate.get("state"),
            "oi_guard_would_keep_long_signal": oi_guard_candidate.get("would_keep_long_signal"),
            "oi_guard_can_filter_now": oi_guard_candidate.get("can_filter_now"),
            "oi_guard_live_permission": oi_guard_candidate.get("live_permission"),
        },
        "oi_funding_scoreboard": {
            "exists": isinstance(oi_funding_scoreboard, dict),
            "classification": oi_score_summary.get("classification"),
            "context_observations": oi_score_summary.get("context_observations"),
            "unique_context_bars": oi_score_summary.get("unique_context_bars"),
            "entry_contexts": oi_score_summary.get("entry_contexts"),
            "resolved": oi_score_summary.get("resolved"),
            "expectancy_r": oi_score_summary.get("expectancy_r"),
            "data_degraded_observations": oi_score_summary.get("data_degraded_observations"),
            "oi_guard_entry_contexts": oi_score_summary.get("oi_guard_entry_contexts"),
            "oi_guard_resolved_contexts": oi_score_summary.get("oi_guard_resolved_contexts"),
            "latest_oi_guard_state": latest_oi_score_guard.get("state"),
            "latest_oi_guard_would_keep_long_signal": latest_oi_score_guard.get("would_keep_long_signal"),
            "latest_oi_guard_can_filter_now": latest_oi_score_guard.get("can_filter_now"),
            "oi_guard_state_counts": oi_score_counts.get("oi_guard_state"),
            "oi_guard_would_keep_counts": oi_score_counts.get("oi_guard_would_keep"),
        },
        "oi_funding_replay_audit": {
            "exists": isinstance(oi_funding_replay_audit, dict),
            "classification": oi_replay_audit_summary.get("classification"),
            "total_trades": oi_replay_audit_summary.get("total_trades"),
            "overall_expectancy_r": oi_replay_audit_summary.get("overall_expectancy_r"),
            "overall_winrate_pct": oi_replay_audit_summary.get("overall_winrate_pct"),
            "funding_available_trades": oi_replay_audit_summary.get("funding_available_trades"),
            "oi_available_trades": oi_replay_audit_summary.get("oi_available_trades"),
            "full_context_available_trades": oi_replay_audit_summary.get("full_context_available_trades"),
            "data_degraded_trades": oi_replay_audit_summary.get("data_degraded_trades"),
        },
        "oi_guard_promotion_gate": {
            "exists": isinstance(oi_guard_promotion_gate, dict),
            "decision": oi_guard_promotion_gate.get("decision") if isinstance(oi_guard_promotion_gate, dict) else None,
            "shadow_guard_allowed": (
                oi_guard_promotion_gate.get("promotion", {}).get("shadow_guard_allowed")
                if isinstance(oi_guard_promotion_gate, dict) and isinstance(oi_guard_promotion_gate.get("promotion"), dict)
                else None
            ),
            "active_filter_allowed": (
                oi_guard_promotion_gate.get("promotion", {}).get("active_filter_allowed")
                if isinstance(oi_guard_promotion_gate, dict) and isinstance(oi_guard_promotion_gate.get("promotion"), dict)
                else None
            ),
            "paper_execution_allowed": (
                oi_guard_promotion_gate.get("promotion", {}).get("paper_execution_allowed")
                if isinstance(oi_guard_promotion_gate, dict) and isinstance(oi_guard_promotion_gate.get("promotion"), dict)
                else None
            ),
            "live_execution_allowed": (
                oi_guard_promotion_gate.get("promotion", {}).get("live_execution_allowed")
                if isinstance(oi_guard_promotion_gate, dict) and isinstance(oi_guard_promotion_gate.get("promotion"), dict)
                else None
            ),
            "next_action": oi_guard_promotion_gate.get("next_action") if isinstance(oi_guard_promotion_gate, dict) else None,
        },
        "forward_outcome_accumulator": {
            "exists": isinstance(forward_outcome_accumulator, dict),
            "classification": forward_outcome_accumulator.get("classification") if isinstance(forward_outcome_accumulator, dict) else None,
            "next_action": forward_outcome_accumulator.get("next_action") if isinstance(forward_outcome_accumulator, dict) else None,
            "forward_entry_intents": accumulator_metrics.get("forward_entry_intents"),
            "forward_resolved_entries": accumulator_metrics.get("forward_resolved_entries"),
            "oi_guard_entry_contexts": accumulator_metrics.get("oi_guard_entry_contexts"),
            "oi_guard_resolved_contexts": accumulator_metrics.get("oi_guard_resolved_contexts"),
            "unique_context_bars": accumulator_metrics.get("unique_context_bars"),
            "unique_context_bars_required": accumulator_gates.get("unique_context_bars", {}).get("required") if isinstance(accumulator_gates.get("unique_context_bars"), dict) else None,
            "forward_entry_intents_required": accumulator_gates.get("forward_entry_intents", {}).get("required") if isinstance(accumulator_gates.get("forward_entry_intents"), dict) else None,
            "forward_resolved_entries_required": accumulator_gates.get("forward_resolved_entries", {}).get("required") if isinstance(accumulator_gates.get("forward_resolved_entries"), dict) else None,
            "oi_guard_resolved_contexts_required": accumulator_gates.get("oi_guard_resolved_contexts", {}).get("required") if isinstance(accumulator_gates.get("oi_guard_resolved_contexts"), dict) else None,
            "can_trade": forward_outcome_accumulator.get("can_trade") if isinstance(forward_outcome_accumulator, dict) else False,
        },
        "entry_scarcity_diagnostic": {
            "exists": isinstance(entry_scarcity_diagnostic, dict),
            "classification": entry_scarcity_diagnostic.get("classification") if isinstance(entry_scarcity_diagnostic, dict) else None,
            "next_action": entry_scarcity_diagnostic.get("next_action") if isinstance(entry_scarcity_diagnostic, dict) else None,
            "locked_signal_like_bars": (
                entry_scarcity_diagnostic.get("data", {}).get("locked_signal_like_bars")
                if isinstance(entry_scarcity_diagnostic, dict) and isinstance(entry_scarcity_diagnostic.get("data"), dict)
                else None
            ),
            "bars_analyzed": (
                entry_scarcity_diagnostic.get("data", {}).get("bars_analyzed")
                if isinstance(entry_scarcity_diagnostic, dict) and isinstance(entry_scarcity_diagnostic.get("data"), dict)
                else None
            ),
            "latest_analyzed_bar_ts": (
                entry_scarcity_diagnostic.get("data", {}).get("latest_analyzed_bar_ts")
                if isinstance(entry_scarcity_diagnostic, dict) and isinstance(entry_scarcity_diagnostic.get("data"), dict)
                else None
            ),
            "latest_blockers": (
                entry_scarcity_diagnostic.get("shadow_variants", [{}])[0].get("latest_blockers")
                if isinstance(entry_scarcity_diagnostic, dict)
                and isinstance(entry_scarcity_diagnostic.get("shadow_variants"), list)
                and entry_scarcity_diagnostic.get("shadow_variants")
                and isinstance(entry_scarcity_diagnostic.get("shadow_variants", [{}])[0], dict)
                else None
            ),
            "primary_bottleneck": (
                entry_scarcity_diagnostic.get("condition_stats", [{}])[0].get("condition")
                if isinstance(entry_scarcity_diagnostic, dict)
                and isinstance(entry_scarcity_diagnostic.get("condition_stats"), list)
                and entry_scarcity_diagnostic.get("condition_stats")
                and isinstance(entry_scarcity_diagnostic.get("condition_stats", [{}])[0], dict)
                else None
            ),
            "can_trade": entry_scarcity_diagnostic.get("can_trade") if isinstance(entry_scarcity_diagnostic, dict) else False,
        },
        "shadow_relaxation_validator": {
            "exists": isinstance(shadow_relaxation_validator, dict),
            "decision": shadow_relaxation_validator.get("decision") if isinstance(shadow_relaxation_validator, dict) else None,
            "next_action": shadow_relaxation_validator.get("next_action") if isinstance(shadow_relaxation_validator, dict) else None,
            "tested": shadow_relaxation_validator.get("tested") if isinstance(shadow_relaxation_validator, dict) else None,
            "shadow_candidate_count": shadow_relaxation_validator.get("shadow_candidate_count") if isinstance(shadow_relaxation_validator, dict) else None,
            "top_variant": (
                shadow_relaxation_validator.get("results", [{}])[0].get("variant_id")
                if isinstance(shadow_relaxation_validator, dict)
                and isinstance(shadow_relaxation_validator.get("results"), list)
                and shadow_relaxation_validator.get("results")
                and isinstance(shadow_relaxation_validator.get("results", [{}])[0], dict)
                else None
            ),
            "top_variant_verdict": (
                shadow_relaxation_validator.get("results", [{}])[0].get("verdict")
                if isinstance(shadow_relaxation_validator, dict)
                and isinstance(shadow_relaxation_validator.get("results"), list)
                and shadow_relaxation_validator.get("results")
                and isinstance(shadow_relaxation_validator.get("results", [{}])[0], dict)
                else None
            ),
            "can_trade": shadow_relaxation_validator.get("can_trade") if isinstance(shadow_relaxation_validator, dict) else False,
        },
        "range_family_validator": {
            "exists": isinstance(range_family_validator, dict),
            "decision": range_family_validator.get("decision") if isinstance(range_family_validator, dict) else None,
            "next_action": range_family_validator.get("next_action") if isinstance(range_family_validator, dict) else None,
            "tested": range_family_validator.get("tested") if isinstance(range_family_validator, dict) else None,
            "candidate_count": range_family_validator.get("candidate_count") if isinstance(range_family_validator, dict) else None,
            "watchlist_count": range_family_validator.get("watchlist_count") if isinstance(range_family_validator, dict) else None,
            "top_strategy": (
                range_family_validator.get("top_results", [{}])[0].get("strategy_id")
                if isinstance(range_family_validator, dict)
                and isinstance(range_family_validator.get("top_results"), list)
                and range_family_validator.get("top_results")
                and isinstance(range_family_validator.get("top_results", [{}])[0], dict)
                else None
            ),
            "top_verdict": (
                range_family_validator.get("top_results", [{}])[0].get("verdict")
                if isinstance(range_family_validator, dict)
                and isinstance(range_family_validator.get("top_results"), list)
                and range_family_validator.get("top_results")
                and isinstance(range_family_validator.get("top_results", [{}])[0], dict)
                else None
            ),
            "top_holdout_expectancy_r": (
                range_family_validator.get("top_results", [{}])[0].get("holdout", {}).get("summary", {}).get("expectancy_r")
                if isinstance(range_family_validator, dict)
                and isinstance(range_family_validator.get("top_results"), list)
                and range_family_validator.get("top_results")
                and isinstance(range_family_validator.get("top_results", [{}])[0], dict)
                else None
            ),
            "top_full_expectancy_r": (
                range_family_validator.get("top_results", [{}])[0].get("full", {}).get("summary", {}).get("expectancy_r")
                if isinstance(range_family_validator, dict)
                and isinstance(range_family_validator.get("top_results"), list)
                and range_family_validator.get("top_results")
                and isinstance(range_family_validator.get("top_results", [{}])[0], dict)
                else None
            ),
            "can_trade": range_family_validator.get("can_trade") if isinstance(range_family_validator, dict) else False,
        },
        "range_watchlist_refiner": {
            "exists": isinstance(range_watchlist_refiner, dict),
            "decision": range_watchlist_refiner.get("decision") if isinstance(range_watchlist_refiner, dict) else None,
            "next_action": range_watchlist_refiner.get("next_action") if isinstance(range_watchlist_refiner, dict) else None,
            "tested": range_watchlist_refiner.get("tested") if isinstance(range_watchlist_refiner, dict) else None,
            "candidate_count": range_watchlist_refiner.get("candidate_count") if isinstance(range_watchlist_refiner, dict) else None,
            "watchlist_count": range_watchlist_refiner.get("watchlist_count") if isinstance(range_watchlist_refiner, dict) else None,
            "selected_base_strategy": range_refiner_selected.get("base_strategy_id"),
            "selected_filter": range_refiner_selected.get("filter_mode"),
            "selected_verdict": range_refiner_selected.get("verdict"),
            "selected_full_expectancy_r": range_refiner_selected.get("full", {}).get("summary", {}).get("expectancy_r"),
            "selected_holdout_expectancy_r": range_refiner_selected.get("holdout", {}).get("summary", {}).get("expectancy_r"),
            "selected_cost10_expectancy_r": (
                range_refiner_selected_cost10.get("summary", {}).get("expectancy_r")
                if isinstance(range_refiner_selected_cost10, dict)
                else None
            ),
            "top_base_strategy": (
                range_watchlist_refiner.get("top_results", [{}])[0].get("base_strategy_id")
                if isinstance(range_watchlist_refiner, dict)
                and isinstance(range_watchlist_refiner.get("top_results"), list)
                and range_watchlist_refiner.get("top_results")
                and isinstance(range_watchlist_refiner.get("top_results", [{}])[0], dict)
                else None
            ),
            "top_filter": (
                range_watchlist_refiner.get("top_results", [{}])[0].get("filter_mode")
                if isinstance(range_watchlist_refiner, dict)
                and isinstance(range_watchlist_refiner.get("top_results"), list)
                and range_watchlist_refiner.get("top_results")
                and isinstance(range_watchlist_refiner.get("top_results", [{}])[0], dict)
                else None
            ),
            "top_verdict": (
                range_watchlist_refiner.get("top_results", [{}])[0].get("verdict")
                if isinstance(range_watchlist_refiner, dict)
                and isinstance(range_watchlist_refiner.get("top_results"), list)
                and range_watchlist_refiner.get("top_results")
                and isinstance(range_watchlist_refiner.get("top_results", [{}])[0], dict)
                else None
            ),
            "top_holdout_expectancy_r": (
                range_watchlist_refiner.get("top_results", [{}])[0].get("holdout", {}).get("summary", {}).get("expectancy_r")
                if isinstance(range_watchlist_refiner, dict)
                and isinstance(range_watchlist_refiner.get("top_results"), list)
                and range_watchlist_refiner.get("top_results")
                and isinstance(range_watchlist_refiner.get("top_results", [{}])[0], dict)
                else None
            ),
            "top_full_expectancy_r": (
                range_watchlist_refiner.get("top_results", [{}])[0].get("full", {}).get("summary", {}).get("expectancy_r")
                if isinstance(range_watchlist_refiner, dict)
                and isinstance(range_watchlist_refiner.get("top_results"), list)
                and range_watchlist_refiner.get("top_results")
                and isinstance(range_watchlist_refiner.get("top_results", [{}])[0], dict)
                else None
            ),
            "top_cost10_expectancy_r": (
                range_watchlist_refiner.get("top_results", [{}])[0].get("cost_stress", [{}])[2].get("summary", {}).get("expectancy_r")
                if isinstance(range_watchlist_refiner, dict)
                and isinstance(range_watchlist_refiner.get("top_results"), list)
                and range_watchlist_refiner.get("top_results")
                and isinstance(range_watchlist_refiner.get("top_results", [{}])[0], dict)
                and isinstance(range_watchlist_refiner.get("top_results", [{}])[0].get("cost_stress"), list)
                and len(range_watchlist_refiner.get("top_results", [{}])[0].get("cost_stress")) > 2
                else None
            ),
            "can_trade": range_watchlist_refiner.get("can_trade") if isinstance(range_watchlist_refiner, dict) else False,
        },
        "range_refined_forward_observer": {
            "exists": isinstance(range_refined_observer, dict),
            "decision": range_refined_observer.get("decision") if isinstance(range_refined_observer, dict) else None,
            "next_action": range_refined_observer.get("next_action") if isinstance(range_refined_observer, dict) else None,
            "status": range_refined_latest.get("status"),
            "strategy_id": range_refined_latest.get("strategy_id"),
            "filter_mode": range_refined_latest.get("filter_mode"),
            "latest_closed_bar_ts": range_refined_latest.get("latest_closed_bar_ts"),
            "latest_closed_close": range_refined_latest.get("latest_closed_close"),
            "raw_signals_on_latest_bar": range_refined_latest.get("raw_signals_on_latest_bar"),
            "refined_signals_on_latest_bar": range_refined_latest.get("refined_signals_on_latest_bar"),
            "data_degraded": range_refined_latest.get("data_degraded"),
            "missing_filter_inputs": range_refined_latest.get("missing_filter_inputs"),
            "journal_path": range_refined_latest.get("journal_path"),
            "can_trade": range_refined_observer.get("can_trade") if isinstance(range_refined_observer, dict) else False,
        },
        "range_refined_observer_scoreboard": {
            "exists": isinstance(range_refined_scoreboard, dict),
            "decision": range_refined_scoreboard.get("decision") if isinstance(range_refined_scoreboard, dict) else None,
            "next_action": range_refined_scoreboard.get("next_action") if isinstance(range_refined_scoreboard, dict) else None,
            "classification": range_refined_score_summary.get("classification"),
            "observer_signal_events": range_refined_score_summary.get("observer_signal_events"),
            "filtered_out_events": range_refined_score_summary.get("filtered_out_events"),
            "no_signal_events": range_refined_score_summary.get("no_signal_events"),
            "resolved": range_refined_score_summary.get("resolved"),
            "unresolved": range_refined_score_summary.get("unresolved"),
            "winrate_pct": range_refined_score_summary.get("winrate_pct"),
            "expectancy_r": range_refined_score_summary.get("expectancy_r"),
            "breakeven_winrate_pct": range_refined_score_summary.get("breakeven_winrate_pct"),
            "can_trade": range_refined_scoreboard.get("can_trade") if isinstance(range_refined_scoreboard, dict) else False,
        },
        "range_refined_signal_scarcity_diagnostic": {
            "exists": isinstance(range_refined_scarcity, dict),
            "classification": range_refined_scarcity.get("classification") if isinstance(range_refined_scarcity, dict) else None,
            "next_action": range_refined_scarcity.get("next_action") if isinstance(range_refined_scarcity, dict) else None,
            "base_setup_bars": (
                range_refined_scarcity.get("summary", {}).get("base_setup_bars")
                if isinstance(range_refined_scarcity, dict) and isinstance(range_refined_scarcity.get("summary"), dict)
                else None
            ),
            "refined_setup_bars": (
                range_refined_scarcity.get("summary", {}).get("refined_setup_bars")
                if isinstance(range_refined_scarcity, dict) and isinstance(range_refined_scarcity.get("summary"), dict)
                else None
            ),
            "latest_status": (
                range_refined_scarcity.get("latest_bar", {}).get("status")
                if isinstance(range_refined_scarcity, dict) and isinstance(range_refined_scarcity.get("latest_bar"), dict)
                else None
            ),
            "top_base_blocker": (
                range_refined_scarcity.get("base_blockers", [{}])[0].get("condition")
                if isinstance(range_refined_scarcity, dict)
                and isinstance(range_refined_scarcity.get("base_blockers"), list)
                and range_refined_scarcity.get("base_blockers")
                and isinstance(range_refined_scarcity.get("base_blockers", [{}])[0], dict)
                else None
            ),
            "top_filter_blocker": (
                range_refined_scarcity.get("filter_blockers", [{}])[0].get("condition")
                if isinstance(range_refined_scarcity, dict)
                and isinstance(range_refined_scarcity.get("filter_blockers"), list)
                and range_refined_scarcity.get("filter_blockers")
                and isinstance(range_refined_scarcity.get("filter_blockers", [{}])[0], dict)
                else None
            ),
            "can_trade": range_refined_scarcity.get("can_trade") if isinstance(range_refined_scarcity, dict) else False,
        },
        "range_refined_pending_watch": {
            "exists": isinstance(range_refined_pending_watch, dict),
            "classification": range_refined_pending_watch.get("classification") if isinstance(range_refined_pending_watch, dict) else None,
            "next_action": range_refined_pending_watch.get("next_action") if isinstance(range_refined_pending_watch, dict) else None,
            "decision": range_refined_pending_watch.get("decision") if isinstance(range_refined_pending_watch, dict) else None,
            "context_ok": (
                range_refined_pending_watch.get("latest", {}).get("context_ok")
                if isinstance(range_refined_pending_watch, dict) and isinstance(range_refined_pending_watch.get("latest"), dict)
                else None
            ),
            "trigger_ok": (
                range_refined_pending_watch.get("latest", {}).get("trigger_ok")
                if isinstance(range_refined_pending_watch, dict) and isinstance(range_refined_pending_watch.get("latest"), dict)
                else None
            ),
            "refined_ready": (
                range_refined_pending_watch.get("latest", {}).get("refined_ready")
                if isinstance(range_refined_pending_watch, dict) and isinstance(range_refined_pending_watch.get("latest"), dict)
                else None
            ),
            "distance_to_trigger_atr": (
                range_refined_pending_watch.get("latest", {}).get("trigger", {}).get("distance_to_trigger_atr")
                if isinstance(range_refined_pending_watch, dict)
                and isinstance(range_refined_pending_watch.get("latest"), dict)
                and isinstance(range_refined_pending_watch.get("latest", {}).get("trigger"), dict)
                else None
            ),
            "distance_to_trigger_pct": (
                range_refined_pending_watch.get("latest", {}).get("trigger", {}).get("distance_to_trigger_pct")
                if isinstance(range_refined_pending_watch, dict)
                and isinstance(range_refined_pending_watch.get("latest"), dict)
                and isinstance(range_refined_pending_watch.get("latest", {}).get("trigger"), dict)
                else None
            ),
            "trigger_progress_pct": (
                range_refined_pending_watch.get("latest", {}).get("trigger", {}).get("trigger_progress_pct")
                if isinstance(range_refined_pending_watch, dict)
                and isinstance(range_refined_pending_watch.get("latest"), dict)
                and isinstance(range_refined_pending_watch.get("latest", {}).get("trigger"), dict)
                else None
            ),
            "can_trade": range_refined_pending_watch.get("can_trade") if isinstance(range_refined_pending_watch, dict) else False,
        },
        "range_refined_pending_watch_telegram_notify": {
            "exists": isinstance(range_refined_pending_watch_notify, dict),
            "decision": range_refined_pending_watch_notify.get("decision") if isinstance(range_refined_pending_watch_notify, dict) else None,
            "classification": range_refined_pending_watch_notify.get("classification") if isinstance(range_refined_pending_watch_notify, dict) else None,
            "notification_key": range_refined_pending_watch_notify.get("notification_key") if isinstance(range_refined_pending_watch_notify, dict) else None,
            "telegram_response_ok": (
                range_refined_pending_watch_notify.get("telegram_response_ok")
                if isinstance(range_refined_pending_watch_notify, dict)
                else None
            ),
            "card_json_path": range_refined_pending_watch_notify.get("card_json_path") if isinstance(range_refined_pending_watch_notify, dict) else None,
            "card_md_path": range_refined_pending_watch_notify.get("card_md_path") if isinstance(range_refined_pending_watch_notify, dict) else None,
            "can_trade": range_refined_pending_watch_notify.get("can_trade") if isinstance(range_refined_pending_watch_notify, dict) else False,
        },
        "edge_forward_range_observer": {
            "exists": isinstance(edge_forward_observer, dict),
            "decision": edge_forward_observer.get("decision") if isinstance(edge_forward_observer, dict) else None,
            "next_action": edge_forward_observer.get("next_action") if isinstance(edge_forward_observer, dict) else None,
            "status": edge_forward_latest.get("status"),
            "strategy_id": edge_forward_latest.get("strategy_id"),
            "filter_mode": edge_forward_latest.get("filter_mode"),
            "latest_closed_bar_ts": edge_forward_latest.get("latest_closed_bar_ts"),
            "latest_closed_close": edge_forward_latest.get("latest_closed_close"),
            "raw_signals_on_latest_bar": edge_forward_latest.get("raw_signals_on_latest_bar"),
            "refined_signals_on_latest_bar": edge_forward_latest.get("refined_signals_on_latest_bar"),
            "data_degraded": edge_forward_latest.get("data_degraded"),
            "journal_path": edge_forward_latest.get("journal_path"),
            "can_trade": edge_forward_observer.get("can_trade") if isinstance(edge_forward_observer, dict) else False,
        },
        "edge_forward_range_scoreboard": {
            "exists": isinstance(edge_forward_scoreboard, dict),
            "decision": edge_forward_scoreboard.get("decision") if isinstance(edge_forward_scoreboard, dict) else None,
            "next_action": edge_forward_scoreboard.get("next_action") if isinstance(edge_forward_scoreboard, dict) else None,
            "classification": edge_forward_score_summary.get("classification"),
            "observer_signal_events": edge_forward_score_summary.get("observer_signal_events"),
            "filtered_out_events": edge_forward_score_summary.get("filtered_out_events"),
            "no_signal_events": edge_forward_score_summary.get("no_signal_events"),
            "resolved": edge_forward_score_summary.get("resolved"),
            "unresolved": edge_forward_score_summary.get("unresolved"),
            "winrate_pct": edge_forward_score_summary.get("winrate_pct"),
            "expectancy_r": edge_forward_score_summary.get("expectancy_r"),
            "breakeven_winrate_pct": edge_forward_score_summary.get("breakeven_winrate_pct"),
            "can_trade": edge_forward_scoreboard.get("can_trade") if isinstance(edge_forward_scoreboard, dict) else False,
        },
        "edge_liquidation_context_shadow": {
            "exists": isinstance(edge_liquidation_context, dict),
            "decision": edge_liquidation_context.get("decision") if isinstance(edge_liquidation_context, dict) else None,
            "context": edge_liquidation_latest.get("context"),
            "continuous_score": edge_liquidation_latest.get("continuous_score"),
            "score_bin": edge_liquidation_latest.get("score_bin"),
            "score_lock_status": edge_liquidation_latest.get("score_lock_status"),
            "bar_ts": edge_liquidation_latest.get("bar_ts"),
            "displacement_atr": edge_liquidation_latest.get("displacement_atr"),
            "oi_delta_pct": edge_liquidation_latest.get("oi_delta_pct"),
            "volume_z": edge_liquidation_latest.get("volume_z"),
            "data_degraded": edge_liquidation_latest.get("data_degraded"),
            "edge_effect": edge_liquidation_latest.get("edge_effect"),
            "filter_applied": edge_liquidation_latest.get("filter_applied", False),
            "veto_applied": edge_liquidation_latest.get("veto_applied", False),
            "can_trade": edge_liquidation_context.get("can_trade") if isinstance(edge_liquidation_context, dict) else False,
        },
        "edge_liquidation_context_scoreboard": {
            "exists": isinstance(edge_liquidation_context_scoreboard, dict),
            "classification": (
                edge_liquidation_context_scoreboard.get("classification")
                if isinstance(edge_liquidation_context_scoreboard, dict)
                else None
            ),
            "edge_signal_events": (
                edge_liquidation_context_scoreboard.get("edge_signal_events")
                if isinstance(edge_liquidation_context_scoreboard, dict)
                else None
            ),
            "context_labelled_signals": (
                edge_liquidation_context_scoreboard.get("context_labelled_signals")
                if isinstance(edge_liquidation_context_scoreboard, dict)
                else None
            ),
            "by_context": (
                edge_liquidation_context_scoreboard.get("by_context")
                if isinstance(edge_liquidation_context_scoreboard, dict)
                else {}
            ),
            "by_score_bin": (
                edge_liquidation_context_scoreboard.get("by_score_bin")
                if isinstance(edge_liquidation_context_scoreboard, dict)
                else {}
            ),
            "recommended_filter_change": (
                edge_liquidation_context_scoreboard.get("recommended_filter_change", False)
                if isinstance(edge_liquidation_context_scoreboard, dict)
                else False
            ),
            "can_trade": (
                edge_liquidation_context_scoreboard.get("can_trade")
                if isinstance(edge_liquidation_context_scoreboard, dict)
                else False
            ),
        },
        "edge_liquidation_context_historical_replay": {
            "exists": isinstance(edge_liquidation_context_replay, dict),
            "decision": (
                edge_liquidation_context_replay.get("decision")
                if isinstance(edge_liquidation_context_replay, dict)
                else None
            ),
            "exact_trade_count_match": (
                edge_liquidation_context_replay.get("reproduction", {}).get("exact_trade_count_match")
                if isinstance(edge_liquidation_context_replay, dict)
                and isinstance(edge_liquidation_context_replay.get("reproduction"), dict)
                else None
            ),
            "train_trades": (
                edge_liquidation_context_replay.get("reproduction", {}).get("actual_train_trades")
                if isinstance(edge_liquidation_context_replay, dict)
                and isinstance(edge_liquidation_context_replay.get("reproduction"), dict)
                else None
            ),
            "oos_trades": (
                edge_liquidation_context_replay.get("reproduction", {}).get("actual_oos_trades")
                if isinstance(edge_liquidation_context_replay, dict)
                and isinstance(edge_liquidation_context_replay.get("reproduction"), dict)
                else None
            ),
            "informative_oos_contexts": (
                edge_liquidation_context_replay.get("evidence_gate", {}).get("informative_oos_contexts")
                if isinstance(edge_liquidation_context_replay, dict)
                and isinstance(edge_liquidation_context_replay.get("evidence_gate"), dict)
                else []
            ),
            "recommended_filter_change": (
                edge_liquidation_context_replay.get("evidence_gate", {}).get("recommended_filter_change", False)
                if isinstance(edge_liquidation_context_replay, dict)
                and isinstance(edge_liquidation_context_replay.get("evidence_gate"), dict)
                else False
            ),
            "can_trade": (
                edge_liquidation_context_replay.get("can_trade")
                if isinstance(edge_liquidation_context_replay, dict)
                else False
            ),
        },
        "edge_liquidation_score_evidence_gate": {
            "exists": isinstance(edge_liquidation_score_evidence_gate, dict),
            "decision": (
                edge_liquidation_score_evidence_gate.get("decision")
                if isinstance(edge_liquidation_score_evidence_gate, dict)
                else None
            ),
            "resolved_total": (
                edge_liquidation_score_evidence_gate.get("evidence", {}).get("resolved_total")
                if isinstance(edge_liquidation_score_evidence_gate, dict)
                and isinstance(edge_liquidation_score_evidence_gate.get("evidence"), dict)
                else None
            ),
            "resolved_required": (
                edge_liquidation_score_evidence_gate.get("requirements", {}).get("min_total_resolved")
                if isinstance(edge_liquidation_score_evidence_gate, dict)
                and isinstance(edge_liquidation_score_evidence_gate.get("requirements"), dict)
                else None
            ),
            "inactive_resolved": (
                edge_liquidation_score_evidence_gate.get("evidence", {}).get("inactive_resolved")
                if isinstance(edge_liquidation_score_evidence_gate, dict)
                and isinstance(edge_liquidation_score_evidence_gate.get("evidence"), dict)
                else None
            ),
            "qualifying_bins": (
                edge_liquidation_score_evidence_gate.get("evidence", {}).get("qualifying_bins")
                if isinstance(edge_liquidation_score_evidence_gate, dict)
                and isinstance(edge_liquidation_score_evidence_gate.get("evidence"), dict)
                else []
            ),
            "research_review_allowed": (
                edge_liquidation_score_evidence_gate.get("evidence", {}).get("research_review_allowed", False)
                if isinstance(edge_liquidation_score_evidence_gate, dict)
                and isinstance(edge_liquidation_score_evidence_gate.get("evidence"), dict)
                else False
            ),
            "filter_change_allowed": False,
            "can_trade": False,
        },
        "edge_forward_pending_watch": {
            "exists": isinstance(edge_forward_pending_watch, dict),
            "classification": edge_forward_pending_watch.get("classification") if isinstance(edge_forward_pending_watch, dict) else None,
            "next_action": edge_forward_pending_watch.get("next_action") if isinstance(edge_forward_pending_watch, dict) else None,
            "decision": edge_forward_pending_watch.get("decision") if isinstance(edge_forward_pending_watch, dict) else None,
            "context_ok": (
                edge_forward_pending_watch.get("latest", {}).get("context_ok")
                if isinstance(edge_forward_pending_watch, dict) and isinstance(edge_forward_pending_watch.get("latest"), dict)
                else None
            ),
            "trigger_ok": (
                edge_forward_pending_watch.get("latest", {}).get("trigger_ok")
                if isinstance(edge_forward_pending_watch, dict) and isinstance(edge_forward_pending_watch.get("latest"), dict)
                else None
            ),
            "refined_ready": (
                edge_forward_pending_watch.get("latest", {}).get("refined_ready")
                if isinstance(edge_forward_pending_watch, dict) and isinstance(edge_forward_pending_watch.get("latest"), dict)
                else None
            ),
            "distance_to_trigger_atr": (
                edge_forward_pending_watch.get("latest", {}).get("trigger", {}).get("distance_to_trigger_atr")
                if isinstance(edge_forward_pending_watch, dict)
                and isinstance(edge_forward_pending_watch.get("latest"), dict)
                and isinstance(edge_forward_pending_watch.get("latest", {}).get("trigger"), dict)
                else None
            ),
            "distance_to_trigger_pct": (
                edge_forward_pending_watch.get("latest", {}).get("trigger", {}).get("distance_to_trigger_pct")
                if isinstance(edge_forward_pending_watch, dict)
                and isinstance(edge_forward_pending_watch.get("latest"), dict)
                and isinstance(edge_forward_pending_watch.get("latest", {}).get("trigger"), dict)
                else None
            ),
            "trigger_progress_pct": (
                edge_forward_pending_watch.get("latest", {}).get("trigger", {}).get("trigger_progress_pct")
                if isinstance(edge_forward_pending_watch, dict)
                and isinstance(edge_forward_pending_watch.get("latest"), dict)
                and isinstance(edge_forward_pending_watch.get("latest", {}).get("trigger"), dict)
                else None
            ),
            "can_trade": edge_forward_pending_watch.get("can_trade") if isinstance(edge_forward_pending_watch, dict) else False,
        },
        "edge_forward_pending_watch_telegram_notify": {
            "exists": isinstance(edge_forward_pending_watch_notify, dict),
            "decision": edge_forward_pending_watch_notify.get("decision") if isinstance(edge_forward_pending_watch_notify, dict) else None,
            "classification": edge_forward_pending_watch_notify.get("classification") if isinstance(edge_forward_pending_watch_notify, dict) else None,
            "notification_key": edge_forward_pending_watch_notify.get("notification_key") if isinstance(edge_forward_pending_watch_notify, dict) else None,
            "telegram_response_ok": (
                edge_forward_pending_watch_notify.get("telegram_response_ok")
                if isinstance(edge_forward_pending_watch_notify, dict)
                else None
            ),
            "card_json_path": edge_forward_pending_watch_notify.get("card_json_path") if isinstance(edge_forward_pending_watch_notify, dict) else None,
            "card_md_path": edge_forward_pending_watch_notify.get("card_md_path") if isinstance(edge_forward_pending_watch_notify, dict) else None,
            "can_trade": edge_forward_pending_watch_notify.get("can_trade") if isinstance(edge_forward_pending_watch_notify, dict) else False,
        },
        "edge_forward_promotion_gate": {
            "exists": isinstance(edge_forward_promotion_gate, dict),
            "decision": edge_forward_promotion_gate.get("decision") if isinstance(edge_forward_promotion_gate, dict) else None,
            "next_action": edge_forward_promotion_gate.get("next_action") if isinstance(edge_forward_promotion_gate, dict) else None,
            "observer_allowed": (
                edge_forward_promotion_gate.get("promotion", {}).get("observer_allowed")
                if isinstance(edge_forward_promotion_gate, dict) and isinstance(edge_forward_promotion_gate.get("promotion"), dict)
                else None
            ),
            "paper_design_review_allowed": (
                edge_forward_promotion_gate.get("promotion", {}).get("paper_design_review_allowed")
                if isinstance(edge_forward_promotion_gate, dict) and isinstance(edge_forward_promotion_gate.get("promotion"), dict)
                else None
            ),
            "paper_execution_allowed": (
                edge_forward_promotion_gate.get("promotion", {}).get("paper_execution_allowed")
                if isinstance(edge_forward_promotion_gate, dict) and isinstance(edge_forward_promotion_gate.get("promotion"), dict)
                else None
            ),
            "live_execution_allowed": (
                edge_forward_promotion_gate.get("promotion", {}).get("live_execution_allowed")
                if isinstance(edge_forward_promotion_gate, dict) and isinstance(edge_forward_promotion_gate.get("promotion"), dict)
                else None
            ),
            "observer_signal_events": (
                edge_forward_promotion_gate.get("scoreboard", {}).get("observer_signal_events")
                if isinstance(edge_forward_promotion_gate, dict) and isinstance(edge_forward_promotion_gate.get("scoreboard"), dict)
                else None
            ),
            "resolved": (
                edge_forward_promotion_gate.get("scoreboard", {}).get("resolved")
                if isinstance(edge_forward_promotion_gate, dict) and isinstance(edge_forward_promotion_gate.get("scoreboard"), dict)
                else None
            ),
            "can_trade": edge_forward_promotion_gate.get("can_trade") if isinstance(edge_forward_promotion_gate, dict) else False,
        },
        "range_refined_filter_shadow_ablation": {
            "exists": isinstance(range_refined_filter_ablation, dict),
            "decision": range_refined_filter_ablation.get("decision") if isinstance(range_refined_filter_ablation, dict) else None,
            "next_action": range_refined_filter_ablation.get("next_action") if isinstance(range_refined_filter_ablation, dict) else None,
            "tested": range_refined_filter_ablation.get("tested") if isinstance(range_refined_filter_ablation, dict) else None,
            "shadow_shape_pass_count": range_refined_filter_ablation.get("shadow_shape_pass_count") if isinstance(range_refined_filter_ablation, dict) else None,
            "best_variant": (
                range_refined_filter_ablation.get("best_variant", {}).get("variant_id")
                if isinstance(range_refined_filter_ablation, dict) and isinstance(range_refined_filter_ablation.get("best_variant"), dict)
                else None
            ),
            "best_verdict": (
                range_refined_filter_ablation.get("best_variant", {}).get("verdict")
                if isinstance(range_refined_filter_ablation, dict) and isinstance(range_refined_filter_ablation.get("best_variant"), dict)
                else None
            ),
            "best_full_expectancy_r": (
                range_refined_filter_ablation.get("best_variant", {}).get("full", {}).get("summary", {}).get("expectancy_r")
                if isinstance(range_refined_filter_ablation, dict)
                and isinstance(range_refined_filter_ablation.get("best_variant"), dict)
                and isinstance(range_refined_filter_ablation.get("best_variant", {}).get("full"), dict)
                and isinstance(range_refined_filter_ablation.get("best_variant", {}).get("full", {}).get("summary"), dict)
                else None
            ),
            "can_trade": range_refined_filter_ablation.get("can_trade") if isinstance(range_refined_filter_ablation, dict) else False,
        },
        "range_refined_filter_shadow_forward_observer": {
            "exists": isinstance(range_refined_shadow_forward, dict),
            "decision": range_refined_shadow_forward.get("decision") if isinstance(range_refined_shadow_forward, dict) else None,
            "next_action": range_refined_shadow_forward.get("next_action") if isinstance(range_refined_shadow_forward, dict) else None,
            "raw_base_signals_on_latest_bar": (
                range_refined_shadow_forward.get("latest_result", {}).get("raw_base_signals_on_latest_bar")
                if isinstance(range_refined_shadow_forward, dict)
                and isinstance(range_refined_shadow_forward.get("latest_result"), dict)
                else None
            ),
            "variant_signals_on_latest_bar": (
                range_refined_shadow_forward.get("latest_result", {}).get("variant_signals_on_latest_bar")
                if isinstance(range_refined_shadow_forward, dict)
                and isinstance(range_refined_shadow_forward.get("latest_result"), dict)
                else None
            ),
            "signalling_variants": (
                range_refined_shadow_forward.get("latest_result", {}).get("signalling_variants")
                if isinstance(range_refined_shadow_forward, dict)
                and isinstance(range_refined_shadow_forward.get("latest_result"), dict)
                else []
            ),
            "journal_path": range_refined_shadow_forward.get("journal_path") if isinstance(range_refined_shadow_forward, dict) else None,
            "can_trade": range_refined_shadow_forward.get("can_trade") if isinstance(range_refined_shadow_forward, dict) else False,
        },
        "range_refined_filter_shadow_forward_scoreboard": {
            "exists": isinstance(range_refined_shadow_forward_scoreboard, dict),
            "decision": range_refined_shadow_forward_scoreboard.get("decision") if isinstance(range_refined_shadow_forward_scoreboard, dict) else None,
            "next_action": range_refined_shadow_forward_scoreboard.get("next_action") if isinstance(range_refined_shadow_forward_scoreboard, dict) else None,
            "classification": (
                range_refined_shadow_forward_scoreboard.get("summary", {}).get("classification")
                if isinstance(range_refined_shadow_forward_scoreboard, dict)
                and isinstance(range_refined_shadow_forward_scoreboard.get("summary"), dict)
                else None
            ),
            "shadow_signal_events": (
                range_refined_shadow_forward_scoreboard.get("summary", {}).get("shadow_signal_events")
                if isinstance(range_refined_shadow_forward_scoreboard, dict)
                and isinstance(range_refined_shadow_forward_scoreboard.get("summary"), dict)
                else None
            ),
            "resolved": (
                range_refined_shadow_forward_scoreboard.get("summary", {}).get("resolved")
                if isinstance(range_refined_shadow_forward_scoreboard, dict)
                and isinstance(range_refined_shadow_forward_scoreboard.get("summary"), dict)
                else None
            ),
            "expectancy_r": (
                range_refined_shadow_forward_scoreboard.get("summary", {}).get("expectancy_r")
                if isinstance(range_refined_shadow_forward_scoreboard, dict)
                and isinstance(range_refined_shadow_forward_scoreboard.get("summary"), dict)
                else None
            ),
            "can_trade": range_refined_shadow_forward_scoreboard.get("can_trade") if isinstance(range_refined_shadow_forward_scoreboard, dict) else False,
        },
        "range_refined_filter_shadow_promotion_gate": {
            "exists": isinstance(range_refined_shadow_promotion_gate, dict),
            "decision": range_refined_shadow_promotion_gate.get("decision") if isinstance(range_refined_shadow_promotion_gate, dict) else None,
            "next_action": range_refined_shadow_promotion_gate.get("next_action") if isinstance(range_refined_shadow_promotion_gate, dict) else None,
            "shadow_observer_allowed": (
                range_refined_shadow_promotion_gate.get("promotion", {}).get("shadow_observer_allowed")
                if isinstance(range_refined_shadow_promotion_gate, dict)
                and isinstance(range_refined_shadow_promotion_gate.get("promotion"), dict)
                else None
            ),
            "paper_design_review_allowed": (
                range_refined_shadow_promotion_gate.get("promotion", {}).get("paper_design_review_allowed")
                if isinstance(range_refined_shadow_promotion_gate, dict)
                and isinstance(range_refined_shadow_promotion_gate.get("promotion"), dict)
                else None
            ),
            "paper_execution_allowed": (
                range_refined_shadow_promotion_gate.get("promotion", {}).get("paper_execution_allowed")
                if isinstance(range_refined_shadow_promotion_gate, dict)
                and isinstance(range_refined_shadow_promotion_gate.get("promotion"), dict)
                else None
            ),
            "live_execution_allowed": (
                range_refined_shadow_promotion_gate.get("promotion", {}).get("live_execution_allowed")
                if isinstance(range_refined_shadow_promotion_gate, dict)
                and isinstance(range_refined_shadow_promotion_gate.get("promotion"), dict)
                else None
            ),
            "historical_ready_variants": (
                range_refined_shadow_promotion_gate.get("historical_ready_variants")
                if isinstance(range_refined_shadow_promotion_gate, dict)
                else []
            ),
            "paper_design_ready_variants": (
                range_refined_shadow_promotion_gate.get("paper_design_ready_variants")
                if isinstance(range_refined_shadow_promotion_gate, dict)
                else []
            ),
            "can_trade": range_refined_shadow_promotion_gate.get("can_trade") if isinstance(range_refined_shadow_promotion_gate, dict) else False,
        },
        "range_refined_promotion_gate": {
            "exists": isinstance(range_refined_promotion_gate, dict),
            "decision": range_refined_promotion_gate.get("decision") if isinstance(range_refined_promotion_gate, dict) else None,
            "next_action": range_refined_promotion_gate.get("next_action") if isinstance(range_refined_promotion_gate, dict) else None,
            "observer_allowed": (
                range_refined_promotion_gate.get("promotion", {}).get("observer_allowed")
                if isinstance(range_refined_promotion_gate, dict) and isinstance(range_refined_promotion_gate.get("promotion"), dict)
                else None
            ),
            "paper_design_review_allowed": (
                range_refined_promotion_gate.get("promotion", {}).get("paper_design_review_allowed")
                if isinstance(range_refined_promotion_gate, dict) and isinstance(range_refined_promotion_gate.get("promotion"), dict)
                else None
            ),
            "paper_execution_allowed": (
                range_refined_promotion_gate.get("promotion", {}).get("paper_execution_allowed")
                if isinstance(range_refined_promotion_gate, dict) and isinstance(range_refined_promotion_gate.get("promotion"), dict)
                else None
            ),
            "live_execution_allowed": (
                range_refined_promotion_gate.get("promotion", {}).get("live_execution_allowed")
                if isinstance(range_refined_promotion_gate, dict) and isinstance(range_refined_promotion_gate.get("promotion"), dict)
                else None
            ),
            "observer_signal_events": (
                range_refined_promotion_gate.get("scoreboard", {}).get("observer_signal_events")
                if isinstance(range_refined_promotion_gate, dict) and isinstance(range_refined_promotion_gate.get("scoreboard"), dict)
                else None
            ),
            "resolved": (
                range_refined_promotion_gate.get("scoreboard", {}).get("resolved")
                if isinstance(range_refined_promotion_gate, dict) and isinstance(range_refined_promotion_gate.get("scoreboard"), dict)
                else None
            ),
            "can_trade": range_refined_promotion_gate.get("can_trade") if isinstance(range_refined_promotion_gate, dict) else False,
        },
        "range_refined_signal_alert_guard": {
            "exists": isinstance(range_refined_alert_guard, dict),
            "decision": range_refined_alert_guard.get("decision") if isinstance(range_refined_alert_guard, dict) else None,
            "status": range_refined_alert_guard.get("status") if isinstance(range_refined_alert_guard, dict) else None,
            "notification_key": range_refined_alert_guard.get("notification_key") if isinstance(range_refined_alert_guard, dict) else None,
            "telegram_response_ok": range_refined_alert_guard.get("telegram_response_ok") if isinstance(range_refined_alert_guard, dict) else None,
            "card_json_path": range_refined_alert_guard.get("card_json_path") if isinstance(range_refined_alert_guard, dict) else None,
            "card_md_path": range_refined_alert_guard.get("card_md_path") if isinstance(range_refined_alert_guard, dict) else None,
            "can_trade": range_refined_alert_guard.get("can_trade") if isinstance(range_refined_alert_guard, dict) else False,
        },
        "range_refined_signal_alert_drill": {
            "exists": isinstance(range_refined_alert_drill, dict),
            "decision": range_refined_alert_drill.get("decision") if isinstance(range_refined_alert_drill, dict) else None,
            "first_decision": (
                range_refined_alert_drill.get("first_guard", {}).get("guard_report", {}).get("decision")
                if isinstance(range_refined_alert_drill, dict)
                and isinstance(range_refined_alert_drill.get("first_guard"), dict)
                and isinstance(range_refined_alert_drill.get("first_guard", {}).get("guard_report"), dict)
                else None
            ),
            "duplicate_decision": (
                range_refined_alert_drill.get("duplicate_guard", {}).get("guard_report", {}).get("decision")
                if isinstance(range_refined_alert_drill, dict)
                and isinstance(range_refined_alert_drill.get("duplicate_guard"), dict)
                and isinstance(range_refined_alert_drill.get("duplicate_guard", {}).get("guard_report"), dict)
                else None
            ),
            "fixture_path": range_refined_alert_drill.get("fixture_path") if isinstance(range_refined_alert_drill, dict) else None,
            "card_md_path": range_refined_alert_drill.get("card_md_path") if isinstance(range_refined_alert_drill, dict) else None,
            "can_trade": range_refined_alert_drill.get("can_trade") if isinstance(range_refined_alert_drill, dict) else False,
        },
        "last_status": last_status,
        "last_run_at": state.get("last_run_at") if isinstance(state, dict) else None,
        "last_closed_bar_ts": (
            card.get("latest_closed_bar_ts")
            if isinstance(card, dict)
            else state.get("last_closed_bar_ts")
            if isinstance(state, dict)
            else latest_result.get("latest_closed_bar_ts")
        ),
        "latest_closed_close": card.get("latest_closed_close") if isinstance(card, dict) else latest_result.get("latest_closed_close"),
        "signals_on_latest_bar": card.get("signals_on_latest_bar") if isinstance(card, dict) else latest_result.get("signals_on_latest_bar"),
        "strategy_id": (
            card.get("strategy_id")
            if isinstance(card, dict)
            else state.get("strategy_id")
            if isinstance(state, dict)
            else latest_result.get("strategy_id")
        ),
        "symbol": card.get("symbol") if isinstance(card, dict) else latest_result.get("symbol"),
        "interval": card.get("interval") if isinstance(card, dict) else latest_result.get("interval"),
        "journal_events": journal_events,
        "journal_tail_event_counts": event_counts,
        "last_event_type": latest_event.get("event_type") if isinstance(latest_event, dict) else None,
        "can_trade": False,
        "decision": "forward_paper_only_no_orders",
    }


def latest_autostart_summary() -> dict[str, Any]:
    startup_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup_cmd = startup_dir / "TradingOS_Autostart.cmd"
    runtime_status_path = ROOT / "logs" / "runtime_autostart_status.json"
    optimizer_status_path = ROOT / "logs" / "runtime_optimizer_status.json"
    panel_status_path = ROOT / "logs" / "control_panel_autostart_status.json"
    loop_status_path = ROOT / "logs" / "forward_paper_feed" / "forward_scheduler_loop_status.json"
    loop_lock_path = ROOT / "logs" / "forward_paper_feed" / "forward_scheduler_loop.lock.json"
    watchdog_status_path = ROOT / "logs" / "forward_paper_feed" / "forward_runtime_watchdog_loop_status.json"
    watchdog_lock_path = ROOT / "logs" / "forward_paper_feed" / "forward_runtime_watchdog_loop.lock.json"
    crowd_fade_status_path = ROOT / "logs" / "forward_paper_feed" / "crowd_fade_observer_loop_status.json"
    crowd_fade_lock_path = ROOT / "logs" / "forward_paper_feed" / "crowd_fade_observer_loop.lock.json"
    crowd_fade_last_run_path = ROOT / "logs" / "forward_paper_feed" / "crowd_fade_refresh_last_run.json"
    backup_status_path = ROOT / "logs" / "runtime_backup" / "daily_drive_backup_loop_status.json"
    backup_lock_path = ROOT / "logs" / "runtime_backup" / "daily_drive_backup_loop.lock.json"
    backup_last_run_path = ROOT / "logs" / "runtime_backup" / "daily_drive_backup_last_run.json"
    microstructure_book_status_path = ROOT / "logs" / "cross_venue_microstructure" / "microstructure_book_loop_status.json"
    microstructure_book_lock_path = ROOT / "logs" / "cross_venue_microstructure" / "microstructure_book_loop.lock.json"
    microstructure_unblock_status_path = ROOT / "logs" / "cross_venue_microstructure" / "microstructure_unblock_status_loop_status.json"
    microstructure_unblock_lock_path = ROOT / "logs" / "cross_venue_microstructure" / "microstructure_unblock_status_loop.lock.json"
    microstructure_unblock_report_path = ROOT / "docs" / "MICROSTRUCTURE_UNBLOCK_STATUS_2026-07-03.json"
    bybit_gate_pulse_status_path = ROOT / "logs" / "liquidation_bybit" / "bybit_forward_gate_pulse_loop_status.json"
    bybit_gate_pulse_lock_path = ROOT / "logs" / "liquidation_bybit" / "bybit_forward_gate_pulse_loop.lock.json"
    real_edge_observer_pulse_status_path = ROOT / "logs" / "real_edge_observer" / "real_edge_observer_pulse_loop_status.json"
    real_edge_observer_pulse_lock_path = ROOT / "logs" / "real_edge_observer" / "real_edge_observer_pulse_loop.lock.json"
    cross_stack_replication_transition_status_path = ROOT / "logs" / "cross_stack_replication" / "cross_stack_replication_transition_loop_status.json"
    cross_stack_replication_transition_lock_path = ROOT / "logs" / "cross_stack_replication" / "cross_stack_replication_transition_loop.lock.json"
    cex_funding_watchdog_status_path = ROOT / "logs" / "cex_dex_funding" / "cex_dex_funding_freshness_watchdog_loop_status.json"
    cex_funding_watchdog_lock_path = ROOT / "logs" / "cex_dex_funding" / "cex_dex_funding_freshness_watchdog_loop.lock.json"
    cex_funding_watchdog_report_path = ROOT / "docs" / "CEX_FUNDING_FRESHNESS_WATCHDOG_2026-07-13.json"
    cex_funding_incident_alert_report_path = ROOT / "docs" / "CEX_FUNDING_FRESHNESS_INCIDENT_ALERT_2026-07-13.json"
    bitunix_v3r4_loop_status_path = ROOT / "logs" / "bitunix_wo105_v3r4" / "bitunix_wo105_v3r4_forward_loop_status.json"
    bitunix_v3r4_loop_lock_path = ROOT / "logs" / "bitunix_wo105_v3r4" / "bitunix_wo105_v3r4_forward_loop.lock.json"
    bitunix_v3r4_status_path = ROOT / "docs" / "BITUNIX_WO105_V3R4_STATUS_2026-07-15.json"
    bitunix_v3r4_blind_gate_path = ROOT / "docs" / "BITUNIX_WO105_V3R4_BLIND_REVIEW_GATE_2026-07-15.json"
    bitunix_v3r4_first_cycle_gate_path = ROOT / "docs" / "BITUNIX_WO105_V3R4_FIRST_CYCLE_GATE_2026-07-15.json"
    bitunix_v3r4_health_path = ROOT / "docs" / "BITUNIX_WO105_V3R4_FORWARD_HEALTH_2026-07-15.json"
    last_run_path = ROOT / "logs" / "forward_paper_feed" / "scheduled_task_last_run.json"
    runtime = read_json(runtime_status_path)
    optimizer = read_json(optimizer_status_path)
    panel = read_json(panel_status_path)
    loop = read_json(loop_status_path)
    lock = read_json(loop_lock_path)
    watchdog = read_json(watchdog_status_path)
    watchdog_lock = read_json(watchdog_lock_path)
    crowd_fade = read_json(crowd_fade_status_path)
    crowd_fade_lock = read_json(crowd_fade_lock_path)
    crowd_fade_last_run = read_json(crowd_fade_last_run_path)
    backup_status = read_json(backup_status_path)
    backup_lock = read_json(backup_lock_path)
    backup_last_run = read_json(backup_last_run_path)
    microstructure_book = read_json(microstructure_book_status_path)
    microstructure_book_lock = read_json(microstructure_book_lock_path)
    microstructure_unblock = read_json(microstructure_unblock_status_path)
    microstructure_unblock_lock = read_json(microstructure_unblock_lock_path)
    microstructure_unblock_report = read_json(microstructure_unblock_report_path)
    bybit_gate_pulse = read_json(bybit_gate_pulse_status_path)
    bybit_gate_pulse_lock = read_json(bybit_gate_pulse_lock_path)
    real_edge_observer_pulse = read_json(real_edge_observer_pulse_status_path)
    real_edge_observer_pulse_lock = read_json(real_edge_observer_pulse_lock_path)
    cross_stack_replication_transition = read_json(cross_stack_replication_transition_status_path)
    cross_stack_replication_transition_lock = read_json(cross_stack_replication_transition_lock_path)
    cex_funding_watchdog = read_json(cex_funding_watchdog_status_path)
    cex_funding_watchdog_lock = read_json(cex_funding_watchdog_lock_path)
    cex_funding_watchdog_report = read_json(cex_funding_watchdog_report_path)
    cex_funding_incident_alert_report = read_json(cex_funding_incident_alert_report_path)
    bitunix_v3r4_loop_status = read_json(bitunix_v3r4_loop_status_path) or {}
    bitunix_v3r4_loop_lock = read_json(bitunix_v3r4_loop_lock_path) or {}
    bitunix_v3r4_status = read_json(bitunix_v3r4_status_path) or {}
    bitunix_v3r4_blind_gate = read_json(bitunix_v3r4_blind_gate_path) or {}
    bitunix_v3r4_first_cycle_gate = read_json(bitunix_v3r4_first_cycle_gate_path) or {}
    bitunix_v3r4_health = read_json(bitunix_v3r4_health_path) or {}
    last_run = read_json(last_run_path)
    raw_runtime_component_states = (
        runtime.get("runtime_component_states")
        if isinstance(runtime, dict) and isinstance(runtime.get("runtime_component_states"), list)
        else []
    )
    runtime_component_states: list[dict[str, Any]] = []
    for raw_state in raw_runtime_component_states:
        if not isinstance(raw_state, dict):
            continue
        state = dict(raw_state)
        receipt_state = runtime_receipt_identity_summary(state.get("id"))
        snapshot_pid = state.get("pid")
        receipt_pid = receipt_state.get("receipt_pid")
        state.update(receipt_state)
        state["snapshot_pid_alive_unbound"] = process_alive(snapshot_pid)
        state["snapshot_pid_matches_receipt"] = (
            receipt_state.get("receipt_valid") is True and snapshot_pid == receipt_pid
        )
        state["pid_drifted_from_snapshot"] = (
            receipt_state.get("receipt_valid") is True and snapshot_pid != receipt_pid
        )
        runtime_component_states.append(state)

    runtime_snapshot_age_seconds = timestamp_age_seconds(runtime.get("ts")) if isinstance(runtime, dict) else None
    runtime_snapshot_age_minutes = (
        round(runtime_snapshot_age_seconds / 60.0, 3)
        if runtime_snapshot_age_seconds is not None
        else None
    )
    runtime_snapshot_stale = (
        runtime_snapshot_age_minutes is None
        or runtime_snapshot_age_minutes > RUNTIME_STARTUP_SNAPSHOT_STALE_AFTER_MINUTES
    )
    receipt_identity_alive_count = sum(
        state.get("receipt_identity_alive") is True for state in runtime_component_states
    )
    receipt_pid_drift_count = sum(
        state.get("pid_drifted_from_snapshot") is True for state in runtime_component_states
    )
    bitunix_v3r4_state = next(
        (
            item
            for item in runtime_component_states
            if isinstance(item, dict) and item.get("id") == "bitunix_wo105_v3r4_forward"
        ),
        {},
    )
    return {
        "startup_folder": {
            "exists": startup_cmd.exists(),
            "path": str(startup_cmd),
        },
        "runtime": {
            "exists": isinstance(runtime, dict),
            "status": runtime.get("status") if isinstance(runtime, dict) else None,
            "ts": runtime.get("ts") if isinstance(runtime, dict) else None,
            "snapshot_age_minutes": runtime_snapshot_age_minutes,
            "snapshot_stale_after_minutes": RUNTIME_STARTUP_SNAPSHOT_STALE_AFTER_MINUTES,
            "snapshot_stale": runtime_snapshot_stale,
            "components_expected": runtime.get("runtime_components_expected") if isinstance(runtime, dict) else None,
            "components_healthy": runtime.get("runtime_components_healthy") if isinstance(runtime, dict) else None,
            "components_failed": runtime.get("runtime_components_failed") if isinstance(runtime, dict) else None,
            "component_states": runtime_component_states,
            "receipt_components_observed": len(runtime_component_states),
            "receipt_identity_alive_count": receipt_identity_alive_count,
            "receipt_pid_drift_count": receipt_pid_drift_count,
            "receipt_identity_all_alive": (
                bool(runtime_component_states)
                and receipt_identity_alive_count == len(runtime_component_states)
            ),
            "ownership_proof_scope": "startup_snapshot_plus_current_receipt_process_identity",
            "bitunix_wo105_v3r4": {
                "eligible": runtime.get("bitunix_wo105_v3r4_forward_eligible") if isinstance(runtime, dict) else None,
                "already_running_at_start": runtime.get("bitunix_wo105_v3r4_forward_already_running") if isinstance(runtime, dict) else None,
                "public_shadow_only": runtime.get("bitunix_wo105_v3r4_public_shadow_only") if isinstance(runtime, dict) else None,
                "decision": bitunix_v3r4_state.get("decision"),
                "ownership_decision": bitunix_v3r4_state.get("ownership_decision"),
                "job_contained": bitunix_v3r4_state.get("job_contained"),
                "pid": bitunix_v3r4_state.get("pid"),
                "loop_status": bitunix_v3r4_loop_status.get("status"),
                "loop_pid": bitunix_v3r4_loop_status.get("pid"),
                "loop_lock_exists": bitunix_v3r4_loop_lock_path.exists(),
                "loop_lock_pid": bitunix_v3r4_loop_lock.get("pid"),
                "phase": bitunix_v3r4_status.get("phase"),
                "forward_start_at": bitunix_v3r4_status.get("forward_start_at"),
                "forward_progress": bitunix_v3r4_status.get("forward_progress"),
                "terminal_forward_progress": bitunix_v3r4_status.get("terminal_forward_progress"),
                "first_cycle_decision": bitunix_v3r4_first_cycle_gate.get("decision"),
                "blind_review_decision": bitunix_v3r4_blind_gate.get("decision"),
                "edge_evaluated": bitunix_v3r4_status.get("edge_evaluated"),
                "health_decision": bitunix_v3r4_health.get("decision"),
                "health_failures": bitunix_v3r4_health.get("failures"),
                "health_warnings": bitunix_v3r4_health.get("warnings"),
                "rest_quality": bitunix_v3r4_health.get("rest_quality"),
                "ws_quality": bitunix_v3r4_health.get("ws_quality"),
                "can_trade": False,
            },
            "forward_loop_already_running": runtime.get("forward_loop_already_running") if isinstance(runtime, dict) else None,
            "forward_sleep_seconds": runtime.get("forward_sleep_seconds") if isinstance(runtime, dict) else None,
            "crowd_fade_loop_already_running": runtime.get("crowd_fade_loop_already_running") if isinstance(runtime, dict) else None,
            "crowd_fade_sleep_seconds": runtime.get("crowd_fade_sleep_seconds") if isinstance(runtime, dict) else None,
            "microstructure_unblock_status": runtime.get("microstructure_unblock_status") if isinstance(runtime, dict) else None,
            "microstructure_unblock_status_pid": runtime.get("microstructure_unblock_status_pid") if isinstance(runtime, dict) else None,
            "microstructure_unblock_status_alive": runtime.get("microstructure_unblock_status_alive") if isinstance(runtime, dict) else None,
            "microstructure_unblock_status_sleep_seconds": runtime.get("microstructure_unblock_status_sleep_seconds") if isinstance(runtime, dict) else None,
        },
        "optimizer": {
            "exists": isinstance(optimizer, dict),
            "status": optimizer.get("status") if isinstance(optimizer, dict) else None,
            "ts": optimizer.get("ts") if isinstance(optimizer, dict) else None,
            "control_panel_listening": optimizer.get("control_panel_listening") if isinstance(optimizer, dict) else None,
            "forward_loop_alive": optimizer.get("forward_loop_alive") if isinstance(optimizer, dict) else None,
            "crowd_fade_loop_alive": optimizer.get("crowd_fade_loop_alive") if isinstance(optimizer, dict) else None,
            "microstructure_unblock_status_loop_alive": optimizer.get("microstructure_unblock_status_loop_alive") if isinstance(optimizer, dict) else None,
        },
        "control_panel_autostart": {
            "exists": isinstance(panel, dict),
            "status": panel.get("status") if isinstance(panel, dict) else None,
            "ts": panel.get("ts") if isinstance(panel, dict) else None,
            "port": panel.get("port") if isinstance(panel, dict) else None,
        },
        "forward_loop": {
            "exists": isinstance(loop, dict),
            "status": loop.get("status") if isinstance(loop, dict) else None,
            "ts": loop.get("ts") if isinstance(loop, dict) else None,
            "pid": loop.get("pid") if isinstance(loop, dict) else None,
            "sleep_seconds": loop.get("sleep_seconds") if isinstance(loop, dict) else None,
            "lock_exists": loop_lock_path.exists(),
            "lock_pid": lock.get("pid") if isinstance(lock, dict) else None,
        },
        "watchdog_loop": {
            "exists": isinstance(watchdog, dict),
            "status": watchdog.get("status") if isinstance(watchdog, dict) else None,
            "exit_code": watchdog.get("exit_code") if isinstance(watchdog, dict) else None,
            "ts": watchdog.get("ts") if isinstance(watchdog, dict) else None,
            "pid": watchdog.get("pid") if isinstance(watchdog, dict) else None,
            "sleep_seconds": watchdog.get("sleep_seconds") if isinstance(watchdog, dict) else None,
            "last_health_exit_code": (
                watchdog.get("extra", {}).get("last_health_exit_code")
                if isinstance(watchdog, dict) and isinstance(watchdog.get("extra"), dict)
                else None
            ),
            "last_notify_exit_code": (
                watchdog.get("extra", {}).get("last_notify_exit_code")
                if isinstance(watchdog, dict) and isinstance(watchdog.get("extra"), dict)
                else None
            ),
            "lock_exists": watchdog_lock_path.exists(),
            "lock_pid": watchdog_lock.get("pid") if isinstance(watchdog_lock, dict) else None,
        },
        "crowd_fade_loop": {
            "exists": isinstance(crowd_fade, dict),
            "status": crowd_fade.get("status") if isinstance(crowd_fade, dict) else None,
            "ts": crowd_fade.get("ts") if isinstance(crowd_fade, dict) else None,
            "pid": crowd_fade.get("pid") if isinstance(crowd_fade, dict) else None,
            "sleep_seconds": crowd_fade.get("sleep_seconds") if isinstance(crowd_fade, dict) else None,
            "last_refresh_exit_code": (
                crowd_fade.get("extra", {}).get("last_refresh_exit_code")
                if isinstance(crowd_fade, dict) and isinstance(crowd_fade.get("extra"), dict)
                else None
            ),
            "lock_exists": crowd_fade_lock_path.exists(),
            "lock_pid": crowd_fade_lock.get("pid") if isinstance(crowd_fade_lock, dict) else None,
        },
        "crowd_fade_last_run": {
            "exists": isinstance(crowd_fade_last_run, dict),
            "status": crowd_fade_last_run.get("status") if isinstance(crowd_fade_last_run, dict) else None,
            "exit_code": crowd_fade_last_run.get("exit_code") if isinstance(crowd_fade_last_run, dict) else None,
            "ts": crowd_fade_last_run.get("ts") if isinstance(crowd_fade_last_run, dict) else None,
            "message": crowd_fade_last_run.get("message") if isinstance(crowd_fade_last_run, dict) else None,
        },
        "daily_backup_loop": {
            "exists": isinstance(backup_status, dict),
            "status": backup_status.get("status") if isinstance(backup_status, dict) else None,
            "ts": backup_status.get("ts") if isinstance(backup_status, dict) else None,
            "pid": backup_status.get("pid") if isinstance(backup_status, dict) else None,
            "sleep_seconds": backup_status.get("sleep_seconds") if isinstance(backup_status, dict) else None,
            "lock_exists": backup_lock_path.exists(),
            "lock_pid": backup_lock.get("pid") if isinstance(backup_lock, dict) else None,
            "last_run_status": backup_last_run.get("status") if isinstance(backup_last_run, dict) else None,
            "last_run_ts": backup_last_run.get("ts") if isinstance(backup_last_run, dict) else None,
        },
        "microstructure_book_loop": {
            "exists": isinstance(microstructure_book, dict),
            "status": microstructure_book.get("status") if isinstance(microstructure_book, dict) else None,
            "exit_code": microstructure_book.get("exit_code") if isinstance(microstructure_book, dict) else None,
            "ts": microstructure_book.get("ts") if isinstance(microstructure_book, dict) else None,
            "pid": microstructure_book.get("pid") if isinstance(microstructure_book, dict) else None,
            "pid_alive": runtime_report_process_alive(
                "cross_venue_microstructure_book", microstructure_book, microstructure_book_lock
            ),
            "sleep_seconds": microstructure_book.get("sleep_seconds") if isinstance(microstructure_book, dict) else None,
            "lock_exists": microstructure_book_lock_path.exists(),
            "lock_pid": microstructure_book_lock.get("pid") if isinstance(microstructure_book_lock, dict) else None,
            "signals_allowed": microstructure_book.get("signals_allowed") if isinstance(microstructure_book, dict) else None,
            "paper_entries_allowed": microstructure_book.get("paper_entries_allowed") if isinstance(microstructure_book, dict) else None,
            "orders_allowed": microstructure_book.get("orders_allowed") if isinstance(microstructure_book, dict) else None,
            "can_trade": microstructure_book.get("can_trade") if isinstance(microstructure_book, dict) else None,
        },
        "microstructure_unblock_status_loop": {
            "exists": isinstance(microstructure_unblock, dict),
            "status": microstructure_unblock.get("status") if isinstance(microstructure_unblock, dict) else None,
            "exit_code": microstructure_unblock.get("exit_code") if isinstance(microstructure_unblock, dict) else None,
            "ts": microstructure_unblock.get("ts") if isinstance(microstructure_unblock, dict) else None,
            "pid": microstructure_unblock.get("pid") if isinstance(microstructure_unblock, dict) else None,
            "pid_alive": runtime_report_process_alive(
                "microstructure_unblock_status", microstructure_unblock, microstructure_unblock_lock
            ),
            "sleep_seconds": microstructure_unblock.get("sleep_seconds") if isinstance(microstructure_unblock, dict) else None,
            "lock_exists": microstructure_unblock_lock_path.exists(),
            "lock_pid": microstructure_unblock_lock.get("pid") if isinstance(microstructure_unblock_lock, dict) else None,
            "report_generated_at": microstructure_unblock_report.get("generated_at") if isinstance(microstructure_unblock_report, dict) else None,
            "decision": microstructure_unblock_report.get("decision") if isinstance(microstructure_unblock_report, dict) else None,
            "book_coverage_pct": (
                microstructure_unblock_report.get("coverage", {}).get("book_coverage_pct")
                if isinstance(microstructure_unblock_report, dict) and isinstance(microstructure_unblock_report.get("coverage"), dict)
                else None
            ),
            "recent_1h_book_coverage_pct": (
                microstructure_unblock_report.get("book_diagnostic", {}).get("recent_1h_dual_book_pct")
                if isinstance(microstructure_unblock_report, dict) and isinstance(microstructure_unblock_report.get("book_diagnostic"), dict)
                else None
            ),
            "recent_6h_book_coverage_pct": (
                microstructure_unblock_report.get("book_diagnostic", {}).get("recent_6h_dual_book_pct")
                if isinstance(microstructure_unblock_report, dict) and isinstance(microstructure_unblock_report.get("book_diagnostic"), dict)
                else None
            ),
            "eta_utc": (
                microstructure_unblock_report.get("book_diagnostic", {}).get("eta_utc")
                if isinstance(microstructure_unblock_report, dict) and isinstance(microstructure_unblock_report.get("book_diagnostic"), dict)
                else None
            ),
            "observability_only": True,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "bybit_gate_pulse_loop": {
            "exists": isinstance(bybit_gate_pulse, dict),
            "status": bybit_gate_pulse.get("status") if isinstance(bybit_gate_pulse, dict) else None,
            "exit_code": bybit_gate_pulse.get("exit_code") if isinstance(bybit_gate_pulse, dict) else None,
            "ts": bybit_gate_pulse.get("ts") if isinstance(bybit_gate_pulse, dict) else None,
            "pid": bybit_gate_pulse.get("pid") if isinstance(bybit_gate_pulse, dict) else None,
            "pid_alive": runtime_report_process_alive(
                "bybit_forward_gate_pulse", bybit_gate_pulse, bybit_gate_pulse_lock
            ),
            "sleep_seconds": bybit_gate_pulse.get("sleep_seconds") if isinstance(bybit_gate_pulse, dict) else None,
            "lock_exists": bybit_gate_pulse_lock_path.exists(),
            "lock_pid": bybit_gate_pulse_lock.get("pid") if isinstance(bybit_gate_pulse_lock, dict) else None,
            "live_trading_locked": bybit_gate_pulse.get("live_trading_locked") if isinstance(bybit_gate_pulse, dict) else None,
            "signals_allowed": bybit_gate_pulse.get("signals_allowed") if isinstance(bybit_gate_pulse, dict) else None,
            "paper_entries_allowed": bybit_gate_pulse.get("paper_entries_allowed") if isinstance(bybit_gate_pulse, dict) else None,
            "orders_allowed": bybit_gate_pulse.get("orders_allowed") if isinstance(bybit_gate_pulse, dict) else None,
        },
        "real_edge_observer_pulse_loop": {
            "exists": isinstance(real_edge_observer_pulse, dict),
            "status": real_edge_observer_pulse.get("status") if isinstance(real_edge_observer_pulse, dict) else None,
            "exit_code": real_edge_observer_pulse.get("exit_code") if isinstance(real_edge_observer_pulse, dict) else None,
            "ts": real_edge_observer_pulse.get("ts") if isinstance(real_edge_observer_pulse, dict) else None,
            "pid": real_edge_observer_pulse.get("pid") if isinstance(real_edge_observer_pulse, dict) else None,
            "pid_alive": runtime_report_process_alive(
                "real_edge_observer", real_edge_observer_pulse, real_edge_observer_pulse_lock
            ),
            "sleep_seconds": real_edge_observer_pulse.get("sleep_seconds") if isinstance(real_edge_observer_pulse, dict) else None,
            "lock_exists": real_edge_observer_pulse_lock_path.exists(),
            "lock_pid": real_edge_observer_pulse_lock.get("pid") if isinstance(real_edge_observer_pulse_lock, dict) else None,
            "live_trading_locked": real_edge_observer_pulse.get("live_trading_locked") if isinstance(real_edge_observer_pulse, dict) else None,
            "signals_allowed": real_edge_observer_pulse.get("signals_allowed") if isinstance(real_edge_observer_pulse, dict) else None,
            "paper_entries_allowed": real_edge_observer_pulse.get("paper_entries_allowed") if isinstance(real_edge_observer_pulse, dict) else None,
            "orders_allowed": real_edge_observer_pulse.get("orders_allowed") if isinstance(real_edge_observer_pulse, dict) else None,
            "telegram_send_allowed": real_edge_observer_pulse.get("telegram_send_allowed") if isinstance(real_edge_observer_pulse, dict) else None,
        },
        "cross_stack_replication_transition_loop": {
            "exists": isinstance(cross_stack_replication_transition, dict),
            "status": cross_stack_replication_transition.get("status") if isinstance(cross_stack_replication_transition, dict) else None,
            "exit_code": cross_stack_replication_transition.get("exit_code") if isinstance(cross_stack_replication_transition, dict) else None,
            "ts": cross_stack_replication_transition.get("ts") if isinstance(cross_stack_replication_transition, dict) else None,
            "pid": cross_stack_replication_transition.get("pid") if isinstance(cross_stack_replication_transition, dict) else None,
            "pid_alive": runtime_report_process_alive(
                "cross_stack_replication_transition",
                cross_stack_replication_transition,
                cross_stack_replication_transition_lock,
            ),
            "sleep_seconds": cross_stack_replication_transition.get("sleep_seconds") if isinstance(cross_stack_replication_transition, dict) else None,
            "lock_exists": cross_stack_replication_transition_lock_path.exists(),
            "lock_pid": cross_stack_replication_transition_lock.get("pid") if isinstance(cross_stack_replication_transition_lock, dict) else None,
            "live_trading_locked": cross_stack_replication_transition.get("live_trading_locked") if isinstance(cross_stack_replication_transition, dict) else None,
            "external_replication_monitor_only": cross_stack_replication_transition.get("external_replication_monitor_only") if isinstance(cross_stack_replication_transition, dict) else None,
            "signals_allowed": cross_stack_replication_transition.get("signals_allowed") if isinstance(cross_stack_replication_transition, dict) else None,
            "alerts_allowed": cross_stack_replication_transition.get("alerts_allowed") if isinstance(cross_stack_replication_transition, dict) else None,
            "paper_entries_allowed": cross_stack_replication_transition.get("paper_entries_allowed") if isinstance(cross_stack_replication_transition, dict) else None,
            "orders_allowed": cross_stack_replication_transition.get("orders_allowed") if isinstance(cross_stack_replication_transition, dict) else None,
            "telegram_send_allowed": cross_stack_replication_transition.get("telegram_send_allowed") if isinstance(cross_stack_replication_transition, dict) else None,
        },
        "cex_funding_freshness_watchdog_loop": {
            "exists": isinstance(cex_funding_watchdog, dict),
            "status": cex_funding_watchdog.get("status") if isinstance(cex_funding_watchdog, dict) else None,
            "exit_code": cex_funding_watchdog.get("exit_code") if isinstance(cex_funding_watchdog, dict) else None,
            "ts": cex_funding_watchdog.get("ts") if isinstance(cex_funding_watchdog, dict) else None,
            "pid": cex_funding_watchdog.get("pid") if isinstance(cex_funding_watchdog, dict) else None,
            "pid_alive": runtime_report_process_alive(
                "cex_funding_freshness_watchdog", cex_funding_watchdog, cex_funding_watchdog_lock
            ),
            "sleep_seconds": cex_funding_watchdog.get("sleep_seconds") if isinstance(cex_funding_watchdog, dict) else None,
            "lock_exists": cex_funding_watchdog_lock_path.exists(),
            "lock_pid": cex_funding_watchdog_lock.get("pid") if isinstance(cex_funding_watchdog_lock, dict) else None,
            "decision": cex_funding_watchdog_report.get("decision") if isinstance(cex_funding_watchdog_report, dict) else None,
            "healthy": cex_funding_watchdog_report.get("healthy") if isinstance(cex_funding_watchdog_report, dict) else False,
            "blockers": cex_funding_watchdog_report.get("blockers") if isinstance(cex_funding_watchdog_report, dict) else ["watchdog_report_missing"],
            "incident_alert_decision": cex_funding_incident_alert_report.get("decision") if isinstance(cex_funding_incident_alert_report, dict) else None,
            "incident_transition_kind": cex_funding_incident_alert_report.get("transition_kind") if isinstance(cex_funding_incident_alert_report, dict) else None,
            "incident_pending_notifications": cex_funding_incident_alert_report.get("pending_notifications") if isinstance(cex_funding_incident_alert_report, dict) else None,
            "incident_telegram_response_ok": cex_funding_incident_alert_report.get("telegram_response_ok") if isinstance(cex_funding_incident_alert_report, dict) else None,
            "automatic_restart_allowed": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "forward_last_run": {
            "exists": isinstance(last_run, dict),
            "status": last_run.get("status") if isinstance(last_run, dict) else None,
            "exit_code": last_run.get("exit_code") if isinstance(last_run, dict) else None,
            "ts": last_run.get("ts") if isinstance(last_run, dict) else None,
            "message": last_run.get("message") if isinstance(last_run, dict) else None,
        },
        "live_trading_locked": True,
    }


def latest_oi_funding_data_quality_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "OI_FUNDING_DATA_QUALITY_2026-06-15.json"
    payload = read_json(path) if path.exists() else None
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path)}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    replay = payload.get("replay_trade_coverage") if isinstance(payload.get("replay_trade_coverage"), dict) else {}
    endpoint = payload.get("endpoint_window") if isinstance(payload.get("endpoint_window"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "generated_at": payload.get("generated_at"),
        "classification": summary.get("classification"),
        "kline_rows": summary.get("kline_rows"),
        "merged_oi_rows": summary.get("merged_oi_rows"),
        "aligned_oi_rows": summary.get("aligned_oi_rows"),
        "aligned_oi_coverage_pct": summary.get("aligned_oi_coverage_pct"),
        "aligned_funding_coverage_pct": summary.get("aligned_funding_coverage_pct"),
        "replay_trades": replay.get("trades"),
        "replay_full_context_available": replay.get("full_context_available"),
        "replay_full_context_coverage_pct": replay.get("full_context_coverage_pct"),
        "oi_first": endpoint.get("oi_first"),
        "oi_last": endpoint.get("oi_last"),
        "oi_window_days": endpoint.get("oi_window_days"),
        "decision": payload.get("decision"),
        "next_action": payload.get("next_action"),
    }


def latest_forward_runtime_health_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "FORWARD_RUNTIME_HEALTH_2026-06-16.json"
    payload = read_json(path) if path.exists() else None
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path)}
    observed = payload.get("observed") if isinstance(payload.get("observed"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "generated_at": payload.get("generated_at"),
        "classification": payload.get("classification"),
        "decision": payload.get("decision"),
        "last_run_status": observed.get("last_run_status"),
        "last_run_exit_code": observed.get("last_run_exit_code"),
        "last_run_age_minutes": observed.get("last_run_age_minutes"),
        "loop_status": observed.get("loop_status"),
        "loop_pid": observed.get("loop_pid"),
        "loop_status_age_minutes": observed.get("loop_status_age_minutes"),
        "panel_port_open": observed.get("panel_port_open"),
        "signal_status": observed.get("signal_status"),
        "latest_closed_bar": observed.get("latest_closed_bar"),
        "promotion_decision": observed.get("promotion_decision"),
        "promotion_active_filter_allowed": observed.get("promotion_active_filter_allowed"),
        "promotion_live_execution_allowed": observed.get("promotion_live_execution_allowed"),
        "data_quality_classification": observed.get("data_quality_classification"),
        "next_action": payload.get("next_action"),
        "can_trade": False,
    }


def latest_event_export_summary() -> dict[str, Any]:
    candidates = list((ROOT / "_dl" / "event_exports").glob("*.json")) + list(OUT_DIR.glob("*EVENTS.json"))
    if not candidates:
        return {"exists": False}
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": True, "path": str(path), "read_error": True}
    return {
        "exists": True,
        "path": str(path),
        "summary": payload.get("summary"),
        "params": payload.get("params"),
        "files": payload.get("files"),
    }


def latest_feature_miner_summary() -> dict[str, Any]:
    candidates = list((ROOT / "_dl" / "event_exports").glob("*v07_miner.json")) + list(OUT_DIR.glob("*V07_MINER.json"))
    if not candidates:
        return {"exists": False}
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": True, "path": str(path), "read_error": True}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    top = result.get("top_candidates") if isinstance(result.get("top_candidates"), list) else []
    return {
        "exists": True,
        "path": str(path),
        "baseline": result.get("baseline"),
        "top_candidate": top[0] if top else None,
        "promoted_for_rule_design": len(result.get("promoted_for_rule_design", [])) if isinstance(result.get("promoted_for_rule_design"), list) else None,
    }


def latest_research_grid_summary() -> dict[str, Any]:
    candidates = list((ROOT / "_dl" / "research_grid").glob("*v09_grid.json")) + list(OUT_DIR.glob("*V09_GRID.json"))
    if not candidates:
        return {"exists": False}
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": True, "path": str(path), "read_error": True}
    grid_results = payload.get("grid_results") if isinstance(payload.get("grid_results"), list) else []
    compact = [
        {
            "grid_id": item.get("grid_id"),
            "events": item.get("events"),
            "top_candidate": item.get("top_candidate"),
        }
        for item in grid_results
    ]
    return {
        "exists": True,
        "path": str(path),
        "grid_results": compact,
        "condition_repeats": payload.get("condition_repeats"),
        "promoted_for_strategy_design": len(payload.get("promoted_for_strategy_design", [])) if isinstance(payload.get("promoted_for_strategy_design"), list) else None,
    }


def latest_hardening_summary() -> dict[str, Any]:
    candidates = list((ROOT / "_dl" / "hardening").glob("*V10_HARDENING.json")) + list(OUT_DIR.glob("*V10_HARDENING.json"))
    if not candidates:
        return {"exists": False}
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": True, "path": str(path), "read_error": True}
    return {
        "exists": True,
        "path": str(path),
        "decision": payload.get("decision"),
        "passed": len(payload.get("passed", [])) if isinstance(payload.get("passed"), list) else None,
        "candidates": [
            {
                "id": item.get("id"),
                "summary": item.get("summary"),
                "research_gate": item.get("research_gate"),
            }
            for item in payload.get("candidates", [])
            if isinstance(item, dict)
        ],
    }


def latest_edge_same_shape_shadow_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "EDGE_SAME_SHAPE_SHADOW_OBSERVER_2026-06-19.json"
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": False}
    latest = payload.get("latest") if isinstance(payload.get("latest"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "decision": payload.get("decision"),
        "latest_closed_bar_ts": latest.get("latest_closed_bar_ts"),
        "latest_closed_close": latest.get("latest_closed_close"),
        "variants_checked": latest.get("variants_checked"),
        "base_signals": latest.get("base_signals"),
        "variant_signals": latest.get("variant_signals"),
        "signalling_variants": latest.get("signalling_variants"),
        "can_trade": payload.get("can_trade"),
    }


def latest_edge_same_shape_shadow_scoreboard_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "EDGE_SAME_SHAPE_SHADOW_SCOREBOARD_2026-06-19.json"
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": False}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "decision": payload.get("decision"),
        "classification": summary.get("classification"),
        "shadow_signal_events": summary.get("shadow_signal_events"),
        "resolved": summary.get("resolved"),
        "unresolved": summary.get("unresolved"),
        "winrate_pct": summary.get("winrate_pct"),
        "expectancy_r": summary.get("expectancy_r"),
        "can_trade": payload.get("can_trade"),
    }


def latest_edge_compression_guard_summary() -> dict[str, Any]:
    diagnostic_path = ROOT / "docs" / "EDGE_COMPRESSION_GUARD_DIAGNOSTIC_2026-06-19.json"
    observer_path = ROOT / "docs" / "EDGE_COMPRESSION_GUARD_SHADOW_OBSERVER_2026-06-19.json"
    scoreboard_path = ROOT / "docs" / "EDGE_COMPRESSION_GUARD_SHADOW_SCOREBOARD_2026-06-19.json"
    diagnostic = read_json(diagnostic_path)
    observer = read_json(observer_path)
    scoreboard = read_json(scoreboard_path)
    observer_latest = observer.get("latest") if isinstance(observer, dict) and isinstance(observer.get("latest"), dict) else {}
    gate = scoreboard.get("guard_shadow_gate") if isinstance(scoreboard, dict) and isinstance(scoreboard.get("guard_shadow_gate"), dict) else {}
    keep = scoreboard.get("keep_bucket") if isinstance(scoreboard, dict) and isinstance(scoreboard.get("keep_bucket"), dict) else {}
    veto = scoreboard.get("veto_bucket") if isinstance(scoreboard, dict) and isinstance(scoreboard.get("veto_bucket"), dict) else {}
    return {
        "diagnostic_exists": isinstance(diagnostic, dict),
        "observer_exists": isinstance(observer, dict),
        "scoreboard_exists": isinstance(scoreboard, dict),
        "diagnostic_path": str(diagnostic_path),
        "observer_path": str(observer_path),
        "scoreboard_path": str(scoreboard_path),
        "diagnostic_decision": diagnostic.get("decision") if isinstance(diagnostic, dict) else None,
        "diagnostic_candidate_count": diagnostic.get("candidate_count") if isinstance(diagnostic, dict) else None,
        "diagnostic_watchlist_count": diagnostic.get("watchlist_count") if isinstance(diagnostic, dict) else None,
        "observer_status": observer_latest.get("status"),
        "guard_id": observer_latest.get("guard_id"),
        "guard_action": observer_latest.get("guard_action"),
        "raw_signals": observer_latest.get("raw_signals"),
        "refined_signals": observer_latest.get("refined_signals"),
        "gate_decision": gate.get("decision"),
        "keep_events": keep.get("signal_events"),
        "veto_events": veto.get("signal_events"),
        "keep_resolved": keep.get("resolved"),
        "veto_resolved": veto.get("resolved"),
        "can_trade": False,
    }


def latest_crypto_guides_web_ingest_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "CRYPTO_GUIDES_WEB_INGEST_2026-06-19.json"
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": False}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "decision": payload.get("decision"),
        "fetched_pages": source.get("fetched_pages"),
        "route_count": summary.get("route_count"),
        "codable_now_routes": summary.get("codable_now_routes"),
        "guard_overlay_routes": summary.get("guard_overlay_routes"),
        "external_data_routes": summary.get("external_data_routes"),
        "can_trade": payload.get("can_trade"),
    }


def latest_crowd_positioning_summary() -> dict[str, Any]:
    collector_path = ROOT / "docs" / "BINANCE_CROWD_POSITIONING_COLLECTOR_2026-06-19.json"
    backfill_path = ROOT / "docs" / "BINANCE_VISION_CROWD_BACKFILL_2026-06-23.json"
    diagnostic_path = ROOT / "docs" / "CROWD_FADE_POSITIONING_DIAGNOSTIC_2026-06-19.json"
    nested_holdout_path = ROOT / "docs" / "CROWD_FADE_NESTED_HOLDOUT_2026-06-23.json"
    observer_path = ROOT / "docs" / "CROWD_FADE_POSITIONING_SHADOW_OBSERVER_2026-06-19.json"
    scoreboard_path = ROOT / "docs" / "CROWD_FADE_POSITIONING_SHADOW_SCOREBOARD_2026-06-19.json"
    notify_path = ROOT / "docs" / "CROWD_FADE_POSITIONING_TELEGRAM_NOTIFY_2026-06-19.json"
    drill_path = ROOT / "docs" / "CROWD_FADE_POSITIONING_TELEGRAM_DRILL_2026-06-19.json"
    promotion_path = ROOT / "docs" / "CROWD_FADE_POSITIONING_PROMOTION_GATE_2026-06-19.json"
    refresh_pack_path = ROOT / "docs" / "CROWD_FADE_REFRESH_PACK_2026-06-19.json"
    collector = read_json(collector_path)
    backfill = read_json(backfill_path)
    diagnostic = read_json(diagnostic_path)
    nested_holdout = read_json(nested_holdout_path)
    observer = read_json(observer_path)
    scoreboard = read_json(scoreboard_path)
    notify = read_json(notify_path)
    drill = read_json(drill_path)
    promotion_gate = read_json(promotion_path)
    refresh_pack = read_json(refresh_pack_path)
    artifacts = collector.get("artifacts") if isinstance(collector, dict) and isinstance(collector.get("artifacts"), list) else []
    backfill_artifacts = backfill.get("artifacts") if isinstance(backfill, dict) and isinstance(backfill.get("artifacts"), list) else []
    coverage = diagnostic.get("coverage") if isinstance(diagnostic, dict) and isinstance(diagnostic.get("coverage"), list) else []
    top_results = diagnostic.get("top_results") if isinstance(diagnostic, dict) and isinstance(diagnostic.get("top_results"), list) else []
    best = top_results[0] if top_results and isinstance(top_results[0], dict) else {}
    best_summary = best.get("summary") if isinstance(best.get("summary"), dict) else {}
    best_holdout = best.get("holdout_summary") if isinstance(best.get("holdout_summary"), dict) else {}
    observer_latest = observer.get("latest") if isinstance(observer, dict) and isinstance(observer.get("latest"), dict) else {}
    scoreboard_summary = scoreboard.get("summary") if isinstance(scoreboard, dict) and isinstance(scoreboard.get("summary"), dict) else {}
    return {
        "collector_exists": isinstance(collector, dict),
        "backfill_exists": isinstance(backfill, dict),
        "diagnostic_exists": isinstance(diagnostic, dict),
        "nested_holdout_exists": isinstance(nested_holdout, dict),
        "observer_exists": isinstance(observer, dict),
        "scoreboard_exists": isinstance(scoreboard, dict),
        "notify_exists": isinstance(notify, dict),
        "drill_exists": isinstance(drill, dict),
        "promotion_gate_exists": isinstance(promotion_gate, dict),
        "refresh_pack_exists": isinstance(refresh_pack, dict),
        "collector_path": str(collector_path),
        "backfill_path": str(backfill_path),
        "diagnostic_path": str(diagnostic_path),
        "nested_holdout_path": str(nested_holdout_path),
        "observer_path": str(observer_path),
        "scoreboard_path": str(scoreboard_path),
        "notify_path": str(notify_path),
        "drill_path": str(drill_path),
        "promotion_gate_path": str(promotion_path),
        "refresh_pack_path": str(refresh_pack_path),
        "collector_decision": collector.get("decision") if isinstance(collector, dict) else None,
        "backfill_decision": backfill.get("decision") if isinstance(backfill, dict) else None,
        "backfill_rows": {
            str(item.get("interval")): item.get("merged_rows")
            for item in backfill_artifacts
            if isinstance(item, dict) and item.get("interval")
        },
        "backfill_coverage_pct": {
            str(item.get("interval")): item.get("coverage", {}).get("coverage_pct")
            for item in backfill_artifacts
            if isinstance(item, dict) and item.get("interval") and isinstance(item.get("coverage"), dict)
        },
        "diagnostic_decision": diagnostic.get("decision") if isinstance(diagnostic, dict) else None,
        "nested_holdout_decision": nested_holdout.get("decision") if isinstance(nested_holdout, dict) else None,
        "nested_holdout_train_qualified": nested_holdout.get("qualified_train_variants") if isinstance(nested_holdout, dict) else None,
        "scoreboard_classification": scoreboard_summary.get("classification"),
        "scoreboard_signals": scoreboard_summary.get("observer_signal_events"),
        "scoreboard_resolved": scoreboard_summary.get("resolved"),
        "scoreboard_expectancy_r": scoreboard_summary.get("expectancy_r"),
        "notify_decision": notify.get("decision") if isinstance(notify, dict) else None,
        "notify_signal_found": notify.get("signal_found") if isinstance(notify, dict) else None,
        "notify_telegram_response_ok": notify.get("telegram_response_ok") if isinstance(notify, dict) else None,
        "drill_decision": drill.get("decision") if isinstance(drill, dict) else None,
        "drill_notify_decision": (
            drill.get("notify_result", {}).get("notify_report", {}).get("decision")
            if isinstance(drill, dict)
            and isinstance(drill.get("notify_result"), dict)
            and isinstance(drill.get("notify_result", {}).get("notify_report"), dict)
            else None
        ),
        "promotion_decision": promotion_gate.get("decision") if isinstance(promotion_gate, dict) else None,
        "watch_observer_allowed": (
            promotion_gate.get("promotion", {}).get("watch_observer_allowed")
            if isinstance(promotion_gate, dict) and isinstance(promotion_gate.get("promotion"), dict)
            else None
        ),
        "paper_design_review_allowed": (
            promotion_gate.get("promotion", {}).get("paper_design_review_allowed")
            if isinstance(promotion_gate, dict) and isinstance(promotion_gate.get("promotion"), dict)
            else None
        ),
        "refresh_pack_decision": refresh_pack.get("decision") if isinstance(refresh_pack, dict) else None,
        "refresh_pack_failed_steps": refresh_pack.get("failed_steps") if isinstance(refresh_pack, dict) else None,
        "observer_status": observer_latest.get("status"),
        "observer_signal_time": observer_latest.get("signal_time"),
        "observer_side_hint": observer_latest.get("side_hint"),
        "observer_ratio_z": observer_latest.get("ratio_z"),
        "interval_rows": {
            str(item.get("interval")): item.get("merged_rows")
            for item in artifacts
            if isinstance(item, dict) and item.get("interval")
        },
        "matched_bars": {
            str(item.get("interval")): item.get("matched_bars")
            for item in coverage
            if isinstance(item, dict) and item.get("interval")
        },
        "evaluated_count": diagnostic.get("evaluated_count") if isinstance(diagnostic, dict) else None,
        "candidate_count": diagnostic.get("candidate_count") if isinstance(diagnostic, dict) else None,
        "watchlist_count": diagnostic.get("watchlist_count") if isinstance(diagnostic, dict) else None,
        "best_strategy_id": best.get("strategy_id"),
        "best_classification": best.get("classification"),
        "best_trades": best_summary.get("trades"),
        "best_winrate_pct": best_summary.get("winrate_pct"),
        "best_expectancy_r": best_summary.get("expectancy_r"),
        "best_holdout_trades": best_holdout.get("trades"),
        "best_holdout_expectancy_r": best_holdout.get("expectancy_r"),
        "can_trade": False,
    }


def latest_active_strategy_runtime_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "ACTIVE_STRATEGY_RUNTIME_MAP_2026-06-22.json"
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path)}
    strategies = payload.get("strategies") if isinstance(payload.get("strategies"), list) else []
    coverage = payload.get("watchdog_coverage") if isinstance(payload.get("watchdog_coverage"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "generated_at": payload.get("generated_at"),
        "decision": payload.get("decision"),
        "strategy_family_count": payload.get("strategy_family_count"),
        "active_observer_count": payload.get("active_observer_count"),
        "rejected_family_count": payload.get("rejected_family_count"),
        "families": [
            {
                "family": item.get("family"),
                "runtime_status": item.get("runtime_status"),
                "observer_status": item.get("observer_status"),
                "promotion": item.get("promotion"),
            }
            for item in strategies
            if isinstance(item, dict)
        ],
        "scheduler_executable_steps": coverage.get("scheduler_executable_steps"),
        "scheduler_nonzero_steps": coverage.get("scheduler_nonzero_steps"),
        "health_classification": coverage.get("health_classification"),
        "can_trade": False,
    }


def latest_four_family_portfolio_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "FOUR_FAMILY_FORWARD_PORTFOLIO_SCOREBOARD_2026-06-22.json"
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path)}
    portfolio = payload.get("portfolio") if isinstance(payload.get("portfolio"), dict) else {}
    lifecycle_path = ROOT / "docs" / "FORWARD_EVIDENCE_LIFECYCLE_2026-06-23.json"
    lifecycle = read_json(lifecycle_path)
    lifecycle_families = lifecycle.get("families") if isinstance(lifecycle.get("families"), list) else []
    return {
        "exists": True,
        "path": str(path),
        "generated_at": payload.get("generated_at"),
        "decision": payload.get("decision"),
        "total_resolved": portfolio.get("total_resolved"),
        "total_net_r": portfolio.get("total_net_r"),
        "ready_families": portfolio.get("families_ready_for_paper_design"),
        "correlation_status": portfolio.get("correlation_status"),
        "lifecycle_decision": lifecycle.get("decision"),
        "lifecycle_families": [
            {
                "family": item.get("family"),
                "state": item.get("state"),
                "progress_pct": item.get("progress_pct"),
            }
            for item in lifecycle_families
            if isinstance(item, dict)
        ],
        "can_trade": False,
    }


def latest_range_edge_nested_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "RANGE_EDGE_NESTED_HOLDOUT_2026-06-23.json"
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path)}
    families = payload.get("families") if isinstance(payload.get("families"), list) else []
    return {
        "exists": True,
        "path": str(path),
        "decision": payload.get("decision"),
        "split_ts": payload.get("data", {}).get("split_ts"),
        "families": {
            str(item.get("family")): {
                "decision": item.get("decision"),
                "strategy_id": item.get("selected_on_train", {}).get("strategy_id"),
                "oos_trades": item.get("oos", {}).get("summary", {}).get("trades"),
                "oos_winrate_pct": item.get("oos", {}).get("summary", {}).get("winrate_pct"),
                "oos_expectancy_r": item.get("oos", {}).get("summary", {}).get("expectancy_r"),
            }
            for item in families
            if isinstance(item, dict) and item.get("family")
        },
        "can_trade": False,
    }


def latest_basis_funding_carry_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "BASIS_FUNDING_CARRY_NESTED_HOLDOUT_2026-06-23.json"
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path), "can_trade": False}
    selected = payload.get("selected_on_train") if isinstance(payload.get("selected_on_train"), dict) else {}
    train = selected.get("train", {}).get("summary", {}) if isinstance(selected, dict) else {}
    validation = payload.get("validation", {}).get("summary", {}) if isinstance(payload.get("validation"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "decision": payload.get("decision"),
        "strategy_id": selected.get("strategy_id"),
        "train_trades": train.get("trades"),
        "train_positive_pct": train.get("positive_pct"),
        "train_mean_net_bps": train.get("mean_net_bps"),
        "validation_trades": validation.get("trades"),
        "validation_mean_net_bps": validation.get("mean_net_bps"),
        "oos_opened": isinstance(payload.get("oos"), dict),
        "can_trade": False,
    }


def latest_liquidation_impulse_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "LIQUIDATION_IMPULSE_CONTINUATION_NESTED_HOLDOUT_2026-06-23.json"
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path), "can_trade": False}
    selected = payload.get("selected_on_train") if isinstance(payload.get("selected_on_train"), dict) else {}
    train = selected.get("train", {}).get("summary", {}) if isinstance(selected, dict) else {}
    validation = payload.get("validation", {}).get("summary", {}) if isinstance(payload.get("validation"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "decision": payload.get("decision"),
        "strategy_id": selected.get("strategy_id"),
        "train_trades": train.get("trades"),
        "train_expectancy_r": train.get("expectancy_r"),
        "validation_trades": validation.get("trades"),
        "validation_expectancy_r": validation.get("expectancy_r"),
        "validation_stress_expectancy_r": payload.get("validation", {}).get("cost_stress", {}).get("summary", {}).get("expectancy_r"),
        "oos_opened": isinstance(payload.get("oos"), dict),
        "can_trade": False,
    }


def latest_session_opening_range_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "SESSION_OPENING_RANGE_NESTED_HOLDOUT_2026-06-23.json"
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path), "can_trade": False}
    top = (
        payload.get("top_train_results_regardless_of_gate", [{}])[0]
        if isinstance(payload.get("top_train_results_regardless_of_gate"), list)
        and payload.get("top_train_results_regardless_of_gate")
        and isinstance(payload.get("top_train_results_regardless_of_gate", [{}])[0], dict)
        else {}
    )
    summary = top.get("train", {}).get("summary", {}) if isinstance(top.get("train"), dict) else {}
    stress = top.get("train", {}).get("cost_stress", {}).get("summary", {}) if isinstance(top.get("train"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "decision": payload.get("decision"),
        "tested": payload.get("search", {}).get("configs_tested"),
        "train_qualified": payload.get("search", {}).get("train_qualified"),
        "best_strategy_id": top.get("config", {}).get("strategy_id") if isinstance(top.get("config"), dict) else None,
        "best_trades": summary.get("trades"),
        "best_winrate_pct": summary.get("winrate_pct"),
        "best_expectancy_r": summary.get("expectancy_r"),
        "best_stress_expectancy_r": stress.get("expectancy_r"),
        "best_max_drawdown_r": summary.get("max_drawdown_r"),
        "validation_opened": isinstance(payload.get("validation"), dict),
        "oos_opened": payload.get("oos_opened") is True,
        "can_trade": False,
    }


def latest_basis_shock_reversion_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "BASIS_SHOCK_REVERSION_NESTED_HOLDOUT_2026-06-23.json"
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path), "can_trade": False}
    top = (
        payload.get("top_train_results_regardless_of_gate", [{}])[0]
        if isinstance(payload.get("top_train_results_regardless_of_gate"), list)
        and payload.get("top_train_results_regardless_of_gate")
        and isinstance(payload.get("top_train_results_regardless_of_gate", [{}])[0], dict)
        else {}
    )
    train = top.get("train", {}) if isinstance(top.get("train"), dict) else {}
    summary = train.get("summary", {}) if isinstance(train.get("summary"), dict) else {}
    stress = train.get("cost_stress", {}).get("summary", {}) if isinstance(train.get("cost_stress"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "decision": payload.get("decision"),
        "tested": payload.get("search", {}).get("tested"),
        "train_qualified": payload.get("search", {}).get("train_qualified"),
        "best_strategy_id": top.get("strategy_id"),
        "best_trades": summary.get("trades"),
        "best_positive_pct": summary.get("positive_pct"),
        "best_mean_net_bps": summary.get("mean_net_bps"),
        "best_stress_mean_net_bps": stress.get("mean_net_bps"),
        "best_max_drawdown_bps": summary.get("max_drawdown_bps"),
        "best_positive_folds": train.get("positive_folds"),
        "validation_opened": isinstance(payload.get("validation"), dict),
        "oos_opened": payload.get("oos_opened") is True,
        "can_trade": False,
    }


def latest_funding_settlement_reversion_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "FUNDING_SETTLEMENT_REVERSION_NESTED_HOLDOUT_2026-06-24.json"
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path), "can_trade": False}
    top = (
        payload.get("top_train_results_regardless_of_gate", [{}])[0]
        if isinstance(payload.get("top_train_results_regardless_of_gate"), list)
        and payload.get("top_train_results_regardless_of_gate")
        and isinstance(payload.get("top_train_results_regardless_of_gate", [{}])[0], dict)
        else {}
    )
    train = top.get("train", {}) if isinstance(top.get("train"), dict) else {}
    summary = train.get("summary", {}) if isinstance(train.get("summary"), dict) else {}
    stress = train.get("cost_stress", {}).get("summary", {}) if isinstance(train.get("cost_stress"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "decision": payload.get("decision"),
        "tested": payload.get("search", {}).get("tested"),
        "train_qualified": payload.get("search", {}).get("train_qualified"),
        "best_strategy_id": top.get("strategy_id"),
        "best_trades": summary.get("trades"),
        "best_winrate_pct": summary.get("winrate_pct"),
        "best_expectancy_r": summary.get("expectancy_r"),
        "best_stress_expectancy_r": stress.get("expectancy_r"),
        "best_max_drawdown_r": summary.get("max_drawdown_r"),
        "best_stable_folds": train.get("stable_folds"),
        "validation_opened": isinstance(payload.get("validation"), dict),
        "oos_opened": payload.get("oos_opened") is True,
        "can_trade": False,
    }


def latest_spot_led_continuation_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "SPOT_LED_CONTINUATION_NESTED_HOLDOUT_2026-06-24.json"
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path), "can_trade": False}
    top = (
        payload.get("top_train_results_regardless_of_gate", [{}])[0]
        if isinstance(payload.get("top_train_results_regardless_of_gate"), list)
        and payload.get("top_train_results_regardless_of_gate")
        and isinstance(payload.get("top_train_results_regardless_of_gate", [{}])[0], dict)
        else {}
    )
    train = top.get("train", {}) if isinstance(top.get("train"), dict) else {}
    summary = train.get("summary", {}) if isinstance(train.get("summary"), dict) else {}
    stress = train.get("cost_stress", {}).get("summary", {}) if isinstance(train.get("cost_stress"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "decision": payload.get("decision"),
        "tested": payload.get("search", {}).get("tested"),
        "train_qualified": payload.get("search", {}).get("train_qualified"),
        "best_strategy_id": top.get("strategy_id"),
        "best_trades": summary.get("trades"),
        "best_winrate_pct": summary.get("winrate_pct"),
        "best_expectancy_r": summary.get("expectancy_r"),
        "best_stress_expectancy_r": stress.get("expectancy_r"),
        "best_max_drawdown_r": summary.get("max_drawdown_r"),
        "best_stable_folds": train.get("stable_folds"),
        "validation_opened": isinstance(payload.get("validation"), dict),
        "oos_opened": payload.get("oos_opened") is True,
        "can_trade": False,
    }


def latest_cross_venue_spot_data_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "CROSS_VENUE_SPOT_DATA_QUALITY_2026-06-24.json"
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path), "can_trade": False}
    venues = payload.get("venues") if isinstance(payload.get("venues"), dict) else {}
    alignment = payload.get("alignment") if isinstance(payload.get("alignment"), dict) else {}
    archive = payload.get("archive") if isinstance(payload.get("archive"), dict) else {}
    window = payload.get("window") if isinstance(payload.get("window"), dict) else {}
    outputs = payload.get("outputs") if isinstance(payload.get("outputs"), dict) else {}
    manifest_path = Path(str(outputs.get("collection_manifest") or ""))
    if not manifest_path.is_absolute():
        manifest_path = ROOT / manifest_path
    manifest = read_json(manifest_path) if manifest_path.is_file() else None
    return {
        "exists": True,
        "path": str(path),
        "classification": payload.get("classification"),
        "interval": window.get("interval"),
        "start": window.get("start"),
        "end_exclusive": window.get("end_exclusive"),
        "requested_bars": window.get("requested_bars"),
        "binance_rows": venues.get("binance", {}).get("rows"),
        "coinbase_rows": venues.get("coinbase", {}).get("rows"),
        "pull_aligned_rows": alignment.get("rows"),
        "archive_aligned_rows": archive.get("aligned_rows", alignment.get("rows")),
        "aligned_rows": archive.get("aligned_rows", alignment.get("rows")),
        "archive_first": archive.get("first", window.get("start")),
        "archive_last": archive.get("last"),
        "retention_hours": archive.get("retention_hours"),
        "overlap_coverage_pct": alignment.get("coverage_pct"),
        "return_correlation": alignment.get("return_correlation"),
        "p95_abs_close_spread_bps": alignment.get("p95_abs_close_spread_bps"),
        "level_spread_comparison_allowed": alignment.get("level_spread_comparison_allowed"),
        "spread_interpretation": alignment.get("spread_interpretation"),
        "manifest_verified_present": isinstance(manifest, dict) and len(manifest.get("files", [])) == 3,
        "manifest_files": len(manifest.get("files", [])) if isinstance(manifest, dict) else 0,
        "can_trade": False,
    }


def latest_cross_venue_microstructure_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_DATA_QUALITY_2026-06-24.json"
    payload = read_json(path)
    loop_path = ROOT / "logs" / "cross_venue_microstructure" / "microstructure_loop_status.json"
    watchdog_path = ROOT / "logs" / "cross_venue_microstructure" / "microstructure_watchdog_loop_status.json"
    health_path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_HEALTH_2026-06-24.json"
    storage_guard_path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_STORAGE_GUARD_2026-06-25.json"
    notify_path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_HEALTH_TELEGRAM_2026-06-24.json"
    seal_path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json"
    seal_notify_path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_TELEGRAM_2026-06-25.json"
    loop = read_json(loop_path)
    watchdog = read_json(watchdog_path)
    health = read_json(health_path)
    storage_guard = read_json(storage_guard_path)
    notify = read_json(notify_path)
    seal = read_json(seal_path)
    seal_notify = read_json(seal_notify_path)
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path), "loop": loop, "can_trade": False}
    archive = payload.get("archive") if isinstance(payload.get("archive"), dict) else {}
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    readiness = payload.get("research_readiness") if isinstance(payload.get("research_readiness"), dict) else {}
    integrity = payload.get("trade_id_integrity") if isinstance(payload.get("trade_id_integrity"), dict) else {}
    backfill = payload.get("gap_backfill") if isinstance(payload.get("gap_backfill"), dict) else {}
    outputs = payload.get("outputs") if isinstance(payload.get("outputs"), dict) else {}
    watchdog_extra = watchdog.get("extra") if isinstance(watchdog, dict) and isinstance(watchdog.get("extra"), dict) else {}
    manifest_path = Path(str(outputs.get("collection_manifest") or ""))
    if not manifest_path.is_absolute():
        manifest_path = ROOT / manifest_path
    manifest = read_json(manifest_path) if manifest_path.is_file() else None
    return {
        "exists": True,
        "path": str(path),
        "classification": payload.get("classification"),
        "storage_engine": payload.get("storage", {}).get("engine"),
        "generated_at": payload.get("generated_at"),
        "archive_trades": archive.get("trades"),
        "binance_trades": archive.get("binance_trades"),
        "coinbase_trades": archive.get("coinbase_trades"),
        "book_snapshots": archive.get("book_snapshots"),
        "minute_feature_rows": archive.get("minute_feature_rows"),
        "span_hours": coverage.get("span_hours"),
        "dual_trade_coverage_pct": coverage.get("both_trade_coverage_pct"),
        "dual_book_coverage_pct": coverage.get("both_book_coverage_pct"),
        "research_ready": readiness.get("ready"),
        "minimum_hours": readiness.get("minimum_hours"),
        "binance_missing_ids": integrity.get("binance", {}).get("missing_ids"),
        "coinbase_missing_ids": integrity.get("coinbase", {}).get("missing_ids"),
        "backfill_rows_recovered": backfill.get("rows_recovered"),
        "backfill_pages_used": backfill.get("pages_used"),
        "backfill_budget_exhausted": backfill.get("page_budget_exhausted"),
        "loop_status": loop.get("status") if isinstance(loop, dict) else None,
        "loop_pid": loop.get("pid") if isinstance(loop, dict) else None,
        "loop_sleep_seconds": loop.get("sleep_seconds") if isinstance(loop, dict) else None,
        "watchdog_status": watchdog.get("status") if isinstance(watchdog, dict) else None,
        "watchdog_pid": watchdog.get("pid") if isinstance(watchdog, dict) else None,
        "watchdog_last_storage_exit_code": watchdog_extra.get("last_storage_exit_code"),
        "watchdog_last_seal_notify_exit_code": watchdog_extra.get("last_seal_notify_exit_code"),
        "health_classification": health.get("classification") if isinstance(health, dict) else None,
        "health_failed_gates": health.get("failed_hard_gates") if isinstance(health, dict) else None,
        "storage_guard_classification": storage_guard.get("classification") if isinstance(storage_guard, dict) else None,
        "storage_guard_failed_hard_gates": storage_guard.get("failed_hard_gates") if isinstance(storage_guard, dict) else None,
        "storage_guard_failed_warn_gates": storage_guard.get("failed_warn_gates") if isinstance(storage_guard, dict) else None,
        "storage_free_bytes": storage_guard.get("observed", {}).get("disk_free_bytes") if isinstance(storage_guard, dict) else None,
        "storage_free_pct": storage_guard.get("observed", {}).get("disk_free_pct") if isinstance(storage_guard, dict) else None,
        "storage_authoritative_bytes": storage_guard.get("observed", {}).get("authoritative_bytes") if isinstance(storage_guard, dict) else None,
        "storage_estimated_target_bytes": storage_guard.get("observed", {}).get("estimated_target_bytes") if isinstance(storage_guard, dict) else None,
        "notify_decision": notify.get("decision") if isinstance(notify, dict) else None,
        "notify_kind": notify.get("kind") if isinstance(notify, dict) else None,
        "seal_decision": seal.get("decision") if isinstance(seal, dict) else None,
        "seal_notify_decision": seal_notify.get("decision") if isinstance(seal_notify, dict) else None,
        "seal_notify_kind": seal_notify.get("kind") if isinstance(seal_notify, dict) else None,
        "seal_notify_telegram_response_ok": seal_notify.get("telegram_response_ok") if isinstance(seal_notify, dict) else None,
        "seal_checks_passed": seal.get("summary", {}).get("passed") if isinstance(seal, dict) else None,
        "seal_checks_total": seal.get("summary", {}).get("total") if isinstance(seal, dict) else None,
        "sealed_snapshot_id": seal.get("snapshot_id") if isinstance(seal, dict) else None,
        "seal_primary_blocker": seal.get("readiness_diagnostics", {}).get("primary_blocker") if isinstance(seal, dict) else None,
        "seal_remaining_hours": seal.get("readiness_diagnostics", {}).get("remaining_hours") if isinstance(seal, dict) else None,
        "seal_earliest_time_gate_at_utc": seal.get("readiness_diagnostics", {}).get("estimated_earliest_time_gate_at_utc") if isinstance(seal, dict) else None,
        "seal_failed_checks": seal.get("readiness_diagnostics", {}).get("failed_checks") if isinstance(seal, dict) else None,
        "manifest_verified_present": isinstance(manifest, dict) and len(manifest.get("files", [])) == 3,
        "can_trade": False,
    }


def latest_microstructure_collector_sla_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_COLLECTOR_SLA_GUARD_2026-06-25.json"
    notify_path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_COLLECTOR_SLA_TELEGRAM_2026-06-25.json"
    drill_path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_COLLECTOR_SLA_TELEGRAM_DRILL_2026-06-25.json"
    replay_path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_COLLECTOR_SLA_REPLAY_2026-06-25.json"
    payload = read_json(path)
    notify = read_json(notify_path)
    drill = read_json(drill_path)
    replay = read_json(replay_path)
    watchdog_path = ROOT / "logs" / "cross_venue_microstructure" / "microstructure_watchdog_loop_status.json"
    watchdog = read_json(watchdog_path)
    watchdog_extra = watchdog.get("extra") if isinstance(watchdog, dict) and isinstance(watchdog.get("extra"), dict) else {}
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path), "can_trade": False}
    return {
        "exists": True,
        "path": str(path),
        "decision": payload.get("decision"),
        "classification": payload.get("classification"),
        "data_generated_at": payload.get("data_generated_at"),
        "report_age_minutes": payload.get("report_age_minutes"),
        "new_rows": payload.get("new_rows"),
        "inserted_trades": payload.get("inserted_trades"),
        "inserted_books": payload.get("inserted_books"),
        "archive_trades": payload.get("archive_trades"),
        "archive_books": payload.get("archive_books"),
        "archive_features": payload.get("archive_features"),
        "archive_trades_delta": payload.get("archive_trades_delta"),
        "archive_books_delta": payload.get("archive_books_delta"),
        "archive_features_delta": payload.get("archive_features_delta"),
        "feature_retention_drop_rows": payload.get("feature_retention_drop_rows"),
        "feature_retention_drop_allowance_rows": payload.get("feature_retention_drop_allowance_rows"),
        "feature_retention_drop_bounded": payload.get("feature_retention_drop_bounded"),
        "span_hours": payload.get("span_hours"),
        "trade_coverage_pct": payload.get("trade_coverage_pct"),
        "book_coverage_pct": payload.get("book_coverage_pct"),
        "recent_6h_book_coverage_pct": payload.get("recent_6h_book_coverage_pct"),
        "recent_24h_book_coverage_pct": payload.get("recent_24h_book_coverage_pct"),
        "legacy_gap_recent_coverage_verified": payload.get("legacy_gap_recent_coverage_verified"),
        "readiness_blockers": payload.get("readiness_blockers"),
        "retention_hours": payload.get("retention_hours"),
        "trade_coverage_delta_pct": payload.get("trade_coverage_delta_pct"),
        "book_coverage_delta_pct": payload.get("book_coverage_delta_pct"),
        "binance_missing_ids": payload.get("binance_missing_ids"),
        "coinbase_missing_ids": payload.get("coinbase_missing_ids"),
        "failed_checks": payload.get("failed_checks"),
        "next_action": payload.get("next_action"),
        "notify_decision": notify.get("decision") if isinstance(notify, dict) else None,
        "notify_kind": notify.get("kind") if isinstance(notify, dict) else None,
        "notify_telegram_response_ok": notify.get("telegram_response_ok") if isinstance(notify, dict) else None,
        "drill_decision": drill.get("decision") if isinstance(drill, dict) else None,
        "drill_steps_passed": drill.get("steps_passed") if isinstance(drill, dict) else None,
        "drill_steps_total": drill.get("steps_total") if isinstance(drill, dict) else None,
        "drill_degraded_first_decision": drill.get("degraded_first_decision") if isinstance(drill, dict) else None,
        "drill_degraded_same_decision": drill.get("degraded_same_decision") if isinstance(drill, dict) else None,
        "drill_degraded_changed_decision": drill.get("degraded_changed_decision") if isinstance(drill, dict) else None,
        "drill_recovery_decision": drill.get("recovery_decision") if isinstance(drill, dict) else None,
        "replay_decision": replay.get("decision") if isinstance(replay, dict) else None,
        "replay_observations": replay.get("observations") if isinstance(replay, dict) else None,
        "replay_incident_count": replay.get("incident_count") if isinstance(replay, dict) else None,
        "replay_open_incident": replay.get("open_incident") if isinstance(replay, dict) else None,
        "replay_state_transitions": replay.get("state_transitions") if isinstance(replay, dict) else None,
        "replay_degraded_observations": replay.get("degraded_observations") if isinstance(replay, dict) else None,
        "replay_raw_degraded_observations": replay.get("raw_degraded_observations") if isinstance(replay, dict) else None,
        "replay_superseded_degraded_observations": replay.get("superseded_degraded_observations") if isinstance(replay, dict) else None,
        "replay_stability_blocker": replay.get("stability_blocker") if isinstance(replay, dict) else None,
        "replay_latest_degraded_generated_at": replay.get("latest_degraded_generated_at") if isinstance(replay, dict) else None,
        "replay_stability_cooldown_until_utc": replay.get("stability_cooldown_until_utc") if isinstance(replay, dict) else None,
        "replay_stability_cooldown_remaining_minutes": replay.get("stability_cooldown_remaining_minutes") if isinstance(replay, dict) else None,
        "replay_min_trade_coverage_pct": replay.get("min_trade_coverage_pct") if isinstance(replay, dict) else None,
        "replay_min_book_coverage_pct": replay.get("min_book_coverage_pct") if isinstance(replay, dict) else None,
        "replay_avg_inserted_trades": replay.get("avg_inserted_trades") if isinstance(replay, dict) else None,
        "replay_avg_inserted_books": replay.get("avg_inserted_books") if isinstance(replay, dict) else None,
        "replay_next_action": replay.get("next_action") if isinstance(replay, dict) else None,
        "watchdog_last_collector_sla_exit_code": watchdog_extra.get("last_collector_sla_exit_code"),
        "watchdog_last_collector_sla_notify_exit_code": watchdog_extra.get("last_collector_sla_notify_exit_code"),
        "watchdog_last_collector_sla_replay_exit_code": watchdog_extra.get("last_collector_sla_replay_exit_code"),
        "can_trade": False,
    }


def latest_active_source_integrity_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "ACTIVE_SOURCE_INTEGRITY_GUARD.json"
    lock_path = ROOT / "configs" / "ACTIVE_SOURCE_INTEGRITY_LOCK.json"
    watchdog_path = ROOT / "logs" / "cross_venue_microstructure" / "microstructure_watchdog_loop_status.json"
    payload = read_json(path)
    watchdog = read_json(watchdog_path)
    watchdog_extra = watchdog.get("extra") if isinstance(watchdog, dict) and isinstance(watchdog.get("extra"), dict) else {}
    if not isinstance(payload, dict):
        return {
            "exists": False,
            "path": str(path),
            "lock_exists": lock_path.is_file(),
            "watchdog_last_exit_code": watchdog_extra.get("last_source_integrity_exit_code"),
            "can_trade": False,
        }
    return {
        "exists": True,
        "path": str(path),
        "lock_path": str(lock_path),
        "lock_exists": lock_path.is_file(),
        "decision": payload.get("decision"),
        "lock_review_id": payload.get("lock_review_id"),
        "lock_sealed_at": payload.get("lock_sealed_at"),
        "expected_files": payload.get("expected_files"),
        "current_files": payload.get("current_files"),
        "drift_count": payload.get("drift_count"),
        "missing": payload.get("missing"),
        "changed": payload.get("changed"),
        "untracked": payload.get("untracked"),
        "next_action": payload.get("next_action"),
        "watchdog_last_exit_code": watchdog_extra.get("last_source_integrity_exit_code"),
        "research_runner_blocked_by_source_integrity": watchdog_extra.get("research_runner_blocked_by_source_integrity"),
        "can_trade": False,
    }


def latest_cross_venue_catchup_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "CROSS_VENUE_CATCHUP_NESTED_HOLDOUT_2026-06-24.json"
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path), "can_trade": False}
    top = (
        payload.get("top_train_results_regardless_of_gate", [{}])[0]
        if isinstance(payload.get("top_train_results_regardless_of_gate"), list)
        and payload.get("top_train_results_regardless_of_gate")
        and isinstance(payload.get("top_train_results_regardless_of_gate", [{}])[0], dict)
        else {}
    )
    train = top.get("train", {}) if isinstance(top.get("train"), dict) else {}
    summary = train.get("summary", {}) if isinstance(train.get("summary"), dict) else {}
    stress = train.get("cost_stress", {}).get("summary", {}) if isinstance(train.get("cost_stress"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "decision": payload.get("decision"),
        "snapshot_id": payload.get("data", {}).get("snapshot", {}).get("snapshot_id"),
        "tested": payload.get("search", {}).get("tested"),
        "train_qualified": payload.get("search", {}).get("train_qualified"),
        "best_strategy_id": top.get("strategy_id"),
        "best_signals": train.get("signals"),
        "best_trades": summary.get("trades"),
        "best_winrate_pct": summary.get("winrate_pct"),
        "best_mean_net_bps": summary.get("mean_net_bps"),
        "best_stress_mean_net_bps": stress.get("mean_net_bps"),
        "best_stable_folds": train.get("stable_folds"),
        "validation_opened": isinstance(payload.get("validation"), dict),
        "oos_opened": payload.get("oos_opened") is True,
        "can_trade": False,
    }


def latest_cross_venue_rebound_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "CROSS_VENUE_NEGATIVE_REBOUND_TRAIN_2026-06-24.json"
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path), "can_trade": False}
    rows = payload.get("top_train_results_regardless_of_gate")
    top = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
    train = top.get("train", {}) if isinstance(top.get("train"), dict) else {}
    summary = train.get("summary", {}) if isinstance(train.get("summary"), dict) else {}
    stress_block = train.get("cost_stress", {}) if isinstance(train.get("cost_stress"), dict) else {}
    stress = stress_block.get("summary", {}) if isinstance(stress_block.get("summary"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "decision": payload.get("decision"),
        "snapshot_id": payload.get("data", {}).get("snapshot", {}).get("snapshot_id"),
        "tested": payload.get("search", {}).get("tested"),
        "train_qualified": payload.get("search", {}).get("train_qualified"),
        "best_strategy_id": top.get("strategy_id"),
        "best_signals": train.get("signals"),
        "best_trades": summary.get("trades"),
        "best_winrate_pct": summary.get("winrate_pct"),
        "best_mean_net_bps": summary.get("mean_net_bps"),
        "best_stress_mean_net_bps": stress.get("mean_net_bps"),
        "best_stable_folds": train.get("stable_folds"),
        "validation_opened": payload.get("validation_opened") is True,
        "oos_opened": payload.get("oos_opened") is True,
        "can_trade": False,
    }


def latest_microstructure_registry_summary() -> dict[str, Any]:
    registry_path = ROOT / "configs" / "CROSS_VENUE_HYPOTHESIS_REGISTRY.json"
    audit_path = ROOT / "docs" / "CROSS_VENUE_HYPOTHESIS_REGISTRY_AUDIT_2026-06-24.json"
    registry = read_json(registry_path)
    audit = read_json(audit_path)
    if not isinstance(registry, dict):
        return {"exists": False, "path": str(registry_path), "can_trade": False}
    hypotheses = registry.get("hypotheses") if isinstance(registry.get("hypotheses"), list) else []
    budget = registry.get("portfolio_budget") if isinstance(registry.get("portfolio_budget"), dict) else {}
    return {
        "exists": True,
        "path": str(registry_path),
        "registry_id": registry.get("registry_id"),
        "audit_decision": audit.get("decision") if isinstance(audit, dict) else None,
        "registered": len(hypotheses),
        "rejected": sum(str(item.get("status", "")).startswith("rejected") for item in hypotheses if isinstance(item, dict)),
        "pending": sum(item.get("status") == "registered_pending" for item in hypotheses if isinstance(item, dict)),
        "configurations_used": budget.get("used_configurations"),
        "configurations_max": budget.get("max_total_configurations"),
        "oos_used": budget.get("used_oos_openings"),
        "oos_max": budget.get("max_oos_openings"),
        "can_trade": False,
    }


def latest_microstructure_prereg_queue_summary() -> dict[str, Any]:
    queue_path = ROOT / "configs" / "CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE.json"
    audit_path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE_AUDIT_2026-06-25.json"
    queue = read_json(queue_path)
    audit = read_json(audit_path)
    if not isinstance(queue, dict):
        return {"exists": False, "path": str(queue_path), "can_trade": False}
    hypotheses = queue.get("hypotheses") if isinstance(queue.get("hypotheses"), list) else []
    budget = queue.get("portfolio_budget") if isinstance(queue.get("portfolio_budget"), dict) else {}
    summary = audit.get("summary") if isinstance(audit, dict) and isinstance(audit.get("summary"), dict) else {}
    return {
        "exists": True,
        "path": str(queue_path),
        "queue_id": queue.get("queue_id"),
        "decision": audit.get("decision") if isinstance(audit, dict) else None,
        "execution_state": audit.get("execution_state") if isinstance(audit, dict) else None,
        "seal_decision": audit.get("seal_decision") if isinstance(audit, dict) else None,
        "latest_snapshot_id": audit.get("latest_snapshot_id") if isinstance(audit, dict) else None,
        "registered": summary.get("registered", len(hypotheses)),
        "pending_first_seal": summary.get("pending_first_seal", sum(item.get("status") == "registered_pending_first_seal" for item in hypotheses if isinstance(item, dict))),
        "configurations_used": summary.get("configurations_used", budget.get("used_configurations")),
        "configurations_max": summary.get("configurations_max", budget.get("max_total_configurations")),
        "oos_used": summary.get("oos_openings_used", budget.get("used_oos_openings")),
        "oos_max": summary.get("oos_openings_max", budget.get("max_oos_openings")),
        "can_trade": False,
    }


def latest_microstructure_runner_contract_summary() -> dict[str, Any]:
    contract_path = ROOT / "configs" / "CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT.json"
    audit_path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT_AUDIT_2026-06-25.json"
    contract = read_json(contract_path)
    audit = read_json(audit_path)
    if not isinstance(contract, dict):
        return {"exists": False, "path": str(contract_path), "can_trade": False}
    experiments = contract.get("experiments") if isinstance(contract.get("experiments"), dict) else {}
    summary = audit.get("summary") if isinstance(audit, dict) and isinstance(audit.get("summary"), dict) else {}
    return {
        "exists": True,
        "path": str(contract_path),
        "contract_id": contract.get("contract_id"),
        "decision": audit.get("decision") if isinstance(audit, dict) else None,
        "execution_state": audit.get("execution_state") if isinstance(audit, dict) else None,
        "seal_decision": audit.get("seal_decision") if isinstance(audit, dict) else None,
        "latest_snapshot_id": audit.get("latest_snapshot_id") if isinstance(audit, dict) else None,
        "experiments": summary.get("experiments", len(experiments)),
        "planned_not_implemented": summary.get("planned_not_implemented", sum(item.get("implementation_status") == "planned_not_implemented" for item in experiments.values() if isinstance(item, dict))),
        "implemented_locked": summary.get("implemented_locked", sum(item.get("implementation_status") == "implemented_locked" for item in experiments.values() if isinstance(item, dict))),
        "scripts_existing": summary.get("scripts_existing"),
        "runner_execution_allowed_now": contract.get("runtime_boundary", {}).get("runner_execution_allowed_now") if isinstance(contract.get("runtime_boundary"), dict) else None,
        "can_trade": False,
    }


def latest_microstructure_research_runner_summary() -> dict[str, Any]:
    report_path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_RESEARCH_RUNNER_2026-06-25.json"
    notify_path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_RESEARCH_RUNNER_TELEGRAM_2026-06-25.json"
    latest_path = ROOT / "_dl" / "research_runs_cross_venue_microstructure" / "LATEST.json"
    report = read_json(report_path)
    notify = read_json(notify_path)
    latest = read_json(latest_path)
    if not isinstance(report, dict):
        return {"exists": False, "path": str(report_path), "can_trade": False}
    return {
        "exists": True,
        "path": str(report_path),
        "decision": report.get("decision"),
        "gate_decision": report.get("gate_decision"),
        "snapshot_id": report.get("snapshot_id"),
        "run_id": report.get("run_id") or (latest.get("run_id") if isinstance(latest, dict) else None),
        "status": latest.get("status") if isinstance(latest, dict) else None,
        "experiments": report.get("experiments"),
        "completed": report.get("completed") or (latest.get("completed") if isinstance(latest, dict) else None),
        "failed": report.get("failed") or (latest.get("failed") if isinstance(latest, dict) else None),
        "candidate_count": report.get("candidate_count") or (latest.get("candidate_count") if isinstance(latest, dict) else None),
        "tested_total": report.get("tested_total") or (latest.get("tested_total") if isinstance(latest, dict) else None),
        "train_qualified_total": report.get("train_qualified_total") or (latest.get("train_qualified_total") if isinstance(latest, dict) else None),
        "notify_decision": notify.get("decision") if isinstance(notify, dict) else None,
        "notify_kind": notify.get("kind") if isinstance(notify, dict) else None,
        "telegram_response_ok": notify.get("telegram_response_ok") if isinstance(notify, dict) else None,
        "can_trade": False,
    }


def latest_microstructure_readiness_progress_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_READINESS_PROGRESS_2026-06-25.json"
    payload = read_json(path)
    watchdog_path = ROOT / "logs" / "cross_venue_microstructure" / "microstructure_watchdog_loop_status.json"
    watchdog = read_json(watchdog_path)
    watchdog_extra = watchdog.get("extra") if isinstance(watchdog, dict) and isinstance(watchdog.get("extra"), dict) else {}
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path), "can_trade": False}
    return {
        "exists": True,
        "path": str(path),
        "decision": payload.get("decision"),
        "gate_decision": payload.get("gate_decision"),
        "health_classification": payload.get("health_classification"),
        "data_generated_at": payload.get("data_generated_at"),
        "previous_data_generated_at": payload.get("previous_data_generated_at"),
        "elapsed_minutes_since_previous_data_report": payload.get("elapsed_minutes_since_previous_data_report"),
        "span_hours": payload.get("span_hours"),
        "previous_span_hours": payload.get("previous_span_hours"),
        "span_delta_hours": payload.get("span_delta_hours"),
        "required_hours": payload.get("required_hours"),
        "remaining_hours": payload.get("remaining_hours"),
        "remaining_delta_hours": payload.get("remaining_delta_hours"),
        "earliest_time_gate_at_utc": payload.get("earliest_time_gate_at_utc"),
        "book_coverage_eta_utc": payload.get("book_coverage_eta_utc"),
        "trade_coverage_pct": payload.get("trade_coverage_pct"),
        "book_coverage_pct": payload.get("book_coverage_pct"),
        "recent_6h_book_coverage_pct": payload.get("recent_6h_book_coverage_pct"),
        "recent_24h_book_coverage_pct": payload.get("recent_24h_book_coverage_pct"),
        "legacy_gap_rollout_verified": payload.get("legacy_gap_rollout_verified"),
        "trade_coverage_delta_pct": payload.get("trade_coverage_delta_pct"),
        "book_coverage_delta_pct": payload.get("book_coverage_delta_pct"),
        "binance_missing_ids": payload.get("binance_missing_ids"),
        "coinbase_missing_ids": payload.get("coinbase_missing_ids"),
        "failed_checks": payload.get("failed_checks"),
        "next_action": payload.get("next_action"),
        "watchdog_last_readiness_progress_exit_code": watchdog_extra.get("last_readiness_progress_exit_code"),
        "can_trade": False,
    }


def latest_microstructure_snapshot_transition_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_TRANSITION_MONITOR_2026-06-25.json"
    notify_path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_TRANSITION_TELEGRAM_2026-06-25.json"
    drill_path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_TRANSITION_TELEGRAM_DRILL_2026-06-25.json"
    payload = read_json(path)
    notify = read_json(notify_path)
    drill = read_json(drill_path)
    watchdog_path = ROOT / "logs" / "cross_venue_microstructure" / "microstructure_watchdog_loop_status.json"
    watchdog = read_json(watchdog_path)
    watchdog_extra = watchdog.get("extra") if isinstance(watchdog, dict) and isinstance(watchdog.get("extra"), dict) else {}
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path), "can_trade": False}
    runtime = payload.get("runtime_boundary") if isinstance(payload.get("runtime_boundary"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "transition_state": payload.get("transition_state"),
        "previous_transition_state": payload.get("previous_transition_state"),
        "transition_changed": payload.get("transition_changed"),
        "gate_decision": payload.get("gate_decision"),
        "runner_decision": payload.get("runner_decision"),
        "snapshot_id": payload.get("snapshot_id"),
        "runner_snapshot_id": payload.get("runner_snapshot_id"),
        "primary_blocker": payload.get("primary_blocker"),
        "remaining_hours": payload.get("remaining_hours"),
        "earliest_time_gate_at_utc": payload.get("earliest_time_gate_at_utc"),
        "book_coverage_eta_utc": payload.get("book_coverage_eta_utc"),
        "failed_checks": payload.get("failed_checks"),
        "checks_passed": payload.get("checks_passed"),
        "checks_total": payload.get("checks_total"),
        "trade_coverage_pct": payload.get("trade_coverage_pct"),
        "book_coverage_pct": payload.get("book_coverage_pct"),
        "recent_6h_book_coverage_pct": payload.get("recent_6h_book_coverage_pct"),
        "recent_24h_book_coverage_pct": payload.get("recent_24h_book_coverage_pct"),
        "legacy_gap_rollout_verified": payload.get("legacy_gap_rollout_verified"),
        "binance_missing_ids": payload.get("binance_missing_ids"),
        "coinbase_missing_ids": payload.get("coinbase_missing_ids"),
        "research_runner_can_attempt_now": payload.get("research_runner_can_attempt_now"),
        "runs_research_batch": runtime.get("runs_research_batch"),
        "notify_decision": notify.get("decision") if isinstance(notify, dict) else None,
        "notify_kind": notify.get("kind") if isinstance(notify, dict) else None,
        "notify_telegram_response_ok": notify.get("telegram_response_ok") if isinstance(notify, dict) else None,
        "drill_decision": drill.get("decision") if isinstance(drill, dict) else None,
        "drill_steps_passed": drill.get("steps_passed") if isinstance(drill, dict) else None,
        "drill_steps_total": drill.get("steps_total") if isinstance(drill, dict) else None,
        "drill_ready_duplicate_decision": drill.get("ready_duplicate_decision") if isinstance(drill, dict) else None,
        "next_action": payload.get("next_action"),
        "watchdog_last_snapshot_transition_exit_code": watchdog_extra.get("last_snapshot_transition_exit_code"),
        "watchdog_last_snapshot_transition_notify_exit_code": watchdog_extra.get("last_snapshot_transition_notify_exit_code"),
        "can_trade": False,
    }


def latest_microstructure_candidate_governance_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_CANDIDATE_GOVERNANCE_2026-06-25.json"
    payload = read_json(path)
    watchdog_path = ROOT / "logs" / "cross_venue_microstructure" / "microstructure_watchdog_loop_status.json"
    watchdog = read_json(watchdog_path)
    watchdog_extra = watchdog.get("extra") if isinstance(watchdog, dict) and isinstance(watchdog.get("extra"), dict) else {}
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path), "can_trade": False}
    return {
        "exists": True,
        "path": str(path),
        "decision": payload.get("decision"),
        "runner_decision": payload.get("runner_decision"),
        "snapshot_id": payload.get("snapshot_id"),
        "run_id": payload.get("run_id"),
        "candidate_count": payload.get("candidate_count"),
        "failed_checks": payload.get("failed_checks"),
        "next_action": payload.get("next_action"),
        "observer_registration_allowed": payload.get("promotion_boundary", {}).get("observer_registration_allowed") if isinstance(payload.get("promotion_boundary"), dict) else None,
        "paper_execution_allowed": payload.get("promotion_boundary", {}).get("paper_execution_allowed") if isinstance(payload.get("promotion_boundary"), dict) else None,
        "live_execution_allowed": payload.get("promotion_boundary", {}).get("live_execution_allowed") if isinstance(payload.get("promotion_boundary"), dict) else None,
        "watchdog_last_candidate_governance_exit_code": watchdog_extra.get("last_candidate_governance_exit_code"),
        "can_trade": False,
    }


def latest_microstructure_candidate_review_pack_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_CANDIDATE_REVIEW_PACK_2026-06-25.json"
    payload = read_json(path)
    watchdog_path = ROOT / "logs" / "cross_venue_microstructure" / "microstructure_watchdog_loop_status.json"
    watchdog = read_json(watchdog_path)
    watchdog_extra = watchdog.get("extra") if isinstance(watchdog, dict) and isinstance(watchdog.get("extra"), dict) else {}
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path), "can_trade": False}
    rules = payload.get("review_rules") if isinstance(payload.get("review_rules"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "decision": payload.get("decision"),
        "governance_decision": payload.get("governance_decision"),
        "runner_decision": payload.get("runner_decision"),
        "snapshot_id": payload.get("snapshot_id"),
        "run_id": payload.get("run_id"),
        "candidate_count": payload.get("candidate_count"),
        "next_action": payload.get("next_action"),
        "manual_review_required": rules.get("manual_review_required"),
        "automatic_validation_opening_allowed": rules.get("automatic_validation_opening_allowed"),
        "paper_or_live_execution_allowed": rules.get("paper_or_live_execution_allowed"),
        "watchdog_last_candidate_review_exit_code": watchdog_extra.get("last_candidate_review_exit_code"),
        "can_trade": False,
    }


def latest_microstructure_validation_protocol_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_VALIDATION_PROTOCOL_DRAFT_2026-06-25.json"
    payload = read_json(path)
    watchdog_path = ROOT / "logs" / "cross_venue_microstructure" / "microstructure_watchdog_loop_status.json"
    watchdog = read_json(watchdog_path)
    watchdog_extra = watchdog.get("extra") if isinstance(watchdog, dict) and isinstance(watchdog.get("extra"), dict) else {}
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path), "can_trade": False}
    rules = payload.get("global_validation_rules") if isinstance(payload.get("global_validation_rules"), dict) else {}
    protocols = payload.get("protocols") if isinstance(payload.get("protocols"), list) else []
    first_protocol = protocols[0] if protocols and isinstance(protocols[0], dict) else {}
    promotion = first_protocol.get("promotion_after_validation") if isinstance(first_protocol.get("promotion_after_validation"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "decision": payload.get("decision"),
        "review_pack_decision": payload.get("review_pack_decision"),
        "governance_decision": payload.get("governance_decision"),
        "source_train_snapshot_id": payload.get("source_train_snapshot_id"),
        "source_run_id": payload.get("source_run_id"),
        "candidate_count": payload.get("candidate_count"),
        "next_action": payload.get("next_action"),
        "manual_approval_required": rules.get("manual_approval_required"),
        "validation_data_opened_by_this_builder": rules.get("validation_data_opened_by_this_builder"),
        "automatic_oos_opening_allowed": promotion.get("automatic_oos_opening_allowed"),
        "watchdog_last_validation_protocol_exit_code": watchdog_extra.get("last_validation_protocol_exit_code"),
        "can_trade": False,
    }


def latest_microstructure_validation_approval_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_VALIDATION_APPROVAL_AUDIT_2026-06-25.json"
    approval_path = ROOT / "configs" / "CROSS_VENUE_MICROSTRUCTURE_VALIDATION_APPROVAL.json"
    template_path = ROOT / "configs" / "CROSS_VENUE_MICROSTRUCTURE_VALIDATION_APPROVAL_TEMPLATE.json"
    payload = read_json(path)
    watchdog_path = ROOT / "logs" / "cross_venue_microstructure" / "microstructure_watchdog_loop_status.json"
    watchdog = read_json(watchdog_path)
    watchdog_extra = watchdog.get("extra") if isinstance(watchdog, dict) and isinstance(watchdog.get("extra"), dict) else {}
    if not isinstance(payload, dict):
        return {
            "exists": False,
            "path": str(path),
            "approval_file_present": approval_path.is_file(),
            "template_exists": template_path.is_file(),
            "can_trade": False,
        }
    checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
    runtime = payload.get("runtime_boundary") if isinstance(payload.get("runtime_boundary"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "approval_file_present": approval_path.is_file(),
        "template_exists": template_path.is_file(),
        "decision": payload.get("decision"),
        "protocol_decision": payload.get("protocol_decision"),
        "source_train_snapshot_id": payload.get("source_train_snapshot_id"),
        "approval_candidate_rank": payload.get("approval_candidate_rank"),
        "approval_strategy_id": payload.get("approval_strategy_id"),
        "approval_validation_snapshot_id": payload.get("approval_validation_snapshot_id"),
        "current_snapshot_id": payload.get("current_snapshot_id"),
        "candidate_count": payload.get("candidate_count"),
        "failed_checks": payload.get("failed_checks"),
        "next_action": payload.get("next_action"),
        "manual_approval_granted": checks.get("manual_approval_granted"),
        "validation_opening_allowed": checks.get("validation_opening_allowed"),
        "candidate_matches_protocol": checks.get("candidate_matches_protocol"),
        "approval_matches_current_snapshot": checks.get("approval_matches_current_snapshot"),
        "all_human_checks_true": checks.get("all_human_checks_true"),
        "all_execution_prohibitions_false": checks.get("all_execution_prohibitions_false"),
        "opens_validation": runtime.get("opens_validation"),
        "watchdog_last_validation_approval_exit_code": watchdog_extra.get("last_validation_approval_exit_code"),
        "can_trade": False,
    }


def latest_microstructure_validation_runner_summary() -> dict[str, Any]:
    path = ROOT / "docs" / "CROSS_VENUE_MICROSTRUCTURE_VALIDATION_RUNNER_SKELETON_2026-06-25.json"
    payload = read_json(path)
    watchdog_path = ROOT / "logs" / "cross_venue_microstructure" / "microstructure_watchdog_loop_status.json"
    watchdog = read_json(watchdog_path)
    watchdog_extra = watchdog.get("extra") if isinstance(watchdog, dict) and isinstance(watchdog.get("extra"), dict) else {}
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path), "can_trade": False}
    runtime = payload.get("runtime_boundary") if isinstance(payload.get("runtime_boundary"), dict) else {}
    checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
    return {
        "exists": True,
        "path": str(path),
        "decision": payload.get("decision"),
        "protocol_decision": payload.get("protocol_decision"),
        "source_train_snapshot_id": payload.get("source_train_snapshot_id"),
        "validation_snapshot_id": payload.get("validation_snapshot_id"),
        "candidate_count": payload.get("candidate_count"),
        "failed_checks": payload.get("failed_checks"),
        "next_action": payload.get("next_action"),
        "manual_approval_granted": checks.get("manual_approval_granted"),
        "validation_snapshot_sealed": checks.get("validation_snapshot_sealed"),
        "validation_execution_implemented": checks.get("validation_execution_implemented"),
        "opens_validation": runtime.get("opens_validation"),
        "executes_strategy_code": runtime.get("executes_strategy_code"),
        "watchdog_last_validation_runner_exit_code": watchdog_extra.get("last_validation_runner_exit_code"),
        "can_trade": False,
    }


def latest_research_data_snapshot_summary() -> dict[str, Any]:
    latest_path = ROOT / "data" / "research_snapshots" / "LATEST.json"
    latest = read_json(latest_path)
    if not isinstance(latest, dict):
        return {"exists": False, "path": str(latest_path), "can_trade": False}
    snapshot_dir = Path(str(latest.get("snapshot_dir") or ""))
    manifest = read_json(snapshot_dir / "SNAPSHOT_MANIFEST.json") if snapshot_dir.is_dir() else None
    verification = read_json(snapshot_dir / "VERIFICATION.json") if snapshot_dir.is_dir() else None
    files = manifest.get("files", []) if isinstance(manifest, dict) else []
    last_values = [str(item.get("last")) for item in files if isinstance(item, dict) and item.get("last")]
    return {
        "exists": True,
        "path": str(latest_path),
        "snapshot_id": latest.get("snapshot_id"),
        "profile": latest.get("profile"),
        "dataset_sha256": latest.get("dataset_sha256"),
        "files": latest.get("files"),
        "bytes": latest.get("bytes"),
        "latest_data_time": max(last_values) if last_values else None,
        "verification_passed": (
            latest.get("verification_passed") is True
            and isinstance(verification, dict)
            and verification.get("passed") is True
        ),
        "verified_at": verification.get("verified_at") if isinstance(verification, dict) else None,
        "can_trade": False,
    }


def latest_verified_research_run_summary() -> dict[str, Any]:
    latest_path = ROOT / "_dl" / "research_runs" / "LATEST.json"
    latest = read_json(latest_path)
    if not isinstance(latest, dict):
        return {"exists": False, "path": str(latest_path), "can_trade": False}
    run_dir = Path(str(latest.get("run_dir") or ""))
    result = read_json(run_dir / "RUN_RESULT.json") if run_dir.is_dir() else None
    request = read_json(run_dir / "RUN_REQUEST.json") if run_dir.is_dir() else None
    snapshot = request.get("snapshot", {}) if isinstance(request, dict) else {}
    provenance = request.get("provenance", {}) if isinstance(request, dict) else {}
    return {
        "exists": True,
        "path": str(latest_path),
        "run_id": latest.get("run_id"),
        "experiment": latest.get("experiment"),
        "hypothesis_id": latest.get("hypothesis_id"),
        "purpose": latest.get("purpose"),
        "snapshot_id": latest.get("snapshot_id"),
        "dataset_sha256": snapshot.get("dataset_sha256"),
        "status": latest.get("status"),
        "decision": latest.get("decision"),
        "return_code": latest.get("return_code"),
        "report_contract_passed": latest.get("report_contract_passed") is True,
        "multiplicity_status": latest.get("multiplicity_status"),
        "multiplicity_pass": latest.get("multiplicity_pass") is True,
        "eligible_for_next_stage": latest.get("eligible_for_next_stage") is True,
        "report_sha256": result.get("report_sha256") if isinstance(result, dict) else None,
        "contract_sha256": provenance.get("contract_sha256"),
        "script_sha256": provenance.get("script_sha256"),
        "shell": provenance.get("shell"),
        "arbitrary_extra_args": provenance.get("arbitrary_extra_args"),
        "finished_at": result.get("finished_at") if isinstance(result, dict) else None,
        "can_trade": False,
    }


def latest_hypothesis_registry_summary() -> dict[str, Any]:
    registry_path = ROOT / "configs" / "HYPOTHESIS_REGISTRY.json"
    audit_path = ROOT / "docs" / "HYPOTHESIS_REGISTRY_AUDIT_2026-06-24.json"
    registry = read_json(registry_path)
    audit = read_json(audit_path)
    if not isinstance(registry, dict):
        return {"exists": False, "path": str(registry_path), "can_trade": False}
    hypotheses = registry.get("hypotheses") if isinstance(registry.get("hypotheses"), list) else []
    budget = registry.get("portfolio_budget") if isinstance(registry.get("portfolio_budget"), dict) else {}
    policy = registry.get("multiple_testing_policy") if isinstance(registry.get("multiple_testing_policy"), dict) else {}
    return {
        "exists": True,
        "path": str(registry_path),
        "registry_id": registry.get("registry_id"),
        "audit_decision": audit.get("decision") if isinstance(audit, dict) else None,
        "registered": len(hypotheses),
        "rejected": sum(str(item.get("status", "")).startswith("rejected") for item in hypotheses if isinstance(item, dict)),
        "pending": sum(item.get("status") == "registered_pending" for item in hypotheses if isinstance(item, dict)),
        "configurations_used": budget.get("used_configurations"),
        "configurations_max": budget.get("max_total_configurations"),
        "oos_used": budget.get("used_oos_openings"),
        "oos_max": budget.get("max_oos_openings"),
        "correction": policy.get("method"),
        "familywise_alpha": policy.get("familywise_alpha"),
        "can_trade": False,
    }


def timestamp_age_seconds(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
        return round(max(0.0, age.total_seconds()), 3)
    except ValueError:
        return None


def latest_research_runtime_supervisor_summary(
    supervisor_dir: Path | None = None,
    launcher_status_path: Path | None = None,
) -> dict[str, Any]:
    root = supervisor_dir or RESEARCH_RUNTIME_SUPERVISOR_DIR
    report_path = root / "runtime" / "LATEST.json"
    loop_status_path = root / "runtime" / "loop_status.json"
    launcher_path = launcher_status_path or (ROOT / "logs" / "research_runtime_supervisor_autostart_status.json")
    report = read_json(report_path)
    loop_status = read_json(loop_status_path)
    launcher_status = read_json(launcher_path)
    if not isinstance(report, dict):
        return {
            "exists": False,
            "path": str(report_path),
            "decision": "research_runtime_supervisor_report_missing",
            "healthy": False,
            "can_trade": False,
        }

    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    failed_checks = report.get("failed_checks") if isinstance(report.get("failed_checks"), list) else []
    loop = loop_status if isinstance(loop_status, dict) else {}
    launcher = launcher_status if isinstance(launcher_status, dict) else {}
    pid = loop.get("pid")
    report_age_seconds = timestamp_age_seconds(report.get("generated_at"))
    loop_age_seconds = timestamp_age_seconds(loop.get("updated_at"))
    registered = int(summary.get("registered_components") or 0)
    healthy_components = int(summary.get("healthy_components") or 0)
    loop_pid_alive = process_alive(pid)
    healthy = bool(
        report.get("decision") == "research_runtime_registry_healthy"
        and report.get("can_trade") is False
        and registered > 0
        and healthy_components == registered
        and not failed_checks
        and loop.get("status") in {"running_once", "sleeping"}
        and loop.get("can_trade") is False
        and loop_pid_alive
        and launcher.get("status") in {"already_running", "started"}
        and launcher.get("automatic_restart_allowed") is False
        and launcher.get("can_trade") is False
        and report_age_seconds is not None
        and report_age_seconds <= 900
        and loop_age_seconds is not None
        and loop_age_seconds <= 900
    )
    return {
        "exists": True,
        "path": str(report_path),
        "decision": report.get("decision"),
        "healthy": healthy,
        "registered_components": registered,
        "healthy_components": healthy_components,
        "registered_pids": summary.get("registered_pids"),
        "unique_pids": summary.get("unique_pids"),
        "signature_groups": summary.get("signature_groups"),
        "retired_versions": summary.get("retired_versions"),
        "failed_checks": failed_checks,
        "report_age_seconds": report_age_seconds,
        "loop_status": loop.get("status"),
        "loop_pid": pid,
        "loop_pid_alive": loop_pid_alive,
        "loop_age_seconds": loop_age_seconds,
        "launcher_status": launcher.get("status"),
        "startup_launch_only": launcher.get("startup_launch_only"),
        "automatic_restart_allowed": False,
        "orders_allowed": False,
        "can_trade": False,
    }


def package_status() -> dict[str, Any]:
    manifest = read_json(ROOT / "MANIFEST.json")
    latest_futures = read_json(FUTURES_DIR / "data" / "live" / "status" / "latest.json")
    strategy_mix_forward_feed = latest_strategy_mix_forward_feed_summary()
    return {
        "as_of": now_iso(),
        "root": str(ROOT),
        "python": sys.version.split()[0],
        "safe_mode": True,
        "live_trading_locked": True,
        "live_guard": "Reviewed source hashes are locked; drift blocks post-seal research. No task contains --send, private stream, wallet signing, approvals, or arbitrary shell input.",
        "manifest": {
            "built": manifest.get("built") if isinstance(manifest, dict) else None,
            "total_files": manifest.get("total_files") if isinstance(manifest, dict) else None,
            "total_bytes": manifest.get("total_bytes") if isinstance(manifest, dict) else None,
        },
        "components": {
            "autostart": latest_autostart_summary(),
            "bounded_smoke": smoke_summary(),
            "futures_latest": latest_futures,
            "dex_paper": dex_journal_summary(),
            "research_runtime_supervisor": latest_research_runtime_supervisor_summary(),
            "max_pipeline": {
                "status": "repo-local-lite-runtime",
                "engine": "MAX_CORE_LITE",
                "full_historical_core": "not_found",
                "latest_data_cache": latest_data_cache_summary(),
                "safe_probe_task": "max_core_lite_composite",
                "latest_backtest": latest_backtest_summary(),
                "latest_event_export": latest_event_export_summary(),
                "latest_feature_miner": latest_feature_miner_summary(),
                "latest_research_grid": latest_research_grid_summary(),
                "latest_hardening": latest_hardening_summary(),
                "latest_v11_candidate": latest_v11_candidate_summary(),
                "latest_v12_regime": latest_v12_regime_summary(),
                "latest_v13_candidate": latest_v13_candidate_summary(),
                "latest_v14_expansion": latest_v14_expansion_summary(),
                "latest_v15_state_filters": latest_v15_state_summary(),
                "latest_v16_event_first": latest_v16_event_first_summary(),
                "latest_v17_short_continuation": latest_v17_short_continuation_summary(),
                "market_state_alerts": latest_market_state_alerts_summary(),
                "forward_evidence": latest_forward_evidence_summary(),
                "oi_funding_data_quality": latest_oi_funding_data_quality_summary(),
                "forward_runtime_health": latest_forward_runtime_health_summary(),
                "strategy_mix_forward_feed": strategy_mix_forward_feed,
                "edge_same_shape_shadow_observer": latest_edge_same_shape_shadow_summary(),
                "edge_same_shape_shadow_scoreboard": latest_edge_same_shape_shadow_scoreboard_summary(),
                "edge_compression_guard": latest_edge_compression_guard_summary(),
                "crypto_guides_web_ingest": latest_crypto_guides_web_ingest_summary(),
                "crowd_positioning": latest_crowd_positioning_summary(),
                "active_strategy_runtime": latest_active_strategy_runtime_summary(),
                "four_family_portfolio": latest_four_family_portfolio_summary(),
                "range_edge_nested_holdout": latest_range_edge_nested_summary(),
                "basis_funding_carry": latest_basis_funding_carry_summary(),
                "liquidation_impulse": latest_liquidation_impulse_summary(),
                "session_opening_range": latest_session_opening_range_summary(),
                "basis_shock_reversion": latest_basis_shock_reversion_summary(),
                "funding_settlement_reversion": latest_funding_settlement_reversion_summary(),
                "spot_led_continuation": latest_spot_led_continuation_summary(),
                "cross_venue_spot_data": latest_cross_venue_spot_data_summary(),
                "cross_venue_microstructure": latest_cross_venue_microstructure_summary(),
                "source_integrity": latest_active_source_integrity_summary(),
                "microstructure_collector_sla": latest_microstructure_collector_sla_summary(),
                "cross_venue_catchup": latest_cross_venue_catchup_summary(),
                "cross_venue_rebound": latest_cross_venue_rebound_summary(),
                "microstructure_registry": latest_microstructure_registry_summary(),
                "microstructure_prereg_queue": latest_microstructure_prereg_queue_summary(),
                "microstructure_runner_contract": latest_microstructure_runner_contract_summary(),
                "microstructure_readiness_progress": latest_microstructure_readiness_progress_summary(),
                "microstructure_snapshot_transition": latest_microstructure_snapshot_transition_summary(),
                "microstructure_research_runner": latest_microstructure_research_runner_summary(),
                "microstructure_candidate_governance": latest_microstructure_candidate_governance_summary(),
                "microstructure_candidate_review_pack": latest_microstructure_candidate_review_pack_summary(),
                "microstructure_validation_protocol": latest_microstructure_validation_protocol_summary(),
                "microstructure_validation_approval": latest_microstructure_validation_approval_summary(),
                "microstructure_validation_runner": latest_microstructure_validation_runner_summary(),
                "research_data_snapshot": latest_research_data_snapshot_summary(),
                "verified_research_run": latest_verified_research_run_summary(),
                "hypothesis_registry": latest_hypothesis_registry_summary(),
            },
            "delist_ews": {
                "status": "compile-check-only",
                "runnable_scan": False,
            },
        },
        "forward": strategy_mix_forward_feed,
        "tasks": [
            {
                "id": task_id,
                "title": task.title,
                "description": task.description,
                "network_note": task.network_note,
                "expected_exit_codes": list(task.expected_exit_codes),
            }
            for task_id, task in TASKS.items()
        ],
        "recent_jobs": load_recent_jobs(),
    }


INDEX_HTML = r"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trading OS Control Panel</title>
  <style>
    :root {
      --ink: #17201b;
      --muted: #607066;
      --paper: #f5efe3;
      --panel: #fffaf0;
      --line: #dccfb8;
      --green: #177245;
      --red: #a33a31;
      --amber: #9a6b16;
      --blue: #1e5a7a;
      --shadow: 0 22px 80px rgba(42, 35, 20, .16);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at 12% 6%, rgba(23, 114, 69, .22), transparent 28rem),
        radial-gradient(circle at 90% 0%, rgba(154, 107, 22, .20), transparent 26rem),
        linear-gradient(135deg, #ede1cc 0%, #f8f0df 42%, #e4eadf 100%);
      font: 15px/1.45 "Segoe UI", "Aptos", sans-serif;
      min-height: 100vh;
    }
    header {
      padding: 32px clamp(18px, 4vw, 56px) 18px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 20px;
      align-items: end;
    }
    h1 {
      margin: 0;
      font-size: clamp(32px, 5vw, 62px);
      line-height: .95;
      letter-spacing: -.055em;
      max-width: 860px;
    }
    .subtitle { color: var(--muted); margin-top: 14px; max-width: 760px; font-size: 17px; }
    .badge {
      display: inline-flex;
      gap: 8px;
      align-items: center;
      padding: 10px 14px;
      border: 1px solid rgba(23, 114, 69, .22);
      background: rgba(255,255,255,.42);
      border-radius: 999px;
      font-weight: 700;
      color: var(--green);
      backdrop-filter: blur(6px);
      white-space: nowrap;
    }
    main {
      padding: 14px clamp(18px, 4vw, 56px) 44px;
      display: grid;
      gap: 18px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 18px;
    }
    .card {
      background: rgba(255, 250, 240, .86);
      border: 1px solid rgba(116, 88, 45, .18);
      border-radius: 28px;
      box-shadow: var(--shadow);
      padding: 22px;
      overflow: hidden;
    }
    .span-3 { grid-column: span 3; }
    .span-4 { grid-column: span 4; }
    .span-5 { grid-column: span 5; }
    .span-7 { grid-column: span 7; }
    .span-12 { grid-column: span 12; }
    .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .12em; font-weight: 800; }
    .big { font-size: 34px; font-weight: 900; letter-spacing: -.03em; margin-top: 8px; }
    .small { color: var(--muted); margin-top: 8px; }
    .ok { color: var(--green); }
    .bad { color: var(--red); }
    .warn { color: var(--amber); }
    .info { color: var(--blue); }
    .tasks {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 12px;
      margin-top: 14px;
    }
    button {
      width: 100%;
      border: 0;
      border-radius: 18px;
      padding: 16px;
      text-align: left;
      background: #1d2a22;
      color: #fff7e8;
      cursor: pointer;
      min-height: 122px;
      box-shadow: 0 12px 28px rgba(23, 32, 27, .18);
      transition: transform .14s ease, box-shadow .14s ease;
    }
    button:hover { transform: translateY(-2px); box-shadow: 0 18px 34px rgba(23, 32, 27, .24); }
    button:disabled { opacity: .55; cursor: wait; transform: none; }
    button strong { display: block; font-size: 17px; margin-bottom: 8px; }
    button span { color: #dbcdb6; }
    .job-list { display: grid; gap: 10px; max-height: 520px; overflow: auto; padding-right: 4px; }
    .job {
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 14px;
      background: rgba(255,255,255,.42);
    }
    .job-head { display: flex; justify-content: space-between; gap: 12px; align-items: start; }
    .status-pill {
      border-radius: 999px;
      padding: 4px 9px;
      font-weight: 800;
      font-size: 12px;
      background: #eee1cb;
      color: var(--muted);
      white-space: nowrap;
    }
    .status-success { background: rgba(23, 114, 69, .14); color: var(--green); }
    .status-failed, .status-timeout { background: rgba(163, 58, 49, .14); color: var(--red); }
    .status-running, .status-queued { background: rgba(30, 90, 122, .14); color: var(--blue); }
    .status-expected_fail { background: rgba(154, 107, 22, .14); color: var(--amber); }
    pre {
      background: #17201b;
      color: #e9dec9;
      padding: 14px;
      border-radius: 16px;
      overflow: auto;
      max-height: 340px;
      font-size: 12px;
      white-space: pre-wrap;
    }
    a { color: var(--blue); font-weight: 800; }
    @media (max-width: 920px) {
      header { grid-template-columns: 1fr; }
      .span-3, .span-4, .span-5, .span-7 { grid-column: span 12; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Trading OS Control Panel</h1>
      <div class="subtitle">Локальный пульт для безопасных проверок: статус пакета, smoke-команды, futures proof, DEX paper и журнал запусков. Live-trading намеренно заблокирован.</div>
    </div>
    <div class="badge">SAFE MODE · local allowlist only</div>
  </header>
  <main>
    <section class="grid">
      <article class="card span-3">
        <div class="label">Package</div>
        <div id="files" class="big">...</div>
        <div id="built" class="small">manifest loading</div>
      </article>
      <article class="card span-3">
        <div class="label">Smoke</div>
        <div id="smoke" class="big">...</div>
        <div class="small">MAX CSV preflight + utilities</div>
      </article>
      <article class="card span-3">
        <div class="label">Futures latest</div>
        <div id="futures" class="big">...</div>
        <div id="futuresSub" class="small">status loading</div>
      </article>
      <article class="card span-3">
        <div class="label">DEX paper</div>
        <div id="dex" class="big">...</div>
        <div id="dexSub" class="small">journal loading</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Isolated Research Runtime Supervisor</div>
        <div id="researchRuntimeSupervisor" class="big">...</div>
        <div id="researchRuntimeSupervisorSub" class="small">research process registry loading</div>
        <div class="small">read-only audit; duplicate, stale or hash-mismatched processes fail closed; no restart, signals or orders</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Market-State Alerts</div>
        <div id="alerts" class="big">...</div>
        <div id="alertsSub" class="small">alert observability loading</div>
        <div id="alertsPolicy" class="small">observability only; no trade permission</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Forward Paper Feed</div>
        <div id="forwardFeed" class="big">...</div>
        <div id="forwardFeedSub" class="small">forward feed loading</div>
        <div id="forwardFeedPolicy" class="small">public data only; no private credentials; no orders</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Research Data Authority</div>
        <div id="researchDataSnapshot" class="big">...</div>
        <div id="researchDataSnapshotSub" class="small">snapshot registry loading</div>
        <div class="small">immutable local data input; SHA-256 verified; no credentials or trade permission</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Cross-Venue BTC Spot Data</div>
        <div id="crossVenueData" class="big">...</div>
        <div id="crossVenueDataSub" class="small">Binance/Coinbase collection status loading</div>
        <div class="small">returns-only comparison for BTCUSDT vs BTC-USD; USD/USDT level spread is intentionally blocked</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Cross-Venue Microstructure Forward Data</div>
        <div id="crossVenueMicrostructure" class="big">...</div>
        <div id="crossVenueMicrostructureSub" class="small">trade and top-of-book collection loading</div>
        <div class="small">public forward data only; Coinbase reported side is not treated as aggressor side; no hypothesis or signals</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Microstructure Collector SLA</div>
        <div id="microstructureCollectorSla" class="big">...</div>
        <div id="microstructureCollectorSlaSub" class="small">collector SLA guard loading</div>
        <div class="small">early degradation guard for fresh reports, trade/book inserts, coverage, archive rows and trade-id gaps</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-4">
        <div class="label">Cross-Venue Catch-up Research</div>
        <div id="crossVenueCatchup" class="big">...</div>
        <div id="crossVenueCatchupSub" class="small">minute lead-lag result loading</div>
        <div class="small">data readiness does not imply edge; validation/OOS remain conditional and closed on train failure</div>
      </article>
      <article class="card span-4">
        <div class="label">Cross-Venue Rebound Research</div>
        <div id="crossVenueRebound" class="big">...</div>
        <div id="crossVenueReboundSub" class="small">adaptive inverse result loading</div>
        <div class="small">train-only adaptive follow-up; current snapshot cannot be reused as future validation</div>
      </article>
      <article class="card span-4">
        <div class="label">Microstructure Governance</div>
        <div id="microstructureRegistry" class="big">...</div>
        <div id="microstructureRegistrySub" class="small">separate trial budget loading</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Microstructure Preregistered Queue</div>
        <div id="microstructurePreregQueue" class="big">...</div>
        <div id="microstructurePreregQueueSub" class="small">first sealed SQLite snapshot hypotheses loading</div>
        <div class="small">prospective queue only; waits for exact sealed snapshot ID; no runner, signals or orders</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Microstructure Runner Contract</div>
        <div id="microstructureRunnerContract" class="big">...</div>
        <div id="microstructureRunnerContractSub" class="small">skeleton runner contract loading</div>
        <div class="small">all scripts are research-only; runner remains blocked until exact sealed snapshot</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Microstructure Research Runner</div>
        <div id="microstructureResearchRunner" class="big">...</div>
        <div id="microstructureResearchRunnerSub" class="small">sealed snapshot batch runner loading</div>
        <div class="small">runs preregistered research scripts once per sealed snapshot; no observer registration, no paper/live trading</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Microstructure Readiness Progress</div>
        <div id="microstructureReadinessProgress" class="big">...</div>
        <div id="microstructureReadinessProgressSub" class="small">readiness progress loading</div>
        <div class="small">progress monitor only; detects stalls, coverage regressions and ETA drift before snapshot sealing</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Microstructure Snapshot Transition</div>
        <div id="microstructureSnapshotTransition" class="big">...</div>
        <div id="microstructureSnapshotTransitionSub" class="small">snapshot transition monitor loading</div>
        <div class="small">handoff monitor only; does not run research, open validation or create signals</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Microstructure Candidate Governance</div>
        <div id="microstructureCandidateGovernance" class="big">...</div>
        <div id="microstructureCandidateGovernanceSub" class="small">post-batch governance gate loading</div>
        <div class="small">candidate review gate only; never opens validation, observer, paper or live execution automatically</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Microstructure Candidate Review Pack</div>
        <div id="microstructureCandidateReviewPack" class="big">...</div>
        <div id="microstructureCandidateReviewPackSub" class="small">manual review pack loading</div>
        <div class="small">single-place candidate checklist; does not open validation/OOS or execution</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Microstructure Validation Protocol</div>
        <div id="microstructureValidationProtocol" class="big">...</div>
        <div id="microstructureValidationProtocolSub" class="small">validation protocol draft loading</div>
        <div class="small">draft only; requires manual approval and a future exact sealed validation snapshot</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Microstructure Validation Approval Audit</div>
        <div id="microstructureValidationApproval" class="big">...</div>
        <div id="microstructureValidationApprovalSub" class="small">manual approval audit loading</div>
        <div class="small">template-only by default; real approval must match candidate, train snapshot and exact validation snapshot</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Microstructure Validation Runner Skeleton</div>
        <div id="microstructureValidationRunner" class="big">...</div>
        <div id="microstructureValidationRunnerSub" class="small">validation runner skeleton loading</div>
        <div class="small">precondition audit only; does not open validation data or execute strategy code</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Verified Research Runner</div>
        <div id="verifiedResearchRun" class="big">...</div>
        <div id="verifiedResearchRunSub" class="small">latest allowlisted run loading</div>
        <div class="small">exact snapshot ID; shell=false; no arbitrary args; report must keep canTrade=false</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Hypothesis Governance</div>
        <div id="hypothesisRegistry" class="big">...</div>
        <div id="hypothesisRegistrySub" class="small">trial budget and multiplicity policy loading</div>
        <div class="small">retroactive records are labelled; discovery requires prospective preregistration and unused budget</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Basis / Funding Carry</div>
        <div id="carryResearch" class="big">...</div>
        <div id="carryResearchSub" class="small">research result loading</div>
        <div class="small">market-neutral research only; no observer or trade permission</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Liquidation Impulse</div>
        <div id="impulseResearch" class="big">...</div>
        <div id="impulseResearchSub" class="small">research result loading</div>
        <div class="small">reversal and continuation entry research; no observer or trade permission</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Session Opening Range</div>
        <div id="sessionOrbResearch" class="big">...</div>
        <div id="sessionOrbResearchSub" class="small">independent research result loading</div>
        <div class="small">train/validation/conditional OOS research only; not a runtime family</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Basis Shock Reversion</div>
        <div id="basisShockResearch" class="big">...</div>
        <div id="basisShockResearchSub" class="small">independent market-neutral research loading</div>
        <div class="small">event-driven convergence research; validation/OOS remain gated; not a runtime family</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Funding Settlement Reversion</div>
        <div id="fundingEventResearch" class="big">...</div>
        <div id="fundingEventResearchSub" class="small">prospectively registered event research loading</div>
        <div class="small">actual funding settlements; next-hour entry; costs and conditional OOS; not a runtime family</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-12">
        <div class="label">Spot-Led Continuation</div>
        <div id="spotLeadResearch" class="big">...</div>
        <div id="spotLeadResearchSub" class="small">prospectively registered spot/perpetual research loading</div>
        <div class="small">causal spot-minus-perpetual divergence; next-hour entry; insufficient samples remain rejected</div>
      </article>
    </section>

    <section class="grid">
      <article class="card span-7">
        <div class="label">Safe Actions</div>
        <div class="small">Команды фиксированы в allowlist. Нет произвольного shell, нет --send, нет private stream, нет wallet signing.</div>
        <div id="tasks" class="tasks"></div>
      </article>
      <article class="card span-5">
        <div class="label">Run Journal</div>
        <div id="jobs" class="job-list"></div>
      </article>
    </section>

    <section class="card span-12">
      <div class="label">Selected Output</div>
      <pre id="output">Выбери запуск или дождись обновления статуса.</pre>
    </section>
  </main>

  <script>
    let state = null;
    let busy = false;

    function clsFor(status) {
      return `status-pill status-${status || 'unknown'}`;
    }

    function fmt(value) {
      if (value === null || value === undefined || value === '') return 'n/a';
      return String(value);
    }

    async function api(path, options) {
      const res = await fetch(path, options);
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    }

    async function runTask(id) {
      busy = true;
      renderTasks();
      document.getElementById('output').textContent = `Запущено: ${id}\nЖду результат...`;
      try {
        await api('/api/run', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({task: id})
        });
        await refresh();
      } catch (err) {
        document.getElementById('output').textContent = `Ошибка запуска: ${err.message}`;
      } finally {
        busy = false;
        renderTasks();
      }
    }

    function renderTop() {
      const manifest = state.manifest || {};
      const autostart = state.components?.autostart || {};
      const strategyRuntime = state.components?.max_pipeline?.active_strategy_runtime || {};
      const nestedHoldout = state.components?.max_pipeline?.range_edge_nested_holdout || {};
      document.getElementById('files').textContent = fmt(manifest.total_files);
      document.getElementById('built').textContent =
        `built: ${fmt(manifest.built)} · Python ${state.python} · strategies:${fmt(strategyRuntime.strategy_family_count)} active:${fmt(strategyRuntime.active_observer_count)} rejected:${fmt(strategyRuntime.rejected_family_count)} · steps:${fmt(strategyRuntime.scheduler_executable_steps)} · autostart:${autostart.startup_folder?.exists ? 'on' : 'off'} · runtimeSnapshot:${fmt(autostart.runtime?.components_healthy)}/${fmt(autostart.runtime?.components_expected)} age:${fmt(autostart.runtime?.snapshot_age_minutes)}m stale:${fmt(autostart.runtime?.snapshot_stale)} · receiptAlive:${fmt(autostart.runtime?.receipt_identity_alive_count)}/${fmt(autostart.runtime?.receipt_components_observed)} pidDrift:${fmt(autostart.runtime?.receipt_pid_drift_count)} · bitunixV3R4:${fmt(autostart.runtime?.bitunix_wo105_v3r4?.phase)}:${fmt(autostart.runtime?.bitunix_wo105_v3r4?.terminal_forward_progress)} q:${fmt(autostart.runtime?.bitunix_wo105_v3r4?.rest_quality?.accepted_runs)}/${fmt(autostart.runtime?.bitunix_wo105_v3r4?.rest_quality?.candidate_runs)}|${fmt(autostart.runtime?.bitunix_wo105_v3r4?.ws_quality?.accepted_runs)}/${fmt(autostart.runtime?.bitunix_wo105_v3r4?.ws_quality?.candidate_runs)} · loop:${fmt(autostart.forward_loop?.status)} · crowd1h:${fmt(autostart.crowd_fade_loop?.status)} · watchdog:${fmt(autostart.watchdog_loop?.status)} · backup:${fmt(autostart.daily_backup_loop?.status)} · bookLoop:${fmt(autostart.microstructure_book_loop?.status)}:${autostart.microstructure_book_loop?.pid_alive ? 'alive' : 'dead'} · unblockLoop:${fmt(autostart.microstructure_unblock_status_loop?.status)}:${autostart.microstructure_unblock_status_loop?.pid_alive ? 'alive' : 'dead'} book:${fmt(autostart.microstructure_unblock_status_loop?.book_coverage_pct)}% recent6h:${fmt(autostart.microstructure_unblock_status_loop?.recent_6h_book_coverage_pct)}% eta:${fmt(autostart.microstructure_unblock_status_loop?.eta_utc)} · bybitPulse:${fmt(autostart.bybit_gate_pulse_loop?.status)}:${autostart.bybit_gate_pulse_loop?.pid_alive ? 'alive' : 'dead'} · realEdgePulse:${fmt(autostart.real_edge_observer_pulse_loop?.status)}:${autostart.real_edge_observer_pulse_loop?.pid_alive ? 'alive' : 'dead'} · crossStackRep:${fmt(autostart.cross_stack_replication_transition_loop?.status)}:${autostart.cross_stack_replication_transition_loop?.pid_alive ? 'alive' : 'dead'} · last4h:${fmt(autostart.forward_last_run?.status)}`;

      document.getElementById('built').textContent += ` - nested:${fmt(nestedHoldout.decision)}`;

      const preflightOk = state.components?.bounded_smoke?.preflight_all_ok;
      document.getElementById('smoke').innerHTML = preflightOk ? '<span class="ok">PASS</span>' : '<span class="warn">CHECK</span>';

      const fut = state.components?.futures_latest || {};
      document.getElementById('futures').innerHTML = fut.last_mark_price ? '<span class="ok">READY</span>' : '<span class="warn">NO DATA</span>';
      document.getElementById('futuresSub').textContent = `messages: ${fmt(fut.market_messages)} · last mark: ${fmt(fut.last_mark_price)}`;

      const dex = state.components?.dex_paper || {};
      document.getElementById('dex').innerHTML = dex.exists ? '<span class="ok">PAPER</span>' : '<span class="warn">EMPTY</span>';
      document.getElementById('dexSub').textContent = `events: ${fmt(dex.events)} · last: ${fmt(dex.last_event?.action || dex.last_event?.status)}`;
      const researchSupervisor = state.components?.research_runtime_supervisor || {};
      document.getElementById('researchRuntimeSupervisor').innerHTML = researchSupervisor.healthy
        ? `<span class="ok">${fmt(researchSupervisor.healthy_components)}/${fmt(researchSupervisor.registered_components)} HEALTHY</span>`
        : researchSupervisor.exists
          ? '<span class="warn">DEGRADED</span>'
          : '<span class="warn">MISSING</span>';
      document.getElementById('researchRuntimeSupervisorSub').textContent =
        `decision:${fmt(researchSupervisor.decision)} components:${fmt(researchSupervisor.healthy_components)}/${fmt(researchSupervisor.registered_components)} pids:${fmt(researchSupervisor.unique_pids)}/${fmt(researchSupervisor.registered_pids)} signatures:${fmt(researchSupervisor.signature_groups)} retired:${fmt(researchSupervisor.retired_versions)} startup:${fmt(researchSupervisor.launcher_status)} startupOnly:${fmt(researchSupervisor.startup_launch_only)} autoRestart:${fmt(researchSupervisor.automatic_restart_allowed)} loop:${fmt(researchSupervisor.loop_status)} pid:${fmt(researchSupervisor.loop_pid)} alive:${fmt(researchSupervisor.loop_pid_alive)} reportAge:${fmt(researchSupervisor.report_age_seconds)}s loopAge:${fmt(researchSupervisor.loop_age_seconds)}s failed:${fmt((researchSupervisor.failed_checks || []).join('+'))} - canTrade:false`;
      const alerts = state.components?.max_pipeline?.market_state_alerts || {};
      const evidence = state.components?.max_pipeline?.forward_evidence || {};
      const oiDataQuality = state.components?.max_pipeline?.oi_funding_data_quality || {};
      const runtimeHealth = state.components?.max_pipeline?.forward_runtime_health || {};
      const crowdPositioning = state.components?.max_pipeline?.crowd_positioning || {};
      const familyPortfolio = state.components?.max_pipeline?.four_family_portfolio || {};
      const carryResearch = state.components?.max_pipeline?.basis_funding_carry || {};
      const impulseResearch = state.components?.max_pipeline?.liquidation_impulse || {};
      const sessionOrbResearch = state.components?.max_pipeline?.session_opening_range || {};
      const basisShockResearch = state.components?.max_pipeline?.basis_shock_reversion || {};
      const fundingEventResearch = state.components?.max_pipeline?.funding_settlement_reversion || {};
      const spotLeadResearch = state.components?.max_pipeline?.spot_led_continuation || {};
      const crossVenueData = state.components?.max_pipeline?.cross_venue_spot_data || {};
      const crossVenueMicrostructure = state.components?.max_pipeline?.cross_venue_microstructure || {};
      const microstructureCollectorSla = state.components?.max_pipeline?.microstructure_collector_sla || {};
      const crossVenueCatchup = state.components?.max_pipeline?.cross_venue_catchup || {};
      const crossVenueRebound = state.components?.max_pipeline?.cross_venue_rebound || {};
      const microstructureRegistry = state.components?.max_pipeline?.microstructure_registry || {};
      const microstructurePreregQueue = state.components?.max_pipeline?.microstructure_prereg_queue || {};
      const microstructureRunnerContract = state.components?.max_pipeline?.microstructure_runner_contract || {};
      const microstructureReadinessProgress = state.components?.max_pipeline?.microstructure_readiness_progress || {};
      const microstructureSnapshotTransition = state.components?.max_pipeline?.microstructure_snapshot_transition || {};
      const microstructureResearchRunner = state.components?.max_pipeline?.microstructure_research_runner || {};
      const microstructureCandidateGovernance = state.components?.max_pipeline?.microstructure_candidate_governance || {};
      const microstructureCandidateReviewPack = state.components?.max_pipeline?.microstructure_candidate_review_pack || {};
      const microstructureValidationProtocol = state.components?.max_pipeline?.microstructure_validation_protocol || {};
      const microstructureValidationApproval = state.components?.max_pipeline?.microstructure_validation_approval || {};
      const microstructureValidationRunner = state.components?.max_pipeline?.microstructure_validation_runner || {};
      const researchDataSnapshot = state.components?.max_pipeline?.research_data_snapshot || {};
      const verifiedResearchRun = state.components?.max_pipeline?.verified_research_run || {};
      const hypothesisRegistry = state.components?.max_pipeline?.hypothesis_registry || {};
      const latestV19 = alerts.latest_v19 || {};
      const tracker = latestV19.tracker || {};
      const log = latestV19.log || {};
      const activeCount = Number(alerts.active_count || 0);
      document.getElementById('alerts').innerHTML = activeCount > 0
        ? `<span class="warn">${activeCount} ACTIVE</span>`
        : '<span class="ok">QUIET</span>';
      document.getElementById('alertsSub').textContent =
        `log: ${fmt(log.total_events || alerts.log_events)} - tracker pending/resolved: ${fmt(tracker.pending)}/${fmt(tracker.resolved)}`;
      document.getElementById('alertsPolicy').textContent =
        `entry: ${fmt(alerts.entry_permission || latestV19.policy?.entry_permission)} - v1.9: ${latestV19.exists ? 'ready' : 'not run yet'} - v2.0: ${fmt(evidence.classification || 'not run yet')}`;

      document.getElementById('carryResearch').innerHTML = carryResearch.exists
        ? '<span class="warn">DORMANT</span>'
        : '<span class="warn">NOT RUN</span>';
      document.getElementById('carryResearchSub').textContent =
        `${fmt(carryResearch.decision)} - train:${fmt(carryResearch.train_trades)} mean:${fmt(carryResearch.train_mean_net_bps)}bps positive:${fmt(carryResearch.train_positive_pct)}% - validation:${fmt(carryResearch.validation_trades)} - OOS opened:${fmt(carryResearch.oos_opened)} - canTrade:false`;
      document.getElementById('impulseResearch').innerHTML = impulseResearch.exists
        ? '<span class="warn">REJECTED</span>'
        : '<span class="warn">NOT RUN</span>';
      document.getElementById('impulseResearchSub').textContent =
        `${fmt(impulseResearch.decision)} - train:${fmt(impulseResearch.train_trades)} exp:${fmt(impulseResearch.train_expectancy_r)}R - validation:${fmt(impulseResearch.validation_trades)} exp:${fmt(impulseResearch.validation_expectancy_r)}R stress:${fmt(impulseResearch.validation_stress_expectancy_r)}R - OOS opened:${fmt(impulseResearch.oos_opened)} - canTrade:false`;
      document.getElementById('sessionOrbResearch').innerHTML = sessionOrbResearch.exists
        ? '<span class="warn">REJECTED</span>'
        : '<span class="warn">NOT RUN</span>';
      document.getElementById('sessionOrbResearchSub').textContent =
        `${fmt(sessionOrbResearch.decision)} - tested:${fmt(sessionOrbResearch.tested)} qualified:${fmt(sessionOrbResearch.train_qualified)} - best:${fmt(sessionOrbResearch.best_strategy_id)} trades:${fmt(sessionOrbResearch.best_trades)} win:${fmt(sessionOrbResearch.best_winrate_pct)}% exp:${fmt(sessionOrbResearch.best_expectancy_r)}R stress:${fmt(sessionOrbResearch.best_stress_expectancy_r)}R DD:${fmt(sessionOrbResearch.best_max_drawdown_r)}R - validation:${fmt(sessionOrbResearch.validation_opened)} OOS:${fmt(sessionOrbResearch.oos_opened)} - canTrade:false`;
      document.getElementById('basisShockResearch').innerHTML = basisShockResearch.exists
        ? '<span class="warn">REJECTED</span>'
        : '<span class="warn">NOT RUN</span>';
      document.getElementById('basisShockResearchSub').textContent =
        `${fmt(basisShockResearch.decision)} - tested:${fmt(basisShockResearch.tested)} qualified:${fmt(basisShockResearch.train_qualified)} - best:${fmt(basisShockResearch.best_strategy_id)} trades:${fmt(basisShockResearch.best_trades)} positive:${fmt(basisShockResearch.best_positive_pct)}% mean:${fmt(basisShockResearch.best_mean_net_bps)}bps stress:${fmt(basisShockResearch.best_stress_mean_net_bps)}bps folds:${fmt(basisShockResearch.best_positive_folds)} DD:${fmt(basisShockResearch.best_max_drawdown_bps)}bps - validation:${fmt(basisShockResearch.validation_opened)} OOS:${fmt(basisShockResearch.oos_opened)} - canTrade:false`;
      document.getElementById('fundingEventResearch').innerHTML = fundingEventResearch.exists
        ? '<span class="warn">REJECTED</span>'
        : '<span class="warn">NOT RUN</span>';
      document.getElementById('fundingEventResearchSub').textContent =
        `${fmt(fundingEventResearch.decision)} - tested:${fmt(fundingEventResearch.tested)} qualified:${fmt(fundingEventResearch.train_qualified)} - best:${fmt(fundingEventResearch.best_strategy_id)} trades:${fmt(fundingEventResearch.best_trades)} win:${fmt(fundingEventResearch.best_winrate_pct)}% exp:${fmt(fundingEventResearch.best_expectancy_r)}R stress:${fmt(fundingEventResearch.best_stress_expectancy_r)}R folds:${fmt(fundingEventResearch.best_stable_folds)} DD:${fmt(fundingEventResearch.best_max_drawdown_r)}R - validation:${fmt(fundingEventResearch.validation_opened)} OOS:${fmt(fundingEventResearch.oos_opened)} - canTrade:false`;
      document.getElementById('spotLeadResearch').innerHTML = spotLeadResearch.exists
        ? '<span class="warn">REJECTED</span>'
        : '<span class="warn">NOT RUN</span>';
      document.getElementById('spotLeadResearchSub').textContent =
        `${fmt(spotLeadResearch.decision)} - tested:${fmt(spotLeadResearch.tested)} qualified:${fmt(spotLeadResearch.train_qualified)} - best:${fmt(spotLeadResearch.best_strategy_id)} trades:${fmt(spotLeadResearch.best_trades)} win:${fmt(spotLeadResearch.best_winrate_pct)}% exp:${fmt(spotLeadResearch.best_expectancy_r)}R stress:${fmt(spotLeadResearch.best_stress_expectancy_r)}R folds:${fmt(spotLeadResearch.best_stable_folds)} DD:${fmt(spotLeadResearch.best_max_drawdown_r)}R - validation:${fmt(spotLeadResearch.validation_opened)} OOS:${fmt(spotLeadResearch.oos_opened)} - canTrade:false`;
      document.getElementById('crossVenueData').innerHTML = crossVenueData.classification === 'cross_venue_collection_ready'
        ? '<span class="ok">READY</span>'
        : '<span class="warn">BLOCKED</span>';
      document.getElementById('crossVenueDataSub').textContent =
        `interval:${fmt(crossVenueData.interval)} pull:${fmt(crossVenueData.start)}..${fmt(crossVenueData.end_exclusive)} rows B/C/A:${fmt(crossVenueData.binance_rows)}/${fmt(crossVenueData.coinbase_rows)}/${fmt(crossVenueData.pull_aligned_rows)} coverage:${fmt(crossVenueData.overlap_coverage_pct)}% corr:${fmt(crossVenueData.return_correlation)} archive:${fmt(crossVenueData.archive_aligned_rows)} rows ${fmt(crossVenueData.archive_first)}..${fmt(crossVenueData.archive_last)} retention:${fmt(crossVenueData.retention_hours)}h manifest:${fmt(crossVenueData.manifest_verified_present)}/${fmt(crossVenueData.manifest_files)} levelSpreadAllowed:${fmt(crossVenueData.level_spread_comparison_allowed)} interpretation:${fmt(crossVenueData.spread_interpretation)} - canTrade:false`;
      document.getElementById('crossVenueMicrostructure').innerHTML = crossVenueMicrostructure.research_ready
        ? '<span class="ok">RESEARCH READY</span>'
        : crossVenueMicrostructure.exists
          ? '<span class="warn">COLLECTING</span>'
          : '<span class="warn">NOT RUN</span>';
      document.getElementById('crossVenueMicrostructureSub').textContent =
        `class:${fmt(crossVenueMicrostructure.classification)} storage:${fmt(crossVenueMicrostructure.storage_engine)} updated:${fmt(crossVenueMicrostructure.generated_at)} trades B/C:${fmt(crossVenueMicrostructure.binance_trades)}/${fmt(crossVenueMicrostructure.coinbase_trades)} books:${fmt(crossVenueMicrostructure.book_snapshots)} features:${fmt(crossVenueMicrostructure.minute_feature_rows)} span:${fmt(crossVenueMicrostructure.span_hours)}/${fmt(crossVenueMicrostructure.minimum_hours)}h coverage trades/books:${fmt(crossVenueMicrostructure.dual_trade_coverage_pct)}%/${fmt(crossVenueMicrostructure.dual_book_coverage_pct)}% missing IDs B/C:${fmt(crossVenueMicrostructure.binance_missing_ids)}/${fmt(crossVenueMicrostructure.coinbase_missing_ids)} backfill rows/pages:${fmt(crossVenueMicrostructure.backfill_rows_recovered)}/${fmt(crossVenueMicrostructure.backfill_pages_used)} exhausted:${fmt(crossVenueMicrostructure.backfill_budget_exhausted)} manifest:${fmt(crossVenueMicrostructure.manifest_verified_present)} loop:${fmt(crossVenueMicrostructure.loop_status)} pid:${fmt(crossVenueMicrostructure.loop_pid)} every:${fmt(crossVenueMicrostructure.loop_sleep_seconds)}s watchdog:${fmt(crossVenueMicrostructure.watchdog_status)} pid:${fmt(crossVenueMicrostructure.watchdog_pid)} storage:${fmt(crossVenueMicrostructure.storage_guard_classification)} free:${fmt(crossVenueMicrostructure.storage_free_bytes)}B/${fmt(crossVenueMicrostructure.storage_free_pct)}% auth:${fmt(crossVenueMicrostructure.storage_authoritative_bytes)}B est7d:${fmt(crossVenueMicrostructure.storage_estimated_target_bytes)}B storageExit:${fmt(crossVenueMicrostructure.watchdog_last_storage_exit_code)} sealNotifyExit:${fmt(crossVenueMicrostructure.watchdog_last_seal_notify_exit_code)} health:${fmt(crossVenueMicrostructure.health_classification)} failed:${fmt(crossVenueMicrostructure.health_failed_gates)} notify:${fmt(crossVenueMicrostructure.notify_decision)}/${fmt(crossVenueMicrostructure.notify_kind)} seal:${fmt(crossVenueMicrostructure.seal_decision)} ${fmt(crossVenueMicrostructure.seal_checks_passed)}/${fmt(crossVenueMicrostructure.seal_checks_total)} sealNotify:${fmt(crossVenueMicrostructure.seal_notify_decision)}/${fmt(crossVenueMicrostructure.seal_notify_kind)} tg:${fmt(crossVenueMicrostructure.seal_notify_telegram_response_ok)} blocker:${fmt(crossVenueMicrostructure.seal_primary_blocker)} rem:${fmt(crossVenueMicrostructure.seal_remaining_hours)}h eta:${fmt(crossVenueMicrostructure.seal_earliest_time_gate_at_utc)} failed:${fmt((crossVenueMicrostructure.seal_failed_checks || []).join('+'))} snapshot:${fmt(crossVenueMicrostructure.sealed_snapshot_id)} ready:${fmt(crossVenueMicrostructure.research_ready)} - canTrade:false`;
      let collectorSlaLabel = '<span class="warn">CHECK</span>';
      if (microstructureCollectorSla.decision === 'collector_sla_healthy') {
        collectorSlaLabel = '<span class="ok">HEALTHY</span>';
      } else if (microstructureCollectorSla.decision === 'collector_sla_healthy_legacy_gap_rolling_out') {
        collectorSlaLabel = '<span class="ok">HEALTHY / ROLLING</span>';
      } else if (microstructureCollectorSla.decision === 'collector_sla_baseline_recorded') {
        collectorSlaLabel = '<span class="info">BASELINE</span>';
      } else if ((microstructureCollectorSla.decision || '').startsWith('collector_sla_degraded')) {
        collectorSlaLabel = '<span class="warn">DEGRADED</span>';
      } else if (microstructureCollectorSla.exists === false) {
        collectorSlaLabel = '<span class="warn">MISSING</span>';
      }
      document.getElementById('microstructureCollectorSla').innerHTML = collectorSlaLabel;
      document.getElementById('microstructureCollectorSlaSub').textContent =
        `decision:${fmt(microstructureCollectorSla.decision)} class:${fmt(microstructureCollectorSla.classification)} data:${fmt(microstructureCollectorSla.data_generated_at)} age:${fmt(microstructureCollectorSla.report_age_minutes)}m newRows:${fmt(microstructureCollectorSla.new_rows)} inserts T/B:${fmt(microstructureCollectorSla.inserted_trades)}/${fmt(microstructureCollectorSla.inserted_books)} archive:${fmt(microstructureCollectorSla.archive_trades)}/${fmt(microstructureCollectorSla.archive_books)}/${fmt(microstructureCollectorSla.archive_features)} delta:${fmt(microstructureCollectorSla.archive_trades_delta)}/${fmt(microstructureCollectorSla.archive_books_delta)}/${fmt(microstructureCollectorSla.archive_features_delta)} retentionDropF:${fmt(microstructureCollectorSla.feature_retention_drop_rows)}/${fmt(microstructureCollectorSla.feature_retention_drop_allowance_rows)} bounded:${fmt(microstructureCollectorSla.feature_retention_drop_bounded)} retention:${fmt(microstructureCollectorSla.retention_hours)}h coverage168:${fmt(microstructureCollectorSla.trade_coverage_pct)}%/${fmt(microstructureCollectorSla.book_coverage_pct)}% recentBook6/24:${fmt(microstructureCollectorSla.recent_6h_book_coverage_pct)}%/${fmt(microstructureCollectorSla.recent_24h_book_coverage_pct)}% legacyVerified:${fmt(microstructureCollectorSla.legacy_gap_recent_coverage_verified)} readiness:${fmt((microstructureCollectorSla.readiness_blockers || []).join('+'))} covDelta:${fmt(microstructureCollectorSla.trade_coverage_delta_pct)}/${fmt(microstructureCollectorSla.book_coverage_delta_pct)} gaps:${fmt(microstructureCollectorSla.binance_missing_ids)}/${fmt(microstructureCollectorSla.coinbase_missing_ids)} failed:${fmt((microstructureCollectorSla.failed_checks || []).join('+'))} next:${fmt(microstructureCollectorSla.next_action)} notify:${fmt(microstructureCollectorSla.notify_decision)}/${fmt(microstructureCollectorSla.notify_kind)} tg:${fmt(microstructureCollectorSla.notify_telegram_response_ok)} drill:${fmt(microstructureCollectorSla.drill_decision)} ${fmt(microstructureCollectorSla.drill_steps_passed)}/${fmt(microstructureCollectorSla.drill_steps_total)} replay:${fmt(microstructureCollectorSla.replay_decision)} obs:${fmt(microstructureCollectorSla.replay_observations)} inc:${fmt(microstructureCollectorSla.replay_incident_count)} open:${fmt(microstructureCollectorSla.replay_open_incident)} trans:${fmt(microstructureCollectorSla.replay_state_transitions)} degEff/raw/sup:${fmt(microstructureCollectorSla.replay_degraded_observations)}/${fmt(microstructureCollectorSla.replay_raw_degraded_observations)}/${fmt(microstructureCollectorSla.replay_superseded_degraded_observations)} blocker:${fmt(microstructureCollectorSla.replay_stability_blocker)} latestDeg:${fmt(microstructureCollectorSla.replay_latest_degraded_generated_at)} cooldownUntil:${fmt(microstructureCollectorSla.replay_stability_cooldown_until_utc)} cooldownMin:${fmt(microstructureCollectorSla.replay_stability_cooldown_remaining_minutes)} minCov:${fmt(microstructureCollectorSla.replay_min_trade_coverage_pct)}%/${fmt(microstructureCollectorSla.replay_min_book_coverage_pct)}% avgIns:${fmt(microstructureCollectorSla.replay_avg_inserted_trades)}/${fmt(microstructureCollectorSla.replay_avg_inserted_books)} replayNext:${fmt(microstructureCollectorSla.replay_next_action)} exit:${fmt(microstructureCollectorSla.watchdog_last_collector_sla_exit_code)}/${fmt(microstructureCollectorSla.watchdog_last_collector_sla_notify_exit_code)}/${fmt(microstructureCollectorSla.watchdog_last_collector_sla_replay_exit_code)} - canTrade:false`;
      document.getElementById('crossVenueCatchup').innerHTML = crossVenueCatchup.exists
        ? '<span class="warn">REJECTED</span>'
        : '<span class="warn">NOT RUN</span>';
      document.getElementById('crossVenueCatchupSub').textContent =
        `decision:${fmt(crossVenueCatchup.decision)} snapshot:${fmt(crossVenueCatchup.snapshot_id)} tested:${fmt(crossVenueCatchup.tested)} qualified:${fmt(crossVenueCatchup.train_qualified)} best:${fmt(crossVenueCatchup.best_strategy_id)} signals/trades:${fmt(crossVenueCatchup.best_signals)}/${fmt(crossVenueCatchup.best_trades)} win:${fmt(crossVenueCatchup.best_winrate_pct)}% net:${fmt(crossVenueCatchup.best_mean_net_bps)}bps stress:${fmt(crossVenueCatchup.best_stress_mean_net_bps)}bps folds:${fmt(crossVenueCatchup.best_stable_folds)} validation:${fmt(crossVenueCatchup.validation_opened)} OOS:${fmt(crossVenueCatchup.oos_opened)} - canTrade:false`;
      document.getElementById('crossVenueRebound').innerHTML = crossVenueRebound.exists
        ? '<span class="warn">REJECTED</span>'
        : '<span class="warn">NOT RUN</span>';
      document.getElementById('crossVenueReboundSub').textContent =
        `decision:${fmt(crossVenueRebound.decision)} snapshot:${fmt(crossVenueRebound.snapshot_id)} tested:${fmt(crossVenueRebound.tested)} qualified:${fmt(crossVenueRebound.train_qualified)} best:${fmt(crossVenueRebound.best_strategy_id)} signals/trades:${fmt(crossVenueRebound.best_signals)}/${fmt(crossVenueRebound.best_trades)} win:${fmt(crossVenueRebound.best_winrate_pct)}% net:${fmt(crossVenueRebound.best_mean_net_bps)}bps stress:${fmt(crossVenueRebound.best_stress_mean_net_bps)}bps folds:${fmt(crossVenueRebound.best_stable_folds)} validation:${fmt(crossVenueRebound.validation_opened)} OOS:${fmt(crossVenueRebound.oos_opened)} - canTrade:false`;
      document.getElementById('microstructureRegistry').innerHTML = microstructureRegistry.audit_decision === 'hypothesis_registry_valid'
        ? '<span class="ok">GOVERNED</span>'
        : '<span class="warn">CHECK</span>';
      document.getElementById('microstructureRegistrySub').textContent =
        `registry:${fmt(microstructureRegistry.registry_id)} audit:${fmt(microstructureRegistry.audit_decision)} registered:${fmt(microstructureRegistry.registered)} rejected:${fmt(microstructureRegistry.rejected)} pending:${fmt(microstructureRegistry.pending)} budget:${fmt(microstructureRegistry.configurations_used)}/${fmt(microstructureRegistry.configurations_max)} OOS:${fmt(microstructureRegistry.oos_used)}/${fmt(microstructureRegistry.oos_max)} - canTrade:false`;
      document.getElementById('microstructurePreregQueue').innerHTML = microstructurePreregQueue.decision === 'microstructure_prereg_queue_valid'
        ? '<span class="ok">PREREGISTERED</span>'
        : '<span class="warn">CHECK</span>';
      document.getElementById('microstructurePreregQueueSub').textContent =
        `queue:${fmt(microstructurePreregQueue.queue_id)} decision:${fmt(microstructurePreregQueue.decision)} state:${fmt(microstructurePreregQueue.execution_state)} seal:${fmt(microstructurePreregQueue.seal_decision)} snapshot:${fmt(microstructurePreregQueue.latest_snapshot_id)} registered/pending:${fmt(microstructurePreregQueue.registered)}/${fmt(microstructurePreregQueue.pending_first_seal)} budget:${fmt(microstructurePreregQueue.configurations_used)}/${fmt(microstructurePreregQueue.configurations_max)} OOS:${fmt(microstructurePreregQueue.oos_used)}/${fmt(microstructurePreregQueue.oos_max)} - canTrade:false`;
      document.getElementById('microstructureRunnerContract').innerHTML = ['microstructure_runner_contract_valid_skeleton', 'microstructure_runner_contract_valid_locked'].includes(microstructureRunnerContract.decision)
        ? '<span class="ok">CONTRACTED</span>'
        : '<span class="warn">CHECK</span>';
      document.getElementById('microstructureRunnerContractSub').textContent =
        `contract:${fmt(microstructureRunnerContract.contract_id)} decision:${fmt(microstructureRunnerContract.decision)} state:${fmt(microstructureRunnerContract.execution_state)} seal:${fmt(microstructureRunnerContract.seal_decision)} snapshot:${fmt(microstructureRunnerContract.latest_snapshot_id)} experiments:${fmt(microstructureRunnerContract.experiments)} planned/impl:${fmt(microstructureRunnerContract.planned_not_implemented)}/${fmt(microstructureRunnerContract.implemented_locked)} scripts:${fmt(microstructureRunnerContract.scripts_existing)} runNow:${fmt(microstructureRunnerContract.runner_execution_allowed_now)} - canTrade:false`;
      let microProgressLabel = '<span class="warn">CHECK</span>';
      if (microstructureReadinessProgress.decision === 'readiness_progress_waiting_healthy') {
        microProgressLabel = '<span class="info">PROGRESS</span>';
      } else if (microstructureReadinessProgress.decision === 'readiness_progress_baseline_recorded') {
        microProgressLabel = '<span class="info">BASELINE</span>';
      } else if (microstructureReadinessProgress.decision === 'readiness_progress_snapshot_sealed') {
        microProgressLabel = '<span class="ok">SEALED</span>';
      } else if (['readiness_progress_stalled_no_span_growth', 'readiness_progress_span_regressed', 'readiness_progress_eta_regressed', 'readiness_progress_coverage_below_threshold', 'readiness_progress_coverage_regressed', 'readiness_progress_trade_id_gaps_present', 'readiness_progress_time_window_met_but_not_sealed', 'readiness_progress_health_degraded'].includes(microstructureReadinessProgress.decision)) {
        microProgressLabel = '<span class="warn">BLOCKED</span>';
      } else if (microstructureReadinessProgress.exists === false) {
        microProgressLabel = '<span class="warn">MISSING</span>';
      }
      document.getElementById('microstructureReadinessProgress').innerHTML = microProgressLabel;
      document.getElementById('microstructureReadinessProgressSub').textContent =
        `decision:${fmt(microstructureReadinessProgress.decision)} gate:${fmt(microstructureReadinessProgress.gate_decision)} health:${fmt(microstructureReadinessProgress.health_classification)} data:${fmt(microstructureReadinessProgress.data_generated_at)} prev:${fmt(microstructureReadinessProgress.previous_data_generated_at)} elapsed:${fmt(microstructureReadinessProgress.elapsed_minutes_since_previous_data_report)}m span:${fmt(microstructureReadinessProgress.span_hours)}/${fmt(microstructureReadinessProgress.required_hours)}h delta:${fmt(microstructureReadinessProgress.span_delta_hours)}h remaining:${fmt(microstructureReadinessProgress.remaining_hours)}h deltaRem:${fmt(microstructureReadinessProgress.remaining_delta_hours)}h eta:${fmt(microstructureReadinessProgress.earliest_time_gate_at_utc)} coverage:${fmt(microstructureReadinessProgress.trade_coverage_pct)}%/${fmt(microstructureReadinessProgress.book_coverage_pct)}% covDelta:${fmt(microstructureReadinessProgress.trade_coverage_delta_pct)}/${fmt(microstructureReadinessProgress.book_coverage_delta_pct)} gaps:${fmt(microstructureReadinessProgress.binance_missing_ids)}/${fmt(microstructureReadinessProgress.coinbase_missing_ids)} failed:${fmt((microstructureReadinessProgress.failed_checks || []).join('+'))} next:${fmt(microstructureReadinessProgress.next_action)} exit:${fmt(microstructureReadinessProgress.watchdog_last_readiness_progress_exit_code)} - canTrade:false`;
      let microTransitionLabel = '<span class="warn">CHECK</span>';
      if (microstructureSnapshotTransition.transition_state === 'waiting_for_minimum_time_window') {
        microTransitionLabel = '<span class="info">WAITING</span>';
      } else if (microstructureSnapshotTransition.transition_state === 'waiting_for_book_coverage_rollout') {
        microTransitionLabel = '<span class="info">ROLLING GAP</span>';
      } else if (microstructureSnapshotTransition.transition_state === 'sealed_snapshot_ready_for_train_research_batch') {
        microTransitionLabel = '<span class="ok">READY</span>';
      } else if (microstructureSnapshotTransition.transition_state === 'sealed_snapshot_research_batch_already_completed') {
        microTransitionLabel = '<span class="ok">DONE</span>';
      } else if (microstructureSnapshotTransition.transition_state === 'blocked_after_time_window') {
        microTransitionLabel = '<span class="warn">BLOCKED AFTER TIME</span>';
      } else if ((microstructureSnapshotTransition.transition_state || '').startsWith('blocked_')) {
        microTransitionLabel = '<span class="warn">BLOCKED</span>';
      } else if (microstructureSnapshotTransition.exists === false) {
        microTransitionLabel = '<span class="warn">MISSING</span>';
      }
      document.getElementById('microstructureSnapshotTransition').innerHTML = microTransitionLabel;
      document.getElementById('microstructureSnapshotTransitionSub').textContent =
        `state:${fmt(microstructureSnapshotTransition.transition_state)} prev:${fmt(microstructureSnapshotTransition.previous_transition_state)} changed:${fmt(microstructureSnapshotTransition.transition_changed)} gate:${fmt(microstructureSnapshotTransition.gate_decision)} runner:${fmt(microstructureSnapshotTransition.runner_decision)} snapshot:${fmt(microstructureSnapshotTransition.snapshot_id)} runnerSnapshot:${fmt(microstructureSnapshotTransition.runner_snapshot_id)} checks:${fmt(microstructureSnapshotTransition.checks_passed)}/${fmt(microstructureSnapshotTransition.checks_total)} blocker:${fmt(microstructureSnapshotTransition.primary_blocker)} rem:${fmt(microstructureSnapshotTransition.remaining_hours)}h eta:${fmt(microstructureSnapshotTransition.earliest_time_gate_at_utc)} coverage168:${fmt(microstructureSnapshotTransition.trade_coverage_pct)}%/${fmt(microstructureSnapshotTransition.book_coverage_pct)}% recentBook6/24:${fmt(microstructureSnapshotTransition.recent_6h_book_coverage_pct)}%/${fmt(microstructureSnapshotTransition.recent_24h_book_coverage_pct)}% legacyVerified:${fmt(microstructureSnapshotTransition.legacy_gap_rollout_verified)} gaps:${fmt(microstructureSnapshotTransition.binance_missing_ids)}/${fmt(microstructureSnapshotTransition.coinbase_missing_ids)} canAttempt:${fmt(microstructureSnapshotTransition.research_runner_can_attempt_now)} runsBatch:${fmt(microstructureSnapshotTransition.runs_research_batch)} notify:${fmt(microstructureSnapshotTransition.notify_decision)}/${fmt(microstructureSnapshotTransition.notify_kind)} tg:${fmt(microstructureSnapshotTransition.notify_telegram_response_ok)} drill:${fmt(microstructureSnapshotTransition.drill_decision)} ${fmt(microstructureSnapshotTransition.drill_steps_passed)}/${fmt(microstructureSnapshotTransition.drill_steps_total)} dup:${fmt(microstructureSnapshotTransition.drill_ready_duplicate_decision)} failed:${fmt((microstructureSnapshotTransition.failed_checks || []).join('+'))} next:${fmt(microstructureSnapshotTransition.next_action)} exit:${fmt(microstructureSnapshotTransition.watchdog_last_snapshot_transition_exit_code)}/${fmt(microstructureSnapshotTransition.watchdog_last_snapshot_transition_notify_exit_code)} - canTrade:false`;
      let microRunnerLabel = '<span class="warn">CHECK</span>';
      if (microstructureResearchRunner.decision === 'blocked_waiting_for_sealed_snapshot') {
        microRunnerLabel = '<span class="info">WAITING</span>';
      } else if (microstructureResearchRunner.decision === 'microstructure_research_batch_completed_no_candidate') {
        microRunnerLabel = '<span class="warn">NO EDGE</span>';
      } else if (microstructureResearchRunner.decision === 'microstructure_candidates_require_validation_review') {
        microRunnerLabel = '<span class="info">REVIEW</span>';
      } else if (microstructureResearchRunner.decision === 'microstructure_research_batch_already_completed_for_snapshot') {
        microRunnerLabel = '<span class="ok">DONE</span>';
      } else if (microstructureResearchRunner.exists === false) {
        microRunnerLabel = '<span class="warn">MISSING</span>';
      }
      document.getElementById('microstructureResearchRunner').innerHTML = microRunnerLabel;
      document.getElementById('microstructureResearchRunnerSub').textContent =
        `decision:${fmt(microstructureResearchRunner.decision)} gate:${fmt(microstructureResearchRunner.gate_decision)} snapshot:${fmt(microstructureResearchRunner.snapshot_id)} run:${fmt(microstructureResearchRunner.run_id)} status:${fmt(microstructureResearchRunner.status)} experiments:${fmt(microstructureResearchRunner.experiments)} completed/failed:${fmt(microstructureResearchRunner.completed)}/${fmt(microstructureResearchRunner.failed)} candidates:${fmt(microstructureResearchRunner.candidate_count)} qualified:${fmt(microstructureResearchRunner.train_qualified_total)} tested:${fmt(microstructureResearchRunner.tested_total)} notify:${fmt(microstructureResearchRunner.notify_decision)}/${fmt(microstructureResearchRunner.notify_kind)} tg:${fmt(microstructureResearchRunner.telegram_response_ok)} - canTrade:false`;
      let microGovernanceLabel = '<span class="warn">CHECK</span>';
      if (microstructureCandidateGovernance.decision === 'blocked_waiting_for_sealed_snapshot') {
        microGovernanceLabel = '<span class="info">WAITING</span>';
      } else if (microstructureCandidateGovernance.decision === 'reject_no_microstructure_candidate') {
        microGovernanceLabel = '<span class="warn">NO CANDIDATE</span>';
      } else if (microstructureCandidateGovernance.decision === 'microstructure_candidate_review_required_no_promotion') {
        microGovernanceLabel = '<span class="info">REVIEW ONLY</span>';
      } else if ((microstructureCandidateGovernance.decision || '').startsWith('blocked_')) {
        microGovernanceLabel = '<span class="warn">BLOCKED</span>';
      } else if (microstructureCandidateGovernance.exists === false) {
        microGovernanceLabel = '<span class="warn">MISSING</span>';
      }
      document.getElementById('microstructureCandidateGovernance').innerHTML = microGovernanceLabel;
      document.getElementById('microstructureCandidateGovernanceSub').textContent =
        `decision:${fmt(microstructureCandidateGovernance.decision)} runner:${fmt(microstructureCandidateGovernance.runner_decision)} snapshot:${fmt(microstructureCandidateGovernance.snapshot_id)} run:${fmt(microstructureCandidateGovernance.run_id)} candidates:${fmt(microstructureCandidateGovernance.candidate_count)} failed:${fmt((microstructureCandidateGovernance.failed_checks || []).join('+'))} next:${fmt(microstructureCandidateGovernance.next_action)} observer:${fmt(microstructureCandidateGovernance.observer_registration_allowed)} paper:${fmt(microstructureCandidateGovernance.paper_execution_allowed)} live:${fmt(microstructureCandidateGovernance.live_execution_allowed)} exit:${fmt(microstructureCandidateGovernance.watchdog_last_candidate_governance_exit_code)} - canTrade:false`;
      let microReviewLabel = '<span class="warn">CHECK</span>';
      if (microstructureCandidateReviewPack.decision === 'blocked_waiting_for_sealed_snapshot') {
        microReviewLabel = '<span class="info">WAITING</span>';
      } else if (microstructureCandidateReviewPack.decision === 'microstructure_candidate_review_pack_ready') {
        microReviewLabel = '<span class="info">READY</span>';
      } else if (microstructureCandidateReviewPack.decision === 'blocked_no_candidate_to_review') {
        microReviewLabel = '<span class="warn">NO CANDIDATE</span>';
      } else if ((microstructureCandidateReviewPack.decision || '').startsWith('blocked_')) {
        microReviewLabel = '<span class="warn">BLOCKED</span>';
      } else if (microstructureCandidateReviewPack.exists === false) {
        microReviewLabel = '<span class="warn">MISSING</span>';
      }
      document.getElementById('microstructureCandidateReviewPack').innerHTML = microReviewLabel;
      document.getElementById('microstructureCandidateReviewPackSub').textContent =
        `decision:${fmt(microstructureCandidateReviewPack.decision)} governance:${fmt(microstructureCandidateReviewPack.governance_decision)} runner:${fmt(microstructureCandidateReviewPack.runner_decision)} snapshot:${fmt(microstructureCandidateReviewPack.snapshot_id)} run:${fmt(microstructureCandidateReviewPack.run_id)} candidates:${fmt(microstructureCandidateReviewPack.candidate_count)} next:${fmt(microstructureCandidateReviewPack.next_action)} manual:${fmt(microstructureCandidateReviewPack.manual_review_required)} validationAuto:${fmt(microstructureCandidateReviewPack.automatic_validation_opening_allowed)} paperLive:${fmt(microstructureCandidateReviewPack.paper_or_live_execution_allowed)} exit:${fmt(microstructureCandidateReviewPack.watchdog_last_candidate_review_exit_code)} - canTrade:false`;
      let microValidationLabel = '<span class="warn">CHECK</span>';
      if (microstructureValidationProtocol.decision === 'blocked_waiting_for_sealed_snapshot') {
        microValidationLabel = '<span class="info">WAITING</span>';
      } else if (microstructureValidationProtocol.decision === 'microstructure_validation_protocol_draft_ready') {
        microValidationLabel = '<span class="info">DRAFT READY</span>';
      } else if (microstructureValidationProtocol.decision === 'blocked_no_candidate_to_validate') {
        microValidationLabel = '<span class="warn">NO CANDIDATE</span>';
      } else if ((microstructureValidationProtocol.decision || '').startsWith('blocked_')) {
        microValidationLabel = '<span class="warn">BLOCKED</span>';
      } else if (microstructureValidationProtocol.exists === false) {
        microValidationLabel = '<span class="warn">MISSING</span>';
      }
      document.getElementById('microstructureValidationProtocol').innerHTML = microValidationLabel;
      document.getElementById('microstructureValidationProtocolSub').textContent =
        `decision:${fmt(microstructureValidationProtocol.decision)} review:${fmt(microstructureValidationProtocol.review_pack_decision)} governance:${fmt(microstructureValidationProtocol.governance_decision)} trainSnapshot:${fmt(microstructureValidationProtocol.source_train_snapshot_id)} run:${fmt(microstructureValidationProtocol.source_run_id)} candidates:${fmt(microstructureValidationProtocol.candidate_count)} next:${fmt(microstructureValidationProtocol.next_action)} manual:${fmt(microstructureValidationProtocol.manual_approval_required)} opened:${fmt(microstructureValidationProtocol.validation_data_opened_by_this_builder)} autoOOS:${fmt(microstructureValidationProtocol.automatic_oos_opening_allowed)} exit:${fmt(microstructureValidationProtocol.watchdog_last_validation_protocol_exit_code)} - canTrade:false`;
      let microValidationApprovalLabel = '<span class="warn">CHECK</span>';
      if (microstructureValidationApproval.decision === 'blocked_waiting_for_training_candidate_snapshot') {
        microValidationApprovalLabel = '<span class="info">WAITING</span>';
      } else if (microstructureValidationApproval.decision === 'blocked_validation_approval_missing') {
        microValidationApprovalLabel = '<span class="warn">MISSING APPROVAL</span>';
      } else if (microstructureValidationApproval.decision === 'blocked_validation_approval_not_granted') {
        microValidationApprovalLabel = '<span class="warn">NOT GRANTED</span>';
      } else if (microstructureValidationApproval.decision === 'validation_approval_structurally_valid_runner_still_skeleton') {
        microValidationApprovalLabel = '<span class="info">VALID STRUCTURE</span>';
      } else if ((microstructureValidationApproval.decision || '').startsWith('blocked_')) {
        microValidationApprovalLabel = '<span class="warn">BLOCKED</span>';
      } else if (microstructureValidationApproval.exists === false) {
        microValidationApprovalLabel = '<span class="warn">MISSING</span>';
      }
      document.getElementById('microstructureValidationApproval').innerHTML = microValidationApprovalLabel;
      document.getElementById('microstructureValidationApprovalSub').textContent =
        `decision:${fmt(microstructureValidationApproval.decision)} protocol:${fmt(microstructureValidationApproval.protocol_decision)} template:${fmt(microstructureValidationApproval.template_exists)} approvalFile:${fmt(microstructureValidationApproval.approval_file_present)} candidate:${fmt(microstructureValidationApproval.approval_candidate_rank)}/${fmt(microstructureValidationApproval.approval_strategy_id)} train:${fmt(microstructureValidationApproval.source_train_snapshot_id)} approvedSnapshot:${fmt(microstructureValidationApproval.approval_validation_snapshot_id)} currentSnapshot:${fmt(microstructureValidationApproval.current_snapshot_id)} granted:${fmt(microstructureValidationApproval.manual_approval_granted)} allowed:${fmt(microstructureValidationApproval.validation_opening_allowed)} matchCandidate:${fmt(microstructureValidationApproval.candidate_matches_protocol)} matchSnapshot:${fmt(microstructureValidationApproval.approval_matches_current_snapshot)} checks:${fmt(microstructureValidationApproval.all_human_checks_true)} prohibitions:${fmt(microstructureValidationApproval.all_execution_prohibitions_false)} opened:${fmt(microstructureValidationApproval.opens_validation)} failed:${fmt((microstructureValidationApproval.failed_checks || []).join('+'))} next:${fmt(microstructureValidationApproval.next_action)} exit:${fmt(microstructureValidationApproval.watchdog_last_validation_approval_exit_code)} - canTrade:false`;
      let microValidationRunnerLabel = '<span class="warn">CHECK</span>';
      if (microstructureValidationRunner.decision === 'blocked_waiting_for_training_candidate_snapshot') {
        microValidationRunnerLabel = '<span class="info">WAITING</span>';
      } else if (microstructureValidationRunner.decision === 'blocked_manual_approval_missing') {
        microValidationRunnerLabel = '<span class="warn">APPROVAL NEEDED</span>';
      } else if (microstructureValidationRunner.decision === 'blocked_waiting_for_validation_snapshot') {
        microValidationRunnerLabel = '<span class="info">WAITING VALIDATION SNAPSHOT</span>';
      } else if (microstructureValidationRunner.decision === 'blocked_validation_runner_skeleton_no_execution') {
        microValidationRunnerLabel = '<span class="warn">SKELETON ONLY</span>';
      } else if ((microstructureValidationRunner.decision || '').startsWith('blocked_')) {
        microValidationRunnerLabel = '<span class="warn">BLOCKED</span>';
      } else if (microstructureValidationRunner.exists === false) {
        microValidationRunnerLabel = '<span class="warn">MISSING</span>';
      }
      document.getElementById('microstructureValidationRunner').innerHTML = microValidationRunnerLabel;
      document.getElementById('microstructureValidationRunnerSub').textContent =
        `decision:${fmt(microstructureValidationRunner.decision)} protocol:${fmt(microstructureValidationRunner.protocol_decision)} train:${fmt(microstructureValidationRunner.source_train_snapshot_id)} validation:${fmt(microstructureValidationRunner.validation_snapshot_id)} candidates:${fmt(microstructureValidationRunner.candidate_count)} approval:${fmt(microstructureValidationRunner.manual_approval_granted)} sealed:${fmt(microstructureValidationRunner.validation_snapshot_sealed)} implemented:${fmt(microstructureValidationRunner.validation_execution_implemented)} opened:${fmt(microstructureValidationRunner.opens_validation)} execCode:${fmt(microstructureValidationRunner.executes_strategy_code)} failed:${fmt((microstructureValidationRunner.failed_checks || []).join('+'))} next:${fmt(microstructureValidationRunner.next_action)} exit:${fmt(microstructureValidationRunner.watchdog_last_validation_runner_exit_code)} - canTrade:false`;
      document.getElementById('researchDataSnapshot').innerHTML = researchDataSnapshot.verification_passed
        ? '<span class="ok">SEALED</span>'
        : '<span class="warn">CHECK</span>';
      document.getElementById('researchDataSnapshotSub').textContent =
        `snapshot:${fmt(researchDataSnapshot.snapshot_id)} profile:${fmt(researchDataSnapshot.profile)} files:${fmt(researchDataSnapshot.files)} bytes:${fmt(researchDataSnapshot.bytes)} last:${fmt(researchDataSnapshot.latest_data_time)} verified:${fmt(researchDataSnapshot.verified_at)} sha:${fmt(researchDataSnapshot.dataset_sha256)} - canTrade:false`;
      document.getElementById('verifiedResearchRun').innerHTML = verifiedResearchRun.report_contract_passed
        ? '<span class="ok">VERIFIED</span>'
        : '<span class="warn">CHECK</span>';
      document.getElementById('verifiedResearchRunSub').textContent =
        `run:${fmt(verifiedResearchRun.run_id)} hypothesis:${fmt(verifiedResearchRun.hypothesis_id)} purpose:${fmt(verifiedResearchRun.purpose)} experiment:${fmt(verifiedResearchRun.experiment)} snapshot:${fmt(verifiedResearchRun.snapshot_id)} status:${fmt(verifiedResearchRun.status)} decision:${fmt(verifiedResearchRun.decision)} multiplicity:${fmt(verifiedResearchRun.multiplicity_status)}/${fmt(verifiedResearchRun.multiplicity_pass)} eligible:${fmt(verifiedResearchRun.eligible_for_next_stage)} rc:${fmt(verifiedResearchRun.return_code)} shell:${fmt(verifiedResearchRun.shell)} extraArgs:${fmt(verifiedResearchRun.arbitrary_extra_args)} reportSha:${fmt(verifiedResearchRun.report_sha256)} finished:${fmt(verifiedResearchRun.finished_at)} - canTrade:false`;
      document.getElementById('hypothesisRegistry').innerHTML = hypothesisRegistry.audit_decision === 'hypothesis_registry_valid'
        ? '<span class="ok">GOVERNED</span>'
        : '<span class="warn">CHECK</span>';
      document.getElementById('hypothesisRegistrySub').textContent =
        `registry:${fmt(hypothesisRegistry.registry_id)} audit:${fmt(hypothesisRegistry.audit_decision)} registered:${fmt(hypothesisRegistry.registered)} rejected:${fmt(hypothesisRegistry.rejected)} pending:${fmt(hypothesisRegistry.pending)} configBudget:${fmt(hypothesisRegistry.configurations_used)}/${fmt(hypothesisRegistry.configurations_max)} OOS:${fmt(hypothesisRegistry.oos_used)}/${fmt(hypothesisRegistry.oos_max)} correction:${fmt(hypothesisRegistry.correction)} alpha:${fmt(hypothesisRegistry.familywise_alpha)} - canTrade:false`;

      const forward = state.components?.max_pipeline?.strategy_mix_forward_feed || {};
      const familyStates = strategyRuntime.families || [];
      const trendRejected = familyStates.some(item => item.family === 'TREND_MIX_4H' && item.runtime_status === 'observer_paused_historical_rejection');
      const activeFamilies = familyStates.filter(item => item.runtime_status === 'observer_running').map(item => item.family);
      const rejectedFamilies = familyStates.filter(item => item.runtime_status === 'observer_paused_historical_rejection').map(item => item.family);
      const forwardStatus = forward.last_status || 'not_run';
      let forwardLabel = '<span class="warn">EMPTY</span>';
      if (trendRejected) {
        forwardLabel = '<span class="info">EDGE WATCH</span>';
      } else if (forwardStatus === 'paper_entry_intent') {
        forwardLabel = '<span class="warn">PAPER SIGNAL</span>';
      } else if (forwardStatus === 'no_signal' || forwardStatus === 'duplicate_signal') {
        forwardLabel = '<span class="ok">NO SIGNAL</span>';
      } else if (forward.exists) {
        forwardLabel = `<span class="info">${fmt(forwardStatus)}</span>`;
      }
      document.getElementById('forwardFeed').innerHTML = forwardLabel;
      document.getElementById('forwardFeedSub').textContent =
        `${fmt(forward.symbol)} ${fmt(forward.interval)} - bar: ${fmt(forward.last_closed_bar_ts)} - close: ${fmt(forward.latest_closed_close)} - signals: ${fmt(forward.signals_on_latest_bar)} · crowd:${fmt(crowdPositioning.observer_status)} ${fmt(crowdPositioning.observer_side_hint)} z:${fmt(crowdPositioning.observer_ratio_z)} · fwd:${fmt(crowdPositioning.scoreboard_signals)}/${fmt(crowdPositioning.scoreboard_resolved)} · portfolio:${fmt(familyPortfolio.total_resolved)} resolved ${fmt(familyPortfolio.total_net_r)}R · lifecycle:${fmt(familyPortfolio.lifecycle_decision)} · gate:${fmt(crowdPositioning.promotion_decision)} · tg:${fmt(crowdPositioning.notify_decision)}`;
      if (trendRejected) {
        document.getElementById('forwardFeedSub').textContent =
          `active observer: ${activeFamilies.join(', ') || 'none'} - rejected: ${rejectedFamilies.join(', ') || 'none'} - historical Trend feed ignored`;
      }
      const forwardScore = forward.scoreboard || {};
      const tg = forward.telegram_notify || {};
      const regime = forward.canonical_regime || {};
      const oiFunding = forward.oi_funding_context || {};
      const oiFundingScore = forward.oi_funding_scoreboard || {};
      const oiReplay = forward.oi_funding_replay_audit || {};
      const oiPromotion = forward.oi_guard_promotion_gate || {};
      const accumulator = forward.forward_outcome_accumulator || {};
      const entryScarcity = forward.entry_scarcity_diagnostic || {};
      const shadowRelax = forward.shadow_relaxation_validator || {};
      const rangeFamily = forward.range_family_validator || {};
      const rangeRefiner = forward.range_watchlist_refiner || {};
      const rangeObserver = forward.range_refined_forward_observer || {};
      const rangeScore = forward.range_refined_observer_scoreboard || {};
      const rangeScarcity = forward.range_refined_signal_scarcity_diagnostic || {};
      const rangeWatch = forward.range_refined_pending_watch || {};
      const rangeWatchNotify = forward.range_refined_pending_watch_telegram_notify || {};
      const edgeObserver = forward.edge_forward_range_observer || {};
      const edgeScore = forward.edge_forward_range_scoreboard || {};
      const edgeLiquidationContext = forward.edge_liquidation_context_shadow || {};
      const edgeLiquidationScore = forward.edge_liquidation_context_scoreboard || {};
      const edgeLiquidationReplay = forward.edge_liquidation_context_historical_replay || {};
      const edgeLiquidationEvidenceGate = forward.edge_liquidation_score_evidence_gate || {};
      const edgeWatch = forward.edge_forward_pending_watch || {};
      const edgeWatchNotify = forward.edge_forward_pending_watch_telegram_notify || {};
      const edgeGate = forward.edge_forward_promotion_gate || {};
      const rangeAblation = forward.range_refined_filter_shadow_ablation || {};
      const rangeShadowForward = forward.range_refined_filter_shadow_forward_observer || {};
      const rangeShadowScore = forward.range_refined_filter_shadow_forward_scoreboard || {};
      const rangeShadowGate = forward.range_refined_filter_shadow_promotion_gate || {};
      const rangePromotion = forward.range_refined_promotion_gate || {};
      const rangeAlert = forward.range_refined_signal_alert_guard || {};
      const rangeAlertDrill = forward.range_refined_signal_alert_drill || {};
      document.getElementById('forwardFeedPolicy').textContent =
        `strategy: ${fmt(forward.strategy_id)} - health:${fmt(runtimeHealth.classification)} age:${fmt(runtimeHealth.last_run_age_minutes)}m panel:${fmt(runtimeHealth.panel_port_open)} - regime: ${fmt(regime.regime)} shock:${fmt(regime.shock_watch)} - oi/funding: ${fmt(oiFunding.context_bias)} degraded:${fmt(oiFunding.data_degraded)} funding:${fmt(oiFunding.funding_state)} guard:${fmt(oiFunding.oi_guard_state)} keepLong:${fmt(oiFunding.oi_guard_would_keep_long_signal)} filterNow:${fmt(oiFunding.oi_guard_can_filter_now)} promotion:${fmt(oiPromotion.decision)} active:${fmt(oiPromotion.active_filter_allowed)} live:${fmt(oiPromotion.live_execution_allowed)} dataQ:${fmt(oiDataQuality.classification)} oi:${fmt(oiDataQuality.aligned_oi_coverage_pct)}% replayCtx:${fmt(oiDataQuality.replay_full_context_available)}/${fmt(oiDataQuality.replay_trades)} ctxScore:${fmt(oiFundingScore.classification)} ${fmt(oiFundingScore.context_observations)}/${fmt(oiFundingScore.entry_contexts)} guardCtx:${fmt(oiFundingScore.oi_guard_entry_contexts)}/${fmt(oiFundingScore.oi_guard_resolved_contexts)} guardCounts:${fmt(JSON.stringify(oiFundingScore.oi_guard_state_counts || {}))} replayAudit:${fmt(oiReplay.classification)} fullCtx:${fmt(oiReplay.full_context_available_trades)}/${fmt(oiReplay.total_trades)} - score: ${fmt(forwardScore.classification)} ${fmt(forwardScore.resolved)}/${fmt(forwardScore.entry_intents)} exp ${fmt(forwardScore.expectancy_r)}R - acc:${fmt(accumulator.classification)} ctxBars:${fmt(accumulator.unique_context_bars)}/${fmt(accumulator.unique_context_bars_required)} entries:${fmt(accumulator.forward_entry_intents)}/${fmt(accumulator.forward_entry_intents_required)} resolved:${fmt(accumulator.forward_resolved_entries)}/${fmt(accumulator.forward_resolved_entries_required)} guardResolved:${fmt(accumulator.oi_guard_resolved_contexts)}/${fmt(accumulator.oi_guard_resolved_contexts_required)} - scarcity:${fmt(entryScarcity.classification)} locked:${fmt(entryScarcity.locked_signal_like_bars)}/${fmt(entryScarcity.bars_analyzed)} bottleneck:${fmt(entryScarcity.primary_bottleneck)} blockers:${fmt((entryScarcity.latest_blockers || []).join('+'))} - relax:${fmt(shadowRelax.decision)} candidates:${fmt(shadowRelax.shadow_candidate_count)}/${fmt(shadowRelax.tested)} top:${fmt(shadowRelax.top_variant)}:${fmt(shadowRelax.top_variant_verdict)} - range:${fmt(rangeFamily.decision)} candidates:${fmt(rangeFamily.candidate_count)}/${fmt(rangeFamily.tested)} watch:${fmt(rangeFamily.watchlist_count)} topExp:${fmt(rangeFamily.top_full_expectancy_r)}/${fmt(rangeFamily.top_holdout_expectancy_r)} - refine:${fmt(rangeRefiner.decision)} candidates:${fmt(rangeRefiner.candidate_count)}/${fmt(rangeRefiner.tested)} watch:${fmt(rangeRefiner.watchlist_count)} selected:${fmt(rangeRefiner.selected_filter)} exp:${fmt(rangeRefiner.selected_full_expectancy_r)}/${fmt(rangeRefiner.selected_holdout_expectancy_r)} cost10:${fmt(rangeRefiner.selected_cost10_expectancy_r)} - rangeObs:${fmt(rangeObserver.status)} raw/ref:${fmt(rangeObserver.raw_signals_on_latest_bar)}/${fmt(rangeObserver.refined_signals_on_latest_bar)} degraded:${fmt(rangeObserver.data_degraded)} rangeScarcity:${fmt(rangeScarcity.classification)} base/ref:${fmt(rangeScarcity.base_setup_bars)}/${fmt(rangeScarcity.refined_setup_bars)} filterBlock:${fmt(rangeScarcity.top_filter_blocker)} rangeWatch:${fmt(rangeWatch.classification)} ctx/trig:${fmt(rangeWatch.context_ok)}/${fmt(rangeWatch.trigger_ok)} dist:${fmt(rangeWatch.distance_to_trigger_atr)}ATR/${fmt(rangeWatch.distance_to_trigger_pct)}% prog:${fmt(rangeWatch.trigger_progress_pct)}% watchNotify:${fmt(rangeWatchNotify.decision)} tg:${fmt(rangeWatchNotify.telegram_response_ok)} edgeObs:${fmt(edgeObserver.status)} raw/ref:${fmt(edgeObserver.raw_signals_on_latest_bar)}/${fmt(edgeObserver.refined_signals_on_latest_bar)} edgeScore:${fmt(edgeScore.classification)} ${fmt(edgeScore.resolved)}/${fmt(edgeScore.observer_signal_events)} exp:${fmt(edgeScore.expectancy_r)}R edgeWatch:${fmt(edgeWatch.classification)} ctx/trig:${fmt(edgeWatch.context_ok)}/${fmt(edgeWatch.trigger_ok)} dist:${fmt(edgeWatch.distance_to_trigger_atr)}ATR/${fmt(edgeWatch.distance_to_trigger_pct)}% edgeNotify:${fmt(edgeWatchNotify.decision)} tg:${fmt(edgeWatchNotify.telegram_response_ok)} edgeGate:${fmt(edgeGate.decision)} observer:${fmt(edgeGate.observer_allowed)} paperDesign:${fmt(edgeGate.paper_design_review_allowed)} rangeAblation:${fmt(rangeAblation.decision)} best:${fmt(rangeAblation.best_variant)} pass:${fmt(rangeAblation.shadow_shape_pass_count)}/${fmt(rangeAblation.tested)} shadowFwd:${fmt(rangeShadowForward.decision)} raw/var:${fmt(rangeShadowForward.raw_base_signals_on_latest_bar)}/${fmt(rangeShadowForward.variant_signals_on_latest_bar)} variants:${fmt((rangeShadowForward.signalling_variants || []).join('+'))} shadowScore:${fmt(rangeShadowScore.classification)} ${fmt(rangeShadowScore.resolved)}/${fmt(rangeShadowScore.shadow_signal_events)} exp:${fmt(rangeShadowScore.expectancy_r)}R shadowGate:${fmt(rangeShadowGate.decision)} observer:${fmt(rangeShadowGate.shadow_observer_allowed)} paperDesign:${fmt(rangeShadowGate.paper_design_review_allowed)} score:${fmt(rangeScore.classification)} ${fmt(rangeScore.resolved)}/${fmt(rangeScore.observer_signal_events)} exp:${fmt(rangeScore.expectancy_r)}R rangeGate:${fmt(rangePromotion.decision)} observer:${fmt(rangePromotion.observer_allowed)} paperDesign:${fmt(rangePromotion.paper_design_review_allowed)} alert:${fmt(rangeAlert.decision)} card:${rangeAlert.card_md_path ? 'ready' : 'not written'} drill:${fmt(rangeAlertDrill.decision)} first:${fmt(rangeAlertDrill.first_decision)} dup:${fmt(rangeAlertDrill.duplicate_decision)} - tg: ${fmt(tg.decision)} - card: ${forward.card_md_path ? 'ready' : 'not written'} - no orders`;
      if (trendRejected) {
        document.getElementById('forwardFeedPolicy').textContent =
          `EDGE observer - status:${fmt(edgeObserver.status)} - score:${fmt(edgeScore.classification)} ${fmt(edgeScore.resolved)}/${fmt(edgeScore.observer_signal_events)} exp:${fmt(edgeScore.expectancy_r)}R - liqCtx:${fmt(edgeLiquidationContext.context)} continuous:${fmt(edgeLiquidationContext.continuous_score)}/${fmt(edgeLiquidationContext.score_bin)} lock:${fmt(edgeLiquidationContext.score_lock_status)} bar:${fmt(edgeLiquidationContext.bar_ts)} dATR:${fmt(edgeLiquidationContext.displacement_atr)} oi:${fmt(edgeLiquidationContext.oi_delta_pct)}% vz:${fmt(edgeLiquidationContext.volume_z)} filter:${fmt(edgeLiquidationContext.filter_applied)} - liqScore:${fmt(edgeLiquidationScore.classification)} ${fmt(edgeLiquidationScore.context_labelled_signals)}/${fmt(edgeLiquidationScore.edge_signal_events)} bins:${fmt(JSON.stringify(edgeLiquidationScore.by_score_bin || {}))} change:${fmt(edgeLiquidationScore.recommended_filter_change)} - liqEvidence:${fmt(edgeLiquidationEvidenceGate.decision)} resolved:${fmt(edgeLiquidationEvidenceGate.resolved_total)}/${fmt(edgeLiquidationEvidenceGate.resolved_required)} inactive:${fmt(edgeLiquidationEvidenceGate.inactive_resolved)} qualifying:${fmt((edgeLiquidationEvidenceGate.qualifying_bins || []).join('+'))} review:${fmt(edgeLiquidationEvidenceGate.research_review_allowed)} filter:${fmt(edgeLiquidationEvidenceGate.filter_change_allowed)} - liqReplay:${fmt(edgeLiquidationReplay.decision)} train/oos:${fmt(edgeLiquidationReplay.train_trades)}/${fmt(edgeLiquidationReplay.oos_trades)} exact:${fmt(edgeLiquidationReplay.exact_trade_count_match)} informativeOOS:${fmt((edgeLiquidationReplay.informative_oos_contexts || []).join('+'))} change:${fmt(edgeLiquidationReplay.recommended_filter_change)} - watch:${fmt(edgeWatch.classification)} dist:${fmt(edgeWatch.distance_to_trigger_atr)}ATR - notify:${fmt(edgeWatchNotify.decision)} - gate:${fmt(edgeGate.decision)} observer:${fmt(edgeGate.observer_allowed)} paperDesign:${fmt(edgeGate.paper_design_review_allowed)} - health:${fmt(runtimeHealth.classification)} - canTrade:false - no orders`;
      }
    }

    function renderTasks() {
      if (!state) return;
      const root = document.getElementById('tasks');
      root.innerHTML = '';
      for (const task of state.tasks) {
        const btn = document.createElement('button');
        btn.disabled = busy;
        btn.innerHTML = `<strong>${task.title}</strong><span>${task.description}<br><br>${task.network_note}</span>`;
        btn.onclick = () => runTask(task.id);
        root.appendChild(btn);
      }
    }

    function renderJobs() {
      const root = document.getElementById('jobs');
      const jobs = state.recent_jobs || [];
      root.innerHTML = '';
      if (!jobs.length) {
        root.innerHTML = '<div class="small">Пока нет запусков через пульт.</div>';
        return;
      }
      for (const job of jobs) {
        const div = document.createElement('div');
        div.className = 'job';
        div.innerHTML = `
          <div class="job-head">
            <div><strong>${job.title}</strong><div class="small">${job.created_at || ''}</div></div>
            <div class="${clsFor(job.status)}">${job.status}</div>
          </div>
          <div class="small">exit: ${fmt(job.exit_code)} · ${fmt(job.duration_s)}s</div>`;
        div.onclick = () => {
          document.getElementById('output').textContent =
            `# ${job.title}\nstatus=${job.status} exit=${fmt(job.exit_code)} duration=${fmt(job.duration_s)}s\n\nSTDOUT:\n${job.stdout || ''}\n\nSTDERR:\n${job.stderr || ''}`;
        };
        root.appendChild(div);
      }
    }

    async function refresh() {
      state = await api('/api/status');
      renderTop();
      renderTasks();
      renderJobs();
      const active = (state.recent_jobs || []).some(job => ['queued', 'running'].includes(job.status));
      if (active) setTimeout(refresh, 1500);
    }

    refresh().catch(err => {
      document.getElementById('output').textContent = `Status error: ${err.message}`;
    });
    setInterval(refresh, 10000);
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "TradingOSControlPanel/1.0"

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(INDEX_HTML)
            return
        if parsed.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if parsed.path == "/api/status":
            self._send_json(package_status())
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if job is None:
                job = read_json(JOBS_DIR / f"{job_id}.json")
            if job is None:
                self._send_json({"error": "job_not_found"}, HTTPStatus.NOT_FOUND)
                return
            self._send_json(job)
            return
        self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/run":
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return

        length = int(self.headers.get("Content-Length", "0"))
        if length > 2048:
            self._send_json({"error": "request_too_large"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_json({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
            return

        task_id = str(payload.get("task", ""))
        if task_id not in TASKS:
            self._send_json({"error": "task_not_allowed", "allowed": sorted(TASKS)}, HTTPStatus.BAD_REQUEST)
            return

        job = start_job(task_id)
        self._send_json(job, HTTPStatus.ACCEPTED)

    def log_message(self, fmt: str, *args: Any) -> None:
        message = f"{now_iso()} {self.client_address[0]} {fmt % args}\n"
        ensure_dirs()
        with (OUT_DIR / "access.log").open("a", encoding="utf-8") as handle:
            handle.write(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Local safe Trading OS web control panel")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    join_windows_runtime_job(f"control_panel_{args.port}")
    ensure_dirs()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Trading OS Control Panel: http://{args.host}:{args.port}", flush=True)
    print("Safe mode: live trading locked; only allowlisted commands are exposed.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping control panel.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

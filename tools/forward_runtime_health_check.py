#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {"_read_error": "invalid_json", "_path": str(path)}
    return payload if isinstance(payload, dict) else {"_read_error": "not_object", "_path": str(path)}


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_minutes(value: Any, now: datetime) -> float | None:
    parsed = parse_ts(value)
    if parsed is None:
        return None
    return round((now - parsed).total_seconds() / 60.0, 3)


def can_connect(host: str, port: int, timeout_s: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def process_alive(value: Any) -> bool:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if handle:
            exit_code = ctypes.c_ulong()
            query_ok = bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)))
            kernel32.CloseHandle(handle)
            return query_ok and exit_code.value == 259  # STILL_ACTIVE
        return kernel32.GetLastError() == 5  # Access denied still proves the PID exists.
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def gate(name: str, passed: bool, actual: Any, required: Any, severity: str = "hard") -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "actual": actual,
        "required": required,
        "severity": severity,
    }


def hard_ok(gates: list[dict[str, Any]]) -> bool:
    return all(item.get("passed") for item in gates if item.get("severity") == "hard")


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    now = now_utc()
    last_run_path = resolve_path(args.last_run_status)
    loop_status_path = resolve_path(args.loop_status)
    control_panel_status_path = resolve_path(args.control_panel_status)
    scheduler_report_path = resolve_path(args.scheduler_report)
    signal_card_path = resolve_path(args.signal_card)
    trend_lock_path = resolve_path(args.trend_lock)
    edge_observer_path = resolve_path(args.edge_observer)
    edge_pending_path = resolve_path(args.edge_pending)
    edge_scoreboard_path = resolve_path(args.edge_scoreboard)
    promotion_gate_path = resolve_path(args.promotion_gate)
    data_quality_path = resolve_path(args.data_quality)
    crowd_loop_status_path = resolve_path(args.crowd_loop_status)
    crowd_last_run_path = resolve_path(args.crowd_last_run_status)
    crowd_refresh_report_path = resolve_path(args.crowd_refresh_report)
    crowd_promotion_gate_path = resolve_path(args.crowd_promotion_gate)
    backup_loop_status_path = resolve_path(args.backup_loop_status)
    backup_last_run_path = resolve_path(args.backup_last_run_status)
    microstructure_book_loop_status_path = resolve_path(args.microstructure_book_loop_status)
    real_edge_observer_loop_status_path = resolve_path(args.real_edge_observer_loop_status)

    last_run = read_json(last_run_path)
    loop_status = read_json(loop_status_path)
    control_panel_status = read_json(control_panel_status_path)
    scheduler_report = read_json(scheduler_report_path)
    signal_card = read_json(signal_card_path)
    trend_lock = read_json(trend_lock_path)
    edge_observer = read_json(edge_observer_path)
    edge_pending = read_json(edge_pending_path)
    edge_scoreboard = read_json(edge_scoreboard_path)
    promotion_gate = read_json(promotion_gate_path)
    data_quality = read_json(data_quality_path)
    crowd_loop_status = read_json(crowd_loop_status_path)
    crowd_last_run = read_json(crowd_last_run_path)
    crowd_refresh_report = read_json(crowd_refresh_report_path)
    crowd_promotion_gate = read_json(crowd_promotion_gate_path)
    backup_loop_status = read_json(backup_loop_status_path)
    backup_last_run = read_json(backup_last_run_path)
    microstructure_book_loop_status = read_json(microstructure_book_loop_status_path)
    real_edge_observer_loop_status = read_json(real_edge_observer_loop_status_path)

    last_run_age = age_minutes(last_run.get("ts"), now)
    loop_status_age = age_minutes(loop_status.get("ts"), now)
    forward_loop_pid_alive = process_alive(loop_status.get("pid"))
    scheduler_age = age_minutes(scheduler_report.get("generated_at"), now)
    card_generated_age = age_minutes(signal_card.get("generated_at"), now)
    edge_observer_age = age_minutes(edge_observer.get("generated_at"), now)
    edge_pending_age = age_minutes(edge_pending.get("generated_at"), now)
    edge_scoreboard_age = age_minutes(edge_scoreboard.get("generated_at"), now)
    panel_port_open = can_connect(args.panel_host, args.panel_port, args.connect_timeout_s)
    crowd_loop_age = age_minutes(crowd_loop_status.get("ts"), now)
    crowd_last_run_age = age_minutes(crowd_last_run.get("ts"), now)
    crowd_loop_pid_alive = process_alive(crowd_loop_status.get("pid"))
    backup_required = "my drive" not in str(ROOT).lower()
    backup_loop_age = age_minutes(backup_loop_status.get("ts"), now)
    backup_last_run_age = age_minutes(backup_last_run.get("ts"), now)
    backup_loop_pid_alive = process_alive(backup_loop_status.get("pid")) if backup_required else None
    microstructure_book_loop_age = age_minutes(microstructure_book_loop_status.get("ts"), now)
    microstructure_book_loop_pid_alive = process_alive(microstructure_book_loop_status.get("pid"))
    real_edge_observer_loop_age = age_minutes(real_edge_observer_loop_status.get("ts"), now)
    real_edge_observer_loop_pid_alive = process_alive(real_edge_observer_loop_status.get("pid"))

    latest_cycle = scheduler_report.get("latest_cycle") if isinstance(scheduler_report.get("latest_cycle"), dict) else {}
    promotion = promotion_gate.get("promotion") if isinstance(promotion_gate.get("promotion"), dict) else {}
    dq_summary = data_quality.get("summary") if isinstance(data_quality.get("summary"), dict) else {}
    last_extra = last_run.get("extra") if isinstance(last_run.get("extra"), dict) else {}
    crowd_promotion = (
        crowd_promotion_gate.get("promotion")
        if isinstance(crowd_promotion_gate.get("promotion"), dict)
        else {}
    )
    crowd_critical_failures = (
        crowd_refresh_report.get("critical_failed_steps")
        if isinstance(crowd_refresh_report.get("critical_failed_steps"), list)
        else crowd_refresh_report.get("failed_steps")
        if isinstance(crowd_refresh_report.get("failed_steps"), list)
        else []
    )
    trend_historically_rejected = (
        trend_lock.get("family") == "TREND_MIX_4H"
        and trend_lock.get("enabled") is False
        and str(trend_lock.get("status") or "").startswith("historically_rejected")
        and trend_lock.get("boundaries", {}).get("can_trade") is False
    )

    latest_cycle_gates: list[dict[str, Any]] = []
    for key, value in latest_cycle.items():
        if not isinstance(value, dict) or "exit_code" not in value:
            continue
        latest_cycle_gates.append(gate(f"latest_cycle_{key}_exit_0", value.get("exit_code") == 0, value.get("exit_code"), 0))

    acceptable_last_run_statuses = {"completed", "health_check_running"}
    gates = [
        gate("last_run_status_exists", bool(last_run) and not last_run.get("_read_error"), str(last_run_path), "readable JSON"),
        gate("last_run_status_ok", last_run.get("status") in acceptable_last_run_statuses, last_run.get("status"), "completed or health_check_running"),
        gate("last_run_exit_0", last_run.get("exit_code") == 0, last_run.get("exit_code"), 0),
        gate("last_run_fresh", last_run_age is not None and last_run_age <= args.max_last_run_age_minutes, last_run_age, f"<= {args.max_last_run_age_minutes} minutes"),
        gate("data_quality_exit_0", last_extra.get("data_quality_exit_code") == 0, last_extra.get("data_quality_exit_code"), 0),
        gate("loop_status_exists", bool(loop_status) and not loop_status.get("_read_error"), str(loop_status_path), "readable JSON"),
        gate("forward_loop_pid_alive", forward_loop_pid_alive, loop_status.get("pid"), "running process"),
        gate("loop_status_recent", loop_status_age is not None and loop_status_age <= args.max_loop_status_age_minutes, loop_status_age, f"<= {args.max_loop_status_age_minutes} minutes"),
        gate("panel_port_open", panel_port_open, f"{args.panel_host}:{args.panel_port}", "connectable"),
        gate("scheduler_report_exists", bool(scheduler_report) and not scheduler_report.get("_read_error"), str(scheduler_report_path), "readable JSON"),
        gate("scheduler_report_fresh", scheduler_age is not None and scheduler_age <= args.max_last_run_age_minutes, scheduler_age, f"<= {args.max_last_run_age_minutes} minutes"),
        gate("trend_lock_valid_if_rejected", not str(trend_lock.get("status") or "").startswith("historically_rejected") or trend_historically_rejected, trend_historically_rejected, True),
        gate("signal_card_exists", trend_historically_rejected or (bool(signal_card) and not signal_card.get("_read_error")), str(signal_card_path), "readable JSON unless Trend is historically rejected"),
        gate("signal_card_fresh", trend_historically_rejected or (card_generated_age is not None and card_generated_age <= args.max_last_run_age_minutes), card_generated_age, f"<= {args.max_last_run_age_minutes} minutes unless Trend is historically rejected"),
        gate("edge_observer_exists", not trend_historically_rejected or (bool(edge_observer) and not edge_observer.get("_read_error")), str(edge_observer_path), "readable JSON when Trend is rejected"),
        gate("edge_observer_fresh", not trend_historically_rejected or (edge_observer_age is not None and edge_observer_age <= args.max_last_run_age_minutes), edge_observer_age, f"<= {args.max_last_run_age_minutes} minutes when Trend is rejected"),
        gate("edge_pending_exists", not trend_historically_rejected or (bool(edge_pending) and not edge_pending.get("_read_error")), str(edge_pending_path), "readable JSON when Trend is rejected"),
        gate("edge_pending_fresh", not trend_historically_rejected or (edge_pending_age is not None and edge_pending_age <= args.max_last_run_age_minutes), edge_pending_age, f"<= {args.max_last_run_age_minutes} minutes when Trend is rejected"),
        gate("edge_scoreboard_exists", not trend_historically_rejected or (bool(edge_scoreboard) and not edge_scoreboard.get("_read_error")), str(edge_scoreboard_path), "readable JSON when Trend is rejected"),
        gate("edge_scoreboard_fresh", not trend_historically_rejected or (edge_scoreboard_age is not None and edge_scoreboard_age <= args.max_last_run_age_minutes), edge_scoreboard_age, f"<= {args.max_last_run_age_minutes} minutes when Trend is rejected"),
        gate("promotion_gate_exists", bool(promotion_gate) and not promotion_gate.get("_read_error"), str(promotion_gate_path), "readable JSON"),
        gate("promotion_gate_no_live", promotion.get("live_execution_allowed") is False, promotion.get("live_execution_allowed"), False),
        gate("promotion_gate_no_active_filter", promotion.get("active_filter_allowed") is False, promotion.get("active_filter_allowed"), False),
        gate("data_quality_ready", dq_summary.get("classification") == "oi_guard_data_ready", dq_summary.get("classification"), "oi_guard_data_ready", severity="soft"),
        gate("crowd_loop_status_exists", bool(crowd_loop_status) and not crowd_loop_status.get("_read_error"), str(crowd_loop_status_path), "readable JSON"),
        gate(
            "crowd_loop_status_ok",
            crowd_loop_status.get("status") in {"running", "running_once", "sleeping"},
            crowd_loop_status.get("status"),
            "running, running_once or sleeping",
        ),
        gate("crowd_loop_pid_alive", crowd_loop_pid_alive, crowd_loop_status.get("pid"), "running process"),
        gate(
            "crowd_loop_fresh",
            crowd_loop_age is not None and crowd_loop_age <= args.max_crowd_loop_age_minutes,
            crowd_loop_age,
            f"<= {args.max_crowd_loop_age_minutes} minutes",
        ),
        gate("crowd_last_run_exists", bool(crowd_last_run) and not crowd_last_run.get("_read_error"), str(crowd_last_run_path), "readable JSON"),
        gate(
            "crowd_last_run_status_ok",
            crowd_last_run.get("status") in {"completed_observer_only", "completed_notification_warning"},
            crowd_last_run.get("status"),
            "completed observer-only status",
        ),
        gate("crowd_last_run_exit_0", crowd_last_run.get("exit_code") == 0, crowd_last_run.get("exit_code"), 0),
        gate(
            "crowd_last_run_fresh",
            crowd_last_run_age is not None and crowd_last_run_age <= args.max_crowd_last_run_age_minutes,
            crowd_last_run_age,
            f"<= {args.max_crowd_last_run_age_minutes} minutes",
        ),
        gate("crowd_refresh_report_exists", bool(crowd_refresh_report) and not crowd_refresh_report.get("_read_error"), str(crowd_refresh_report_path), "readable JSON"),
        gate("crowd_refresh_no_critical_failures", len(crowd_critical_failures) == 0, crowd_critical_failures, []),
        gate("crowd_promotion_gate_exists", bool(crowd_promotion_gate) and not crowd_promotion_gate.get("_read_error"), str(crowd_promotion_gate_path), "readable JSON"),
        gate("crowd_promotion_no_paper_execution", crowd_promotion.get("paper_execution_allowed") is False, crowd_promotion.get("paper_execution_allowed"), False),
        gate("crowd_promotion_no_live_execution", crowd_promotion.get("live_execution_allowed") is False, crowd_promotion.get("live_execution_allowed"), False),
        gate(
            "microstructure_book_loop_status_ok",
            microstructure_book_loop_status.get("status") in {"running_book_cycle", "ran_book_cycle"},
            microstructure_book_loop_status.get("status"),
            "running_book_cycle or ran_book_cycle",
        ),
        gate(
            "microstructure_book_loop_pid_alive",
            microstructure_book_loop_pid_alive,
            microstructure_book_loop_status.get("pid"),
            "running process",
        ),
        gate(
            "microstructure_book_loop_fresh",
            microstructure_book_loop_age is not None
            and microstructure_book_loop_age <= args.max_microstructure_book_loop_age_minutes,
            microstructure_book_loop_age,
            f"<= {args.max_microstructure_book_loop_age_minutes} minutes",
        ),
        gate(
            "real_edge_observer_loop_status_ok",
            real_edge_observer_loop_status.get("status")
            in {"running", "sleeping_initial", "running_observer_pulse_cycle", "ran_observer_pulse_cycle"},
            real_edge_observer_loop_status.get("status"),
            "active observer-loop status",
        ),
        gate(
            "real_edge_observer_loop_pid_alive",
            real_edge_observer_loop_pid_alive,
            real_edge_observer_loop_status.get("pid"),
            "running process",
        ),
        gate(
            "real_edge_observer_loop_fresh",
            real_edge_observer_loop_age is not None
            and real_edge_observer_loop_age <= args.max_real_edge_observer_loop_age_minutes,
            real_edge_observer_loop_age,
            f"<= {args.max_real_edge_observer_loop_age_minutes} minutes",
        ),
        *latest_cycle_gates,
    ]
    if backup_required:
        gates.extend(
            [
                gate("daily_backup_loop_exists", bool(backup_loop_status) and not backup_loop_status.get("_read_error"), str(backup_loop_status_path), "readable JSON"),
                gate("daily_backup_loop_status_ok", backup_loop_status.get("status") in {"sleeping", "running_backup"}, backup_loop_status.get("status"), "sleeping or running_backup"),
                gate("daily_backup_loop_pid_alive", backup_loop_pid_alive is True, backup_loop_status.get("pid"), "running process"),
                gate("daily_backup_loop_fresh", backup_loop_age is not None and backup_loop_age <= args.max_backup_age_minutes, backup_loop_age, f"<= {args.max_backup_age_minutes} minutes"),
                gate("daily_backup_last_run_exists", bool(backup_last_run) and not backup_last_run.get("_read_error"), str(backup_last_run_path), "readable JSON"),
                gate("daily_backup_last_run_ok", backup_last_run.get("status") == "completed", backup_last_run.get("status"), "completed"),
                gate("daily_backup_last_run_fresh", backup_last_run_age is not None and backup_last_run_age <= args.max_backup_age_minutes, backup_last_run_age, f"<= {args.max_backup_age_minutes} minutes"),
            ]
        )

    if hard_ok(gates):
        classification = "forward_runtime_healthy_observing"
        next_action = "keep 4H forward loop running; wait for paper-entry outcomes"
    else:
        failed = [item["name"] for item in gates if item.get("severity") == "hard" and not item.get("passed")]
        if any(item.startswith("daily_backup_") for item in failed):
            classification = "forward_runtime_backup_degraded"
            next_action = "inspect or restart the local daily runtime backup loop"
        elif any(item.startswith("crowd_") for item in failed):
            classification = "forward_runtime_crowd_fade_degraded"
            next_action = "inspect crowd-fade loop/refresh status; keep execution locked"
        elif any(item.startswith("latest_cycle_") for item in failed):
            classification = "forward_runtime_strategy_chain_degraded"
            next_action = "inspect non-zero strategy-chain steps in the latest 4H scheduler cycle"
        elif any("fresh" in item or "recent" in item for item in failed):
            classification = "forward_runtime_stale_or_late"
            next_action = "run ops/autostart/Run-ForwardPaperOnce.ps1 or inspect the sleeping loop"
        elif "panel_port_open" in failed:
            classification = "forward_runtime_panel_down"
            next_action = "restart the control panel through ops/autostart/Start-TradingOSRuntime.ps1"
        else:
            classification = "forward_runtime_attention_required"
            next_action = "inspect failed gates before trusting forward observation"

    return {
        "generated_at": now_iso(),
        "boundary": {
            "classification": "forward_runtime_health_local_check_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "network_required": False,
        },
        "inputs": {
            "last_run_status": str(last_run_path),
            "loop_status": str(loop_status_path),
            "control_panel_status": str(control_panel_status_path),
            "scheduler_report": str(scheduler_report_path),
            "signal_card": str(signal_card_path),
            "promotion_gate": str(promotion_gate_path),
            "data_quality": str(data_quality_path),
            "crowd_loop_status": str(crowd_loop_status_path),
            "crowd_last_run_status": str(crowd_last_run_path),
            "crowd_refresh_report": str(crowd_refresh_report_path),
            "crowd_promotion_gate": str(crowd_promotion_gate_path),
            "backup_loop_status": str(backup_loop_status_path),
            "backup_last_run_status": str(backup_last_run_path),
        },
        "thresholds": {
            "max_last_run_age_minutes": args.max_last_run_age_minutes,
            "max_loop_status_age_minutes": args.max_loop_status_age_minutes,
            "panel_host": args.panel_host,
            "panel_port": args.panel_port,
            "max_crowd_loop_age_minutes": args.max_crowd_loop_age_minutes,
            "max_crowd_last_run_age_minutes": args.max_crowd_last_run_age_minutes,
            "max_backup_age_minutes": args.max_backup_age_minutes,
        },
        "observed": {
            "last_run_status": last_run.get("status"),
            "last_run_exit_code": last_run.get("exit_code"),
            "last_run_age_minutes": last_run_age,
            "loop_status": loop_status.get("status"),
            "loop_pid": loop_status.get("pid"),
            "forward_loop_pid_alive": forward_loop_pid_alive,
            "loop_status_age_minutes": loop_status_age,
            "panel_port_open": panel_port_open,
            "scheduler_age_minutes": scheduler_age,
            "signal_card_age_minutes": card_generated_age,
            "trend_historically_rejected": trend_historically_rejected,
            "edge_observer_age_minutes": edge_observer_age,
            "edge_pending_age_minutes": edge_pending_age,
            "edge_scoreboard_age_minutes": edge_scoreboard_age,
            "signal_status": signal_card.get("status"),
            "latest_closed_bar": signal_card.get("latest_closed_bar_ts"),
            "promotion_decision": promotion_gate.get("decision"),
            "promotion_active_filter_allowed": promotion.get("active_filter_allowed"),
            "promotion_live_execution_allowed": promotion.get("live_execution_allowed"),
            "data_quality_classification": dq_summary.get("classification"),
            "crowd_loop_status": crowd_loop_status.get("status"),
            "crowd_loop_pid": crowd_loop_status.get("pid"),
            "crowd_loop_pid_alive": crowd_loop_pid_alive,
            "crowd_loop_age_minutes": crowd_loop_age,
            "crowd_last_run_status": crowd_last_run.get("status"),
            "crowd_last_run_exit_code": crowd_last_run.get("exit_code"),
            "crowd_last_run_age_minutes": crowd_last_run_age,
            "crowd_refresh_decision": crowd_refresh_report.get("decision"),
            "crowd_critical_failed_steps": crowd_critical_failures,
            "crowd_promotion_decision": crowd_promotion_gate.get("decision"),
            "crowd_watch_observer_allowed": crowd_promotion.get("watch_observer_allowed"),
            "crowd_paper_execution_allowed": crowd_promotion.get("paper_execution_allowed"),
            "crowd_live_execution_allowed": crowd_promotion.get("live_execution_allowed"),
            "daily_backup_required": backup_required,
            "daily_backup_loop_status": backup_loop_status.get("status"),
            "daily_backup_loop_pid": backup_loop_status.get("pid"),
            "daily_backup_loop_pid_alive": backup_loop_pid_alive,
            "daily_backup_loop_age_minutes": backup_loop_age,
            "daily_backup_last_run_status": backup_last_run.get("status"),
            "daily_backup_last_run_age_minutes": backup_last_run_age,
            "microstructure_book_loop_status": microstructure_book_loop_status.get("status"),
            "microstructure_book_loop_pid": microstructure_book_loop_status.get("pid"),
            "microstructure_book_loop_pid_alive": microstructure_book_loop_pid_alive,
            "microstructure_book_loop_age_minutes": microstructure_book_loop_age,
            "real_edge_observer_loop_status": real_edge_observer_loop_status.get("status"),
            "real_edge_observer_loop_pid": real_edge_observer_loop_status.get("pid"),
            "real_edge_observer_loop_pid_alive": real_edge_observer_loop_pid_alive,
            "real_edge_observer_loop_age_minutes": real_edge_observer_loop_age,
        },
        "gates": gates,
        "classification": classification,
        "decision": classification,
        "next_action": next_action,
        "can_trade": False,
    }


def render_gate_table(items: list[dict[str, Any]]) -> list[str]:
    lines = ["| gate | pass | severity | actual | required |", "|---|---:|---|---|---|"]
    for item in items:
        lines.append(
            f"| {item.get('name')} | `{item.get('passed')}` | `{item.get('severity')}` | `{item.get('actual')}` | `{item.get('required')}` |"
        )
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    observed = report.get("observed", {})
    lines = [
        "# Forward Runtime Health Check",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Local health check only.",
        "- No private credentials, no exchange account, no orders.",
        "",
        "## Decision",
        "",
        f"- Classification: `{report.get('classification')}`.",
        f"- Next action: `{report.get('next_action')}`.",
        f"- Can trade: `{report.get('can_trade')}`.",
        "",
        "## Observed",
        "",
        f"- Last run: `{observed.get('last_run_status')}` exit `{observed.get('last_run_exit_code')}` age `{observed.get('last_run_age_minutes')}` min.",
        f"- Loop: `{observed.get('loop_status')}` pid `{observed.get('loop_pid')}` alive `{observed.get('forward_loop_pid_alive')}` age `{observed.get('loop_status_age_minutes')}` min.",
        f"- Panel port open: `{observed.get('panel_port_open')}`.",
        f"- Signal: `{observed.get('signal_status')}` latest bar `{observed.get('latest_closed_bar')}`.",
        f"- Promotion: `{observed.get('promotion_decision')}` active `{observed.get('promotion_active_filter_allowed')}` live `{observed.get('promotion_live_execution_allowed')}`.",
        f"- Data quality: `{observed.get('data_quality_classification')}`.",
        f"- Crowd loop: `{observed.get('crowd_loop_status')}` pid `{observed.get('crowd_loop_pid')}` alive `{observed.get('crowd_loop_pid_alive')}` age `{observed.get('crowd_loop_age_minutes')}` min.",
        f"- Crowd refresh: `{observed.get('crowd_last_run_status')}` exit `{observed.get('crowd_last_run_exit_code')}` age `{observed.get('crowd_last_run_age_minutes')}` min.",
        f"- Crowd promotion: `{observed.get('crowd_promotion_decision')}` paper `{observed.get('crowd_paper_execution_allowed')}` live `{observed.get('crowd_live_execution_allowed')}`.",
        f"- Daily backup: required `{observed.get('daily_backup_required')}` loop `{observed.get('daily_backup_loop_status')}` pid `{observed.get('daily_backup_loop_pid')}` alive `{observed.get('daily_backup_loop_pid_alive')}` last `{observed.get('daily_backup_last_run_status')}`.",
        "",
        "## Gates",
        "",
        *render_gate_table(report.get("gates", [])),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check 4H forward runtime health and staleness")
    parser.add_argument("--last-run-status", default="logs/forward_paper_feed/scheduled_task_last_run.json")
    parser.add_argument("--loop-status", default="logs/forward_paper_feed/forward_scheduler_loop_status.json")
    parser.add_argument("--control-panel-status", default="logs/control_panel_autostart_status.json")
    parser.add_argument("--scheduler-report", default="docs/STRATEGY_MIX_FORWARD_SCHEDULER_2026-06-08.json")
    parser.add_argument("--signal-card", default="logs/forward_paper_feed/latest_signal_card.json")
    parser.add_argument("--trend-lock", default="configs/TREND_MIX_FORWARD_LOCK.json")
    parser.add_argument("--edge-observer", default="docs/EDGE_FORWARD_RANGE_OBSERVER_2026-06-18.json")
    parser.add_argument("--edge-pending", default="docs/EDGE_FORWARD_PENDING_WATCH_2026-06-18.json")
    parser.add_argument("--edge-scoreboard", default="docs/EDGE_FORWARD_RANGE_SCOREBOARD_2026-06-18.json")
    parser.add_argument("--promotion-gate", default="docs/OI_GUARD_PROMOTION_GATE_2026-06-15.json")
    parser.add_argument("--data-quality", default="docs/OI_FUNDING_DATA_QUALITY_2026-06-15.json")
    parser.add_argument("--crowd-loop-status", default="logs/forward_paper_feed/crowd_fade_observer_loop_status.json")
    parser.add_argument("--crowd-last-run-status", default="logs/forward_paper_feed/crowd_fade_refresh_last_run.json")
    parser.add_argument("--crowd-refresh-report", default="docs/CROWD_FADE_REFRESH_PACK_2026-06-19.json")
    parser.add_argument("--crowd-promotion-gate", default="docs/CROWD_FADE_POSITIONING_PROMOTION_GATE_2026-06-19.json")
    parser.add_argument("--backup-loop-status", default="logs/runtime_backup/daily_drive_backup_loop_status.json")
    parser.add_argument("--backup-last-run-status", default="logs/runtime_backup/daily_drive_backup_last_run.json")
    parser.add_argument(
        "--microstructure-book-loop-status",
        default="logs/cross_venue_microstructure/microstructure_book_loop_status.json",
    )
    parser.add_argument(
        "--real-edge-observer-loop-status",
        default="logs/real_edge_observer/real_edge_observer_pulse_loop_status.json",
    )
    parser.add_argument("--panel-host", default="127.0.0.1")
    parser.add_argument("--panel-port", type=int, default=8765)
    parser.add_argument("--connect-timeout-s", type=float, default=1.0)
    parser.add_argument("--max-last-run-age-minutes", type=float, default=330.0)
    parser.add_argument("--max-loop-status-age-minutes", type=float, default=330.0)
    parser.add_argument("--max-crowd-loop-age-minutes", type=float, default=75.0)
    parser.add_argument("--max-crowd-last-run-age-minutes", type=float, default=90.0)
    parser.add_argument("--max-backup-age-minutes", type=float, default=1500.0)
    parser.add_argument("--max-microstructure-book-loop-age-minutes", type=float, default=5.0)
    parser.add_argument("--max-real-edge-observer-loop-age-minutes", type=float, default=35.0)
    parser.add_argument("--out-prefix", default="docs/FORWARD_RUNTIME_HEALTH_2026-06-16")
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "classification": report.get("classification"),
                "last_run_age_minutes": report.get("observed", {}).get("last_run_age_minutes"),
                "panel_port_open": report.get("observed", {}).get("panel_port_open"),
                "promotion": report.get("observed", {}).get("promotion_decision"),
                "json": str(json_path),
                "md": str(md_path),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

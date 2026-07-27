#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]

FAMILY_OWNERS = {
    "alt_breadth_dislocation": "forward_scheduler",
    "derivatives_squeeze_disagreement": "forward_scheduler",
    "force_order_liquidation_context": "force_order_watchdog",
    "bybit_liquidation_canonical_reversal_v5r2": "real_edge_pulse",
    "cross_venue_liquidation_receipt_leadership": "real_edge_pulse",
    "exogenous_liquidity_regime": "real_edge_pulse",
    "liquidation_book_replenishment": "real_edge_pulse",
    "deribit_options_skew_forward": "deribit_runtime",
}


def now_iso(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_seconds(value: Any, now: datetime) -> float | None:
    parsed = parse_ts(value)
    return max(0.0, (now - parsed).total_seconds()) if parsed else None


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def scheduler_check(
    owner: str,
    *,
    real_edge_status: dict[str, Any],
    forward_status: dict[str, Any],
    force_order_status: dict[str, Any],
    force_order_collector_status: dict[str, Any],
    deribit_report: dict[str, Any],
    observed_at: datetime,
    process_checker: Callable[[int], bool],
) -> tuple[dict[str, bool], dict[str, Any]]:
    if owner == "deribit_runtime":
        runtime = deribit_report.get("runtime") if isinstance(deribit_report.get("runtime"), dict) else {}
        checks = {
            "runtime_audit_present": bool(deribit_report),
            "runtime_components_passed": runtime.get("all_components_passed") is True,
            "runtime_not_blocked": "blocked" not in str(deribit_report.get("decision") or ""),
            "runtime_can_trade_false": deribit_report.get("can_trade") is False,
        }
        return checks, {"decision": deribit_report.get("decision")}

    if owner == "force_order_watchdog":
        status = force_order_status
        collector = force_order_collector_status
        allowed = {
            "running",
            "running_watchdog_cycle",
            "ran_watchdog_cycle",
            "skipped_existing_liquidation_force_order_watchdog_loop",
        }
        pid = int(status.get("pid") or 0)
        sleep_seconds = int(status.get("sleep_seconds") or 0)
        status_age = age_seconds(status.get("ts") or status.get("updated_at"), observed_at)
        collector_pid = int(collector.get("pid") or 0)
        collector_cycle_seconds = int(collector.get("cycle_seconds") or 0)
        collector_age = age_seconds(collector.get("ts") or collector.get("updated_at"), observed_at)
        collector_allowed = {"running", "running_collector_cycle", "ran_collector_cycle", "sleeping_initial"}
        checks = {
            "watchdog_status_present": bool(status),
            "watchdog_status_allowed": status.get("status") in allowed,
            "watchdog_pid_alive": process_checker(pid),
            "watchdog_fresh": status_age is not None and sleep_seconds > 0 and status_age <= sleep_seconds + 1800,
            "watchdog_last_exit_success": status.get("exit_code") in {None, 0},
            "watchdog_live_trading_locked": status.get("live_trading_locked") is True,
            "watchdog_data_collector_only": status.get("data_collector_only") is True,
            "collector_status_present": bool(collector),
            "collector_status_allowed": collector.get("status") in collector_allowed,
            "collector_pid_alive": process_checker(collector_pid),
            "collector_fresh": collector_age is not None
            and collector_cycle_seconds > 0
            and collector_age <= collector_cycle_seconds + 600,
            "collector_last_exit_success": collector.get("exit_code") in {None, 0},
            "collector_live_trading_locked": collector.get("live_trading_locked") is True,
            "collector_data_collector_only": collector.get("data_collector_only") is True,
        }
        return checks, {
            "watchdog": {
                "pid": pid,
                "status": status.get("status"),
                "age_seconds": status_age,
                "sleep_seconds": sleep_seconds,
            },
            "collector": {
                "pid": collector_pid,
                "status": collector.get("status"),
                "age_seconds": collector_age,
                "cycle_seconds": collector_cycle_seconds,
            },
        }

    status = real_edge_status if owner == "real_edge_pulse" else forward_status
    allowed = (
        {"ran_observer_pulse_cycle", "running_observer_pulse_cycle", "running_observer_pulse", "sleeping"}
        if owner == "real_edge_pulse"
        else {"sleeping", "running", "running_once"}
    )
    pid = int(status.get("pid") or 0)
    sleep_seconds = int(status.get("sleep_seconds") or 0)
    freshness_limit = sleep_seconds + (1800 if owner == "real_edge_pulse" else 3600)
    status_age = age_seconds(status.get("ts") or status.get("updated_at"), observed_at)
    checks = {
        "scheduler_status_present": bool(status),
        "scheduler_status_allowed": status.get("status") in allowed,
        "scheduler_pid_alive": process_checker(pid),
        "scheduler_fresh": status_age is not None and sleep_seconds > 0 and status_age <= freshness_limit,
        "scheduler_live_trading_locked": status.get("live_trading_locked") is True,
    }
    if owner == "real_edge_pulse":
        checks.update(
            {
                "scheduler_signals_false": status.get("signals_allowed") is False,
                "scheduler_paper_entries_false": status.get("paper_entries_allowed") is False,
                "scheduler_orders_false": status.get("orders_allowed") is False,
            }
        )
    return checks, {"pid": pid, "status": status.get("status"), "age_seconds": status_age, "sleep_seconds": sleep_seconds}


def build_report(
    frontier_path: Path,
    real_edge_status_path: Path,
    forward_status_path: Path,
    force_order_status_path: Path,
    force_order_collector_status_path: Path,
    deribit_report_path: Path,
    *,
    now: datetime | None = None,
    process_checker: Callable[[int], bool] = process_alive,
) -> dict[str, Any]:
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    frontier = read_json(frontier_path)
    real_edge_status = read_json(real_edge_status_path)
    forward_status = read_json(forward_status_path)
    force_order_status = read_json(force_order_status_path)
    force_order_collector_status = read_json(force_order_collector_status_path)
    deribit_report = read_json(deribit_report_path)
    active = [
        item
        for item in frontier.get("families") or []
        if isinstance(item, dict) and item.get("status") == "observer_only_waiting_forward"
    ]
    rows: list[dict[str, Any]] = []
    for item in active:
        family = str(item.get("family") or "")
        owner = FAMILY_OWNERS.get(family)
        report_path = resolve_path(str(item.get("path") or ""))
        observer_report = read_json(report_path)
        report_age = age_seconds(observer_report.get("generated_at"), observed_at)
        maximum_report_age = 1800 if owner in {"real_edge_pulse", "deribit_runtime"} else 18_000
        owner_checks, scheduler = scheduler_check(
            owner or "unknown",
            real_edge_status=real_edge_status,
            forward_status=forward_status,
            force_order_status=force_order_status,
            force_order_collector_status=force_order_collector_status,
            deribit_report=deribit_report,
            observed_at=observed_at,
            process_checker=process_checker,
        ) if owner else ({"known_runtime_owner": False}, {})
        checks = {
            "known_runtime_owner": owner is not None,
            "frontier_can_trade_false": item.get("can_trade") is False,
            "observer_report_present": bool(observer_report),
            "observer_report_can_trade_false": observer_report.get("can_trade") is False,
            "observer_report_fresh": report_age is not None and report_age <= maximum_report_age,
            **owner_checks,
        }
        rows.append(
            {
                "family": family,
                "owner": owner,
                "observer_report": portable(report_path),
                "observer_report_age_seconds": round(report_age, 3) if report_age is not None else None,
                "scheduler": scheduler,
                "checks": checks,
                "failed_checks": [name for name, passed in checks.items() if not passed],
                "covered": all(checks.values()),
                "can_trade": False,
            }
        )
    failures = [item for item in rows if not item["covered"]]
    decision = "active_observer_runtime_coverage_pass" if active and not failures else "active_observer_runtime_coverage_blocked"
    return {
        "schema_version": 1,
        "generated_at": now_iso(observed_at),
        "tool": "tools/active_observer_runtime_coverage_audit.py",
        "decision": decision,
        "summary": {
            "active_observer_families": len(active),
            "covered_families": len(rows) - len(failures),
            "blocked_families": len(failures),
            "known_owner_families": sum(item["owner"] is not None for item in rows),
        },
        "rows": rows,
        "blockers": [f"{item['family']}:{','.join(item['failed_checks'])}" for item in failures],
        "sources": {
            "frontier": portable(frontier_path),
            "real_edge_status": portable(real_edge_status_path),
            "forward_status": portable(forward_status_path),
            "force_order_status": portable(force_order_status_path),
            "force_order_collector_status": portable(force_order_collector_status_path),
            "deribit_runtime_audit": portable(deribit_report_path),
        },
        "runtime_boundary": {
            "audit_only": True,
            "process_mutation_allowed": False,
            "automatic_restart_allowed": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
        "orders_allowed": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Active Observer Runtime Coverage",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Covered: `{report['summary']['covered_families']}/{report['summary']['active_observer_families']}`",
        "- Can trade: `false`",
        "",
        "| Family | Owner | Covered | Failed checks |",
        "|---|---|---|---|",
    ]
    for item in report["rows"]:
        lines.append(f"| `{item['family']}` | `{item['owner']}` | `{item['covered']}` | `{','.join(item['failed_checks'])}` |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed runtime coverage audit for every active observer family")
    parser.add_argument("--frontier", default="docs/STRATEGY_RESEARCH_FRONTIER_MATRIX_2026-07-03_AFTER_OBSERVER_PULSE.json")
    parser.add_argument("--real-edge-status", default="logs/real_edge_observer/real_edge_observer_pulse_loop_status.json")
    parser.add_argument("--forward-status", default="logs/forward_paper_feed/forward_scheduler_loop_status.json")
    parser.add_argument(
        "--force-order-status",
        default="logs/liquidation_force_order/liquidation_force_order_watchdog_loop_status.json",
    )
    parser.add_argument(
        "--force-order-collector-status",
        default="logs/liquidation_force_order/liquidation_force_order_loop_status.json",
    )
    parser.add_argument("--deribit-audit", default="docs/DERIBIT_OPTIONS_V3_DATA_LAYER_AUDIT_2026-07-16.json")
    parser.add_argument("--out-prefix", default="docs/ACTIVE_OBSERVER_RUNTIME_COVERAGE_2026-07-13")
    args = parser.parse_args()
    report = build_report(
        resolve_path(args.frontier),
        resolve_path(args.real_edge_status),
        resolve_path(args.forward_status),
        resolve_path(args.force_order_status),
        resolve_path(args.force_order_collector_status),
        resolve_path(args.deribit_audit),
    )
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], **report["summary"], "can_trade": False}, indent=2))
    return 0 if report["decision"].endswith("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())

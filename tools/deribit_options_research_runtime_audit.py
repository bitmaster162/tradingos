#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
COLLECTOR_ROOT = ROOT / "HANDOFF" / "INCOMING" / "codex" / "20260711_deribit_options_surface_collector"
READINESS_ROOT = ROOT / "HANDOFF" / "INCOMING" / "codex" / "20260711_deribit_options_readiness_guard"
OBSERVER_ROOT = ROOT / "HANDOFF" / "INCOMING" / "codex" / "20260712_deribit_options_skew_forward"


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
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def age_seconds(value: Any, observed_at: datetime) -> float | None:
    parsed = parse_ts(value)
    return round(max(0.0, (observed_at - parsed).total_seconds()), 3) if parsed else None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
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


def check_values(checks: dict[str, bool]) -> dict[str, Any]:
    return {"passed": all(checks.values()), "checks": checks, "failed": [name for name, value in checks.items() if not value]}


def self_integrity() -> dict[str, Any]:
    collector_lock = read_json(COLLECTOR_ROOT / "IMMUTABLE_LOCK_V2.json")
    readiness_lock = read_json(READINESS_ROOT / "IMMUTABLE_LOCK.json")
    observer_lock = read_json(OBSERVER_ROOT / "IMMUTABLE_LOCK.json")
    collector_checks = {
        "lock_present": bool(collector_lock),
        "script_hash": collector_lock.get("script_sha256") == sha256_file(COLLECTOR_ROOT / "collector.py"),
        "contract_hash": collector_lock.get("contract_sha256") == sha256_file(COLLECTOR_ROOT / "CONTRACT.json"),
        "orders_false": collector_lock.get("orders_allowed") is False,
        "can_trade_false": collector_lock.get("can_trade") is False,
    }
    readiness_checks = {
        "lock_present": bool(readiness_lock),
        "script_hash": readiness_lock.get("script_sha256") == sha256_file(READINESS_ROOT / "monitor.py"),
        "contract_hash": readiness_lock.get("contract_sha256") == sha256_file(READINESS_ROOT / "CONTRACT.json"),
        "orders_false": readiness_lock.get("orders_allowed") is False,
        "can_trade_false": readiness_lock.get("can_trade") is False,
    }
    observer_checks = {
        "lock_present": bool(observer_lock),
        "script_hash": observer_lock.get("script_sha256") == sha256_file(OBSERVER_ROOT / "observer.py"),
        "prereg_hash": observer_lock.get("prereg_sha256") == sha256_file(OBSERVER_ROOT / "PREREG.json"),
        "collector_lock_hash": observer_lock.get("collector_lock_sha256") == sha256_file(COLLECTOR_ROOT / "IMMUTABLE_LOCK_V2.json"),
        "readiness_contract_hash": observer_lock.get("readiness_contract_sha256") == sha256_file(READINESS_ROOT / "CONTRACT.json"),
        "retuning_false": observer_lock.get("retuning_allowed") is False,
        "orders_false": observer_lock.get("orders_allowed") is False,
        "can_trade_false": observer_lock.get("can_trade") is False,
    }
    payload = {
        "collector": check_values(collector_checks),
        "readiness": check_values(readiness_checks),
        "observer": check_values(observer_checks),
    }
    payload["passed"] = all(item["passed"] for item in payload.values())
    return payload


def active_seal_check() -> dict[str, Any]:
    lock = read_json(ROOT / "configs" / "ACTIVE_SOURCE_INTEGRITY_LOCK.json")
    files = lock.get("files") if isinstance(lock.get("files"), dict) else {}
    expected = [
        "HANDOFF/INCOMING/codex/20260711_deribit_options_surface_collector/collector.py",
        "HANDOFF/INCOMING/codex/20260711_deribit_options_surface_collector/CONTRACT.json",
        "HANDOFF/INCOMING/codex/20260711_deribit_options_surface_collector/IMMUTABLE_LOCK_V2.json",
        "HANDOFF/INCOMING/codex/20260711_deribit_options_readiness_guard/monitor.py",
        "HANDOFF/INCOMING/codex/20260711_deribit_options_readiness_guard/CONTRACT.json",
        "HANDOFF/INCOMING/codex/20260711_deribit_options_readiness_guard/IMMUTABLE_LOCK.json",
        "HANDOFF/INCOMING/codex/20260712_deribit_options_skew_forward/observer.py",
        "HANDOFF/INCOMING/codex/20260712_deribit_options_skew_forward/PREREG.json",
        "HANDOFF/INCOMING/codex/20260712_deribit_options_skew_forward/IMMUTABLE_LOCK.json",
    ]
    checks = {}
    for relative in expected:
        entry = files.get(relative) if isinstance(files.get(relative), dict) else {}
        checks[relative] = entry.get("sha256") == sha256_file(ROOT / relative)
    result = check_values(checks)
    result["review_id"] = lock.get("review_id")
    return result


def runtime_component(
    *,
    name: str,
    runtime_dir: Path,
    launcher_path: Path,
    allowed_loop_statuses: set[str],
    allowed_decisions: set[str],
    observed_at: datetime,
    maximum_age_seconds: float,
    process_checker: Callable[[int], bool],
) -> dict[str, Any]:
    loop = read_json(runtime_dir / "loop_status.json")
    latest = read_json(runtime_dir / "LATEST.json")
    launcher = read_json(launcher_path)
    pid = int(loop.get("pid") or 0)
    report_age = age_seconds(latest.get("generated_at"), observed_at)
    checks = {
        "process_alive": process_checker(pid),
        "loop_status_allowed": str(loop.get("status")) in allowed_loop_statuses,
        "loop_can_trade_false": loop.get("can_trade") is False,
        "latest_decision_allowed": str(latest.get("decision")) in allowed_decisions,
        "latest_lock_verified": latest.get("lock_verified") is True,
        "latest_can_trade_false": latest.get("can_trade") is False,
        "latest_fresh": report_age is not None and report_age <= maximum_age_seconds,
        "launcher_present": bool(launcher),
        "launcher_status_allowed": str(launcher.get("status")) in {"already_running", "started"},
        "launcher_can_trade_false": launcher.get("can_trade") is False,
    }
    if name == "readiness":
        checks["collector_integrity_verified"] = bool((latest.get("collector_integrity") or {}).get("passed"))
    if name == "observer":
        checks["collector_integrity_verified"] = latest.get("collector_integrity_verified") is True
    result = check_values(checks)
    result.update(
        {
            "pid": pid or None,
            "loop_status": loop.get("status"),
            "decision": latest.get("decision"),
            "report_age_seconds": report_age,
            "launcher_status": launcher.get("status"),
            "can_trade": False,
        }
    )
    return result


def classify_decision(*, integrity_ok: bool, sealed: bool, runtime_ok: bool, readiness_ready: bool, outcomes_ready: bool) -> str:
    if not integrity_ok or not sealed:
        return "deribit_options_stack_integrity_blocked"
    if not runtime_ok:
        return "deribit_options_stack_runtime_or_freshness_blocked"
    if not readiness_ready:
        return "deribit_options_stack_forward_collecting_readiness"
    if not outcomes_ready:
        return "deribit_options_skew_forward_collecting"
    return "deribit_options_skew_forward_terminal_review_required"


def build_report(
    *,
    observed_at: datetime | None = None,
    maximum_age_seconds: float = 900.0,
    process_checker: Callable[[int], bool] = pid_alive,
) -> dict[str, Any]:
    observed = (observed_at or now_utc()).astimezone(timezone.utc)
    integrity = self_integrity()
    seal = active_seal_check()
    collector = runtime_component(
        name="collector",
        runtime_dir=COLLECTOR_ROOT / "runtime_v2",
        launcher_path=ROOT / "logs" / "deribit_options_surface_collector_autostart_status.json",
        allowed_loop_statuses={"running_once", "sleeping", "sleeping_after_fetch_failure"},
        allowed_decisions={"deribit_options_surface_snapshot_healthy", "deribit_options_surface_snapshot_degraded"},
        observed_at=observed,
        maximum_age_seconds=maximum_age_seconds,
        process_checker=process_checker,
    )
    readiness = runtime_component(
        name="readiness",
        runtime_dir=READINESS_ROOT / "runtime",
        launcher_path=ROOT / "logs" / "deribit_options_readiness_guard_autostart_status.json",
        allowed_loop_statuses={"running_once", "sleeping"},
        allowed_decisions={"deribit_options_forward_data_collecting", "deribit_options_ready_for_preregistration_review"},
        observed_at=observed,
        maximum_age_seconds=maximum_age_seconds,
        process_checker=process_checker,
    )
    observer = runtime_component(
        name="observer",
        runtime_dir=OBSERVER_ROOT / "runtime",
        launcher_path=ROOT / "logs" / "deribit_options_skew_forward_autostart_status.json",
        allowed_loop_statuses={"running_once", "sleeping"},
        allowed_decisions={"deribit_options_skew_waiting_readiness_gate", "deribit_options_skew_forward_collecting"},
        observed_at=observed,
        maximum_age_seconds=maximum_age_seconds,
        process_checker=process_checker,
    )
    readiness_report = read_json(READINESS_ROOT / "runtime" / "LATEST.json")
    observer_report = read_json(OBSERVER_ROOT / "runtime" / "LATEST.json")
    summary_rows = observer_report.get("summary") if isinstance(observer_report.get("summary"), dict) else {}
    outcomes_ready = bool(summary_rows) and all(bool(item.get("threshold_ready")) for item in summary_rows.values())
    runtime_ok = collector["passed"] and readiness["passed"] and observer["passed"]
    decision = classify_decision(
        integrity_ok=bool(integrity["passed"]),
        sealed=bool(seal["passed"]),
        runtime_ok=runtime_ok,
        readiness_ready=readiness_report.get("research_gate_ready") is True,
        outcomes_ready=outcomes_ready,
    )
    metrics = readiness_report.get("metrics") if isinstance(readiness_report.get("metrics"), dict) else {}
    return {
        "schema_version": 1,
        "generated_at": observed.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "decision": decision,
        "family": "DERIBIT_OPTIONS_SKEW_IV_DOWNSIDE_CONTINUATION",
        "can_trade": False,
        "integrity": integrity,
        "active_source_seal": seal,
        "runtime": {"collector": collector, "readiness": readiness, "observer": observer, "all_components_passed": runtime_ok},
        "forward_progress": {
            "readiness_gate_ready": readiness_report.get("research_gate_ready") is True,
            "span_days": metrics.get("span_days"),
            "healthy_slots": metrics.get("healthy_slots"),
            "minimum_span_days": 7.0,
            "minimum_healthy_slots": 1800,
            "scheduled_coverage": metrics.get("scheduled_coverage"),
            "events_total": observer_report.get("events_total"),
            "outcomes_total": observer_report.get("outcomes_total"),
            "outcome_summary": summary_rows,
        },
        "boundary": {
            "public_data_only": True,
            "forward_only": True,
            "retuning_allowed": False,
            "alerts_allowed": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "next_action": "keep_collecting_without_retune" if "collecting" in decision else "manual_integrity_or_terminal_review",
    }


def render_markdown(report: dict[str, Any]) -> str:
    progress = report["forward_progress"]
    runtime = report["runtime"]
    lines = [
        "# Deribit Options Research Runtime Audit",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `false`",
        f"- Self-integrity: `{report['integrity']['passed']}`",
        f"- Active source seal: `{report['active_source_seal']['passed']}`",
        f"- Runtime components: `{runtime['all_components_passed']}`",
        "",
        "## Components",
        "",
        "| Component | PID | Status | Decision | Age seconds | Passed |",
        "|---|---:|---|---|---:|---|",
    ]
    for name in ("collector", "readiness", "observer"):
        item = runtime[name]
        lines.append(
            f"| `{name}` | `{item['pid']}` | `{item['loop_status']}` | `{item['decision']}` | "
            f"`{item['report_age_seconds']}` | `{item['passed']}` |"
        )
    lines.extend(
        [
            "",
            "## Forward Progress",
            "",
            f"- Span: `{progress['span_days']}` / `7.0` days.",
            f"- Healthy slots: `{progress['healthy_slots']}` / `1800`.",
            f"- Scheduled coverage: `{progress['scheduled_coverage']}`.",
            f"- Events/outcomes: `{progress['events_total']}` / `{progress['outcomes_total']}`.",
            "- No historical backfill, retuning, alerts, signals, paper entries or orders are permitted.",
            "- Keep collecting until the immutable readiness gate opens; then observer outcomes remain manual-review only.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the self-locked Deribit options research runtime")
    parser.add_argument("--maximum-age-seconds", type=float, default=900.0)
    parser.add_argument("--out-prefix", default="docs/DERIBIT_OPTIONS_RESEARCH_RUNTIME_AUDIT_2026-07-12")
    args = parser.parse_args()
    report = build_report(maximum_age_seconds=args.maximum_age_seconds)
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "runtime_ok": report["runtime"]["all_components_passed"],
                "span_days": report["forward_progress"]["span_days"],
                "healthy_slots": report["forward_progress"]["healthy_slots"],
                "events_total": report["forward_progress"]["events_total"],
                "can_trade": False,
                "out": portable(out.with_suffix(".json")),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if "blocked" not in report["decision"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

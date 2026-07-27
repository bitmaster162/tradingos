#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def make_degraded_fixture(health: dict[str, Any], scenario: str) -> dict[str, Any]:
    fixture = deepcopy(health)
    fixture["generated_at"] = now_iso()
    if scenario == "crowd_fade":
        classification = "forward_runtime_crowd_fade_degraded"
        gate_name = "crowd_loop_fresh"
        observed_key = "crowd_loop_age_minutes"
        observed_value: Any = 999.0
        required = "<= 75 minutes"
    else:
        classification = "forward_runtime_panel_down"
        gate_name = "panel_port_open"
        observed_key = "panel_port_open"
        observed_value = False
        required = "connectable"
    fixture["classification"] = classification
    fixture["decision"] = classification
    fixture["next_action"] = f"drill only: verify {scenario} runtime-health Telegram degraded alert path"
    fixture["can_trade"] = False
    observed = fixture.setdefault("observed", {})
    if isinstance(observed, dict):
        observed[observed_key] = observed_value
        observed["promotion_active_filter_allowed"] = False
        observed["promotion_live_execution_allowed"] = False
    gates = fixture.setdefault("gates", [])
    if isinstance(gates, list):
        matched = False
        for gate in gates:
            if isinstance(gate, dict) and gate.get("name") == gate_name:
                gate["passed"] = False
                gate["actual"] = "drill_forced_false"
                gate["required"] = required
                gate["severity"] = "hard"
                matched = True
        if not matched:
            gates.append(
                {
                    "name": gate_name,
                    "passed": False,
                    "actual": "drill_forced_false",
                    "required": required,
                    "severity": "hard",
                }
            )
    return fixture


def run_notify(
    *,
    health_path: Path,
    state_path: Path,
    out_prefix: Path,
    timeout_s: int,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "tools/forward_runtime_health_telegram_notify.py",
        "--health-json-path",
        str(health_path),
        "--state-path",
        str(state_path),
        "--out-prefix",
        str(out_prefix),
        "--dry-run",
        "--force",
    ]
    started = time.time()
    env = dict(os.environ)
    env.setdefault("TELEGRAM_BOT_TOKEN", "DRILL_DRY_RUN_TOKEN")
    env.setdefault("TELEGRAM_CHAT_ID", "DRILL_DRY_RUN_CHAT")
    proc = subprocess.run(command, cwd=str(ROOT), text=True, capture_output=True, timeout=timeout_s, env=env)
    report = read_json(out_prefix.with_suffix(".json"), {})
    return {
        "command": command,
        "exit_code": proc.returncode,
        "duration_s": round(time.time() - started, 3),
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
        "notify_report": report if isinstance(report, dict) else {},
    }


def display_path(value: Any) -> str:
    if value is None:
        return "None"
    path = Path(str(value))
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    degraded = report.get("degraded_notify", {})
    recovered = report.get("recovery_notify", {})
    degraded_report = degraded.get("notify_report") if isinstance(degraded, dict) else {}
    recovered_report = recovered.get("notify_report") if isinstance(recovered, dict) else {}
    return "\n".join(
        [
            "# Forward Runtime Health Incident Drill",
            "",
            f"Generated: `{report.get('generated_at')}`",
            "",
            "## Boundary",
            "",
            "- Drill only.",
            "- Uses a synthetic degraded health fixture.",
            "- Uses `--dry-run --force`; no Telegram message is sent.",
            "- No orders, no private exchange credentials, no trading permission.",
            "",
            "## Result",
            "",
            f"- Decision: `{report.get('decision')}`.",
            f"- Scenario: `{report.get('scenario')}`.",
            f"- Can trade: `{report.get('can_trade')}`.",
            f"- Degraded notify exit: `{degraded.get('exit_code') if isinstance(degraded, dict) else None}`.",
            f"- Degraded notify kind: `{degraded_report.get('kind') if isinstance(degraded_report, dict) else None}`.",
            f"- Degraded notify decision: `{degraded_report.get('decision') if isinstance(degraded_report, dict) else None}`.",
            f"- Recovery notify exit: `{recovered.get('exit_code') if isinstance(recovered, dict) else None}`.",
            f"- Recovery notify kind: `{recovered_report.get('kind') if isinstance(recovered_report, dict) else None}`.",
            f"- Recovery notify decision: `{recovered_report.get('decision') if isinstance(recovered_report, dict) else None}`.",
            "",
            "## Files",
            "",
            f"- Degraded fixture: `{display_path(report.get('degraded_fixture'))}`.",
            f"- Degraded notify report: `{display_path(report.get('degraded_notify_report'))}`.",
            f"- Recovery notify report: `{display_path(report.get('recovery_notify_report'))}`.",
            f"- Drill state: `{display_path(report.get('state_path'))}`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run incident drill for runtime-health Telegram alerts")
    parser.add_argument("--health-json-path", default="docs/FORWARD_RUNTIME_HEALTH_2026-06-16.json")
    parser.add_argument("--work-dir", default="_dl/runtime_drills")
    parser.add_argument("--out-prefix", default="docs/FORWARD_RUNTIME_HEALTH_INCIDENT_DRILL_2026-06-16")
    parser.add_argument("--timeout-s", type=int, default=30)
    parser.add_argument("--scenario", choices=("crowd_fade", "panel"), default="crowd_fade")
    args = parser.parse_args()

    health_path = resolve_path(args.health_json_path)
    work_dir = resolve_path(args.work_dir)
    out_prefix = resolve_path(args.out_prefix)
    health = read_json(health_path, {})
    if not isinstance(health, dict) or not health:
        report = {
            "generated_at": now_iso(),
            "decision": "blocked_missing_health_report",
            "health_path": str(health_path),
            "can_trade": False,
        }
        write_json(out_prefix.with_suffix(".json"), report)
        out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    work_dir.mkdir(parents=True, exist_ok=True)
    degraded_fixture_path = work_dir / "forward_runtime_health_degraded_fixture.json"
    state_path = work_dir / "forward_runtime_health_incident_drill_state.json"
    degraded_notify_prefix = resolve_path("docs/FORWARD_RUNTIME_HEALTH_INCIDENT_DRILL_DEGRADED_NOTIFY_2026-06-16")
    recovery_notify_prefix = resolve_path("docs/FORWARD_RUNTIME_HEALTH_INCIDENT_DRILL_RECOVERY_NOTIFY_2026-06-16")

    if state_path.exists():
        state_path.unlink()
    degraded = make_degraded_fixture(health, args.scenario)
    write_json(degraded_fixture_path, degraded)

    degraded_result = run_notify(
        health_path=degraded_fixture_path,
        state_path=state_path,
        out_prefix=degraded_notify_prefix,
        timeout_s=args.timeout_s,
    )
    recovery_result = run_notify(
        health_path=health_path,
        state_path=state_path,
        out_prefix=recovery_notify_prefix,
        timeout_s=args.timeout_s,
    )
    degraded_report = degraded_result.get("notify_report") if isinstance(degraded_result, dict) else {}
    recovery_report = recovery_result.get("notify_report") if isinstance(recovery_result, dict) else {}
    degraded_ok = (
        degraded_result.get("exit_code") == 0
        and isinstance(degraded_report, dict)
        and degraded_report.get("kind") == "runtime_degraded"
        and degraded_report.get("decision") == "dry_run_ready"
    )
    recovery_ok = (
        recovery_result.get("exit_code") == 0
        and isinstance(recovery_report, dict)
        and recovery_report.get("kind") == "runtime_recovered"
        and recovery_report.get("decision") == "dry_run_ready"
    )
    decision = "incident_drill_passed" if degraded_ok and recovery_ok else "incident_drill_failed"
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "runtime_health_incident_drill_dry_run_only",
            "can_trade": False,
            "sends_orders": False,
            "sends_telegram": False,
            "uses_private_credentials": False,
        },
        "health_path": str(health_path),
        "degraded_fixture": str(degraded_fixture_path),
        "state_path": str(state_path),
        "degraded_notify_report": str(degraded_notify_prefix.with_suffix(".json")),
        "recovery_notify_report": str(recovery_notify_prefix.with_suffix(".json")),
        "degraded_notify": degraded_result,
        "recovery_notify": recovery_result,
        "decision": decision,
        "scenario": args.scenario,
        "can_trade": False,
    }
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(out_prefix.with_suffix(".json")),
                "md": str(out_prefix.with_suffix(".md")),
                "decision": decision,
                "degraded_kind": degraded_report.get("kind") if isinstance(degraded_report, dict) else None,
                "recovery_kind": recovery_report.get("kind") if isinstance(recovery_report, dict) else None,
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if decision == "incident_drill_passed" else 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

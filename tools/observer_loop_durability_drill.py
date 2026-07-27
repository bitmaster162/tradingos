#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OBSERVER_GATES = (
    "microstructure_book_loop_status_ok",
    "microstructure_book_loop_pid_alive",
    "microstructure_book_loop_fresh",
    "real_edge_observer_loop_status_ok",
    "real_edge_observer_loop_pid_alive",
    "real_edge_observer_loop_fresh",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc)}
    return payload if isinstance(payload, dict) else {"_read_error": "json_root_not_object"}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def degraded_fixture(health: dict[str, Any]) -> dict[str, Any]:
    fixture = deepcopy(health)
    fixture["generated_at"] = now_iso()
    fixture["classification"] = "observer_loop_durability_drill_degraded"
    fixture["decision"] = "observer_loop_durability_drill_degraded"
    fixture["can_trade"] = False
    seen: set[str] = set()
    gates = fixture.setdefault("gates", [])
    if not isinstance(gates, list):
        gates = []
        fixture["gates"] = gates
    for gate in gates:
        if not isinstance(gate, dict) or gate.get("name") not in OBSERVER_GATES:
            continue
        seen.add(str(gate["name"]))
        gate["passed"] = False
        gate["actual"] = "synthetic_observer_failure"
        gate["severity"] = "hard"
    for name in OBSERVER_GATES:
        if name not in seen:
            gates.append(
                {
                    "name": name,
                    "passed": False,
                    "actual": "synthetic_observer_failure",
                    "required": "healthy observer loop",
                    "severity": "hard",
                }
            )
    return fixture


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Observer Loop Durability Drill",
            "",
            f"Generated: `{report.get('generated_at')}`",
            f"Decision: `{report.get('decision')}`",
            f"Can trade: `{report.get('can_trade')}`",
            "",
            "## Boundary",
            "",
            "- Synthetic health failure only.",
            "- Repair script runs with `-DryRun`.",
            "- No process restart, Telegram message, signal, paper entry or order.",
            "",
            "## Result",
            "",
            f"- Repair decision: `{report.get('repair', {}).get('decision')}`.",
            f"- Expected gates: `{report.get('expected_gates')}`.",
            f"- Matched gates: `{report.get('repair', {}).get('matched_repairable_gates')}`.",
            f"- Missing gates: `{report.get('missing_matched_gates')}`.",
            f"- Restart budget: `{report.get('repair', {}).get('max_repairs_in_window')}` per `{report.get('repair', {}).get('window_minutes')}` minutes.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic dry-run proof for observer-loop bounded self-heal.")
    parser.add_argument("--health-report", default="docs/FORWARD_RUNTIME_HEALTH_2026-07-10_AFTER_OBSERVER_DURABILITY.json")
    parser.add_argument("--work-dir", default="_dl/runtime_drills/observer_loop_durability")
    parser.add_argument("--out-prefix", default="docs/OBSERVER_LOOP_DURABILITY_DRILL_2026-07-10")
    args = parser.parse_args()

    health_path = resolve_path(args.health_report)
    work_dir = resolve_path(args.work_dir)
    out_prefix = resolve_path(args.out_prefix)
    health = read_json(health_path)
    fixture_path = work_dir / "synthetic_observer_failure_health.json"
    state_path = work_dir / "repair_state.json"
    status_path = work_dir / "repair_status.json"
    fixture = degraded_fixture(health)
    write_json(fixture_path, fixture)

    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "ops" / "autostart" / "Repair-TradingOSRuntime.ps1"),
        "-HealthReport",
        str(fixture_path),
        "-StatePath",
        str(state_path),
        "-StatusPath",
        str(status_path),
        "-MaxRepairs",
        "3",
        "-WindowMinutes",
        "60",
        "-DryRun",
    ]
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=30, check=False)
    repair = read_json(status_path)
    matched = set(repair.get("matched_repairable_gates") or [])
    missing = [name for name in OBSERVER_GATES if name not in matched]
    passed = completed.returncode == 0 and repair.get("decision") == "dry_run_restart_ready" and not missing
    report = {
        "generated_at": now_iso(),
        "tool": "observer_loop_durability_drill",
        "decision": "observer_loop_durability_drill_passed" if passed else "observer_loop_durability_drill_failed",
        "health_report": str(health_path),
        "fixture_path": str(fixture_path),
        "expected_gates": list(OBSERVER_GATES),
        "missing_matched_gates": missing,
        "repair": repair,
        "process": {
            "exit_code": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        },
        "runtime_boundary": {
            "synthetic_only": True,
            "dry_run": True,
            "restarts_processes": False,
            "sends_telegram": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
        },
        "can_trade": False,
    }
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "repair_decision": repair.get("decision"),
                "matched_gates": sorted(matched),
                "missing_gates": missing,
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

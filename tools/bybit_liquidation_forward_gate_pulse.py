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


def write_md(path: Path, report: dict[str, Any]) -> None:
    sample = report.get("sample_progress", {})
    watcher = report.get("watcher", {})
    lines = [
        "# Bybit Liquidation Forward Gate Pulse",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Review action: `{report.get('review_action')}`",
        f"- Watcher transition: `{watcher.get('transition_kind')}`",
        f"- Watcher notification action: `{watcher.get('notification_action')}`",
        f"- can_trade: `{str(report.get('can_trade')).lower()}`",
        f"- orders_allowed: `{str(report.get('orders_allowed')).lower()}`",
        "",
        "## Sample",
        "",
        f"- Event bars: `{sample.get('event_bars_current')}/{sample.get('event_bars_required')}`",
        f"- Event bar deficit: `{sample.get('event_bars_deficit')}`",
        f"- Liquidation events: `{sample.get('liquidation_events_current')}`",
        f"- Resolved records: `{sample.get('resolved_records')}`",
        f"- Sample ready: `{sample.get('sample_ready')}`",
        f"- Resolution ready: `{sample.get('resolution_ready')}`",
        "",
        "## Next Action",
        "",
        str(report.get("next_action") or ""),
        "",
        "## Boundary",
        "",
        "Pulse only runs the safe gate runner and watcher. It does not send Telegram, emit trade signals, open paper entries or send orders.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_step(name: str, args: list[str]) -> dict[str, Any]:
    started = now_iso()
    proc = subprocess.run(
        [sys.executable, *args],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "name": name,
        "started_at": started,
        "finished_at": now_iso(),
        "exit_code": proc.returncode,
        "command": " ".join([sys.executable, *args]),
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
    }


def classify(failed_steps: list[dict[str, Any]], gate: dict[str, Any], watcher: dict[str, Any]) -> tuple[str, str]:
    if failed_steps:
        return "bybit_gate_pulse_failed_step", "Inspect failed step output before rerunning."
    review_action = str(gate.get("review_action") or "")
    watcher_decision = str(watcher.get("decision") or "")
    if review_action == "manual_pass_review":
        return "bybit_gate_pulse_manual_pass_review_required", "Inspect manual review pack. Do not open paper/live trading from this pulse."
    if review_action == "manual_tombstone_review":
        return "bybit_gate_pulse_manual_tombstone_review_required", "Review data integrity, then tombstone without retune if failure is real."
    if "progress_changed" in watcher_decision:
        return "bybit_gate_pulse_progress_changed_waiting_sample", "Progress changed but review is still waiting. Keep collecting."
    if "no_change" in watcher_decision:
        return "bybit_gate_pulse_no_change_waiting_sample", "No state transition. Keep collecting."
    return "bybit_gate_pulse_waiting_sample", "Keep collecting until gate runner returns manual review state."


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Bybit liquidation gate runner and watcher as one safe pulse.")
    parser.add_argument("--runner-prefix", default="docs/BYBIT_LIQUIDATION_FORWARD_GATE_RUNNER_2026-07-03")
    parser.add_argument("--watcher-prefix", default="docs/BYBIT_LIQUIDATION_FORWARD_GATE_WATCHER_2026-07-03")
    parser.add_argument("--out-prefix", default="docs/BYBIT_LIQUIDATION_FORWARD_GATE_PULSE_2026-07-03")
    args = parser.parse_args()

    steps = [
        run_step(
            "gate_runner",
            [
                "tools/bybit_liquidation_forward_gate_runner.py",
                "--out-prefix",
                args.runner_prefix,
            ],
        ),
        run_step(
            "gate_watcher",
            [
                "tools/bybit_liquidation_forward_gate_watcher.py",
                "--gate-report",
                f"{args.runner_prefix}.json",
                "--out-prefix",
                args.watcher_prefix,
            ],
        ),
    ]
    gate = read_json(f"{args.runner_prefix}.json")
    watcher = read_json(f"{args.watcher_prefix}.json")
    failed_steps = [step for step in steps if step.get("exit_code") != 0]
    decision, next_action = classify(failed_steps, gate, watcher)
    sample = gate.get("sample_progress") if isinstance(gate.get("sample_progress"), dict) else {}

    report = {
        "generated_at": now_iso(),
        "tool": "tools/bybit_liquidation_forward_gate_pulse.py",
        "decision": decision,
        "review_action": gate.get("review_action"),
        "next_action": next_action,
        "can_trade": False,
        "orders_allowed": False,
        "steps": steps,
        "failed_steps": failed_steps,
        "sample_progress": sample,
        "horizon_progress": gate.get("horizon_progress") if isinstance(gate.get("horizon_progress"), dict) else {},
        "runner": {
            "decision": gate.get("decision"),
            "review_action": gate.get("review_action"),
            "path": f"{args.runner_prefix}.json",
        },
        "watcher": {
            "decision": watcher.get("decision"),
            "transition_kind": watcher.get("transition_kind"),
            "notification_action": watcher.get("notification_action"),
            "path": f"{args.watcher_prefix}.json",
        },
        "boundary": {
            "pulse_only": True,
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
                "review_action": report["review_action"],
                "watcher_decision": report["watcher"]["decision"],
                "event_bars": sample.get("event_bars_current"),
                "event_bars_required": sample.get("event_bars_required"),
                "out": report["out"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failed_steps else 0


if __name__ == "__main__":
    raise SystemExit(main())


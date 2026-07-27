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
    if not p.exists():
        return {"_missing": portable(p)}
    try:
        value = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc), "_path": portable(p)}
    return value if isinstance(value, dict) else {"_read_error": "not_object", "_path": portable(p)}


def command_spec(script: str, out_prefix: str, extra: list[str] | None = None) -> dict[str, Any]:
    cmd = [sys.executable, script, "--out-prefix", out_prefix]
    if extra:
        cmd.extend(extra)
    return {"script": script, "out_prefix": out_prefix, "command": cmd}


def run_command(spec: dict[str, Any], timeout_s: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            spec["command"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        status = "success" if proc.returncode == 0 else "failed"
        return {
            "script": spec["script"],
            "out_prefix": spec["out_prefix"],
            "status": status,
            "exit_code": proc.returncode,
            "duration_s": round(time.monotonic() - started, 3),
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "script": spec["script"],
            "out_prefix": spec["out_prefix"],
            "status": "timeout",
            "exit_code": None,
            "duration_s": round(time.monotonic() - started, 3),
            "stdout": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
        }


def report_decision(path: str) -> dict[str, Any]:
    payload = read_json(path)
    return {
        "path": path,
        "decision": payload.get("decision"),
        "can_trade": payload.get("can_trade"),
        "ready": payload.get("ready_for_preregistered_research"),
        "summary": payload.get("summary"),
        "events": payload.get("events", {}).get("events") if isinstance(payload.get("events"), dict) else None,
        "sealed": payload.get("sealed"),
    }


def build_pulse(args: argparse.Namespace, steps: list[dict[str, Any]]) -> dict[str, Any]:
    failed_steps = [step for step in steps if step["status"] != "success"]
    outputs = {
        "liquidation_data_quality": report_decision("docs/LIQUIDATION_FORCE_ORDER_DATA_QUALITY_2026-06-30.json"),
        "liquidation_first_event": report_decision("docs/LIQUIDATION_FORCE_ORDER_FIRST_EVENT_TRIGGER_2026-06-30.json"),
        "microstructure_snapshot_pack": report_decision("docs/CROSS_VENUE_MICROSTRUCTURE_SEALED_SNAPSHOT_PACK_2026-06-30.json"),
        "edge_tombstone_registry": report_decision("docs/EDGE_TOMBSTONE_REGISTRY_2026-06-30.json"),
        "unified_readiness_matrix": report_decision("docs/UNIFIED_READINESS_MATRIX_2026-06-30.json"),
    }
    unified = read_json("docs/UNIFIED_READINESS_MATRIX_2026-06-30.json")
    blockers = []
    summary = unified.get("summary") if isinstance(unified.get("summary"), dict) else {}
    for blocker in summary.get("hard_blockers") or []:
        blockers.append(blocker)
    if failed_steps:
        blockers.append("pulse_step_failed")
    if outputs["unified_readiness_matrix"].get("can_trade") is not False:
        blockers.append("unexpected_can_trade_state")
    decision = "readiness_pulse_no_trade_edge_unproven"
    if failed_steps:
        decision = "readiness_pulse_degraded_step_failure"
    return {
        "generated_at": now_iso(),
        "tool": "tools/tradingos_readiness_pulse.py",
        "decision": decision,
        "can_trade": False,
        "boundary": {
            "observability_only": True,
            "sends_orders": False,
            "uses_private_credentials": False,
            "network_required": False,
        },
        "steps": steps,
        "failed_steps": failed_steps,
        "outputs": outputs,
        "blockers": sorted(set(str(item) for item in blockers)),
        "next_action": "keep collectors running; wait for microstructure seal and real forceOrder events before preregistered research",
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# TradingOS Readiness Pulse",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `{str(report['can_trade']).lower()}`",
        f"- Failed steps: `{len(report['failed_steps'])}`",
        "",
        "## Outputs",
        "",
        "| Layer | Decision | Can Trade | Extra |",
        "|---|---|---:|---|",
    ]
    for name, item in report["outputs"].items():
        extra = ""
        if item.get("events") is not None:
            extra = f"events={item.get('events')}"
        elif item.get("sealed") is not None:
            extra = f"sealed={item.get('sealed')}"
        elif item.get("summary") is not None:
            extra = str(item.get("summary"))[:180]
        lines.append(f"| `{name}` | `{item.get('decision')}` | `{str(item.get('can_trade')).lower()}` | `{extra}` |")
    lines.extend(
        [
            "",
            "## Steps",
            "",
            "| Script | Status | Exit | Duration |",
            "|---|---|---:|---:|",
        ]
    )
    for step in report["steps"]:
        lines.append(
            f"| `{step['script']}` | `{step['status']}` | `{step['exit_code']}` | `{step['duration_s']}` |"
        )
    lines.extend(["", "## Blockers", ""])
    for blocker in report["blockers"]:
        lines.append(f"- `{blocker}`")
    lines.extend(["", "## Next Action", "", f"- {report['next_action']}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a safe observability pulse across current TradingOS readiness layers")
    parser.add_argument("--timeout-s", type=int, default=60)
    parser.add_argument("--out-prefix", default="docs/TRADINGOS_READINESS_PULSE_2026-06-30")
    args = parser.parse_args()

    specs = [
        command_spec("tools/liquidation_force_order_data_quality.py", "docs/LIQUIDATION_FORCE_ORDER_DATA_QUALITY_2026-06-30"),
        command_spec("tools/liquidation_force_order_first_event_trigger.py", "docs/LIQUIDATION_FORCE_ORDER_FIRST_EVENT_TRIGGER_2026-06-30"),
        command_spec("tools/microstructure_sealed_snapshot_pack.py", "docs/CROSS_VENUE_MICROSTRUCTURE_SEALED_SNAPSHOT_PACK_2026-06-30"),
        command_spec("tools/edge_tombstone_registry.py", "docs/EDGE_TOMBSTONE_REGISTRY_2026-06-30"),
        command_spec("tools/unified_readiness_matrix.py", "docs/UNIFIED_READINESS_MATRIX_2026-06-30"),
    ]
    steps = [run_command(spec, args.timeout_s) for spec in specs]
    report = build_pulse(args, steps)
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "failed_steps": len(report["failed_steps"]),
                "blockers": report["blockers"],
                "out": portable(out.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not report["failed_steps"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

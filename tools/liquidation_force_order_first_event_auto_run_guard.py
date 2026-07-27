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


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc), "_path": portable(path)}
    return payload if isinstance(payload, dict) else {"_read_error": "not_object", "_path": portable(path)}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_command(command: list[str], timeout_s: int) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "exit_code": None,
            "timed_out": True,
            "stdout": (exc.stdout or "")[-12000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-12000:] if isinstance(exc.stderr, str) else "",
        }
    return {
        "command": command,
        "exit_code": result.returncode,
        "timed_out": False,
        "stdout": result.stdout[-12000:],
        "stderr": result.stderr[-12000:],
    }


def event_identity(dq: dict[str, Any]) -> str:
    events = dq.get("events") if isinstance(dq.get("events"), dict) else {}
    research = (
        events.get("preregistered_sample")
        if isinstance(events.get("preregistered_sample"), dict)
        else events.get("research_universe")
        if isinstance(events.get("research_universe"), dict)
        else events
    )
    first_event = research.get("first_event_time") or "unknown_first"
    return str(first_event)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Liquidation ForceOrder First-Event Auto-Run Guard",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `false`",
        f"- Events: `{report['events']}`",
        f"- Event identity: `{report['event_identity']}`",
        f"- Pipeline ran: `{str(report['pipeline_ran']).lower()}`",
        "",
        "## Boundary",
        "",
        "- Local guard only.",
        "- Runs data-quality and research pipeline only.",
        "- Does not create alerts, intents, paper entries or orders.",
        "- Idempotent: after the first successful pipeline run, later event-count growth never rearms it.",
        "",
        "## Data Quality",
        "",
        f"- Decision: `{report.get('data_quality', {}).get('decision')}`",
        f"- Hard failures: `{', '.join(report.get('data_quality_hard_failures', [])) or 'none'}`",
        "",
        "## Pipeline",
        "",
        f"- Decision: `{report.get('pipeline', {}).get('decision') if report.get('pipeline') else None}`",
        f"- Output: `{report.get('pipeline_output')}`",
        "",
        "## Next Action",
        "",
        f"- {report['next_action']}",
        "",
    ]
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    dq_prefix = resolve_path(args.data_quality_prefix)
    dq_path = dq_prefix.with_suffix(".json")
    state_path = resolve_path(args.state_path)
    out_prefix = resolve_path(args.out_prefix)
    pipeline_prefix = resolve_path(args.pipeline_out_prefix)

    dq_run = None
    if args.run_data_quality or not dq_path.exists():
        dq_command = [
            sys.executable,
            str(ROOT / "tools" / "liquidation_force_order_data_quality.py"),
            "--data-dir",
            args.data_dir,
            "--min-events-for-research",
            str(args.min_events_for_research),
            "--out-prefix",
            portable(dq_prefix),
        ]
        dq_run = run_command(dq_command, timeout_s=args.timeout_seconds)
    dq = read_json(dq_path)
    state = read_json(state_path)

    hard_failures = [
        str(item.get("name"))
        for item in dq.get("hard_failures", [])
        if isinstance(item, dict) and item.get("name")
    ]
    all_events_block = dq.get("events") if isinstance(dq.get("events"), dict) else {}
    events_block = (
        all_events_block.get("preregistered_sample")
        if isinstance(all_events_block.get("preregistered_sample"), dict)
        else all_events_block.get("research_universe")
        if isinstance(all_events_block.get("research_universe"), dict)
        else all_events_block
    )
    events = int(events_block.get("events") or 0)
    identity = event_identity(dq)
    pipeline_run = None
    pipeline = None
    pipeline_ran = False

    if dq_run and dq_run.get("exit_code") not in {0, None}:
        decision = "first_event_auto_run_guard_blocked_data_quality_runtime"
        next_action = "fix data-quality runtime before guard can evaluate first forceOrder event"
    elif hard_failures:
        decision = "first_event_auto_run_guard_blocked_data_quality_hard_fail"
        next_action = "fix forceOrder data-quality hard failures before running pipeline"
    elif state.get("pipeline_ran") is True:
        decision = "first_event_auto_run_guard_already_ran"
        pipeline = read_json(resolve_path(str(state.get("pipeline_output") or "")).with_suffix(".json")) if state.get("pipeline_output") else None
        pipeline_ran = True
        next_action = "continue collecting until minimum event sample is ready; do not rerun first-event pipeline automatically"
    elif events <= 0:
        decision = "first_event_auto_run_guard_waiting_real_event"
        next_action = "keep collector running; guard will auto-run after the first post-lock research-universe event appears"
    else:
        pipeline_command = [
            sys.executable,
            str(ROOT / "tools" / "force_order_liquidation_research_pipeline.py"),
            "--data-dir",
            args.data_dir,
            "--symbols",
            args.symbols,
            "--interval",
            args.interval,
            "--horizons",
            args.horizons,
            "--min-events-for-research",
            str(args.min_events_for_research),
            "--min-event-bars-for-research",
            str(args.min_event_bars_for_research),
            "--min-context-bars",
            str(args.min_context_bars),
            "--out-prefix",
            portable(pipeline_prefix),
        ]
        pipeline_run = run_command(pipeline_command, timeout_s=args.timeout_seconds)
        pipeline = read_json(pipeline_prefix.with_suffix(".json"))
        pipeline_ran = pipeline_run.get("exit_code") == 0
        if pipeline_ran:
            state = {
                "first_pipeline_event_identity": identity,
                "pipeline_ran": True,
                "pipeline_ran_at": now_iso(),
                "events_at_run": events,
                "first_event_time": events_block.get("first_event_time"),
                "last_event_time": events_block.get("last_event_time"),
                "data_quality": portable(dq_path),
                "pipeline_output": portable(pipeline_prefix.with_suffix(".json")),
            }
            write_json(state_path, state)
            decision = "first_event_auto_run_guard_pipeline_ran"
            next_action = "review first-event pipeline output; continue collecting until preregistered sample thresholds are reached"
        else:
            decision = "first_event_auto_run_guard_pipeline_failed"
            next_action = "fix pipeline runtime before treating forceOrder context as research-ready"

    return {
        "generated_at": now_iso(),
        "tool": "tools/liquidation_force_order_first_event_auto_run_guard.py",
        "decision": decision,
        "can_trade": False,
        "boundary": {
            "guard_only": True,
            "research_pipeline_only": True,
            "sends_orders": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "events": events,
        "event_identity": identity,
        "pipeline_ran": pipeline_ran,
        "inputs": {
            "data_dir": args.data_dir,
            "symbols": args.symbols,
            "interval": args.interval,
            "horizons": args.horizons,
            "min_events_for_research": args.min_events_for_research,
            "min_event_bars_for_research": args.min_event_bars_for_research,
            "min_context_bars": args.min_context_bars,
        },
        "data_quality": dq,
        "data_quality_run": dq_run,
        "data_quality_hard_failures": hard_failures,
        "pipeline": pipeline,
        "pipeline_run": pipeline_run,
        "pipeline_output": portable(pipeline_prefix.with_suffix(".json")),
        "state_path": portable(state_path),
        "state": state,
        "next_action": next_action,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Idempotent first-event auto-run guard for forceOrder research pipeline")
    parser.add_argument("--data-dir", default="data/live/liquidations/binance_force_order")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BCHUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--horizons", default="1,2,4,8")
    parser.add_argument("--min-events-for-research", type=int, default=500)
    parser.add_argument("--min-event-bars-for-research", type=int, default=50)
    parser.add_argument("--min-context-bars", type=int, default=15)
    parser.add_argument("--data-quality-prefix", default="docs/LIQUIDATION_FORCE_ORDER_DATA_QUALITY_2026-06-30")
    parser.add_argument("--pipeline-out-prefix", default="docs/FORCE_ORDER_LIQUIDATION_RESEARCH_PIPELINE_FIRST_EVENT_AUTO_2026-07-01")
    parser.add_argument("--state-path", default="logs/liquidation_force_order/first_event_auto_run_guard_state.json")
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_FORCE_ORDER_FIRST_EVENT_AUTO_RUN_GUARD_2026-07-01")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--run-data-quality", action="store_true")
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "events": report["events"],
                "pipeline_ran": report["pipeline_ran"],
                "out": portable(out_prefix.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

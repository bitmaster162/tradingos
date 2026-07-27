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
    horizons = report.get("horizon_progress", {})
    lines = [
        "# Bybit Liquidation Forward Gate Runner",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Review action: `{report.get('review_action')}`",
        f"- can_trade: `{str(report.get('can_trade')).lower()}`",
        f"- orders_allowed: `{str(report.get('orders_allowed')).lower()}`",
        "",
        "## Sample Progress",
        "",
        f"- Event bars: `{sample.get('event_bars_current')}/{sample.get('event_bars_required')}`",
        f"- Event bar deficit: `{sample.get('event_bars_deficit')}`",
        f"- Liquidation events: `{sample.get('liquidation_events_current')}/{sample.get('liquidation_events_required')}`",
        f"- Resolved records: `{sample.get('resolved_records')}`",
        f"- Sample ready: `{sample.get('sample_ready')}`",
        f"- Resolution ready: `{sample.get('resolution_ready')}`",
        "",
        "## Horizon Progress",
        "",
        "| Horizon | Current | Required | Deficit | Mean after cost bps | Winrate positive | Cost buffer |",
        "|---:|---:|---:|---:|---:|---:|---|",
    ]
    for key, row in horizons.items():
        if not isinstance(row, dict):
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    str(key),
                    str(row.get("current")),
                    str(row.get("required")),
                    str(row.get("deficit")),
                    str(row.get("mean_after_cost_bps")),
                    str(row.get("winrate_positive_pct")),
                    str(row.get("passes_cost_buffer")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Next Action",
            "",
            str(report.get("next_action") or ""),
            "",
            "## Boundary",
            "",
            "This runner only refreshes observer/progress/review reports. It does not emit trade signals, does not open paper entries and does not send orders.",
        ]
    )
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


def compact_sample(progress: dict[str, Any]) -> dict[str, Any]:
    sample = progress.get("sample_progress") if isinstance(progress.get("sample_progress"), dict) else {}
    event_bars = sample.get("event_bars") if isinstance(sample.get("event_bars"), dict) else {}
    liquidation_events = sample.get("liquidation_events") if isinstance(sample.get("liquidation_events"), dict) else {}
    return {
        "event_bars_current": event_bars.get("current"),
        "event_bars_required": event_bars.get("required"),
        "event_bars_deficit": event_bars.get("deficit"),
        "liquidation_events_current": liquidation_events.get("current"),
        "liquidation_events_required": liquidation_events.get("required"),
        "resolved_records": sample.get("resolved_records"),
        "sample_ready": sample.get("sample_ready") is True,
        "resolution_ready": sample.get("resolution_ready") is True,
    }


def classify(failed_steps: list[dict[str, Any]], review: dict[str, Any]) -> tuple[str, str, str]:
    if failed_steps:
        return (
            "bybit_forward_gate_runner_failed_step",
            "blocked",
            "Inspect failed step stdout/stderr before rerunning.",
        )
    review_action = str(review.get("review_action") or "")
    if review_action == "manual_pass_review":
        return (
            "bybit_forward_gate_runner_manual_pass_review_required",
            review_action,
            "Prepare manual integrity review. Do not open paper/live trading from this runner.",
        )
    if review_action == "manual_tombstone_review":
        return (
            "bybit_forward_gate_runner_manual_tombstone_review_required",
            review_action,
            "Review whether failure is data-related; otherwise tombstone without retune.",
        )
    if review_action == "wait":
        return (
            "bybit_forward_gate_runner_waiting_sample",
            review_action,
            "Keep collecting until post-lock event bars and every horizon meet minimum resolved sample.",
        )
    return (
        "bybit_forward_gate_runner_review_state_unknown",
        review_action or "unknown",
        "Inspect review pack output before taking next action.",
    )


def build_report(args: argparse.Namespace, steps: list[dict[str, Any]]) -> dict[str, Any]:
    observer = read_json(f"{args.observer_prefix}.json")
    progress = read_json(f"{args.progress_prefix}.json")
    review = read_json(f"{args.review_prefix}.json")
    live_focus = read_json(f"{args.live_focus_prefix}.json")
    failed_steps = [step for step in steps if step.get("exit_code") != 0]
    decision, review_action, next_action = classify(failed_steps, review)
    report = {
        "generated_at": now_iso(),
        "tool": "tools/bybit_liquidation_forward_gate_runner.py",
        "decision": decision,
        "review_action": review_action,
        "next_action": next_action,
        "can_trade": False,
        "orders_allowed": False,
        "steps": steps,
        "failed_steps": failed_steps,
        "observer_decision": observer.get("decision"),
        "progress_decision": progress.get("decision"),
        "review_decision": review.get("decision"),
        "live_focus_decision": live_focus.get("decision"),
        "sample_progress": compact_sample(progress),
        "horizon_progress": progress.get("horizon_progress") if isinstance(progress.get("horizon_progress"), dict) else {},
        "blockers": progress.get("blockers") if isinstance(progress.get("blockers"), list) else [],
        "source_reports": {
            "observer": f"{args.observer_prefix}.json",
            "progress": f"{args.progress_prefix}.json",
            "review": f"{args.review_prefix}.json",
            "live_focus": f"{args.live_focus_prefix}.json",
        },
        "boundary": {
            "runner_only": True,
            "emits_trade_signals": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "can_trade": False,
        },
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh Bybit liquidation forward observer/progress/review as a single safe gate run.")
    parser.add_argument("--lock", default="configs/BYBIT_LIQUIDATION_FORWARD_OBSERVER_LOCK_2026-07-02.json")
    parser.add_argument("--observer-prefix", default="docs/BYBIT_LIQUIDATION_FORWARD_OBSERVER_2026-07-02")
    parser.add_argument("--progress-prefix", default="docs/BYBIT_LIQUIDATION_FORWARD_PROGRESS_2026-07-02")
    parser.add_argument("--review-prefix", default="docs/BYBIT_LIQUIDATION_FORWARD_REVIEW_PACK_2026-07-02")
    parser.add_argument("--live-focus-prefix", default="docs/LIVE_DATA_EDGE_FOCUS_SUMMARY_2026-07-03")
    parser.add_argument("--microstructure-unblock", default="docs/MICROSTRUCTURE_UNBLOCK_STATUS_2026-07-03_AFTER_HEALTH_REFRESH.json")
    parser.add_argument("--devil-audit", default="docs/FULL_SYSTEM_DEVIL_AUDIT_2026-07-03_AFTER_MICROSTRUCTURE_HEALTH_REFRESH.json")
    parser.add_argument("--out-prefix", default="docs/BYBIT_LIQUIDATION_FORWARD_GATE_RUNNER_2026-07-03")
    args = parser.parse_args()

    steps = [
        run_step(
            "observer",
            [
                "tools/bybit_liquidation_forward_observer.py",
                "--lock",
                args.lock,
                "--out-prefix",
                args.observer_prefix,
            ],
        ),
        run_step(
            "progress",
            [
                "tools/bybit_liquidation_forward_progress_monitor.py",
                "--out-prefix",
                args.progress_prefix,
            ],
        ),
        run_step(
            "review",
            [
                "tools/bybit_liquidation_forward_review_pack.py",
                "--out-prefix",
                args.review_prefix,
            ],
        ),
        run_step(
            "live_focus",
            [
                "tools/live_data_edge_focus_summary.py",
                "--microstructure-unblock",
                args.microstructure_unblock,
                "--devil-audit",
                args.devil_audit,
                "--out-prefix",
                args.live_focus_prefix,
            ],
        ),
    ]

    report = build_report(args, steps)
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
                "event_bars": report["sample_progress"].get("event_bars_current"),
                "event_bars_required": report["sample_progress"].get("event_bars_required"),
                "blockers": report["blockers"],
                "out": report["out"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if report["failed_steps"] else 0


if __name__ == "__main__":
    raise SystemExit(main())


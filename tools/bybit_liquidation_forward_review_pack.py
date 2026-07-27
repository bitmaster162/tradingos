#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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


def as_bool(value: Any) -> bool:
    return value is True


def classify(forward: dict[str, Any], progress: dict[str, Any]) -> tuple[str, str, str]:
    forward_decision = str(forward.get("decision") or "")
    sample_progress = progress.get("sample_progress") if isinstance(progress.get("sample_progress"), dict) else {}
    sample_ready = as_bool(sample_progress.get("sample_ready"))
    resolution_ready = as_bool(sample_progress.get("resolution_ready"))

    if forward_decision == "bybit_liquidation_forward_observer_passed_for_manual_review":
        return (
            "bybit_forward_review_pack_pass_candidate_manual_review_required",
            "manual_pass_review",
            "Review sample integrity, horizon consistency, execution realism and correlation before any paper-design discussion.",
        )
    if forward_decision == "bybit_liquidation_forward_observer_failed_gate_for_tombstone_review":
        return (
            "bybit_forward_review_pack_tombstone_candidate_manual_review_required",
            "manual_tombstone_review",
            "Review whether the failure is a data issue; otherwise tombstone the forward lock without retune.",
        )
    if not sample_ready:
        return (
            "bybit_forward_review_pack_waiting_sample",
            "wait",
            "Wait for minimum post-lock sample thresholds before judging the candidate.",
        )
    if not resolution_ready:
        return (
            "bybit_forward_review_pack_waiting_horizon_resolution",
            "wait",
            "Wait for enough future bars to resolve every locked horizon before judging outcome quality.",
        )
    return (
        "bybit_forward_review_pack_ready_for_observer_rerun",
        "rerun_observer",
        "Rerun forward observer; the progress monitor says sample and horizon resolution are ready.",
    )


def review_checklist(action: str) -> list[dict[str, Any]]:
    common = [
        {
            "check": "runtime_boundary",
            "question": "Do all reports keep can_trade=false and orders_allowed=false?",
            "required": True,
        },
        {
            "check": "no_parameter_changes",
            "question": "Were context, direction, horizons and thresholds unchanged from the accepted lock?",
            "required": True,
        },
        {
            "check": "real_feed_only",
            "question": "Are rows from real Bybit allLiquidation feed with matched price bars only?",
            "required": True,
        },
        {
            "check": "sample_independence",
            "question": "Are event bars independent enough, not one liquidation cluster split into tiny duplicate observations?",
            "required": True,
        },
        {
            "check": "execution_reality",
            "question": "Would the effect survive fees, spread, slippage, latency and no look-ahead execution timing?",
            "required": True,
        },
    ]
    if action == "manual_pass_review":
        return common + [
            {
                "check": "horizon_consistency",
                "question": "Do at least the locked minimum positive horizons pass after cost buffer, without relying on one outlier symbol?",
                "required": True,
            },
            {
                "check": "paper_design_scope",
                "question": "If approved, is the next step only paper-design review, not live or automated entry?",
                "required": True,
            },
        ]
    if action == "manual_tombstone_review":
        return common + [
            {
                "check": "data_issue_exception",
                "question": "Is there a concrete data-quality issue that invalidates the failed gate?",
                "required": False,
            },
            {
                "check": "no_retune_after_failure",
                "question": "If no data issue exists, is the candidate tombstoned with no threshold/horizon/context retune?",
                "required": True,
            },
        ]
    return common


def render_markdown(report: dict[str, Any]) -> str:
    progress = report["progress_summary"]
    lines = [
        "# Bybit Liquidation Forward Review Pack",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Review action: `{report['review_action']}`",
        "- Can trade: `false`",
        "",
        "## Current State",
        "",
        f"- Forward observer decision: `{report['forward_observer_decision']}`",
        f"- Progress decision: `{report['progress_decision']}`",
        f"- Event bars: `{progress['event_bars_current']}` / `{progress['event_bars_required']}`",
        f"- Liquidation events: `{progress['liquidation_events_current']}` / `{progress['liquidation_events_required']}`",
        f"- Sample ready: `{progress['sample_ready']}`",
        f"- Resolution ready: `{progress['resolution_ready']}`",
        "",
        "## Horizon State",
        "",
        "| Horizon | N | Required | Ready | Mean after cost | Pass |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for horizon, item in report["horizon_progress"].items():
        lines.append(
            f"| {horizon} | `{item.get('current')}` | `{item.get('required')}` | `{item.get('ready')}` | "
            f"`{item.get('mean_after_cost_bps')}` | `{item.get('passes_cost_buffer')}` |"
        )
    lines.extend(["", "## Manual Checklist", ""])
    for item in report["manual_checklist"]:
        lines.append(f"- `{item['check']}` required `{item['required']}`: {item['question']}")
    lines.extend(
        [
            "",
            "## Decision Policy",
            "",
            "- `wait`: no judgement; keep collecting.",
            "- `manual_pass_review`: review can discuss paper-design only, never live execution.",
            "- `manual_tombstone_review`: if no data issue, tombstone without retuning opened forward data.",
            "- `rerun_observer`: rerun observer because progress says the locked sample/resolution gates are ready.",
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}",
            "",
            "## Boundary",
            "",
            "- Review pack only.",
            "- Does not emit trade signals, open paper entries, send orders or grant permission.",
            "- `can_trade=false`.",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    forward = read_json(args.forward_observer)
    progress = read_json(args.progress)
    lock = read_json(args.lock)
    decision, action, next_action = classify(forward, progress)
    sample = progress.get("sample_progress") if isinstance(progress.get("sample_progress"), dict) else {}
    event_bars = sample.get("event_bars") if isinstance(sample.get("event_bars"), dict) else {}
    liquidation_events = sample.get("liquidation_events") if isinstance(sample.get("liquidation_events"), dict) else {}
    horizon = progress.get("horizon_progress") if isinstance(progress.get("horizon_progress"), dict) else {}
    hypothesis = lock.get("hypothesis") if isinstance(lock.get("hypothesis"), dict) else {}
    return {
        "generated_at": now_iso(),
        "tool": "tools/bybit_liquidation_forward_review_pack.py",
        "decision": decision,
        "review_action": action,
        "can_trade": False,
        "orders_allowed": False,
        "lock_id": lock.get("lock_id"),
        "hypothesis": hypothesis,
        "forward_observer_decision": forward.get("decision"),
        "progress_decision": progress.get("decision"),
        "progress_summary": {
            "event_bars_current": event_bars.get("current"),
            "event_bars_required": event_bars.get("required"),
            "liquidation_events_current": liquidation_events.get("current"),
            "liquidation_events_required": liquidation_events.get("required"),
            "resolved_records": sample.get("resolved_records"),
            "sample_ready": sample.get("sample_ready"),
            "resolution_ready": sample.get("resolution_ready"),
        },
        "horizon_progress": horizon,
        "manual_checklist": review_checklist(action),
        "source_reports": {
            "forward_observer": args.forward_observer,
            "progress": args.progress,
            "lock": args.lock,
        },
        "next_action": next_action,
        "boundary": {
            "review_pack_only": True,
            "emits_trade_signals": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "can_trade": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual review pack for the accepted Bybit liquidation forward observer.")
    parser.add_argument("--forward-observer", default="docs/BYBIT_LIQUIDATION_FORWARD_OBSERVER_2026-07-02.json")
    parser.add_argument("--progress", default="docs/BYBIT_LIQUIDATION_FORWARD_PROGRESS_2026-07-02.json")
    parser.add_argument("--lock", default="configs/BYBIT_LIQUIDATION_FORWARD_OBSERVER_LOCK_2026-07-02.json")
    parser.add_argument("--out-prefix", default="docs/BYBIT_LIQUIDATION_FORWARD_REVIEW_PACK_2026-07-02")
    args = parser.parse_args()

    report = build_report(args)
    out = resolve_path(args.out_prefix)
    write_json(out.with_suffix(".json"), report)
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "review_action": report["review_action"],
                "next_action": report["next_action"],
                "out": portable(out.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

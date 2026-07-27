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


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"_missing": True, "_path": portable(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc), "_path": portable(path)}
    return payload if isinstance(payload, dict) else {"_read_error": "json_not_object", "_path": portable(path)}


def list_names(rows: Any) -> list[str]:
    if not isinstance(rows, list):
        return []
    names: list[str] = []
    for row in rows:
        if isinstance(row, dict) and row.get("name"):
            names.append(str(row["name"]))
        elif isinstance(row, str):
            names.append(row)
    return names


def classify_microstructure(snapshot: dict[str, Any], health: dict[str, Any], queue: dict[str, Any]) -> dict[str, Any]:
    health_ok = health.get("classification") == "cross_venue_microstructure_healthy_collecting"
    failed_hard = health.get("failed_hard_gates") if isinstance(health.get("failed_hard_gates"), list) else []
    snapshot_ready = snapshot.get("decision") in {"microstructure_snapshot_sealed", "snapshot_already_sealed_for_readiness_epoch"}
    queue_valid = queue.get("decision") == "microstructure_prereg_queue_valid"
    registered = queue.get("summary", {}).get("registered") if isinstance(queue.get("summary"), dict) else queue.get("registered")
    pending = queue.get("summary", {}).get("pending_first_seal") if isinstance(queue.get("summary"), dict) else queue.get("pending")
    if not health_ok:
        decision = "microstructure_blocked_health_degraded"
        next_action = "repair collector/storage freshness before any research runner"
    elif not queue_valid:
        decision = "microstructure_blocked_prereg_queue_invalid"
        next_action = "fix preregistration queue before opening any snapshot research"
    elif snapshot_ready:
        decision = "microstructure_ready_for_locked_runner"
        next_action = "run only the locked preregistered microstructure research runner; no paper/live"
    else:
        decision = "microstructure_collecting_waiting_snapshot"
        next_action = "keep collector running until snapshot gate reaches sealed state"
    return {
        "decision": decision,
        "health_ok": health_ok,
        "snapshot_ready": snapshot_ready,
        "queue_valid": queue_valid,
        "snapshot_decision": snapshot.get("decision"),
        "snapshot_passed": snapshot.get("passed"),
        "snapshot_total": snapshot.get("total"),
        "snapshot_id": snapshot.get("snapshot_id"),
        "health_classification": health.get("classification"),
        "failed_hard_gates": failed_hard,
        "registered_hypotheses": registered,
        "pending_hypotheses": pending,
        "next_action": next_action,
    }


def classify_liquidation(coverage: dict[str, Any]) -> dict[str, Any]:
    sources = coverage.get("sources") if isinstance(coverage.get("sources"), list) else []
    source_events: dict[str, int] = {}
    hard_failures: dict[str, list[str]] = {}
    ready_sources: list[str] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        name = str(source.get("name") or "unknown")
        source_events[name] = int(source.get("events") or 0)
        hard_failures[name] = [str(item) for item in source.get("hard_failures") or []]
        if source.get("ready_with_events"):
            ready_sources.append(name)
    total_events = sum(source_events.values())
    hard_fail_count = sum(len(items) for items in hard_failures.values())
    alive_sources = sum(1 for source in sources if isinstance(source, dict) and source.get("alive"))
    coverage_decision = str(coverage.get("decision") or "")
    if hard_fail_count:
        decision = "liquidation_blocked_hard_failures"
        next_action = "repair liquidation collectors/data-quality before research"
    elif coverage_decision in {
        "liquidation_coverage_multi_venue_research_ready",
        "liquidation_coverage_single_venue_research_ready",
    } or ready_sources:
        decision = "liquidation_events_available_for_preregistered_study"
        next_action = "run only preregistered liquidation event study; no paper/live"
    elif total_events > 0:
        decision = "liquidation_events_collecting_sample"
        next_action = "keep collectors running until minimum event, bar and context-balance gates pass"
    elif alive_sources >= 2:
        decision = "liquidation_collecting_waiting_events"
        next_action = "keep Binance and Bybit collectors running until real events arrive"
    elif alive_sources == 1:
        decision = "liquidation_single_source_collecting"
        next_action = "restore second venue or keep single-source collector running"
    else:
        decision = "liquidation_no_live_feed"
        next_action = "start at least one liquidation collector"
    return {
        "decision": decision,
        "coverage_decision": coverage.get("decision"),
        "alive_sources": alive_sources,
        "source_events": source_events,
        "total_events": total_events,
        "ready_sources": sorted(ready_sources),
        "hard_failures": hard_failures,
        "next_action": next_action,
    }


def classify_post_liq_absorption(report: dict[str, Any]) -> dict[str, Any]:
    decision = str(report.get("decision") or "missing")
    evidence = report.get("evidence") if isinstance(report.get("evidence"), dict) else {}
    if report.get("_missing") or decision == "missing":
        status = "post_liq_absorption_missing"
        next_action = "run the post-liquidation absorption forward observer runner"
    elif decision == "post_liq_absorption_forward_observer_passed_for_manual_review":
        status = "post_liq_absorption_manual_review_required"
        next_action = "manual review only; still no paper/live permission"
    elif decision == "post_liq_absorption_forward_observer_failed_gate_for_tombstone_review":
        status = "post_liq_absorption_tombstone_review_required"
        next_action = "manual tombstone review; do not retune the opened sample"
    elif "waiting" in decision or "collecting" in decision:
        status = "post_liq_absorption_waiting_forward_sample"
        next_action = "keep Bybit liquidation collector running and rerun the locked observer"
    else:
        status = "post_liq_absorption_research_only"
        next_action = "keep as observer-only until a locked forward gate resolves"
    return {
        "decision": status,
        "runner_decision": decision,
        "selected_bucket_min_n": evidence.get("selected_bucket_min_n"),
        "positive_horizons": evidence.get("positive_horizons"),
        "selected_symbols": evidence.get("selected_symbols"),
        "blockers": report.get("blockers") if isinstance(report.get("blockers"), list) else [],
        "can_trade": report.get("can_trade", False),
        "next_action": next_action,
    }


def final_decision(micro: dict[str, Any], liquidation: dict[str, Any], post_liq: dict[str, Any]) -> tuple[str, str]:
    if "blocked" in micro["decision"] or "blocked" in liquidation["decision"] or liquidation["decision"] == "liquidation_no_live_feed":
        return "real_edge_blocked_infrastructure", "fix hard/degraded gates before research"
    if post_liq["decision"] in {
        "post_liq_absorption_manual_review_required",
        "post_liq_absorption_tombstone_review_required",
    }:
        return "real_edge_post_liq_absorption_requires_manual_review", post_liq["next_action"]
    ready = []
    if micro["decision"] == "microstructure_ready_for_locked_runner":
        ready.append("microstructure")
    if liquidation["decision"] == "liquidation_events_available_for_preregistered_study":
        ready.append("liquidation")
    if len(ready) == 2:
        return "real_edge_two_independent_classes_ready_for_research", "run locked research in sequence, no paper/live"
    if ready:
        return f"real_edge_{ready[0]}_ready_for_research", "run only the ready locked research path, no paper/live"
    return "real_edge_collecting_waiting_for_data", "keep collectors running; do not retune document cards"


def render_markdown(report: dict[str, Any]) -> str:
    micro = report["microstructure"]
    liq = report["liquidation"]
    post_liq = report["post_liq_absorption"]
    lines = [
        "# Real Edge Readiness Matrix",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `false`",
        "",
        "## Microstructure",
        "",
        f"- Decision: `{micro['decision']}`",
        f"- Health: `{micro['health_classification']}`",
        f"- Snapshot: `{micro['snapshot_decision']}`",
        f"- Snapshot progress: `{micro.get('snapshot_passed')}` / `{micro.get('snapshot_total')}`",
        f"- Snapshot ID: `{micro.get('snapshot_id')}`",
        f"- Prereg queue valid: `{micro['queue_valid']}`",
        f"- Registered/pending hypotheses: `{micro.get('registered_hypotheses')}` / `{micro.get('pending_hypotheses')}`",
        f"- Next: {micro['next_action']}",
        "",
        "## Liquidation",
        "",
        f"- Decision: `{liq['decision']}`",
        f"- Coverage: `{liq.get('coverage_decision')}`",
        f"- Alive sources: `{liq['alive_sources']}`",
        f"- Total events: `{liq['total_events']}`",
        f"- Source events: `{liq['source_events']}`",
        f"- Next: {liq['next_action']}",
        "",
        "## Post-Liquidation Absorption Observer",
        "",
        f"- Decision: `{post_liq['decision']}`",
        f"- Runner decision: `{post_liq.get('runner_decision')}`",
        f"- Selected bucket min N: `{post_liq.get('selected_bucket_min_n')}`",
        f"- Positive horizons: `{post_liq.get('positive_horizons')}`",
        f"- Selected symbols: `{post_liq.get('selected_symbols')}`",
        f"- Blockers: `{post_liq.get('blockers')}`",
        f"- Next: {post_liq['next_action']}",
        "",
        "## Boundary",
        "",
        "- This is a readiness/status report only.",
        "- It does not run optimization, emit signals, open paper entries, or place orders.",
        "- `can_trade=false` is intentional until a locked research path passes independent evidence gates.",
        "",
        "## Next Action",
        "",
        f"- {report['next_action']}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="One fail-closed readiness matrix for real-edge classes: microstructure and liquidation")
    parser.add_argument("--snapshot-gate", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_REAL_EDGE_2026-07-01.json")
    parser.add_argument("--microstructure-health", default="docs/CROSS_VENUE_MICROSTRUCTURE_HEALTH_REAL_EDGE_2026-07-01.json")
    parser.add_argument("--prereg-queue", default="docs/CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE_REAL_EDGE_2026-07-01.json")
    parser.add_argument("--liquidation-coverage", default="docs/LIQUIDATION_MULTI_VENUE_COVERAGE_SUMMARY_2026-07-01.json")
    parser.add_argument("--post-liq-absorption-runner", default="docs/POST_LIQUIDATION_ABSORPTION_FORWARD_OBSERVER_RUNNER_2026-07-03.json")
    parser.add_argument("--out-prefix", default="docs/REAL_EDGE_READINESS_MATRIX_2026-07-01")
    args = parser.parse_args()

    inputs = {
        "snapshot_gate": resolve_path(args.snapshot_gate),
        "microstructure_health": resolve_path(args.microstructure_health),
        "prereg_queue": resolve_path(args.prereg_queue),
        "liquidation_coverage": resolve_path(args.liquidation_coverage),
        "post_liq_absorption_runner": resolve_path(args.post_liq_absorption_runner),
    }
    snapshot = read_json(inputs["snapshot_gate"])
    health = read_json(inputs["microstructure_health"])
    queue = read_json(inputs["prereg_queue"])
    coverage = read_json(inputs["liquidation_coverage"])
    post_liq_report = read_json(inputs["post_liq_absorption_runner"])
    micro = classify_microstructure(snapshot, health, queue)
    liquidation = classify_liquidation(coverage)
    post_liq = classify_post_liq_absorption(post_liq_report)
    decision, next_action = final_decision(micro, liquidation, post_liq)
    report = {
        "generated_at": now_iso(),
        "tool": "tools/real_edge_readiness_matrix.py",
        "decision": decision,
        "can_trade": False,
        "boundary": {
            "status_only": True,
            "research_only": True,
            "runs_optimization": False,
            "emits_signals": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "inputs": {key: portable(value) for key, value in inputs.items()},
        "microstructure": micro,
        "liquidation": liquidation,
        "post_liq_absorption": post_liq,
        "next_action": next_action,
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": decision,
                "microstructure": micro["decision"],
                "liquidation": liquidation["decision"],
                "post_liq_absorption": post_liq["decision"],
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

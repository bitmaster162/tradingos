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
    if not p.exists():
        return {"_missing": portable(p)}
    try:
        value = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc), "_path": portable(p)}
    return value if isinstance(value, dict) else {"_read_error": "not_object", "_path": portable(p)}


def component(
    name: str,
    status: str,
    ready: bool,
    can_trade: bool,
    evidence: dict[str, Any],
    blocker: str,
    next_action: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "ready": ready,
        "can_trade": can_trade,
        "evidence": evidence,
        "blocker": blocker,
        "next_action": next_action,
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    forward = read_json(args.forward_health)
    micro_readiness = read_json(args.micro_readiness)
    micro_snapshot = read_json(args.micro_snapshot_gate)
    micro_post_seal = read_json(args.micro_post_seal_guard)
    liq = read_json(args.liquidation_dq)
    post_liq = read_json(args.post_liq_absorption_runner)
    oi = read_json(args.oi_funding_matrix)
    frontier = read_json(args.strategy_frontier)
    devil = read_json(args.devil_audit)
    arena = read_json(args.arena_audit)
    basis_shock = read_json(args.basis_shock)
    basis_carry = read_json(args.basis_carry)
    basis_dispersion = read_json(args.basis_dispersion)

    components: list[dict[str, Any]] = []
    components.append(
        component(
            "forward_runtime",
            str(forward.get("decision") or forward.get("classification") or "unknown"),
            str(forward.get("decision")) == "forward_runtime_healthy_observing",
            False,
            {"path": args.forward_health, "can_trade": forward.get("can_trade")},
            "profitability_unproven",
            str(forward.get("next_action") or "keep observer runtime alive"),
        )
    )
    remaining = micro_readiness.get("remaining_hours")
    snapshot_failed = micro_snapshot.get("summary", {}).get("failed") if isinstance(micro_snapshot.get("summary"), dict) else None
    micro_ready = str(micro_snapshot.get("decision")) not in {"waiting_for_microstructure_readiness", "unknown"} and not snapshot_failed
    components.append(
        component(
            "microstructure_snapshot",
            str(micro_snapshot.get("decision") or micro_readiness.get("decision") or "unknown"),
            bool(micro_ready),
            False,
            {
                "readiness_path": args.micro_readiness,
                "snapshot_gate_path": args.micro_snapshot_gate,
                "post_seal_guard_path": args.micro_post_seal_guard,
                "remaining_hours": remaining,
                "failed": snapshot_failed,
                "post_seal_decision": micro_post_seal.get("decision"),
            },
            "waiting_for_sealed_snapshot" if not micro_ready else "manual_review_required_before_validation",
            "wait for sealed snapshot, then run locked post-seal chain",
        )
    )
    liq_events_block = liq.get("events") if isinstance(liq.get("events"), dict) else {}
    liq_research = (
        liq_events_block.get("preregistered_sample")
        if isinstance(liq_events_block.get("preregistered_sample"), dict)
        else liq_events_block.get("research_universe")
        if isinstance(liq_events_block.get("research_universe"), dict)
        else liq_events_block
    )
    liq_events = int(liq_research.get("events") or 0)
    components.append(
        component(
            "liquidation_force_order_feed",
            str(liq.get("decision") or "unknown"),
            bool(liq.get("ready_for_preregistered_research")),
            False,
            {
                "path": args.liquidation_dq,
                "events": liq_events,
                "all_market_events": int(liq_events_block.get("events") or 0),
                "research_universe_events": int((liq_events_block.get("research_universe") or {}).get("events") or 0),
                "hard_failures": [item.get("name") for item in liq.get("hard_failures", [])],
                "soft_failures": [item.get("name") for item in liq.get("soft_failures", [])],
            },
            "waiting_preregistered_force_order_sample"
            if liq_events == 0 and int(liq_events_block.get("events") or 0) > 0
            else "no_real_force_order_sample_yet"
            if liq_events == 0
            else "insufficient_or_unreviewed_force_order_sample",
            str(liq.get("next_action") or "keep collector running"),
        )
    )
    post_liq_decision = str(post_liq.get("decision") or "unknown")
    post_liq_evidence = post_liq.get("evidence") if isinstance(post_liq.get("evidence"), dict) else {}
    post_liq_blocker = "post_liq_absorption_waiting_new_events"
    if post_liq_decision == "post_liq_absorption_forward_observer_passed_for_manual_review":
        post_liq_blocker = "manual_review_required_no_paper_permission"
    elif post_liq_decision == "post_liq_absorption_forward_observer_failed_gate_for_tombstone_review":
        post_liq_blocker = "tombstone_review_required"
    components.append(
        component(
            "post_liq_absorption_forward_observer",
            post_liq_decision,
            post_liq_decision in {
                "post_liq_absorption_forward_observer_passed_for_manual_review",
                "post_liq_absorption_forward_observer_failed_gate_for_tombstone_review",
            },
            False,
            {
                "path": args.post_liq_absorption_runner,
                "selected_bucket_min_n": post_liq_evidence.get("selected_bucket_min_n"),
                "positive_horizons": post_liq_evidence.get("positive_horizons"),
                "selected_symbols": post_liq_evidence.get("selected_symbols"),
            },
            post_liq_blocker,
            str(post_liq.get("next_action") or "keep observing the locked post-liquidation absorption bucket"),
        )
    )
    oi_ready = str(oi.get("decision")) == "oi_funding_quality_ready_for_research"
    components.append(
        component(
            "oi_funding_data",
            str(oi.get("decision") or "unknown"),
            oi_ready,
            False,
            {"path": args.oi_funding_matrix, "summary": oi.get("summary")},
            "data_not_active_blocker" if oi_ready else "oi_funding_quality_not_ready",
            str(oi.get("next_action") or "refresh OI/funding quality matrix"),
        )
    )
    basis_status = "basis_mechanisms_rejected"
    components.append(
        component(
            "basis_research",
            basis_status,
            False,
            False,
            {
                "shock_decision": basis_shock.get("decision"),
                "carry_decision": basis_carry.get("decision"),
                "dispersion_decision": basis_dispersion.get("decision"),
            },
            "shock_carry_dispersion_failed_gates",
            "only test materially different preregistered basis mechanisms",
        )
    )
    frontier_summary = frontier.get("summary") if isinstance(frontier.get("summary"), dict) else {}
    components.append(
        component(
            "strategy_frontier",
            str(frontier.get("decision") or "unknown"),
            int(frontier_summary.get("promotable") or 0) > 0,
            False,
            {"path": args.strategy_frontier, "summary": frontier_summary},
            "no_promotable_family",
            str(frontier.get("next_action") or "collect forward outcomes and reject weak families"),
        )
    )
    components.append(
        component(
            "devil_audit",
            str(devil.get("decision") or "unknown"),
            devil.get("source_runtime_parity", {}).get("passed") is True and devil.get("open_severity_counts", {}).get("P0") == 0,
            False,
            {
                "path": args.devil_audit,
                "open": devil.get("open_severity_counts"),
                "parity": devil.get("source_runtime_parity", {}).get("passed"),
            },
            "edge_unproven" if devil.get("can_trade") is False else "manual_review_required",
            str(devil.get("next_strong_move") or "keep live trading locked"),
        )
    )
    components.append(
        component(
            "arena_contract",
            str(arena.get("decision") or "unknown"),
            str(arena.get("decision")) == "pass_contract_safe_for_local_docs",
            False,
            {"path": args.arena_audit},
            "paper_only_boundary",
            "keep Arena handoff as paper/spec only",
        )
    )

    hard_blockers = [item["blocker"] for item in components if not item["ready"] or item["can_trade"] is not False]
    decision = "unified_readiness_no_trade_edge_unproven"
    if any(item["name"] == "microstructure_snapshot" and item["ready"] for item in components):
        decision = "unified_readiness_snapshot_ready_manual_research_gate_next"
    return {
        "generated_at": now_iso(),
        "tool": "tools/unified_readiness_matrix.py",
        "decision": decision,
        "can_trade": False,
        "summary": {
            "components": len(components),
            "ready_components": sum(1 for item in components if item["ready"]),
            "trade_enabled_components": sum(1 for item in components if item["can_trade"]),
            "hard_blockers": hard_blockers,
        },
        "components": components,
        "next_strong_move": "keep collectors running; wait for microstructure seal and forceOrder events; only then run preregistered research",
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Unified Readiness Matrix",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `{str(report['can_trade']).lower()}`",
        f"- Ready components: `{report['summary']['ready_components']}/{report['summary']['components']}`",
        "",
        "| Component | Status | Ready | Can Trade | Blocker | Next Action |",
        "|---|---|---:|---:|---|---|",
    ]
    for item in report["components"]:
        lines.append(
            f"| `{item['name']}` | `{item['status']}` | `{str(item['ready']).lower()}` | "
            f"`{str(item['can_trade']).lower()}` | `{item['blocker']}` | {item['next_action']} |"
        )
    lines.extend(["", "## Next Strong Move", "", f"- {report['next_strong_move']}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build one readiness matrix across TradingOS runtime, data and research gates")
    parser.add_argument("--forward-health", default="docs/FORWARD_RUNTIME_HEALTH_2026-06-16.json")
    parser.add_argument("--micro-readiness", default="docs/CROSS_VENUE_MICROSTRUCTURE_READINESS_PROGRESS_2026-06-25.json")
    parser.add_argument("--micro-snapshot-gate", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json")
    parser.add_argument("--micro-post-seal-guard", default="docs/CROSS_VENUE_MICROSTRUCTURE_POST_SEAL_AUTO_RUN_GUARD_2026-06-29.json")
    parser.add_argument("--liquidation-dq", default="docs/LIQUIDATION_FORCE_ORDER_DATA_QUALITY_2026-06-30.json")
    parser.add_argument("--post-liq-absorption-runner", default="docs/POST_LIQUIDATION_ABSORPTION_FORWARD_OBSERVER_RUNNER_2026-07-03.json")
    parser.add_argument("--oi-funding-matrix", default="docs/OI_FUNDING_DATA_QUALITY_MATRIX_2026-06-29.json")
    parser.add_argument("--strategy-frontier", default="docs/STRATEGY_RESEARCH_FRONTIER_MATRIX_2026-06-29.json")
    parser.add_argument("--devil-audit", default="docs/FULL_SYSTEM_DEVIL_AUDIT_2026-06-30_POST_LIQUIDATION_DQ_DEPLOY.json")
    parser.add_argument("--arena-audit", default="docs/ARENA_PAPER_EDGE_CONTRACT_AUDIT_2026-06-30_POST_LIQUIDATION_DQ.json")
    parser.add_argument("--basis-shock", default="docs/BASIS_SHOCK_REVERSION_MULTI_SYMBOL_NESTED_HOLDOUT_2026-06-30.json")
    parser.add_argument("--basis-carry", default="docs/BASIS_FUNDING_CARRY_MULTI_SYMBOL_NESTED_HOLDOUT_2026-06-30.json")
    parser.add_argument("--basis-dispersion", default="docs/BASIS_DISPERSION_REVERSION_MULTI_SYMBOL_NESTED_HOLDOUT_2026-06-30.json")
    parser.add_argument("--out-prefix", default="docs/UNIFIED_READINESS_MATRIX_2026-06-30")
    args = parser.parse_args()
    report = build_report(args)
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "summary": report["summary"], "out": portable(out.with_suffix(".json")), "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from audit_evidence import ROOT, latest_json, now_iso, portable, read_json, resolve_path, today_tag, write_json


def load_latest(explicit: str | None, pattern: str, root: Path) -> tuple[Path | None, dict[str, Any]]:
    excluded_tokens = ("TELEGRAM",) if "SNAPSHOT_GATE" in pattern else ()
    path = resolve_path(explicit, root) if explicit else latest_json(pattern, root, exclude_name_tokens=excluded_tokens)
    return path, read_json(path) if path else {"_read_error": "report_not_found"}


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_path(args.active_root)
    devil_path, devil = load_latest(args.devil_report, "docs/FULL_SYSTEM_DEVIL_AUDIT_*.json", root)
    angel_path, angel = load_latest(args.angel_report, "docs/FULL_SYSTEM_ANGEL_AUDIT_*.json", root)
    frontier_path, frontier = load_latest(args.frontier_report, "docs/STRATEGY_RESEARCH_FRONTIER_MATRIX_*.json", root)
    execution_path, execution = load_latest(args.execution_gate, "docs/EXECUTION_REALISM_PROMOTION_GATE_*.json", root)
    replication_path, replication = load_latest(
        args.replication_monitor, "docs/CROSS_STACK_REPLICATION_TRANSITION_MONITOR_*.json", root
    )
    micro_path, micro = load_latest(args.microstructure_gate, "docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_*.json", root)
    coverage_path, coverage = load_latest(
        args.book_coverage_diagnostic,
        "docs/CROSS_VENUE_MICROSTRUCTURE_BOOK_COVERAGE_DIAGNOSTIC_*.json",
        root,
    )
    cost_path, cost_audit = load_latest(
        getattr(args, "microstructure_cost_audit", None),
        "docs/MICROSTRUCTURE_COST_MODEL_AUDIT_*.json",
        root,
    )
    binding_path, binding_drill = load_latest(
        getattr(args, "candidate_binding_drill", None),
        "docs/EXECUTION_REALISM_CANDIDATE_BINDING_DRILL_*.json",
        root,
    )
    rollout_path, rollout_drill = load_latest(
        getattr(args, "rollout_handoff_drill", None),
        "docs/CROSS_VENUE_MICROSTRUCTURE_ROLLOUT_HANDOFF_DRILL_*.json",
        root,
    )
    integrity_path, integrity = load_latest(
        getattr(args, "source_integrity_guard", None),
        "docs/ACTIVE_SOURCE_INTEGRITY_GUARD*.json",
        root,
    )

    devil_counts = devil.get("open_severity_counts") if isinstance(devil.get("open_severity_counts"), dict) else {}
    frontier_summary = frontier.get("summary") if isinstance(frontier.get("summary"), dict) else {}
    angel_runtime = angel.get("runtime") if isinstance(angel.get("runtime"), dict) else {}
    angel_strengths = angel.get("strengths") if isinstance(angel.get("strengths"), list) else []
    strength_ids = {item.get("id") for item in angel_strengths if isinstance(item, dict)}
    stopped_loops = angel_runtime.get("stopped_or_stale") if isinstance(angel_runtime.get("stopped_or_stale"), list) else []
    execution_promotion = execution.get("promotion") if isinstance(execution.get("promotion"), dict) else {}
    replication_transition = replication.get("transition") if isinstance(replication.get("transition"), dict) else {}
    coverage_summary = coverage.get("coverage") if isinstance(coverage.get("coverage"), dict) else {}
    coverage_recent = coverage.get("recent_windows") if isinstance(coverage.get("recent_windows"), dict) else {}
    coverage_eta = coverage.get("eta") if isinstance(coverage.get("eta"), dict) else {}

    p0 = int(devil_counts.get("P0") or 0)
    promotable = int(frontier_summary.get("promotable") or 0)
    safety_verified = "safety_containment_verified" in strength_ids
    edge_unproven = promotable == 0 or devil.get("decision") == "operational_runtime_healthy_but_edge_unproven"
    execution_gate_clean = execution_promotion.get("execution_realism_gate_passed") is True
    observer_self_heal_proven = "observer_self_heal_dry_run_proven" in strength_ids
    prereg_independence_proven = "microstructure_prereg_mechanisms_independent" in strength_ids
    seal_pipeline_drill_proven = "microstructure_seal_pipeline_drill_proven" in strength_ids
    microstructure_cost_accounting_consistent = (
        "microstructure_cost_accounting_consistent" in strength_ids
        and cost_audit.get("decision") == "cost_model_consistent_research_only_execution_overlay_required"
        and cost_audit.get("failed_checks") == []
        and cost_audit.get("can_trade") is False
    )
    candidate_execution_binding_tamper_proof = (
        "candidate_execution_binding_tamper_proof" in strength_ids
        and binding_drill.get("decision") == "execution_candidate_binding_drill_passed"
        and binding_drill.get("can_trade") is False
    )
    microstructure_rollout_handoff_exactly_once_proven = (
        "microstructure_rollout_handoff_exactly_once_proven" in strength_ids
        and rollout_drill.get("decision") == "microstructure_rollout_handoff_drill_passed"
        and rollout_drill.get("checks_passed") == rollout_drill.get("checks_total") == 10
        and rollout_drill.get("can_trade") is False
    )
    active_source_integrity_clean = (
        "active_source_integrity_fail_closed" in strength_ids
        and integrity.get("decision") == "active_source_integrity_clean"
        and integrity.get("drift_count") == 0
        and integrity.get("can_trade") is False
    )
    micro_sealed = micro.get("decision") == "microstructure_snapshot_sealed"
    external_ready = replication_transition.get("threshold_ready") is True

    blockers: list[dict[str, Any]] = []
    if p0 > 0 or not safety_verified:
        blockers.append(
            {
                "severity": "P0",
                "id": "safety_boundary_not_verified",
                "evidence": {"devil_p0": p0, "angel_safety_verified": safety_verified},
                "unlock": "repair and re-audit all no-order boundaries",
            }
        )
    if promotable == 0:
        blockers.append(
            {
                "severity": "P1",
                "id": "no_promotable_strategy_family",
                "evidence": frontier_summary,
                "unlock": "a preregistered family must pass its untouched evidence gate",
            }
        )
    if stopped_loops:
        blockers.append(
            {
                "severity": "P1",
                "id": "observer_runtime_degraded",
                "evidence": {"stopped_or_stale": stopped_loops},
                "unlock": "restore only the stopped public-data observer loops and verify fresh PID/status proof",
            }
        )
    if not micro_sealed:
        blockers.append(
            {
                "severity": "P2",
                "id": "microstructure_snapshot_not_sealed",
                "evidence": {
                    "decision": micro.get("decision"),
                    "failed_checks": micro.get("summary", {}).get("failed"),
                    "book_coverage_pct": micro.get("readiness_diagnostics", {}).get("book_coverage_pct"),
                    "recent_24h_book_coverage_pct": (coverage_recent.get("24h") or {}).get(
                        "dual_book_coverage_pct"
                    ),
                    "missing_dual_book_minutes": coverage_summary.get("missing_dual_book_minutes"),
                    "recovery_eta_utc": coverage_eta.get("eta_utc"),
                },
                "unlock": "reach the locked collection thresholds and seal the exact dataset before validation",
            }
        )
    if not external_ready:
        blockers.append(
            {
                "severity": "P2",
                "id": "external_replication_below_threshold",
                "evidence": {
                    "transition": replication_transition,
                    "current_state": replication.get("current_state"),
                },
                "unlock": "wait for the immutable external observer to reach its precommitted horizon floors",
            }
        )
    if promotable > 0 and execution_promotion.get("candidate_specific_overlay_present") is not True:
        blockers.append(
            {
                "severity": "P1",
                "id": "candidate_execution_overlay_missing",
                "evidence": execution_promotion,
                "unlock": "run the candidate-specific execution overlay without changing candidate parameters",
            }
        )

    contradictions = []
    if devil.get("runtime", {}).get("health") == "forward_runtime_healthy_observing" and stopped_loops:
        contradictions.append(
            {
                "claim_a": "Devil report labels the forward runtime healthy",
                "claim_b": "Fresh Angel PID/freshness proof shows stopped or stale research loops",
                "resolution": "core forward runtime is healthy, but the broader research observer runtime is degraded",
            }
        )

    agreements = [
        "live trading must remain locked",
        "no profitable strategy is proven",
        "research governance and safety controls are real assets",
        "observer evidence must accumulate without retuning",
    ]
    if microstructure_cost_accounting_consistent:
        agreements.append(
            "microstructure research costs are internally consistent, while candidate-specific execution realism remains unproven"
        )
    if candidate_execution_binding_tamper_proof:
        agreements.append(
            "future candidate overlays are identity-bound and tamper-evident, but no real candidate exists yet"
        )
    if p0 > 0 or not safety_verified:
        decision = "dialectic_stop_repair_safety_boundary"
        next_move = "repair safety boundaries before any research continuation"
    elif stopped_loops:
        decision = "dialectic_repair_observer_runtime_then_collect_evidence"
        next_move = "restore microstructure_book and real_edge_observer, verify fresh loop proof, then refresh the sealed-data gates"
    elif edge_unproven:
        decision = "dialectic_collect_precommitted_evidence_no_trade"
        recovery_eta = coverage_eta.get("eta_utc")
        if not micro_sealed and recovery_eta:
            next_move = (
                f"keep public-data collectors uninterrupted through the rolling-window ETA {recovery_eta}; "
                "then refresh the sealed snapshot gate and run only preregistered research"
            )
        else:
            next_move = "continue fixed-parameter observers and preregistered data collection until one family passes untouched gates"
    elif not execution_gate_clean:
        decision = "dialectic_blocked_execution_realism"
        next_move = "repair execution-realism evidence before candidate review"
    else:
        decision = "dialectic_manual_candidate_review_only"
        next_move = "perform candidate-specific overlay and manual paper-design review; execution remains disabled"

    return {
        "generated_at": now_iso(),
        "tool": "dialectic_synthesizer",
        "method": {
            "thesis": "Angel: preserve verified operational strengths and reusable research assets.",
            "antithesis": "Devil: reject profitability claims, stale runtime claims and unsealed evidence.",
            "priority_rule": "Safety failures override strengths; missing edge overrides infrastructure quality; infrastructure strengths remain reusable.",
        },
        "source_reports": {
            "devil": portable(devil_path, root) if devil_path else None,
            "angel": portable(angel_path, root) if angel_path else None,
            "frontier": portable(frontier_path, root) if frontier_path else None,
            "execution_gate": portable(execution_path, root) if execution_path else None,
            "replication_monitor": portable(replication_path, root) if replication_path else None,
            "microstructure_gate": portable(micro_path, root) if micro_path else None,
            "book_coverage_diagnostic": portable(coverage_path, root) if coverage_path else None,
            "microstructure_cost_audit": portable(cost_path, root) if cost_path else None,
            "candidate_binding_drill": portable(binding_path, root) if binding_path else None,
            "rollout_handoff_drill": portable(rollout_path, root) if rollout_path else None,
            "source_integrity_guard": portable(integrity_path, root) if integrity_path else None,
        },
        "agreements": agreements,
        "contradictions": contradictions,
        "blockers": blockers,
        "state": {
            "research": "allowed_with_locked_hypotheses",
            "public_data_collection": "allowed",
            "paper_design_review": "blocked" if promotable == 0 else "manual_gate_only",
            "paper_execution": "blocked",
            "live_execution": "blocked",
            "promotable_families": promotable,
            "observer_runtime_degraded": bool(stopped_loops),
            "execution_realism_generic_gate_clean": execution_gate_clean,
            "microstructure_snapshot_sealed": micro_sealed,
            "external_replication_threshold_ready": external_ready,
            "observer_self_heal_dry_run_proven": observer_self_heal_proven,
            "microstructure_prereg_mechanisms_independent": prereg_independence_proven,
            "microstructure_seal_pipeline_drill_proven": seal_pipeline_drill_proven,
            "microstructure_rollout_handoff_exactly_once_proven": microstructure_rollout_handoff_exactly_once_proven,
            "active_source_integrity_clean": active_source_integrity_clean,
            "microstructure_cost_accounting_consistent": microstructure_cost_accounting_consistent,
            "microstructure_execution_realism_proven": False,
            "candidate_execution_binding_tamper_proof": candidate_execution_binding_tamper_proof,
            "microstructure_book_coverage_pct": coverage_summary.get("dual_book_coverage_pct"),
            "microstructure_recent_24h_book_coverage_pct": (coverage_recent.get("24h") or {}).get(
                "dual_book_coverage_pct"
            ),
            "microstructure_recovery_eta_utc": coverage_eta.get("eta_utc"),
        },
        "decision": decision,
        "human_verdict": "We have a disciplined research operating system, not a proven money-making bot. The immediate job is runtime evidence repair, then untouched forward validation.",
        "next_strong_move": next_move,
        "runtime_boundary": {
            "synthesis_only": True,
            "alerts_allowed": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    method = report.get("method") or {}
    state = report.get("state") or {}
    lines = [
        "# Trading OS Dialectic Synthesis",
        "",
        f"Generated: `{report.get('generated_at')}`",
        f"Decision: `{report.get('decision')}`",
        f"Can trade: `{report.get('can_trade')}`",
        "",
        "## Verdict",
        "",
        report.get("human_verdict") or "",
        "",
        "## Dialectic",
        "",
        f"- Thesis: {method.get('thesis')}",
        f"- Antithesis: {method.get('antithesis')}",
        f"- Priority rule: {method.get('priority_rule')}",
        "",
        "## Agreements",
        "",
    ]
    for item in report.get("agreements") or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Resolved Contradictions", ""])
    if not report.get("contradictions"):
        lines.append("- None detected.")
    for item in report.get("contradictions") or []:
        lines.append(f"- `{item.get('claim_a')}` vs `{item.get('claim_b')}` -> {item.get('resolution')}")
    lines.extend(["", "## Promotion Blockers", ""])
    for item in report.get("blockers") or []:
        lines.append(
            f"- `{item.get('severity')}` `{item.get('id')}`: unlock = {item.get('unlock')}; evidence = `{item.get('evidence')}`"
        )
    lines.extend(["", "## Current State", ""])
    for name, value in state.items():
        lines.append(f"- `{name}`: `{value}`")
    lines.extend(["", "## Next Strong Move", "", report.get("next_strong_move") or "", ""])
    return "\n".join(lines)


def write_outputs(report: dict[str, Any], out_prefix: str, root: Path) -> None:
    prefix = resolve_path(out_prefix, root)
    write_json(prefix.with_suffix(".json"), report)
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthesize Devil and Angel audits into one bounded decision.")
    parser.add_argument("--active-root", default=str(ROOT))
    parser.add_argument("--devil-report")
    parser.add_argument("--angel-report")
    parser.add_argument("--frontier-report")
    parser.add_argument("--execution-gate")
    parser.add_argument("--replication-monitor")
    parser.add_argument("--microstructure-gate")
    parser.add_argument("--book-coverage-diagnostic")
    parser.add_argument("--microstructure-cost-audit")
    parser.add_argument("--candidate-binding-drill")
    parser.add_argument("--rollout-handoff-drill")
    parser.add_argument("--source-integrity-guard")
    parser.add_argument("--out-prefix")
    args = parser.parse_args()
    root = resolve_path(args.active_root)
    out_prefix = args.out_prefix or f"docs/DIALECTIC_SYNTHESIS_{today_tag()}"
    report = build_report(args)
    write_outputs(report, out_prefix, root)
    print(
        {
            "decision": report["decision"],
            "blockers": [item["id"] for item in report["blockers"]],
            "next_strong_move": report["next_strong_move"],
            "can_trade": report["can_trade"],
        }
    )
    return 2 if any(item["severity"] == "P0" for item in report["blockers"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())

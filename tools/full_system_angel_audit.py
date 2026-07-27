#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from audit_evidence import (
    ROOT,
    boundary_false,
    latest_json,
    now_iso,
    observe_runtime_loops,
    portable,
    read_json,
    resolve_path,
    today_tag,
    write_json,
)


def load_latest(explicit: str | None, patterns: str | tuple[str, ...], root: Path) -> tuple[Path | None, dict[str, Any]]:
    excluded_tokens = ("TELEGRAM",) if "SNAPSHOT_GATE" in str(patterns) else ()
    path = resolve_path(explicit, root) if explicit else latest_json(patterns, root, exclude_name_tokens=excluded_tokens)
    return path, read_json(path) if path else {"_read_error": "report_not_found"}


def add_strength(
    strengths: list[dict[str, Any]],
    strength_id: str,
    title: str,
    evidence: dict[str, Any],
    confidence: str,
    limitation: str,
) -> None:
    strengths.append(
        {
            "id": strength_id,
            "title": title,
            "confidence": confidence,
            "evidence": evidence,
            "limitation": limitation,
        }
    )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_path(args.active_root)
    devil_path, devil = load_latest(args.devil_report, "docs/FULL_SYSTEM_DEVIL_AUDIT_*.json", root)
    frontier_path, frontier = load_latest(args.frontier_report, "docs/STRATEGY_RESEARCH_FRONTIER_MATRIX_*.json", root)
    execution_path, execution = load_latest(args.execution_gate, "docs/EXECUTION_REALISM_PROMOTION_GATE_*.json", root)
    replication_path, replication = load_latest(
        args.replication_monitor, "docs/CROSS_STACK_REPLICATION_TRANSITION_MONITOR_*.json", root
    )
    micro_path, micro = load_latest(args.microstructure_gate, "docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_*.json", root)
    durability_path, durability = load_latest(
        args.observer_durability_drill, "docs/OBSERVER_LOOP_DURABILITY_DRILL_*.json", root
    )
    coverage_path, coverage = load_latest(
        args.book_coverage_diagnostic,
        "docs/CROSS_VENUE_MICROSTRUCTURE_BOOK_COVERAGE_DIAGNOSTIC_*.json",
        root,
    )
    independence_path, independence = load_latest(
        args.prereg_independence_audit,
        "docs/MICROSTRUCTURE_PREREG_INDEPENDENCE_AUDIT_*.json",
        root,
    )
    seal_drill_path, seal_drill = load_latest(
        args.seal_pipeline_drill,
        "docs/CROSS_VENUE_MICROSTRUCTURE_SEAL_PIPELINE_DRILL_*.json",
        root,
    )
    rollout_drill_path, rollout_drill = load_latest(
        getattr(args, "rollout_handoff_drill", None),
        "docs/CROSS_VENUE_MICROSTRUCTURE_ROLLOUT_HANDOFF_DRILL_*.json",
        root,
    )
    cost_audit_path, cost_audit = load_latest(
        getattr(args, "microstructure_cost_audit", None),
        "docs/MICROSTRUCTURE_COST_MODEL_AUDIT_*.json",
        root,
    )
    binding_drill_path, binding_drill = load_latest(
        getattr(args, "candidate_binding_drill", None),
        "docs/EXECUTION_REALISM_CANDIDATE_BINDING_DRILL_*.json",
        root,
    )
    integrity_path, integrity = load_latest(
        getattr(args, "source_integrity_guard", None),
        "docs/ACTIVE_SOURCE_INTEGRITY_GUARD*.json",
        root,
    )

    loops = observe_runtime_loops(root)
    active_loops = [item for item in loops if item["active"]]
    stopped_or_stale = [item for item in loops if not item["active"]]
    safety_violations = [item for item in loops if item["safety_violation"]]
    core_loops = [item for item in loops if item["role"] == "core_observer"]
    core_active = [item for item in core_loops if item["active"]]

    devil_counts = devil.get("open_severity_counts") if isinstance(devil.get("open_severity_counts"), dict) else {}
    frontier_summary = frontier.get("summary") if isinstance(frontier.get("summary"), dict) else {}
    execution_checks = execution.get("failed_checks") if isinstance(execution.get("failed_checks"), list) else ["missing"]
    hardening = devil.get("hardening_proof") if isinstance(devil.get("hardening_proof"), dict) else {}
    parity = devil.get("source_runtime_parity") if isinstance(devil.get("source_runtime_parity"), dict) else {}
    oi_matrix = devil.get("research_frontier", {}).get("oi_funding_matrix_summary", {}) if isinstance(devil.get("research_frontier"), dict) else {}
    micro_checks = micro.get("checks") if isinstance(micro.get("checks"), dict) else {}
    micro_diag = micro.get("readiness_diagnostics") if isinstance(micro.get("readiness_diagnostics"), dict) else {}
    coverage_summary = coverage.get("coverage") if isinstance(coverage.get("coverage"), dict) else {}
    coverage_recent = coverage.get("recent_windows") if isinstance(coverage.get("recent_windows"), dict) else {}
    coverage_eta = coverage.get("eta") if isinstance(coverage.get("eta"), dict) else {}
    replication_transition = replication.get("transition") if isinstance(replication.get("transition"), dict) else {}
    replication_state = replication.get("current_state") if isinstance(replication.get("current_state"), dict) else {}

    safety_verified = (
        boundary_false(devil)
        and boundary_false(frontier)
        and boundary_false(execution)
        and int(devil_counts.get("P0") or 0) == 0
        and int(frontier_summary.get("unsafe") or 0) == 0
        and not safety_violations
    )

    strengths: list[dict[str, Any]] = []
    if safety_verified:
        add_strength(
            strengths,
            "safety_containment_verified",
            "Trading remains contained behind explicit no-order boundaries",
            {
                "devil_p0": int(devil_counts.get("P0") or 0),
                "frontier_unsafe": int(frontier_summary.get("unsafe") or 0),
                "loop_safety_violations": len(safety_violations),
                "can_trade": False,
            },
            "verified",
            "This proves containment, not profitability.",
        )

    if len(core_active) == len(core_loops) and core_loops:
        add_strength(
            strengths,
            "core_observer_runtime_alive",
            "Core observer scheduler, watchdog and forward journal loops are alive",
            {"active": [item["loop_id"] for item in core_active], "total": len(core_loops)},
            "verified",
            "Research collectors outside the core observer set may still be stopped.",
        )

    if parity.get("passed") is True:
        add_strength(
            strengths,
            "source_runtime_parity_verified",
            "Curated source and active runtime have hash-level parity",
            {
                "source_curated_files": parity.get("source_curated_files"),
                "active_curated_files": parity.get("active_curated_files"),
                "missing_in_active": parity.get("missing_in_active"),
                "different_hash": parity.get("different_hash"),
            },
            "verified",
            "Generated reports and local runtime state are intentionally excluded from parity.",
        )

    hardening_passed = [name for name, passed in hardening.items() if passed is True]
    if hardening.get("backup_restore_drill_passed") is True and hardening.get("repair_restart_budget_present") is True:
        add_strength(
            strengths,
            "operational_hardening_proven",
            "Restore integrity and restart-storm controls have runnable proof",
            {"passed_controls": hardening_passed, "passed_count": len(hardening_passed)},
            "verified",
            "Controls reduce operational loss; they do not create trading expectancy.",
        )

    governance_verified = (
        int(frontier_summary.get("families") or 0) > 0
        and int(frontier_summary.get("unsafe") or 0) == 0
        and int(frontier_summary.get("rejected") or 0) > 0
        and frontier.get("can_trade") is False
    )
    if governance_verified:
        add_strength(
            strengths,
            "research_governance_is_selective",
            "The research frontier rejects weak families instead of promoting train winners",
            {
                "families": int(frontier_summary.get("families") or 0),
                "rejected": int(frontier_summary.get("rejected") or 0),
                "observer_only": int(frontier_summary.get("observer_only") or 0),
                "promotable": int(frontier_summary.get("promotable") or 0),
            },
            "verified",
            "A good rejection process is valuable, but the current frontier still has no promotable edge.",
        )

    if not execution_checks and execution.get("promotion", {}).get("execution_realism_gate_passed") is True:
        add_strength(
            strengths,
            "execution_realism_gate_verified",
            "Historical ledgers survive the generic shadow execution-realism floor",
            {
                "ledgers_analyzed": execution.get("metrics", {}).get("ledgers_analyzed"),
                "shadow_weighted_expectancy_r": execution.get("metrics", {}).get("shadow_weighted_expectancy_r"),
                "retention_ratio": execution.get("metrics", {}).get("retention_ratio"),
                "candidate_family_count": execution.get("promotion", {}).get("candidate_family_count"),
            },
            "verified",
            "No candidate-specific overlay exists because no family is promotable.",
        )

    ready_intervals = int(oi_matrix.get("ready_intervals") or 0)
    if ready_intervals > 0 or micro_checks.get("dual_trade_coverage") is True:
        add_strength(
            strengths,
            "research_data_assets_exist",
            "Reusable derivatives and cross-venue data assets are present",
            {
                "oi_funding_ready_intervals": ready_intervals,
                "oi_funding_ready_interval_ids": oi_matrix.get("ready_interval_ids") or [],
                "microstructure_span_hours": micro_diag.get("span_hours"),
                "microstructure_trade_coverage_pct": micro_diag.get("trade_coverage_pct"),
                "microstructure_book_coverage_pct": micro_diag.get("book_coverage_pct"),
                "microstructure_recent_24h_book_coverage_pct": (coverage_recent.get("24h") or {}).get(
                    "dual_book_coverage_pct"
                ),
                "microstructure_missing_dual_book_minutes": coverage_summary.get("missing_dual_book_minutes"),
                "microstructure_recovery_eta_utc": coverage_eta.get("eta_utc"),
            },
            "conditional",
            "The microstructure snapshot is not sealed and the book collector is currently inactive.",
        )

    resolved = replication_state.get("resolved_per_horizon") if isinstance(replication_state.get("resolved_per_horizon"), dict) else {}
    if replication_transition.get("nonzero_detected") is True or sum(int(value or 0) for value in resolved.values()) > 0:
        add_strength(
            strengths,
            "external_replication_pipeline_has_real_events",
            "Independent external replication has crossed from zero to real post-lock observations",
            {
                "post_floor_event_bars": replication_state.get("post_floor_squeeze_event_bars"),
                "resolved_per_horizon": resolved,
                "required_per_horizon": replication_state.get("required_per_horizon"),
                "threshold_ready": replication_transition.get("threshold_ready"),
            },
            "conditional",
            "External evidence is below threshold and is not counted as the Codex forward sample.",
        )

    if (
        durability.get("decision") == "observer_loop_durability_drill_passed"
        and durability.get("repair", {}).get("decision") == "dry_run_restart_ready"
        and durability.get("missing_matched_gates") == []
        and durability.get("can_trade") is False
    ):
        add_strength(
            strengths,
            "observer_self_heal_dry_run_proven",
            "Observer-loop failures are wired into bounded self-heal",
            {
                "matched_gates": durability.get("repair", {}).get("matched_repairable_gates"),
                "max_repairs_in_window": durability.get("repair", {}).get("max_repairs_in_window"),
                "window_minutes": durability.get("repair", {}).get("window_minutes"),
                "dry_run": durability.get("repair", {}).get("dry_run"),
            },
            "verified",
            "The drill proves routing and restart budgeting, not future process uptime.",
        )

    if (
        independence.get("decision") == "four_preregistered_mechanisms_independent_queue_full"
        and independence.get("failed_checks") == []
        and independence.get("can_trade") is False
    ):
        add_strength(
            strengths,
            "microstructure_prereg_mechanisms_independent",
            "The locked microstructure queue contains four mechanism-distinct hypotheses",
            independence.get("summary") or {},
            "verified",
            "Mechanism independence reduces duplication; it does not prove that any hypothesis has positive expectancy.",
        )

    seal_checks = seal_drill.get("checks") if isinstance(seal_drill.get("checks"), dict) else {}
    if (
        seal_drill.get("decision") == "microstructure_seal_pipeline_drill_passed"
        and seal_drill.get("runner_completed") == 4
        and seal_drill.get("runner_failed") == 0
        and seal_drill.get("runner_tested_total") == 774
        and seal_checks
        and all(value is True for value in seal_checks.values())
        and seal_drill.get("can_trade") is False
    ):
        add_strength(
            strengths,
            "microstructure_seal_pipeline_drill_proven",
            "The sealed-snapshot research chain passes an end-to-end synthetic drill",
            {
                "steps": f"{seal_drill.get('steps_passed')}/{seal_drill.get('steps_total')}",
                "runner_completed": seal_drill.get("runner_completed"),
                "runner_failed": seal_drill.get("runner_failed"),
                "runner_tested_total": seal_drill.get("runner_tested_total"),
                "runner_candidate_count": seal_drill.get("runner_candidate_count"),
                "snapshot_notify_decision": seal_drill.get("snapshot_notify_decision"),
                "runner_notify_decision": seal_drill.get("runner_notify_decision"),
            },
            "verified",
            "Synthetic execution proves orchestration and contracts, not strategy profitability on the future sealed snapshot.",
        )

    rollout_checks = rollout_drill.get("checks") if isinstance(rollout_drill.get("checks"), dict) else {}
    if (
        rollout_drill.get("decision") == "microstructure_rollout_handoff_drill_passed"
        and rollout_drill.get("checks_passed") == rollout_drill.get("checks_total") == 10
        and rollout_drill.get("runner_completed") == 4
        and rollout_drill.get("runner_failed") == 0
        and rollout_drill.get("runner_tested_total") == 774
        and rollout_checks
        and all(value is True for value in rollout_checks.values())
        and rollout_drill.get("can_trade") is False
    ):
        add_strength(
            strengths,
            "microstructure_rollout_handoff_exactly_once_proven",
            "The rolling-gap to sealed-snapshot handoff executes the locked runner once and blocks duplicates",
            {
                "checks": f"{rollout_drill.get('checks_passed')}/{rollout_drill.get('checks_total')}",
                "runner_tested_total": rollout_drill.get("runner_tested_total"),
                "runner_execution": rollout_drill.get("states", {}).get("runner_execution"),
                "duplicate_call": rollout_drill.get("states", {}).get("duplicate_call"),
            },
            "verified",
            "Synthetic orchestration proof does not consume real evidence and does not prove positive expectancy.",
        )

    cost_summary = cost_audit.get("summary") if isinstance(cost_audit.get("summary"), dict) else {}
    cost_verdict = (
        cost_audit.get("governance_verdict")
        if isinstance(cost_audit.get("governance_verdict"), dict)
        else {}
    )
    if (
        cost_audit.get("decision") == "cost_model_consistent_research_only_execution_overlay_required"
        and cost_audit.get("failed_checks") == []
        and cost_summary.get("source_audits_passed") == 4
        and cost_summary.get("synthetic_reports_passed") == 4
        and cost_verdict.get("execution_realism_proven") is False
        and cost_audit.get("can_trade") is False
    ):
        add_strength(
            strengths,
            "microstructure_cost_accounting_consistent",
            "All four locked microstructure studies use the same verified research cost accounting",
            {
                "source_audits_passed": cost_summary.get("source_audits_passed"),
                "synthetic_reports_passed": cost_summary.get("synthetic_reports_passed"),
                "total_registered_configurations": cost_summary.get("total_registered_configurations"),
                "base_round_trip_bps": cost_summary.get("base_round_trip_bps"),
                "stress_round_trip_bps": cost_summary.get("stress_round_trip_bps"),
            },
            "verified",
            "This proves consistent research accounting only; a candidate-specific fill, spread, latency and market-impact overlay is still mandatory.",
        )

    binding_checks = binding_drill.get("checks") if isinstance(binding_drill.get("checks"), dict) else {}
    if (
        binding_drill.get("decision") == "execution_candidate_binding_drill_passed"
        and binding_checks
        and all(value is True for value in binding_checks.values())
        and binding_drill.get("positive_case", {}).get("paper_execution_allowed") is False
        and binding_drill.get("positive_case", {}).get("live_execution_allowed") is False
        and binding_drill.get("tamper_case", {}).get("paper_design_review_allowed") is False
        and binding_drill.get("can_trade") is False
    ):
        add_strength(
            strengths,
            "candidate_execution_binding_tamper_proof",
            "Future candidate overlays can be SHA-256 bound to exact reports and ledgers, with tamper rejection",
            {
                "checks": len(binding_checks),
                "positive_review_allowed": binding_drill.get("positive_case", {}).get("paper_design_review_allowed"),
                "tamper_review_allowed": binding_drill.get("tamper_case", {}).get("paper_design_review_allowed"),
                "paper_execution_allowed": binding_drill.get("positive_case", {}).get("paper_execution_allowed"),
            },
            "verified",
            "This is a synthetic contract proof. A real candidate still needs its own untouched report, ledgers and execution overlay.",
        )

    if (
        integrity.get("decision") == "active_source_integrity_clean"
        and integrity.get("drift_count") == 0
        and integrity.get("expected_files") == integrity.get("current_files")
        and integrity.get("can_trade") is False
    ):
        add_strength(
            strengths,
            "active_source_integrity_fail_closed",
            "Reviewed source hashes are locked and watchdog research execution fails closed on drift",
            {
                "review_id": integrity.get("lock_review_id"),
                "files": integrity.get("current_files"),
                "drift_count": integrity.get("drift_count"),
            },
            "verified",
            "This protects reviewed runtime provenance; it does not prove strategy profitability.",
        )

    constraints = []
    if int(frontier_summary.get("promotable") or 0) == 0:
        constraints.append("no_promotable_strategy_family")
    if micro.get("decision") != "microstructure_snapshot_sealed":
        constraints.append("microstructure_snapshot_not_sealed")
    if stopped_or_stale:
        constraints.append("research_runtime_loops_stopped_or_stale")
    if replication_transition.get("threshold_ready") is not True:
        constraints.append("external_replication_below_threshold")
    if (
        int(frontier_summary.get("promotable") or 0) > 0
        and execution.get("promotion", {}).get("candidate_specific_overlay_present") is not True
    ):
        constraints.append("candidate_specific_execution_overlay_absent")

    verified_count = sum(item["confidence"] == "verified" for item in strengths)
    conditional_count = sum(item["confidence"] == "conditional" for item in strengths)
    if not safety_verified:
        decision = "angel_audit_boundary_failure_no_positive_runtime_claim"
    elif verified_count >= 4:
        decision = "verified_operational_foundation_edge_not_proven"
    else:
        decision = "partial_operational_foundation_requires_repair"

    return {
        "generated_at": now_iso(),
        "audit_mode": "angels_advocate_runtime_proof",
        "active_root": str(root),
        "source_reports": {
            "devil": portable(devil_path, root) if devil_path else None,
            "frontier": portable(frontier_path, root) if frontier_path else None,
            "execution_gate": portable(execution_path, root) if execution_path else None,
            "replication_monitor": portable(replication_path, root) if replication_path else None,
            "microstructure_gate": portable(micro_path, root) if micro_path else None,
            "observer_durability_drill": portable(durability_path, root) if durability_path else None,
            "book_coverage_diagnostic": portable(coverage_path, root) if coverage_path else None,
            "prereg_independence_audit": portable(independence_path, root) if independence_path else None,
            "seal_pipeline_drill": portable(seal_drill_path, root) if seal_drill_path else None,
            "rollout_handoff_drill": portable(rollout_drill_path, root) if rollout_drill_path else None,
            "microstructure_cost_audit": portable(cost_audit_path, root) if cost_audit_path else None,
            "candidate_binding_drill": portable(binding_drill_path, root) if binding_drill_path else None,
            "source_integrity_guard": portable(integrity_path, root) if integrity_path else None,
        },
        "runtime": {
            "loops": loops,
            "active_count": len(active_loops),
            "total_count": len(loops),
            "stopped_or_stale": [item["loop_id"] for item in stopped_or_stale],
            "safety_violations": [item["loop_id"] for item in safety_violations],
        },
        "strengths": strengths,
        "strength_counts": {"verified": verified_count, "conditional": conditional_count},
        "constraints": constraints,
        "decision": decision,
        "human_verdict": "The system has a real safe research foundation, but it does not yet have a proven profitable strategy.",
        "next_best_use_of_strengths": "restore stopped public-data observer loops, refresh sealed-data gates, and keep collecting precommitted evidence without retuning",
        "runtime_boundary": {
            "audit_only": True,
            "alerts_allowed": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Full System Angel Audit",
        "",
        f"Generated: `{report.get('generated_at')}`",
        f"Decision: `{report.get('decision')}`",
        f"Can trade: `{report.get('can_trade')}`",
        "",
        "## Honest Positive Case",
        "",
        report.get("human_verdict") or "",
        "",
        "The Angel audit counts only strengths with runnable or artifact-level proof. It does not convert infrastructure quality into a profitability claim.",
        "",
        "## Verified Strengths",
        "",
    ]
    for item in report.get("strengths") or []:
        lines.extend(
            [
                f"### {item.get('title')}",
                "",
                f"- ID: `{item.get('id')}`",
                f"- Confidence: `{item.get('confidence')}`",
                f"- Evidence: `{item.get('evidence')}`",
                f"- Limitation: {item.get('limitation')}",
                "",
            ]
        )
    lines.extend(["## Current Constraints", ""])
    for item in report.get("constraints") or []:
        lines.append(f"- `{item}`")
    lines.extend(
        [
            "",
            "## Runtime Loops",
            "",
        ]
    )
    for item in report.get("runtime", {}).get("loops", []):
        lines.append(
            f"- `{item.get('loop_id')}`: active=`{item.get('active')}`, status=`{item.get('status')}`, "
            f"pid_alive=`{item.get('pid_alive')}`, fresh=`{item.get('fresh')}`"
        )
    lines.extend(
        [
            "",
            "## Best Next Use",
            "",
            report.get("next_best_use_of_strengths") or "",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(report: dict[str, Any], out_prefix: str, root: Path) -> None:
    prefix = resolve_path(out_prefix, root)
    write_json(prefix.with_suffix(".json"), report)
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evidence-bounded Angel audit of the Trading OS.")
    parser.add_argument("--active-root", default=str(ROOT))
    parser.add_argument("--devil-report")
    parser.add_argument("--frontier-report")
    parser.add_argument("--execution-gate")
    parser.add_argument("--replication-monitor")
    parser.add_argument("--microstructure-gate")
    parser.add_argument("--observer-durability-drill")
    parser.add_argument("--book-coverage-diagnostic")
    parser.add_argument("--prereg-independence-audit")
    parser.add_argument("--seal-pipeline-drill")
    parser.add_argument("--rollout-handoff-drill")
    parser.add_argument("--microstructure-cost-audit")
    parser.add_argument("--candidate-binding-drill")
    parser.add_argument("--source-integrity-guard")
    parser.add_argument("--out-prefix")
    args = parser.parse_args()
    root = resolve_path(args.active_root)
    out_prefix = args.out_prefix or f"docs/FULL_SYSTEM_ANGEL_AUDIT_{today_tag()}"
    report = build_report(args)
    write_outputs(report, out_prefix, root)
    print(
        json.dumps(
            {
            "decision": report["decision"],
            "verified_strengths": report["strength_counts"]["verified"],
            "conditional_strengths": report["strength_counts"]["conditional"],
            "active_loops": report["runtime"]["active_count"],
            "stopped_or_stale": report["runtime"]["stopped_or_stale"],
            "can_trade": report["can_trade"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not report["runtime"]["safety_violations"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

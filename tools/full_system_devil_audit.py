#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOL_ROOT = Path(__file__).resolve().parents[1]
CURATED_DIRS = ("tools", "ops", "portable", "scripts", "adapters", "v7", "smartmoney", "bitevo", "configs")
EXCLUDED_NAMES = {"telegram.env", ".env"}
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}
CURATED_SUFFIXES = {".py", ".ps1", ".json", ".md", ".yaml", ".yml", ".txt", ".toml"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def latest_json(root: Path, pattern: str, *, exclude_contains: tuple[str, ...] = ()) -> tuple[Path | None, dict[str, Any]]:
    excludes = tuple(item.upper() for item in exclude_contains)
    matches = [
        path
        for path in root.glob(pattern)
        if path.is_file() and not any(item in path.name.upper() for item in excludes)
    ]
    if not matches:
        return None, {}
    latest = max(matches, key=lambda path: path.stat().st_mtime)
    return latest, read_json(latest)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def curated_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    # MANIFEST.json is generated and contains build-time/file-set metadata that
    # can legitimately differ between source and active runtime docs. Source
    # parity should compare curated code/config surfaces, not the manifest's own
    # timestamp or runtime output inventory.
    candidates = [root / "README.md"]
    for directory in CURATED_DIRS:
        base = root / directory
        if base.exists():
            candidates.extend(path for path in base.rglob("*") if path.is_file())
    for path in candidates:
        if not path.exists() or path.name in EXCLUDED_NAMES or path.suffix.lower() not in CURATED_SUFFIXES:
            continue
        rel = path.relative_to(root)
        if any(part in EXCLUDED_PARTS for part in rel.parts):
            continue
        result[rel.as_posix()] = sha256(path)
    return result


def finding(severity: str, finding_id: str, title: str, evidence: Any, action: str, status: str = "open") -> dict[str, Any]:
    return {
        "severity": severity,
        "id": finding_id,
        "title": title,
        "evidence": evidence,
        "action": action,
        "status": status,
    }


def build_report(active_root: Path, source_root: Path) -> dict[str, Any]:
    source_hashes = curated_files(source_root)
    active_hashes = curated_files(active_root) if active_root.exists() else {}
    missing = sorted(path for path in source_hashes if path not in active_hashes)
    different = sorted(path for path, digest in source_hashes.items() if path in active_hashes and active_hashes[path] != digest)

    docs = active_root / "docs"
    strategy_map = read_json(active_root / "docs" / "ACTIVE_STRATEGY_RUNTIME_MAP_2026-06-22.json")
    health = read_json(active_root / "docs" / "FORWARD_RUNTIME_HEALTH_2026-06-16.json")
    scheduler = read_json(active_root / "docs" / "STRATEGY_MIX_FORWARD_SCHEDULER_2026-06-08.json")
    crowd_score = read_json(active_root / "docs" / "CROWD_FADE_POSITIONING_SHADOW_SCOREBOARD_2026-06-19.json")
    crowd_gate = read_json(active_root / "docs" / "CROWD_FADE_POSITIONING_PROMOTION_GATE_2026-06-19.json")
    crowd_diagnostic = read_json(active_root / "docs" / "CROWD_FADE_POSITIONING_DIAGNOSTIC_2026-06-19.json")
    crowd_lock = read_json(active_root / "configs" / "CROWD_FADE_FORWARD_LOCK.json")
    trend_lock = read_json(active_root / "configs" / "TREND_MIX_FORWARD_LOCK.json")
    trend_nested = read_json(active_root / "docs" / "TREND_MIX_NESTED_HOLDOUT_2026-06-23.json")
    portfolio = read_json(active_root / "docs" / "FOUR_FAMILY_FORWARD_PORTFOLIO_SCOREBOARD_2026-06-22.json")
    lifecycle = read_json(active_root / "docs" / "FORWARD_EVIDENCE_LIFECYCLE_2026-06-23.json")
    range_edge_nested = read_json(active_root / "docs" / "RANGE_EDGE_NESTED_HOLDOUT_2026-06-23.json")
    restore_drill = read_json(active_root / "docs" / "RUNTIME_BACKUP_RESTORE_DRILL_2026-06-22.json")
    backup = read_json(active_root / "logs" / "runtime_backup" / "daily_drive_backup_last_run.json")
    repair = read_json(active_root / "logs" / "runtime_safe_repair_last_run.json")
    repair_script_path = active_root / "ops" / "autostart" / "Repair-TradingOSRuntime.ps1"
    _, core_readiness = latest_json(docs, "TRADINGOS_CORE_READINESS_EDGE_REPORT_20*.json")
    _, autopilot = latest_json(docs, "CROSS_VENUE_MICROSTRUCTURE_AUTOPILOT_AUDIT_20*.json")
    _, post_seal_guard = latest_json(docs, "CROSS_VENUE_MICROSTRUCTURE_POST_SEAL_AUTO_RUN_GUARD_20*.json")
    _, post_snapshot_launch = latest_json(docs, "CROSS_VENUE_MICROSTRUCTURE_POST_SNAPSHOT_LAUNCH_AUDIT_20*.json")
    _, snapshot_gate = latest_json(docs, "CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_20*.json", exclude_contains=("TELEGRAM",))
    _, snapshot_transition = latest_json(docs, "CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_TRANSITION_MONITOR_20*.json")
    _, microstructure_health = latest_json(docs, "CROSS_VENUE_MICROSTRUCTURE_HEALTH_20*.json", exclude_contains=("TELEGRAM",))
    _, microstructure_quality = latest_json(docs, "CROSS_VENUE_MICROSTRUCTURE_DATA_QUALITY_20*.json")
    _, oi_funding_matrix = latest_json(docs, "OI_FUNDING_DATA_QUALITY_MATRIX_20*.json")
    _, frontier_matrix = latest_json(docs, "STRATEGY_RESEARCH_FRONTIER_MATRIX_20*.json")
    _, derivatives_research_matrix = latest_json(docs, "DERIVATIVES_EVENT_RESEARCH_MATRIX_20*.json")
    _, context_evidence_matrix = latest_json(docs, "CONTEXT_EVIDENCE_MATRIX_20*.json")
    _, drift_audit = latest_json(docs, "DERIVATIVES_EVENT_RUNTIME_DRIFT_AUDIT_20*.json")
    _, execution_realism_gate = latest_json(docs, "EXECUTION_REALISM_PROMOTION_GATE_20*.json")
    _, observer_durability_drill = latest_json(docs, "OBSERVER_LOOP_DURABILITY_DRILL_20*.json")
    _, seal_pipeline_drill = latest_json(docs, "CROSS_VENUE_MICROSTRUCTURE_SEAL_PIPELINE_DRILL_20*.json")
    _, source_integrity = latest_json(docs, "ACTIVE_SOURCE_INTEGRITY_GUARD*.json")
    try:
        repair_script = repair_script_path.read_text(encoding="utf-8-sig")
    except OSError:
        repair_script = ""
    try:
        control_panel_script = (active_root / "ops" / "control_panel" / "control_panel.py").read_text(encoding="utf-8-sig")
    except OSError:
        control_panel_script = ""

    strategies = strategy_map.get("strategies") if isinstance(strategy_map.get("strategies"), list) else []
    promotions = [str(item.get("promotion")) for item in strategies if isinstance(item, dict)]
    latest_cycle = scheduler.get("latest_cycle") if isinstance(scheduler.get("latest_cycle"), dict) else {}
    executable_steps = [
        {"name": name, "exit_code": value.get("exit_code")}
        for name, value in latest_cycle.items()
        if isinstance(value, dict) and "exit_code" in value
    ]
    nonzero_steps = [item for item in executable_steps if item.get("exit_code") != 0]
    hard_failures = [
        item.get("name")
        for item in health.get("gates", [])
        if isinstance(item, dict) and item.get("severity") == "hard" and not item.get("passed")
    ]
    crowd_summary = crowd_score.get("summary") if isinstance(crowd_score.get("summary"), dict) else {}
    crowd_coverage = crowd_diagnostic.get("coverage") if isinstance(crowd_diagnostic.get("coverage"), list) else []
    crowd_long_history = any(
        isinstance(item, dict)
        and int(item.get("matched_bars") or 0) >= 10_000
        and str(item.get("first_match") or "")[:4] <= "2021"
        for item in crowd_coverage
    )
    crowd_historically_rejected = (
        crowd_lock.get("enabled") is False
        and str(crowd_lock.get("status") or "").startswith("historically_rejected")
    )
    trend_historically_rejected = (
        trend_lock.get("family") == "TREND_MIX_4H"
        and trend_lock.get("enabled") is False
        and str(trend_lock.get("status") or "").startswith("historically_rejected")
        and trend_lock.get("boundaries", {}).get("can_trade") is False
    )
    trend_paper_shadow_safe = (
        trend_lock.get("family") == "TREND_MIX_4H"
        and trend_lock.get("enabled") is True
        and str(trend_lock.get("status") or "").startswith("paper_shadow_collecting")
        and trend_lock.get("boundaries", {}).get("can_trade") is False
        and trend_lock.get("boundaries", {}).get("paper_execution_allowed") is False
        and trend_lock.get("boundaries", {}).get("live_execution_allowed") is False
        and trend_lock.get("boundaries", {}).get("sends_orders") is False
    )
    trend_nested_proven = (
        trend_nested.get("method") == "train_only_grid_selection_then_single_untouched_calendar_oos"
        and trend_nested.get("selection_frozen_before_oos") is True
        and trend_nested.get("decision") == "reject_oos_gate_failed"
        and trend_nested.get("can_trade") is False
    )
    portfolio_families = portfolio.get("families") if isinstance(portfolio.get("families"), list) else []
    portfolio_proven = (
        len(portfolio_families) == 4
        and portfolio.get("runtime_boundary", {}).get("can_trade") is False
        and portfolio.get("can_trade") is False
    )
    restore_proven = (
        restore_drill.get("decision") == "runtime_backup_restore_drill_passed"
        and restore_drill.get("all_hashes_match") is True
        and restore_drill.get("runtime_boundary", {}).get("restores_into_active_runtime") is False
    )
    repair_budget_proven = all(
        token in repair_script
        for token in ("MaxRepairs", "WindowMinutes", "blocked_restart_budget_exhausted", "repair_timestamps")
    )
    observer_durability_proven = (
        observer_durability_drill.get("decision") == "observer_loop_durability_drill_passed"
        and observer_durability_drill.get("repair", {}).get("decision") == "dry_run_restart_ready"
        and observer_durability_drill.get("missing_matched_gates") == []
        and observer_durability_drill.get("can_trade") is False
    )
    seal_pipeline_drill_checks = (
        seal_pipeline_drill.get("checks") if isinstance(seal_pipeline_drill.get("checks"), dict) else {}
    )
    seal_pipeline_drill_proven = (
        seal_pipeline_drill.get("decision") == "microstructure_seal_pipeline_drill_passed"
        and seal_pipeline_drill.get("runner_completed") == 4
        and seal_pipeline_drill.get("runner_failed") == 0
        and seal_pipeline_drill.get("runner_tested_total") == 774
        and seal_pipeline_drill_checks
        and all(value is True for value in seal_pipeline_drill_checks.values())
        and seal_pipeline_drill.get("can_trade") is False
    )
    lifecycle_families = lifecycle.get("families") if isinstance(lifecycle.get("families"), list) else []
    lifecycle_proven = (
        len(lifecycle_families) == 4
        and lifecycle.get("can_trade") is False
        and lifecycle.get("boundaries", {}).get("sends_orders") is False
        and lifecycle.get("boundaries", {}).get("changes_strategy_parameters") is False
    )
    nested_families = range_edge_nested.get("families") if isinstance(range_edge_nested.get("families"), list) else []
    nested_by_family = {
        str(item.get("family")): item for item in nested_families if isinstance(item, dict) and item.get("family")
    }
    range_nested = nested_by_family.get("RANGE_REFINED_4H", {})
    edge_nested = nested_by_family.get("EDGE_FORWARD_4H", {})
    nested_proven = (
        range_edge_nested.get("method") == "train_only_nested_selection_then_untouched_calendar_oos"
        and range_edge_nested.get("selection_frozen_before_oos") is True
        and range_edge_nested.get("can_trade") is False
        and len(nested_families) == 2
    )
    range_lifecycle = next((item for item in lifecycle_families if item.get("family") == "RANGE_REFINED_4H"), {})
    core_scoreboard = core_readiness.get("scoreboard") if isinstance(core_readiness.get("scoreboard"), dict) else {}
    runtime_checks = core_scoreboard.get("runtime_checks") if isinstance(core_scoreboard.get("runtime_checks"), dict) else {}
    data_checks = core_scoreboard.get("data_checks") if isinstance(core_scoreboard.get("data_checks"), dict) else {}
    strategy_checks = core_scoreboard.get("strategy_checks") if isinstance(core_scoreboard.get("strategy_checks"), dict) else {}
    core_blockers = core_readiness.get("blockers") if isinstance(core_readiness.get("blockers"), list) else []
    autopilot_failed = autopilot.get("failed_checks") if isinstance(autopilot.get("failed_checks"), list) else []
    post_snapshot_failed = post_snapshot_launch.get("failed_checks") if isinstance(post_snapshot_launch.get("failed_checks"), list) else []
    snapshot_failed = snapshot_gate.get("summary", {}).get("failed") if isinstance(snapshot_gate.get("summary"), dict) else []
    if not isinstance(snapshot_failed, list):
        snapshot_failed = []
    frontier_summary = frontier_matrix.get("summary") if isinstance(frontier_matrix.get("summary"), dict) else {}
    execution_realism_gate_failed = (
        execution_realism_gate.get("failed_checks")
        if isinstance(execution_realism_gate.get("failed_checks"), list)
        else []
    )
    execution_realism_promotion = (
        execution_realism_gate.get("promotion")
        if isinstance(execution_realism_gate.get("promotion"), dict)
        else {}
    )
    oi_matrix_summary = oi_funding_matrix.get("summary") if isinstance(oi_funding_matrix.get("summary"), dict) else {}
    microstructure_ready = microstructure_quality.get("research_readiness", {}).get("ready") if isinstance(microstructure_quality.get("research_readiness"), dict) else None
    control_panel_tasks_present = all(
        token in control_panel_script
        for token in (
            "microstructure_autopilot_audit",
            "microstructure_post_seal_auto_run_guard",
            "microstructure_post_snapshot_launch_audit",
            "tradingos_core_readiness_edge_report",
            "dialectic_audit_pack",
            "observer_loop_durability_drill",
        )
    )
    post_seal_guard_failed = post_seal_guard.get("failed_checks") if isinstance(post_seal_guard.get("failed_checks"), list) else []
    post_seal_guard_ready = (
        bool(post_seal_guard)
        and post_seal_guard.get("can_trade") is False
        and not post_seal_guard_failed
        and str(post_seal_guard.get("decision") or "") in {
            "post_seal_auto_run_guard_armed_waiting_for_snapshot",
            "post_seal_auto_run_guard_would_execute_once",
            "post_seal_auto_run_guard_duplicate_blocked_already_completed",
            "post_seal_auto_run_guard_executed_locked_runner_once",
        }
    )

    findings: list[dict[str, Any]] = []
    if strategy_map.get("can_trade") is not False or health.get("can_trade") is not False:
        findings.append(finding("P0", "execution_boundary", "Execution boundary is not locked", {"strategy_can_trade": strategy_map.get("can_trade"), "health_can_trade": health.get("can_trade")}, "Stop runtime and restore can_trade=false."))
    if missing or different:
        findings.append(finding("P1", "source_runtime_drift", "Active runtime differs from curated source", {"missing": missing[:50], "different": different[:50]}, "Redeploy and require manifest parity before restart."))
    if source_integrity and source_integrity.get("decision") != "active_source_integrity_clean":
        findings.append(
            finding(
                "P1",
                "active_source_integrity_drift",
                "Reviewed Active source hash lock detected drift",
                {
                    "decision": source_integrity.get("decision"),
                    "drift_count": source_integrity.get("drift_count"),
                    "missing": source_integrity.get("missing"),
                    "changed": source_integrity.get("changed"),
                    "untracked": source_integrity.get("untracked"),
                },
                "Quarantine the drift, restore or review it explicitly, then reseal the integrity lock.",
            )
        )
    if health.get("classification") != "forward_runtime_healthy_observing" or hard_failures:
        findings.append(finding("P1", "runtime_health", "Runtime health is degraded", {"classification": health.get("classification"), "hard_failures": hard_failures}, "Repair infrastructure only; do not hide strategy/data failures."))
    if nonzero_steps:
        findings.append(finding("P1", "scheduler_failures", "Latest scheduler cycle contains non-zero steps", nonzero_steps, "Fix failed chain before trusting observers."))
    if len(strategies) != 4:
        findings.append(finding("P1", "strategy_count_drift", "Independent strategy family count drifted", {"actual": len(strategies), "required": 4}, "Rebuild active strategy inventory."))
    if core_readiness and runtime_checks.get("passed") != runtime_checks.get("total"):
        findings.append(
            finding(
                "P1",
                "core_readiness_runtime_failed",
                "Core readiness runtime checks are not all passing",
                {"runtime_checks": runtime_checks, "blockers": core_blockers},
                "Repair runtime/autopilot before relying on any observer or post-snapshot automation.",
            )
        )
    if autopilot and autopilot_failed:
        findings.append(
            finding(
                "P1",
                "microstructure_autopilot_failed",
                "Microstructure autopilot has failed checks",
                {"decision": autopilot.get("decision"), "failed_checks": autopilot_failed},
                "Fix watchdog/collector handoff before waiting for sealed snapshot.",
            )
        )
    if post_snapshot_launch and post_snapshot_failed:
        findings.append(
            finding(
                "P1",
                "post_snapshot_launch_not_ready",
                "Post-snapshot launch chain is not ready",
                {"decision": post_snapshot_launch.get("decision"), "failed_checks": post_snapshot_failed},
                "Repair locked research/governance/validation-skeleton chain before the snapshot gate opens.",
            )
        )
    if not control_panel_tasks_present:
        findings.append(
            finding(
                "P2",
                "control_panel_task_registry_incomplete",
                "Control panel does not expose the current safety audits",
                {
                    "required_tasks": [
                        "microstructure_autopilot_audit",
                        "microstructure_post_seal_auto_run_guard",
                        "microstructure_post_snapshot_launch_audit",
                        "tradingos_core_readiness_edge_report",
                    ]
                },
                "Expose current safety audits in the pult so runtime proof can be triggered without shell access.",
            )
        )
    if post_snapshot_launch and not post_seal_guard_ready:
        findings.append(
            finding(
                "P2",
                "post_seal_auto_run_guard_not_proven",
                "Post-seal one-shot runner guard is not proven",
                {"decision": post_seal_guard.get("decision"), "failed_checks": post_seal_guard_failed},
                "Run the post-seal guard report and keep execution routed through it before relying on the research runner.",
            )
        )

    findings.append(
        finding(
            "P1",
            "profitability_unproven",
            "No strategy has enough independent forward evidence for paper/live execution",
            {"promotions": promotions, "can_trade": strategy_map.get("can_trade")},
            "Accumulate independent resolved outcomes and reject weak families; do not optimize on tiny samples.",
        )
    )
    findings.append(
        finding(
            "P2",
            "observer_loop_self_heal_unproven",
            "Microstructure and real-edge observer loops need bounded self-heal proof",
            {
                "decision": observer_durability_drill.get("decision"),
                "repair_decision": observer_durability_drill.get("repair", {}).get("decision"),
                "matched_gates": observer_durability_drill.get("repair", {}).get("matched_repairable_gates"),
                "missing_gates": observer_durability_drill.get("missing_matched_gates"),
            },
            "Keep observer PID/freshness gates repairable under the existing restart budget and retain a dry-run drill.",
            status="remediated" if observer_durability_proven else "open",
        )
    )
    if trend_nested:
        findings.append(
            finding(
                "P1",
                "trend_historical_invalidation",
                "Legacy TREND_MIX train winner failed untouched calendar OOS",
                {
                    "decision": trend_nested.get("decision"),
                    "current_lock_status": trend_lock.get("status"),
                    "current_lock_can_trade": trend_lock.get("boundaries", {}).get("can_trade"),
                    "train": trend_nested.get("selected_on_train", {}).get("train", {}).get("summary"),
                    "oos": trend_nested.get("oos", {}).get("summary"),
                    "oos_gate": trend_nested.get("oos_gate"),
                },
                "Keep the legacy winner rejected; allow only explicitly locked observer-only replacements with can_trade=false.",
                status="remediated" if trend_nested_proven and (trend_historically_rejected or trend_paper_shadow_safe) else "open",
            )
        )
    findings.append(
        finding(
            "P2",
            "strategy_lifecycle_governance",
            "Strategy promotion and rejection need precommitted forward-evidence checkpoints",
            {
                "decision": lifecycle.get("decision"),
                "families": [item.get("family") for item in lifecycle_families if isinstance(item, dict)],
                "states": [item.get("state") for item in lifecycle_families if isinstance(item, dict)],
            },
            "Apply fixed observe, pause, reject and paper-design-review rules without enabling execution.",
            status="remediated" if lifecycle_proven else "open",
        )
    )
    if range_nested:
        findings.append(
            finding(
                "P1",
                "range_historical_invalidation",
                "RANGE train edge failed untouched calendar OOS",
                {
                    "decision": range_nested.get("decision"),
                    "train": range_nested.get("selected_on_train", {}).get("train", {}).get("summary"),
                    "oos": range_nested.get("oos", {}).get("summary"),
                },
                "Keep RANGE rejected and do not retune it on the opened OOS period.",
                status="remediated" if str(range_lifecycle.get("state") or "").startswith("rejected_") else "open",
            )
        )
    if edge_nested:
        findings.append(
            finding(
                "P2",
                "edge_oos_sample_insufficient",
                "EDGE has positive OOS economics but not enough independent trades",
                {
                    "decision": edge_nested.get("decision"),
                    "oos": edge_nested.get("oos", {}).get("summary"),
                    "gate": edge_nested.get("oos_gate"),
                },
                "Keep observer-only; freeze parameters and collect the missing independent sample without using OOS for reselection.",
            )
        )
    if snapshot_gate.get("decision") not in {"microstructure_snapshot_sealed", "snapshot_already_sealed_for_readiness_epoch"}:
        findings.append(
            finding(
                "P2",
                "microstructure_snapshot_not_sealed",
                "Microstructure research snapshot is not sealed yet",
                {
                    "decision": snapshot_gate.get("decision"),
                    "failed_checks": snapshot_failed,
                    "transition_state": snapshot_transition.get("transition_state"),
                    "remaining_hours": snapshot_transition.get("remaining_hours"),
                    "research_ready": microstructure_ready,
                },
                "Keep collecting. Do not run validation/OOS/paper/live until the exact sealed snapshot exists.",
            )
        )
    if microstructure_health and microstructure_health.get("classification") not in {
        "cross_venue_microstructure_healthy_collecting",
        "cross_venue_microstructure_healthy_research_ready",
    }:
        findings.append(
            finding(
                "P1",
                "microstructure_health_degraded",
                "Microstructure collector health is degraded",
                {"classification": microstructure_health.get("classification"), "failed_hard_gates": microstructure_health.get("failed_hard_gates")},
                "Fix collector health before trusting any sealed snapshot.",
            )
        )
    if oi_funding_matrix and oi_funding_matrix.get("decision") != "oi_funding_quality_ready_for_research":
        findings.append(
            finding(
                "P1",
                "oi_funding_matrix_degraded",
                "OI/funding matrix is not research-ready",
                {"decision": oi_funding_matrix.get("decision"), "summary": oi_matrix_summary},
                "Use OI/funding only on intervals explicitly marked ready; repair coverage before adding filters.",
            )
        )
    if frontier_matrix and int(frontier_summary.get("promotable") or 0) == 0:
        findings.append(
            finding(
                "P1",
                "no_promotable_strategy_frontier",
                "Strategy research frontier has no promotable family",
                {"decision": frontier_matrix.get("decision"), "summary": frontier_summary},
                "Do not promote observers. Continue pre-registered microstructure research or collect forward outcomes.",
            )
        )
    if not execution_realism_gate or execution_realism_gate_failed:
        findings.append(
            finding(
                "P1",
                "execution_realism_gate_not_clean",
                "Execution-realism promotion gate is missing or failed",
                {
                    "decision": execution_realism_gate.get("decision"),
                    "failed_checks": execution_realism_gate_failed,
                    "promotion": execution_realism_promotion,
                },
                "Run execution_realism_shadow_overlay and execution_realism_promotion_gate before any promotion discussion.",
            )
        )
    if derivatives_research_matrix and int(derivatives_research_matrix.get("summary", {}).get("promotable") or 0) == 0:
        findings.append(
            finding(
                "P2",
                "derivatives_event_matrix_no_promotable",
                "Derivatives-event strategy matrix has no promotable candidate",
                {"decision": derivatives_research_matrix.get("decision"), "summary": derivatives_research_matrix.get("summary")},
                "Do not loosen gates; only test predeclared new mechanisms or stronger context filters.",
            )
        )
    if context_evidence_matrix and int(context_evidence_matrix.get("summary", {}).get("ready_for_integration") or 0) == 0:
        findings.append(
            finding(
                "P2",
                "context_evidence_not_ready",
                "Context evidence layer has no ready filter for integration",
                {"decision": context_evidence_matrix.get("decision"), "summary": context_evidence_matrix.get("summary")},
                "Keep context as research notes until a precommitted integration test passes.",
            )
        )
    if drift_audit and drift_audit.get("decision") != "source_runtime_in_sync":
        findings.append(
            finding(
                "P1",
                "runtime_drift_detected",
                "Latest runtime drift audit is not clean",
                {"decision": drift_audit.get("decision"), "data_drift_count": drift_audit.get("data_drift_count"), "report_drift_count": drift_audit.get("report_drift_count")},
                "Reconcile or redeploy before accepting any runtime-generated evidence.",
            )
        )
    if (crowd_summary.get("resolved") or 0) > 0 and (crowd_summary.get("expectancy_r") or 0) < 0:
        findings.append(
            finding(
                "P1",
                "crowd_forward_negative",
                "Crowd Fade first independent forward evidence is negative",
                {"resolved": crowd_summary.get("resolved"), "expectancy_r": crowd_summary.get("expectancy_r"), "winrate_pct": crowd_summary.get("winrate_pct")},
                "Keep blocked; if broader history invalidates the candidate, reject it instead of waiting for more forward outcomes.",
                status="remediated" if crowd_historically_rejected else "open",
            )
        )
    if crowd_historically_rejected:
        findings.append(
            finding(
                "P1",
                "crowd_historical_invalidation",
                "Crowd Fade locked candidate fails broader historical validation",
                crowd_lock.get("invalidation"),
                "Keep the candidate rejected; preserve its journal and do not auto-select a replacement from the same search.",
                status="remediated",
            )
        )
    overlap_suppressed = int(crowd_score.get("overlap_suppressed_events") or 0)
    if overlap_suppressed:
        findings.append(
            finding(
                "P1",
                "crowd_overlap_accounting",
                "Crowd raw signals included overlapping positions",
                {"raw": crowd_score.get("raw_unique_signal_events"), "independent": crowd_score.get("independent_signal_events"), "suppressed": overlap_suppressed},
                "Count only independent non-overlapping outcomes in scoreboard and promotion gate.",
                status="remediated",
            )
        )
    findings.append(
        finding(
            "P1",
            "telegram_token_exposed",
            "Telegram bot token was disclosed in conversation history",
            {"repo_secret_file_excluded_from_manifest": True},
            "Rotate the bot token before any production or financially sensitive notification workflow.",
        )
    )
    findings.append(
        finding(
            "P2",
            "crowd_history_short",
            "Crowd diagnostic requires multi-year matched history",
            {"decision": crowd_diagnostic.get("decision"), "coverage": crowd_coverage},
            "Treat as forward observer only; acquire independent longer history before parameter changes.",
            status="remediated" if crowd_long_history else "open",
        )
    )
    findings.append(
        finding(
            "P2",
            "backup_restore_unproven",
            "Daily backup restore integrity must be proven",
            {
                "backup_status": backup.get("status"),
                "backup_ts": backup.get("ts"),
                "restore_decision": restore_drill.get("decision"),
                "sampled_files": restore_drill.get("sampled_files"),
                "all_hashes_match": restore_drill.get("all_hashes_match"),
            },
            "Run a bounded restore-to-temp validation and compare checksums.",
            status="remediated" if restore_proven else "open",
        )
    )
    findings.append(
        finding(
            "P2",
            "repair_rate_limit",
            "Infrastructure self-heal needs a restart-storm circuit breaker",
            {
                "last_repair": repair.get("decision"),
                "repairable_gates": repair.get("matched_repairable_gates"),
                "max_repairs_in_window": repair.get("max_repairs_in_window"),
                "window_minutes": repair.get("window_minutes"),
                "active_script_has_budget": repair_budget_proven,
            },
            "Add rolling restart budget and cooldown before unattended operation.",
            status="remediated" if repair_budget_proven else "open",
        )
    )
    findings.append(
        finding(
            "P2",
            "portfolio_scoreboard_missing",
            "Four families need one normalized forward portfolio scoreboard",
            {
                "runtime_families": [item.get("family") for item in strategies if isinstance(item, dict)],
                "scoreboard_families": [item.get("family") for item in portfolio_families if isinstance(item, dict)],
                "decision": portfolio.get("decision"),
            },
            "Standardize independent signals, resolved R, sample size, drawdown and correlation across families.",
            status="remediated" if portfolio_proven else "open",
        )
    )

    open_findings = [item for item in findings if item["status"] == "open"]
    severity_counts = {level: sum(1 for item in open_findings if item["severity"] == level) for level in ("P0", "P1", "P2", "P3")}
    return {
        "generated_at": now_iso(),
        "audit_mode": "devils_advocate_runtime_proof",
        "source_root": str(source_root),
        "active_root": str(active_root),
        "runtime": {
            "health": health.get("classification"),
            "hard_failures": hard_failures,
            "strategy_families": len(strategies),
            "scheduler_executable_steps": len(executable_steps),
            "scheduler_nonzero_steps": nonzero_steps,
            "core_readiness_decision": core_readiness.get("decision"),
            "core_runtime_checks": runtime_checks,
            "can_trade": False,
        },
        "microstructure": {
            "health": microstructure_health.get("classification"),
            "snapshot_gate": snapshot_gate.get("decision"),
            "snapshot_transition": snapshot_transition.get("transition_state"),
            "remaining_hours": snapshot_transition.get("remaining_hours"),
            "autopilot_decision": autopilot.get("decision"),
            "autopilot_failed_checks": autopilot_failed,
            "post_snapshot_launch_decision": post_snapshot_launch.get("decision"),
            "post_snapshot_failed_checks": post_snapshot_failed,
            "post_seal_guard_decision": post_seal_guard.get("decision"),
            "post_seal_guard_failed_checks": post_seal_guard_failed,
            "control_panel_tasks_present": control_panel_tasks_present,
        },
        "research_frontier": {
            "decision": frontier_matrix.get("decision"),
            "summary": frontier_summary,
            "oi_funding_matrix_decision": oi_funding_matrix.get("decision"),
            "oi_funding_matrix_summary": oi_matrix_summary,
            "derivatives_matrix_decision": derivatives_research_matrix.get("decision"),
            "context_matrix_decision": context_evidence_matrix.get("decision"),
            "execution_realism_gate_decision": execution_realism_gate.get("decision"),
            "execution_realism_gate_failed_checks": execution_realism_gate_failed,
            "execution_realism_gate_promotion": execution_realism_promotion,
        },
        "source_runtime_parity": {
            "source_curated_files": len(source_hashes),
            "active_curated_files": len(active_hashes),
            "generated_artifacts_excluded": ["MANIFEST.json"],
            "missing_in_active": missing,
            "different_hash": different,
            "passed": not missing and not different,
        },
        "crowd_forward": {
            "raw_signals": crowd_score.get("raw_unique_signal_events"),
            "independent_signals": crowd_score.get("independent_signal_events"),
            "overlap_suppressed": crowd_score.get("overlap_suppressed_events"),
            "resolved": crowd_summary.get("resolved"),
            "expectancy_r": crowd_summary.get("expectancy_r"),
            "promotion": crowd_gate.get("decision"),
        },
        "hardening_proof": {
            "backup_restore_drill_passed": restore_proven,
            "repair_restart_budget_present": repair_budget_proven,
            "four_family_portfolio_scoreboard_present": portfolio_proven,
            "forward_evidence_lifecycle_present": lifecycle_proven,
            "range_edge_nested_holdout_present": nested_proven,
            "trend_nested_holdout_present": trend_nested_proven,
            "microstructure_autopilot_clean": bool(autopilot) and not autopilot_failed,
            "post_seal_auto_run_guard_ready": post_seal_guard_ready,
            "post_snapshot_launch_ready": bool(post_snapshot_launch) and not post_snapshot_failed,
            "control_panel_current_safety_tasks_present": control_panel_tasks_present,
            "execution_realism_promotion_gate_clean": bool(execution_realism_gate) and not execution_realism_gate_failed,
            "observer_loop_self_heal_dry_run_proven": observer_durability_proven,
            "microstructure_seal_pipeline_drill_proven": seal_pipeline_drill_proven,
            "active_source_integrity_clean": source_integrity.get("decision") == "active_source_integrity_clean",
        },
        "findings": findings,
        "open_severity_counts": severity_counts,
        "decision": "operational_runtime_healthy_but_edge_unproven" if severity_counts["P0"] == 0 else "stop_runtime_boundary_failure",
        "next_strong_move": "keep live trading locked; keep TREND, RANGE and CROWD rejected; continue microstructure collection until exact sealed snapshot, then allow only the locked research runner/governance chain; do not promote any family without independent forward or validation evidence",
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Full System Devil's Advocate Audit",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Active root: `{report.get('active_root')}`",
        f"- Can trade: `{report.get('can_trade')}`",
        "",
        "## Findings",
        "",
    ]
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    for item in sorted(report.get("findings", []), key=lambda row: (order.get(row.get("severity"), 9), row.get("id", ""))):
        lines.extend(
            [
                f"### {item.get('severity')} `{item.get('id')}` - {item.get('title')}",
                "",
                f"- Status: `{item.get('status')}`",
                f"- Evidence: `{json.dumps(item.get('evidence'), ensure_ascii=False)}`",
                f"- Action: {item.get('action')}",
                "",
            ]
        )
    parity = report.get("source_runtime_parity", {})
    runtime = report.get("runtime", {})
    microstructure = report.get("microstructure", {})
    frontier = report.get("research_frontier", {})
    crowd = report.get("crowd_forward", {})
    hardening = report.get("hardening_proof", {})
    lines.extend(
        [
            "## Runtime Proof",
            "",
            f"- Health: `{runtime.get('health')}`; hard failures: `{runtime.get('hard_failures')}`.",
            f"- Core readiness: `{runtime.get('core_readiness_decision')}`; runtime checks: `{runtime.get('core_runtime_checks')}`.",
            f"- Strategies: `{runtime.get('strategy_families')}`; scheduler steps: `{runtime.get('scheduler_executable_steps')}`; non-zero: `{runtime.get('scheduler_nonzero_steps')}`.",
            f"- Source/runtime parity: `{parity.get('passed')}`; missing `{len(parity.get('missing_in_active', []))}`; different `{len(parity.get('different_hash', []))}`.",
            "",
            "## Microstructure / Research Frontier",
            "",
            f"- Microstructure health: `{microstructure.get('health')}`.",
            f"- Snapshot gate / transition / remaining hours: `{microstructure.get('snapshot_gate')}` / `{microstructure.get('snapshot_transition')}` / `{microstructure.get('remaining_hours')}`.",
            f"- Autopilot: `{microstructure.get('autopilot_decision')}` failed `{microstructure.get('autopilot_failed_checks')}`.",
            f"- Post-seal guard: `{microstructure.get('post_seal_guard_decision')}` failed `{microstructure.get('post_seal_guard_failed_checks')}`.",
            f"- Post-snapshot launch: `{microstructure.get('post_snapshot_launch_decision')}` failed `{microstructure.get('post_snapshot_failed_checks')}`.",
            f"- Control panel safety tasks present: `{microstructure.get('control_panel_tasks_present')}`.",
            f"- Strategy frontier: `{frontier.get('decision')}`; summary `{frontier.get('summary')}`.",
            f"- OI/funding matrix: `{frontier.get('oi_funding_matrix_decision')}`; summary `{frontier.get('oi_funding_matrix_summary')}`.",
            f"- Derivatives/context matrices: `{frontier.get('derivatives_matrix_decision')}` / `{frontier.get('context_matrix_decision')}`.",
            "",
            "## Forward / Hardening",
            "",
            f"- Crowd raw/independent/suppressed: `{crowd.get('raw_signals')}` / `{crowd.get('independent_signals')}` / `{crowd.get('overlap_suppressed')}`.",
            f"- Crowd resolved/expectancy: `{crowd.get('resolved')}` / `{crowd.get('expectancy_r')}`R.",
            f"- Restore drill / restart budget / portfolio scoreboard: `{hardening.get('backup_restore_drill_passed')}` / `{hardening.get('repair_restart_budget_present')}` / `{hardening.get('four_family_portfolio_scoreboard_present')}`.",
            f"- Forward lifecycle governance: `{hardening.get('forward_evidence_lifecycle_present')}`.",
            f"- RANGE/EDGE nested holdout: `{hardening.get('range_edge_nested_holdout_present')}`.",
            f"- Microstructure autopilot / post-seal guard / post-snapshot launch / pult tasks: `{hardening.get('microstructure_autopilot_clean')}` / `{hardening.get('post_seal_auto_run_guard_ready')}` / `{hardening.get('post_snapshot_launch_ready')}` / `{hardening.get('control_panel_current_safety_tasks_present')}`.",
            "",
            "## Next Strong Move",
            "",
            f"- {report.get('next_strong_move')}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Full bounded devil's-advocate audit of source and active Trading OS runtime.")
    parser.add_argument("--active-root", default=str(Path.home() / "TradingOS" / "Active"))
    parser.add_argument("--source-root", default="", help="Curated source root. Defaults to deployment provenance when available.")
    parser.add_argument("--out-prefix", default="docs/FULL_SYSTEM_DEVIL_AUDIT_2026-06-22")
    args = parser.parse_args()
    active_root = Path(args.active_root).resolve()
    deploy = read_json(active_root / "docs" / "LOCAL_RUNTIME_DEPLOY_2026-06-22.json")
    provenance_root = deploy.get("source_root") if isinstance(deploy.get("source_root"), str) else ""
    source_root = Path(args.source_root or provenance_root or TOOL_ROOT).resolve()
    if not source_root.exists():
        source_root = TOOL_ROOT
    report = build_report(active_root, source_root)
    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = TOOL_ROOT / out_prefix
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "open": report["open_severity_counts"], "parity": report["source_runtime_parity"]["passed"], "strategies": report["runtime"]["strategy_families"], "steps": report["runtime"]["scheduler_executable_steps"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if report["open_severity_counts"]["P0"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

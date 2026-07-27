#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def latest_json(root: Path, pattern: str, *, exclude_contains: tuple[str, ...] = ()) -> tuple[Path | None, dict[str, Any]]:
    excludes = tuple(item.upper() for item in exclude_contains)
    matches = [
        path for path in root.glob(pattern)
        if path.is_file() and not any(item in path.name.upper() for item in excludes)
    ]
    if not matches:
        return None, {}
    latest = max(matches, key=lambda path: path.stat().st_mtime)
    return latest, read_json(latest)


def get(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def bool_count(values: list[bool]) -> dict[str, int]:
    return {"passed": sum(1 for value in values if value), "total": len(values)}


def component_status(*, exists: bool, healthy: bool, blocked: bool = False) -> str:
    if not exists:
        return "missing"
    if healthy:
        return "working"
    if blocked:
        return "blocked"
    return "partial"


def build_report(root: Path = ROOT) -> dict[str, Any]:
    docs = root / "docs"
    paths: dict[str, Path | None] = {}

    paths["snapshot_gate"], snapshot_gate = latest_json(docs, "CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_20*.json")
    paths["microstructure_autopilot_audit"], microstructure_autopilot = latest_json(docs, "CROSS_VENUE_MICROSTRUCTURE_AUTOPILOT_AUDIT_20*.json")
    paths["collector_sla_replay"], sla_replay = latest_json(docs, "CROSS_VENUE_MICROSTRUCTURE_COLLECTOR_SLA_REPLAY_20*.json")
    paths["oi_funding_quality"], oi_quality = latest_json(docs, "OI_FUNDING_DATA_QUALITY*.json", exclude_contains=("MATRIX",))
    paths["oi_funding_quality_matrix"], oi_quality_matrix = latest_json(docs, "OI_FUNDING_DATA_QUALITY_MATRIX*.json")
    paths["strategy_runtime_map"], strategy_runtime = latest_json(docs, "ACTIVE_STRATEGY_RUNTIME_MAP_*.json")
    paths["strategy_polygon"], strategy_polygon = latest_json(docs, "STRATEGY_POLYGON_PARALLEL*.json")
    paths["derivatives_event_miner"], derivatives_event = latest_json(docs, "DERIVATIVES_EVENT_EDGE_MINER*.json")
    paths["derivatives_event_observer"], derivatives_observer = latest_json(docs, "DERIVATIVES_EVENT_FORWARD_OBSERVER*.json")
    paths["derivatives_event_pending_watch"], derivatives_pending_watch = latest_json(docs, "DERIVATIVES_EVENT_PENDING_WATCH*.json")
    paths["derivatives_event_scoreboard"], derivatives_scoreboard = latest_json(docs, "DERIVATIVES_EVENT_FORWARD_SCOREBOARD*.json")
    paths["derivatives_event_gate"], derivatives_gate = latest_json(docs, "DERIVATIVES_EVENT_PROMOTION_GATE*.json")
    paths["derivatives_event_notify"], derivatives_notify = latest_json(docs, "DERIVATIVES_EVENT_TELEGRAM_NOTIFY*.json")
    paths["derivatives_event_alert_drill"], derivatives_alert_drill = latest_json(docs, "DERIVATIVES_EVENT_SIGNAL_ALERT_DRILL*.json")
    paths["derivatives_event_research_matrix"], derivatives_research_matrix = latest_json(docs, "DERIVATIVES_EVENT_RESEARCH_MATRIX*.json")
    paths["context_evidence_matrix"], context_evidence_matrix = latest_json(docs, "CONTEXT_EVIDENCE_MATRIX*.json")
    paths["strategy_research_frontier_matrix"], strategy_research_frontier_matrix = latest_json(docs, "STRATEGY_RESEARCH_FRONTIER_MATRIX*.json")
    paths["forward_scoreboard"], forward_scoreboard = latest_json(docs, "STRATEGY_MIX_FORWARD_SCOREBOARD*.json")
    paths["forward_health"], forward_health = latest_json(docs, "FORWARD_RUNTIME_HEALTH_20*.json")

    snapshot_decision = snapshot_gate.get("decision")
    snapshot_checks = snapshot_gate.get("checks") if isinstance(snapshot_gate.get("checks"), dict) else {}
    snapshot_failed = get(snapshot_gate, "summary", "failed", default=[])
    if not isinstance(snapshot_failed, list):
        snapshot_failed = []

    autopilot_decision = microstructure_autopilot.get("decision")
    autopilot_failed = microstructure_autopilot.get("failed_checks")
    if not isinstance(autopilot_failed, list):
        autopilot_failed = []
    autopilot_transition_state = get(microstructure_autopilot, "snapshot", "transition_state")
    autopilot_remaining_hours = get(microstructure_autopilot, "snapshot", "remaining_hours")

    sla_decision = sla_replay.get("decision")
    oi_classification = get(oi_quality, "summary", "classification")
    oi_full_context_pct = get(oi_quality, "replay_trade_coverage", "full_context_coverage_pct")
    oi_matrix_decision = oi_quality_matrix.get("decision")
    oi_matrix_ready = get(oi_quality_matrix, "summary", "ready_intervals")
    oi_matrix_degraded = get(oi_quality_matrix, "summary", "degraded_intervals")
    oi_matrix_ready_ids = get(oi_quality_matrix, "summary", "ready_interval_ids", default=[])

    strategy_families = strategy_runtime.get("strategies") if isinstance(strategy_runtime.get("strategies"), list) else []
    active_observers = [
        item for item in strategy_families
        if isinstance(item, dict) and item.get("runtime_status") == "observer_running"
    ]
    rejected_families = strategy_runtime.get("rejected_families") if isinstance(strategy_runtime.get("rejected_families"), list) else []

    polygon_candidates = int(strategy_polygon.get("polygon_candidate_count") or 0)
    polygon_watchlist = int(strategy_polygon.get("watchlist_count") or 0)
    derivatives_decision = derivatives_event.get("decision")
    derivatives_train_qualified = int(get(derivatives_event, "summary", "train_qualified", default=0) or 0)
    derivatives_validation_qualified = int(get(derivatives_event, "summary", "validation_qualified", default=0) or 0)
    derivatives_observer_decision = derivatives_observer.get("decision")
    derivatives_observer_signal = get(derivatives_observer, "latest_observation", "signal")
    derivatives_observer_events = get(derivatives_observer, "latest_observation", "events_written")
    derivatives_pending_watch_decision = derivatives_pending_watch.get("decision")
    derivatives_pending_watch_passed = get(derivatives_pending_watch, "latest", "summary", "passed")
    derivatives_pending_watch_total = get(derivatives_pending_watch, "latest", "summary", "total")
    derivatives_pending_watch_blockers = get(derivatives_pending_watch, "latest", "summary", "blockers", default=[])
    derivatives_scoreboard_decision = derivatives_scoreboard.get("decision")
    derivatives_scoreboard_resolved = get(derivatives_scoreboard, "summary", "resolved")
    derivatives_scoreboard_expectancy = get(derivatives_scoreboard, "summary", "expectancy_r")
    derivatives_gate_decision = derivatives_gate.get("decision")
    derivatives_gate_paper_design = get(derivatives_gate, "promotion", "paper_design_review_allowed")
    derivatives_notify_decision = derivatives_notify.get("decision")
    derivatives_notify_signal = derivatives_notify.get("signal")
    derivatives_notify_events = derivatives_notify.get("events_written")
    derivatives_alert_drill_decision = derivatives_alert_drill.get("decision")
    derivatives_matrix_decision = derivatives_research_matrix.get("decision")
    derivatives_matrix_promotable = get(derivatives_research_matrix, "summary", "promotable")
    derivatives_matrix_mirages = get(derivatives_research_matrix, "summary", "validation_mirages")
    context_matrix_decision = context_evidence_matrix.get("decision")
    context_matrix_ready = get(context_evidence_matrix, "summary", "ready_for_integration")
    context_matrix_reports = get(context_evidence_matrix, "summary", "reports")
    frontier_decision = strategy_research_frontier_matrix.get("decision")
    frontier_promotable = get(strategy_research_frontier_matrix, "summary", "promotable")
    frontier_observer_only = get(strategy_research_frontier_matrix, "summary", "observer_only")
    frontier_preregistered = get(strategy_research_frontier_matrix, "summary", "preregistered")
    frontier_rejected = get(strategy_research_frontier_matrix, "summary", "rejected")
    forward_resolved = get(forward_scoreboard, "summary", "resolved", default=get(forward_scoreboard, "resolved", default=None))
    forward_expectancy = get(forward_scoreboard, "summary", "expectancy_r", default=get(forward_scoreboard, "expectancy_r", default=None))

    data_checks = [
        oi_classification == "oi_guard_data_ready",
        oi_matrix_decision in {None, "oi_funding_quality_ready_for_research"},
        sla_decision in {"collector_sla_replay_stable", "collector_sla_replay_missing_history"},
        snapshot_checks.get("policy_locked") is True,
        snapshot_checks.get("source_can_trade_false") is True,
    ]
    strategy_checks = [
        bool(active_observers),
        polygon_candidates > 0 or polygon_watchlist > 0 or len(strategy_families) > 0,
        strategy_runtime.get("can_trade") is False if strategy_runtime else True,
    ]
    runtime_checks = [
        forward_health.get("can_trade") is False if forward_health else True,
        snapshot_gate.get("can_trade") is False if snapshot_gate else True,
        microstructure_autopilot.get("can_trade") is False if microstructure_autopilot else True,
        not autopilot_failed if microstructure_autopilot else True,
    ]

    components = [
        {
            "id": "oi_funding_quality",
            "status": component_status(
                exists=bool(oi_quality),
                healthy=oi_classification == "oi_guard_data_ready",
                blocked=bool(oi_quality),
            ),
            "decision": oi_classification,
            "key_metric": f"full_context_pct={oi_full_context_pct}",
            "path": portable(paths["oi_funding_quality"]) if paths["oi_funding_quality"] else None,
        },
        {
            "id": "oi_funding_quality_matrix",
            "status": component_status(
                exists=bool(oi_quality_matrix),
                healthy=oi_matrix_decision == "oi_funding_quality_ready_for_research",
                blocked=bool(oi_quality_matrix) and oi_matrix_decision != "oi_funding_quality_ready_for_research",
            ),
            "decision": oi_matrix_decision,
            "key_metric": f"ready={oi_matrix_ready} degraded={oi_matrix_degraded} intervals={oi_matrix_ready_ids}",
            "path": portable(paths["oi_funding_quality_matrix"]) if paths["oi_funding_quality_matrix"] else None,
        },
        {
            "id": "microstructure_snapshot_gate",
            "status": component_status(
                exists=bool(snapshot_gate),
                healthy=snapshot_decision in {"microstructure_snapshot_sealed", "snapshot_already_sealed_for_readiness_epoch"},
                blocked=bool(snapshot_gate),
            ),
            "decision": snapshot_decision,
            "key_metric": f"primary_blocker={get(snapshot_gate, 'readiness_diagnostics', 'primary_blocker')}",
            "path": portable(paths["snapshot_gate"]) if paths["snapshot_gate"] else None,
        },
        {
            "id": "microstructure_autopilot_audit",
            "status": component_status(
                exists=bool(microstructure_autopilot),
                healthy=bool(microstructure_autopilot) and not autopilot_failed and microstructure_autopilot.get("can_trade") is False,
                blocked=bool(microstructure_autopilot) and (bool(autopilot_failed) or microstructure_autopilot.get("can_trade") is not False),
            ),
            "decision": autopilot_decision,
            "key_metric": f"transition={autopilot_transition_state} remaining_hours={autopilot_remaining_hours} failed={autopilot_failed}",
            "path": portable(paths["microstructure_autopilot_audit"]) if paths["microstructure_autopilot_audit"] else None,
        },
        {
            "id": "collector_sla_replay",
            "status": component_status(
                exists=bool(sla_replay),
                healthy=sla_decision == "collector_sla_replay_stable",
                blocked=sla_decision not in {None, "collector_sla_replay_stable", "collector_sla_replay_missing_history"},
            ),
            "decision": sla_decision,
            "key_metric": f"cooldown_remaining_minutes={sla_replay.get('stability_cooldown_remaining_minutes')}",
            "path": portable(paths["collector_sla_replay"]) if paths["collector_sla_replay"] else None,
        },
        {
            "id": "strategy_runtime_inventory",
            "status": component_status(exists=bool(strategy_runtime), healthy=bool(active_observers), blocked=not bool(active_observers) and bool(strategy_runtime)),
            "decision": strategy_runtime.get("decision"),
            "key_metric": f"active={len(active_observers)} rejected={len(rejected_families)} total={len(strategy_families)}",
            "path": portable(paths["strategy_runtime_map"]) if paths["strategy_runtime_map"] else None,
        },
        {
            "id": "strategy_polygon",
            "status": component_status(
                exists=bool(strategy_polygon),
                healthy=polygon_candidates > 0,
                blocked=False,
            ),
            "decision": get(strategy_polygon, "next_action", "id"),
            "key_metric": f"candidates={polygon_candidates} watchlist={polygon_watchlist}",
            "path": portable(paths["strategy_polygon"]) if paths["strategy_polygon"] else None,
        },
        {
            "id": "derivatives_event_edge_miner",
            "status": component_status(
                exists=bool(derivatives_event),
                healthy=str(derivatives_decision or "").startswith("oos_pass"),
                blocked=bool(derivatives_event) and not str(derivatives_decision or "").startswith("oos_pass"),
            ),
            "decision": derivatives_decision,
            "key_metric": f"train={derivatives_train_qualified} validation={derivatives_validation_qualified}",
            "path": portable(paths["derivatives_event_miner"]) if paths["derivatives_event_miner"] else None,
        },
        {
            "id": "derivatives_event_forward_observer",
            "status": component_status(
                exists=bool(derivatives_observer),
                healthy=str(derivatives_observer_decision or "").startswith("observer_"),
                blocked=bool(derivatives_observer) and str(derivatives_observer_decision or "").startswith("blocked_"),
            ),
            "decision": derivatives_observer_decision,
            "key_metric": f"signal={derivatives_observer_signal} events_written={derivatives_observer_events}",
            "path": portable(paths["derivatives_event_observer"]) if paths["derivatives_event_observer"] else None,
        },
        {
            "id": "derivatives_event_pending_watch",
            "status": component_status(
                exists=bool(derivatives_pending_watch),
                healthy=bool(derivatives_pending_watch) and derivatives_pending_watch.get("can_trade") is False and not str(derivatives_pending_watch_decision or "").startswith("blocked_"),
                blocked=bool(derivatives_pending_watch) and str(derivatives_pending_watch_decision or "").startswith("blocked_"),
            ),
            "decision": derivatives_pending_watch_decision,
            "key_metric": f"passed={derivatives_pending_watch_passed}/{derivatives_pending_watch_total} blockers={derivatives_pending_watch_blockers}",
            "path": portable(paths["derivatives_event_pending_watch"]) if paths["derivatives_event_pending_watch"] else None,
        },
        {
            "id": "derivatives_event_forward_scoreboard",
            "status": component_status(
                exists=bool(derivatives_scoreboard),
                healthy=bool(derivatives_scoreboard) and derivatives_scoreboard.get("can_trade") is False,
                blocked=False,
            ),
            "decision": derivatives_scoreboard_decision,
            "key_metric": f"resolved={derivatives_scoreboard_resolved} expectancy_r={derivatives_scoreboard_expectancy}",
            "path": portable(paths["derivatives_event_scoreboard"]) if paths["derivatives_event_scoreboard"] else None,
        },
        {
            "id": "derivatives_event_promotion_gate",
            "status": component_status(
                exists=bool(derivatives_gate),
                healthy=bool(derivatives_gate) and derivatives_gate.get("can_trade") is False,
                blocked=False,
            ),
            "decision": derivatives_gate_decision,
            "key_metric": f"paper_design_review_allowed={derivatives_gate_paper_design}",
            "path": portable(paths["derivatives_event_gate"]) if paths["derivatives_event_gate"] else None,
        },
        {
            "id": "derivatives_event_telegram_notify",
            "status": component_status(
                exists=bool(derivatives_notify),
                healthy=bool(derivatives_notify) and derivatives_notify.get("can_trade") is False and derivatives_notify_decision not in {"telegram_api_error", "telegram_send_error"},
                blocked=derivatives_notify_decision in {"telegram_api_error", "telegram_send_error"},
            ),
            "decision": derivatives_notify_decision,
            "key_metric": f"signal={derivatives_notify_signal} events_written={derivatives_notify_events}",
            "path": portable(paths["derivatives_event_notify"]) if paths["derivatives_event_notify"] else None,
        },
        {
            "id": "derivatives_event_signal_alert_drill",
            "status": component_status(
                exists=bool(derivatives_alert_drill),
                healthy=derivatives_alert_drill_decision == "derivatives_event_signal_alert_drill_passed",
                blocked=bool(derivatives_alert_drill) and derivatives_alert_drill_decision != "derivatives_event_signal_alert_drill_passed",
            ),
            "decision": derivatives_alert_drill_decision,
            "key_metric": f"first={derivatives_alert_drill.get('first_notify_decision')} second={derivatives_alert_drill.get('second_notify_decision')}",
            "path": portable(paths["derivatives_event_alert_drill"]) if paths["derivatives_event_alert_drill"] else None,
        },
        {
            "id": "derivatives_event_research_matrix",
            "status": component_status(
                exists=bool(derivatives_research_matrix),
                healthy=bool(derivatives_research_matrix) and derivatives_research_matrix.get("can_trade") is False,
                blocked=False,
            ),
            "decision": derivatives_matrix_decision,
            "key_metric": f"promotable={derivatives_matrix_promotable} validation_mirages={derivatives_matrix_mirages}",
            "path": portable(paths["derivatives_event_research_matrix"]) if paths["derivatives_event_research_matrix"] else None,
        },
        {
            "id": "context_evidence_matrix",
            "status": component_status(
                exists=bool(context_evidence_matrix),
                healthy=bool(context_evidence_matrix) and context_evidence_matrix.get("can_trade") is False,
                blocked=False,
            ),
            "decision": context_matrix_decision,
            "key_metric": f"ready_for_integration={context_matrix_ready} reports={context_matrix_reports}",
            "path": portable(paths["context_evidence_matrix"]) if paths["context_evidence_matrix"] else None,
        },
        {
            "id": "strategy_research_frontier_matrix",
            "status": component_status(
                exists=bool(strategy_research_frontier_matrix),
                healthy=bool(strategy_research_frontier_matrix) and strategy_research_frontier_matrix.get("can_trade") is False,
                blocked=bool(strategy_research_frontier_matrix) and strategy_research_frontier_matrix.get("can_trade") is not False,
            ),
            "decision": frontier_decision,
            "key_metric": f"promotable={frontier_promotable} observer_only={frontier_observer_only} preregistered={frontier_preregistered} rejected={frontier_rejected}",
            "path": portable(paths["strategy_research_frontier_matrix"]) if paths["strategy_research_frontier_matrix"] else None,
        },
        {
            "id": "forward_scoreboard",
            "status": component_status(
                exists=bool(forward_scoreboard),
                healthy=isinstance(forward_resolved, int) and forward_resolved > 0,
                blocked=False,
            ),
            "decision": get(forward_scoreboard, "summary", "classification", default=forward_scoreboard.get("classification")),
            "key_metric": f"resolved={forward_resolved} expectancy_r={forward_expectancy}",
            "path": portable(paths["forward_scoreboard"]) if paths["forward_scoreboard"] else None,
        },
    ]

    blockers: list[str] = []
    if snapshot_failed:
        blockers.extend(f"microstructure:{item}" for item in snapshot_failed)
    if autopilot_failed:
        blockers.extend(f"microstructure_autopilot:{item}" for item in autopilot_failed)
    if microstructure_autopilot and microstructure_autopilot.get("can_trade") is not False:
        blockers.append("microstructure_autopilot:can_trade_not_false")
    if sla_decision not in {None, "collector_sla_replay_stable", "collector_sla_replay_missing_history"}:
        blockers.append(f"collector_sla:{sla_decision}")
    if not active_observers:
        blockers.append("strategy:no_active_observer")
    if polygon_candidates == 0 and polygon_watchlist == 0:
        blockers.append("strategy:no_polygon_candidate_or_watchlist")
    if derivatives_event and not str(derivatives_decision or "").startswith("oos_pass"):
        blockers.append(f"strategy:derivatives_event_{derivatives_decision}")
    if derivatives_event and str(derivatives_decision or "").startswith("oos_pass") and not derivatives_observer:
        blockers.append("strategy:derivatives_event_observer_not_run")
    if context_evidence_matrix and int(context_matrix_ready or 0) == 0:
        blockers.append("context:no_ready_context_factor")
    if strategy_research_frontier_matrix and int(frontier_promotable or 0) == 0:
        blockers.append("strategy:no_promotable_family_in_frontier")
    if oi_quality_matrix and oi_matrix_decision != "oi_funding_quality_ready_for_research":
        blockers.append(f"data:oi_funding_quality_matrix_{oi_matrix_decision}")

    next_actions: list[dict[str, str]] = []
    if autopilot_failed:
        next_actions.append({
            "id": "repair_microstructure_autopilot_before_waiting",
            "why": "Collector data can be healthy while the watchdog handoff is broken; fix autopilot before relying on sealed-snapshot automation.",
        })
    if snapshot_decision not in {"microstructure_snapshot_sealed", "snapshot_already_sealed_for_readiness_epoch"}:
        next_actions.append({
            "id": "continue_microstructure_collection_until_gate_passes",
            "why": "Microstructure dataset is not sealed yet; research promotion should wait for time window, coverage and SLA stability.",
        })
    if sla_decision not in {None, "collector_sla_replay_stable", "collector_sla_replay_missing_history"}:
        next_actions.append({
            "id": "wait_or_repair_collector_sla_before_snapshot",
            "why": "Recent collector instability blocks trusting a research snapshot even if the latest cycle recovered.",
        })
    if polygon_candidates > 0:
        next_actions.append({
            "id": "isolate_polygon_candidates_for_oos",
            "why": "A polygon candidate exists, but must pass independent OOS/forward validation before promotion.",
        })
    elif polygon_watchlist > 0:
        next_actions.append({
            "id": "expand_watchlist_with_oos_split",
            "why": "Watchlist has signal shape but no robust candidate; run stricter holdout before adding complexity.",
        })
    else:
        next_actions.append({
            "id": "mine_better_event_features_not_more_telegram",
            "why": "More alerts do not create expectancy; the current bottleneck is evidence, data quality and forward outcomes.",
        })
    if derivatives_train_qualified > 0 and derivatives_validation_qualified == 0:
        next_actions.append({
            "id": "add_regime_filter_to_4h_oi_build_continuation_or_archive",
            "why": "Derivatives-event train candidates exist, but validation failed; the next test must be a predeclared regime filter, not looser gates.",
        })
    if str(derivatives_decision or "").startswith("oos_pass"):
        next_actions.append({
            "id": "collect_derivatives_event_forward_observer_outcomes",
            "why": "The derivatives-event candidate reproduced across Source/Active, but OOS has only a small sample; promotion requires fresh observer evidence.",
        })
    if context_evidence_matrix and int(context_matrix_ready or 0) == 0:
        next_actions.append({
            "id": "build_precommitted_composite_context_features",
            "why": "Existing liquidation, spot/perp and spot-led context reports are not ready as filters; the next test must add context as predeclared features and rerun nested holdout.",
        })
    if strategy_research_frontier_matrix and int(frontier_promotable or 0) == 0:
        next_actions.append({
            "id": "open_new_preregistered_mechanism_or_forward_resolve_observers",
            "why": "The research frontier has no promotable family; do not spend cycles retesting rejected families without new data or a new mechanism.",
        })
    if oi_quality_matrix and oi_matrix_decision != "oi_funding_quality_ready_for_research":
        next_actions.append({
            "id": "repair_oi_funding_quality_matrix",
            "why": "Interval-level OI/funding coverage is degraded; do not trust OI/funding filters outside ready intervals.",
        })
    next_actions.append({
        "id": "keep_telegram_as_observability_only",
        "why": "Telegram should notify about accepted cards/incidents; it is not a trading edge and should not consume core iteration time.",
    })

    decision = "research_runtime_observing_no_trade"
    if snapshot_decision in {"microstructure_snapshot_sealed", "snapshot_already_sealed_for_readiness_epoch"} and polygon_candidates > 0:
        decision = "ready_for_oos_validation_not_trading"
    elif snapshot_decision not in {"microstructure_snapshot_sealed", "snapshot_already_sealed_for_readiness_epoch"}:
        decision = "data_readiness_first_not_telegram"

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "runtime_boundary": {
            "can_trade": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "telegram_is_edge": False,
        },
        "scoreboard": {
            "data_checks": bool_count(data_checks),
            "strategy_checks": bool_count(strategy_checks),
            "runtime_checks": bool_count(runtime_checks),
            "active_observer_count": len(active_observers),
            "rejected_family_count": len(rejected_families),
            "polygon_candidate_count": polygon_candidates,
            "polygon_watchlist_count": polygon_watchlist,
            "derivatives_event_train_qualified": derivatives_train_qualified,
            "derivatives_event_validation_qualified": derivatives_validation_qualified,
            "strategy_frontier_promotable": frontier_promotable,
            "strategy_frontier_observer_only": frontier_observer_only,
            "strategy_frontier_preregistered": frontier_preregistered,
            "strategy_frontier_rejected": frontier_rejected,
        },
        "components": components,
        "blockers": blockers,
        "next_actions": next_actions[:5],
        "telegram_assessment": {
            "priority": "low_after_existing_alerting",
            "needed_now": False,
            "reason": "Existing Telegram alerts are enough for observability; next high-value work is data readiness, OOS validation and forward evidence.",
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    score = report["scoreboard"]
    lines = [
        "# TradingOS Core Readiness / Edge State",
        "",
        f"- Generated: `{report['generated_at']}`.",
        f"- Decision: `{report['decision']}`.",
        "- Boundary: `can_trade=false`, `signals_allowed=false`, `orders_allowed=false`.",
        f"- Telegram priority: `{report['telegram_assessment']['priority']}`; needed now: `{report['telegram_assessment']['needed_now']}`.",
        "",
        "## Scoreboard",
        "",
        f"- Data checks: `{score['data_checks']['passed']}/{score['data_checks']['total']}`.",
        f"- Strategy checks: `{score['strategy_checks']['passed']}/{score['strategy_checks']['total']}`.",
        f"- Runtime checks: `{score['runtime_checks']['passed']}/{score['runtime_checks']['total']}`.",
        f"- Active observers: `{score['active_observer_count']}`; rejected families: `{score['rejected_family_count']}`.",
        f"- Polygon candidates/watchlist: `{score['polygon_candidate_count']}` / `{score['polygon_watchlist_count']}`.",
        f"- Derivatives-event train/validation qualified: `{score['derivatives_event_train_qualified']}` / `{score['derivatives_event_validation_qualified']}`.",
        "",
        "## Components",
        "",
        "| component | status | decision | metric | path |",
        "|---|---|---|---|---|",
    ]
    for item in report["components"]:
        lines.append(
            f"| `{item['id']}` | `{item['status']}` | `{item.get('decision')}` | `{item.get('key_metric')}` | `{item.get('path')}` |"
        )
    lines.extend(["", "## Blockers", ""])
    blockers = report.get("blockers") or []
    if blockers:
        lines.extend(f"- `{item}`." for item in blockers)
    else:
        lines.append("- `none`.")
    lines.extend(["", "## Next Actions", ""])
    for item in report["next_actions"]:
        lines.append(f"- `{item['id']}`: {item['why']}")
    lines.extend(["", "## Practical Answer", ""])
    lines.append("- No, the system should not keep iterating on Telegram now. Telegram stays as observability only.")
    lines.append("- The next useful work is data readiness, independent OOS validation and forward outcome accumulation.")
    lines.append("- No live/paper execution promotion is granted by this report.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate TradingOS data/edge readiness without trading or Telegram expansion")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--out-prefix", default="docs/TRADINGOS_CORE_READINESS_EDGE_REPORT_2026-06-25")
    args = parser.parse_args()

    root = resolve_path(args.root)
    report = build_report(root)
    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = root / out_prefix
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "data_checks": report["scoreboard"]["data_checks"],
                "strategy_checks": report["scoreboard"]["strategy_checks"],
                "runtime_checks": report["scoreboard"]["runtime_checks"],
                "blockers": report["blockers"],
                "next_action": report["next_actions"][0]["id"],
                "telegram_needed_now": report["telegram_assessment"]["needed_now"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

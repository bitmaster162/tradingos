#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: str | Path) -> dict[str, Any]:
    resolved = resolve_path(path)
    if not resolved.is_file():
        return {"_missing": portable(resolved)}
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc), "_path": portable(resolved)}
    return payload if isinstance(payload, dict) else {"_read_error": "not_object", "_path": portable(resolved)}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def latest(pattern: str) -> Path | None:
    candidates = list((ROOT / "docs").glob(pattern))
    return max(candidates, key=lambda item: item.stat().st_mtime) if candidates else None


def as_int(value: Any, default: int = 0) -> int:
    try:
        return default if value is None else int(float(value))
    except (TypeError, ValueError):
        return default


def as_float(value: Any) -> float | None:
    try:
        return None if value is None else round(float(value), 6)
    except (TypeError, ValueError):
        return None


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def source_age_hours(payload: dict[str, Any], now: datetime) -> float | None:
    generated = parse_ts(payload.get("generated_at"))
    if generated is None:
        return None
    return round(max(0.0, (now - generated).total_seconds() / 3600.0), 6)


def missing_or_invalid(payload: dict[str, Any]) -> bool:
    return bool(payload.get("_missing") or payload.get("_read_error"))


def gate_value(gates: Any, name: str, key: str, default: Any = None) -> Any:
    if not isinstance(gates, list):
        return default
    for row in gates:
        if isinstance(row, dict) and row.get("name") == name:
            return row.get(key, default)
    return default


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    paths = {
        "bybit_v5r1": resolve_path(args.bybit_canonical_forward),
        "force_order": resolve_path(args.force_order_progress),
        "force_order_continuity": resolve_path(args.force_order_continuity),
        "microstructure": resolve_path(args.microstructure_unblock),
        "deribit": resolve_path(args.deribit_audit),
        "funding": resolve_path(args.funding_readiness),
        "funding_successor_admission": resolve_path(args.funding_successor_admission),
        "spot_perp_flow": resolve_path(args.spot_perp_flow_readiness),
        "spot_perp_flow_snapshot": resolve_path(args.spot_perp_flow_snapshot),
    }
    audit_path = resolve_path(args.devil_audit) if args.devil_audit else latest("FULL_SYSTEM_DEVIL_AUDIT_*.json")
    payloads = {name: read_json(path) for name, path in paths.items()}
    audit = read_json(audit_path) if audit_path else {}

    missing_inputs = [name for name, payload in payloads.items() if missing_or_invalid(payload)]
    stale_threshold_hours = 1.0
    source_ages = {name: source_age_hours(payload, now) for name, payload in payloads.items()}
    stale_inputs = [
        name
        for name, age in source_ages.items()
        if age is None or age > stale_threshold_hours
    ]

    bybit = payloads["bybit_v5r1"]
    bybit_progress = mapping(bybit.get("source_progress"))
    bybit_sample = mapping(bybit.get("sample"))
    bybit_lock = mapping(bybit.get("lock"))
    bybit_decision = str(bybit.get("decision") or "")
    bybit_class = (
        "outcome_blind_forward_collecting"
        if "collecting_outcome_blind_sample" in bybit_decision
        else "blocked_or_unknown"
    )

    force = payloads["force_order"]
    force_sample = mapping(force.get("sample"))
    force_velocity = mapping(force.get("velocity"))
    force_sample_ready = force.get("ready_for_pipeline") is True
    force_continuity = payloads["force_order_continuity"]
    force_transport_ready = (
        force_continuity.get("continuity_observed") is True
        and force_continuity.get("decision") == "force_order_transport_continuity_observed"
    )
    force_ready = force_sample_ready and force_transport_ready
    if force_ready:
        force_class = "sample_and_transport_ready_manual_review"
    elif force_sample_ready:
        force_class = "sample_gate_ready_transport_blocked"
    else:
        force_class = "preregistered_sample_collecting"
    force_gates = force.get("gates")

    micro = payloads["microstructure"]
    micro_coverage = mapping(micro.get("coverage"))
    micro_sla = mapping(micro.get("sla"))
    micro_decision = str(micro.get("decision") or "")
    micro_snapshot = micro.get("snapshot_id")
    micro_cooldown = micro_sla.get("cooldown_until_utc")
    micro_transition = mapping(micro.get("transition"))
    micro_transition_state = str(micro_transition.get("state") or "")
    micro_research_completed = (
        micro_transition_state == "sealed_snapshot_research_batch_already_completed"
        or "sealed_snapshot_research_batch_already_completed"
        in list(micro.get("blockers") or [])
    )
    if micro_research_completed:
        micro_class = "research_batch_already_completed"
    elif micro_snapshot:
        micro_class = "sealed_snapshot_available"
    elif micro_sla.get("decision") == "collector_sla_replay_flapping" and micro_cooldown:
        micro_class = "coverage_ready_cooldown_waiting"
    else:
        micro_class = "readiness_blocked"

    deribit = payloads["deribit"]
    deribit_progress = mapping(deribit.get("forward_progress"))
    deribit_runtime = mapping(deribit.get("runtime"))
    deribit_ready = deribit_progress.get("readiness_gate_ready") is True
    deribit_class = "readiness_gate_open" if deribit_ready else "forward_readiness_collecting"

    funding = payloads["funding"]
    funding_alignment = mapping(funding.get("alignment"))
    funding_freshness = mapping(funding.get("freshness"))
    funding_terminal = funding_alignment.get("terminal") is True
    funding_admission = payloads["funding_successor_admission"]
    funding_admission_window = mapping(funding_admission.get("diagnostic_window"))
    funding_admission_eligible = funding_admission.get("eligible_for_manual_successor_lock_review") is True
    funding_admission_decision = str(funding_admission.get("decision") or "")
    if funding_terminal and funding_admission_eligible:
        funding_class = "successor_admission_ready_manual_review"
    elif funding_terminal:
        funding_class = "successor_admission_waiting_clean_window"
    else:
        funding_class = "readiness_collecting"

    spot_perp_flow = payloads["spot_perp_flow"]
    spot_perp_flow_coverage = mapping(spot_perp_flow.get("coverage"))
    spot_perp_flow_readiness = mapping(spot_perp_flow.get("research_readiness"))
    spot_perp_flow_ready = spot_perp_flow_readiness.get("ready") is True
    spot_perp_flow_decision = str(spot_perp_flow.get("classification") or "")
    spot_perp_flow_snapshot = payloads["spot_perp_flow_snapshot"]
    spot_perp_snapshot_decision = str(spot_perp_flow_snapshot.get("decision") or "")
    spot_perp_snapshot_sealed = (
        spot_perp_flow_snapshot.get("sealed") is True
        and bool(spot_perp_flow_snapshot.get("snapshot_id"))
        and spot_perp_snapshot_decision
        in {
            "spot_perp_flow_snapshot_sealed",
            "spot_perp_flow_snapshot_already_sealed_verified",
        }
    )
    if spot_perp_snapshot_sealed:
        spot_perp_flow_class = "sealed_snapshot_available"
    elif spot_perp_flow_ready:
        spot_perp_flow_class = "data_gate_ready_for_seal_review"
    elif "with_gaps" in spot_perp_flow_decision:
        spot_perp_flow_class = "forward_collecting_with_gaps"
    else:
        spot_perp_flow_class = "forward_data_collecting"

    priorities = (
        {
            "force": 1,
            "bybit": 2,
            "deribit": 3,
            "spot_perp": 4,
            "microstructure": 5,
            "funding": 6,
        }
        if micro_research_completed
        else {
            "microstructure": 1,
            "force": 2,
            "bybit": 3,
            "deribit": 4,
            "funding": 5,
            "spot_perp": 6,
        }
    )
    action_queue = [
        {
            "priority": priorities["microstructure"],
            "edge_class": "cross_venue_microstructure_snapshot",
            "state": micro_class,
            "next_eligible_at_utc": micro_cooldown,
            "next_action": (
                "preserve the terminal exactly-once receipt; do not rerun or retune this snapshot"
                if micro_research_completed
                else (
                    "let the armed exactly-once watchdog seal and run the locked research batch after every gate passes"
                    if not micro_snapshot
                    else "run only the locked post-seal governance chain"
                )
            ),
        },
        {
            "priority": priorities["force"],
            "edge_class": "binance_force_order_feed",
            "state": force_class,
            "next_eligible_at_utc": (
                None
                if force_sample_ready and not force_transport_ready
                else force_velocity.get("theoretical_earliest_pipeline_at")
            ),
            "next_action": (
                "open only the preregistered forceOrder pipeline manual review; no retune or automatic promotion"
                if force_ready
                else (
                    "keep the research gate closed until transport continuity is observed; preserve historical gaps"
                    if force_sample_ready
                    else "keep collector and OHLCV cache fixed until the final matured-block gate passes"
                )
            ),
        },
        {
            "priority": priorities["bybit"],
            "edge_class": "bybit_liquidation_canonical_reversal_v5r1",
            "state": bybit_class,
            "next_eligible_at_utc": None,
            "next_action": "keep V5R1 outcome-blind; do not compute or inspect interim returns",
        },
        {
            "priority": priorities["deribit"],
            "edge_class": "deribit_options_skew_forward",
            "state": deribit_class,
            "next_eligible_at_utc": None,
            "next_action": "keep collecting until the immutable 7d/1800-slot readiness gate opens",
        },
        {
            "priority": priorities["funding"],
            "edge_class": "cex_funding_alignment",
            "state": funding_class,
            "next_eligible_at_utc": funding_admission_window.get("earliest_recheck_at_utc"),
            "next_action": (
                str(funding_admission.get("next_action") or "manual parameter-identical successor review only")
                if funding_terminal
                else "continue readiness collection without edge evaluation"
            ),
        },
        {
            "priority": priorities["spot_perp"],
            "edge_class": "binance_spot_perp_aggressor_flow_lead_lag",
            "state": spot_perp_flow_class,
            "next_eligible_at_utc": None,
            "next_action": (
                "manually review a separate prospective preregistration; the sealed snapshot does not authorize research"
                if spot_perp_snapshot_sealed
                else (
                    "let the exactly-once guard freeze and seal the completed forward snapshot"
                    if spot_perp_flow_ready
                    else "keep the data-only collector running; do not inspect strategy outcomes or search parameters"
                )
            ),
        },
    ]
    action_queue.sort(key=lambda item: int(item["priority"]))

    if missing_inputs:
        decision = "live_data_focus_inputs_missing_fail_closed"
        status_note = "canonical_input_missing"
        blockers = [f"missing_or_invalid:{name}" for name in missing_inputs]
        next_action = "restore current canonical reports before selecting another edge action"
    elif stale_inputs:
        decision = "live_data_focus_inputs_stale_fail_closed"
        status_note = "canonical_input_stale"
        blockers = [f"stale:{name}" for name in stale_inputs]
        next_action = "refresh the existing observer stack; do not infer state from stale reports"
    elif micro_snapshot and not micro_research_completed:
        decision = "live_data_focus_microstructure_snapshot_ready"
        status_note = "sealed_snapshot_available"
        blockers = []
        next_action = "run the locked post-seal microstructure governance chain exactly once"
    elif force_ready:
        decision = "live_data_focus_force_order_pipeline_ready_manual_gate"
        status_note = "force_order_fixed_sample_gate_passed"
        blockers = []
        next_action = "open only the preregistered forceOrder pipeline review; no automatic promotion"
    elif force_sample_ready and not force_transport_ready:
        decision = "live_data_focus_force_order_transport_blocked"
        status_note = "force_order_sample_ready_transport_not_continuous"
        blockers = list(force_continuity.get("blockers") or ["transport_continuity_not_observed"])
        next_action = (
            "keep the exactly-once research gate closed and collect a clean continuity window; "
            "do not delete prior transport evidence"
        )
    elif micro_class == "coverage_ready_cooldown_waiting":
        decision = "live_data_focus_microstructure_cooldown_waiting"
        status_note = "coverage_passed_autopilot_armed"
        blockers = list(micro.get("blockers") or [])
        next_action = f"keep the watchdog unchanged until cooldown expiry {micro_cooldown}; it is already armed exactly once"
    else:
        decision = "live_data_focus_forward_samples_collecting"
        status_note = "canonical_sources_current"
        blockers = []
        next_action = "keep current collectors and immutable observers running; do not retune"

    audit_counts = mapping(audit.get("open_severity_counts"))
    return {
        "generated_at": now_iso(),
        "tool": "tools/live_data_edge_focus_summary.py",
        "schema_version": 2,
        "decision": decision,
        "status_note": status_note,
        "blockers": blockers,
        "can_trade": False,
        "orders_allowed": False,
        "live_classes": {
            "bybit_liquidation_v5r1": {
                "status": bybit_class,
                "decision": bybit.get("decision"),
                "forward_floor_at": bybit_lock.get("forward_start_at"),
                "post_floor_raw_events": as_int(bybit_progress.get("post_floor_raw_events")),
                "post_floor_schema_valid_events": as_int(bybit_progress.get("post_floor_schema_valid_events")),
                "post_floor_packets": as_int(bybit_progress.get("post_floor_packets")),
                "eligible_event_bars": as_int(bybit_progress.get("eligible_event_bars")),
                "resolved_events": as_int(bybit_sample.get("resolved_events")),
                "utc_days": as_int(bybit_sample.get("utc_days")),
                "outcomes_hidden": mapping(bybit.get("outcome_review")).get("interim_outcomes_hidden") is not False,
                "blockers": bybit.get("blockers") or [],
            },
            "binance_force_order": {
                "status": force_class,
                "decision": force.get("decision"),
                "events": as_int(force_sample.get("events")),
                "event_bars": as_int(force_sample.get("event_bars")),
                "symbols": len(force_sample.get("symbols_with_events") or []),
                "independent_4h_blocks": as_int(force_sample.get("independent_4h_blocks")),
                "matured_independent_4h_blocks": as_int(force_sample.get("matured_independent_4h_blocks")),
                "required_matured_independent_4h_blocks": as_int(
                    gate_value(force_gates, "minimum_matured_independent_4h_blocks", "required")
                ),
                "sample_gate_ready": force_sample_ready,
                "transport_continuity_observed": force_transport_ready,
                "transport_decision": force_continuity.get("decision"),
                "transport_blockers": force_continuity.get("blockers") or [],
                "ready_for_pipeline": force_ready,
                "earliest_pipeline_at_utc": force_velocity.get("theoretical_earliest_pipeline_at"),
                "blockers": force.get("blockers") or [],
            },
            "microstructure": {
                "status": micro_class,
                "decision": micro_decision,
                "snapshot_id": micro_snapshot,
                "transition_state": micro_transition_state or None,
                "research_batch_already_completed": micro_research_completed,
                "span_hours": as_float(micro_coverage.get("span_hours")),
                "trade_coverage_pct": as_float(micro_coverage.get("trade_coverage_pct")),
                "book_coverage_pct": as_float(micro_coverage.get("book_coverage_pct")),
                "cooldown_until_utc": micro_cooldown,
                "cooldown_remaining_minutes": as_float(micro_sla.get("cooldown_remaining_minutes")),
                "open_incident": micro_sla.get("open_incident") is True,
                "blockers": micro.get("blockers") or [],
            },
            "deribit_options": {
                "status": deribit_class,
                "decision": deribit.get("decision"),
                "span_days": as_float(deribit_progress.get("span_days")),
                "minimum_span_days": as_float(deribit_progress.get("minimum_span_days")),
                "healthy_slots": as_int(deribit_progress.get("healthy_slots")),
                "minimum_healthy_slots": as_int(deribit_progress.get("minimum_healthy_slots")),
                "scheduled_coverage": as_float(deribit_progress.get("scheduled_coverage")),
                "runtime_components_passed": deribit_runtime.get("all_components_passed") is True,
                "readiness_gate_ready": deribit_ready,
            },
            "cex_funding": {
                "status": funding_class,
                "decision": funding.get("decision"),
                "alignment_decision": funding_alignment.get("decision"),
                "alignment_terminal": funding_terminal,
                "freshness_healthy": funding_freshness.get("healthy") is True,
                "blockers": funding_alignment.get("blockers") or [],
                "successor_admission_decision": funding_admission_decision,
                "successor_admission_eligible": funding_admission_eligible,
                "successor_earliest_recheck_at_utc": funding_admission_window.get("earliest_recheck_at_utc"),
                "successor_created": mapping(funding_admission.get("runtime_boundary")).get("successor_created") is True,
            },
            "binance_spot_perp_aggressor_flow": {
                "status": spot_perp_flow_class,
                "classification": spot_perp_flow_decision,
                "span_hours": as_float(spot_perp_flow_coverage.get("span_hours")),
                "dual_market_coverage_pct": as_float(
                    spot_perp_flow_coverage.get("dual_market_coverage_pct")
                ),
                "spot_missing_ids": as_int(
                    mapping(mapping(spot_perp_flow.get("integrity")).get("spot")).get("missing_ids")
                ),
                "perpetual_missing_ids": as_int(
                    mapping(mapping(spot_perp_flow.get("integrity")).get("perpetual")).get("missing_ids")
                ),
                "research_data_gate_ready": spot_perp_flow_ready,
                "snapshot_guard_decision": spot_perp_snapshot_decision,
                "snapshot_sealed": spot_perp_snapshot_sealed,
                "snapshot_id": spot_perp_flow_snapshot.get("snapshot_id"),
                "hypothesis_registered": mapping(spot_perp_flow.get("runtime_boundary")).get(
                    "hypothesis_registered"
                ) is True,
                "blockers": spot_perp_flow_readiness.get("blockers") or [],
            },
        },
        "action_queue": action_queue,
        "source_freshness": {
            "threshold_hours": stale_threshold_hours,
            "ages_hours": source_ages,
            "stale_inputs": stale_inputs,
            "missing_inputs": missing_inputs,
        },
        "audit": {
            "path": portable(audit_path) if audit_path else None,
            "decision": audit.get("decision"),
            "open": audit_counts,
            "parity": mapping(audit.get("source_runtime_parity")).get("passed"),
        },
        "source_reports": {
            **{name: portable(path) for name, path in paths.items()},
            "devil_audit": portable(audit_path) if audit_path else None,
        },
        "legacy_inputs": {
            "liquidation_refresh_ignored": bool(getattr(args, "liquidation_refresh", "")),
            "edge_sweep_ignored": bool(getattr(args, "edge_sweep", "")),
            "reason": "schema-v2 focus uses current canonical observers instead of legacy aggregate refresh files",
        },
        "next_action": next_action,
        "boundary": {
            "research_summary_only": True,
            "reads_interim_returns": False,
            "runs_research": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "can_trade": False,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    classes = report["live_classes"]
    bybit = classes["bybit_liquidation_v5r1"]
    force = classes["binance_force_order"]
    micro = classes["microstructure"]
    deribit = classes["deribit_options"]
    funding = classes["cex_funding"]
    spot_perp_flow = classes["binance_spot_perp_aggressor_flow"]
    lines = [
        "# Live Data Edge Focus Summary",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Status note: `{report['status_note']}`",
        "- Can trade: `false`",
        f"- Next action: {report['next_action']}",
        "",
        "## Current Canonical Classes",
        "",
        f"- Bybit V5R1: `{bybit['status']}`, post-floor rows `{bybit['post_floor_raw_events']}`, valid `{bybit['post_floor_schema_valid_events']}`, eligible bars `{bybit['eligible_event_bars']}`, outcomes hidden `{bybit['outcomes_hidden']}`.",
        f"- Binance forceOrder: `{force['status']}`, events `{force['events']}`, bars `{force['event_bars']}`, matured blocks `{force['matured_independent_4h_blocks']}/{force['required_matured_independent_4h_blocks']}`, transport `{force['transport_decision']}`, earliest `{force['earliest_pipeline_at_utc']}`.",
        f"- Microstructure: `{micro['status']}`, trade/book coverage `{micro['trade_coverage_pct']}/{micro['book_coverage_pct']}`, cooldown until `{micro['cooldown_until_utc']}`.",
        f"- Deribit options: `{deribit['status']}`, span `{deribit['span_days']}/{deribit['minimum_span_days']}` days, slots `{deribit['healthy_slots']}/{deribit['minimum_healthy_slots']}`.",
        f"- CEX funding: `{funding['status']}`, freshness healthy `{funding['freshness_healthy']}`, alignment `{funding['alignment_decision']}`, admission `{funding['successor_admission_decision']}`, recheck `{funding['successor_earliest_recheck_at_utc']}`.",
        f"- Binance Spot/Perp aggressor flow: `{spot_perp_flow['status']}`, span `{spot_perp_flow['span_hours']}h`, coverage `{spot_perp_flow['dual_market_coverage_pct']}%`, gaps `{spot_perp_flow['spot_missing_ids']}/{spot_perp_flow['perpetual_missing_ids']}`, snapshot `{spot_perp_flow['snapshot_guard_decision']}` / `{spot_perp_flow['snapshot_id']}`, hypothesis registered `{spot_perp_flow['hypothesis_registered']}`.",
        "",
        "## Action Queue",
        "",
        "| Priority | Edge class | State | Earliest UTC | Next action |",
        "|---:|---|---|---|---|",
    ]
    for item in report["action_queue"]:
        lines.append(
            f"| `{item['priority']}` | `{item['edge_class']}` | `{item['state']}` | "
            f"`{item.get('next_eligible_at_utc')}` | {item['next_action']} |"
        )
    lines.extend(
        [
            "",
            "## Source Freshness",
            "",
            f"- Threshold: `{report['source_freshness']['threshold_hours']}` hours.",
            f"- Stale: `{report['source_freshness']['stale_inputs']}`.",
            f"- Missing: `{report['source_freshness']['missing_inputs']}`.",
            "",
            "## Boundary",
            "",
            "- Current-source aggregation only.",
            "- Does not compute or inspect interim returns.",
            "- Does not run research, emit signals, open paper entries or send orders.",
            "- `can_trade=false`.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Rank current canonical live-data edge gates without opening outcomes.")
    parser.add_argument(
        "--bybit-canonical-forward",
        default="docs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_OBSERVER_V5R2_2026-07-18.json",
    )
    parser.add_argument(
        "--force-order-progress",
        default="docs/LIQUIDATION_FORCE_ORDER_PREREGISTERED_PROGRESS_2026-07-12.json",
    )
    parser.add_argument(
        "--force-order-continuity",
        default="docs/LIQUIDATION_FORCE_ORDER_TRANSPORT_CONTINUITY_2026-07-15.json",
    )
    parser.add_argument(
        "--microstructure-unblock",
        default="docs/MICROSTRUCTURE_UNBLOCK_STATUS_2026-07-03.json",
    )
    parser.add_argument(
        "--deribit-audit",
        default="docs/DERIBIT_OPTIONS_V3_DATA_LAYER_AUDIT_2026-07-16.json",
    )
    parser.add_argument(
        "--funding-readiness",
        default="docs/CEX_FUNDING_RESEARCH_READINESS_2026-07-13.json",
    )
    parser.add_argument(
        "--funding-successor-admission",
        default="docs/CEX_FUNDING_SUCCESSOR_ADMISSION_2026-07-16.json",
    )
    parser.add_argument(
        "--spot-perp-flow-readiness",
        default="docs/BINANCE_SPOT_PERP_AGGRESSOR_FLOW_DATA_QUALITY_2026-07-15.json",
    )
    parser.add_argument(
        "--spot-perp-flow-snapshot",
        default="docs/BINANCE_SPOT_PERP_AGGRESSOR_FLOW_SNAPSHOT_GUARD_2026-07-15.json",
    )
    parser.add_argument("--devil-audit", default="")
    parser.add_argument("--liquidation-refresh", default="", help=argparse.SUPPRESS)
    parser.add_argument("--edge-sweep", default="", help=argparse.SUPPRESS)
    parser.add_argument("--out-prefix", default="docs/LIVE_DATA_EDGE_FOCUS_SUMMARY_2026-07-03")
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "status_note": report["status_note"],
                "bybit_events": report["live_classes"]["bybit_liquidation_v5r1"]["post_floor_raw_events"],
                "bybit_forward_bars": report["live_classes"]["bybit_liquidation_v5r1"]["eligible_event_bars"],
                "force_order_events": report["live_classes"]["binance_force_order"]["events"],
                "microstructure": report["live_classes"]["microstructure"]["status"],
                "spot_perp_flow": report["live_classes"]["binance_spot_perp_aggressor_flow"]["status"],
                "out": portable(out_prefix.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not report["source_freshness"]["missing_inputs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

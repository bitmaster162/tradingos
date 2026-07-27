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


FAMILY_PATTERNS: dict[str, tuple[str, ...]] = {
    "derivatives_event": ("DERIVATIVES_EVENT_EDGE_MINER", "DERIVATIVES_EVENT_FUNDING_FOCUS"),
    "derivatives_context": ("DERIVATIVES_CONTEXT_COMPOSITE_MINER", "CONTEXT_EVIDENCE_MATRIX"),
    "basis_funding_carry": ("BASIS_FUNDING_CARRY_NESTED_HOLDOUT",),
    "basis_dispersion_reversion": ("BASIS_DISPERSION_REVERSION_MULTI_SYMBOL_NESTED_HOLDOUT",),
    "basis_shock_reversion": ("BASIS_SHOCK_REVERSION_NESTED_HOLDOUT",),
    "basis_shock_funding_alignment": ("BASIS_SHOCK_FUNDING_ALIGNMENT_MULTI_SYMBOL_NESTED_HOLDOUT",),
    "funding_settlement_reversion": ("FUNDING_SETTLEMENT_REVERSION_NESTED_HOLDOUT",),
    "cross_venue_catchup": ("CROSS_VENUE_CATCHUP_NESTED_HOLDOUT",),
    "cross_venue_negative_rebound": ("CROSS_VENUE_NEGATIVE_REBOUND_TRAIN",),
    "spot_led_continuation": ("SPOT_LED_CONTINUATION_NESTED_HOLDOUT",),
    "oi_funding_reset_reversal": ("OI_FUNDING_RESET_REVERSAL_RESEARCH",),
    "derivatives_squeeze_disagreement": (
        "DERIVATIVES_SQUEEZE_DISAGREEMENT_FORWARD_OBSERVER",
        "DERIVATIVES_SQUEEZE_DISAGREEMENT_RESEARCH",
    ),
    "alt_breadth_dislocation": (
        "ALT_BREADTH_DISLOCATION_FORWARD_OBSERVER",
        "ALT_BREADTH_DISLOCATION_RESEARCH",
    ),
    "liquidation_impulse": ("LIQUIDATION_IMPULSE_CONTINUATION_NESTED_HOLDOUT", "LIQUIDATION_IMPULSE_REVERSAL_NESTED_HOLDOUT"),
    "bybit_liquidation_directional_v1": ("BYBIT_LIQUIDATION_FORWARD_SEMANTIC_TOMBSTONE",),
    "bybit_liquidation_canonical_reversal_v2": (
        "BYBIT_LIQUIDATION_CANONICAL_FORWARD_V2_DESIGN_TOMBSTONE",
    ),
    "bybit_liquidation_canonical_reversal_v3": (
        "BYBIT_LIQUIDATION_CANONICAL_V3_CLOCK_TOMBSTONE",
    ),
    "bybit_liquidation_canonical_reversal_v4": (
        "BYBIT_LIQUIDATION_CANONICAL_V4_PACKET_IDENTITY_TOMBSTONE",
    ),
    "bybit_liquidation_canonical_reversal_v5": (
        "BYBIT_LIQUIDATION_CANONICAL_V5_SOURCE_COMPAT_TOMBSTONE",
    ),
    "bybit_liquidation_canonical_reversal_v5r2": (
        "BYBIT_LIQUIDATION_CANONICAL_FORWARD_OBSERVER_V5R2",
    ),
    "post_liquidation_absorption_spot_perp": (
        "POST_LIQUIDATION_ABSORPTION_SEMANTIC_TOMBSTONE",
        "POST_LIQUIDATION_ABSORPTION_SPOT_PERP",
    ),
    "liquidation_timing_vol_continuation": (
        "LIQUIDATION_TIMING_VOL_SEMANTIC_TOMBSTONE",
        "LIQUIDATION_TIMING_VOL_CONTINUATION",
        "LIQUIDATION_TIMING_VOL_FORWARD_OBSERVER",
    ),
    "liquidation_book_replenishment": (
        "LIQUIDATION_BOOK_REPLENISHMENT_INDEPENDENCE_GATE",
        "LIQUIDATION_BOOK_REPLENISHMENT_FORWARD_OBSERVER",
    ),
    "force_order_liquidation_context": (
        "LIQUIDATION_FORCE_ORDER_PREREGISTERED_PROGRESS",
        "LIQUIDATION_FORCE_ORDER_DATA_QUALITY",
        "FORCE_ORDER_LIQUIDATION_CONTEXT_INTAKE",
        "FORCE_ORDER_LIQUIDATION_EVENT_STUDY",
        "FORCE_ORDER_LIQUIDATION_RESEARCH_PIPELINE",
    ),
    "range_edge": ("RANGE_EDGE_NESTED_HOLDOUT",),
    "trend_mix": ("TREND_MIX_NESTED_HOLDOUT",),
    "short_continuation": ("SHORT_CONTINUATION_NESTED_HOLDOUT",),
    "session_opening_range": ("SESSION_OPENING_RANGE_NESTED_HOLDOUT",),
    "session_volatility_compression_breakout": ("SESSION_VOLATILITY_COMPRESSION_BREAKOUT_NESTED_HOLDOUT",),
    "calendar_session_drift": ("CALENDAR_SESSION_DRIFT_NESTED_HOLDOUT", "CALENDAR_SESSION_DRIFT_RESEARCH_PASS"),
    "relative_strength_rotation": ("RELATIVE_STRENGTH_ROTATION_NESTED_HOLDOUT",),
    "volatility_regime_transition": ("VOLATILITY_REGIME_TRANSITION_NESTED_HOLDOUT",),
    "strategy_mix_combo": ("STRATEGY_MIX_COMBO_TESTER",),
    "strategy_polygon_parallel": ("STRATEGY_POLYGON_PARALLEL",),
    "guard_matrix_overlay": ("TRADE_LEDGER_GUARD_MATRIX",),
    "range_refined": ("RANGE_REFINED_",),
    "crowd_fade": ("CROWD_FADE_POSITIONING_PROMOTION_GATE",),
    "microstructure_prereg_queue": ("CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE_AUDIT",),
    "composite_liquidity_derivatives": ("COMPOSITE_LIQUIDITY_DERIVATIVES_NESTED_HOLDOUT",),
    "cross_asset_residual_reversion": ("CROSS_ASSET_COINTEGRATION_RESIDUAL_NESTED_HOLDOUT",),
    "deribit_options_skew_forward": ("DERIBIT_OPTIONS_RESEARCH_RUNTIME_AUDIT",),
    "exogenous_liquidity_regime": ("EXOGENOUS_LIQUIDITY_REGIME_FORWARD_OBSERVER",),
    "cross_venue_large_trade_tail": ("LARGE_TRADE_TAIL_TERMINAL_REVIEW",),
    "cross_venue_liquidation_receipt_leadership": (
        "LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_FORWARD_OBSERVER",
    ),
}

FAMILY_PATTERNS["post_liquidation_absorption_spot_perp"] = (
    "POST_LIQUIDATION_ABSORPTION_SEMANTIC_TOMBSTONE",
    "POST_LIQUIDATION_ABSORPTION_FORWARD_OBSERVER_RUNNER",
    "POST_LIQUIDATION_ABSORPTION_SPOT_PERP",
)

PREFERRED_REPORT_PATTERNS: dict[str, tuple[str, ...]] = {
    # Outcome-blind preregistered progress is the current runtime truth once
    # present. Data quality remains the fallback through FAMILY_PATTERNS, while
    # a newer child research report still cannot hide observer state.
    "force_order_liquidation_context": (
        "LIQUIDATION_FORCE_ORDER_PREREGISTERED_PROGRESS",
        "LIQUIDATION_FORCE_ORDER_DATA_QUALITY",
    ),
    # The external independence gate is stricter than the base observer and
    # must remain the runtime truth once present.
    "liquidation_book_replenishment": ("LIQUIDATION_BOOK_REPLENISHMENT_INDEPENDENCE_GATE",),
    "bybit_liquidation_directional_v1": ("BYBIT_LIQUIDATION_FORWARD_SEMANTIC_TOMBSTONE",),
    "bybit_liquidation_canonical_reversal_v2": (
        "BYBIT_LIQUIDATION_CANONICAL_FORWARD_V2_DESIGN_TOMBSTONE",
    ),
    "bybit_liquidation_canonical_reversal_v3": (
        "BYBIT_LIQUIDATION_CANONICAL_V3_CLOCK_TOMBSTONE",
    ),
    "bybit_liquidation_canonical_reversal_v4": (
        "BYBIT_LIQUIDATION_CANONICAL_V4_PACKET_IDENTITY_TOMBSTONE",
    ),
    "bybit_liquidation_canonical_reversal_v5": (
        "BYBIT_LIQUIDATION_CANONICAL_V5_SOURCE_COMPAT_TOMBSTONE",
    ),
    "bybit_liquidation_canonical_reversal_v5r2": (
        "BYBIT_LIQUIDATION_CANONICAL_FORWARD_OBSERVER_V5R2",
    ),
    "post_liquidation_absorption_spot_perp": ("POST_LIQUIDATION_ABSORPTION_SEMANTIC_TOMBSTONE",),
    "liquidation_timing_vol_continuation": ("LIQUIDATION_TIMING_VOL_SEMANTIC_TOMBSTONE",),
    "cross_venue_liquidation_receipt_leadership": (
        "LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_FORWARD_OBSERVER",
    ),
}

EXCLUDED_REPORT_PREFIXES = (
    "STRATEGY_DISCOVERY_",
    "STRATEGY_HYPOTHESIS_",
    "TARGETED_STRATEGY_RULE_EXTRACTOR",
    "DOCUMENT_RULE_CARD_BATCH_TEST",
    "DOCUMENT_RULE_CANDIDATE_DIAGNOSTICS",
    "DOCUMENT_RULE_FILTER_PROBE",
    "DOCUMENT_RULE_FORWARD_",
    "DOCUMENT_RULE_PREREG_",
    "DOWNLOADS_CANDIDATE_SCAN",
)

NON_RUNTIME_RESEARCH_FAMILIES = {
    # This report searches overlays across unrelated historical ledgers. It is
    # not one frozen strategy and cannot have a truthful shared observer.
    "guard_matrix_overlay",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def report_age_hours(payload: dict[str, Any], as_of: datetime) -> float | None:
    generated_at = parse_ts(payload.get("generated_at"))
    if generated_at is None:
        return None
    return max(0.0, (as_of - generated_at).total_seconds() / 3600.0)


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"_read_error": str(path)}
    return payload if isinstance(payload, dict) else {"_read_error": str(path)}


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def latest_family_reports(docs_dir: Path) -> dict[str, Path]:
    latest: dict[str, Path] = {}
    for path in docs_dir.glob("*.json"):
        name = path.name.upper()
        if name.startswith(EXCLUDED_REPORT_PREFIXES):
            continue
        for family, patterns in FAMILY_PATTERNS.items():
            if any(pattern in name for pattern in patterns):
                current = latest.get(family)
                if current is None or path.stat().st_mtime > current.stat().st_mtime:
                    latest[family] = path
                break
    for family, patterns in PREFERRED_REPORT_PATTERNS.items():
        for pattern in patterns:
            preferred = [path for path in docs_dir.glob("*.json") if pattern in path.name.upper()]
            if preferred:
                latest[family] = max(preferred, key=lambda item: item.stat().st_mtime)
                break
    return latest


def nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def classify_family(payload: dict[str, Any]) -> str:
    decision = str(payload.get("decision") or payload.get("classification") or "")
    if payload.get("can_trade") is not False and payload:
        return "unsafe_boundary"
    if decision == "microstructure_prereg_queue_valid":
        return "preregistered_waiting_snapshot"
    if decision == "guard_candidates_need_forward_observer":
        return "candidate_needs_observer_runtime"
    if (
        "collector_alive_no_events" in decision
        or decision.startswith("liquidation_force_order_collecting_")
        or decision.startswith("force_order_preregistered_progress_")
        or decision.startswith("bybit_liquidation_canonical_v5_collecting_outcome_blind_sample")
    ):
        return "observer_only_waiting_forward"
    if decision.startswith("liquidation_book_replenishment_independence_gate_collecting_"):
        return "observer_only_waiting_forward"
    if decision.startswith("liquidation_cross_venue_receipt_leadership_waiting_") or decision.startswith(
        "liquidation_cross_venue_receipt_leadership_collecting_"
    ):
        return "observer_only_waiting_forward"
    if decision.startswith("liquidation_cross_venue_paired_leadership_waiting_") or decision.startswith(
        "liquidation_cross_venue_paired_leadership_collecting_"
    ):
        return "observer_only_waiting_forward"
    if decision.endswith("candidate_for_manual_price_impact_preregistration"):
        return "candidate_needs_forward_proof"
    if decision.startswith("oos_pass") or "ready_for_paper" in decision or "candidate_accepted" in decision:
        return "candidate_needs_forward_proof"
    if (
        "watchlist" in decision
        or "observer" in decision
        or "waiting" in decision
        or "shadow" in decision
        or "forward_collecting" in decision
    ):
        return "observer_only_waiting_forward"
    if (
        decision.startswith("reject")
        or "failed" in decision
        or "insufficient" in decision
        or "no_train" in decision
        or "no_promotion" in decision
        or "train_only" in decision
        or "tombstone" in decision
    ):
        return "rejected_research_only"
    if not decision:
        return "unknown_needs_audit"
    return "research_only"


def extract_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    search = payload.get("search") if isinstance(payload.get("search"), dict) else {}
    validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
    oos = payload.get("oos") if isinstance(payload.get("oos"), dict) else {}
    return {
        "tested": payload.get("tested") or summary.get("tested") or search.get("tested") or search.get("configs_tested"),
        "train_qualified": payload.get("train_qualified") or summary.get("train_qualified") or search.get("train_qualified"),
        "validation_qualified": payload.get("validation_qualified") or summary.get("validation_qualified") or search.get("validation_qualified"),
        "oos_qualified": payload.get("oos_qualified") or summary.get("oos_qualified") or search.get("oos_qualified"),
        "resolved": summary.get("resolved") or payload.get("resolved"),
        "expectancy_r": summary.get("expectancy_r") or payload.get("expectancy_r") or oos.get("expectancy_r"),
        "validation_trades": validation.get("trades"),
        "oos_trades": oos.get("trades"),
        "stable_folds": oos.get("stable_folds") or validation.get("stable_folds"),
    }


def build_report(
    docs_dir: Path,
    *,
    as_of: datetime | None = None,
    observer_max_age_hours: float = 26.0,
) -> dict[str, Any]:
    observed_at = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    latest = latest_family_reports(docs_dir)
    families: list[dict[str, Any]] = []
    for family in sorted(FAMILY_PATTERNS):
        path = latest.get(family)
        payload = read_json(path) if path else {}
        status = "missing" if not path else classify_family(payload)
        if path and family in NON_RUNTIME_RESEARCH_FAMILIES:
            status = "research_tool_not_independent_strategy"
        age_hours = report_age_hours(payload, observed_at) if path else None
        report_fresh = age_hours is not None and age_hours <= observer_max_age_hours
        if status == "observer_only_waiting_forward" and not report_fresh:
            status = "observer_only_stale_not_running"
        families.append({
            "family": family,
            "status": status,
            "decision": payload.get("decision") or payload.get("classification"),
            "path": portable(path) if path else None,
            "can_trade": payload.get("can_trade", False),
            "report_generated_at": payload.get("generated_at"),
            "report_age_hours": round(age_hours, 3) if age_hours is not None else None,
            "report_fresh": report_fresh,
            "runtime_role": (
                "offline_candidate_discovery_only"
                if family in NON_RUNTIME_RESEARCH_FAMILIES
                else "strategy_or_observer_family"
            ),
            "metrics": extract_metrics(payload),
        })

    counts: dict[str, int] = {}
    for item in families:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    promotable = [item for item in families if item["status"] == "candidate_needs_forward_proof"]
    observer_only = [item for item in families if item["status"] == "observer_only_waiting_forward"]
    stale_observers = [item for item in families if item["status"] == "observer_only_stale_not_running"]
    runtime_gaps = [item for item in families if item["status"] == "candidate_needs_observer_runtime"]
    research_tools = [item for item in families if item["status"] == "research_tool_not_independent_strategy"]
    preregistered = [item for item in families if item["status"] == "preregistered_waiting_snapshot"]
    rejected = [item for item in families if item["status"] == "rejected_research_only"]
    unsafe = [item for item in families if item["status"] == "unsafe_boundary"]

    decision = "no_promotable_strategy_family"
    next_action = "stop_retesting_rejected_families; continue microstructure data collection and preregister a new mechanism before coding"
    if unsafe:
        decision = "unsafe_boundary_detected"
        next_action = "fix can_trade/live boundary before any further research"
    elif promotable:
        decision = "candidate_family_needs_forward_proof"
        next_action = "route promotable family into observer-only forward proof, not live trading"
    elif stale_observers or runtime_gaps:
        decision = "observer_runtime_truth_gap_detected"
        next_action = "do not count stale or missing observers as forward collection; explicitly restore, pause or tombstone each runtime before new research"
    elif observer_only:
        decision = "observer_families_waiting_forward_outcomes"
        next_action = "collect resolved forward outcomes for observer-only families before promotion"
    elif preregistered:
        decision = "preregistered_families_waiting_snapshot"
        next_action = "continue microstructure collection until sealed snapshot, then run locked research runner"

    return {
        "generated_at": now_iso(),
        "boundary": {
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "network_required": False,
        },
        "summary": {
            "families": len(families),
            "status_counts": counts,
            "promotable": len(promotable),
            "observer_only": len(observer_only),
            "stale_observers": len(stale_observers),
            "candidate_needs_observer_runtime": len(runtime_gaps),
            "research_tools_not_runtime": len(research_tools),
            "preregistered": len(preregistered),
            "rejected": len(rejected),
            "unsafe": len(unsafe),
        },
        "families": families,
        "decision": decision,
        "next_action": next_action,
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Strategy Research Frontier Matrix",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Local report aggregation only.",
        "- No network, no private credentials, no orders.",
        "",
        "## Summary",
        "",
        f"- Decision: `{report.get('decision')}`.",
        f"- Next action: `{report.get('next_action')}`.",
        f"- Status counts: `{report.get('summary', {}).get('status_counts')}`.",
        "",
        "## Families",
        "",
        "| family | status | age hours | fresh | decision | key metrics | path |",
        "|---|---|---:|---|---|---|---|",
    ]
    for item in report.get("families", []):
        metrics = item.get("metrics") or {}
        metric_text = ", ".join(
            f"{key}={value}" for key, value in metrics.items()
            if value not in {None, ""}
        )
        lines.append(
            f"| {item.get('family')} | {item.get('status')} | {item.get('report_age_hours')} | "
            f"{item.get('report_fresh')} | {item.get('decision')} | {metric_text} | `{item.get('path')}` |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate strategy-family research state from docs reports")
    parser.add_argument("--docs-dir", default="docs")
    parser.add_argument("--observer-max-age-hours", type=float, default=26.0)
    parser.add_argument("--out-prefix", default="docs/STRATEGY_RESEARCH_FRONTIER_MATRIX_2026-06-29")
    args = parser.parse_args()

    report = build_report(resolve_path(args.docs_dir), observer_max_age_hours=args.observer_max_age_hours)
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "md": str(md_path), "summary": report["summary"], "decision": report["decision"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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


def portable(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def latest(pattern: str) -> Path | None:
    candidates = [item for item in (ROOT / "docs").glob(pattern) if item.is_file()]
    return max(candidates, key=lambda item: item.stat().st_mtime) if candidates else None


def read_json(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
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


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def pct(value: Any) -> str:
    parsed = as_float(value)
    return "n/a" if parsed is None else f"{parsed:g}%"


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def report_age_hours(data: dict[str, Any], observed_at: datetime | None = None) -> float | None:
    generated_at = parse_ts(data.get("generated_at"))
    if generated_at is None:
        return None
    now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return round(max(0.0, (now - generated_at).total_seconds() / 3600.0), 3)


def row(
    edge_class: str,
    state: str,
    progress: str,
    blocker: str,
    unlock_condition: str,
    next_action: str,
    source: Path | None,
    priority: int,
    *,
    wait_mode: str = "not_applicable",
    earliest_recheck_at_utc: str | None = None,
    gate_progress: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "edge_class": edge_class,
        "state": state,
        "progress": progress,
        "blocker": blocker,
        "unlock_condition": unlock_condition,
        "next_action": next_action,
        "source": portable(source),
        "priority": priority,
        "wait_mode": wait_mode,
        "earliest_recheck_at_utc": earliest_recheck_at_utc,
        "gate_progress": gate_progress or [],
        "can_trade": False,
        "orders_allowed": False,
    }


def gate_progress(
    name: str,
    actual: Any,
    required: Any,
    *,
    direction: str = "minimum",
    passed: bool | None = None,
) -> dict[str, Any]:
    actual_value = as_float(actual)
    required_value = as_float(required)
    if passed is None and actual_value is not None and required_value is not None:
        passed = actual_value >= required_value if direction == "minimum" else actual_value <= required_value
    return {
        "name": name,
        "actual": actual_value,
        "required": required_value,
        "direction": direction,
        "passed": passed,
    }


def latest_timestamp_value(*values: Any) -> str | None:
    parsed = [(parse_ts(value), value) for value in values]
    valid = [(timestamp, str(value)) for timestamp, value in parsed if timestamp is not None]
    return max(valid, key=lambda item: item[0])[1] if valid else None


def select_next_recheck(rows: list[dict[str, Any]], observed_at: datetime) -> dict[str, Any]:
    candidates: list[tuple[datetime, int, dict[str, Any]]] = []
    for item in rows:
        if not str(item.get("state") or "").startswith("waiting"):
            continue
        timestamp = parse_ts(item.get("earliest_recheck_at_utc"))
        if timestamp is not None:
            candidates.append((timestamp, as_int(item.get("priority"), 999), item))
    if not candidates:
        return {
            "status": "sample_driven_only",
            "edge_class": None,
            "at_utc": None,
            "seconds_until": None,
            "read_only_recheck": True,
        }
    due = [item for item in candidates if item[0] <= observed_at]
    if due:
        timestamp, _, item = min(due, key=lambda candidate: (candidate[0], candidate[1]))
        status = "due"
        seconds_until = 0
    else:
        timestamp, _, item = min(candidates, key=lambda candidate: (candidate[0], candidate[1]))
        status = "scheduled"
        seconds_until = int((timestamp - observed_at).total_seconds())
    return {
        "status": status,
        "edge_class": item["edge_class"],
        "at_utc": timestamp.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "seconds_until": seconds_until,
        "read_only_recheck": True,
    }


def registry_has_tombstone(path: Path | None, tombstone_id: str) -> bool:
    data = read_json(path)
    entries = data.get("entries") if isinstance(data.get("entries"), list) else []
    return any(isinstance(item, dict) and item.get("id") == tombstone_id for item in entries)


def bybit_forward_row(path: Path | None, tombstone_registry: Path | None = None) -> dict[str, Any]:
    data = read_json(path)
    decision = str(data.get("decision") or "missing")
    semantic_tombstone = "semantic" in decision or "tombstone" in decision
    sample = data.get("sample_progress") if isinstance(data.get("sample_progress"), dict) else {}
    horizons = data.get("horizon_progress") if isinstance(data.get("horizon_progress"), dict) else {}
    deficits: list[str] = []
    economics: list[str] = []
    for key, item in sorted(horizons.items(), key=lambda kv: as_int(kv[0])):
        if not isinstance(item, dict):
            continue
        deficits.append(f"h{key}:{as_int(item.get('current'))}/{as_int(item.get('required'))}")
        economics.append(
            f"h{key}:mean={as_float(item.get('mean_after_cost_bps'))},wr={as_float(item.get('winrate_positive_pct'))}"
        )
    review_action = str(data.get("review_action") or "unknown")
    tombstoned = registry_has_tombstone(tombstone_registry, "bybit_liquidation_forward_lock_failed")
    if semantic_tombstone:
        state = "tombstoned_semantic_contract"
        blocker = "legacy_position_side_labels_invalid"
    elif review_action == "manual_tombstone_review" and tombstoned:
        state = "tombstoned_no_retune"
        blocker = "forward_gate_failed_tombstoned"
    elif review_action in {"manual_pass_review", "manual_tombstone_review"}:
        state = "manual_review_required"
        blocker = review_action
    elif "waiting" in decision or review_action == "wait":
        state = "waiting_horizon_resolution"
        blocker = ",".join(str(item) for item in data.get("blockers") or []) or "minimum_resolved_event_bars_per_horizon"
    elif data:
        state = "observer_state_unknown"
        blocker = decision
    else:
        state = "missing_report"
        blocker = "run_bybit_liquidation_forward_gate_runner"
    progress = (
        "semantic contract invalid; legacy outcomes excluded"
        if semantic_tombstone
        else (
            f"event_bars {sample.get('event_bars_current')}/{sample.get('event_bars_required')}; "
            f"liq_events {sample.get('liquidation_events_current')}/{sample.get('liquidation_events_required')}; "
            f"horizons {'; '.join(deficits) or 'n/a'}; economics {'; '.join(economics) or 'n/a'}"
        )
    )
    return row(
        "bybit_liquidation_forward_observer",
        state,
        progress,
        blocker,
        "all locked horizons resolved at required N; then review pass/tombstone without retune",
        "never resume this V1 runner; use only the separately locked canonical V4 observer"
        if state in {"tombstoned_no_retune", "tombstoned_semantic_contract"}
        else str(data.get("next_action") or "run gate pulse and keep collecting"),
        path,
        10,
    )


def bybit_canonical_v2_tombstone_row(path: Path | None) -> dict[str, Any]:
    data = read_json(path)
    decision = str(data.get("decision") or "missing")
    v2 = data.get("v2") if isinstance(data.get("v2"), dict) else {}
    state = "tombstoned_design_contract" if data.get("terminal") is True and "tombstone" in decision else "manual_attention_required"
    if not data:
        state = "missing_report"
    return row(
        "bybit_liquidation_canonical_reversal_v2",
        state,
        f"resolved before retirement {as_int(v2.get('resolved_events_at_tombstone'))}; outcomes admitted to successors false",
        "open_exit_bar_risk" if state == "tombstoned_design_contract" else decision,
        "none; V2 is immutable and permanently retired",
        "never resume V2; collect only under the independently sealed packet-ordinal V5 lock",
        path,
        11,
    )


def bybit_canonical_forward_row(path: Path | None) -> dict[str, Any]:
    data = read_json(path)
    decision = str(data.get("decision") or "missing")
    sample = data.get("sample") if isinstance(data.get("sample"), dict) else {}
    lock = data.get("lock") if isinstance(data.get("lock"), dict) else {}
    outcome = data.get("outcome_review") if isinstance(data.get("outcome_review"), dict) else {}
    boundary = data.get("runtime_boundary") if isinstance(data.get("runtime_boundary"), dict) else {}
    if not data:
        state = "missing_report"
    elif data.get("can_trade") is not False or data.get("orders_allowed") is not False or boundary.get("orders_allowed") is not False:
        state = "unsafe_boundary_attention"
    elif decision.endswith("accepted_manual_shadow_only"):
        state = "manual_shadow_review_required"
    elif decision.endswith("no_edge_tombstone"):
        state = "tombstoned_no_retune"
    else:
        state = "waiting_forward_sample"
    progress = (
        f"resolved {as_int(sample.get('resolved_events'))}/100; days {as_int(sample.get('utc_days'))}/5; "
        f"symbols {as_int(sample.get('symbol_count'))}/5; blocks4h {as_int(sample.get('independent_4h_blocks'))}/20; "
        f"floor {lock.get('forward_start_at') or 'n/a'}; outcomes_hidden {outcome.get('interim_outcomes_hidden')}"
    )
    return row(
        "bybit_liquidation_canonical_reversal_v5r1",
        state,
        progress,
        ",".join(str(item) for item in data.get("blockers") or []) or decision,
        "all immutable post-floor sample gates pass; then exactly one terminal cost-adjusted evaluation",
        str(data.get("next_action") or "keep collecting without inspecting interim outcomes"),
        path,
        12,
        wait_mode="outcome_blind_sample_accumulation",
        gate_progress=[
            gate_progress("minimum_resolved_events", sample.get("resolved_events"), 100),
            gate_progress("minimum_utc_days", sample.get("utc_days"), 5),
            gate_progress("minimum_symbols", sample.get("symbol_count"), 5),
            gate_progress("minimum_independent_4h_blocks", sample.get("independent_4h_blocks"), 20),
        ],
    )


def cross_venue_paired_v4_row(path: Path | None) -> dict[str, Any]:
    data = read_json(path)
    decision = str(data.get("decision") or "missing")
    sample = data.get("primary_sample") if isinstance(data.get("primary_sample"), dict) else {}
    source_counters = data.get("source_counters") if isinstance(data.get("source_counters"), dict) else {}
    binance = source_counters.get("binance") if isinstance(source_counters.get("binance"), dict) else {}
    bybit = source_counters.get("bybit") if isinstance(source_counters.get("bybit"), dict) else {}
    lock_summary = data.get("lock") if isinstance(data.get("lock"), dict) else {}
    lock = read_json(lock_summary.get("path")) if lock_summary.get("path") else {}
    gate = lock.get("terminal_gate") if isinstance(lock.get("terminal_gate"), dict) else {}
    terminal = data.get("terminal") if isinstance(data.get("terminal"), dict) else {}
    boundary = data.get("runtime_boundary") if isinstance(data.get("runtime_boundary"), dict) else {}
    required_pairs = as_int(gate.get("minimum_primary_window_pairs"), 200)
    required_days = as_int(gate.get("minimum_utc_days"), 5)
    required_symbols = as_int(gate.get("minimum_symbols"), 5)
    maximum_share = as_float(gate.get("maximum_single_symbol_share"))
    observed_share = as_float(sample.get("max_single_symbol_share"))
    if not data:
        state = "missing_report"
    elif (
        data.get("can_trade") is not False
        or boundary.get("can_trade") is not False
        or boundary.get("orders_allowed") is not False
    ):
        state = "unsafe_boundary_attention"
    elif terminal.get("reached") is True and decision.endswith("manual_price_impact_preregistration"):
        state = "manual_price_impact_preregistration_review_required"
    elif terminal.get("reached") is True and "tombstone" in decision:
        state = "tombstoned_no_retune"
    elif terminal.get("reached") is True:
        state = "terminal_review_required"
    elif "waiting_forward_floor" in decision:
        state = "waiting_forward_floor"
    else:
        state = "waiting_forward_sample"
    progress = (
        f"pairs5s {as_int(sample.get('matched_pairs'))}/{required_pairs}; "
        f"days {as_int(sample.get('utc_days'))}/{required_days}; "
        f"symbols {as_int(sample.get('symbol_count'))}/{required_symbols}; "
        f"max_share {observed_share if observed_share is not None else 'n/a'}/"
        f"{maximum_share if maximum_share is not None else 'n/a'}; "
        f"accepted Bn/By {as_int(binance.get('accepted'))}/{as_int(bybit.get('accepted'))}; "
        f"floor {lock_summary.get('forward_start_at') or 'n/a'}; "
        f"cutoff {data.get('evaluation_cutoff') or 'n/a'}"
    )
    return row(
        "liquidation_cross_venue_paired_leadership_v4",
        state,
        progress,
        ",".join(str(item) for item in data.get("blockers") or []) or decision,
        "all immutable pair, day, symbol, concentration and leadership gates pass; then manual review only",
        str(data.get("next_action") or "keep both collectors and the locked observer running without retuning"),
        path,
        13,
        wait_mode="outcome_blind_sample_accumulation",
        gate_progress=[
            gate_progress("minimum_primary_window_pairs", sample.get("matched_pairs"), required_pairs),
            gate_progress("minimum_utc_days", sample.get("utc_days"), required_days),
            gate_progress("minimum_symbols", sample.get("symbol_count"), required_symbols),
            gate_progress(
                "maximum_single_symbol_share",
                observed_share,
                maximum_share,
                direction="maximum",
            ),
        ],
    )


def post_liq_absorption_row(path: Path | None) -> dict[str, Any]:
    data = read_json(path)
    decision = str(data.get("decision") or "missing")
    semantic_tombstone = "semantic" in decision or "tombstone" in decision
    evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    min_n = as_int(evidence.get("selected_bucket_min_n"))
    required_n = as_int(evidence.get("required_new_events"), 30)
    symbols = evidence.get("selected_symbols") if isinstance(evidence.get("selected_symbols"), list) else []
    required_symbols = as_int(evidence.get("required_new_symbols"), 2)
    positive = as_int(evidence.get("positive_horizons"))
    required_positive = as_int(evidence.get("required_positive_horizons"), 2)
    independent_blocks = as_int(evidence.get("independent_blocks_min"))
    required_independent_blocks = as_int(evidence.get("required_independent_blocks"), 20)
    independence_decision = str(evidence.get("independence_decision") or "missing")
    independence_eligible = evidence.get("independence_eligible_for_manual_review") is True
    if semantic_tombstone:
        state = "tombstoned_semantic_contract"
    elif "passed" in decision:
        state = "manual_review_required"
    elif "failed" in decision:
        state = "tombstone_review_required"
    elif data:
        state = "waiting_new_post_lock_events"
    else:
        state = "missing_report"
    blockers = ["legacy_position_side_labels_invalid"] if semantic_tombstone else [str(item) for item in data.get("blockers") or []]
    if data and not semantic_tombstone and not independence_eligible and "independence" not in " ".join(blockers):
        blockers.append("independence_sample_not_ready")
    progress = (
        "semantic contract invalid; legacy outcomes excluded"
        if semantic_tombstone
        else (
            f"min_n {min_n}/{required_n}; symbols {len(symbols)}/{required_symbols}; "
            f"positive_horizons {positive}/{required_positive}; independent_4h_blocks "
            f"{independent_blocks}/{required_independent_blocks}; independence {independence_decision}"
        )
    )
    return row(
        "post_liquidation_absorption_spot_perp",
        state,
        progress,
        ",".join(sorted(set(blockers))) or "post_lock_sample_not_ready",
        "locked bucket reaches required post-lock N, symbols, independent blocks and independence-adjusted cost gates",
        "never resume this V1 runner; any reuse requires a new canonical-label preregistration"
        if semantic_tombstone
        else str(data.get("next_action") or "run post-liq absorption forward observer runner"),
        path,
        20,
    )


def liquidation_timing_vol_row(path: Path | None) -> dict[str, Any]:
    data = read_json(path)
    decision = str(data.get("decision") or "missing")
    semantic_tombstone = "semantic" in decision or "tombstone" in decision
    evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    min_n = as_int(evidence.get("selected_bucket_min_n"))
    required_n = as_int(evidence.get("required_new_events"), 30)
    symbols = evidence.get("selected_symbols") if isinstance(evidence.get("selected_symbols"), list) else []
    required_symbols = as_int(evidence.get("required_new_symbols"), 2)
    positive = as_int(evidence.get("positive_horizons"))
    required_positive = as_int(evidence.get("required_positive_horizons"), 1)
    if semantic_tombstone:
        state = "tombstoned_semantic_contract"
    elif "passed" in decision:
        state = "manual_review_required"
    elif "failed" in decision:
        state = "tombstone_review_required"
    elif data:
        state = "waiting_new_post_lock_events"
    else:
        state = "missing_report"
    progress = (
        "semantic contract invalid; legacy outcomes excluded"
        if semantic_tombstone
        else f"min_n {min_n}/{required_n}; symbols {len(symbols)}/{required_symbols}; positive_horizons {positive}/{required_positive}"
    )
    return row(
        "liquidation_timing_vol_continuation",
        state,
        progress,
        "legacy_position_side_labels_invalid"
        if semantic_tombstone
        else ",".join(str(item) for item in data.get("blockers") or []) or "post_lock_sample_not_ready",
        "locked timing/vol bucket reaches required post-lock N, symbols and positive horizons",
        "never resume this V1 runner; any reuse requires a new canonical-label preregistration"
        if semantic_tombstone
        else str(data.get("next_action") or "run liquidation timing-vol forward observer runner"),
        path,
        25,
    )


def forward_observer_row(
    path: Path | None,
    *,
    edge_class: str,
    priority: int,
    observed_at: datetime | None = None,
    max_age_hours: float = 26.0,
) -> dict[str, Any]:
    data = read_json(path)
    decision = str(data.get("decision") or "missing")
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    scan = data.get("latest_scan") if isinstance(data.get("latest_scan"), dict) else {}
    integrity = data.get("sample_integrity") if isinstance(data.get("sample_integrity"), dict) else {}
    lock = read_json(data.get("lock_path")) if data.get("lock_path") else {}
    gate = lock.get("forward_gate_required") if isinstance(lock.get("forward_gate_required"), dict) else {}
    boundary = data.get("runtime_boundary") if isinstance(data.get("runtime_boundary"), dict) else {}
    required = as_int(gate.get("minimum_new_resolved_signals"), 30)
    canonical = as_int(integrity.get("canonical_nonoverlap_rows"), as_int(summary.get("trades")))
    raw = as_int(integrity.get("journal_rows"), canonical)
    excluded = as_int(integrity.get("excluded_rows"))
    pending = len(scan.get("pending")) if isinstance(scan.get("pending"), list) else 0
    age_hours = report_age_hours(data, observed_at)
    stale = age_hours is None or age_hours > max_age_hours
    if not data:
        state = "missing_report"
    elif data.get("can_trade") is not False or boundary.get("orders_allowed") is not False:
        state = "unsafe_boundary_attention"
    elif stale:
        state = "stale_observer_attention"
    elif "passed" in decision:
        state = "manual_review_required"
    elif "failed" in decision:
        state = "tombstone_review_required"
    else:
        state = "waiting_forward_sample"
    blockers = [str(item) for item in data.get("blockers") or []]
    if stale:
        blockers.append("observer_report_stale")
    progress = (
        f"canonical {canonical}/{required}; raw {raw}; excluded {excluded}; pending {pending}; "
        f"latest_bar {scan.get('latest_bar_ts') or 'n/a'}; age_h {age_hours if age_hours is not None else 'n/a'}"
    )
    return row(
        edge_class,
        state,
        progress,
        ",".join(sorted(set(blockers))) or "minimum_forward_sample",
        "fresh locked observer reaches required canonical non-overlapping sample; then manual review only",
        str(data.get("next_action") or "keep collecting locked forward outcomes without retuning"),
        path,
        priority,
        wait_mode="forward_sample_accumulation",
        gate_progress=[gate_progress("minimum_new_resolved_signals", canonical, required)],
    )


def microstructure_row(path: Path | None) -> dict[str, Any]:
    data = read_json(path)
    coverage = data.get("coverage") if isinstance(data.get("coverage"), dict) else {}
    sla = data.get("sla") if isinstance(data.get("sla"), dict) else {}
    diagnostic = data.get("book_diagnostic") if isinstance(data.get("book_diagnostic"), dict) else {}
    decision = str(data.get("decision") or "missing")
    book = coverage.get("book_coverage_pct")
    required = coverage.get("required_book_coverage_pct")
    cooldown = sla.get("cooldown_until_utc")
    earliest_recheck = latest_timestamp_value(diagnostic.get("eta_utc"), cooldown)
    if "available" in decision or "sealed" in decision:
        state = "snapshot_ready_for_locked_research"
    elif data:
        state = "waiting_book_coverage_sla"
    else:
        state = "missing_report"
    progress = (
        f"trade_coverage {pct(coverage.get('trade_coverage_pct'))}/{pct(coverage.get('required_trade_coverage_pct'))}; "
        f"book_coverage {pct(book)}/{pct(required)}; recent_1h {pct(diagnostic.get('recent_1h_dual_book_pct'))}; "
        f"recent_6h {pct(diagnostic.get('recent_6h_dual_book_pct'))}; eta_utc {diagnostic.get('eta_utc') or 'n/a'}; "
        f"cooldown_until {cooldown or 'n/a'}"
    )
    return row(
        "cross_venue_microstructure_snapshot",
        state,
        progress,
        ",".join(str(item) for item in data.get("blockers") or []) or "microstructure_gate_not_ready",
        "book coverage and SLA pass; then sealed snapshot runner only",
        str(data.get("next_action") or "keep collector running and recheck gate"),
        path,
        30,
        wait_mode="time_and_operational_gate",
        earliest_recheck_at_utc=earliest_recheck,
        gate_progress=[
            gate_progress("minimum_trade_coverage_pct", coverage.get("trade_coverage_pct"), coverage.get("required_trade_coverage_pct")),
            gate_progress("minimum_book_coverage_pct", book, required),
            gate_progress("minimum_recent_1h_dual_book_pct", diagnostic.get("recent_1h_dual_book_pct"), required),
            gate_progress("minimum_recent_6h_dual_book_pct", diagnostic.get("recent_6h_dual_book_pct"), required),
        ],
    )


def binance_force_order_row(
    path: Path | None,
    progress_path: Path | None = None,
    continuity_path: Path | None = None,
) -> dict[str, Any]:
    data = read_json(path)
    progress_data = read_json(progress_path)
    continuity_data = read_json(continuity_path)
    events = data.get("events") if isinstance(data.get("events"), dict) else {}
    research = (
        events.get("preregistered_sample")
        if isinstance(events.get("preregistered_sample"), dict)
        else events.get("research_universe")
        if isinstance(events.get("research_universe"), dict)
        else events
    )
    data_quality_event_count = as_int(research.get("events"))
    decision = str(data.get("decision") or "missing")
    continuity_decision = str(continuity_data.get("decision") or "missing")
    continuity_observed = continuity_data.get("continuity_observed") is True
    hard = [str(item.get("name") or item) for item in data.get("hard_failures") or []]
    soft = [str(item.get("name") or item) for item in data.get("soft_failures") or []]
    sample = progress_data.get("sample") if isinstance(progress_data.get("sample"), dict) else {}
    progress_event_count = as_int(sample.get("events"))
    event_count = progress_event_count if progress_data else data_quality_event_count
    if hard:
        state = "data_quality_attention_required"
    elif progress_data.get("ready_for_pipeline") is True and data.get("ready_for_preregistered_research") is True:
        if not continuity_data:
            state = "waiting_transport_continuity_report"
        elif continuity_observed:
            state = "ready_for_locked_research_pipeline"
        else:
            state = "waiting_transport_continuity"
    elif progress_data and data.get("ready_for_preregistered_research") is True:
        state = "waiting_preregistered_sample_gates"
    elif data.get("ready_for_preregistered_research") is True:
        state = "ready_for_preregistered_research"
    elif event_count > 0:
        state = "waiting_real_event_sample"
    elif as_int(events.get("events")) > 0:
        state = "waiting_preregistered_sample"
    elif data:
        state = "waiting_real_events"
    else:
        state = "missing_report"
    contexts = sample.get("contexts") if isinstance(sample.get("contexts"), dict) else {}
    progress_text = (
        f"preregistered_events {event_count}; event_bars {as_int(sample.get('event_bars'))}; "
        f"matched {as_int(sample.get('matched_price_bars'))}; contexts L/S "
        f"{as_int(contexts.get('long_liquidation_flush'))}/{as_int(contexts.get('short_liquidation_squeeze'))}; "
        f"symbols {len(sample.get('symbols_with_events') or [])}; "
        f"independent4h {as_int(sample.get('independent_4h_blocks'))}; "
        f"matured4h {as_int(sample.get('matured_independent_4h_blocks'))}; "
        f"earliest {((progress_data.get('velocity') or {}).get('theoretical_earliest_pipeline_at') or 'n/a')}; "
        f"all_market_events {as_int(events.get('events'))}"
        if progress_data
        else f"preregistered_events {event_count}; research_universe_events {as_int((events.get('research_universe') or {}).get('events'))}; all_market_events {as_int(events.get('events'))}; last_age_min {research.get('last_event_age_minutes')}"
    )
    if continuity_data:
        continuity_sample = continuity_data.get("sample") if isinstance(continuity_data.get("sample"), dict) else {}
        progress_text += (
            f"; transport {continuity_decision}; continuity {continuity_observed}; "
            f"continuity_h {as_float(continuity_sample.get('observation_hours'))}; "
            f"gaps {len(continuity_data.get('gaps_over_threshold') or [])}; "
            f"invalid_proofs {as_int(continuity_sample.get('invalid_liveness_rows'))}"
        )
    progress_blockers = [str(item) for item in progress_data.get("blockers") or []]
    continuity_blockers = [str(item) for item in continuity_data.get("blockers") or []]
    source_gates = progress_data.get("gates") if isinstance(progress_data.get("gates"), list) else []
    normalized_gates = [
        gate_progress(
            str(item.get("name") or "unnamed_gate"),
            item.get("actual"),
            item.get("required"),
            passed=item.get("passed") if isinstance(item.get("passed"), bool) else None,
        )
        for item in source_gates
        if isinstance(item, dict)
    ]
    if continuity_data:
        normalized_gates.append(
            gate_progress(
                "transport_continuity_observed",
                1 if continuity_observed else 0,
                1,
                passed=continuity_observed,
            )
        )
    earliest_pipeline = (progress_data.get("velocity") or {}).get("theoretical_earliest_pipeline_at")
    transport_waiting = state in {"waiting_transport_continuity", "waiting_transport_continuity_report"}
    continuity_recheck = (
        (continuity_data.get("recovery") or {}).get("earliest_recheck_at_utc")
        if isinstance(continuity_data.get("recovery"), dict)
        else None
    )
    next_action = (
        "keep the collector running until rolling transport continuity is observed; do not run locked research"
        if transport_waiting
        else str(progress_data.get("next_action") or data.get("next_action") or "keep collector running")
    )
    return row(
        "binance_force_order_feed",
        state,
        progress_text,
        ",".join(hard + progress_blockers + continuity_blockers + soft) or continuity_decision or decision,
        "all immutable sample and data-quality gates pass and rolling transport continuity is observed",
        next_action,
        continuity_path if transport_waiting and continuity_data else progress_path if progress_data else path,
        40,
        wait_mode="transport_continuity_gate" if transport_waiting else "sample_maturity_gate",
        earliest_recheck_at_utc=(
            str(continuity_recheck)
            if transport_waiting and continuity_recheck
            else None
            if transport_waiting
            else str(earliest_pipeline)
            if earliest_pipeline
            else None
        ),
        gate_progress=normalized_gates,
    )


def strategy_frontier_row(path: Path | None) -> dict[str, Any]:
    data = read_json(path)
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    decision = str(data.get("decision") or "missing")
    promotable = as_int(summary.get("promotable"))
    active_observers = as_int(summary.get("observer_only"))
    stale_observers = as_int(summary.get("stale_observers"))
    missing_runtime = as_int(summary.get("candidate_needs_observer_runtime"))
    rejected = as_int(summary.get("rejected"))
    if promotable > 0:
        state = "promotable_family_present"
    elif decision == "observer_runtime_truth_gap_detected":
        state = "observer_runtime_truth_gap"
    else:
        state = "no_promotable_family"
    if stale_observers or missing_runtime:
        blocker = f"stale_observers:{stale_observers},candidate_needs_runtime:{missing_runtime}"
    else:
        blocker = "no_promotable_family" if promotable == 0 else "manual_review_required"
    return row(
        "strategy_frontier",
        state if data else "missing_report",
        f"promotable {promotable}; active_observers {active_observers}; stale_observers {stale_observers}; "
        f"candidate_needs_runtime {missing_runtime}; rejected {rejected}",
        blocker,
        "only count fresh runtime-proven observers; only promote families with independent forward/holdout evidence",
        str(data.get("next_action") or "keep rejecting weak families and search independent classes"),
        path,
        50,
    )


def devil_audit_row(path: Path | None) -> dict[str, Any]:
    data = read_json(path)
    counts = data.get("open_severity_counts") if isinstance(data.get("open_severity_counts"), dict) else {}
    parity = (data.get("source_runtime_parity") or {}).get("passed") if isinstance(data.get("source_runtime_parity"), dict) else None
    p0 = as_int(counts.get("P0"))
    p1 = as_int(counts.get("P1"))
    state = "operational_runtime_healthy_edge_unproven" if p0 == 0 and parity is True else "audit_attention_required"
    return row(
        "devil_audit_runtime_boundary",
        state if data else "missing_report",
        f"P0 {p0}; P1 {p1}; parity {parity}; can_trade {data.get('can_trade')}",
        "edge_unproven" if p0 == 0 else "P0_open",
        "P0=0, parity=true, can_trade=false while edge remains unproven",
        str(data.get("next_strong_move") or data.get("next_action") or "rerun full system devil audit"),
        path,
        60,
    )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    observed_at = datetime.now(timezone.utc)
    paths = {
        "bybit_forward": resolve_path(args.bybit_forward) if args.bybit_forward else (latest("BYBIT_LIQUIDATION_FORWARD_SEMANTIC_TOMBSTONE_*.json") or latest("BYBIT_LIQUIDATION_FORWARD_GATE_RUNNER_*.json")),
        "bybit_canonical_v2_tombstone": resolve_path(args.bybit_canonical_v2_tombstone) if args.bybit_canonical_v2_tombstone else latest("BYBIT_LIQUIDATION_CANONICAL_FORWARD_V2_DESIGN_TOMBSTONE_*.json"),
        "bybit_canonical_forward": resolve_path(args.bybit_canonical_forward) if args.bybit_canonical_forward else latest("BYBIT_LIQUIDATION_CANONICAL_FORWARD_OBSERVER_V5R2_*.json"),
        "cross_venue_paired_v4": resolve_path(args.cross_venue_paired_v4) if args.cross_venue_paired_v4 else latest("LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_FORWARD_OBSERVER_V4_*.json"),
        "post_liq_absorption": resolve_path(args.post_liq_absorption) if args.post_liq_absorption else (latest("POST_LIQUIDATION_ABSORPTION_SEMANTIC_TOMBSTONE_*.json") or latest("POST_LIQUIDATION_ABSORPTION_FORWARD_OBSERVER_RUNNER_*.json")),
        "liquidation_timing_vol": resolve_path(args.liquidation_timing_vol) if args.liquidation_timing_vol else (latest("LIQUIDATION_TIMING_VOL_SEMANTIC_TOMBSTONE_*.json") or latest("LIQUIDATION_TIMING_VOL_FORWARD_OBSERVER_RUNNER_*.json")),
        "derivatives_squeeze": resolve_path(args.derivatives_squeeze) if args.derivatives_squeeze else latest("DERIVATIVES_SQUEEZE_DISAGREEMENT_FORWARD_OBSERVER_*.json"),
        "alt_breadth": resolve_path(args.alt_breadth) if args.alt_breadth else latest("ALT_BREADTH_DISLOCATION_FORWARD_OBSERVER_*.json"),
        "microstructure": resolve_path(args.microstructure) if args.microstructure else (
            latest("MICROSTRUCTURE_UNBLOCK_STATUS_*.json") or latest("CROSS_VENUE_MICROSTRUCTURE_UNBLOCK_STATUS_*.json")
        ),
        "binance_force_order": resolve_path(args.binance_force_order) if args.binance_force_order else latest("LIQUIDATION_FORCE_ORDER_DATA_QUALITY*.json"),
        "binance_force_order_progress": resolve_path(args.binance_force_order_progress) if args.binance_force_order_progress else latest("LIQUIDATION_FORCE_ORDER_PREREGISTERED_PROGRESS*.json"),
        "binance_force_order_continuity": resolve_path(args.binance_force_order_continuity) if args.binance_force_order_continuity else latest("LIQUIDATION_FORCE_ORDER_TRANSPORT_CONTINUITY*.json"),
        "strategy_frontier": resolve_path(args.strategy_frontier) if args.strategy_frontier else latest("STRATEGY_RESEARCH_FRONTIER_MATRIX_*.json"),
        "devil_audit": resolve_path(args.devil_audit) if args.devil_audit else latest("FULL_SYSTEM_DEVIL_AUDIT_*.json"),
        "tombstone_registry": resolve_path(args.tombstone_registry) if args.tombstone_registry else latest("EDGE_TOMBSTONE_REGISTRY_*.json"),
    }
    rows = [
        bybit_forward_row(paths["bybit_forward"], paths["tombstone_registry"]),
        bybit_canonical_v2_tombstone_row(paths["bybit_canonical_v2_tombstone"]),
        bybit_canonical_forward_row(paths["bybit_canonical_forward"]),
        cross_venue_paired_v4_row(paths["cross_venue_paired_v4"]),
        post_liq_absorption_row(paths["post_liq_absorption"]),
        liquidation_timing_vol_row(paths["liquidation_timing_vol"]),
        forward_observer_row(paths["derivatives_squeeze"], edge_class="derivatives_squeeze_disagreement", priority=27, observed_at=observed_at),
        forward_observer_row(paths["alt_breadth"], edge_class="alt_breadth_dislocation", priority=28, observed_at=observed_at),
        microstructure_row(paths["microstructure"]),
        binance_force_order_row(
            paths["binance_force_order"],
            paths["binance_force_order_progress"],
            paths["binance_force_order_continuity"],
        ),
        strategy_frontier_row(paths["strategy_frontier"]),
        devil_audit_row(paths["devil_audit"]),
    ]
    rows.sort(key=lambda item: item["priority"])
    action_required = [item for item in rows if "review_required" in item["state"] or "attention" in item["state"]]
    waiting = [item for item in rows if item["state"].startswith("waiting") or item["state"].startswith("no_")]
    missing = [item for item in rows if item["state"] == "missing_report"]
    next_recheck = select_next_recheck(rows, observed_at)
    decision = "edge_waiting_board_no_trade_observing"
    if action_required:
        decision = "edge_waiting_board_manual_attention_required"
    elif missing:
        decision = "edge_waiting_board_missing_reports"
    return {
        "generated_at": observed_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "tool": "tools/edge_waiting_board.py",
        "decision": decision,
        "can_trade": False,
        "orders_allowed": False,
        "summary": {
            "rows": len(rows),
            "manual_attention": len(action_required),
            "waiting": len(waiting),
            "missing_reports": len(missing),
            "trade_enabled": 0,
            "scheduled_rechecks": sum(1 for item in rows if item.get("earliest_recheck_at_utc")),
        },
        "rows": rows,
        "next_recheck": next_recheck,
        "next_strong_move": (
            action_required[0]["next_action"]
            if action_required
            else (
                f"run the read-only gate recheck now for {next_recheck['edge_class']}; do not promote or trade"
                if next_recheck["status"] == "due"
                else (
                    f"keep collectors running; next bounded time-gated recheck is {next_recheck['at_utc']} "
                    f"for {next_recheck['edge_class']}; sample-driven observers continue unchanged"
                    if next_recheck["status"] == "scheduled"
                    else "keep collectors running; all remaining gates are sample-driven"
                )
            )
        ),
        "boundary": {
            "read_only_status": True,
            "runs_research": False,
            "emits_signals": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "can_trade": False,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Edge Waiting Board",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        f"- Manual attention: `{report['summary']['manual_attention']}`",
        f"- Waiting: `{report['summary']['waiting']}`",
        f"- Missing reports: `{report['summary']['missing_reports']}`",
        f"- Scheduled rechecks: `{report['summary']['scheduled_rechecks']}`",
        f"- Next recheck: `{report['next_recheck']['status']}` / `{report['next_recheck']['edge_class']}` / `{report['next_recheck']['at_utc']}`",
        "",
        "| Edge Class | State | Wait Mode | Recheck UTC | Progress | Blocker | Unlock Condition | Next Action | Source |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for item in report["rows"]:
        lines.append(
            f"| `{item['edge_class']}` | `{item['state']}` | `{item['wait_mode']}` | "
            f"`{item['earliest_recheck_at_utc']}` | {item['progress']} | "
            f"`{item['blocker']}` | {item['unlock_condition']} | {item['next_action']} | `{item['source']}` |"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Read-only status board.",
            "- Does not run research, emit signals, open paper entries or send orders.",
            "- `can_trade=false` is intentional.",
            "",
            "## Next Strong Move",
            "",
            f"- {report['next_strong_move']}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only board for current edge candidates and waiting gates.")
    parser.add_argument("--bybit-forward", default="")
    parser.add_argument("--bybit-canonical-forward", default="")
    parser.add_argument("--bybit-canonical-v2-tombstone", default="")
    parser.add_argument("--cross-venue-paired-v4", default="")
    parser.add_argument("--post-liq-absorption", default="")
    parser.add_argument("--liquidation-timing-vol", default="")
    parser.add_argument("--derivatives-squeeze", default="")
    parser.add_argument("--alt-breadth", default="")
    parser.add_argument("--microstructure", default="")
    parser.add_argument("--binance-force-order", default="")
    parser.add_argument("--binance-force-order-progress", default="")
    parser.add_argument("--binance-force-order-continuity", default="")
    parser.add_argument("--strategy-frontier", default="")
    parser.add_argument("--devil-audit", default="")
    parser.add_argument("--tombstone-registry", default="")
    parser.add_argument("--out-prefix", default="docs/EDGE_WAITING_BOARD_2026-07-03")
    args = parser.parse_args()
    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "summary": report["summary"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

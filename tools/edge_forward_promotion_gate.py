#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {"_read_error": "invalid_json", "_path": str(path)}
    return payload if isinstance(payload, dict) else {"_read_error": "not_object", "_path": str(path)}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def safe_int(value: Any) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def gate(name: str, passed: bool, actual: Any, required: Any) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "actual": actual,
        "required": required,
        "severity": "hard",
    }


def gates_pass(items: list[dict[str, Any]]) -> bool:
    return all(item.get("passed") for item in items)


def render_gate_table(items: list[dict[str, Any]]) -> list[str]:
    lines = ["| gate | pass | actual | required |", "|---|---:|---|---|"]
    for item in items:
        lines.append(f"| {item.get('name')} | `{item.get('passed')}` | `{item.get('actual')}` | `{item.get('required')}` |")
    return lines


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    export_path = resolve_path(args.edge_export)
    observer_path = resolve_path(args.observer)
    scoreboard_path = resolve_path(args.scoreboard)
    pending_watch_path = resolve_path(args.pending_watch)
    notify_path = resolve_path(args.pending_watch_notify)
    alert_drill_path = resolve_path(args.alert_drill)

    export = read_json(export_path)
    observer = read_json(observer_path)
    scoreboard = read_json(scoreboard_path)
    pending_watch = read_json(pending_watch_path)
    notify = read_json(notify_path)
    alert_drill = read_json(alert_drill_path)

    selected = export.get("selected_candidate") if isinstance(export.get("selected_candidate"), dict) else {}
    edge_row = export.get("edge_registry_row") if isinstance(export.get("edge_registry_row"), dict) else {}
    nested_evidence = export.get("nested_oos_evidence") if isinstance(export.get("nested_oos_evidence"), dict) else {}
    metrics = edge_row.get("metrics") if isinstance(edge_row.get("metrics"), dict) else {}
    latest = observer.get("latest_result") if isinstance(observer.get("latest_result"), dict) else {}
    summary = scoreboard.get("summary") if isinstance(scoreboard.get("summary"), dict) else {}
    pending_latest = pending_watch.get("latest") if isinstance(pending_watch.get("latest"), dict) else {}

    if nested_evidence:
        train_window = selected.get("train") if isinstance(selected.get("train"), dict) else {}
        train_summary = train_window.get("summary") if isinstance(train_window.get("summary"), dict) else {}
        oos_window = nested_evidence.get("oos") if isinstance(nested_evidence.get("oos"), dict) else {}
        oos_summary = oos_window.get("summary") if isinstance(oos_window.get("summary"), dict) else {}
        full_trades = safe_int(train_summary.get("trades"))
        full_expectancy = safe_float(train_summary.get("expectancy_r"))
        holdout_trades = safe_int(oos_summary.get("trades"))
        holdout_expectancy = safe_float(oos_summary.get("expectancy_r"))
        stable_folds = safe_int(oos_window.get("stable_folds"))
        segment_positive_ratio = None
        cost10_expectancy = safe_float(nested(oos_window, "cost_stress", "summary", "expectancy_r"))
    else:
        full_trades = safe_int(metrics.get("full_trades"))
        full_expectancy = safe_float(metrics.get("full_expectancy_r"))
        holdout_trades = safe_int(metrics.get("holdout_trades"))
        holdout_expectancy = safe_float(metrics.get("holdout_expectancy_r"))
        stable_folds = safe_int(metrics.get("stable_folds"))
        segment_positive_ratio = safe_float(metrics.get("segment_positive_ratio"))
        cost10_expectancy = safe_float(metrics.get("cost10_expectancy_r"))

    observer_signal_events = safe_int(summary.get("observer_signal_events"))
    resolved = safe_int(summary.get("resolved"))
    expectancy = safe_float(summary.get("expectancy_r"))
    winrate = safe_float(summary.get("winrate_pct"))
    breakeven = safe_float(summary.get("breakeven_winrate_pct"))
    max_drawdown = safe_float(summary.get("max_drawdown_r"))
    data_degraded = latest.get("data_degraded")

    if nested_evidence:
        nested_decision = str(nested_evidence.get("decision") or "")
        train_gate_pass = nested(selected, "train_gate", "pass") is True
        research_gates = [
            gate("edge_export_exists", bool(export) and not export.get("_read_error"), rel_path(export_path), "readable JSON"),
            gate("selected_candidate_exists", bool(selected), selected.get("strategy_id"), "selected_candidate"),
            gate("nested_train_gate_pass", train_gate_pass, train_gate_pass, True),
            gate(
                "nested_oos_status_allows_observation",
                nested_decision in {"insufficient_oos_evidence_keep_observer_only", "pass_oos_observer_candidate_not_trade_permission"},
                nested_decision,
                "insufficient_oos_evidence_keep_observer_only or pass_oos_observer_candidate_not_trade_permission",
            ),
            gate("nested_oos_expectancy_positive", holdout_expectancy is not None and holdout_expectancy > 0.0, holdout_expectancy, "> 0R"),
            gate("nested_oos_cost10_positive", cost10_expectancy is not None and cost10_expectancy > 0.0, cost10_expectancy, "> 0R"),
        ]
    else:
        research_gates = [
            gate("edge_export_exists", bool(export) and not export.get("_read_error"), rel_path(export_path), "readable JSON"),
            gate("selected_candidate_exists", bool(selected), selected.get("strategy_id"), "selected_candidate"),
            gate("edge_classification", edge_row.get("edge_classification") == "edge_candidate_forward_proof_required", edge_row.get("edge_classification"), "edge_candidate_forward_proof_required"),
            gate("edge_blocks_empty", edge_row.get("blocks") == [], edge_row.get("blocks"), "[]"),
            gate("history_min_full_trades", full_trades >= args.min_full_trades, full_trades, args.min_full_trades),
            gate("history_full_expectancy", full_expectancy is not None and full_expectancy >= args.min_history_expectancy_r, full_expectancy, args.min_history_expectancy_r),
            gate("history_min_holdout_trades", holdout_trades >= args.min_holdout_trades, holdout_trades, args.min_holdout_trades),
            gate("history_holdout_expectancy", holdout_expectancy is not None and holdout_expectancy >= args.min_holdout_expectancy_r, holdout_expectancy, args.min_holdout_expectancy_r),
            gate("history_stable_folds", stable_folds >= args.min_stable_folds, stable_folds, args.min_stable_folds),
            gate("history_segment_positive_ratio", segment_positive_ratio is not None and segment_positive_ratio >= args.min_segment_positive_ratio, segment_positive_ratio, args.min_segment_positive_ratio),
            gate("history_cost10_expectancy", cost10_expectancy is not None and cost10_expectancy >= args.min_cost10_expectancy_r, cost10_expectancy, args.min_cost10_expectancy_r),
        ]
    observer_gates = [
        gate("observer_report_exists", bool(observer) and not observer.get("_read_error"), rel_path(observer_path), "readable JSON"),
        gate("scoreboard_report_exists", bool(scoreboard) and not scoreboard.get("_read_error"), rel_path(scoreboard_path), "readable JSON"),
        gate("latest_observer_not_degraded", data_degraded is False, data_degraded, False),
        gate("observer_signal_events", observer_signal_events >= args.min_observer_signals, observer_signal_events, args.min_observer_signals),
        gate("observer_resolved_outcomes", resolved >= args.min_resolved, resolved, args.min_resolved),
        gate("observer_expectancy", expectancy is not None and expectancy >= args.min_forward_expectancy_r, expectancy, args.min_forward_expectancy_r),
        gate("observer_winrate_vs_breakeven", winrate is not None and breakeven is not None and winrate >= breakeven, f"{winrate} vs {breakeven}", "winrate >= breakeven"),
        gate("observer_drawdown_cap", max_drawdown is not None and max_drawdown >= -abs(args.max_drawdown_r), max_drawdown, f">= -{abs(args.max_drawdown_r)}R"),
    ]
    operational_gates = [
        gate("pending_watch_report_exists", bool(pending_watch) and not pending_watch.get("_read_error"), rel_path(pending_watch_path), "readable JSON"),
        gate("pending_watch_no_trade_permission", pending_watch.get("can_trade") is False, pending_watch.get("can_trade"), False),
        gate("pending_watch_latest_present", bool(pending_latest), bool(pending_latest), True),
        gate("telegram_notify_report_exists", bool(notify) and not notify.get("_read_error"), rel_path(notify_path), "readable JSON"),
        gate("telegram_notify_no_trade_permission", notify.get("can_trade") is False, notify.get("can_trade"), False),
        gate("telegram_notify_not_error", notify.get("decision") not in {"telegram_api_error", "telegram_send_error"}, notify.get("decision"), "not telegram error"),
        gate("alert_drill_exists", bool(alert_drill) and not alert_drill.get("_read_error"), rel_path(alert_drill_path), "readable JSON"),
        gate("alert_drill_passed", alert_drill.get("decision") == "range_signal_alert_drill_passed", alert_drill.get("decision"), "range_signal_alert_drill_passed"),
        gate("alert_drill_no_trade_permission", alert_drill.get("can_trade") is False, alert_drill.get("can_trade"), False),
    ]

    research_ok = gates_pass(research_gates)
    observer_ok = gates_pass(observer_gates)
    operational_ok = gates_pass(operational_gates)

    if not research_ok:
        decision = "blocked_edge_research_gate_failed"
        next_action = "do not observe this candidate until edge export/history gates are fixed"
    elif not operational_ok:
        decision = "blocked_edge_operational_gate_failed"
        next_action = "fix pending-watch or Telegram warning gate before promotion review"
    elif not observer_ok:
        decision = "blocked_waiting_edge_forward_outcomes"
        next_action = "keep edge observer running until enough real resolved observer outcomes exist"
    else:
        decision = "edge_candidate_for_paper_design_review_only"
        next_action = "manual review required; design a separate paper-entry gate before any execution path"

    promotion = {
        "observer_allowed": research_ok and operational_ok,
        "paper_design_review_allowed": research_ok and operational_ok and observer_ok,
        "paper_execution_allowed": False,
        "live_execution_allowed": False,
        "manual_review_required": True,
        "can_trade": False,
    }

    return {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "edge_forward_promotion_gate_evidence_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
            "changes_active_strategy": False,
        },
        "inputs": {
            "edge_export": rel_path(export_path),
            "observer": rel_path(observer_path),
            "scoreboard": rel_path(scoreboard_path),
            "pending_watch": rel_path(pending_watch_path),
            "pending_watch_notify": rel_path(notify_path),
            "alert_drill": rel_path(alert_drill_path),
        },
        "thresholds": {
            "min_full_trades": args.min_full_trades,
            "min_history_expectancy_r": args.min_history_expectancy_r,
            "min_holdout_trades": args.min_holdout_trades,
            "min_holdout_expectancy_r": args.min_holdout_expectancy_r,
            "min_stable_folds": args.min_stable_folds,
            "min_segment_positive_ratio": args.min_segment_positive_ratio,
            "min_cost10_expectancy_r": args.min_cost10_expectancy_r,
            "min_observer_signals": args.min_observer_signals,
            "min_resolved": args.min_resolved,
            "min_forward_expectancy_r": args.min_forward_expectancy_r,
            "max_drawdown_r": args.max_drawdown_r,
        },
        "candidate": {
            "strategy_id": selected.get("strategy_id"),
            "base_strategy_id": selected.get("base_strategy_id"),
            "filter_mode": selected.get("filter_mode"),
            "side": selected.get("side"),
            "interval": selected.get("interval"),
            "trigger": selected.get("trigger"),
            "rr": selected.get("rr"),
            "edge_classification": edge_row.get("edge_classification") or nested_evidence.get("decision"),
            "evidence_score": edge_row.get("evidence_score"),
            "blocks": edge_row.get("blocks"),
            "full_trades": full_trades,
            "full_expectancy_r": full_expectancy,
            "holdout_trades": holdout_trades,
            "holdout_expectancy_r": holdout_expectancy,
            "stable_folds": stable_folds,
            "segment_positive_ratio": segment_positive_ratio,
            "cost10_expectancy_r": cost10_expectancy,
        },
        "observer": {
            "latest_status": latest.get("status"),
            "latest_closed_bar_ts": latest.get("latest_closed_bar_ts"),
            "latest_closed_close": latest.get("latest_closed_close"),
            "data_degraded": data_degraded,
        },
        "scoreboard": {
            "classification": summary.get("classification"),
            "observer_signal_events": observer_signal_events,
            "resolved": resolved,
            "winrate_pct": winrate,
            "breakeven_winrate_pct": breakeven,
            "expectancy_r": expectancy,
            "max_drawdown_r": max_drawdown,
        },
        "operational": {
            "pending_watch_classification": pending_watch.get("classification"),
            "pending_watch_context_ok": pending_latest.get("context_ok"),
            "pending_watch_trigger_ok": pending_latest.get("trigger_ok"),
            "pending_watch_distance_atr": nested(pending_latest, "trigger", "distance_to_trigger_atr"),
            "telegram_notify_decision": notify.get("decision"),
            "telegram_response_ok": notify.get("telegram_response_ok"),
        },
        "gates": {
            "research": research_gates,
            "observer": observer_gates,
            "operational": operational_gates,
        },
        "promotion": promotion,
        "decision": decision,
        "next_action": next_action,
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    candidate = report.get("candidate", {})
    scoreboard = report.get("scoreboard", {})
    operational = report.get("operational", {})
    promotion = report.get("promotion", {})
    return "\n".join(
        [
            "# Edge Forward Promotion Gate",
            "",
            f"Generated: `{report.get('generated_at')}`",
            "",
            "## Boundary",
            "",
            "- Evidence gate only.",
            "- Does not create paper-entry intents.",
            "- Does not send exchange orders.",
            "- A pass here would allow paper-design review only, not live trading.",
            "",
            "## Decision",
            "",
            f"- Decision: `{report.get('decision')}`.",
            f"- Next action: `{report.get('next_action')}`.",
            f"- Observer allowed: `{promotion.get('observer_allowed')}`.",
            f"- Paper-design review allowed: `{promotion.get('paper_design_review_allowed')}`.",
            f"- Paper execution allowed: `{promotion.get('paper_execution_allowed')}`.",
            f"- Live execution allowed: `{promotion.get('live_execution_allowed')}`.",
            "",
            "## Candidate",
            "",
            f"- Strategy: `{candidate.get('strategy_id')}`.",
            f"- Edge classification / score: `{candidate.get('edge_classification')}` / `{candidate.get('evidence_score')}`.",
            f"- Filter: `{candidate.get('filter_mode')}`.",
            f"- Side / TF / trigger / RR: `{candidate.get('side')}` / `{candidate.get('interval')}` / `{candidate.get('trigger')}` / `{candidate.get('rr')}`.",
            f"- Full expectancy: `{candidate.get('full_expectancy_r')}` R over `{candidate.get('full_trades')}` trades.",
            f"- Holdout expectancy: `{candidate.get('holdout_expectancy_r')}` R over `{candidate.get('holdout_trades')}` trades.",
            f"- Stable folds: `{candidate.get('stable_folds')}`.",
            f"- Cost +10bps expectancy: `{candidate.get('cost10_expectancy_r')}` R.",
            "",
            "## Forward Observer Evidence",
            "",
            f"- Classification: `{scoreboard.get('classification')}`.",
            f"- Observer signals: `{scoreboard.get('observer_signal_events')}`.",
            f"- Resolved outcomes: `{scoreboard.get('resolved')}`.",
            f"- Winrate / breakeven: `{scoreboard.get('winrate_pct')}` / `{scoreboard.get('breakeven_winrate_pct')}`.",
            f"- Expectancy: `{scoreboard.get('expectancy_r')}` R.",
            f"- Max drawdown: `{scoreboard.get('max_drawdown_r')}` R.",
            "",
            "## Operational Evidence",
            "",
            f"- Pending-watch classification: `{operational.get('pending_watch_classification')}`.",
            f"- Context / trigger: `{operational.get('pending_watch_context_ok')}` / `{operational.get('pending_watch_trigger_ok')}`.",
            f"- Distance to trigger: `{operational.get('pending_watch_distance_atr')}` ATR.",
            f"- Telegram notify decision: `{operational.get('telegram_notify_decision')}`.",
            f"- Telegram response ok: `{operational.get('telegram_response_ok')}`.",
            "",
            "## Research Gates",
            "",
            *render_gate_table(report.get("gates", {}).get("research", [])),
            "",
            "## Observer Gates",
            "",
            *render_gate_table(report.get("gates", {}).get("observer", [])),
            "",
            "## Operational Gates",
            "",
            *render_gate_table(report.get("gates", {}).get("operational", [])),
            "",
        ]
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Gate exported edge candidate before paper-design review")
    parser.add_argument("--edge-export", default="docs/EDGE_FORWARD_CANDIDATE_REFINER_2026-06-18.json")
    parser.add_argument("--observer", default="docs/EDGE_FORWARD_RANGE_OBSERVER_2026-06-18.json")
    parser.add_argument("--scoreboard", default="docs/EDGE_FORWARD_RANGE_SCOREBOARD_2026-06-18.json")
    parser.add_argument("--pending-watch", default="docs/EDGE_FORWARD_PENDING_WATCH_2026-06-18.json")
    parser.add_argument("--pending-watch-notify", default="docs/EDGE_FORWARD_PENDING_WATCH_TELEGRAM_NOTIFY_2026-06-18.json")
    parser.add_argument("--alert-drill", default="docs/EDGE_FORWARD_SIGNAL_ALERT_DRILL_2026-06-23.json")
    parser.add_argument("--min-full-trades", type=int, default=60)
    parser.add_argument("--min-history-expectancy-r", type=float, default=0.05)
    parser.add_argument("--min-holdout-trades", type=int, default=20)
    parser.add_argument("--min-holdout-expectancy-r", type=float, default=0.05)
    parser.add_argument("--min-stable-folds", type=int, default=5)
    parser.add_argument("--min-segment-positive-ratio", type=float, default=0.6)
    parser.add_argument("--min-cost10-expectancy-r", type=float, default=0.0)
    parser.add_argument("--min-observer-signals", type=int, default=30)
    parser.add_argument("--min-resolved", type=int, default=30)
    parser.add_argument("--min-forward-expectancy-r", type=float, default=0.05)
    parser.add_argument("--max-drawdown-r", type=float, default=8.0)
    parser.add_argument("--out-prefix", default="docs/EDGE_FORWARD_PROMOTION_GATE_2026-06-18")
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report.get("decision"),
                "observer_allowed": report.get("promotion", {}).get("observer_allowed"),
                "paper_design_review_allowed": report.get("promotion", {}).get("paper_design_review_allowed"),
                "observer_signal_events": report.get("scoreboard", {}).get("observer_signal_events"),
                "resolved": report.get("scoreboard", {}).get("resolved"),
                "json": rel_path(out_prefix.with_suffix(".json")),
                "md": rel_path(out_prefix.with_suffix(".md")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

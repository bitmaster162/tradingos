#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIAGNOSTIC = ROOT / "docs" / "CROWD_FADE_POSITIONING_DIAGNOSTIC_2026-06-19.json"
DEFAULT_CANDIDATE_LOCK = ROOT / "configs" / "CROWD_FADE_FORWARD_LOCK.json"
DEFAULT_OBSERVER = ROOT / "docs" / "CROWD_FADE_POSITIONING_SHADOW_OBSERVER_2026-06-19.json"
DEFAULT_SCOREBOARD = ROOT / "docs" / "CROWD_FADE_POSITIONING_SHADOW_SCOREBOARD_2026-06-19.json"
DEFAULT_NOTIFY = ROOT / "docs" / "CROWD_FADE_POSITIONING_TELEGRAM_NOTIFY_2026-06-19.json"
DEFAULT_DRILL = ROOT / "docs" / "CROWD_FADE_POSITIONING_TELEGRAM_DRILL_2026-06-19.json"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "CROWD_FADE_POSITIONING_PROMOTION_GATE_2026-06-19"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str | Path) -> Path:
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


def gate(name: str, passed: bool, actual: Any, required: Any, severity: str = "hard") -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "actual": actual,
        "required": required,
        "severity": severity,
    }


def gates_pass(items: list[dict[str, Any]], *, severity: str = "hard") -> bool:
    return all(item.get("passed") for item in items if item.get("severity") == severity)


def nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def render_gate_table(items: list[dict[str, Any]]) -> list[str]:
    lines = ["| gate | pass | actual | required |", "|---|---:|---|---|"]
    for item in items:
        lines.append(f"| {item.get('name')} | `{item.get('passed')}` | `{item.get('actual')}` | `{item.get('required')}` |")
    return lines


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    diagnostic_path = resolve_path(args.diagnostic)
    candidate_lock_path = resolve_path(args.candidate_lock)
    observer_path = resolve_path(args.observer)
    scoreboard_path = resolve_path(args.scoreboard)
    notify_path = resolve_path(args.notify)
    drill_path = resolve_path(args.drill)

    diagnostic = read_json(diagnostic_path)
    candidate_lock = read_json(candidate_lock_path)
    observer = read_json(observer_path)
    scoreboard = read_json(scoreboard_path)
    notify = read_json(notify_path)
    drill = read_json(drill_path)

    locked_candidate = candidate_lock.get("candidate") if isinstance(candidate_lock.get("candidate"), dict) else {}
    locked_strategy_id = locked_candidate.get("strategy_id")
    candidate = diagnostic.get("locked_candidate_result") if isinstance(diagnostic.get("locked_candidate_result"), dict) else {}
    candidate_summary = candidate.get("summary") if isinstance(candidate.get("summary"), dict) else {}
    candidate_holdout = candidate.get("holdout_summary") if isinstance(candidate.get("holdout_summary"), dict) else {}
    score_summary = scoreboard.get("summary") if isinstance(scoreboard.get("summary"), dict) else {}
    observer_latest = observer.get("latest") if isinstance(observer.get("latest"), dict) else {}
    drill_notify = nested(drill, "notify_result", "notify_report")
    if not isinstance(drill_notify, dict):
        drill_notify = {}

    candidate_trades = safe_int(candidate_summary.get("trades"))
    candidate_expectancy = safe_float(candidate_summary.get("expectancy_r"))
    candidate_holdout_trades = safe_int(candidate_holdout.get("trades"))
    candidate_holdout_expectancy = safe_float(candidate_holdout.get("expectancy_r"))
    stable_folds = safe_int(candidate.get("stable_folds"))

    observer_signal_events = safe_int(score_summary.get("observer_signal_events"))
    resolved = safe_int(score_summary.get("resolved"))
    forward_expectancy = safe_float(score_summary.get("expectancy_r"))
    forward_winrate = safe_float(score_summary.get("winrate_pct"))
    breakeven = safe_float(score_summary.get("breakeven_winrate_pct"))
    max_drawdown = safe_float(score_summary.get("max_drawdown_r"))

    research_gates = [
        gate("diagnostic_exists", bool(diagnostic) and not diagnostic.get("_read_error"), rel_path(diagnostic_path), "readable JSON"),
        gate("candidate_lock_exists", bool(locked_candidate), rel_path(candidate_lock_path), "locked candidate config"),
        gate("candidate_lock_enabled", candidate_lock.get("enabled") is True, candidate_lock.get("enabled"), True),
        gate("locked_candidate_result_found", bool(candidate), candidate.get("strategy_id"), locked_strategy_id),
        gate("locked_candidate_identity", candidate.get("strategy_id") == locked_strategy_id, candidate.get("strategy_id"), locked_strategy_id),
        gate(
            "candidate_classification",
            candidate.get("classification") == "candidate_watchlist_limited_history",
            candidate.get("classification"),
            "candidate_watchlist_limited_history",
        ),
        gate("candidate_min_trades", candidate_trades >= args.min_history_trades, candidate_trades, args.min_history_trades),
        gate(
            "candidate_expectancy_positive",
            candidate_expectancy is not None and candidate_expectancy >= args.min_history_expectancy_r,
            candidate_expectancy,
            args.min_history_expectancy_r,
        ),
        gate("candidate_min_holdout_trades", candidate_holdout_trades >= args.min_holdout_trades, candidate_holdout_trades, args.min_holdout_trades),
        gate(
            "candidate_holdout_positive",
            candidate_holdout_expectancy is not None and candidate_holdout_expectancy >= args.min_holdout_expectancy_r,
            candidate_holdout_expectancy,
            args.min_holdout_expectancy_r,
        ),
        gate("candidate_stable_folds", stable_folds >= args.min_stable_folds, stable_folds, args.min_stable_folds),
    ]

    operational_gates = [
        gate("observer_exists", bool(observer) and not observer.get("_read_error"), rel_path(observer_path), "readable JSON"),
        gate("observer_matches_candidate_lock", observer.get("strategy_id") == locked_strategy_id, observer.get("strategy_id"), locked_strategy_id),
        gate("observer_no_trade_permission", observer.get("can_trade") is False, observer.get("can_trade"), False),
        gate("scoreboard_exists", bool(scoreboard) and not scoreboard.get("_read_error"), rel_path(scoreboard_path), "readable JSON"),
        gate("scoreboard_no_trade_permission", scoreboard.get("can_trade") is False, scoreboard.get("can_trade"), False),
        gate("notify_exists", bool(notify) and not notify.get("_read_error"), rel_path(notify_path), "readable JSON"),
        gate("notify_no_trade_permission", notify.get("can_trade") is False, notify.get("can_trade"), False),
        gate("notify_not_error", notify.get("decision") not in {"telegram_api_error", "telegram_send_error"}, notify.get("decision"), "not telegram error"),
        gate("telegram_drill_exists", bool(drill) and not drill.get("_read_error"), rel_path(drill_path), "readable JSON"),
        gate("telegram_drill_passed", drill.get("decision") == "crowd_fade_telegram_drill_passed", drill.get("decision"), "crowd_fade_telegram_drill_passed"),
        gate("telegram_drill_notify_ready", drill_notify.get("decision") in {"dry_run_ready", "sent"}, drill_notify.get("decision"), "dry_run_ready or sent"),
    ]

    forward_gates = [
        gate("forward_observer_signal_events", observer_signal_events >= args.min_forward_signals, observer_signal_events, args.min_forward_signals),
        gate("forward_resolved_outcomes", resolved >= args.min_resolved, resolved, args.min_resolved),
        gate(
            "forward_expectancy",
            forward_expectancy is not None and forward_expectancy >= args.min_forward_expectancy_r,
            forward_expectancy,
            args.min_forward_expectancy_r,
        ),
        gate(
            "forward_winrate_vs_breakeven",
            forward_winrate is not None and breakeven is not None and forward_winrate >= breakeven,
            f"{forward_winrate} vs {breakeven}",
            "winrate >= breakeven",
        ),
        gate("forward_drawdown_cap", max_drawdown is not None and max_drawdown >= -abs(args.max_drawdown_r), max_drawdown, f">= -{abs(args.max_drawdown_r)}R"),
    ]

    research_ok = gates_pass(research_gates)
    operational_ok = gates_pass(operational_gates)
    forward_ok = gates_pass(forward_gates)

    if not research_ok:
        decision = "blocked_crowd_fade_research_gate_failed"
        next_action = "re-run diagnostic or refine candidate before relying on observer"
    elif not operational_ok:
        decision = "blocked_crowd_fade_operational_gate_failed"
        next_action = "fix observer/scoreboard/Telegram drill path before forward proof"
    elif not forward_ok:
        decision = "blocked_waiting_crowd_fade_forward_outcomes"
        next_action = "keep refresh-pack running until enough real observer signals resolve"
    else:
        decision = "crowd_fade_candidate_for_paper_design_review_only"
        next_action = "manual review required; design separate paper-entry gate before any execution"

    promotion = {
        "watch_observer_allowed": research_ok and operational_ok,
        "paper_design_review_allowed": research_ok and operational_ok and forward_ok,
        "paper_execution_allowed": False,
        "live_execution_allowed": False,
        "manual_review_required": True,
        "can_trade": False,
    }

    return {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "crowd_fade_promotion_gate_evidence_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_exchange_credentials": False,
            "creates_paper_entry_intents": False,
            "changes_active_strategy": False,
        },
        "inputs": {
            "diagnostic": rel_path(diagnostic_path),
            "candidate_lock": rel_path(candidate_lock_path),
            "observer": rel_path(observer_path),
            "scoreboard": rel_path(scoreboard_path),
            "notify": rel_path(notify_path),
            "drill": rel_path(drill_path),
        },
        "thresholds": {
            "min_history_trades": args.min_history_trades,
            "min_history_expectancy_r": args.min_history_expectancy_r,
            "min_holdout_trades": args.min_holdout_trades,
            "min_holdout_expectancy_r": args.min_holdout_expectancy_r,
            "min_stable_folds": args.min_stable_folds,
            "min_forward_signals": args.min_forward_signals,
            "min_resolved": args.min_resolved,
            "min_forward_expectancy_r": args.min_forward_expectancy_r,
            "max_drawdown_r": args.max_drawdown_r,
        },
        "candidate": {
            "strategy_id": candidate.get("strategy_id"),
            "classification": candidate.get("classification"),
            "interval": candidate.get("interval"),
            "side_mode": candidate.get("side_mode"),
            "ratio_field": candidate.get("ratio_field"),
            "rr": candidate.get("rr"),
            "trades": candidate_trades,
            "winrate_pct": candidate_summary.get("winrate_pct"),
            "expectancy_r": candidate_expectancy,
            "holdout_trades": candidate_holdout_trades,
            "holdout_expectancy_r": candidate_holdout_expectancy,
            "stable_folds": stable_folds,
        },
        "observer": {
            "status": observer_latest.get("status"),
            "signal_found": observer_latest.get("signal_found"),
            "signal_time": observer_latest.get("signal_time"),
            "side_hint": observer_latest.get("side_hint"),
        },
        "scoreboard": {
            "classification": score_summary.get("classification"),
            "observer_signal_events": observer_signal_events,
            "resolved": resolved,
            "winrate_pct": forward_winrate,
            "breakeven_winrate_pct": breakeven,
            "expectancy_r": forward_expectancy,
            "max_drawdown_r": max_drawdown,
        },
        "operational": {
            "notify_decision": notify.get("decision"),
            "notify_signal_found": notify.get("signal_found"),
            "telegram_response_ok": notify.get("telegram_response_ok"),
            "drill_decision": drill.get("decision"),
            "drill_notify_decision": drill_notify.get("decision"),
        },
        "gates": {
            "research": research_gates,
            "operational": operational_gates,
            "forward": forward_gates,
        },
        "promotion": promotion,
        "decision": decision,
        "next_action": next_action,
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    candidate = report.get("candidate", {})
    scoreboard = report.get("scoreboard", {})
    promotion = report.get("promotion", {})
    lines = [
        "# Crowd-Fade Positioning Promotion Gate",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Next action: `{report.get('next_action')}`",
        f"- Can trade: `{report.get('can_trade')}`",
        "",
        "## Promotion State",
        "",
        f"- Watch observer allowed: `{promotion.get('watch_observer_allowed')}`",
        f"- Paper design review allowed: `{promotion.get('paper_design_review_allowed')}`",
        f"- Paper execution allowed: `{promotion.get('paper_execution_allowed')}`",
        f"- Live execution allowed: `{promotion.get('live_execution_allowed')}`",
        "",
        "## Candidate",
        "",
        f"- Strategy: `{candidate.get('strategy_id')}`",
        f"- Classification: `{candidate.get('classification')}`",
        f"- Trades: `{candidate.get('trades')}`",
        f"- Expectancy R: `{candidate.get('expectancy_r')}`",
        f"- Holdout trades: `{candidate.get('holdout_trades')}`",
        f"- Holdout expectancy R: `{candidate.get('holdout_expectancy_r')}`",
        f"- Stable folds: `{candidate.get('stable_folds')}`",
        "",
        "## Forward Scoreboard",
        "",
        f"- Classification: `{scoreboard.get('classification')}`",
        f"- Signals: `{scoreboard.get('observer_signal_events')}`",
        f"- Resolved: `{scoreboard.get('resolved')}`",
        f"- Expectancy R: `{scoreboard.get('expectancy_r')}`",
        f"- Winrate / breakeven: `{scoreboard.get('winrate_pct')}` / `{scoreboard.get('breakeven_winrate_pct')}`",
        "",
        "## Research Gates",
        "",
        *render_gate_table(report["gates"]["research"]),
        "",
        "## Operational Gates",
        "",
        *render_gate_table(report["gates"]["operational"]),
        "",
        "## Forward Gates",
        "",
        *render_gate_table(report["gates"]["forward"]),
        "",
        "## Boundary",
        "",
        "- Evidence gate only.",
        "- Passing this gate would allow manual paper-design review only.",
        "- It never enables paper execution, live execution or exchange orders.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Promotion gate for crowd-fade positioning observer.")
    parser.add_argument("--diagnostic", default=str(DEFAULT_DIAGNOSTIC))
    parser.add_argument("--candidate-lock", default=str(DEFAULT_CANDIDATE_LOCK))
    parser.add_argument("--observer", default=str(DEFAULT_OBSERVER))
    parser.add_argument("--scoreboard", default=str(DEFAULT_SCOREBOARD))
    parser.add_argument("--notify", default=str(DEFAULT_NOTIFY))
    parser.add_argument("--drill", default=str(DEFAULT_DRILL))
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    parser.add_argument("--min-history-trades", type=int, default=50)
    parser.add_argument("--min-history-expectancy-r", type=float, default=0.10)
    parser.add_argument("--min-holdout-trades", type=int, default=12)
    parser.add_argument("--min-holdout-expectancy-r", type=float, default=0.0)
    parser.add_argument("--min-stable-folds", type=int, default=2)
    parser.add_argument("--min-forward-signals", type=int, default=20)
    parser.add_argument("--min-resolved", type=int, default=20)
    parser.add_argument("--min-forward-expectancy-r", type=float, default=0.10)
    parser.add_argument("--max-drawdown-r", type=float, default=6.0)
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "watch_observer_allowed": report["promotion"]["watch_observer_allowed"],
                "paper_design_review_allowed": report["promotion"]["paper_design_review_allowed"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

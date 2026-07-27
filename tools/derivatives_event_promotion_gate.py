#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
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
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


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


def gate(name: str, passed: bool, actual: Any, required: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "actual": actual, "required": required}


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Derivatives Event Promotion Gate",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Decision",
        "",
        f"- `{report.get('decision')}`.",
        f"- Next: `{report.get('next_action')}`.",
        "",
        "## Gates",
        "",
        "| gate | pass | actual | required |",
        "|---|---:|---|---|",
    ]
    for item in report.get("gates", []):
        lines.append(f"| {item.get('name')} | `{item.get('passed')}` | `{item.get('actual')}` | `{item.get('required')}` |")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            f"- Paper execution allowed: `{report.get('promotion', {}).get('paper_execution_allowed')}`.",
            f"- Live execution allowed: `{report.get('promotion', {}).get('live_execution_allowed')}`.",
            f"- Can trade: `{report.get('can_trade')}`.",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    miner_path = resolve_path(args.miner_report)
    observer_path = resolve_path(args.observer)
    scoreboard_path = resolve_path(args.scoreboard)
    miner = read_json(miner_path)
    observer = read_json(observer_path)
    scoreboard = read_json(scoreboard_path)
    summary = scoreboard.get("summary") if isinstance(scoreboard.get("summary"), dict) else {}
    selected = miner.get("selected") if isinstance(miner.get("selected"), dict) else {}

    oos = selected.get("oos") if isinstance(selected.get("oos"), dict) else {}
    oos_summary = oos.get("summary") if isinstance(oos.get("summary"), dict) else {}
    oos_expectancy = safe_float(oos_summary.get("expectancy_r"))
    oos_trades = safe_int(oos_summary.get("trades"))
    observer_decision = observer.get("decision")
    resolved = safe_int(summary.get("resolved"))
    signals = safe_int(summary.get("observer_signal_events"))
    expectancy = safe_float(summary.get("expectancy_r"))
    max_dd = safe_float(summary.get("max_drawdown_r"))

    gates = [
        gate("miner_oos_pass", str(miner.get("decision") or "").startswith("oos_pass"), miner.get("decision"), "oos_pass*"),
        gate("selected_candidate_exists", bool(selected.get("strategy_id")), selected.get("strategy_id"), "strategy_id"),
        gate("historical_oos_min_trades", oos_trades >= args.min_oos_trades, oos_trades, args.min_oos_trades),
        gate("historical_oos_expectancy_positive", oos_expectancy is not None and oos_expectancy > 0, oos_expectancy, "> 0R"),
        gate("observer_report_exists", bool(observer), rel_path(observer_path), "readable JSON"),
        gate("observer_not_blocked", not str(observer_decision or "").startswith("blocked_"), observer_decision, "not blocked_*"),
        gate("scoreboard_report_exists", bool(scoreboard), rel_path(scoreboard_path), "readable JSON"),
        gate("forward_signal_count", signals >= args.min_forward_signals, signals, args.min_forward_signals),
        gate("forward_resolved_count", resolved >= args.min_resolved, resolved, args.min_resolved),
        gate("forward_expectancy", expectancy is not None and expectancy >= args.min_expectancy_r, expectancy, args.min_expectancy_r),
        gate("forward_drawdown_cap", max_dd is not None and max_dd >= -abs(args.max_drawdown_r), max_dd, f">= -{abs(args.max_drawdown_r)}R"),
    ]
    passed = all(item["passed"] for item in gates)
    if passed:
        decision = "candidate_for_manual_paper_design_review_only"
        next_action = "manual review may design a separate paper route; this gate still grants no execution permission"
    else:
        decision = "blocked_waiting_derivatives_event_forward_evidence"
        next_action = "keep observer running until enough resolved forward outcomes exist"
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "inputs": {
            "miner_report": rel_path(miner_path),
            "observer": rel_path(observer_path),
            "scoreboard": rel_path(scoreboard_path),
        },
        "candidate": {
            "strategy_id": selected.get("strategy_id"),
            "family": selected.get("config", {}).get("family") if isinstance(selected.get("config"), dict) else None,
            "side": selected.get("config", {}).get("side") if isinstance(selected.get("config"), dict) else None,
            "interval": selected.get("config", {}).get("interval") if isinstance(selected.get("config"), dict) else None,
        },
        "gates": gates,
        "promotion": {
            "observer_allowed": True,
            "paper_design_review_allowed": passed,
            "paper_execution_allowed": False,
            "live_execution_allowed": False,
            "manual_review_required": True,
            "can_trade": False,
        },
        "decision": decision,
        "next_action": next_action,
        "runtime_boundary": {
            "observer_only": True,
            "paper_allowed": False,
            "live_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Promotion gate for derivatives-event observer evidence")
    parser.add_argument("--miner-report", default="docs/DERIVATIVES_EVENT_EDGE_MINER_REGIME_2026-06-26.json")
    parser.add_argument("--observer", default="docs/DERIVATIVES_EVENT_FORWARD_OBSERVER_2026-06-26.json")
    parser.add_argument("--scoreboard", default="docs/DERIVATIVES_EVENT_FORWARD_SCOREBOARD_2026-06-26.json")
    parser.add_argument("--min-oos-trades", type=int, default=10)
    parser.add_argument("--min-forward-signals", type=int, default=30)
    parser.add_argument("--min-resolved", type=int, default=30)
    parser.add_argument("--min-expectancy-r", type=float, default=0.03)
    parser.add_argument("--max-drawdown-r", type=float, default=12.0)
    parser.add_argument("--out-prefix", default="docs/DERIVATIVES_EVENT_PROMOTION_GATE_2026-06-26")
    args = parser.parse_args()
    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "paper_design_review_allowed": report["promotion"]["paper_design_review_allowed"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

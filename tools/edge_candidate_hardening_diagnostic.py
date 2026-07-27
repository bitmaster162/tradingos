#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EDGE_REFINER = ROOT / "docs" / "EDGE_FORWARD_CANDIDATE_REFINER_2026-06-18.json"
DEFAULT_SOURCE_REFINER = ROOT / "docs" / "RANGE_SWEEP_RECLAIM_REFINER_2026-06-18.json"
DEFAULT_EDGE_REGISTRY = ROOT / "docs" / "EDGE_REGISTRY_2026-06-18.json"
DEFAULT_OBSERVER = ROOT / "docs" / "EDGE_FORWARD_RANGE_OBSERVER_2026-06-18.json"
DEFAULT_SCOREBOARD = ROOT / "docs" / "EDGE_FORWARD_RANGE_SCOREBOARD_2026-06-18.json"
DEFAULT_PENDING = ROOT / "docs" / "EDGE_FORWARD_PENDING_WATCH_2026-06-18.json"
DEFAULT_GATE = ROOT / "docs" / "EDGE_FORWARD_PROMOTION_GATE_2026-06-18.json"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "EDGE_CANDIDATE_HARDENING_DIAGNOSTIC_2026-06-19"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def summary(row: dict[str, Any], scope: str) -> dict[str, Any]:
    block = row.get(scope) if isinstance(row.get(scope), dict) else {}
    return block.get("summary") if isinstance(block.get("summary"), dict) else {}


def cost10_expectancy(row: dict[str, Any]) -> float | None:
    for item in row.get("cost_stress", []):
        if not isinstance(item, dict):
            continue
        if safe_float(item.get("extra_bps_per_side")) == 10.0:
            return safe_float(summary(item, "summary") or item.get("summary", {}))
    # The normal cost-stress row shape is {"extra_bps_per_side": 10, "summary": {...}}.
    for item in row.get("cost_stress", []):
        if isinstance(item, dict) and safe_float(item.get("extra_bps_per_side")) == 10.0:
            s = item.get("summary") if isinstance(item.get("summary"), dict) else {}
            return safe_float(s.get("expectancy_r"))
    return None


def cost10_expectancy_fixed(row: dict[str, Any]) -> float | None:
    for item in row.get("cost_stress", []):
        if not isinstance(item, dict):
            continue
        if safe_float(item.get("extra_bps_per_side")) == 10.0:
            item_summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
            return safe_float(item_summary.get("expectancy_r"))
    return None


def fold_stats(row: dict[str, Any]) -> dict[str, Any]:
    full = row.get("full") if isinstance(row.get("full"), dict) else {}
    folds = full.get("folds") if isinstance(full.get("folds"), list) else []
    negative = [fold for fold in folds if safe_float(fold.get("expectancy_r"), -999.0) < 0]
    weakest = min((safe_float(fold.get("expectancy_r"), 999.0) for fold in folds), default=None)
    return {
        "folds": len(folds),
        "stable_folds": safe_int(full.get("stable_folds")),
        "negative_folds": len(negative),
        "weakest_fold_expectancy_r": weakest,
    }


def parse_base_id(strategy_id: str) -> dict[str, Any]:
    pattern = re.compile(
        r"^range_(?P<tf>[^_]+)_(?P<side>long|short)_(?P<trigger>.+)_lb(?P<lookback>\d+)_edge(?P<edge>[0-9.]+)_rr(?P<stop>[0-9.]+)x(?P<take>[0-9.]+)_h(?P<hold>\d+)"
    )
    match = pattern.match(strategy_id)
    if not match:
        return {}
    data = match.groupdict()
    return {
        "tf": data["tf"],
        "side": data["side"].upper(),
        "trigger": data["trigger"],
        "lookback": safe_int(data["lookback"]),
        "edge_pct": safe_float(data["edge"]),
        "rr": f"{data['stop']}:{data['take']}",
        "max_hold_bars": safe_int(data["hold"]),
        "shape_key": f"{data['tf']}|{data['side']}|{data['trigger']}|lb{data['lookback']}|edge{data['edge']}",
    }


def hard_score(row: dict[str, Any]) -> int:
    full = summary(row, "full")
    holdout = summary(row, "holdout")
    cost10 = cost10_expectancy_fixed(row)
    score = 0
    score += min(safe_int(full.get("trades")) // 10, 12)
    score += min(safe_int(holdout.get("trades")) * 2, 40)
    score += 12 if safe_float(full.get("expectancy_r"), -999.0) and safe_float(full.get("expectancy_r"), -999.0) > 0 else 0
    score += 12 if safe_float(holdout.get("expectancy_r"), -999.0) and safe_float(holdout.get("expectancy_r"), -999.0) > 0 else 0
    score += 10 if safe_float(holdout.get("winrate_pct"), 0.0) and safe_float(holdout.get("winrate_pct"), 0.0) >= 50.0 else 0
    score += 10 if safe_int(row.get("full", {}).get("stable_folds")) >= 5 else 0
    score += 8 if safe_float(row.get("segment_positive_ratio"), 0.0) and safe_float(row.get("segment_positive_ratio"), 0.0) >= 0.66 else 0
    score += 8 if safe_float(row.get("worst_segment_expectancy_r"), -999.0) and safe_float(row.get("worst_segment_expectancy_r"), -999.0) >= -0.25 else 0
    score += 8 if cost10 is not None and cost10 > 0 else 0
    if safe_int(holdout.get("trades")) < 20:
        score -= 25
    if safe_float(row.get("worst_segment_expectancy_r"), -999.0) is not None and safe_float(row.get("worst_segment_expectancy_r"), -999.0) < -0.4:
        score -= 15
    return score


def compact_row(row: dict[str, Any]) -> dict[str, Any]:
    full = summary(row, "full")
    holdout = summary(row, "holdout")
    return {
        "strategy_id": row.get("strategy_id"),
        "base_strategy_id": row.get("base_strategy_id"),
        "filter_mode": row.get("filter_mode"),
        "filters": row.get("filters"),
        "interval": row.get("interval"),
        "side": row.get("side"),
        "trigger": row.get("trigger"),
        "rr": row.get("rr"),
        "max_hold_bars": row.get("max_hold_bars"),
        "signals": row.get("signals"),
        "verdict": row.get("verdict"),
        "full_trades": full.get("trades"),
        "full_winrate_pct": full.get("winrate_pct"),
        "full_expectancy_r": full.get("expectancy_r"),
        "full_max_drawdown_r": full.get("max_drawdown_r"),
        "holdout_trades": holdout.get("trades"),
        "holdout_winrate_pct": holdout.get("winrate_pct"),
        "holdout_expectancy_r": holdout.get("expectancy_r"),
        "stable_folds": row.get("full", {}).get("stable_folds") if isinstance(row.get("full"), dict) else None,
        "segment_positive_ratio": row.get("segment_positive_ratio"),
        "worst_segment_expectancy_r": row.get("worst_segment_expectancy_r"),
        "cost10_expectancy_r": cost10_expectancy_fixed(row),
        "hard_score": hard_score(row),
    }


def load_forward_state(observer: Any, scoreboard: Any, pending: Any, gate: Any) -> dict[str, Any]:
    latest = observer.get("latest_result") if isinstance(observer, dict) and isinstance(observer.get("latest_result"), dict) else {}
    score_summary = scoreboard.get("summary") if isinstance(scoreboard, dict) and isinstance(scoreboard.get("summary"), dict) else {}
    pending_latest = pending.get("latest") if isinstance(pending, dict) and isinstance(pending.get("latest"), dict) else {}
    trigger = pending_latest.get("trigger") if isinstance(pending_latest.get("trigger"), dict) else {}
    promotion = gate.get("promotion") if isinstance(gate, dict) and isinstance(gate.get("promotion"), dict) else {}
    return {
        "observer_status": latest.get("status"),
        "observer_strategy_id": latest.get("strategy_id"),
        "latest_closed_bar_ts": latest.get("latest_closed_bar_ts"),
        "raw_signals_on_latest_bar": latest.get("raw_signals_on_latest_bar"),
        "refined_signals_on_latest_bar": latest.get("refined_signals_on_latest_bar"),
        "data_degraded": latest.get("data_degraded"),
        "scoreboard_classification": score_summary.get("classification"),
        "observer_signal_events": score_summary.get("observer_signal_events"),
        "resolved": score_summary.get("resolved"),
        "expectancy_r": score_summary.get("expectancy_r"),
        "pending_classification": pending.get("classification") if isinstance(pending, dict) else None,
        "pending_next_action": pending.get("next_action") if isinstance(pending, dict) else None,
        "context_ok": pending_latest.get("context_ok"),
        "trigger_ok": pending_latest.get("trigger_ok"),
        "refined_ready": pending_latest.get("refined_ready"),
        "distance_to_trigger_atr": trigger.get("distance_to_trigger_atr"),
        "distance_to_trigger_pct": trigger.get("distance_to_trigger_pct"),
        "trigger_progress_pct": trigger.get("trigger_progress_pct"),
        "promotion_decision": gate.get("decision") if isinstance(gate, dict) else None,
        "observer_allowed": promotion.get("observer_allowed"),
        "paper_design_review_allowed": promotion.get("paper_design_review_allowed"),
        "paper_execution_allowed": promotion.get("paper_execution_allowed"),
        "live_execution_allowed": promotion.get("live_execution_allowed"),
    }


def render_md(report: dict[str, Any]) -> str:
    selected = report["selected_candidate"]
    forward = report["forward_state"]
    lines = [
        "# Edge Candidate Hardening Diagnostic",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Research/observer diagnostic only.",
        "- No paper entry intents, no live orders, no credential usage.",
        "- `can_trade=false` remains enforced.",
        "",
        "## Decision",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Next action: `{report['next_action']}`",
        f"- Can trade: `{report['can_trade']}`",
        "",
        "## Selected Candidate",
        "",
        f"- Strategy: `{selected.get('strategy_id')}`",
        f"- Base: `{selected.get('base_strategy_id')}`",
        f"- Filter mode: `{selected.get('filter_mode')}`",
        f"- Filters: `{', '.join(selected.get('filters') or [])}`",
        f"- TF/side/trigger/RR/hold: `{selected.get('interval')}` / `{selected.get('side')}` / `{selected.get('trigger')}` / `{selected.get('rr')}` / `{selected.get('max_hold_bars')}`",
        "",
        "| Scope | Trades | WR % | Exp R | Max DD R |",
        "|---|---:|---:|---:|---:|",
        f"| Full | `{selected.get('full_trades')}` | `{selected.get('full_winrate_pct')}` | `{selected.get('full_expectancy_r')}` | `{selected.get('full_max_drawdown_r')}` |",
        f"| Holdout | `{selected.get('holdout_trades')}` | `{selected.get('holdout_winrate_pct')}` | `{selected.get('holdout_expectancy_r')}` |  |",
        "",
        f"- Stable folds: `{selected.get('stable_folds')}`",
        f"- Segment positive ratio: `{selected.get('segment_positive_ratio')}`",
        f"- Worst segment expectancy: `{selected.get('worst_segment_expectancy_r')}`",
        f"- Cost +10 bps expectancy: `{selected.get('cost10_expectancy_r')}`",
        "",
        "## Forward State",
        "",
        f"- Observer status: `{forward.get('observer_status')}`",
        f"- Latest closed bar: `{forward.get('latest_closed_bar_ts')}`",
        f"- Raw/refined signals on latest bar: `{forward.get('raw_signals_on_latest_bar')}` / `{forward.get('refined_signals_on_latest_bar')}`",
        f"- Pending watch: `{forward.get('pending_classification')}` / `{forward.get('pending_next_action')}`",
        f"- Distance to trigger: `{forward.get('distance_to_trigger_atr')}` ATR / `{forward.get('distance_to_trigger_pct')}`%",
        f"- Promotion: `{forward.get('promotion_decision')}`",
        f"- Paper design / paper execution / live: `{forward.get('paper_design_review_allowed')}` / `{forward.get('paper_execution_allowed')}` / `{forward.get('live_execution_allowed')}`",
        "",
        "## Same Base Filter Ablation",
        "",
        "| Rank | Filter | Full Trades | Full Exp | Holdout Trades | Holdout Exp | Stable | Worst Seg | Cost+10 | Score | Verdict |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for idx, row in enumerate(report["same_base_variants"][:15], start=1):
        lines.append(
            f"| {idx} | `{row.get('filter_mode')}` | `{row.get('full_trades')}` | `{row.get('full_expectancy_r')}` | "
            f"`{row.get('holdout_trades')}` | `{row.get('holdout_expectancy_r')}` | `{row.get('stable_folds')}` | "
            f"`{row.get('worst_segment_expectancy_r')}` | `{row.get('cost10_expectancy_r')}` | `{row.get('hard_score')}` | `{row.get('verdict')}` |"
        )
    lines.extend(
        [
            "",
            "## Same Shape Alternatives",
            "",
            "| Rank | Strategy | Filter | RR | Hold | Full Exp | Holdout Trades | Holdout Exp | Worst Seg | Cost+10 | Score |",
            "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for idx, row in enumerate(report["same_shape_alternatives"][:20], start=1):
        lines.append(
            f"| {idx} | `{row.get('strategy_id')}` | `{row.get('filter_mode')}` | `{row.get('rr')}` | `{row.get('max_hold_bars')}` | "
            f"`{row.get('full_expectancy_r')}` | `{row.get('holdout_trades')}` | `{row.get('holdout_expectancy_r')}` | "
            f"`{row.get('worst_segment_expectancy_r')}` | `{row.get('cost10_expectancy_r')}` | `{row.get('hard_score')}` |"
        )
    lines.extend(["", "## Hardening Notes", ""])
    for note in report["hardening_notes"]:
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Diagnose and harden the current observer-only edge candidate.")
    parser.add_argument("--edge-refiner", default=str(DEFAULT_EDGE_REFINER))
    parser.add_argument("--source-refiner", default=str(DEFAULT_SOURCE_REFINER))
    parser.add_argument("--edge-registry", default=str(DEFAULT_EDGE_REGISTRY))
    parser.add_argument("--observer", default=str(DEFAULT_OBSERVER))
    parser.add_argument("--scoreboard", default=str(DEFAULT_SCOREBOARD))
    parser.add_argument("--pending", default=str(DEFAULT_PENDING))
    parser.add_argument("--gate", default=str(DEFAULT_GATE))
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    args = parser.parse_args()

    edge_refiner_path = Path(args.edge_refiner)
    source_refiner_path = Path(args.source_refiner)
    out_prefix = Path(args.out_prefix)
    edge_refiner = read_json(edge_refiner_path)
    source_refiner = read_json(source_refiner_path)
    registry = read_json(Path(args.edge_registry))
    observer = read_json(Path(args.observer))
    scoreboard = read_json(Path(args.scoreboard))
    pending = read_json(Path(args.pending))
    gate = read_json(Path(args.gate))

    if not isinstance(edge_refiner, dict) or not isinstance(edge_refiner.get("selected_candidate"), dict):
        print("selected_candidate_not_found", file=sys.stderr)
        return 2
    if not isinstance(source_refiner, dict) or not isinstance(source_refiner.get("results"), list):
        print("source_refiner_results_not_found", file=sys.stderr)
        return 2

    selected_raw = edge_refiner["selected_candidate"]
    selected = compact_row(selected_raw)
    parsed = parse_base_id(str(selected_raw.get("base_strategy_id") or selected_raw.get("strategy_id") or ""))
    source_results = [row for row in source_refiner.get("results", []) if isinstance(row, dict)]
    same_base = [compact_row(row) for row in source_results if row.get("base_strategy_id") == selected_raw.get("base_strategy_id")]
    same_base.sort(key=lambda row: row["hard_score"], reverse=True)

    same_shape = []
    shape_key = parsed.get("shape_key")
    for row in source_results:
        base = parse_base_id(str(row.get("base_strategy_id") or row.get("strategy_id") or ""))
        if shape_key and base.get("shape_key") == shape_key:
            same_shape.append(compact_row(row))
    same_shape.sort(key=lambda row: row["hard_score"], reverse=True)

    forward_state = load_forward_state(observer, scoreboard, pending, gate)
    notes = [
        "Keep `spot_confirms + oi_expansion` as the minimum active filter set for this observer candidate; it is the selected strict candidate with enough holdout sample and positive cost-stress.",
        "Do not promote higher-RR variants just because holdout expectancy is prettier; several fail the holdout-sample gate or have weaker stability.",
        "Current forward state has zero observer signal events, so no paper-design review is allowed yet.",
        "The current market is not near the setup: pending-watch is context-blocked and trigger distance is multiple ATRs away.",
        "Next useful work is forward evidence accumulation plus shadow comparison of same-shape variants; not live execution.",
    ]
    report = {
        "generated_at": now_iso(),
        "inputs": {
            "edge_refiner": rel(edge_refiner_path),
            "source_refiner": rel(source_refiner_path),
            "edge_registry": rel(Path(args.edge_registry)),
            "observer": rel(Path(args.observer)),
            "scoreboard": rel(Path(args.scoreboard)),
            "pending": rel(Path(args.pending)),
            "gate": rel(Path(args.gate)),
        },
        "selected_candidate": selected,
        "selected_parsed": parsed,
        "same_base_variants": same_base,
        "same_shape_alternatives": same_shape,
        "edge_registry_summary": registry.get("summary") if isinstance(registry, dict) and isinstance(registry.get("summary"), dict) else {},
        "forward_state": forward_state,
        "hardening_notes": notes,
        "decision": "keep_observer_only_no_paper_no_live",
        "next_action": "accumulate_forward_outcomes_and_run_shadow_same_shape_comparison",
        "can_trade": False,
    }
    write_json(out_prefix.with_suffix(".json"), report)
    write_text(out_prefix.with_suffix(".md"), render_md(report))
    print(
        json.dumps(
            {
                "status": "ok",
                "decision": report["decision"],
                "selected": selected.get("strategy_id"),
                "same_base_variants": len(same_base),
                "same_shape_alternatives": len(same_shape),
                "json": rel(out_prefix.with_suffix(".json")),
                "md": rel(out_prefix.with_suffix(".md")),
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

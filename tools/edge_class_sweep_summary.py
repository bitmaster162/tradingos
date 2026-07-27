#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


DEFAULT_REPORTS = [
    "docs/PARALLEL_EDGE_SEARCH_PASS_2026-07-02_NEXT.json",
    "docs/RELATIVE_STRENGTH_ROTATION_NESTED_HOLDOUT_2026-07-02_BOUNDED80.json",
    "docs/SESSION_OPENING_RANGE_NESTED_HOLDOUT_2026-07-02_NEXT.json",
    "docs/BASIS_FUNDING_CARRY_NESTED_HOLDOUT_2026-07-02_NEXT.json",
    "docs/BASIS_FUNDING_CARRY_MULTI_SYMBOL_NESTED_HOLDOUT_2026-07-02_REFRESHED.json",
    "docs/BASIS_FUNDING_CARRY_EVENT_SCARCITY_2026-07-02.json",
    "docs/BASIS_SHOCK_REVERSION_NESTED_HOLDOUT_2026-07-02_NEXT.json",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_get(data: dict[str, Any], dotted: str, default: Any = None) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict):
            return default
        current = current.get(part)
    return default if current is None else current


def as_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return round(parsed, 6)


def classify_report(path: Path, report: dict[str, Any]) -> str:
    name = path.stem.lower()
    if "relative_strength" in name:
        return "relative_strength_rotation"
    if "session_opening_range" in name:
        return "session_opening_range"
    if "basis_funding_carry" in name:
        if "event_scarcity" in name:
            return "basis_funding_carry_scarcity"
        if "multi_symbol" in name:
            return "basis_funding_carry_multi_symbol"
        return "basis_funding_carry"
    if "basis_shock_reversion" in name:
        return "basis_shock_reversion"
    if "parallel_edge_search" in name:
        return "document_rule_edge_search"
    return str(report.get("family") or report.get("tool") or "unknown")


def selected_strategy(report: dict[str, Any]) -> str | None:
    for path in (
        "selected_on_train.strategy_id",
        "selected_on_train.config.strategy_id",
        "selected.strategy_id",
        "best_candidate.id",
        "best_candidate.strategy_id",
        "candidate.id",
    ):
        value = safe_get(report, path)
        if value:
            return str(value)
    batch_path = safe_get(report, "batch.path")
    if batch_path:
        return str(batch_path)
    return None


def summarize_report(path: Path) -> dict[str, Any]:
    report = read_json(path)
    if not report:
        return {
            "path": portable(path),
            "class": "missing",
            "decision": "missing_or_unreadable",
            "status": "blocked",
            "can_trade": False,
        }

    decision = str(report.get("decision") or "unknown")
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    search = report.get("search") if isinstance(report.get("search"), dict) else {}
    validation_summary = safe_get(report, "validation.summary", {})
    oos_summary = safe_get(report, "oos.summary", {})
    batch = report.get("batch") if isinstance(report.get("batch"), dict) else {}
    data = report.get("data") if isinstance(report.get("data"), dict) else {}

    tested = as_int(summary.get("tested_configs")) or as_int(search.get("tested")) or as_int(report.get("tested")) or as_int(batch.get("completed_tests"))
    train_qualified = as_int(summary.get("train_qualified")) or as_int(search.get("train_qualified")) or as_int(report.get("train_qualified"))
    validation_qualified = as_int(summary.get("validation_qualified"))
    oos_qualified = as_int(summary.get("oos_qualified"))
    validation_trades = as_int(validation_summary.get("trades")) if isinstance(validation_summary, dict) else None
    oos_trades = as_int(oos_summary.get("trades")) if isinstance(oos_summary, dict) else None

    if "candidate_needs_forward_proof" in decision:
        status = "forward_candidate"
    elif "event_scarcity_no_recent_events" in decision:
        status = "rare_cycle_inactive"
    elif "event_scarcity" in decision:
        status = "scarcity_review"
    elif "insufficient_validation" in decision or (train_qualified and validation_trades == 0):
        status = "needs_more_validation_events"
    elif decision.startswith("reject") or "no_promotable" in decision:
        status = "rejected"
    elif "watchlist" in decision:
        status = "watchlist"
    else:
        status = "review"

    return {
        "path": portable(path),
        "class": classify_report(path, report),
        "decision": decision,
        "status": status,
        "tested": tested,
        "train_qualified": train_qualified,
        "validation_qualified": validation_qualified,
        "oos_qualified": oos_qualified,
        "validation_trades": validation_trades,
        "oos_trades": oos_trades,
        "selected_strategy": selected_strategy(report),
        "data_coverage_pct": as_float(data.get("coverage_pct")) if isinstance(data, dict) else None,
        "data_first_time": data.get("first_time") if isinstance(data, dict) else None,
        "data_last_time": data.get("last_time") if isinstance(data, dict) else None,
        "next_action": report.get("next_action"),
        "can_trade": report.get("can_trade") is True,
    }


def registry_summary(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    top = payload.get("top_candidates") if isinstance(payload.get("top_candidates"), list) else []
    top_rows = top[:5]
    return {
        "path": portable(path),
        "unique_candidates": as_int(payload.get("unique_candidates")),
        "candidate_rows_extracted": as_int(payload.get("candidate_rows_extracted")),
        "class_counts": payload.get("class_counts") if isinstance(payload.get("class_counts"), dict) else {},
        "top_candidates": [
            {
                "candidate_id": row.get("candidate_id"),
                "source": row.get("source"),
                "edge_classification": row.get("edge_classification"),
                "evidence_score": row.get("evidence_score"),
                "metrics": row.get("metrics"),
                "next_action": row.get("next_action"),
            }
            for row in top_rows
            if isinstance(row, dict)
        ],
    }


def decide(rows: list[dict[str, Any]], registry: dict[str, Any]) -> tuple[str, str]:
    if any(row.get("status") == "forward_candidate" for row in rows):
        return "edge_class_sweep_forward_candidate_found", "route candidate into observer-only forward proof; no trading permission"
    if any(row.get("status") == "rare_cycle_inactive" for row in rows):
        return (
            "edge_class_sweep_carry_rare_cycle_inactive",
            "do not wait on frozen carry; keep it as rare-cycle reference and search a materially different live-data class next",
        )
    needs_validation = [row for row in rows if row.get("status") == "needs_more_validation_events"]
    if needs_validation:
        return (
            "edge_class_sweep_needs_more_validation_events",
            "extend validation sample for basis/funding carry before retuning; keep rejected classes tombstoned",
        )
    top = registry.get("top_candidates") or []
    if top:
        return (
            "edge_class_sweep_historical_watchlist_only",
            "keep top historical range/perp-exhaustion candidate in forward observer; search a materially different live-data class next",
        )
    return "edge_class_sweep_no_candidate", "search a materially different edge class; do not retune rejected grids"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Edge Class Sweep Summary",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        f"- Next action: {report['next_action']}",
        "",
        "## Fresh Class Results",
        "",
        "| Class | Decision | Status | Tested | Train Q | Val Q | OOS Q | Val Trades | Selected |",
        "|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in report.get("fresh_results", []):
        lines.append(
            f"| `{row.get('class')}` | `{row.get('decision')}` | `{row.get('status')}` | "
            f"`{row.get('tested')}` | `{row.get('train_qualified')}` | `{row.get('validation_qualified')}` | "
            f"`{row.get('oos_qualified')}` | `{row.get('validation_trades')}` | `{row.get('selected_strategy')}` |"
        )
    lines.extend(["", "## Top Historical Registry Rows", ""])
    top = report.get("edge_registry", {}).get("top_candidates", [])
    if top:
        lines.append("| Candidate | Class | Score | Key metrics |")
        lines.append("|---|---|---:|---|")
        for row in top:
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            metric_text = (
                f"full_exp={metrics.get('full_expectancy_r')}, "
                f"holdout_exp={metrics.get('holdout_expectancy_r')}, "
                f"holdout_trades={metrics.get('holdout_trades')}, "
                f"cost10={metrics.get('cost10_expectancy_r')}"
            )
            lines.append(
                f"| `{row.get('candidate_id')}` | `{row.get('edge_classification')}` | "
                f"`{row.get('evidence_score')}` | {metric_text} |"
            )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Research-only summary.",
            "- Rejected grids must not be retuned on opened validation/OOS data.",
            "- `can_trade=false`; no paper/live permission.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Summarize the latest independent edge-class sweep.")
    parser.add_argument("--report", action="append", default=[])
    parser.add_argument("--edge-registry", default="docs/EDGE_REGISTRY_2026-07-02_AFTER_NEXT_EDGE_SWEEP.json")
    parser.add_argument("--out-prefix", default="docs/EDGE_CLASS_SWEEP_SUMMARY_2026-07-02")
    args = parser.parse_args()

    report_paths = [resolve_path(item) for item in (args.report or DEFAULT_REPORTS)]
    fresh = [summarize_report(path) for path in report_paths]
    registry = registry_summary(resolve_path(args.edge_registry))
    decision, next_action = decide(fresh, registry)
    payload = {
        "generated_at": now_iso(),
        "tool": "tools/edge_class_sweep_summary.py",
        "decision": decision,
        "can_trade": False,
        "orders_allowed": False,
        "fresh_results": fresh,
        "edge_registry": registry,
        "next_action": next_action,
        "boundary": {
            "research_only": True,
            "opens_paper_entries": False,
            "sends_orders": False,
            "can_trade": False,
        },
    }
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), payload)
    out_prefix.with_suffix(".md").write_text(render_markdown(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": decision,
                "fresh_results": len(fresh),
                "top_registry_candidates": len(registry.get("top_candidates") or []),
                "out": portable(out_prefix.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

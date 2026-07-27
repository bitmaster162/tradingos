#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
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
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def get(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def collect_paths(patterns: list[str]) -> list[Path]:
    paths: dict[str, Path] = {}
    for pattern in patterns:
        resolved_pattern = str(resolve_path(pattern))
        for item in glob.glob(resolved_pattern):
            path = Path(item)
            if path.is_file() and path.suffix.lower() == ".json":
                paths[str(path.resolve())] = path
    return sorted(paths.values(), key=lambda item: item.name)


def report_row(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    selected = payload.get("selected") if isinstance(payload.get("selected"), dict) else {}
    config = selected.get("config") if isinstance(selected.get("config"), dict) else {}
    train = selected.get("train") if isinstance(selected.get("train"), dict) else {}
    validation = selected.get("validation") if isinstance(selected.get("validation"), dict) else {}
    oos = selected.get("oos") if isinstance(selected.get("oos"), dict) else {}
    oos_gate = selected.get("oos_gate") if isinstance(selected.get("oos_gate"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "report": path.stem,
        "path": portable(path),
        "decision": payload.get("decision"),
        "tested": summary.get("tested"),
        "train_qualified": summary.get("train_qualified"),
        "validation_qualified": summary.get("validation_qualified"),
        "selected_strategy_id": selected.get("strategy_id"),
        "family": config.get("family"),
        "side": config.get("side"),
        "interval": config.get("interval"),
        "train_trades": get(train, "summary", "trades"),
        "train_expectancy_r": get(train, "summary", "expectancy_r"),
        "validation_trades": get(validation, "summary", "trades"),
        "validation_expectancy_r": get(validation, "summary", "expectancy_r"),
        "oos_trades": get(oos, "summary", "trades"),
        "oos_expectancy_r": get(oos, "summary", "expectancy_r"),
        "oos_gate_pass": oos_gate.get("pass") if oos_gate else None,
        "can_trade": payload.get("can_trade"),
    }


def build_report(patterns: list[str]) -> dict[str, Any]:
    rows = [report_row(path) for path in collect_paths(patterns)]
    promotable = [
        row for row in rows
        if row.get("oos_gate_pass") is True
        and str(row.get("decision") or "").startswith("oos_pass")
        and row.get("can_trade") is False
    ]
    validation_mirages = [
        row for row in rows
        if int(row.get("validation_qualified") or 0) > 0
        and row.get("oos_gate_pass") is False
    ]
    no_train = [row for row in rows if str(row.get("decision") or "") == "reject_no_train_candidate"]
    validation_failed = [row for row in rows if str(row.get("decision") or "") == "reject_validation_gate_failed"]

    family_buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        family = str(row.get("family") or "none")
        bucket = family_buckets.setdefault(
            family,
            {"family": family, "reports": 0, "validation_qualified": 0, "oos_pass": 0, "best_oos_expectancy_r": None},
        )
        bucket["reports"] += 1
        bucket["validation_qualified"] += int(row.get("validation_qualified") or 0)
        if row.get("oos_gate_pass") is True:
            bucket["oos_pass"] += 1
        oos_exp = row.get("oos_expectancy_r")
        if isinstance(oos_exp, (int, float)):
            previous = bucket.get("best_oos_expectancy_r")
            bucket["best_oos_expectancy_r"] = oos_exp if previous is None else max(float(previous), float(oos_exp))

    decision = "no_promotable_derivatives_event_edge"
    if promotable:
        decision = "promotable_observer_candidate_found_not_trade_permission"
    elif validation_mirages:
        decision = "validation_mirage_no_oos_edge"

    next_action = "stop_retuning_this_family_set_and_add_new_features"
    if promotable:
        next_action = "register_oos_pass_for_forward_observer_only"
    elif validation_mirages:
        next_action = "do_not_promote; inspect why validation edges vanish in oos before adding observers"

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "patterns": patterns,
        "decision": decision,
        "summary": {
            "reports": len(rows),
            "promotable": len(promotable),
            "validation_mirages": len(validation_mirages),
            "reject_no_train_candidate": len(no_train),
            "reject_validation_gate_failed": len(validation_failed),
        },
        "family_buckets": sorted(family_buckets.values(), key=lambda item: (item["oos_pass"], item["validation_qualified"], item["reports"]), reverse=True),
        "rows": rows,
        "next_action": next_action,
        "runtime_boundary": {
            "research_summary_only": True,
            "paper_allowed": False,
            "live_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Derivatives Event Research Matrix",
        "",
        f"- Generated: `{report['generated_at']}`.",
        f"- Decision: `{report['decision']}`.",
        f"- Next action: `{report['next_action']}`.",
        "- Boundary: research summary only; `can_trade=false`; no paper/live permission.",
        "",
        "## Summary",
        "",
    ]
    for key, value in report["summary"].items():
        lines.append(f"- `{key}`: `{value}`.")
    lines.extend([
        "",
        "## Family Buckets",
        "",
        "| family | reports | validation qualified | OOS pass | best OOS expectancy R |",
        "|---|---:|---:|---:|---:|",
    ])
    for item in report["family_buckets"]:
        lines.append(
            f"| `{item['family']}` | `{item['reports']}` | `{item['validation_qualified']}` | `{item['oos_pass']}` | `{item['best_oos_expectancy_r']}` |"
        )
    lines.extend([
        "",
        "## Reports",
        "",
        "| report | decision | train | validation | selected | OOS trades | OOS exp R | OOS gate |",
        "|---|---|---:|---:|---|---:|---:|---|",
    ])
    for row in report["rows"]:
        lines.append(
            f"| `{row['report']}` | `{row['decision']}` | `{row['train_qualified']}` | `{row['validation_qualified']}` | `{row['selected_strategy_id']}` | `{row['oos_trades']}` | `{row['oos_expectancy_r']}` | `{row['oos_gate_pass']}` |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize derivatives-event miner reports and promotion blockers")
    parser.add_argument(
        "--patterns",
        default="docs/DERIVATIVES_EVENT_EDGE_MINER_FRESH_SWEEP_*.json,docs/DERIVATIVES_EVENT_FUNDING_FOCUS_*_*.json,docs/DERIVATIVES_EVENT_DELEV_FOCUS_*_*.json,docs/DERIVATIVES_EVENT_SQUEEZE_FOCUS_*_*.json",
    )
    parser.add_argument("--out-prefix", default="docs/DERIVATIVES_EVENT_RESEARCH_MATRIX_2026-06-29")
    args = parser.parse_args()

    patterns = [item.strip() for item in args.patterns.split(",") if item.strip()]
    report = build_report(patterns)
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "reports": report["summary"]["reports"],
                "promotable": report["summary"]["promotable"],
                "validation_mirages": report["summary"]["validation_mirages"],
                "next_action": report["next_action"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

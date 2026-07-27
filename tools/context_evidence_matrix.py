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


DEFAULT_PATTERNS = ",".join(
    [
        "docs/DERIVATIVES_EVENT_RESEARCH_MATRIX*.json",
        "docs/DERIVATIVES_CONTEXT_COMPOSITE_MINER*.json",
        "docs/EDGE_LIQUIDATION_CONTEXT_HISTORICAL_REPLAY*.json",
        "docs/LIQUIDATION_IMPULSE_*NESTED_HOLDOUT*.json",
        "docs/SPOT_PERP_DIVERGENCE_HARDENING*.json",
        "docs/SPOT_LED_CONTINUATION_NESTED_HOLDOUT*.json",
    ]
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


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
        for item in glob.glob(str(resolve_path(pattern))):
            path = Path(item)
            if path.is_file() and path.suffix.lower() == ".json":
                paths[str(path.resolve())] = path
    return sorted(paths.values(), key=lambda item: item.name)


def top_result(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("top_results")
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    return {}


def classify_report(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    name = path.stem
    row: dict[str, Any] = {
        "report": name,
        "path": portable(path),
        "report_type": "unknown",
        "decision": payload.get("decision"),
        "ready_for_integration": False,
        "evidence_level": "unknown",
        "key_metric": None,
        "can_trade": payload.get("can_trade", False),
    }

    if name.startswith("DERIVATIVES_EVENT_RESEARCH_MATRIX"):
        promotable = get(payload, "summary", "promotable", default=0)
        mirages = get(payload, "summary", "validation_mirages", default=0)
        row.update(
            {
                "report_type": "derivatives_event_matrix",
                "ready_for_integration": int(promotable or 0) > 0,
                "evidence_level": "oos_pass" if int(promotable or 0) > 0 else "validation_mirage_or_reject",
                "key_metric": f"promotable={promotable} validation_mirages={mirages}",
            }
        )
        return row

    if name.startswith("DERIVATIVES_CONTEXT_COMPOSITE_MINER"):
        decision = str(payload.get("decision") or "")
        train_qualified = get(payload, "summary", "train_qualified", default=0)
        validation_qualified = get(payload, "summary", "validation_qualified", default=0)
        selected = payload.get("selected") if isinstance(payload.get("selected"), dict) else {}
        oos_exp = get(selected, "oos", "summary", "expectancy_r")
        oos_trades = get(selected, "oos", "summary", "trades")
        row.update(
            {
                "report_type": "derivatives_context_composite",
                "ready_for_integration": decision.startswith("oos_pass"),
                "evidence_level": "oos_pass" if decision.startswith("oos_pass") else "nested_holdout_reject",
                "key_metric": (
                    f"train={train_qualified} validation={validation_qualified} "
                    f"oos_trades={oos_trades} oos_exp={oos_exp}"
                ),
            }
        )
        return row

    if "EDGE_LIQUIDATION_CONTEXT_HISTORICAL_REPLAY" in name:
        classification = get(payload, "evidence_gate", "classification", default=payload.get("decision"))
        repeated = get(payload, "evidence_gate", "repeatable_positive_contexts", default=[])
        row.update(
            {
                "report_type": "liquidation_context_replay",
                "decision": payload.get("decision") or classification,
                "ready_for_integration": bool(repeated),
                "evidence_level": "repeatable_context" if repeated else "insufficient_or_nonrepeatable_oos_context",
                "key_metric": f"classification={classification} repeatable={repeated}",
            }
        )
        return row

    if name.startswith("LIQUIDATION_IMPULSE_"):
        decision = str(payload.get("decision") or "")
        oos_exp = get(payload, "oos", "summary", "expectancy_r", default=get(payload, "oos", "expectancy_r"))
        oos_trades = get(payload, "oos", "summary", "trades", default=get(payload, "oos", "trades"))
        row.update(
            {
                "report_type": "liquidation_impulse_holdout",
                "ready_for_integration": decision.startswith("oos_pass"),
                "evidence_level": "oos_pass" if decision.startswith("oos_pass") else "nested_holdout_reject",
                "key_metric": f"oos_trades={oos_trades} oos_exp={oos_exp}",
            }
        )
        return row

    if name.startswith("SPOT_PERP_DIVERGENCE_HARDENING"):
        passed_count = int(payload.get("passed_count") or 0)
        top = top_result(payload)
        summary = top.get("summary") if isinstance(top.get("summary"), dict) else {}
        row.update(
            {
                "report_type": "spot_perp_divergence",
                "decision": "spot_perp_passed" if passed_count > 0 else "spot_perp_rejected",
                "ready_for_integration": passed_count > 0,
                "evidence_level": "hardening_pass" if passed_count > 0 else "hardening_reject",
                "key_metric": (
                    f"passed={passed_count} top={top.get('strategy_id')} "
                    f"trades={summary.get('trades')} winrate={summary.get('winrate_pct')} exp={summary.get('expectancy_r')}"
                ),
                "can_trade": False,
            }
        )
        return row

    if name.startswith("SPOT_LED_CONTINUATION_NESTED_HOLDOUT"):
        decision = str(payload.get("decision") or "")
        oos_exp = get(payload, "oos", "summary", "expectancy_r", default=get(payload, "oos", "expectancy_r"))
        oos_trades = get(payload, "oos", "summary", "trades", default=get(payload, "oos", "trades"))
        row.update(
            {
                "report_type": "spot_led_nested_holdout",
                "ready_for_integration": decision.startswith("oos_pass"),
                "evidence_level": "oos_pass" if decision.startswith("oos_pass") else "nested_holdout_reject",
                "key_metric": f"oos_trades={oos_trades} oos_exp={oos_exp}",
            }
        )
        return row

    return row


def build_report(patterns: list[str]) -> dict[str, Any]:
    rows = [classify_report(path, read_json(path)) for path in collect_paths(patterns)]
    ready = [row for row in rows if row.get("ready_for_integration") is True]
    by_type: dict[str, dict[str, Any]] = {}
    for row in rows:
        report_type = str(row.get("report_type") or "unknown")
        item = by_type.setdefault(report_type, {"report_type": report_type, "reports": 0, "ready": 0})
        item["reports"] += 1
        item["ready"] += 1 if row.get("ready_for_integration") is True else 0

    decision = "no_context_factor_ready_for_derivatives_event_integration"
    next_action = "do_not_promote_context_filters; add new precommitted composite features and rerun nested holdout"
    if ready:
        decision = "context_factor_candidate_ready_for_precommitted_integration"
        next_action = "integrate ready context only as a precommitted observer-side feature; no trade permission"

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "patterns": patterns,
        "decision": decision,
        "summary": {
            "reports": len(rows),
            "ready_for_integration": len(ready),
            "blocked_or_rejected": len(rows) - len(ready),
        },
        "by_type": sorted(by_type.values(), key=lambda item: (item["ready"], item["reports"]), reverse=True),
        "rows": rows,
        "next_action": next_action,
        "runtime_boundary": {
            "context_evidence_only": True,
            "paper_allowed": False,
            "live_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Context Evidence Matrix",
        "",
        f"- Generated: `{report['generated_at']}`.",
        f"- Decision: `{report['decision']}`.",
        f"- Next action: `{report['next_action']}`.",
        "- Boundary: context evidence only; `can_trade=false`; no paper/live permission.",
        "",
        "## Summary",
        "",
    ]
    for key, value in report["summary"].items():
        lines.append(f"- `{key}`: `{value}`.")
    lines.extend(
        [
            "",
            "## By Type",
            "",
            "| type | reports | ready |",
            "|---|---:|---:|",
        ]
    )
    for item in report["by_type"]:
        lines.append(f"| `{item['report_type']}` | `{item['reports']}` | `{item['ready']}` |")
    lines.extend(
        [
            "",
            "## Reports",
            "",
            "| report | type | decision | evidence | metric | ready |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in report["rows"]:
        lines.append(
            f"| `{row['report']}` | `{row['report_type']}` | `{row['decision']}` | `{row['evidence_level']}` | `{row['key_metric']}` | `{row['ready_for_integration']}` |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize context-factor evidence before integration into derivatives-event research")
    parser.add_argument("--patterns", default=DEFAULT_PATTERNS)
    parser.add_argument("--out-prefix", default="docs/CONTEXT_EVIDENCE_MATRIX_2026-06-29")
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
                "ready_for_integration": report["summary"]["ready_for_integration"],
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

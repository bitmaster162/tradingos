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
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def select_edge_candidate(edge_registry: dict[str, Any]) -> dict[str, Any] | None:
    rows = edge_registry.get("top_candidates")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict) or row.get("edge_classification") != "edge_candidate_forward_proof_required":
            continue
        # Registry diagnostics can score aggregate rows that are not executable strategy shapes.
        # Forward export requires a concrete candidate that the observer can actually consume.
        required = ("candidate_id", "source", "interval", "side", "trigger", "rr")
        if all(row.get(field) not in {None, ""} for field in required):
            return row
    return None


def find_refiner_row(source_payload: dict[str, Any], strategy_id: str) -> dict[str, Any] | None:
    for container in ("selected_candidate", "best_candidate"):
        value = source_payload.get(container)
        if isinstance(value, dict) and value.get("strategy_id") == strategy_id:
            return value
    for container in ("top_results", "results", "all_results", "candidates"):
        value = source_payload.get(container)
        if not isinstance(value, list):
            continue
        for row in value:
            if isinstance(row, dict) and row.get("strategy_id") == strategy_id:
                return row
    return None


def select_nested_edge(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for row in payload.get("families", []):
        if not isinstance(row, dict) or row.get("family") != "EDGE_FORWARD_4H":
            continue
        decision = str(row.get("decision") or "")
        selected = row.get("selected_on_train")
        if isinstance(selected, dict) and (decision.startswith("pass_oos") or decision.startswith("insufficient_oos")):
            normalized = dict(selected)
            config = selected.get("config") if isinstance(selected.get("config"), dict) else {}
            for field in ("interval", "side", "trigger", "max_hold_bars"):
                if normalized.get(field) is None and config.get(field) is not None:
                    normalized[field] = config.get(field)
            if not normalized.get("rr") and config.get("stop_atr") is not None and config.get("take_atr") is not None:
                normalized["rr"] = f"{float(config['stop_atr']):g}:{float(config['take_atr']):g}"
            return normalized, row
    return None


def validate_candidate_lock(lock: dict[str, Any], selected: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if lock.get("enabled") is not True:
        errors.append("lock_not_enabled")
    candidate = lock.get("candidate") if isinstance(lock.get("candidate"), dict) else {}
    for field in ("strategy_id", "base_strategy_id", "filter_mode", "interval", "side", "trigger", "rr", "max_hold_bars"):
        if candidate.get(field) != selected.get(field):
            errors.append(f"lock_mismatch:{field}")
    if list(candidate.get("filters") or []) != list(selected.get("filters") or []):
        errors.append("lock_mismatch:filters")
    boundaries = lock.get("boundaries") if isinstance(lock.get("boundaries"), dict) else {}
    if boundaries.get("can_trade") is not False or boundaries.get("allow_orders") is not False:
        errors.append("unsafe_lock_boundary")
    return not errors, errors


def render_markdown(report: dict[str, Any]) -> str:
    selected = report.get("selected_candidate") if isinstance(report.get("selected_candidate"), dict) else {}
    edge = report.get("edge_registry_row") if isinstance(report.get("edge_registry_row"), dict) else {}
    metrics = edge.get("metrics") if isinstance(edge.get("metrics"), dict) else {}
    return "\n".join(
        [
            "# Edge Forward Candidate Export",
            "",
            f"Generated: `{report.get('generated_at')}`",
            "",
            "## Boundary",
            "",
            "- Exports one strict edge-registry candidate into a refiner-compatible report.",
            "- Does not change the active RANGE selected candidate.",
            "- Does not create paper-entry intents or orders.",
            "",
            "## Selected Candidate",
            "",
            f"- Strategy: `{selected.get('strategy_id')}`.",
            f"- Base: `{selected.get('base_strategy_id')}`.",
            f"- Filter: `{selected.get('filter_mode')}` (`{'+'.join(selected.get('filters') or [])}`).",
            f"- TF / side / trigger / RR: `{selected.get('interval')}` / `{selected.get('side')}` / `{selected.get('trigger')}` / `{selected.get('rr')}`.",
            "",
            "## Evidence",
            "",
            f"- Edge score: `{edge.get('evidence_score')}`.",
            f"- Full trades / expectancy: `{metrics.get('full_trades')}` / `{metrics.get('full_expectancy_r')}`R.",
            f"- Holdout trades / expectancy: `{metrics.get('holdout_trades')}` / `{metrics.get('holdout_expectancy_r')}`R.",
            f"- Stable folds: `{metrics.get('stable_folds')}`.",
            f"- Cost10 expectancy: `{metrics.get('cost10_expectancy_r')}`R.",
            f"- Blocks: `{edge.get('blocks')}`.",
            "",
            "## Decision",
            "",
            f"- `{report.get('decision')}`.",
            f"- Next: `{report.get('next_action')}`.",
            "",
        ]
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Export top strict edge candidate as refiner-compatible observer input")
    parser.add_argument("--edge-registry", default="docs/EDGE_REGISTRY_2026-06-18.json")
    parser.add_argument("--nested-holdout", default="docs/RANGE_EDGE_NESTED_HOLDOUT_2026-06-23.json")
    parser.add_argument("--candidate-lock", default="configs/EDGE_FORWARD_LOCK.json")
    parser.add_argument("--out-prefix", default="docs/EDGE_FORWARD_CANDIDATE_REFINER_2026-06-18")
    args = parser.parse_args()

    nested_path = resolve_path(args.nested_holdout)
    nested_payload = read_json(nested_path) if nested_path.exists() else {}
    nested_selection = select_nested_edge(nested_payload if isinstance(nested_payload, dict) else {})
    if nested_selection:
        selected, family_row = nested_selection
        lock_path = resolve_path(args.candidate_lock)
        candidate_lock = read_json(lock_path) if lock_path.exists() else {}
        lock_ok, lock_errors = validate_candidate_lock(candidate_lock if isinstance(candidate_lock, dict) else {}, selected)
        if not lock_ok:
            report = {
                "generated_at": now_iso(),
                "runtime_boundary": {
                    "classification": "edge_forward_export_blocked_lock_mismatch",
                    "can_trade": False,
                    "sends_orders": False,
                    "creates_paper_entry_intents": False,
                    "changes_active_strategy": False,
                },
                "inputs": {
                    "nested_holdout": rel_path(nested_path),
                    "candidate_lock": rel_path(lock_path),
                },
                "decision": "blocked_edge_candidate_lock_mismatch",
                "lock_errors": lock_errors,
                "next_action": "review candidate lineage; do not auto-reselect or run observer",
                "can_trade": False,
            }
            out_prefix = resolve_path(args.out_prefix)
            write_json(out_prefix.with_suffix(".json"), report)
            out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
            print(json.dumps({"decision": report["decision"], "lock_errors": lock_errors, "can_trade": False}, ensure_ascii=False, indent=2))
            return 2
        report = {
            "generated_at": now_iso(),
            "runtime_boundary": {
                "classification": "edge_forward_export_research_only",
                "can_trade": False,
                "sends_orders": False,
                "creates_paper_entry_intents": False,
                "changes_active_strategy": False,
            },
            "inputs": {
                "nested_holdout": rel_path(nested_path),
                "candidate_lock": rel_path(lock_path),
                "candidate_lock_version": candidate_lock.get("version"),
            },
            "selected_candidate": selected,
            "top_results": [selected],
            "results": [selected],
            "nested_oos_evidence": {
                "decision": family_row.get("decision"),
                "oos": family_row.get("oos"),
                "oos_gate": family_row.get("oos_gate"),
            },
            "decision": "train_selected_edge_exported_for_observer_only_forward_proof",
            "next_action": "collect independent forward outcomes; no parameter changes and no paper/live permission",
            "can_trade": False,
        }
        out_prefix = resolve_path(args.out_prefix)
        write_json(out_prefix.with_suffix(".json"), report)
        out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
        print(json.dumps({"decision": report["decision"], "strategy_id": selected.get("strategy_id"), "can_trade": False}, ensure_ascii=False, indent=2))
        return 0

    registry_path = resolve_path(args.edge_registry)
    registry = read_json(registry_path)
    edge = select_edge_candidate(registry if isinstance(registry, dict) else {})
    if not isinstance(edge, dict):
        report = {
            "generated_at": now_iso(),
            "runtime_boundary": {
                "classification": "edge_forward_export_research_only",
                "can_trade": False,
                "sends_orders": False,
                "creates_paper_entry_intents": False,
                "changes_active_strategy": False,
            },
            "edge_registry": rel_path(registry_path),
            "decision": "blocked_no_forward_proof_candidate",
            "next_action": "build more evidence before observer export",
            "can_trade": False,
        }
        out_prefix = resolve_path(args.out_prefix)
        write_json(out_prefix.with_suffix(".json"), report)
        out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
        print(json.dumps({"decision": report["decision"], "can_trade": False}, ensure_ascii=False, indent=2))
        return 2

    source_path = resolve_path(str(edge.get("source")))
    source_payload = read_json(source_path)
    strategy_id = str(edge.get("candidate_id"))
    selected = find_refiner_row(source_payload if isinstance(source_payload, dict) else {}, strategy_id)
    if not isinstance(selected, dict):
        raise ValueError(f"edge_candidate_not_found_in_source:{strategy_id}:{rel_path(source_path)}")

    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "edge_forward_export_research_only",
            "can_trade": False,
            "sends_orders": False,
            "creates_paper_entry_intents": False,
            "changes_active_strategy": False,
        },
        "inputs": {
            "edge_registry": rel_path(registry_path),
            "source_report": rel_path(source_path),
        },
        "selected_candidate": selected,
        "top_results": [selected],
        "results": [selected],
        "edge_registry_row": edge,
        "decision": "edge_candidate_exported_for_observer_only_forward_proof",
        "next_action": "run range_refined_forward_observer and pending_watch with this refiner report; no paper/live permission",
        "can_trade": False,
    }
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": rel_path(out_prefix.with_suffix(".json")),
                "md": rel_path(out_prefix.with_suffix(".md")),
                "decision": report["decision"],
                "strategy_id": selected.get("strategy_id"),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

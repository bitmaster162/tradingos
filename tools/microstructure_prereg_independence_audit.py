#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except (OSError, ValueError):
        return str(path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc)}
    return payload if isinstance(payload, dict) else {"_read_error": "json_root_not_object"}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return round(len(left & right) / len(union), 6) if union else 1.0


def build_report(queue_path: Path, taxonomy_path: Path, runner_path: Path) -> dict[str, Any]:
    queue = read_json(queue_path)
    taxonomy = read_json(taxonomy_path)
    runner = read_json(runner_path)
    hypotheses = queue.get("hypotheses") if isinstance(queue.get("hypotheses"), list) else []
    family_map = taxonomy.get("families") if isinstance(taxonomy.get("families"), dict) else {}
    experiments = runner.get("experiments") if isinstance(runner.get("experiments"), dict) else {}
    budget = queue.get("portfolio_budget") if isinstance(queue.get("portfolio_budget"), dict) else {}
    policy = taxonomy.get("independence_policy") if isinstance(taxonomy.get("independence_policy"), dict) else {}

    queue_families = [str(item.get("family")) for item in hypotheses if isinstance(item, dict)]
    hypothesis_ids = [str(item.get("hypothesis_id")) for item in hypotheses if isinstance(item, dict)]
    experiment_ids = [str(item.get("experiment")) for item in hypotheses if isinstance(item, dict)]
    primary_mechanisms = [
        str(family_map.get(family, {}).get("primary_mechanism"))
        for family in queue_families
        if isinstance(family_map.get(family), dict)
    ]
    event_archetypes = [
        str(family_map.get(family, {}).get("event_archetype"))
        for family in queue_families
        if isinstance(family_map.get(family), dict)
    ]

    pairwise: list[dict[str, Any]] = []
    for left, right in combinations(queue_families, 2):
        left_spec = family_map.get(left) if isinstance(family_map.get(left), dict) else {}
        right_spec = family_map.get(right) if isinstance(family_map.get(right), dict) else {}
        left_domains = set(left_spec.get("input_domains") or [])
        right_domains = set(right_spec.get("input_domains") or [])
        overlap = jaccard(left_domains, right_domains)
        same_primary = left_spec.get("primary_mechanism") == right_spec.get("primary_mechanism")
        same_event = left_spec.get("event_archetype") == right_spec.get("event_archetype")
        pairwise.append(
            {
                "left": left,
                "right": right,
                "shared_input_domains": sorted(left_domains & right_domains),
                "input_domain_jaccard": overlap,
                "same_primary_mechanism": same_primary,
                "same_event_archetype": same_event,
                "pass": (
                    not same_primary
                    and not same_event
                    and overlap <= float(policy.get("max_pairwise_input_domain_jaccard") or 0.0)
                ),
            }
        )

    runner_rows = []
    for item in hypotheses:
        if not isinstance(item, dict):
            continue
        experiment = str(item.get("experiment"))
        runner_spec = experiments.get(experiment) if isinstance(experiments.get(experiment), dict) else {}
        script_path = resolve_path(str(runner_spec.get("script") or "missing"))
        runner_rows.append(
            {
                "hypothesis_id": item.get("hypothesis_id"),
                "experiment": experiment,
                "family": item.get("family"),
                "implementation_status": runner_spec.get("implementation_status"),
                "supports_lock_path": runner_spec.get("supports_lock_path"),
                "script": portable(script_path),
                "script_exists": script_path.is_file(),
                "matches_contract": (
                    runner_spec.get("hypothesis_id") == item.get("hypothesis_id")
                    and runner_spec.get("family") == item.get("family")
                ),
            }
        )

    checks = {
        "queue_readable": not queue.get("_read_error"),
        "taxonomy_readable": not taxonomy.get("_read_error"),
        "runner_contract_readable": not runner.get("_read_error"),
        "queue_locked": queue.get("status") == "locked_preregistration_queue",
        "queue_at_declared_hypothesis_budget": (
            len(hypotheses) == int(budget.get("registered_hypotheses") or -1)
            and len(hypotheses) == int(budget.get("max_hypotheses") or -2)
        ),
        "configuration_budget_unused": int(budget.get("used_configurations") or 0) == 0,
        "oos_budget_unused": int(budget.get("used_oos_openings") or 0) == 0,
        "automatic_queue_mutation_forbidden": queue.get("runtime_boundary", {}).get("automatic_queue_mutation") is False,
        "taxonomy_covers_exact_queue": set(queue_families) == set(family_map),
        "hypothesis_ids_unique": len(hypothesis_ids) == len(set(hypothesis_ids)),
        "experiments_unique": len(experiment_ids) == len(set(experiment_ids)),
        "primary_mechanisms_unique": len(primary_mechanisms) == len(hypotheses) == len(set(primary_mechanisms)),
        "event_archetypes_unique": len(event_archetypes) == len(hypotheses) == len(set(event_archetypes)),
        "pairwise_overlap_within_policy": bool(pairwise) and all(item["pass"] for item in pairwise),
        "all_runner_scripts_locked_and_present": bool(runner_rows)
        and all(
            item["implementation_status"] == "implemented_locked"
            and item["supports_lock_path"] is True
            and item["script_exists"]
            and item["matches_contract"]
            for item in runner_rows
        ),
        "fifth_hypothesis_forbidden": policy.get("fifth_hypothesis_allowed") is False,
        "runtime_can_trade_false": queue.get("runtime_boundary", {}).get("can_trade") is False,
    }
    failed = [name for name, passed in checks.items() if not passed]
    max_overlap = max((item["input_domain_jaccard"] for item in pairwise), default=None)
    decision = (
        "four_preregistered_mechanisms_independent_queue_full"
        if not failed
        else "preregistered_mechanism_independence_audit_failed"
    )
    return {
        "generated_at": now_iso(),
        "tool": "microstructure_prereg_independence_audit",
        "decision": decision,
        "queue_path": portable(queue_path),
        "taxonomy_path": portable(taxonomy_path),
        "runner_contract_path": portable(runner_path),
        "summary": {
            "registered_hypotheses": len(hypotheses),
            "primary_mechanisms": len(set(primary_mechanisms)),
            "event_archetypes": len(set(event_archetypes)),
            "declared_configurations": sum(
                int(item.get("grid", {}).get("total_configurations") or 0)
                for item in hypotheses
                if isinstance(item, dict)
            ),
            "used_configurations": int(budget.get("used_configurations") or 0),
            "max_pairwise_input_domain_jaccard": max_overlap,
            "queue_capacity": f"{len(hypotheses)}/{budget.get('max_hypotheses')}",
        },
        "families": [
            {
                "hypothesis_id": item.get("hypothesis_id"),
                "family": item.get("family"),
                "primary_mechanism": family_map.get(str(item.get("family")), {}).get("primary_mechanism"),
                "event_archetype": family_map.get(str(item.get("family")), {}).get("event_archetype"),
                "response_class": family_map.get(str(item.get("family")), {}).get("response_class"),
                "input_domains": family_map.get(str(item.get("family")), {}).get("input_domains"),
            }
            for item in hypotheses
            if isinstance(item, dict)
        ],
        "pairwise": pairwise,
        "runner_contract": runner_rows,
        "checks": checks,
        "failed_checks": failed,
        "policy": {
            "current_queue_mutation_allowed": False,
            "fifth_hypothesis_allowed": False,
            "next_queue_allowed_only_after_current_queue_adjudication": True,
            "no_parameter_expansion": True,
            "no_snapshot_opening": True,
        },
        "next_action": (
            "Do not add another hypothesis to this queue. Keep all four locked until the first exact sealed snapshot is available."
            if not failed
            else "Repair taxonomy or runner-contract mismatches without opening the snapshot or expanding parameters."
        ),
        "runtime_boundary": {
            "audit_only": True,
            "changes_queue": False,
            "opens_snapshot": False,
            "opens_validation": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Microstructure Preregistration Independence Audit",
        "",
        f"Generated: `{report.get('generated_at')}`",
        f"Decision: `{report.get('decision')}`",
        f"Can trade: `{report.get('can_trade')}`",
        "",
        "## Summary",
        "",
    ]
    for name, value in (report.get("summary") or {}).items():
        lines.append(f"- `{name}`: `{value}`")
    lines.extend(["", "## Mechanisms", ""])
    for item in report.get("families") or []:
        lines.append(
            f"- `{item.get('hypothesis_id')}`: `{item.get('primary_mechanism')}` / "
            f"`{item.get('event_archetype')}` / `{item.get('response_class')}`."
        )
    lines.extend(["", "## Pairwise Overlap", ""])
    for item in report.get("pairwise") or []:
        lines.append(
            f"- `{item.get('left')}` vs `{item.get('right')}`: Jaccard `{item.get('input_domain_jaccard')}`, "
            f"shared `{item.get('shared_input_domains')}`, pass `{item.get('pass')}`."
        )
    lines.extend(["", "## Checks", ""])
    for name, passed in (report.get("checks") or {}).items():
        lines.append(f"- `{name}`: `{passed}`")
    lines.extend(["", "## Decision", "", report.get("next_action") or "", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit mechanism independence of the locked microstructure queue.")
    parser.add_argument("--queue", default="configs/CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE.json")
    parser.add_argument("--taxonomy", default="configs/CROSS_VENUE_MICROSTRUCTURE_MECHANISM_TAXONOMY.json")
    parser.add_argument("--runner-contract", default="configs/CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT.json")
    parser.add_argument("--out-prefix", default="docs/MICROSTRUCTURE_PREREG_INDEPENDENCE_AUDIT_2026-07-10")
    args = parser.parse_args()
    report = build_report(resolve_path(args.queue), resolve_path(args.taxonomy), resolve_path(args.runner_contract))
    prefix = resolve_path(args.out_prefix)
    write_json(prefix.with_suffix(".json"), report)
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "summary": report["summary"],
                "failed_checks": report["failed_checks"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not report["failed_checks"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

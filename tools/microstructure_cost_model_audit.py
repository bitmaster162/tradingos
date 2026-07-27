#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
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


def sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def latest_drill_report(root: Path) -> Path | None:
    candidates = list((root / "docs").glob("CROSS_VENUE_MICROSTRUCTURE_SEAL_PIPELINE_DRILL_*.json"))
    return max(candidates, key=lambda item: item.stat().st_mtime_ns) if candidates else None


def load_module(path: Path, experiment: str) -> ModuleType:
    name = f"_microstructure_cost_audit_{experiment}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot_load_module:{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def numeric_assignment(source: str, name: str) -> float | None:
    match = re.search(rf"^\s*{re.escape(name)}\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*$", source, re.MULTILINE)
    return float(match.group(1)) if match else None


def close(left: Any, right: Any, tolerance: float = 1e-9) -> bool:
    try:
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=tolerance)
    except (TypeError, ValueError):
        return False


def gate_probe(module: ModuleType, gate: dict[str, Any]) -> dict[str, Any]:
    boundary = {
        "trades": int(gate.get("min_trades") or 0),
        "mean_net_bps": float(gate.get("min_mean_net_bps") or 0.0),
        "positive_folds": int(gate.get("min_positive_folds") or 0),
        "max_drawdown_bps": float(gate.get("max_drawdown_bps") or 0.0),
        "bootstrap_probability_mean_gt_0": float(gate.get("screening_bootstrap_probability") or 0.0),
        "stress_mean_net_bps": 0.000001,
    }
    boundary_pass = bool(module.train_gate_pass(dict(boundary)))
    failure_cases = {
        "trades": boundary["trades"] - 1,
        "mean_net_bps": boundary["mean_net_bps"] - 0.000001,
        "positive_folds": boundary["positive_folds"] - 1,
        "max_drawdown_bps": boundary["max_drawdown_bps"] - 0.000001,
        "bootstrap_probability_mean_gt_0": boundary["bootstrap_probability_mean_gt_0"] - 0.000001,
        "stress_mean_net_bps": 0.0,
    }
    rejects = {}
    for field, value in failure_cases.items():
        candidate = dict(boundary)
        candidate[field] = value
        rejects[field] = not bool(module.train_gate_pass(candidate))
    return {
        "boundary_pass": boundary_pass,
        "rejects_each_below_gate": rejects,
        "pass": boundary_pass and all(rejects.values()),
    }


def audit_script(
    experiment: str,
    script_path: Path,
    hypothesis: dict[str, Any],
    expected_costs: dict[str, Any],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "experiment": experiment,
        "hypothesis_id": hypothesis.get("hypothesis_id"),
        "script": portable(script_path),
        "script_sha256": sha256_file(script_path),
        "script_exists": script_path.is_file(),
    }
    if not script_path.is_file():
        row["checks"] = {"script_exists": False}
        row["pass"] = False
        return row

    source = script_path.read_text(encoding="utf-8-sig")
    base_per_side = numeric_assignment(source, "per_side_cost")
    stress_extra = numeric_assignment(source, "stress_extra")
    expected_base = float(expected_costs.get("fee_and_slippage_bps_per_side") or 0.0)
    expected_extra = float(expected_costs.get("stress_extra_bps_per_side") or 0.0)
    expected_round_trip = float(expected_costs.get("round_trip_bps") or 0.0)
    expected_stress_round_trip = float(expected_costs.get("stress_round_trip_bps") or 0.0)

    try:
        module = load_module(script_path, experiment)
        control_gross = expected_stress_round_trip + 10.0
        summary = module.summarize_trades(
            [{"gross_bps": control_gross}],
            per_side_cost_bps=expected_base,
            stress_extra_per_side_bps=expected_extra,
        )
        rows = [
            SimpleNamespace(binance_price_first=50.0, binance_price_last=50.0, price_first=50.0, price_last=50.0),
            SimpleNamespace(binance_price_first=100.0, binance_price_last=101.0, price_first=100.0, price_last=101.0),
        ]
        long_return = module.trade_return_bps(rows, 0, 1, "LONG")
        short_return = module.trade_return_bps(rows, 0, 1, "SHORT")
        gate_result = gate_probe(module, hypothesis.get("train_gate") or {})
        dynamic_error = None
    except Exception as exc:  # pragma: no cover - reported as evidence rather than hidden
        summary = {}
        long_return = None
        short_return = None
        gate_result = {"pass": False, "error": str(exc)}
        dynamic_error = str(exc)

    checks = {
        "script_exists": True,
        "base_cost_matches_policy": close(base_per_side, expected_base),
        "stress_extra_matches_policy": close(stress_extra, expected_extra),
        "round_trip_formula_present": "round_trip = per_side_cost_bps * 2.0" in source,
        "stress_round_trip_formula_present": (
            "stress_round_trip = (per_side_cost_bps + stress_extra_per_side_bps) * 2.0" in source
        ),
        "net_subtracts_round_trip_once": "net = [value - round_trip for value in gross]" in source,
        "stress_subtracts_stress_round_trip_once": (
            "stress = [value - stress_round_trip for value in gross]" in source
        ),
        "next_minute_entry_source": "entry_index = signal_index + 1" in source,
        "hold_exit_source": "exit_index = entry_index + hold - 1" in source,
        "overlap_block_source": "blocked_until = exit_index" in source,
        "stress_metric_used_by_gate": 'summary["stress_mean_net_bps"] > 0.0' in source,
        "dynamic_base_net_exact": close(summary.get("mean_net_bps"), control_gross - expected_round_trip),
        "dynamic_stress_net_exact": close(
            summary.get("stress_mean_net_bps"), control_gross - expected_stress_round_trip
        ),
        "dynamic_next_minute_long_exact": close(long_return, 100.0),
        "dynamic_next_minute_short_exact": close(short_return, -100.0),
        "dynamic_train_gate_matches_queue": gate_result.get("pass") is True,
        "no_private_or_order_cli": all(
            token not in source
            for token in ("--api-key", "--api-secret", "send_order(", "create_order(", "place_order(")
        ),
    }
    row.update(
        {
            "source_costs": {
                "fee_and_slippage_bps_per_side": base_per_side,
                "stress_extra_bps_per_side": stress_extra,
                "round_trip_bps": base_per_side * 2.0 if base_per_side is not None else None,
                "stress_round_trip_bps": (
                    (base_per_side + stress_extra) * 2.0
                    if base_per_side is not None and stress_extra is not None
                    else None
                ),
            },
            "control_trade": {
                "gross_bps": control_gross,
                "expected_net_bps": control_gross - expected_round_trip,
                "actual_net_bps": summary.get("mean_net_bps"),
                "expected_stress_net_bps": control_gross - expected_stress_round_trip,
                "actual_stress_net_bps": summary.get("stress_mean_net_bps"),
                "next_minute_long_bps": long_return,
                "next_minute_short_bps": short_return,
            },
            "train_gate_probe": gate_result,
            "dynamic_error": dynamic_error,
            "checks": checks,
            "pass": all(checks.values()),
        }
    )
    return row


def report_paths_from_drill(drill: dict[str, Any]) -> list[Path]:
    active_root = Path(str(drill.get("active_root") or ""))
    run_root = active_root / "_dl" / "research_runs_cross_venue_microstructure"
    if not run_root.is_dir():
        return []
    reports = list(run_root.rglob("REPORT.json"))
    latest_by_experiment: dict[str, Path] = {}
    for path in sorted(reports, key=lambda item: item.stat().st_mtime_ns):
        payload = read_json(path)
        experiment = payload.get("experiment")
        if isinstance(experiment, str):
            latest_by_experiment[experiment] = path
    return list(latest_by_experiment.values())


def audit_dynamic_payload(
    payload: dict[str, Any],
    origin: str,
    report_sha256: str | None,
    expected_costs: dict[str, Any],
    expected_configs: dict[str, int],
) -> dict[str, Any]:
    costs = payload.get("costs") if isinstance(payload.get("costs"), dict) else {}
    splits = payload.get("splits") if isinstance(payload.get("splits"), dict) else {}
    boundary = payload.get("runtime_boundary") if isinstance(payload.get("runtime_boundary"), dict) else {}
    search = payload.get("search") if isinstance(payload.get("search"), dict) else {}
    experiment = str(payload.get("experiment") or "unknown")
    checks = {
        "report_readable": not payload.get("_read_error"),
        "base_cost_matches_policy": close(
            costs.get("fee_and_slippage_bps_per_side"), expected_costs.get("fee_and_slippage_bps_per_side")
        ),
        "stress_extra_matches_policy": close(
            costs.get("stress_extra_bps_per_side"), expected_costs.get("stress_extra_bps_per_side")
        ),
        "round_trip_matches_policy": close(costs.get("round_trip_bps"), expected_costs.get("round_trip_bps")),
        "stress_round_trip_matches_policy": close(
            costs.get("stress_round_trip_bps"), expected_costs.get("stress_round_trip_bps")
        ),
        "all_registered_configs_tested": int(search.get("tested") or -1) == expected_configs.get(experiment),
        "validation_closed": splits.get("validation_opened") is False,
        "oos_closed": splits.get("oos_opened") is False,
        "research_only": boundary.get("research_only") is True,
        "signals_forbidden": boundary.get("signals_allowed") is False,
        "orders_forbidden": boundary.get("orders_allowed") is False,
        "runtime_can_trade_false": boundary.get("can_trade") is False,
        "report_can_trade_false": payload.get("can_trade") is False,
    }
    return {
        "experiment": experiment,
        "path": origin,
        "report_sha256": report_sha256,
        "decision": payload.get("decision"),
        "costs": costs,
        "tested": search.get("tested"),
        "train_qualified": search.get("train_qualified"),
        "checks": checks,
        "pass": all(checks.values()),
    }


def audit_dynamic_report(
    path: Path,
    expected_costs: dict[str, Any],
    expected_configs: dict[str, int],
) -> dict[str, Any]:
    return audit_dynamic_payload(
        read_json(path),
        portable(path),
        sha256_file(path),
        expected_costs,
        expected_configs,
    )


def portable_fixture_reports(path: Path) -> list[tuple[dict[str, Any], str | None]]:
    fixture = read_json(path)
    common = fixture.get("common") if isinstance(fixture.get("common"), dict) else {}
    reports = fixture.get("reports") if isinstance(fixture.get("reports"), list) else []
    rows: list[tuple[dict[str, Any], str | None]] = []
    for item in reports:
        if not isinstance(item, dict):
            continue
        payload = dict(common)
        payload.update({key: value for key, value in item.items() if key != "source_report_sha256"})
        rows.append((payload, str(item.get("source_report_sha256") or "") or None))
    return rows


def build_report(
    policy_path: Path,
    queue_path: Path,
    runner_path: Path,
    drill_path: Path | None,
    portable_evidence_path: Path | None = None,
) -> dict[str, Any]:
    policy = read_json(policy_path)
    queue = read_json(queue_path)
    runner = read_json(runner_path)
    drill = read_json(drill_path) if drill_path else {"_read_error": "drill_report_not_found"}
    expected_experiments = list((policy.get("scope") or {}).get("experiments") or [])
    costs = policy.get("canonical_research_cost_model") or {}
    hypotheses = queue.get("hypotheses") if isinstance(queue.get("hypotheses"), list) else []
    by_experiment = {
        str(item.get("experiment")): item for item in hypotheses if isinstance(item, dict) and item.get("experiment")
    }
    runner_experiments = runner.get("experiments") if isinstance(runner.get("experiments"), dict) else {}
    expected_configs = {
        experiment: int((by_experiment.get(experiment, {}).get("grid") or {}).get("total_configurations") or 0)
        for experiment in expected_experiments
    }

    scripts = []
    for experiment in expected_experiments:
        runner_spec = runner_experiments.get(experiment) if isinstance(runner_experiments.get(experiment), dict) else {}
        script_path = resolve_path(str(runner_spec.get("script") or "missing"))
        scripts.append(audit_script(experiment, script_path, by_experiment.get(experiment, {}), costs))

    drill_report_paths = report_paths_from_drill(drill)
    if drill_report_paths:
        dynamic_reports = [audit_dynamic_report(path, costs, expected_configs) for path in drill_report_paths]
        evidence_mode = "synthetic_drill_artifacts"
    else:
        fixture_rows = portable_fixture_reports(portable_evidence_path) if portable_evidence_path else []
        dynamic_reports = [
            audit_dynamic_payload(
                payload,
                f"{portable(portable_evidence_path)}#{payload.get('experiment')}",
                source_hash,
                costs,
                expected_configs,
            )
            for payload, source_hash in fixture_rows
        ]
        evidence_mode = "portable_synthetic_fixture" if dynamic_reports else "missing"
    dynamic_reports.sort(key=lambda item: item["experiment"])
    report_by_experiment = {item["experiment"]: item for item in dynamic_reports}
    report_contract = runner.get("required_report_contract") or {}
    runtime_policy = policy.get("runtime_boundary") or {}
    governance = policy.get("governance") or {}
    feature_contracts = [by_experiment.get(experiment, {}).get("feature_contract") or {} for experiment in expected_experiments]
    train_gates = [by_experiment.get(experiment, {}).get("train_gate") or {} for experiment in expected_experiments]

    checks = {
        "policy_readable": not policy.get("_read_error"),
        "queue_readable": not queue.get("_read_error"),
        "runner_contract_readable": not runner.get("_read_error"),
        "synthetic_evidence_available": evidence_mode != "missing",
        "exact_four_experiments_scoped": len(expected_experiments) == 4 and len(set(expected_experiments)) == 4,
        "queue_matches_policy_scope": set(by_experiment) == set(expected_experiments),
        "runner_matches_policy_scope": set(runner_experiments) == set(expected_experiments),
        "runner_scripts_locked": all(
            (runner_experiments.get(experiment) or {}).get("implementation_status") == "implemented_locked"
            for experiment in expected_experiments
        ),
        "completed_minute_features_only": all(item.get("uses_completed_minutes_only") is True for item in feature_contracts),
        "queue_next_minute_entry_matches_policy": all(
            item.get("entry_delay") == (policy.get("execution_proxy") or {}).get("entry_delay")
            for item in feature_contracts
        ),
        "queue_requires_positive_cost_stress": all(item.get("cost_stress_positive") is True for item in train_gates),
        "stress_cost_above_base": float(costs.get("stress_round_trip_bps") or 0.0)
        > float(costs.get("round_trip_bps") or 0.0)
        > 0.0,
        "all_source_audits_pass": len(scripts) == 4 and all(item.get("pass") is True for item in scripts),
        "four_synthetic_reports_found": len(dynamic_reports) == 4
        and set(report_by_experiment) == set(expected_experiments),
        "all_synthetic_reports_pass": len(dynamic_reports) == 4
        and all(item.get("pass") is True for item in dynamic_reports),
        "runner_requires_costs_field": "costs" in (report_contract.get("top_level_required_fields") or []),
        "runner_requires_cost_stress": report_contract.get("must_report_cost_stress") is True,
        "audit_does_not_change_preregistration": governance.get("changes_preregistration") is False,
        "candidate_execution_overlay_required": (
            governance.get("candidate_specific_execution_overlay_required_before_paper_review") is True
        ),
        "runtime_boundary_safe": all(
            runtime_policy.get(name) is False
            for name in ("credentials_allowed", "alerts_allowed", "signals_allowed", "paper_entries_allowed", "orders_allowed", "can_trade")
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    decision = (
        "cost_model_consistent_research_only_execution_overlay_required"
        if not failed
        else "cost_model_audit_failed_research_results_not_promotable"
    )
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "tool": "microstructure_cost_model_audit",
        "decision": decision,
        "assurance_level": "research_cost_accounting_proven_execution_realism_not_proven" if not failed else "failed",
        "source_paths": {
            "policy": portable(policy_path),
            "queue": portable(queue_path),
            "runner_contract": portable(runner_path),
            "synthetic_drill": portable(drill_path) if drill_path else None,
            "portable_synthetic_evidence": portable(portable_evidence_path) if portable_evidence_path else None,
        },
        "canonical_research_cost_model": costs,
        "summary": {
            "experiments": len(expected_experiments),
            "source_audits_passed": sum(item.get("pass") is True for item in scripts),
            "synthetic_reports_passed": sum(item.get("pass") is True for item in dynamic_reports),
            "total_registered_configurations": sum(expected_configs.values()),
            "base_round_trip_bps": costs.get("round_trip_bps"),
            "stress_round_trip_bps": costs.get("stress_round_trip_bps"),
            "candidate_specific_execution_overlay_required": True,
            "synthetic_evidence_mode": evidence_mode,
        },
        "source_audits": scripts,
        "synthetic_report_audits": dynamic_reports,
        "checks": checks,
        "failed_checks": failed,
        "known_execution_gaps": policy.get("known_execution_gaps") or [],
        "governance_verdict": {
            "research_screening_costs_consistent": not failed,
            "execution_realism_proven": False,
            "paper_review_allowed": False,
            "reason": "The bundled constant-cost baseline is coherent, but it does not model fills, size, queueing, rejects or market impact.",
            "next_required_gate": "candidate-specific execution overlay using observed spread, fee tier, latency, fill probability and size-dependent slippage",
        },
        "runtime_boundary": runtime_policy,
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    verdict = report.get("governance_verdict") or {}
    lines = [
        "# Microstructure Cost Model Audit",
        "",
        f"Generated: `{report.get('generated_at')}`",
        f"Decision: `{report.get('decision')}`",
        f"Assurance: `{report.get('assurance_level')}`",
        f"Can trade: `{report.get('can_trade')}`",
        "",
        "## Result",
        "",
        f"- Scripts passed: `{summary.get('source_audits_passed')}/{summary.get('experiments')}`.",
        f"- Synthetic reports passed: `{summary.get('synthetic_reports_passed')}/{summary.get('experiments')}`.",
        f"- Registered configurations covered: `{summary.get('total_registered_configurations')}`.",
        f"- Base cost: `{summary.get('base_round_trip_bps')} bps round-trip`.",
        f"- Stress cost: `{summary.get('stress_round_trip_bps')} bps round-trip`.",
        "",
        "## Checks",
        "",
    ]
    for name, passed in (report.get("checks") or {}).items():
        lines.append(f"- `{name}`: `{passed}`")
    lines.extend(["", "## Per Experiment", ""])
    for item in report.get("source_audits") or []:
        control = item.get("control_trade") or {}
        lines.append(
            f"- `{item.get('experiment')}`: pass `{item.get('pass')}`, "
            f"net `{control.get('actual_net_bps')}`, stress-net `{control.get('actual_stress_net_bps')}`, "
            f"next-minute long `{control.get('next_minute_long_bps')}` bps."
        )
    lines.extend(["", "## Execution Gaps", ""])
    for item in report.get("known_execution_gaps") or []:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Governance Verdict",
            "",
            f"Research cost accounting consistent: `{verdict.get('research_screening_costs_consistent')}`.",
            f"Execution realism proven: `{verdict.get('execution_realism_proven')}`.",
            f"Paper review allowed: `{verdict.get('paper_review_allowed')}`.",
            "",
            verdict.get("reason") or "",
            "",
            f"Next gate: {verdict.get('next_required_gate')}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit cost accounting across the four locked microstructure studies.")
    parser.add_argument("--policy", default="configs/CROSS_VENUE_MICROSTRUCTURE_COST_AUDIT_POLICY.json")
    parser.add_argument("--queue", default="configs/CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE.json")
    parser.add_argument("--runner-contract", default="configs/CROSS_VENUE_MICROSTRUCTURE_RUNNER_CONTRACT.json")
    parser.add_argument("--synthetic-drill")
    parser.add_argument("--portable-evidence")
    parser.add_argument("--out-prefix", default="docs/MICROSTRUCTURE_COST_MODEL_AUDIT_2026-07-11")
    args = parser.parse_args()
    drill_path = resolve_path(args.synthetic_drill) if args.synthetic_drill else latest_drill_report(ROOT)
    policy_path = resolve_path(args.policy)
    policy = read_json(policy_path)
    evidence_value = args.portable_evidence or (policy.get("scope") or {}).get("portable_synthetic_evidence")
    report = build_report(
        policy_path,
        resolve_path(args.queue),
        resolve_path(args.runner_contract),
        drill_path,
        resolve_path(evidence_value) if evidence_value else None,
    )
    prefix = resolve_path(args.out_prefix)
    write_json(prefix.with_suffix(".json"), report)
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "source_audits_passed": report["summary"]["source_audits_passed"],
                "synthetic_reports_passed": report["summary"]["synthetic_reports_passed"],
                "failed_checks": report["failed_checks"],
                "can_trade": report["can_trade"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not report["failed_checks"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

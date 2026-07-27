#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs" / "ARENA_PAPER_EDGE_CONTRACT.json"


@dataclass(frozen=True)
class Finding:
    severity: str
    id: str
    title: str
    status: str
    detail: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(walk_strings(item))
        return out
    if isinstance(value, dict):
        out = []
        for item in value.values():
            out.extend(walk_strings(item))
        return out
    return []


def add_if(findings: list[Finding], condition: bool, *, severity: str, fid: str, title: str, detail: str) -> None:
    if condition:
        findings.append(Finding(severity=severity, id=fid, title=title, status="open", detail=detail))


def validate_contract(contract: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    execution = contract.get("execution_boundary", {})
    risk_gate = contract.get("risk_gate", {})
    gates = contract.get("validation_gates", {})
    tasks = contract.get("edge_tasks", [])

    add_if(
        findings,
        contract.get("paper_only") is not True,
        severity="P0",
        fid="paper_only_not_true",
        title="Paper-only boundary is not explicitly enabled",
        detail="The contract must set paper_only=true.",
    )
    add_if(
        findings,
        contract.get("can_trade") is not False,
        severity="P0",
        fid="can_trade_not_false",
        title="Contract must not grant trade permission",
        detail="The contract must set can_trade=false.",
    )
    add_if(
        findings,
        execution.get("real_orders_allowed") is not False,
        severity="P0",
        fid="real_orders_not_disabled",
        title="Real orders are not explicitly disabled",
        detail="execution_boundary.real_orders_allowed must be false.",
    )
    add_if(
        findings,
        execution.get("remote_deploy_allowed_by_this_repo") is not False,
        severity="P1",
        fid="remote_deploy_boundary_missing",
        title="Remote deploy boundary is not explicit",
        detail="This local repo should not imply Arena remote deployment.",
    )
    add_if(
        findings,
        set(risk_gate.get("allowed_actions", [])) != {"buy", "sell", "hold"},
        severity="P0",
        fid="risk_actions_invalid",
        title="Risk gate action enum is invalid",
        detail="allowed_actions must be exactly buy/sell/hold.",
    )
    try:
        max_size = float(risk_gate.get("max_size"))
    except (TypeError, ValueError):
        max_size = 999.0
    add_if(
        findings,
        max_size > 0.5,
        severity="P0",
        fid="risk_size_cap_too_high",
        title="Risk gate size cap exceeds Arena limit",
        detail=f"max_size={max_size}; expected <=0.5.",
    )
    add_if(
        findings,
        int(gates.get("min_train_trades", 0)) < 40,
        severity="P1",
        fid="min_train_trades_too_low",
        title="Validation min-trades gate is weaker than Codex discipline",
        detail="min_train_trades must be at least 40.",
    )
    add_if(
        findings,
        int(gates.get("min_positive_folds", 0)) < 3,
        severity="P1",
        fid="min_positive_folds_too_low",
        title="Validation fold gate is weaker than Codex discipline",
        detail="min_positive_folds must be at least 3.",
    )
    add_if(
        findings,
        float(gates.get("screening_bootstrap_probability", 0.0)) < 0.95,
        severity="P1",
        fid="bootstrap_gate_too_low",
        title="Bootstrap probability gate is weaker than Codex discipline",
        detail="screening_bootstrap_probability must be at least 0.95.",
    )
    for key in [
        "validation_before_oos",
        "oos_before_paper",
        "selection_frozen_before_validation",
        "no_retune_after_validation",
        "costs_required",
        "can_trade_flag_required",
    ]:
        add_if(
            findings,
            gates.get(key) is not True,
            severity="P1",
            fid=f"gate_{key}_missing",
            title=f"Validation gate {key} is not enabled",
            detail=f"validation_gates.{key} must be true.",
        )

    add_if(
        findings,
        not isinstance(tasks, list) or not tasks,
        severity="P1",
        fid="edge_tasks_missing",
        title="No edge tasks were captured from handoff",
        detail="edge_tasks must contain the Claude/Arena task map.",
    )
    for idx, task in enumerate(tasks if isinstance(tasks, list) else []):
        task_id = task.get("task_id", f"idx_{idx}")
        add_if(
            findings,
            task.get("can_trade") is not False,
            severity="P0",
            fid=f"task_{task_id}_can_trade_not_false",
            title=f"Task {task_id} grants trade permission",
            detail="Every imported handoff task must remain can_trade=false until independently promoted.",
        )
        add_if(
            findings,
            task.get("integration_status") in {"live", "live_running", "paper_running", "promoted"},
            severity="P0",
            fid=f"task_{task_id}_status_too_strong",
            title=f"Task {task_id} is marked stronger than local proof allows",
            detail=f"integration_status={task.get('integration_status')}",
        )

    sensitive_markers = [
        "ssh -i",
        "password",
        "pass=",
        "dbname=",
        "34.70.",
        "35.217.",
        "ed25519",
    ]
    strings = "\n".join(walk_strings(contract)).lower()
    leaked = [marker for marker in sensitive_markers if marker in strings]
    add_if(
        findings,
        bool(leaked),
        severity="P1",
        fid="sensitive_remote_details_in_contract",
        title="Contract contains sensitive or remote access details",
        detail="Matched markers: " + ", ".join(leaked),
    )
    return findings


def build_report(config_path: Path) -> dict[str, Any]:
    contract = load_json(config_path)
    findings = validate_contract(contract)
    open_p0 = sum(1 for item in findings if item.severity == "P0")
    open_p1 = sum(1 for item in findings if item.severity == "P1")
    return {
        "generated_utc": now_iso(),
        "config_path": portable_path(config_path),
        "decision": "pass_contract_safe_for_local_docs" if not findings else "fail_contract_requires_fix",
        "can_trade": False,
        "paper_only": contract.get("paper_only") is True,
        "summary": {
            "edge_tasks": len(contract.get("edge_tasks", [])) if isinstance(contract.get("edge_tasks"), list) else 0,
            "open_findings": len(findings),
            "open_p0": open_p0,
            "open_p1": open_p1,
        },
        "findings": [item.__dict__ for item in findings],
        "drift_notes": contract.get("drift_notes", []),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Arena Paper Edge Contract Audit",
        "",
        f"- Generated: `{report['generated_utc']}`",
        f"- Decision: `{report['decision']}`",
        f"- Contract: `{report['config_path']}`",
        f"- Paper only: `{str(report['paper_only']).lower()}`",
        f"- Can trade: `{str(report['can_trade']).lower()}`",
        "",
        "## Summary",
        "",
        f"- Edge tasks captured: `{report['summary']['edge_tasks']}`",
        f"- Open findings: `{report['summary']['open_findings']}`",
        f"- P0/P1: `{report['summary']['open_p0']}` / `{report['summary']['open_p1']}`",
        "",
        "## Findings",
        "",
    ]
    if report["findings"]:
        for finding in report["findings"]:
            lines.extend(
                [
                    f"- `{finding['severity']}` `{finding['id']}` - {finding['title']}",
                    f"  Detail: {finding['detail']}",
                ]
            )
    else:
        lines.append("- No open findings. Contract is safe as a local paper-only spec.")
    lines.extend(["", "## Drift Notes", ""])
    for note in report.get("drift_notes", []):
        lines.append(f"- `{note.get('severity', 'P?')}` `{note.get('id')}` - {note.get('note')}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit local Claude/Arena paper-edge contract boundaries.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to ARENA_PAPER_EDGE_CONTRACT.json")
    parser.add_argument("--out-prefix", default="docs/ARENA_PAPER_EDGE_CONTRACT_AUDIT_2026-06-30")
    args = parser.parse_args()

    config_path = resolve_path(args.config)
    out_prefix = resolve_path(args.out_prefix)
    report = build_report(config_path)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    (out_prefix.with_suffix(".json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_prefix.with_suffix(".md")).write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "out": portable_path(out_prefix.with_suffix(".json"))}, ensure_ascii=False, indent=2))
    return 0 if report["decision"].startswith("pass_") else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.execution_realism_promotion_gate import build_report as build_gate  # noqa: E402
from tools.execution_realism_shadow_overlay import build_report as build_overlay  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def compact_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except (OSError, ValueError):
        return str(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_ledger(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["r_net", "side", "obi"])
        writer.writeheader()
        for _ in range(20):
            writer.writerow({"r_net": "0.20", "side": "LONG", "obi": "-0.80"})


def build_drill(policy_path: Path, work_root: Path) -> dict[str, Any]:
    run_dir = work_root / compact_stamp()
    run_dir.mkdir(parents=True, exist_ok=False)
    candidate_path = run_dir / "candidate.json"
    overlay_path = run_dir / "overlay.json"
    frontier_path = run_dir / "frontier.json"
    candidate = {
        "decision": "oos_pass_synthetic_candidate",
        "can_trade": False,
        "runtime_boundary": {
            "synthetic_only": True,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
    }
    write_json(candidate_path, candidate)
    ledgers = []
    for index in range(3):
        path = run_dir / f"candidate_{index}.csv"
        write_ledger(path)
        ledgers.append(str(path))
    frontier = {
        "decision": "candidate_family_needs_forward_proof",
        "summary": {"promotable": 1, "observer_only": 0, "unsafe": 0},
        "families": [
            {
                "family": "synthetic_candidate_family",
                "status": "candidate_needs_forward_proof",
                "path": str(candidate_path),
                "can_trade": False,
            }
        ],
        "can_trade": False,
    }
    write_json(frontier_path, frontier)
    overlay = build_overlay(
        None,
        ledgers,
        0.0,
        candidate_report=candidate_path,
        candidate_family="synthetic_candidate_family",
    )
    write_json(overlay_path, overlay)
    snapshot_path = run_dir / "paper_snapshot_contract_fixture.json"
    snapshot = {
        "snapshot_id": "paper_snapshot_contract_fixture",
        "snapshot_kind": "paper_account",
        "generated_at": now_iso(),
        "source_mode": "local_paper_state",
        "synthetic": False,
        "fixture_only": True,
        "can_trade": False,
    }
    write_json(snapshot_path, snapshot)
    snapshot_hash = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    stress_gate_path = run_dir / "portfolio_stress_gate_contract_fixture.json"
    write_json(
        stress_gate_path,
        {
            "decision": "portfolio_stress_promotion_gate_passed_manual_review_only",
            "inputs": {
                "snapshot_path": str(snapshot_path),
                "bound_snapshot_sha256": snapshot_hash,
                "current_snapshot_sha256": snapshot_hash,
            },
            "promotion": {
                "portfolio_stress_gate_passed": True,
                "paper_design_review_allowed": True,
                "paper_execution_allowed": False,
                "live_execution_allowed": False,
            },
            "runtime_boundary": {"orders_allowed": False, "can_trade": False},
            "can_trade": False,
        },
    )
    positive = build_gate(
        policy_path,
        overlay_path,
        frontier_path,
        portfolio_stress_gate_path=stress_gate_path,
    )

    candidate["tampered_after_overlay"] = True
    write_json(candidate_path, candidate)
    tampered = build_gate(
        policy_path,
        overlay_path,
        frontier_path,
        portfolio_stress_gate_path=stress_gate_path,
    )

    checks = {
        "binding_created": overlay.get("candidate_binding", {}).get("present") is True,
        "matching_binding_allows_design_review": positive.get("promotion", {}).get("paper_design_review_allowed") is True,
        "matching_portfolio_stress_binding_present": positive.get("promotion", {}).get("portfolio_stress_gate_present") is True,
        "matching_binding_keeps_paper_execution_false": positive.get("promotion", {}).get("paper_execution_allowed") is False,
        "matching_binding_keeps_live_execution_false": positive.get("promotion", {}).get("live_execution_allowed") is False,
        "matching_binding_keeps_can_trade_false": positive.get("can_trade") is False,
        "tamper_detected": tampered.get("candidate_binding_audit", {}).get("checks", {}).get(
            "candidate_report_hash_matches_binding"
        )
        is False,
        "tamper_blocks_design_review": tampered.get("promotion", {}).get("paper_design_review_allowed") is False,
        "tamper_keeps_can_trade_false": tampered.get("can_trade") is False,
    }
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "tool": "execution_realism_candidate_binding_drill",
        "decision": "execution_candidate_binding_drill_passed" if all(checks.values()) else "execution_candidate_binding_drill_failed",
        "work_dir": portable(run_dir),
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "positive_case": {
            "decision": positive.get("decision"),
            "candidate_specific_overlay_present": positive.get("promotion", {}).get("candidate_specific_overlay_present"),
            "paper_design_review_allowed": positive.get("promotion", {}).get("paper_design_review_allowed"),
            "paper_execution_allowed": positive.get("promotion", {}).get("paper_execution_allowed"),
            "live_execution_allowed": positive.get("promotion", {}).get("live_execution_allowed"),
            "can_trade": positive.get("can_trade"),
        },
        "tamper_case": {
            "decision": tampered.get("decision"),
            "binding_failed_checks": tampered.get("candidate_binding_audit", {}).get("failed_checks"),
            "paper_design_review_allowed": tampered.get("promotion", {}).get("paper_design_review_allowed"),
            "can_trade": tampered.get("can_trade"),
        },
        "scope": "synthetic contract drill only; portfolio snapshot is a fixture and does not create or promote a real strategy candidate",
        "runtime_boundary": {
            "synthetic_only": True,
            "network_required": False,
            "credentials_allowed": False,
            "alerts_allowed": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Execution Realism Candidate Binding Drill",
        "",
        f"Generated: `{report.get('generated_at')}`",
        f"Decision: `{report.get('decision')}`",
        f"Can trade: `{report.get('can_trade')}`",
        "",
        "## Purpose",
        "",
        report.get("scope") or "",
        "",
        "## Checks",
        "",
    ]
    for name, passed in (report.get("checks") or {}).items():
        lines.append(f"- `{name}`: `{passed}`")
    lines.extend(
        [
            "",
            "## Matching Case",
            "",
            f"`{report.get('positive_case')}`",
            "",
            "## Tamper Case",
            "",
            f"`{report.get('tamper_case')}`",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove candidate/report/ledger binding and tamper rejection.")
    parser.add_argument("--policy", default="configs/EXECUTION_REALISM_PROMOTION_GATE_POLICY.json")
    parser.add_argument("--work-root", default="_dl/runtime_drills/execution_candidate_binding")
    parser.add_argument("--out-prefix", default="docs/EXECUTION_REALISM_CANDIDATE_BINDING_DRILL_2026-07-11")
    args = parser.parse_args()
    report = build_drill(resolve_path(args.policy), resolve_path(args.work_root))
    prefix = resolve_path(args.out_prefix)
    write_json(prefix.with_suffix(".json"), report)
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "failed_checks": report["failed_checks"],
                "positive_review_allowed": report["positive_case"]["paper_design_review_allowed"],
                "tamper_review_allowed": report["tamper_case"]["paper_design_review_allowed"],
                "can_trade": report["can_trade"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not report["failed_checks"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

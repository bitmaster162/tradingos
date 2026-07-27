#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FRONTIER_PATTERN = "STRATEGY_RESEARCH_FRONTIER_MATRIX_*.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc)}
    return payload if isinstance(payload, dict) else {"_read_error": "json_root_not_object"}


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out


def sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def parse_generated_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def frontier_timestamp(path: Path) -> datetime:
    payload = read_json(path)
    generated_at = parse_generated_at(payload.get("generated_at"))
    if generated_at is not None:
        return generated_at
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return datetime.min.replace(tzinfo=timezone.utc)


def latest_frontier(docs_dir: Path) -> Path | None:
    candidates = [
        path
        for path in docs_dir.glob(FRONTIER_PATTERN)
        if path.is_file() and not read_json(path).get("_read_error")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: (frontier_timestamp(path), path.name))


def same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def bound_path(value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def candidate_binding_audit(overlay: dict[str, Any], frontier: dict[str, Any]) -> dict[str, Any]:
    families = frontier.get("families") if isinstance(frontier.get("families"), list) else []
    promotable = [
        item
        for item in families
        if isinstance(item, dict) and item.get("status") == "candidate_needs_forward_proof"
    ]
    binding = overlay.get("candidate_binding") if isinstance(overlay.get("candidate_binding"), dict) else {}
    family = binding.get("candidate_family")
    matching = [item for item in promotable if item.get("family") == family]
    candidate_path = bound_path(binding.get("candidate_report"))
    frontier_path = bound_path(matching[0].get("path")) if len(matching) == 1 else None
    candidate_payload = read_json(candidate_path) if candidate_path else {"_read_error": "candidate_path_missing"}
    ledger_rows = binding.get("ledgers") if isinstance(binding.get("ledgers"), list) else []
    ledger_checks = []
    for item in ledger_rows:
        item = item if isinstance(item, dict) else {}
        path = bound_path(item.get("path"))
        current_hash = sha256_file(path) if path else None
        ledger_checks.append(
            {
                "path": item.get("path"),
                "bound_sha256": item.get("sha256"),
                "current_sha256": current_hash,
                "pass": current_hash is not None and current_hash == item.get("sha256"),
            }
        )
    boundary = overlay.get("runtime_boundary") if isinstance(overlay.get("runtime_boundary"), dict) else {}
    checks = {
        "binding_declared_present": binding.get("present") is True,
        "exactly_one_matching_promotable_family": len(matching) == 1,
        "candidate_report_path_matches_frontier": candidate_path is not None
        and frontier_path is not None
        and candidate_path.resolve() == frontier_path.resolve(),
        "candidate_report_hash_matches_binding": candidate_path is not None
        and sha256_file(candidate_path) is not None
        and sha256_file(candidate_path) == binding.get("candidate_report_sha256"),
        "candidate_report_can_trade_false": candidate_payload.get("can_trade") is False,
        "candidate_ledgers_present": bool(ledger_checks),
        "candidate_ledger_hashes_match": bool(ledger_checks) and all(item["pass"] for item in ledger_checks),
        "overlay_does_not_change_candidate_parameters": boundary.get("does_not_change_candidate_parameters") is True,
    }
    return {
        "applicable": bool(promotable),
        "candidate_family": family,
        "candidate_report": binding.get("candidate_report"),
        "candidate_report_sha256": binding.get("candidate_report_sha256"),
        "promotable_families": [item.get("family") for item in promotable],
        "matching_frontier_row": matching[0] if len(matching) == 1 else None,
        "ledger_checks": ledger_checks,
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "pass": bool(promotable) and all(checks.values()),
    }


def portfolio_stress_gate_audit(gate_path: Path | None) -> dict[str, Any]:
    gate = read_json(gate_path) if gate_path else {"_read_error": "portfolio_stress_gate_path_missing"}
    inputs = gate.get("inputs") if isinstance(gate.get("inputs"), dict) else {}
    promotion = gate.get("promotion") if isinstance(gate.get("promotion"), dict) else {}
    boundary = gate.get("runtime_boundary") if isinstance(gate.get("runtime_boundary"), dict) else {}
    snapshot_path = bound_path(inputs.get("snapshot_path"))
    current_snapshot_hash = sha256_file(snapshot_path) if snapshot_path else None
    checks = {
        "gate_report_present": gate_path is not None and gate_path.is_file() and not gate.get("_read_error"),
        "gate_decision_passed": gate.get("decision") == "portfolio_stress_promotion_gate_passed_manual_review_only",
        "portfolio_stress_gate_passed": promotion.get("portfolio_stress_gate_passed") is True,
        "paper_design_review_allowed": promotion.get("paper_design_review_allowed") is True,
        "paper_execution_false": promotion.get("paper_execution_allowed") is False,
        "live_execution_false": promotion.get("live_execution_allowed") is False,
        "gate_can_trade_false": gate.get("can_trade") is False,
        "gate_orders_false": boundary.get("orders_allowed") is False,
        "snapshot_path_present": snapshot_path is not None and snapshot_path.is_file(),
        "snapshot_hash_matches_bound": current_snapshot_hash is not None
        and current_snapshot_hash == inputs.get("bound_snapshot_sha256"),
        "snapshot_hash_matches_gate_current": current_snapshot_hash is not None
        and current_snapshot_hash == inputs.get("current_snapshot_sha256"),
    }
    return {
        "path": portable(gate_path) if gate_path else None,
        "sha256": sha256_file(gate_path) if gate_path else None,
        "snapshot_path": portable(snapshot_path) if snapshot_path else None,
        "bound_snapshot_sha256": inputs.get("bound_snapshot_sha256"),
        "current_snapshot_sha256": current_snapshot_hash,
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "pass": all(checks.values()),
    }
def boundary_false(payload: dict[str, Any]) -> bool:
    boundary = payload.get("runtime_boundary") if isinstance(payload.get("runtime_boundary"), dict) else {}
    return (
        payload.get("can_trade") is False
        and boundary.get("alerts_allowed") is False
        and boundary.get("signals_allowed") is False
        and boundary.get("paper_entries_allowed") is False
        and boundary.get("orders_allowed") is False
    )


def build_report(
    policy_path: Path,
    overlay_path: Path,
    frontier_path: Path,
    latest_frontier_path: Path | None = None,
    portfolio_stress_gate_path: Path | None = None,
) -> dict[str, Any]:
    policy = read_json(policy_path)
    overlay = read_json(overlay_path)
    frontier = read_json(frontier_path)
    thresholds = policy.get("thresholds") if isinstance(policy.get("thresholds"), dict) else {}
    candidate_rules = policy.get("candidate_rules") if isinstance(policy.get("candidate_rules"), dict) else {}
    overlay_summary = overlay.get("summary") if isinstance(overlay.get("summary"), dict) else {}
    frontier_summary = frontier.get("summary") if isinstance(frontier.get("summary"), dict) else {}

    original_exp = fnum(overlay_summary.get("original_weighted_expectancy_r"))
    shadow_exp = fnum(overlay_summary.get("shadow_weighted_expectancy_r"))
    shadow_total = fnum(overlay_summary.get("shadow_total_r"))
    retention_ratio = round(shadow_exp / original_exp, 6) if original_exp > 0 else 0.0
    promotable_count = int(frontier_summary.get("promotable") or 0)
    observer_only_count = int(frontier_summary.get("observer_only") or 0)
    binding_audit = candidate_binding_audit(overlay, frontier)
    stress_gate_audit = portfolio_stress_gate_audit(portfolio_stress_gate_path)
    frontier_is_latest = latest_frontier_path is None or same_path(frontier_path, latest_frontier_path)

    checks = {
        "policy_exists": policy_path.is_file() and not policy.get("_read_error"),
        "overlay_exists": overlay_path.is_file() and not overlay.get("_read_error"),
        "frontier_exists": frontier_path.is_file() and not frontier.get("_read_error"),
        "overlay_completed": overlay.get("decision") == "execution_realism_shadow_overlay_completed",
        "overlay_boundary_false": boundary_false(overlay),
        "overlay_does_not_change_decisions": overlay.get("runtime_boundary", {}).get("does_not_change_strategy_decisions") is True
        if isinstance(overlay.get("runtime_boundary"), dict)
        else False,
        "ledgers_analyzed_floor": int(overlay.get("ledgers_analyzed") or 0)
        >= int(thresholds.get("min_ledgers_analyzed") or 0),
        "shadow_expectancy_floor": shadow_exp >= fnum(thresholds.get("min_shadow_weighted_expectancy_r")),
        "shadow_total_positive": shadow_total > fnum(thresholds.get("min_shadow_total_r")),
        "retention_ratio_floor": retention_ratio >= fnum(thresholds.get("min_retention_ratio")),
        "frontier_no_unsafe": int(frontier_summary.get("unsafe") or 0) == 0,
        "frontier_boundary_false": frontier.get("can_trade") is False,
        "frontier_is_latest": frontier_is_latest,
    }
    failed = [name for name, passed in checks.items() if not passed]

    candidate_specific_required = candidate_rules.get("candidate_specific_overlay_required_when_promotable_family_exists") is True
    candidate_specific_overlay_present = binding_audit.get("pass") is True if promotable_count > 0 else False
    portfolio_stress_required = candidate_rules.get("portfolio_stress_gate_required_when_promotable_family_exists") is True
    portfolio_stress_gate_present = stress_gate_audit.get("pass") is True if promotable_count > 0 else False
    if failed:
        decision = "execution_realism_promotion_gate_failed"
        next_action = "fix execution-realism overlay or frontier boundary before any candidate promotion discussion"
    elif promotable_count <= 0:
        decision = "execution_realism_gate_ready_no_promotable_candidate"
        next_action = "keep gate as mandatory; continue observer-only forward collection and new preregistered research"
    elif candidate_specific_required and not candidate_specific_overlay_present:
        decision = "execution_realism_gate_blocks_promotable_candidate_until_candidate_specific_overlay"
        next_action = "run candidate-specific execution-realism overlay before paper-design review"
    elif portfolio_stress_required and not portfolio_stress_gate_present:
        decision = "execution_realism_gate_blocks_promotable_candidate_until_portfolio_stress"
        next_action = "bind a fresh non-synthetic local paper-account snapshot to a passing portfolio stress gate"
    else:
        decision = "execution_realism_gate_passed_manual_review_required"
        next_action = "manual review required before paper-design review; no paper entries or orders"

    promotion = {
        "generic_execution_realism_gate_passed": not failed,
        "execution_realism_gate_passed": bool(
            not failed
            and (
                promotable_count <= 0
                or not candidate_specific_required
                or candidate_specific_overlay_present
            )
            and (
                promotable_count <= 0
                or not portfolio_stress_required
                or portfolio_stress_gate_present
            )
        ),
        "candidate_family_count": promotable_count,
        "observer_only_family_count": observer_only_count,
        "candidate_specific_overlay_required": candidate_specific_required,
        "candidate_specific_overlay_present": candidate_specific_overlay_present,
        "portfolio_stress_gate_required": portfolio_stress_required,
        "portfolio_stress_gate_present": portfolio_stress_gate_present,
        "candidate_family_under_review": binding_audit.get("candidate_family")
        if candidate_specific_overlay_present
        else None,
        "paper_design_review_allowed": bool(
            not failed
            and promotable_count > 0
            and (not candidate_specific_required or candidate_specific_overlay_present)
            and (not portfolio_stress_required or portfolio_stress_gate_present)
        ),
        "paper_execution_allowed": False,
        "live_execution_allowed": False,
    }

    return {
        "generated_at": now_iso(),
        "tool": "execution_realism_promotion_gate",
        "decision": decision,
        "policy_path": portable(policy_path),
        "overlay_path": portable(overlay_path),
        "frontier_path": portable(frontier_path),
        "frontier_binding": {
            "selected_path": portable(frontier_path),
            "selected_sha256": sha256_file(frontier_path),
            "selected_generated_at": frontier.get("generated_at"),
            "latest_path": portable(latest_frontier_path) if latest_frontier_path else None,
            "latest_sha256": sha256_file(latest_frontier_path) if latest_frontier_path else None,
            "latest_generated_at": read_json(latest_frontier_path).get("generated_at")
            if latest_frontier_path
            else None,
            "selected_is_latest": frontier_is_latest,
        },
        "thresholds": thresholds,
        "metrics": {
            "ledgers_analyzed": overlay.get("ledgers_analyzed"),
            "original_weighted_expectancy_r": original_exp,
            "shadow_weighted_expectancy_r": shadow_exp,
            "shadow_total_r": shadow_total,
            "delta_weighted_expectancy_r": overlay_summary.get("delta_weighted_expectancy_r"),
            "retention_ratio": retention_ratio,
            "frontier_decision": frontier.get("decision"),
            "frontier_summary": frontier_summary,
        },
        "checks": checks,
        "failed_checks": failed,
        "promotion": promotion,
        "candidate_binding_audit": binding_audit,
        "portfolio_stress_gate_audit": stress_gate_audit,
        "policy": {
            "external_replication_not_counted_as_codex_forward_sample": candidate_rules.get(
                "external_replication_not_counted_as_codex_forward_sample"
            )
            is True,
            "manual_review_required_before_paper_design": candidate_rules.get("manual_review_required_before_paper_design")
            is True,
            "portfolio_stress_gate_required_when_promotable_family_exists": portfolio_stress_required,
            "no_parameter_changes": True,
        },
        "next_action": next_action,
        "runtime_boundary": {
            "gate_only": True,
            "alerts_allowed": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report.get("metrics") or {}
    promotion = report.get("promotion") or {}
    frontier_binding = report.get("frontier_binding") or {}
    lines = [
        "# Execution Realism Promotion Gate",
        "",
        f"Generated: `{report.get('generated_at')}`",
        f"Decision: `{report.get('decision')}`",
        f"Can trade: `{report.get('can_trade')}`",
        "",
        "## Boundary",
        "",
        "- Gate-only.",
        "- No alerts, signals, paper entries or orders.",
        "- External replication is not counted as Codex forward sample.",
        "",
        "## Metrics",
        "",
        f"- Ledgers analyzed: `{metrics.get('ledgers_analyzed')}`.",
        f"- Original weighted expectancy R: `{metrics.get('original_weighted_expectancy_r')}`.",
        f"- Shadow weighted expectancy R: `{metrics.get('shadow_weighted_expectancy_r')}`.",
        f"- Retention ratio: `{metrics.get('retention_ratio')}`.",
        f"- Frontier decision: `{metrics.get('frontier_decision')}`.",
        f"- Frontier summary: `{metrics.get('frontier_summary')}`.",
        "",
        "## Frontier Binding",
        "",
        f"- Selected: `{frontier_binding.get('selected_path')}`.",
        f"- Selected SHA-256: `{frontier_binding.get('selected_sha256')}`.",
        f"- Selected generated_at: `{frontier_binding.get('selected_generated_at')}`.",
        f"- Latest: `{frontier_binding.get('latest_path')}`.",
        f"- Selected is latest: `{frontier_binding.get('selected_is_latest')}`.",
        "",
        "## Promotion",
        "",
        f"- Execution-realism gate passed: `{promotion.get('execution_realism_gate_passed')}`.",
        f"- Candidate family count: `{promotion.get('candidate_family_count')}`.",
        f"- Candidate-specific overlay required: `{promotion.get('candidate_specific_overlay_required')}`.",
        f"- Candidate-specific overlay present: `{promotion.get('candidate_specific_overlay_present')}`.",
        f"- Portfolio stress gate required: `{promotion.get('portfolio_stress_gate_required')}`.",
        f"- Portfolio stress gate present: `{promotion.get('portfolio_stress_gate_present')}`.",
        f"- Candidate family under review: `{promotion.get('candidate_family_under_review')}`.",
        f"- Paper-design review allowed: `{promotion.get('paper_design_review_allowed')}`.",
        f"- Paper execution allowed: `{promotion.get('paper_execution_allowed')}`.",
        f"- Live execution allowed: `{promotion.get('live_execution_allowed')}`.",
        "",
        "## Checks",
        "",
    ]
    for name, passed in sorted((report.get("checks") or {}).items()):
        lines.append(f"- `{name}`: `{passed}`")
    lines.extend(["", "## Candidate Binding", ""])
    binding = report.get("candidate_binding_audit") or {}
    lines.append(f"- Applicable: `{binding.get('applicable')}`.")
    lines.append(f"- Pass: `{binding.get('pass')}`.")
    lines.append(f"- Family: `{binding.get('candidate_family')}`.")
    lines.append(f"- Failed checks: `{binding.get('failed_checks')}`.")
    lines.extend(["", "## Portfolio Stress Binding", ""])
    stress = report.get("portfolio_stress_gate_audit") or {}
    lines.append(f"- Pass: `{stress.get('pass')}`.")
    lines.append(f"- Gate: `{stress.get('path')}`.")
    lines.append(f"- Snapshot: `{stress.get('snapshot_path')}`.")
    lines.append(f"- Failed checks: `{stress.get('failed_checks')}`.")
    lines.extend(
        [
            "",
            "## Result",
            "",
            f"- Failed checks: `{report.get('failed_checks')}`.",
            f"- Next action: `{report.get('next_action')}`.",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(report: dict[str, Any], out_prefix: str) -> None:
    prefix = resolve_path(out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Mandatory execution-realism gate before any future promotion.")
    parser.add_argument("--policy", default="configs/EXECUTION_REALISM_PROMOTION_GATE_POLICY.json")
    parser.add_argument("--overlay", default="docs/EXECUTION_REALISM_SHADOW_OVERLAY_2026-07-11.json")
    parser.add_argument("--frontier", default="", help="Explicit frontier override; must still be the latest matrix.")
    parser.add_argument("--docs-dir", default="docs", help="Directory used to resolve the latest frontier matrix.")
    parser.add_argument("--portfolio-stress-gate", default="docs/PORTFOLIO_STRESS_PROMOTION_GATE_2026-07-12.json")
    parser.add_argument("--out-prefix", default="docs/EXECUTION_REALISM_PROMOTION_GATE_2026-07-12_CURRENT_FRONTIER")
    args = parser.parse_args()
    newest_frontier = latest_frontier(resolve_path(args.docs_dir))
    if newest_frontier is None:
        parser.error(f"no valid frontier matrices found in {resolve_path(args.docs_dir)}")
    selected_frontier = resolve_path(args.frontier) if args.frontier.strip() else newest_frontier
    report = build_report(
        resolve_path(args.policy),
        resolve_path(args.overlay),
        selected_frontier,
        latest_frontier_path=newest_frontier,
        portfolio_stress_gate_path=resolve_path(args.portfolio_stress_gate),
    )
    write_outputs(report, args.out_prefix)
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "failed_checks": report["failed_checks"],
                "shadow_weighted_expectancy_r": report["metrics"]["shadow_weighted_expectancy_r"],
                "retention_ratio": report["metrics"]["retention_ratio"],
                "frontier_path": report["frontier_path"],
                "frontier_is_latest": report["checks"]["frontier_is_latest"],
                "paper_design_review_allowed": report["promotion"]["paper_design_review_allowed"],
                "can_trade": report["can_trade"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not report["failed_checks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

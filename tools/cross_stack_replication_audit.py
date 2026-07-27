#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
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
    except ValueError:
        return str(path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc)}
    return payload if isinstance(payload, dict) else {"_read_error": "json_root_not_object"}


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def contains_any(text: str, needles: list[str]) -> bool:
    lower = text.lower()
    return any(needle.lower() in lower for needle in needles)


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def boundary_false(payload: dict[str, Any]) -> bool:
    boundary = payload.get("boundary") if isinstance(payload.get("boundary"), dict) else payload
    return (
        boundary.get("can_trade") is False
        and boundary.get("paper_entries_allowed") is False
        and boundary.get("orders_allowed") is False
    )


def build_report(lock_path: Path, handoff_path: Path, replication_report_path: Path | None = None) -> dict[str, Any]:
    lock = read_json(lock_path)
    note = read_text(handoff_path)
    replication_report = read_json(replication_report_path) if replication_report_path else {}
    meta = lock.get("_meta") if isinstance(lock.get("_meta"), dict) else {}
    params = lock.get("params_from_codex_lock_20260702_NO_RETUNE") if isinstance(lock.get("params_from_codex_lock_20260702_NO_RETUNE"), dict) else {}
    provenance = lock.get("data_provenance") if isinstance(lock.get("data_provenance"), dict) else {}
    thresholds = lock.get("thresholds_match_codex") if isinstance(lock.get("thresholds_match_codex"), dict) else {}
    verdict_rule = lock.get("verdict_rule") if isinstance(lock.get("verdict_rule"), dict) else {}
    execution = lock.get("execution_realism_assumptions") if isinstance(lock.get("execution_realism_assumptions"), dict) else {}
    report_boundary = replication_report.get("boundary") if isinstance(replication_report.get("boundary"), dict) else {}
    report_provenance = replication_report.get("provenance_addendum") if isinstance(replication_report.get("provenance_addendum"), dict) else {}
    report_resolved = replication_report.get("resolved_per_horizon") if isinstance(replication_report.get("resolved_per_horizon"), dict) else {}
    locked_at = parse_dt(meta.get("locked_at"))
    forward_floor = parse_dt(replication_report.get("forward_floor_utc"))

    checks = {
        "lock_exists": lock_path.is_file(),
        "handoff_exists": handoff_path.is_file(),
        "immutable_true": meta.get("immutable") is True,
        "replication_blind_false": meta.get("replication_blind") is False,
        "disclosure_reason_present": bool(str(meta.get("reason") or "").strip()),
        "can_trade_false": meta.get("can_trade") is False and lock.get("can_trade", False) is False,
        "paper_entries_false": meta.get("paper_entries_allowed") is False,
        "orders_false": meta.get("orders_allowed") is False,
        "no_retune_key_present": "NO_RETUNE" in "".join(lock.keys()),
        "forward_only_present": contains_any(str(provenance), ["forward_only", "event_time > locked_at", "no pre-lock"]),
        "context_matches_codex": params.get("context") == "short_liquidation_squeeze",
        "direction_matches_codex": params.get("direction") == "continuation",
        "interval_1h": params.get("interval") == "1h",
        "horizons_match": params.get("horizons_bars") == [1, 2, 4],
        "threshold_min_15": thresholds.get("min_resolved_events_per_horizon") == 15,
        "manual_review_required": thresholds.get("manual_review_before_any_paper_discussion") is True,
        "execution_assumptions_present": bool(execution),
        "verdict_rule_present": all(key in verdict_rule for key in ("pass", "tombstone", "else")),
        "handoff_mentions_live_forward_pipeline": contains_any(
            note,
            ["arena-bybit-liq-ws", "replication forward pipeline is now live", "own live subscription"],
        ),
        "handoff_mentions_forward_floor": contains_any(note, ["forward_floor", "coverage floor", "no lookahead"]),
        "handoff_boundary_false": contains_any(note, ["can_trade=false", "paper_entries_allowed=false", "orders_allowed=false"]),
        "not_merged_into_codex_forward_sample": contains_any(
            note,
            [
                "not merged into your forward sample",
                "not merged into codex forward sample",
                "not merge this into your original lock",
                "independent execution-model external-validity check",
            ],
        ),
    }
    if replication_report_path:
        checks.update(
            {
                "replication_report_exists": replication_report_path.is_file()
                and not replication_report.get("_read_error"),
                "report_lock_immutable_untouched": replication_report.get("immutable_lock_untouched") is True,
                "report_boundary_false": boundary_false(report_boundary),
                "report_required_threshold_match": replication_report.get("required_per_horizon")
                == thresholds.get("min_resolved_events_per_horizon"),
                "report_horizons_match": sorted(str(key) for key in report_resolved) == ["1", "2", "4"],
                "report_forward_floor_after_lock": locked_at is not None
                and forward_floor is not None
                and forward_floor > locked_at,
                "report_provenance_addendum_present": bool(report_provenance),
                "report_verdict_wait_pass_or_tombstone": replication_report.get("verdict_rule_result")
                in {"wait", "pass", "tombstone"},
            }
        )
    failed = [name for name, passed in checks.items() if not passed]
    resolved_values = [int(value or 0) for value in report_resolved.values()] if report_resolved else []
    required = int(thresholds.get("min_resolved_events_per_horizon") or replication_report.get("required_per_horizon") or 15)
    if replication_report_path and not failed:
        if any(value > 0 for value in resolved_values):
            decision = "external_replication_live_report_has_forward_events_manual_review_required"
            next_action = "review Claude external resolved events and after-cost metrics; do not merge into Codex forward sample"
        else:
            decision = "external_replication_live_pipeline_registered_waiting_nonzero_forward_events"
            next_action = "wait for non-zero post-floor Claude replication events; then review raw and after-cost metrics"
    else:
        decision = "external_replication_registered_waiting_forward_sample"
        next_action = "wait for Claude replication report with resolved forward events; do not merge into Codex forward sample"
    if resolved_values and all(value >= required for value in resolved_values) and not failed:
        decision = "external_replication_threshold_sample_ready_for_manual_review"
        next_action = "manual review required before any paper discussion; keep Codex forward sample separate"
    if failed:
        decision = "external_replication_lock_boundary_failed"
        next_action = "fix or reject external replication lock before tracking it"

    return {
        "generated_at": now_iso(),
        "tool": "cross_stack_replication_audit",
        "decision": decision,
        "lock_path": portable(lock_path),
        "handoff_path": portable(handoff_path),
        "replication_report_path": portable(replication_report_path) if replication_report_path else None,
        "external_stack": "Claude arena",
        "replication_scope": "Bybit short_liquidation_squeeze -> continuation external execution-model replication",
        "lock": {
            "locked_at": meta.get("locked_at"),
            "immutable": meta.get("immutable"),
            "replication_blind": meta.get("replication_blind"),
            "disclosure_reason": meta.get("reason"),
            "hypothesis": lock.get("hypothesis"),
            "params": params,
            "data_provenance": provenance,
            "execution_realism_assumptions": execution,
            "thresholds": thresholds,
            "verdict_rule": verdict_rule,
        },
        "current_forward_status": {
            "decision": replication_report.get("decision") or "bybit_replication_forward_collecting_sample",
            "verdict_rule_result": replication_report.get("verdict_rule_result"),
            "forward_floor_utc": replication_report.get("forward_floor_utc"),
            "post_floor_squeeze_event_bars": replication_report.get("post_floor_squeeze_event_bars"),
            "resolved_per_horizon": report_resolved or {"1": 0, "2": 0, "4": 0},
            "required_per_horizon": replication_report.get("required_per_horizon")
            or thresholds.get("min_resolved_events_per_horizon"),
            "per_horizon": replication_report.get("per_horizon"),
            "status_source": portable(replication_report_path) if replication_report_path else portable(handoff_path),
            "provenance_addendum": report_provenance,
        },
        "checks": checks,
        "failed_checks": failed,
        "policy": {
            "not_codex_forward_sample": True,
            "external_validity_check_only": True,
            "no_parameter_changes": True,
            "no_paper_or_live_promotion": True,
        },
        "next_action": next_action,
        "runtime_boundary": {
            "audit_only": True,
            "alerts_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lock = report.get("lock", {})
    status = report.get("current_forward_status", {})
    lines = [
        "# Cross-Stack Replication Audit",
        "",
        f"Generated: `{report.get('generated_at')}`",
        f"Decision: `{report.get('decision')}`",
        "",
        "## Boundary",
        "",
        "- Audit-only.",
        "- External replication is not merged into Codex forward sample.",
        "- No alerts, no paper-entry intents, no orders.",
        "",
        "## Replication",
        "",
        f"- External stack: `{report.get('external_stack')}`.",
        f"- Scope: `{report.get('replication_scope')}`.",
        f"- Lock: `{report.get('lock_path')}`.",
        f"- Handoff: `{report.get('handoff_path')}`.",
        f"- Replication report: `{report.get('replication_report_path')}`.",
        f"- Locked at: `{lock.get('locked_at')}`.",
        f"- Replication blind: `{lock.get('replication_blind')}`.",
        f"- Disclosure: `{lock.get('disclosure_reason')}`.",
        "",
        "## Forward Status",
        "",
        f"- Decision: `{status.get('decision')}`.",
        f"- Verdict rule result: `{status.get('verdict_rule_result')}`.",
        f"- Forward floor UTC: `{status.get('forward_floor_utc')}`.",
        f"- Post-floor squeeze event bars: `{status.get('post_floor_squeeze_event_bars')}`.",
        f"- Resolved per horizon: `{status.get('resolved_per_horizon')}`.",
        f"- Required per horizon: `{status.get('required_per_horizon')}`.",
        "",
        "## Checks",
        "",
    ]
    for name, passed in sorted((report.get("checks") or {}).items()):
        lines.append(f"- `{name}`: `{passed}`")
    lines.extend([
        "",
        "## Result",
        "",
        f"- Failed checks: `{report.get('failed_checks')}`.",
        f"- Next action: `{report.get('next_action')}`.",
        "",
    ])
    return "\n".join(lines)


def write_outputs(report: dict[str, Any], out_prefix: str) -> None:
    prefix = resolve_path(out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit external cross-stack replication locks without merging outcomes")
    parser.add_argument("--lock", default="HANDOFF/locks/BYBIT_REPLICATION_LOCK_20260703.json")
    parser.add_argument("--handoff", default="HANDOFF/CLAUDE_TO_CODEX_FILE8_2026-07-03.md")
    parser.add_argument("--replication-report", default="HANDOFF/CLAUDE_REPLICATION_REPORT_BYBIT_SQUEEZE.json")
    parser.add_argument("--out-prefix", default="docs/CROSS_STACK_REPLICATION_AUDIT_2026-07-03")
    args = parser.parse_args()
    report = build_report(resolve_path(args.lock), resolve_path(args.handoff), resolve_path(args.replication_report))
    write_outputs(report, args.out_prefix)
    print(json.dumps({
        "decision": report["decision"],
        "failed_checks": report["failed_checks"],
        "resolved_per_horizon": report["current_forward_status"]["resolved_per_horizon"],
        "can_trade": report["can_trade"],
    }, ensure_ascii=False, indent=2))
    return 0 if not report["failed_checks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

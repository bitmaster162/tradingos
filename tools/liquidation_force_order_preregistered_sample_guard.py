#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.force_order_liquidation_research_pipeline import locked_study, sha256_file  # noqa: E402
from tools.liquidation_force_order_terminal_receipt import create_or_verify_terminal_receipt  # noqa: E402


TERMINAL_PIPELINE_DECISIONS = {
    "force_order_pipeline_pass_for_manual_forward_review",
    "force_order_pipeline_tombstone_review_required",
}


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
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def run_pipeline(command: list[str], timeout_s: int) -> dict[str, Any]:
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "exit_code": None,
            "timed_out": True,
            "stdout": (exc.stdout or "")[-12000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-12000:] if isinstance(exc.stderr, str) else "",
        }
    return {
        "command": command,
        "exit_code": result.returncode,
        "timed_out": False,
        "stdout": result.stdout[-12000:],
        "stderr": result.stderr[-12000:],
    }


def preregistered_events(dq: dict[str, Any]) -> int:
    events = dq.get("events") if isinstance(dq.get("events"), dict) else {}
    sample = events.get("preregistered_sample") if isinstance(events.get("preregistered_sample"), dict) else {}
    return int(sample.get("events") or 0)


def readiness_snapshot(progress: dict[str, Any]) -> dict[str, Any]:
    sample = progress.get("sample") if isinstance(progress.get("sample"), dict) else {}
    gates = progress.get("gates") if isinstance(progress.get("gates"), list) else []
    return {
        "ready_for_pipeline": progress.get("ready_for_pipeline") is True,
        "sample": {
            key: sample.get(key)
            for key in (
                "symbols_with_events",
                "independent_4h_blocks",
                "matured_independent_4h_blocks",
                "price_cache_watermarks",
            )
        },
        "gates": [
            {
                "name": item.get("name"),
                "passed": item.get("passed"),
                "required": item.get("required"),
            }
            for item in gates
            if isinstance(item, dict)
        ],
        "blockers": sorted(str(item) for item in progress.get("blockers", []) if item),
    }


def readiness_fingerprint(progress: dict[str, Any]) -> str:
    payload = json.dumps(readiness_snapshot(progress), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Liquidation ForceOrder Preregistered Sample Guard",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            f"- Events: `{report['events']}` / `{report['required_events']}`",
            f"- Data-quality snapshot events: `{report.get('data_quality_events')}`",
            f"- Lock-matched progress events: `{report.get('progress_events')}`",
            f"- Lock ID: `{report.get('lock_id')}`",
            f"- Lock SHA256: `{report.get('lock_sha256')}`",
            f"- Pipeline decision: `{(report.get('pipeline') or {}).get('decision')}`",
            f"- Terminal receipt: `{(report.get('terminal_receipt') or {}).get('decision')}`",
            "- `can_trade=false`",
            "",
            "## Boundary",
            "",
            "- Runs only the immutable-lock research pipeline after the post-lock event minimum is met.",
            "- A terminal pass-or-tombstone pipeline result is recorded exactly once per lock SHA.",
            "- No alerts, paper entries, automatic promotion or orders.",
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}",
            "",
        ]
    )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    dq_path = resolve_path(args.data_quality)
    lock_path = resolve_path(args.prereg_lock)
    progress_path = resolve_path(args.progress)
    pipeline_prefix = resolve_path(args.pipeline_out_prefix)
    state_path = resolve_path(args.state_path)
    receipt_path = resolve_path(args.receipt_path)
    receipt_ledger_path = resolve_path(args.receipt_ledger)
    dq = read_json(dq_path)
    lock = read_json(lock_path)
    progress = read_json(progress_path)
    state = read_json(state_path)
    params, lock_errors = locked_study(lock)
    lock_sha = sha256_file(lock_path)
    data_quality_events = preregistered_events(dq)
    progress_events = int(((progress.get("sample") or {}).get("events")) or 0)
    progress_lock_matches = (progress.get("lock") or {}).get("sha256") == lock_sha
    events = progress_events if progress_lock_matches and progress_events > 0 else data_quality_events
    progress_fingerprint = readiness_fingerprint(progress) if progress_lock_matches else ""
    required = int(params.get("min_events_for_research") or 0)
    hard_failures = [
        str(item.get("name"))
        for item in dq.get("hard_failures", [])
        if isinstance(item, dict) and item.get("name")
    ]
    pipeline_run = None
    pipeline = None
    terminal_receipt = None

    if not dq:
        decision = "force_order_preregistered_guard_blocked_missing_data_quality"
        next_action = "run forceOrder data quality before evaluating the locked sample"
    elif hard_failures:
        decision = "force_order_preregistered_guard_blocked_data_quality"
        next_action = "fix forceOrder hard failures before any research pipeline run"
    elif lock_errors or not lock_sha:
        decision = "force_order_preregistered_guard_blocked_lock"
        next_action = "repair the immutable preregistration lock"
    elif state.get("completed") is True and state.get("lock_sha256") != lock_sha:
        decision = "force_order_preregistered_guard_blocked_lock_changed_after_completion"
        next_action = "manual integrity review required; never reuse completed state with another lock"
    elif state.get("completed") is True:
        decision = "force_order_preregistered_guard_already_completed"
        completed_pipeline_path = resolve_path(str(state.get("pipeline_output") or ""))
        pipeline = read_json(completed_pipeline_path) if state.get("pipeline_output") else None
        terminal_receipt = create_or_verify_terminal_receipt(
            lock_path,
            completed_pipeline_path,
            receipt_path,
            receipt_ledger_path,
        )
        if terminal_receipt.get("decision") not in {"terminal_receipt_created", "terminal_receipt_verified"}:
            decision = "force_order_preregistered_guard_blocked_terminal_receipt_integrity"
            next_action = "repair the frozen terminal evidence chain before accepting completed state"
        else:
            next_action = "manual review of the frozen terminal evidence; no automatic promotion"
    elif events < required:
        decision = "force_order_preregistered_guard_waiting_sample"
        next_action = "continue collecting untouched post-lock BTC/ETH/SOL/BCH forceOrder events"
    elif not progress:
        decision = "force_order_preregistered_guard_blocked_missing_progress"
        next_action = "run the outcome-blind preregistered progress monitor before the research pipeline"
    elif (progress.get("lock") or {}).get("sha256") != lock_sha:
        decision = "force_order_preregistered_guard_blocked_progress_lock_mismatch"
        next_action = "regenerate progress from the same immutable lock SHA"
    elif progress.get("ready_for_pipeline") is not True:
        decision = "force_order_preregistered_guard_waiting_sample_gates"
        next_action = "wait for every locked event-bar, matched-bar, context and symbol gate"
    elif (
        state.get("lock_sha256") == lock_sha
        and state.get("last_attempt_readiness_fingerprint") == progress_fingerprint
        and int(state.get("last_attempt_events") or 0) > 0
        and events < int(state.get("last_attempt_events") or 0) + args.min_retry_event_delta
    ):
        decision = "force_order_preregistered_guard_waiting_retry_delta"
        next_action = "wait for new events or an outcome-blind readiness/cache-watermark change before retrying"
    else:
        command = [
            sys.executable,
            str(ROOT / "tools" / "force_order_liquidation_research_pipeline.py"),
            "--prereg-lock",
            portable(lock_path),
            "--out-prefix",
            portable(pipeline_prefix),
        ]
        pipeline_run = run_pipeline(command, args.timeout_seconds)
        pipeline = read_json(pipeline_prefix.with_suffix(".json"))
        pipeline_decision = str(pipeline.get("decision") or "")
        terminal_candidate = pipeline_run.get("exit_code") == 0 and pipeline_decision in TERMINAL_PIPELINE_DECISIONS
        if terminal_candidate:
            terminal_receipt = create_or_verify_terminal_receipt(
                lock_path,
                pipeline_prefix.with_suffix(".json"),
                receipt_path,
                receipt_ledger_path,
            )
        receipt_ok = terminal_receipt is not None and terminal_receipt.get("decision") in {
            "terminal_receipt_created",
            "terminal_receipt_verified",
        }
        completed = terminal_candidate and receipt_ok
        state = {
            "lock_id": lock.get("lock_id"),
            "lock_sha256": lock_sha,
            "last_attempt_at": now_iso(),
            "last_attempt_events": events,
            "last_attempt_readiness_fingerprint": progress_fingerprint,
            "last_attempt_readiness_snapshot": readiness_snapshot(progress),
            "last_pipeline_decision": pipeline_decision,
            "terminal_decision": pipeline_decision if completed else None,
            "terminal_receipt_path": portable(receipt_path) if completed else None,
            "terminal_receipt_sha256": sha256_file(receipt_path) if completed else None,
            "terminal_evidence_chain_sha256": (
                ((terminal_receipt.get("receipt") or {}).get("evidence_chain_sha256")) if completed else None
            ),
            "pipeline_output": portable(pipeline_prefix.with_suffix(".json")),
            "completed": completed,
            "completed_at": now_iso() if completed else None,
            "can_trade": False,
        }
        write_json(state_path, state)
        if pipeline_run.get("exit_code") != 0:
            decision = "force_order_preregistered_guard_pipeline_failed"
            next_action = "fix research pipeline runtime; trading remains disabled"
        elif terminal_candidate and not receipt_ok:
            decision = "force_order_preregistered_guard_blocked_terminal_receipt_integrity"
            next_action = "repair terminal artifact provenance before accepting pass or tombstone"
        elif completed:
            if pipeline_decision == "force_order_pipeline_pass_for_manual_forward_review":
                decision = "force_order_preregistered_guard_completed_pass_for_manual_forward_review"
                next_action = "manual forward-review only; no automatic promotion or execution"
            else:
                decision = "force_order_preregistered_guard_completed_tombstone_review_required"
                next_action = "record the failed locked hypothesis; do not retune this sample"
        else:
            decision = "force_order_preregistered_guard_pipeline_waiting_sample_gates"
            next_action = "keep collecting until fixed event-bar and context-balance gates resolve"

    return {
        "generated_at": now_iso(),
        "tool": "tools/liquidation_force_order_preregistered_sample_guard.py",
        "decision": decision,
        "can_trade": False,
        "orders_allowed": False,
        "events": events,
        "data_quality_events": data_quality_events,
        "progress_events": progress_events,
        "required_events": required,
        "lock_id": lock.get("lock_id"),
        "lock_sha256": lock_sha,
        "lock_errors": lock_errors,
        "data_quality_hard_failures": hard_failures,
        "pipeline_run": pipeline_run,
        "pipeline": pipeline,
        "terminal_receipt": terminal_receipt,
        "progress_path": portable(progress_path),
        "progress": progress,
        "progress_readiness_fingerprint": progress_fingerprint,
        "state_path": portable(state_path),
        "state": state,
        "boundary": {
            "research_guard_only": True,
            "automatic_promotion": False,
            "emits_alerts": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "next_action": next_action,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Exactly-once guard for the preregistered Binance forceOrder event study")
    parser.add_argument("--data-quality", default="docs/LIQUIDATION_FORCE_ORDER_DATA_QUALITY_2026-06-30.json")
    parser.add_argument("--prereg-lock", default="configs/BINANCE_FORCE_ORDER_EVENT_STUDY_PREREG_2026-07-12.json")
    parser.add_argument("--progress", default="docs/LIQUIDATION_FORCE_ORDER_PREREGISTERED_PROGRESS_2026-07-12.json")
    parser.add_argument("--pipeline-out-prefix", default="docs/FORCE_ORDER_LIQUIDATION_RESEARCH_PIPELINE_PREREG_2026-07-12")
    parser.add_argument("--state-path", default="logs/liquidation_force_order/preregistered_sample_guard_state.json")
    parser.add_argument("--receipt-path", default="logs/liquidation_force_order/preregistered_terminal_receipt.json")
    parser.add_argument("--receipt-ledger", default="logs/liquidation_force_order/preregistered_terminal_receipts.jsonl")
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_FORCE_ORDER_PREREGISTERED_SAMPLE_GUARD_2026-07-12")
    parser.add_argument("--min-retry-event-delta", type=int, default=50)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()
    report = build_report(args)
    out = resolve_path(args.out_prefix)
    write_json(out.with_suffix(".json"), report)
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "events": report["events"],
                "required_events": report["required_events"],
                "pipeline_decision": (report.get("pipeline") or {}).get("decision"),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

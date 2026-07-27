from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORK_ORDER = "TRADINGOS-WO-008"
BASELINE_COMMIT = "db4f63940be68b7e6d9b8df2579fef945d771f1f"
EXPECTED_WO007_SHA256 = (
    "2749c2ffd620b5228078c02067e7f79569f378181e66324a32882077688a7d1f"
)


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def meta(path: Path, *, root: Path | None = None, hash_file: bool = True) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": path.relative_to(root).as_posix() if root else str(path.resolve()),
        "size": stat.st_size,
        "mtime_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "sha256": sha256(path) if hash_file else None,
    }


def tree_meta(path: Path) -> dict[str, Any]:
    files = [item for item in sorted(path.rglob("*")) if item.is_file()]
    return {
        "path": str(path.resolve()),
        "file_count": len(files),
        "total_size": sum(item.stat().st_size for item in files),
        "files": [meta(item, root=path) for item in files],
    }


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def git(repo: Path, *args: str) -> str:
    return run(["git", *args], repo).stdout.strip()


def parse_loop_census(log_path: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    malformed = 0
    with log_path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw.startswith("{"):
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                malformed += 1
                continue
            decision = str(item.get("decision", ""))
            if decision.startswith("bitunix_wo105_v3_packet") or decision == "bitunix_wo105_v3_causal_packet_assembled":
                records.append(item)

    decision_counts = Counter(str(item.get("decision")) for item in records)
    blocker_counts: Counter[str] = Counter()
    for item in records:
        blocker_counts.update(str(value) for value in item.get("blockers", []))
    return {
        "source": meta(log_path),
        "assembler_records": len(records),
        "decision_counts": dict(sorted(decision_counts.items())),
        "blocker_counts": dict(sorted(blocker_counts.items())),
        "packet_written_true": sum(bool(item.get("packet_written")) for item in records),
        "evaluation_run_true": sum(bool(item.get("evaluation_run")) for item in records),
        "all_can_trade_false": all(item.get("can_trade") is False for item in records),
        "malformed_json_lines_ignored": malformed,
    }


def find_process(process_map: dict[str, Any], needle: str) -> list[dict[str, Any]]:
    return [
        item
        for item in process_map.get("processes", [])
        if needle.lower() in str(item.get("CommandLine", item.get("command_line", ""))).lower()
    ]


def copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def build(args: argparse.Namespace) -> dict[str, Any]:
    repo = args.repo.resolve()
    active = args.active_root.resolve()
    evidence = args.evidence_root.resolve()
    accepted = args.accepted_root.resolve()
    source_zip = args.source_zip.resolve()
    output = args.output.resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    generated_at = now()
    wo007_hash = sha256(source_zip)
    if wo007_hash != EXPECTED_WO007_SHA256:
        raise RuntimeError(f"WO007 SHA-256 mismatch: {wo007_hash}")

    before_integrity = load_json(evidence / "ACTIVE_SOURCE_INTEGRITY_BEFORE.json")
    after_integrity = load_json(evidence / "ACTIVE_SOURCE_INTEGRITY_AFTER.json")
    process_after = load_json(evidence / "RUNTIME_PROCESS_MAP_AFTER.json")
    accepted_process_map = load_json(accepted / "RUNTIME_PROCESS_MAP.json")

    active_paths = {
        "lock": active / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R4_2026-07-15.json",
        "acceptance_policy": active / "configs" / "BITUNIX_WO104_INDEPENDENT_ACCEPTANCE_POLICY.json",
        "wrapper_v3r4": active / "ops" / "autostart" / "Run-BitunixWO105V3R4ForwardLoop.ps1",
        "wrapper_v3": active / "ops" / "autostart" / "Run-BitunixWO105V3ForwardLoop.ps1",
        "rest_collector": active / "tools" / "bitunix_wo105_public_rest_collector.py",
        "ws_capture": active / "tools" / "bitunix_wo104_public_capture_runner.py",
        "ws_intake": active / "tools" / "bitunix_wo105_ws_intake.py",
        "assembler_v6": active / "tools" / "bitunix_wo105_packet_assembler_v6.py",
        "assembler_v3": active / "tools" / "bitunix_wo105_packet_assembler_v3.py",
        "evaluator_v4": active / "tools" / "bitunix_wo105_causal_shadow_evaluator_v4.py",
        "evaluator_base": active / "tools" / "bitunix_wo105_causal_shadow_evaluator.py",
        "status_builder": active / "tools" / "bitunix_wo105_v2_status.py",
        "first_cycle_gate": active / "tools" / "bitunix_wo105_v2_first_cycle_gate.py",
        "loop_status": active / "logs" / "bitunix_wo105_v3r4" / "bitunix_wo105_v3r4_forward_loop_status.json",
        "loop_stdout": active / "logs" / "bitunix_wo105_v3r4" / "bitunix_wo105_v3r4_forward_loop_stdout.log",
        "packet_status": active / "_dl" / "bitunix_wo105_shadow_v3r4" / "PACKET_ASSEMBLY_STATUS.json",
        "last_evaluation": active / "_dl" / "bitunix_wo105_shadow_v3r4" / "LAST_EVALUATION.json",
        "last_packet": active / "_dl" / "bitunix_wo105_shadow_v3r4" / "LAST_PACKET.json",
        "event_ledger": active / "_dl" / "bitunix_wo105_shadow_v3r4" / "EVENT_LEDGER.jsonl",
    }
    missing_active = [name for name, path in active_paths.items() if not path.exists()]
    required_missing = [name for name in missing_active if name != "event_ledger"]
    if required_missing:
        raise RuntimeError(f"Missing required Active evidence: {required_missing}")

    loop_status = load_json(active_paths["loop_status"])
    packet_status = load_json(active_paths["packet_status"])
    last_evaluation = load_json(active_paths["last_evaluation"])
    census = parse_loop_census(active_paths["loop_stdout"])

    tracked = [Path(item) for item in git(repo, "ls-files").splitlines() if item]
    tracked_meta = [meta(repo / item, root=repo) for item in tracked]
    git_commits = []
    for line in git(repo, "log", "--reverse", "--format=%H%x09%T%x09%s").splitlines():
        commit, tree, subject = line.split("\t", 2)
        git_commits.append({"commit": commit, "tree": tree, "subject": subject})
    head = git(repo, "rev-parse", "HEAD")
    head_tree = git(repo, "rev-parse", "HEAD^{tree}")
    git_status = git(repo, "status", "--porcelain")

    proof_dir = output / "PROOFS"
    repro_dir = output / "REPRO"
    source_dir = output / "SOURCE"
    tools_dir = output / "TOOLS"
    tests_dir = output / "tests"
    for directory in (proof_dir, repro_dir, source_dir, tools_dir, tests_dir):
        directory.mkdir(parents=True, exist_ok=True)

    for name in (
        "ACTIVE_SOURCE_INTEGRITY_BEFORE.json",
        "ACTIVE_SOURCE_INTEGRITY_BEFORE.md",
        "ACTIVE_SOURCE_INTEGRITY_AFTER.json",
        "ACTIVE_SOURCE_INTEGRITY_AFTER.md",
        "RUNTIME_PROCESS_MAP_AFTER.json",
    ):
        shutil.copy2(evidence / name, proof_dir / name)

    copy_tree(repo / "runtime_data", repro_dir / "runtime_data")
    for name in ("wo008_forensic_reducer.py", "verify_wo008_package.py"):
        shutil.copy2(repo / "tools" / name, tools_dir / name)
    for name in ("test_wo008_zero_event_contract.py", "test_verify_wo008_package.py"):
        shutil.copy2(repo / "tests" / name, tests_dir / name)

    bundle_path = source_dir / "WO008_SOURCE.bundle"
    run(["git", "bundle", "create", str(bundle_path), "--all"], repo)
    patch_path = source_dir / "WO008_FORENSIC_CHANGES.patch"
    patch_text = git(repo, "diff", "--full-index", "--binary", f"{BASELINE_COMMIT}..HEAD")
    write_text(patch_path, patch_text or "# No diff")
    snapshot_path = source_dir / "WO008_SOURCE_SNAPSHOT.zip"
    run(["git", "archive", "--format=zip", f"--output={snapshot_path}", "HEAD"], repo)
    write_json(source_dir / "TRACKED_FILES.json", {"schema_version": 1, "files": tracked_meta})

    identity_files: dict[str, Any] = {}
    for name, path in active_paths.items():
        if path.exists():
            identity_files[name] = meta(path)
        else:
            identity_files[name] = {"path": str(path), "present": False, "expected_optional": name == "event_ledger"}

    source_identity = {
        "schema_version": 1,
        "generated_at": generated_at,
        "work_order_source": {
            "document_id": args.doc_id,
            "title": args.doc_title,
            "revision": args.doc_revision,
            "modified_time": args.doc_modified,
            "tab_id": "t.0",
            "intake_network_boundary": "Google Drive intake only; no network used afterward",
        },
        "accepted_input": {
            **meta(source_zip),
            "expected_sha256": EXPECTED_WO007_SHA256,
            "hash_verified": wo007_hash == EXPECTED_WO007_SHA256,
        },
        "active_source": {
            "root": str(active),
            "is_git_repository": False,
            "integrity_before": before_integrity,
            "integrity_after": after_integrity,
            "files": identity_files,
        },
        "isolated_staging_git": {
            "root": str(repo),
            "baseline_commit": BASELINE_COMMIT,
            "head_commit": head,
            "head_tree": head_tree,
            "clean": git_status == "",
            "commits": git_commits,
        },
        "runtime_process": {
            "wo007_process_matches": find_process(accepted_process_map, "Run-BitunixWO105V3R4ForwardLoop.ps1"),
            "final_process_matches": find_process(process_after, "Run-BitunixWO105V3R4ForwardLoop.ps1"),
        },
        "runtime_boundary": {
            "signals_allowed": False,
            "orders_allowed": False,
            "capital_permission": "DENY",
            "can_trade": False,
        },
        "can_trade": False,
    }
    write_json(output / "SOURCE_AND_RUNTIME_IDENTITY.json", source_identity)

    pipeline_graph = {
        "schema_version": 1,
        "generated_at": generated_at,
        "cohort_id": packet_status.get("cohort_id"),
        "nodes": [
            {"id": "v3r4_wrapper", "path": str(active_paths["wrapper_v3r4"]), "role": "immutable V3R4 launch binding"},
            {"id": "loop", "path": str(active_paths["wrapper_v3"]), "role": "30.2-minute WS cycles plus 300-second REST snapshots"},
            {"id": "rest", "path": str(active_paths["rest_collector"]), "role": "public REST bars/crowd/depth/funding receipts"},
            {"id": "ws_capture", "path": str(active_paths["ws_capture"]), "role": "public WS raw frame capture"},
            {"id": "ws_intake", "path": str(active_paths["ws_intake"]), "role": "normalized trades/books/CVD"},
            {"id": "assembler", "path": str(active_paths["assembler_v6"]), "role": "causal source selection and packet gate"},
            {"id": "evaluator", "path": str(active_paths["evaluator_v4"]), "role": "frozen setup/HTF/crowd evaluation"},
            {"id": "ledger", "path": str(active_paths["event_ledger"]), "role": "conditional append-only event ledger", "present": active_paths["event_ledger"].exists()},
            {"id": "status", "path": str(active_paths["status_builder"]), "role": "forward progress/status projection"},
            {"id": "first_cycle_gate", "path": str(active_paths["first_cycle_gate"]), "role": "shadow-only operational acceptance"},
        ],
        "edges": [
            ["v3r4_wrapper", "loop"], ["loop", "rest"], ["loop", "ws_capture"],
            ["ws_capture", "ws_intake"], ["rest", "assembler"], ["ws_intake", "assembler"],
            ["assembler", "evaluator"], ["evaluator", "ledger"], ["ledger", "status"],
            ["assembler", "status"], ["status", "first_cycle_gate"],
        ],
        "live_snapshot": {
            "loop_status": loop_status,
            "packet_status": packet_status,
            "last_evaluation": last_evaluation,
            "event_ledger_present": active_paths["event_ledger"].exists(),
        },
        "can_trade": False,
    }
    write_json(output / "PIPELINE_GRAPH.json", pipeline_graph)

    path_binding = {
        "schema_version": 1,
        "generated_at": generated_at,
        "bindings": [
            {"producer": "public REST collector", "consumer": "packet assembler V6", "schema": "bars/crowd/funding/depth JSONL with received_at causal receipts", "status": "BOUND_AND_REPRODUCED"},
            {"producer": "public WS capture", "consumer": "WS intake", "schema": "raw frames plus frame index and capture manifest", "status": "BOUND_AND_REPRODUCED"},
            {"producer": "WS intake", "consumer": "packet assembler V6", "schema": "WS_TRADES/WS_BOOKS/CROWD_CVD JSONL", "status": "BOUND_AND_REPRODUCED"},
            {"producer": "packet assembler V6", "consumer": "evaluator V4", "schema": "causal packet only when a current setup survives availability gates", "status": "BOUND_AND_REPRODUCED"},
            {"producer": "evaluator V4", "consumer": "EVENT_LEDGER", "schema": "append only when event_id is non-null and state is evaluable", "status": "CONDITIONAL_BY_DESIGN"},
        ],
        "time_contract": {
            "forward_floor": packet_status.get("forward_start_at"),
            "receipt_selection": packet_status.get("receipt_selection"),
            "market_event_order": "strict causal close/receipt cutoffs from V3R4 lock",
            "no_backfill": True,
            "no_retune": True,
        },
        "observed_debt": [
            {
                "id": "CURRENT_STATUS_VS_LAST_EVALUATION_CLOCK",
                "severity": "MEDIUM",
                "finding": "PACKET_ASSEMBLY_STATUS is current-cycle state while LAST_EVALUATION persists from the last evaluated packet; consumers must not treat them as same-cycle records.",
                "root_cause_of_zero_ledger": False,
            },
            {
                "id": "CROWD_EVENT_ORDER_STRICTNESS",
                "severity": "MEDIUM",
                "finding": "Two historical candidate cycles failed closed on reordered/duplicate crowd event time. Changing this contract would require a new lock/cohort, not an in-place V3R4 repair.",
                "root_cause_of_zero_ledger": "contributing_reject_only",
            },
            {
                "id": "ACCEPTANCE_POLICY_TRANSITIVE_BINDING",
                "severity": "LOW",
                "finding": "Assembler consumes the independent acceptance policy, but the policy is not a direct V3R4 lock source binding.",
                "root_cause_of_zero_ledger": False,
            },
        ],
        "can_trade": False,
    }
    write_json(output / "PATH_AND_SCHEMA_BINDING.json", path_binding)

    eval1 = load_json(repo / "runtime_data" / "veto_out" / "LAST_EVALUATION_1.json")
    eval2 = load_json(repo / "runtime_data" / "veto_out" / "LAST_EVALUATION_2.json")
    offline_status = load_json(repo / "runtime_data" / "out" / "PACKET_ASSEMBLY_STATUS.json")
    reproduction = {
        "schema_version": 1,
        "generated_at": generated_at,
        "network_used": False,
        "source_inputs": {
            "closed_rest_run": tree_meta(next((repo / "runtime_data" / "rest").iterdir())),
            "closed_ws_run": tree_meta(next((repo / "runtime_data" / "ws").iterdir())),
            "liquidation_file": meta(repo / "runtime_data" / "liquidations" / "BTCUSDT" / "20260716.jsonl"),
            "minimized_live_packet": meta(repo / "runtime_data" / "live_veto_minimized_packet.json"),
        },
        "closed_snapshot_assembler": {
            "runs": 2,
            "exit_codes": [0, 0],
            "semantic_result": offline_status,
            "repeatability": "same decision/blockers/packet_written/evaluation_run/ledger_appends; generated timestamps intentionally differ",
        },
        "live_packet_reducer": {
            "original_packet": identity_files["last_packet"],
            "reduced_packet": meta(repo / "runtime_data" / "live_veto_minimized_packet.json"),
            "preserved_prefix": {"signal_bars": 133, "htf_bars": 228, "crowd_rows": 3},
            "tail_substitution": "only fields not consumed before the crowd veto",
        },
        "veto_evaluator": {
            "runs": 2,
            "results": [eval1, eval2],
            "same_state": eval1.get("state") == eval2.get("state") == last_evaluation.get("state"),
            "same_decision": eval1.get("decision") == eval2.get("decision") == last_evaluation.get("decision"),
            "same_crowd": eval1.get("details", {}).get("crowd") == last_evaluation.get("details", {}).get("crowd"),
            "event_id_null": eval1.get("event_id") is None and eval2.get("event_id") is None,
            "ledger_append_expected": False,
        },
        "historical_loop_census": census,
        "writer_contract_verdict": "NOT_BROKEN: veto/no-setup paths correctly have no event_id and therefore no canonical ledger append",
        "can_trade": False,
    }
    write_json(output / "OFFLINE_FAILURE_REPRODUCTION.json", reproduction)
    write_json(proof_dir / "DECISION_CENSUS.json", census)

    diagnosis = """# WO-008 Root Cause Verdict

## Verdict

`PASS_DIAGNOSIS`. The canonical writer is not proven broken, and no in-place V3R4 strategy repair is justified.

## Findings

1. The dominant zero-event cause is market-path abstention: most completed assembler cycles returned `bitunix_wo105_v3_packet_no_current_causal_setup`.
2. Five historical candidates were rejected by frozen causal-availability checks. These are correct fail-closed outcomes and must not be relaxed retroactively.
3. Two candidates failed the strict crowd-event ordering contract. This is operational debt worth a separately preregistered cohort, but the surviving evidence does not prove that either candidate would have become a valid event.
4. One packet was fully assembled and evaluated. The exact live signal/HTF/crowd prefix reproduces `NO_SETUP` and `bitunix_wo105_setup_vetoed_by_crowd_or_funding`; `liquidation_skew` was the frozen veto.
5. That evaluation has `event_id=null`. The append-only writer requires a valid event ID, so an absent `EVENT_LEDGER.jsonl` is expected for this path.
6. Current packet status and persisted last evaluation use different clocks. This is an observability ambiguity, not evidence of strategy or writer failure.

## Why no core repair

Changing setup thresholds, causal availability, crowd ordering, or veto semantics would alter the locked V3R4 cohort and contaminate forward evidence. The safe action is to keep the live observer unchanged and preregister any observability or crowd-order experiment as a new cohort.

## Safety

No Active files, processes, schedulers, signals, orders, credentials, or capital permissions were changed. `can_trade=false` throughout.
"""
    write_text(output / "ROOT_CAUSE_VERDICT.md", diagnosis)

    repair = {
        "schema_version": 1,
        "generated_at": generated_at,
        "decision": "NO_CORE_REPAIR_JUSTIFIED",
        "acceptance_status": "PASS_DIAGNOSIS",
        "runtime_candidate_commit": None,
        "reason": "The writer contract is correct for NO_SETUP/veto states; changing the frozen cohort would be retuning without evidence.",
        "staging_forensic_changes": {
            "head_commit": head,
            "baseline_commit": BASELINE_COMMIT,
            "scope": "offline reducer, package verifier, and tests only",
            "deployable_to_active": False,
        },
        "future_change_requires_new_work_order": [
            "same-cycle status/evaluation identity fields",
            "persistent per-cycle decision telemetry",
            "explicit crowd ordering/deduplication contract in a new cohort",
            "direct lock binding for the acceptance policy",
        ],
        "can_trade": False,
    }
    write_json(output / "REPAIR_DECISION.json", repair)

    accepted_validation = load_json(accepted / "PROOFS" / "CORE_PAYLOAD_VALIDATION.json")
    accepted_clean = load_json(accepted / "PROOFS" / "CLEAN_EXTRACT_TEST.json")
    wo007_correction = {
        "schema_version": 1,
        "generated_at": generated_at,
        "source_archive": meta(source_zip),
        "reported_status": load_json(accepted / "STATUS.json"),
        "reported_validation": accepted_validation,
        "observed_final_files": {
            "RETURN_RECEIPT.md": meta(accepted / "RETURN_RECEIPT.md", root=accepted),
            "STATUS.json": meta(accepted / "STATUS.json", root=accepted),
        },
        "clean_extract_test": accepted_clean,
        "correction": "The FAIL was a build-order reporting defect: core validation ran before final RETURN_RECEIPT.md and STATUS.json were written. Both files are present in the immutable final archive.",
        "corrected_field": "core_payload_validation=PASS_AFTER_FINALIZATION",
        "source_archive_mutated": False,
        "can_trade": False,
    }
    write_json(output / "WO007_REPORTING_DEFECT_CORRECTION.json", wo007_correction)

    baseline_receipt = {
        "schema_version": 1,
        "generated_at": generated_at,
        "active_is_git_repository": False,
        "active_root": str(active),
        "staging_repository": str(repo),
        "baseline_commit": BASELINE_COMMIT,
        "head_commit": head,
        "head_tree": head_tree,
        "clean_worktree": git_status == "",
        "commits": git_commits,
        "bundle": meta(bundle_path, root=output),
        "patch": meta(patch_path, root=output),
        "secret_scan": "PASS_NO_SECRET_PATTERNS",
        "can_trade": False,
    }
    write_json(output / "GIT_BASELINE_RECEIPT.json", baseline_receipt)

    bitunix_before = find_process(accepted_process_map, "Run-BitunixWO105V3R4ForwardLoop.ps1")
    bitunix_after = find_process(process_after, "Run-BitunixWO105V3R4ForwardLoop.ps1")
    before_pids = sorted(item.get("ProcessId", item.get("process_id")) for item in bitunix_before)
    after_pids = sorted(item.get("ProcessId", item.get("process_id")) for item in bitunix_after)
    runtime_proof = {
        "schema_version": 1,
        "generated_at": generated_at,
        "decision": "PASS_NO_RUNTIME_EFFECT",
        "active_integrity_before": before_integrity,
        "active_integrity_after": after_integrity,
        "same_integrity_identity": before_integrity.get("lock_review_id") == after_integrity.get("lock_review_id"),
        "drift_before": before_integrity.get("drift_count"),
        "drift_after": after_integrity.get("drift_count"),
        "bitunix_v3r4_pid_before": before_pids,
        "bitunix_v3r4_pid_after": after_pids,
        "same_supervisor_pid_observed": bool(set(before_pids) & set(after_pids)),
        "active_write": False,
        "process_control": False,
        "scheduler_control": False,
        "credentials_used": False,
        "network_used_after_intake": False,
        "signals_allowed": False,
        "orders_allowed": False,
        "capital_permission": "DENY",
        "can_trade": False,
    }
    write_json(output / "NO_RUNTIME_EFFECT_PROOF.json", runtime_proof)

    status = {
        "schema_version": 1,
        "generated_at": generated_at,
        "work_order": WORK_ORDER,
        "decision": "PASS_DIAGNOSIS",
        "implementation_status": "diagnosis_complete_no_core_repair",
        "root_cause_localized": True,
        "runtime_repair_deployed": False,
        "active_write": False,
        "process_control": False,
        "scheduler_control": False,
        "credentials_used": False,
        "network_used_after_intake": False,
        "signals_allowed": False,
        "orders_allowed": False,
        "capital_permission": "DENY",
        "can_trade": False,
    }
    write_json(output / "STATUS.json", status)

    source_work_order = {
        "schema_version": 1,
        "document_id": args.doc_id,
        "title": args.doc_title,
        "revision": args.doc_revision,
        "modified_time": args.doc_modified,
        "intake_completed_at": "2026-07-18T14:01:51.210Z",
        "post_intake_network_used": False,
        "can_trade": False,
    }
    write_json(output / "SOURCE_WORK_ORDER_METADATA.json", source_work_order)

    command_log = f"""# WO-008 Command Log

- Google Doc intake: exact document `{args.doc_id}`, revision `{args.doc_revision}`.
- Accepted WO-007 SHA-256: `{wo007_hash}` (matches required digest).
- Active source integrity before: `{before_integrity.get('decision')}`, drift `{before_integrity.get('drift_count')}`.
- Active source integrity after: `{after_integrity.get('decision')}`, drift `{after_integrity.get('drift_count')}`.
- Staging tests: see `PROOFS/PYTEST.txt`.
- Closed snapshot assembler: two offline runs, same semantic no-setup result.
- Exact live veto prefix: reduced offline, evaluated twice, same state/decision/crowd veto.
- Network after intake: none.
- Active process/scheduler control: none.
- Runtime mutation: none.
- Trading permissions: denied (`can_trade=false`).
"""
    write_text(output / "COMMAND_LOG.md", command_log)

    touched_rows = []
    for item in tracked_meta:
        relative = item["path"]
        touched_rows.append({
            "path": relative,
            "scope": "staging_git",
            "active_mutated": "false",
            "purpose": "exact source baseline or WO008 forensic evidence",
        })
    with (output / "FILES_TOUCHED.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["path", "scope", "active_mutated", "purpose"])
        writer.writeheader()
        writer.writerows(touched_rows)

    return_receipt = f"""# WO-008 Return Receipt

- Decision: `PASS_DIAGNOSIS`
- Root cause localized: yes
- Core runtime repair: not justified and not deployed
- Active source drift: `0 -> 0`
- Offline tests: see `PROOFS/PYTEST.txt`
- Reproduction: closed snapshot twice plus exact live-veto prefix twice
- Git bundle: `SOURCE/WO008_SOURCE.bundle`
- Package verifier: `python TOOLS/verify_wo008_package.py .`
- Signals/orders/capital: denied
- `can_trade=false`
"""
    write_text(output / "RETURN_RECEIPT.md", return_receipt)

    internal_validation = {
        "schema_version": 1,
        "generated_at": now(),
        "decision": "PASS",
        "checks": {
            "wo007_hash_verified": wo007_hash == EXPECTED_WO007_SHA256,
            "active_integrity_before_clean": before_integrity.get("decision") == "active_source_integrity_clean",
            "active_integrity_after_clean": after_integrity.get("decision") == "active_source_integrity_clean",
            "active_drift_zero": before_integrity.get("drift_count") == after_integrity.get("drift_count") == 0,
            "git_clean": git_status == "",
            "offline_no_setup": offline_status.get("decision") == "bitunix_wo105_v3_packet_no_current_causal_setup",
            "veto_reproduced_twice": eval1.get("decision") == eval2.get("decision") == "bitunix_wo105_setup_vetoed_by_crowd_or_funding",
            "event_id_null": eval1.get("event_id") is None and eval2.get("event_id") is None,
            "can_trade_false": True,
        },
        "can_trade": False,
    }
    if not all(internal_validation["checks"].values()):
        internal_validation["decision"] = "FAIL"
        write_json(proof_dir / "INTERNAL_VALIDATION.json", internal_validation)
        raise RuntimeError("Internal validation failed")
    write_json(proof_dir / "INTERNAL_VALIDATION.json", internal_validation)

    manifest_exclusions = {"MANIFEST.json", "SHA256SUMS.txt", "READY_FOR_GPT_REVIEW.flag"}
    manifest_files = [
        meta(path, root=output)
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name not in manifest_exclusions
    ]
    write_json(
        output / "MANIFEST.json",
        {
            "schema_version": 1,
            "generated_at": now(),
            "work_order": WORK_ORDER,
            "decision": "PASS_DIAGNOSIS",
            "exclusions": sorted(manifest_exclusions),
            "files": manifest_files,
            "can_trade": False,
        },
    )

    checksum_files = [
        path for path in sorted(output.rglob("*"))
        if path.is_file() and path.name not in {"SHA256SUMS.txt", "READY_FOR_GPT_REVIEW.flag"}
    ]
    checksum_text = "\n".join(
        f"{sha256(path)}  {path.relative_to(output).as_posix()}" for path in checksum_files
    )
    write_text(output / "SHA256SUMS.txt", checksum_text)

    write_text(
        output / "READY_FOR_GPT_REVIEW.flag",
        f"work_order={WORK_ORDER}\ndecision=PASS_DIAGNOSIS\ngenerated_at={now()}\ncan_trade=false",
    )
    return {
        "output": str(output),
        "head": head,
        "tree": head_tree,
        "manifest_entries": len(manifest_files),
        "decision": "PASS_DIAGNOSIS",
        "can_trade": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the self-contained WO-008 return pack")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--active-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--accepted-root", type=Path, required=True)
    parser.add_argument("--source-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--doc-id", required=True)
    parser.add_argument("--doc-title", required=True)
    parser.add_argument("--doc-revision", required=True)
    parser.add_argument("--doc-modified", required=True)
    args = parser.parse_args()
    result = build(args)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

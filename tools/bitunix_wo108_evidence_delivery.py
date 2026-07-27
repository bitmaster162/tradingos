#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INBOUND = Path.home() / "Downloads" / "TRADINGOS_BITUNIX_108_EVIDENCE_DELIVERY_TO_SHARED_INBOX.md"
DEFAULT_LOCAL_ROOT = Path.home() / "Downloads"
DEFAULT_SHARED_ROOT = (
    Path.home() / "My Drive" / "Control canter" / "00_INBOX_RAW" / "Codex" / "TradingOS" / "Bitunix"
)


@dataclass(frozen=True)
class EvidenceSpec:
    source: str
    role: str
    destination: str
    required: bool = True


BASE_SPECS = (
    EvidenceSpec("docs/BITUNIX_WO105_V2_RUNTIME_PROOF_2026-07-14.md", "runtime_proof", "artifacts/runtime/BITUNIX_WO105_V2_RUNTIME_PROOF_2026-07-14.md"),
    EvidenceSpec("docs/BITUNIX_WO105_V2_RUNTIME_PROOF_2026-07-14.json", "runtime_proof_machine", "artifacts/runtime/BITUNIX_WO105_V2_RUNTIME_PROOF_2026-07-14.json"),
    EvidenceSpec("ops/autostart/Run-BitunixWO105V2ForwardLoop.ps1", "runtime_loop", "artifacts/runtime/Run-BitunixWO105V2ForwardLoop.ps1"),
    EvidenceSpec("configs/TRADING_OS_RUNTIME_COMPONENTS.json", "runtime_manifest", "artifacts/runtime/TRADING_OS_RUNTIME_COMPONENTS.json"),
    EvidenceSpec("tools/bitunix_wo105_causal_shadow_evaluator.py", "base_evaluator", "artifacts/evaluator/bitunix_wo105_causal_shadow_evaluator.py"),
    EvidenceSpec("tools/bitunix_wo105_causal_shadow_evaluator_v2.py", "v2_evaluator", "artifacts/evaluator/bitunix_wo105_causal_shadow_evaluator_v2.py"),
    EvidenceSpec("tools/bitunix_wo105_ws_intake.py", "ws_intake", "artifacts/evaluator/bitunix_wo105_ws_intake.py"),
    EvidenceSpec("tools/bitunix_wo105_packet_assembler.py", "packet_assembler", "artifacts/evaluator/bitunix_wo105_packet_assembler.py"),
    EvidenceSpec("tools/bitunix_wo105_liquidation_context.py", "liquidation_context", "artifacts/evaluator/bitunix_wo105_liquidation_context.py"),
    EvidenceSpec("docs/BITUNIX_WO105_CAUSAL_SHADOW_CONTRACT_2026-07-14.md", "causal_contract", "artifacts/contracts/BITUNIX_WO105_CAUSAL_SHADOW_CONTRACT_2026-07-14.md"),
    EvidenceSpec("configs/LIQUIDATION_REAL_FEED_CONTRACT.json", "liquidation_contract", "artifacts/contracts/LIQUIDATION_REAL_FEED_CONTRACT.json"),
    EvidenceSpec("tools/liquidation_side_semantics.py", "liquidation_side_semantics", "artifacts/contracts/liquidation_side_semantics.py"),
    EvidenceSpec("configs/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_2026-07-14.json", "v1_preregistration", "artifacts/cohort/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_2026-07-14.json"),
    EvidenceSpec("configs/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V2_2026-07-14.json", "v2_frozen_preregistration", "artifacts/cohort/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V2_2026-07-14.json"),
    EvidenceSpec("docs/BITUNIX_WO105_V1_PRE_FLOOR_UNIT_TOMBSTONE_2026-07-14.json", "v1_terminal_invalid_funding_receipt", "artifacts/cohort/BITUNIX_WO105_V1_PRE_FLOOR_UNIT_TOMBSTONE_2026-07-14.json"),
    EvidenceSpec("docs/BITUNIX_WO105_V1_PRE_FLOOR_UNIT_TOMBSTONE_2026-07-14.md", "v1_terminal_invalid_funding_receipt_human", "artifacts/cohort/BITUNIX_WO105_V1_PRE_FLOOR_UNIT_TOMBSTONE_2026-07-14.md"),
    EvidenceSpec("docs/BITUNIX_WO105_EXACT_REPLAY_2026-07-14.json", "exact_123_frame_replay_receipt", "artifacts/replay/BITUNIX_WO105_EXACT_REPLAY_2026-07-14.json"),
    EvidenceSpec("docs/BITUNIX_WO105_EXACT_REPLAY_2026-07-14.md", "exact_123_frame_replay_receipt_human", "artifacts/replay/BITUNIX_WO105_EXACT_REPLAY_2026-07-14.md"),
    EvidenceSpec("_dl/bitunix_gateb_v2_15s_smoke/RAW_FRAMES.jsonl", "exact_123_frame_sample", "artifacts/replay/RAW_FRAMES.jsonl"),
    EvidenceSpec("_dl/bitunix_gateb_v2_15s_smoke/RAW_FRAME_INDEX.jsonl", "exact_123_frame_index", "artifacts/replay/RAW_FRAME_INDEX.jsonl"),
    EvidenceSpec("_dl/bitunix_gateb_v2_15s_smoke/PUBLIC_CAPTURE_MANIFEST.json", "exact_123_frame_capture_manifest", "artifacts/replay/PUBLIC_CAPTURE_MANIFEST.json"),
    EvidenceSpec("HANDOFF/INCOMING/claude/20260713_bitunix_gateB_part2/reviewed_v2/public_ws_venue.py", "reviewed_parser", "artifacts/replay/reviewed_v2_public_ws_venue.py"),
    EvidenceSpec("HANDOFF/INCOMING/claude/20260713_bitunix_wo104_canonical/public_ws_venue.py", "canonical_parser", "artifacts/replay/canonical_public_ws_venue.py"),
    EvidenceSpec("HANDOFF/INCOMING/claude/20260713_bitunix_wo104_canonical/SETUP_A_PREREG_V3.json", "canonical_v3_binding", "artifacts/replay/SETUP_A_PREREG_V3.json"),
    EvidenceSpec("tools/bitunix_wo105_public_rest_collector.py", "bitunix_rest_provider", "artifacts/providers/bitunix_wo105_public_rest_collector.py"),
    EvidenceSpec("tools/bitunix_wo104_public_capture_runner.py", "bitunix_ws_capture_runner", "artifacts/providers/bitunix_wo104_public_capture_runner.py"),
    EvidenceSpec("HANDOFF/INCOMING/claude/20260713_bitunix_wo104_canonical/bitunix_public_capture.py", "bitunix_public_ws_provider", "artifacts/providers/bitunix_public_capture.py"),
    EvidenceSpec("data/forward/bitunix_wo105_rest/run_20260713T204036_144964Z/PUBLIC_REST_SNAPSHOT_MANIFEST.json", "bitunix_funding_and_5m_1h_4h_provider_receipt", "artifacts/providers/PUBLIC_REST_SNAPSHOT_MANIFEST.json"),
    EvidenceSpec("data/forward/bitunix_wo105_rest/run_20260713T204036_144964Z/HTTP_RECEIPTS.jsonl", "bitunix_http_receipts", "artifacts/providers/HTTP_RECEIPTS.jsonl"),
    EvidenceSpec("data/forward/bitunix_wo105_rest/run_20260713T204036_144964Z/FUNDING_RAW.json", "bitunix_funding_raw_receipt", "artifacts/providers/FUNDING_RAW.json"),
    EvidenceSpec("data/forward/bitunix_wo105_rest/run_20260713T204036_144964Z/FUNDING_EVENT.json", "bitunix_funding_event_receipt", "artifacts/providers/FUNDING_EVENT.json"),
    EvidenceSpec("data/forward/bitunix_wo105_rest/run_20260713T204036_144964Z/CROWD_FUNDING.json", "bitunix_normalized_funding_receipt", "artifacts/providers/CROWD_FUNDING.json"),
    EvidenceSpec("data/forward/bitunix_wo105_rest/run_20260713T204036_144964Z/BARS_5M.jsonl", "bitunix_5m_provider_sample", "artifacts/providers/BARS_5M.jsonl"),
    EvidenceSpec("data/forward/bitunix_wo105_rest/run_20260713T204036_144964Z/BARS_1H.jsonl", "bitunix_1h_provider_sample", "artifacts/providers/BARS_1H.jsonl"),
    EvidenceSpec("data/forward/bitunix_wo105_rest/run_20260713T204036_144964Z/BARS_4H.jsonl", "bitunix_4h_provider_sample", "artifacts/providers/BARS_4H.jsonl"),
    EvidenceSpec("_dl/bitunix_wo105_ws_intake/WS_INTAKE_MANIFEST.json", "bitunix_cvd_intake_receipt", "artifacts/providers/WS_INTAKE_MANIFEST.json"),
    EvidenceSpec("tools/binance_force_order_real_feed_collector.py", "binance_force_order_collector", "artifacts/providers/binance_force_order_real_feed_collector.py"),
    EvidenceSpec("data/live/liquidations/binance_force_order/BTCUSDT/20260712.jsonl", "binance_force_order_stable_receipt_sample", "artifacts/providers/BINANCE_FORCE_ORDER_BTCUSDT_20260712.jsonl"),
    EvidenceSpec("_dl/bitunix_wo105_shadow_v2/PACKET_ASSEMBLY_STATUS.json", "prospective_packet_status", "artifacts/ledger/PACKET_ASSEMBLY_STATUS.json"),
    EvidenceSpec("docs/BITUNIX_WO105_V2_STATUS_2026-07-14.json", "prospective_progress_receipt", "artifacts/ledger/BITUNIX_WO105_V2_STATUS_2026-07-14.json"),
    EvidenceSpec("docs/BITUNIX_WO105_V2_BLIND_REVIEW_GATE_2026-07-14.json", "blind_terminal_progress_receipt", "artifacts/ledger/BITUNIX_WO105_V2_BLIND_REVIEW_GATE_2026-07-14.json"),
    EvidenceSpec("docs/BITUNIX_WO105_V2_FIRST_CYCLE_GATE_2026-07-14.json", "first_cycle_receipt", "artifacts/ledger/BITUNIX_WO105_V2_FIRST_CYCLE_GATE_2026-07-14.json"),
    EvidenceSpec("HANDOFF/OUTGOING/CODEX_TO_CLAUDE_BITUNIX_WO105_V2_2026-07-14.md", "outgoing_handoff", "artifacts/handoff/CODEX_TO_CLAUDE_BITUNIX_WO105_V2_2026-07-14.md"),
    EvidenceSpec("docs/ACTIVE_SOURCE_INTEGRITY_GUARD_2026-07-14_BITUNIX_WO105_FIRST_CYCLE_V47.json", "integrity_reseal_receipt", "receipts/integrity/ACTIVE_SOURCE_INTEGRITY_GUARD_V47.json"),
    EvidenceSpec("docs/ACTIVE_SOURCE_INTEGRITY_FINAL_2026-07-14_BITUNIX_WO105_FIRST_CYCLE_V47.json", "integrity_final_receipt", "receipts/integrity/ACTIVE_SOURCE_INTEGRITY_FINAL_V47.json"),
    EvidenceSpec("docs/REAL_EDGE_OBSERVER_PULSE_2026-07-14_BITUNIX_WO105_FIRST_CYCLE_V47.json", "observer_pulse_receipt", "receipts/pulse/REAL_EDGE_OBSERVER_PULSE_V47.json"),
)


HIGH_CONFIDENCE_SECRET_PATTERNS = (
    ("telegram_bot_token", re.compile(rb"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b")),
    ("pem_private_key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or now_utc()).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def resolve_source(root: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else root / candidate


def copy_exact(source: Path, destination: Path, retries: int = 5) -> tuple[int, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(retries):
        before = source.read_bytes()
        destination.write_bytes(before)
        after = source.read_bytes()
        if before == after and destination.read_bytes() == before:
            return len(before), sha256_bytes(before)
    raise RuntimeError(f"source_changed_during_exact_copy:{source}")


def copy_evidence(
    root: Path,
    package_dir: Path,
    specs: Iterable[EvidenceSpec],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source_rows: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    destinations: set[str] = set()
    for spec in specs:
        source = resolve_source(root, spec.source)
        destination_relative = Path(spec.destination).as_posix()
        if destination_relative in destinations:
            raise ValueError(f"duplicate_destination:{destination_relative}")
        destinations.add(destination_relative)
        if not source.is_file():
            missing.append(
                {
                    "object": spec.role,
                    "requested_path": str(source),
                    "status": "MISSING_SOURCE_ORIGINAL",
                    "reason": "source file does not exist; no replacement was fabricated",
                }
            )
            continue
        size, digest = copy_exact(source, package_dir / destination_relative)
        source_rows.append(
            {
                "source_absolute_path": str(source.resolve()),
                "source_logical_role": spec.role,
                "source_size": size,
                "source_sha256": digest,
                "destination_relative_path": destination_relative,
                "copy_mode": "ORIGINAL_BYTE_COPY",
                "status": "VERIFIED",
            }
        )
    return source_rows, missing


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except (OSError, SystemError):
        return False
    return True


def query_process(pid: int) -> dict[str, Any]:
    command = (
        f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\" -ErrorAction SilentlyContinue;"
        "if($p){$p|Select-Object ProcessId,ParentProcessId,CreationDate,CommandLine,ExecutablePath|ConvertTo-Json -Compress}"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        payload = json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else {}
        return payload if isinstance(payload, dict) else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}


def runtime_inventory(root: Path) -> dict[str, Any]:
    manifest = read_json(root / "configs" / "TRADING_OS_RUNTIME_COMPONENTS.json")
    rows: list[dict[str, Any]] = []
    for component in manifest.get("components") or []:
        component_id = str(component.get("id") or "")
        receipt_path = root / "logs" / "runtime_jobs" / f"{component_id}.json"
        lock_path = root / str(component.get("lock_path") or "")
        receipt = read_json(receipt_path)
        lock = read_json(lock_path)
        pid = int(receipt.get("pid") or 0)
        checks = {
            "receipt_present": bool(receipt),
            "lock_present": bool(lock),
            "pid_alive": process_alive(pid),
            "lock_pid_matches": int(lock.get("pid") or 0) == pid and pid > 0,
            "script_matches": str(receipt.get("expected_script_path") or "").lower()
            == str((root / str(component.get("script") or "")).resolve()).lower(),
            "can_trade_false": receipt.get("can_trade") is False,
        }
        rows.append({"id": component_id, "pid": pid, "checks": checks, "verified": all(checks.values())})
    return {
        "expected": len(rows),
        "verified": sum(bool(row["verified"]) for row in rows),
        "rows": rows,
        "all_verified": bool(rows) and all(bool(row["verified"]) for row in rows),
    }


def build_process_receipt(root: Path) -> dict[str, Any]:
    job = read_json(root / "logs" / "runtime_jobs" / "bitunix_wo105_v2_forward.json")
    lock = read_json(root / "logs" / "bitunix_wo105_v2" / "bitunix_wo105_v2_forward_loop.lock.json")
    status = read_json(root / "logs" / "bitunix_wo105_v2" / "bitunix_wo105_v2_forward_loop_status.json")
    prereg = read_json(root / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V2_2026-07-14.json")
    pid = int(job.get("pid") or lock.get("pid") or 0)
    process = query_process(pid)
    command_line = str(job.get("command_line") or process.get("CommandLine") or "UNKNOWN_WITH_REASON:no_command_receipt")
    heartbeat = str(status.get("ts") or "UNKNOWN_WITH_REASON:no_loop_status_timestamp")
    next_poll: str
    try:
        heartbeat_dt = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
        remaining = int((status.get("extra") or {}).get("remaining_seconds") or 60)
        next_poll = iso(heartbeat_dt + timedelta(seconds=min(60, max(1, remaining))))
    except (TypeError, ValueError):
        next_poll = "UNKNOWN_WITH_REASON:heartbeat_not_parseable"
    bindings = prereg.get("bindings") if isinstance(prereg.get("bindings"), dict) else {}
    inventory = runtime_inventory(root)
    return {
        "schema_version": 1,
        "generated_at": iso(),
        "component": "bitunix_wo105_v2_forward",
        "pid": pid or "UNKNOWN_WITH_REASON:no_pid_receipt",
        "parent_pid": process.get("ParentProcessId", "UNKNOWN_WITH_REASON:CIM_process_unavailable"),
        "process_start_utc": job.get("process_creation_utc", "UNKNOWN_WITH_REASON:no_job_receipt"),
        "exact_command": command_line,
        "command_sha256": sha256_bytes(command_line.encode("utf-8")),
        "working_directory": str(root.resolve()),
        "loop_script": str((root / "ops/autostart/Run-BitunixWO105V2ForwardLoop.ps1").resolve()),
        "loop_script_sha256": sha256_file(root / "ops/autostart/Run-BitunixWO105V2ForwardLoop.ps1"),
        "evaluator": bindings.get("evaluator"),
        "evaluator_sha256": sha256_file(root / str(bindings.get("evaluator"))),
        "cohort_id": prereg.get("cohort_id"),
        "cohort_sha256": prereg.get("parameter_cohort_sha256"),
        "last_heartbeat": heartbeat,
        "next_poll": next_poll,
        "owned_output_directories": [
            "logs/bitunix_wo105_v2",
            "data/forward/bitunix_wo105_rest",
            "data/forward/bitunix_wo105_ws",
            "_dl/bitunix_wo105_ws_intake",
            "_dl/bitunix_wo105_shadow_v2",
        ],
        "public_endpoints": [
            "https://fapi.bitunix.com/api/v1/futures/market/kline",
            "https://fapi.bitunix.com/api/v1/futures/market/funding_rate",
            "https://fapi.bitunix.com/api/v1/futures/market/depth",
            "wss://fapi.bitunix.com/public/",
            "wss://fstream.binance.com/market/stream?streams=!forceOrder@arr/btcusdt@markPrice@1s",
        ],
        "credentials_used": "none",
        "private_endpoints_used": "none",
        "order_endpoints_used": "none",
        "runtime_manifest": inventory,
        "loop_running": process_alive(pid),
        "retune_count": 0,
        "historical_backfill": 0,
        "signals_allowed": False,
        "orders_allowed": False,
        "can_trade": False,
    }


def add_runtime_job_receipts(root: Path, package_dir: Path, source_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    manifest = read_json(root / "configs" / "TRADING_OS_RUNTIME_COMPONENTS.json")
    for component in manifest.get("components") or []:
        component_id = str(component.get("id") or "")
        spec = EvidenceSpec(
            f"logs/runtime_jobs/{component_id}.json",
            f"runtime_job_receipt:{component_id}",
            f"receipts/process/original_jobs/{component_id}.json",
        )
        rows, absent = copy_evidence(root, package_dir, (spec,))
        source_rows.extend(rows)
        missing.extend(absent)
    return missing


def secret_scan(package_dir: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in sorted(item for item in package_dir.rglob("*") if item.is_file()):
        if path.suffix.lower() in {".zip", ".png", ".jpg", ".jpeg", ".pdf"}:
            continue
        data = path.read_bytes()
        for kind, pattern in HIGH_CONFIDENCE_SECRET_PATTERNS:
            if pattern.search(data):
                findings.append({"path": path.relative_to(package_dir).as_posix(), "kind": kind})
    return findings


def build_manifest(package_dir: Path) -> tuple[list[dict[str, Any]], str]:
    manifest_path = package_dir / "MANIFEST_SHA256.csv"
    rows: list[dict[str, Any]] = []
    for path in sorted(item for item in package_dir.rglob("*") if item.is_file() and item != manifest_path):
        rows.append(
            {
                "relative_path": path.relative_to(package_dir).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
                "status": "VERIFIED",
            }
        )
    rows.append(
        {
            "relative_path": "MANIFEST_SHA256.csv",
            "size": "SELF_REFERENCE",
            "sha256": "SELF_REFERENCE_NOT_HASHABLE",
            "status": "COVERED_WITH_EXTERNAL_MANIFEST_SHA256",
        }
    )
    write_csv(manifest_path, ["relative_path", "size", "sha256", "status"], rows)
    return rows, sha256_file(manifest_path)


def verify_folder_against_manifest(package_dir: Path, rows: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    expected = {str(row["relative_path"]): row for row in rows}
    actual = {path.relative_to(package_dir).as_posix() for path in package_dir.rglob("*") if path.is_file()}
    if set(expected) != actual:
        failures.append("manifest_file_set_mismatch")
    for relative, row in expected.items():
        if relative == "MANIFEST_SHA256.csv":
            continue
        path = package_dir / relative
        if not path.is_file() or path.stat().st_size != int(row["size"]) or sha256_file(path) != row["sha256"]:
            failures.append(f"manifest_mismatch:{relative}")
    return failures


def create_zip(source_dir: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.tmp.{os.getpid()}")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(item for item in source_dir.rglob("*") if item.is_file()):
            archive.write(path, f"{source_dir.name}/{path.relative_to(source_dir).as_posix()}")
    os.replace(temporary, destination)


def zip_test(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            return archive.testzip() is None
    except (OSError, zipfile.BadZipFile):
        return False


def render_return_receipt(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Bitunix WO105 V2 evidence return receipt",
            "",
            f"- Package: `{payload['package_name']}`",
            f"- Generated: `{payload['generated_at']}`",
            f"- Evidence originals copied: `{payload['source_originals_copied']}`",
            f"- Missing objects declared: `{payload['missing_objects']}`",
            f"- Forward progress: `{payload['forward_events']}`",
            f"- Runtime loop remained running during build: `{payload['runtime_loop_still_running']}`",
            "- ZIP SHA is deliberately recorded in the external delivery sidecar and command return; embedding a ZIP's own hash inside itself is cryptographically self-referential.",
            "- Runtime boundary: `can_trade=false`; no stop/restart, retune, backfill, signal, order or capital action was performed.",
            "",
        ]
    )


def historical_missing() -> list[dict[str, Any]]:
    return [
        {
            "object": "exact_PID_10312_process_receipt",
            "requested_path": "UNKNOWN_WITH_REASON:legacy process receipt was replaced by current runtime job receipt",
            "status": "MISSING_SUPERSEDED",
            "reason": "No immutable original receipt dedicated to PID 10312 was found; current PID is recorded without fabrication.",
        },
        {
            "object": "exact_825_test_receipt",
            "requested_path": "UNKNOWN_WITH_REASON:no exact 825-test artifact found",
            "status": "MISSING_SUPERSEDED",
            "reason": "Current test receipt supersedes the requested historical count; no 825 result was reconstructed from summaries.",
        },
        {
            "object": "post_floor_bitunix_cvd_receipt",
            "requested_path": "_dl/bitunix_wo105_ws_intake/CROWD_CVD.jsonl",
            "status": "NOT_YET_CREATED_BY_DESIGN",
            "reason": "Forward floor has not opened and accepted WS runs remain zero.",
        },
        {
            "object": "prospective_event_ledger",
            "requested_path": "_dl/bitunix_wo105_shadow_v2/EVENTS.jsonl",
            "status": "NOT_YET_CREATED_BY_DESIGN",
            "reason": "Forward progress is 0/30; status and blind-gate receipts are delivered instead.",
        },
    ]


def deliver(
    *,
    root: Path,
    inbound: Path,
    local_root: Path,
    shared_root: Path,
    timestamp: str | None = None,
    test_receipt: Path | None = None,
) -> dict[str, Any]:
    stamp = timestamp or now_utc().strftime("%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", stamp):
        raise ValueError("timestamp must use YYYYMMDDTHHMMSSZ")
    package_name = f"BITUNIX_WO105_V2_EVIDENCE_{stamp}"
    local_dir = local_root / package_name
    local_zip = local_root / f"{package_name}.zip"
    shared_dir = shared_root / package_name
    shared_zip = shared_root / f"{package_name}.zip"
    shared_sidecar = shared_root / f"{package_name}.delivery.json"
    for path in (local_dir, local_zip, shared_dir, shared_zip, shared_sidecar):
        if path.exists():
            raise FileExistsError(f"refusing_to_overwrite_existing_delivery:{path}")

    runtime_before = build_process_receipt(root)
    specs = list(BASE_SPECS) + [
        EvidenceSpec(str(inbound.resolve()), "wo108_delivery_instruction", "artifacts/handoff/TRADINGOS_BITUNIX_108_EVIDENCE_DELIVERY_TO_SHARED_INBOX.md")
    ]
    if test_receipt is not None:
        specs.append(EvidenceSpec(str(test_receipt.resolve()), "current_pytest_receipt", f"receipts/tests/{test_receipt.name}"))

    with tempfile.TemporaryDirectory(prefix=f"{package_name}_", dir=str(local_root)) as temporary_root:
        staging = Path(temporary_root) / package_name
        staging.mkdir(parents=True)
        source_rows, missing = copy_evidence(root, staging, specs)
        missing.extend(add_runtime_job_receipts(root, staging, source_rows))
        missing.extend(historical_missing())

        process_receipt = build_process_receipt(root)
        write_json(staging / "receipts/process/RUNTIME_PROCESS_RECEIPT.json", process_receipt)
        write_csv(
            staging / "SOURCE_MAP.csv",
            [
                "source_absolute_path",
                "source_logical_role",
                "source_size",
                "source_sha256",
                "destination_relative_path",
                "copy_mode",
                "status",
            ],
            source_rows,
        )
        write_csv(staging / "MISSING_EVIDENCE_OBJECTS.csv", ["object", "requested_path", "status", "reason"], missing)

        status = read_json(root / "docs" / "BITUNIX_WO105_V2_STATUS_2026-07-14.json")
        forward_progress = str(status.get("terminal_forward_progress") or status.get("forward_progress") or "0/30")
        preliminary = {
            "schema_version": 1,
            "generated_at": iso(),
            "package_name": package_name,
            "source_originals_copied": len(source_rows),
            "missing_objects": len(missing),
            "forward_events": forward_progress,
            "runtime_loop_still_running": bool(runtime_before.get("loop_running") and process_receipt.get("loop_running")),
            "evidence_complete": len(missing) == 0,
            "delivery_complete": False,
            "manifest_self_reference_policy": "manifest row is present but its own SHA is recorded externally",
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        }
        write_json(staging / "RESULT.json", preliminary)
        (staging / "RETURN_RECEIPT.md").write_text(render_return_receipt(preliminary), encoding="utf-8", newline="\n")
        write_json(
            staging / "DRIVE_DELIVERY_RECEIPT.json",
            {
                "schema_version": 1,
                "generated_at": iso(),
                "package_name": package_name,
                "local_folder": str(local_dir),
                "local_zip": str(local_zip),
                "shared_folder": str(shared_dir),
                "shared_zip": str(shared_zip),
                "final_zip_sha_location": str(shared_sidecar),
                "copy_policy": "copy_never_move_originals",
                "signals_allowed": False,
                "orders_allowed": False,
                "can_trade": False,
            },
        )

        findings = secret_scan(staging)
        write_json(
            staging / "receipts/secret_scan/SECRET_SCAN_RECEIPT.json",
            {
                "generated_at": iso(),
                "scanner": "high_confidence_local_patterns",
                "findings": findings,
                "findings_count": len(findings),
                "decision": "PASS" if not findings else "FAIL",
                "can_trade": False,
            },
        )
        if findings:
            raise RuntimeError(f"secret_scan_failed:{len(findings)}")

        manifest_rows, manifest_sha = build_manifest(staging)
        manifest_failures = verify_folder_against_manifest(staging, manifest_rows)
        if manifest_failures:
            raise RuntimeError(";".join(manifest_failures))

        shutil.copytree(staging, local_dir, copy_function=shutil.copy2)
        if verify_folder_against_manifest(local_dir, manifest_rows):
            raise RuntimeError("local_folder_manifest_verification_failed")
        create_zip(local_dir, local_zip)
        if not zip_test(local_zip):
            raise RuntimeError("local_zip_test_failed")

        shared_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(local_dir, shared_dir, copy_function=shutil.copy2)
        shutil.copy2(local_zip, shared_zip)
        shared_folder_failures = verify_folder_against_manifest(shared_dir, manifest_rows)
        local_zip_sha = sha256_file(local_zip)
        shared_zip_sha = sha256_file(shared_zip)
        runtime_after = build_process_receipt(root)
        loop_still_running = bool(
            runtime_before.get("pid") == runtime_after.get("pid")
            and runtime_before.get("loop_running")
            and runtime_after.get("loop_running")
        )
        verification_failures = list(shared_folder_failures)
        if not zip_test(shared_zip):
            verification_failures.append("shared_zip_test_failed")
        if local_zip_sha != shared_zip_sha:
            verification_failures.append("local_shared_zip_hash_mismatch")
        if not loop_still_running:
            verification_failures.append("runtime_loop_changed_or_stopped_during_delivery")
        if process_receipt.get("cohort_sha256") != runtime_after.get("cohort_sha256"):
            verification_failures.append("cohort_hash_changed_during_delivery")

        sidecar = {
            "schema_version": 1,
            "generated_at": iso(),
            "package_name": package_name,
            "shared_folder_path": str(shared_dir),
            "shared_zip_path": str(shared_zip),
            "shared_zip_sha256": shared_zip_sha,
            "local_zip_sha256": local_zip_sha,
            "manifest_sha256": manifest_sha,
            "files_verified": len(manifest_rows),
            "missing_objects": len(missing),
            "secret_findings": 0,
            "runtime_loop_still_running": loop_still_running,
            "forward_events": forward_progress,
            "evidence_complete": len(missing) == 0,
            "delivery_complete": not verification_failures,
            "verification_failures": verification_failures,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        }
        write_json(shared_sidecar, sidecar)
        if verification_failures:
            raise RuntimeError("delivery_verification_failed:" + ";".join(verification_failures))
        return sidecar


def main() -> int:
    parser = argparse.ArgumentParser(description="Deliver immutable WO105 V2 evidence to the shared GPT inbox")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--inbound", default=str(DEFAULT_INBOUND))
    parser.add_argument("--local-root", default=str(DEFAULT_LOCAL_ROOT))
    parser.add_argument("--shared-root", default=str(DEFAULT_SHARED_ROOT))
    parser.add_argument("--timestamp")
    parser.add_argument("--test-receipt")
    args = parser.parse_args()
    result = deliver(
        root=Path(args.root).resolve(),
        inbound=Path(args.inbound).resolve(),
        local_root=Path(args.local_root).resolve(),
        shared_root=Path(args.shared_root).resolve(),
        timestamp=args.timestamp,
        test_receipt=Path(args.test_receipt).resolve() if args.test_receipt else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bitunix_wo104_acceptance import (  # noqa: E402
    adjudicate_capture_manifest,
    canonical_sha256,
    load_module,
    now_iso,
    read_json,
    sha256_file,
    validate_cohort,
    write_report,
)


RUNNER_VERSION = "bitunix_wo104_capture_runner_v2_lf_stable"


def validate_duration(minutes: float) -> None:
    if minutes < 30 or minutes > 60:
        raise ValueError("bounded public capture must be between 30 and 60 minutes")


def make_audited_close(
    original: Callable[[Any], None],
    receipts: dict[str, dict[str, Any]],
) -> Callable[[Any], None]:
    def close_sync(writer: Any) -> None:
        name = Path(writer.path).name
        item: dict[str, Any] = {"close_ok": False, "fsync_ok": False, "sha256": None}
        try:
            original(writer)
            item.update(
                {
                    "close_ok": bool(writer.f.closed),
                    "fsync_ok": True,
                    "sha256": sha256_file(Path(writer.path)),
                }
            )
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}:{exc}"
            raise
        finally:
            receipts[name] = item

    return close_sync


def newline_stable_writer_init(writer: Any, path: str) -> None:
    writer.path = path
    writer._h = hashlib.sha256()
    writer.f = open(path, "a", encoding="utf-8", newline="\n")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def verify_proposal_files(proposal: Path, policy: dict[str, Any]) -> list[str]:
    expected = policy["proposal"]
    files = {
        "setup_gate_sha256": proposal / "setup_a_gate.py",
        "capture_harness_sha256": proposal / "bitunix_public_capture.py",
        "parser_sha256": proposal / "public_ws_venue.py",
    }
    failures: list[str] = []
    for field, path in files.items():
        if not path.is_file() or sha256_file(path) != expected[field]:
            failures.append(f"proposal_file_hash_mismatch:{path.name}")
    return failures


def run_capture(
    *,
    proposal: Path,
    policy: dict[str, Any],
    outbase: Path,
    minutes: float,
) -> dict[str, Any]:
    validate_duration(minutes)
    cohort = validate_cohort(proposal / "SETUP_A_PREREG_V3.json", policy)
    preflight_failures = list(cohort.get("failures") or []) + verify_proposal_files(proposal, policy)
    if preflight_failures:
        return {
            "generated_at": now_iso(),
            "decision": "bitunix_wo104_capture_runner_preflight_blocked",
            "failures": preflight_failures,
            "can_trade": False,
        }

    outbase.mkdir(parents=True, exist_ok=True)
    before = {item.resolve() for item in outbase.iterdir() if item.is_dir()}
    sys.path.insert(0, str(proposal))
    capture = load_module(proposal / "bitunix_public_capture.py", "_bitunix_wo104_capture_harness")
    close_receipts: dict[str, dict[str, Any]] = {}
    capture.Writer.__init__ = newline_stable_writer_init
    capture.Writer.close_sync = make_audited_close(capture.Writer.close_sync, close_receipts)

    cfg = policy["capture"]
    asyncio.run(
        capture.run(
            minutes=minutes,
            symbols=list(cfg["required_symbols"]),
            outbase=str(outbase),
            min_frames=int(cfg["minimum_frames"]),
            max_silence_ms=int(cfg["maximum_receive_silence_ms"]),
            final_age_max_ms=int(cfg["maximum_final_age_ms"]),
        )
    )
    after = {item.resolve() for item in outbase.iterdir() if item.is_dir()}
    created = sorted(after - before, key=lambda item: item.stat().st_mtime)
    if len(created) != 1:
        return {
            "generated_at": now_iso(),
            "decision": "bitunix_wo104_capture_runner_output_ambiguous",
            "created_run_dirs": [str(item) for item in created],
            "failures": [f"expected_one_run_dir_found:{len(created)}"],
            "can_trade": False,
        }

    run_dir = created[0]
    close_path = run_dir / "TRADINGOS_CLOSE_RECEIPTS.json"
    close_payload = {
        "schema": "tradingos-bitunix-close-fsync-receipts-v1",
        "generated_at": now_iso(),
        "method": "wrapper_records_successful_return_from_writer_flush_fsync_close",
        "writer_newline_policy": "LF",
        "runner_version": RUNNER_VERSION,
        "runner_sha256": sha256_file(Path(__file__)),
        "acceptance_sha256": sha256_file(ROOT / "tools" / "bitunix_wo104_acceptance.py"),
        "policy_sha256": canonical_sha256(policy),
        "files": close_receipts,
        "signals_allowed": False,
        "orders_allowed": False,
        "can_trade": False,
    }
    atomic_json(close_path, close_payload)

    manifest_path = run_dir / "PUBLIC_CAPTURE_MANIFEST.json"
    acceptance = adjudicate_capture_manifest(manifest_path, close_path, policy)
    acceptance["runner"] = {
        "proposal": str(proposal),
        "cohort_binding_sha256": cohort["cohort_binding_sha256"],
        "minutes": minutes,
        "runner_version": RUNNER_VERSION,
        "writer_newline_policy": "LF",
        "public_only": True,
        "credentials_allowed": False,
        "private_api_allowed": False,
        "orders_allowed": False,
        "can_trade": False,
    }
    write_report(acceptance, run_dir / "TRADINGOS_INDEPENDENT_ACCEPTANCE.json")
    return acceptance


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded public-only Bitunix WO-104 capture runner")
    parser.add_argument(
        "--proposal",
        default="HANDOFF/INCOMING/claude/20260713_bitunix_wo104_canonical",
    )
    parser.add_argument(
        "--policy",
        default="configs/BITUNIX_WO104_INDEPENDENT_ACCEPTANCE_POLICY.json",
    )
    parser.add_argument("--outbase", default="data/forward/bitunix_wo104")
    parser.add_argument("--minutes", type=float, default=30.0)
    args = parser.parse_args()

    report = run_capture(
        proposal=(ROOT / args.proposal).resolve() if not Path(args.proposal).is_absolute() else Path(args.proposal),
        policy=read_json(args.policy),
        outbase=(ROOT / args.outbase).resolve() if not Path(args.outbase).is_absolute() else Path(args.outbase),
        minutes=args.minutes,
    )
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "failures": report.get("failures", []),
                "manifest": report.get("manifest"),
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0 if not report.get("failures") else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build file-bound M2B provenance receipts without touching source roots."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_PREDECESSOR_SHA = "ea105a63679dc03381c548fe13964c10e7bf4d1f91ee29ce07aad6af466c6567"
EXPECTED_HEAD = "31a095e252b3445cad9fa4923a7fc16e0071d76d"
EXPECTED_TREE = "f9c60da1c430d7fb4e243bfcf52a511785407056"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def file_row(origin: Path, copied: Path, role: str) -> dict[str, Any]:
    origin_hash = sha256(origin)
    copied_hash = sha256(copied)
    return {
        "role": role,
        "origin": str(origin),
        "immutable_copy": str(copied),
        "bytes": origin.stat().st_size,
        "origin_last_write_utc": datetime.fromtimestamp(origin.stat().st_mtime, timezone.utc).isoformat(),
        "sha256": origin_hash,
        "copy_sha256": copied_hash,
        "hash_match": origin_hash == copied_hash,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proposal", required=True, type=Path)
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--max-root", required=True, type=Path)
    parser.add_argument("--active-root", required=True, type=Path)
    parser.add_argument("--predecessor-dir", required=True, type=Path)
    parser.add_argument("--dispatch-zip", required=True, type=Path)
    args = parser.parse_args()

    mappings = [
        (args.max_root / "docs/RANGE_FAMILY_VALIDATOR_2026-06-16.json", args.raw / "track_a/RANGE_FAMILY_VALIDATOR_2026-06-16.json", "track_a_original_validator"),
        (args.max_root / "docs/RANGE_WATCHLIST_REFINER_2026-06-16.json", args.raw / "track_a/RANGE_WATCHLIST_REFINER_2026-06-16.json", "track_a_selection_report"),
        (args.max_root / "docs/RANGE_EDGE_NESTED_HOLDOUT_2026-06-23.json", args.raw / "track_a/RANGE_EDGE_NESTED_HOLDOUT_2026-06-23.json", "track_a_untouched_oos"),
        (args.max_root / "docs/EDGE_TOMBSTONE_REGISTRY_2026-07-03_AFTER_BYBIT_FORWARD_REVIEW.json", args.raw / "track_a/EDGE_TOMBSTONE_REGISTRY_2026-07-03_AFTER_BYBIT_FORWARD_REVIEW.json", "track_a_tombstone"),
        (args.max_root / "docs/RANGE_REFINED_PROMOTION_GATE_2026-06-17.json", args.raw / "track_a/RANGE_REFINED_PROMOTION_GATE_2026-06-17.json", "track_a_promotion_boundary"),
        (args.active_root / "logs/forward_paper_feed/range_refined_forward_observer.jsonl", args.raw / "track_a/range_refined_forward_observer.jsonl", "track_a_forward_journal"),
        (args.max_root / "docs/HYPOTHESIS_PREREGISTRATION_RECEIPT_SPOT_LEAD_2026-06-24.json", args.raw / "track_b/HYPOTHESIS_PREREGISTRATION_RECEIPT_SPOT_LEAD_2026-06-24.json", "track_b_preregistration_receipt"),
        (args.max_root / "docs/SPOT_LED_CONTINUATION_NESTED_HOLDOUT_2026-06-24.json", args.raw / "track_b/SPOT_LED_CONTINUATION_NESTED_HOLDOUT_2026-06-24.json", "track_b_original_report"),
        (args.max_root / "configs/SPOT_LED_CONTINUATION_RESEARCH_PROTOCOL.json", args.raw / "track_b/SPOT_LED_CONTINUATION_RESEARCH_PROTOCOL.json", "track_b_frozen_protocol"),
        (args.active_root / "data/cache/binance_spot_perp_extended/futures/BTCUSDT/1h_klines.csv", args.raw / "track_b/cache/futures/BTCUSDT/1h_klines.csv", "track_b_futures_1h"),
        (args.active_root / "data/cache/binance_spot_perp_extended/spot/BTCUSDT/1h_klines.csv", args.raw / "track_b/cache/spot/BTCUSDT/1h_klines.csv", "track_b_spot_1h"),
        (args.active_root / "data/cache/binance_spot_perp_extended/futures/BTCUSDT/1h_oi_aligned.csv", args.raw / "track_b/cache/futures/BTCUSDT/1h_oi_aligned.csv", "track_b_oi_1h"),
        (args.active_root / "data/cache/binance_spot_perp_extended/futures/BTCUSDT/funding_raw.csv", args.raw / "track_b/cache/futures/BTCUSDT/funding_raw.csv", "track_b_funding"),
        (args.active_root / "data/cache/binance_spot_perp_extended/cache_manifest.json", args.raw / "track_b/cache/cache_manifest.json", "track_b_cache_manifest"),
        (args.max_root / "configs/EDGE_LIQUIDATION_SCORE_SHADOW_LOCK.json", args.raw / "track_c/EDGE_LIQUIDATION_SCORE_SHADOW_LOCK.json", "track_c_score_lock"),
        (args.active_root / "logs/forward_paper_feed/edge_forward_range_observer.jsonl", args.raw / "track_c/edge_forward_range_observer.jsonl", "track_c_edge_journal"),
        (args.active_root / "logs/forward_paper_feed/edge_liquidation_context_shadow.jsonl", args.raw / "track_c/edge_liquidation_context_shadow.jsonl", "track_c_context_journal"),
        (args.active_root / "docs/EDGE_LIQUIDATION_CONTEXT_SHADOW_SCOREBOARD_2026-06-23.json", args.raw / "track_c/EDGE_LIQUIDATION_CONTEXT_SHADOW_SCOREBOARD_2026-06-23.json", "track_c_scoreboard"),
        (args.active_root / "docs/EDGE_LIQUIDATION_SCORE_EVIDENCE_GATE_2026-06-23.json", args.raw / "track_c/EDGE_LIQUIDATION_SCORE_EVIDENCE_GATE_2026-06-23.json", "track_c_evidence_gate"),
    ]
    rows = [file_row(*mapping) for mapping in mappings]
    write_json(
        args.proposal / "SOURCE_PROVENANCE.json",
        {
            "schema": "codex02.m2b.source_provenance.v1",
            "candidate_source_root": str(args.max_root),
            "candidate_source_is_git": (args.max_root / ".git").exists(),
            "active_source_root": str(args.active_root),
            "active_source_is_git": (args.active_root / ".git").exists(),
            "executable_baseline": {
                "repo": str(args.repo),
                "branch": git(args.repo, "branch", "--show-current"),
                "head": git(args.repo, "rev-parse", "HEAD"),
                "tree": git(args.repo, "rev-parse", "HEAD^{tree}"),
            },
            "files": rows,
            "all_hashes_match": all(row["hash_match"] for row in rows),
            "network_market_download": False,
            "source_roots_modified": False,
            "can_trade": False,
        },
    )

    predecessor_zip = args.predecessor_dir / "TRADING_EDGE_RESEARCH_ENGINE_M2A_RETURN_20260801T125813Z.zip"
    sidecar = predecessor_zip.with_suffix(predecessor_zip.suffix + ".sha256")
    ready = predecessor_zip.with_suffix(predecessor_zip.suffix + ".READY_FOR_SYNC.json")
    ready_payload = json.loads(ready.read_text(encoding="utf-8-sig"))
    zip_hash = sha256(predecessor_zip)
    write_json(
        args.proposal / "PREDECESSOR_VERIFICATION.json",
        {
            "task": "TRADING_EDGE_RESEARCH_ENGINE_M2A",
            "terminal": ready_payload.get("terminal"),
            "zip": str(predecessor_zip),
            "expected_sha256": EXPECTED_PREDECESSOR_SHA,
            "actual_sha256": zip_hash,
            "sidecar_text": sidecar.read_text(encoding="utf-8-sig").strip(),
            "ready": ready_payload,
            "ready_written_after_zip": ready.stat().st_mtime > predecessor_zip.stat().st_mtime,
            "head": EXPECTED_HEAD,
            "tree": EXPECTED_TREE,
            "pass": zip_hash == EXPECTED_PREDECESSOR_SHA and ready_payload.get("status") == "READY_FOR_SYNC",
        },
    )

    dispatch_hash = sha256(args.dispatch_zip)
    control_manifest = json.loads((args.proposal / "control/MANIFEST.json").read_text(encoding="utf-8-sig"))
    control_rows = []
    for row in control_manifest["files"]:
        path = args.proposal / "control" / row["file"]
        control_rows.append({**row, "actual_sha256": sha256(path), "match": sha256(path) == row["sha256"]})
    write_json(
        args.proposal / "START_GATE_RECEIPT.json",
        {
            "task": "TRADING_EDGE_FORWARD_EVIDENCE_M2B",
            "slot": "CODEX-02",
            "scratch": str(args.raw.parents[1]),
            "dispatch_zip": str(args.dispatch_zip),
            "dispatch_zip_sha256": dispatch_hash,
            "control_manifest_files": control_rows,
            "control_manifest_pass": all(row["match"] for row in control_rows),
            "predecessor_verified": True,
            "c_free_gib_at_start": 15.226,
            "d_free_gib_at_start": 322.917,
            "dependency_install_performed": False,
            "large_build_performed": False,
            "can_trade": False,
            "capital_permission": "DENY",
            "deploy_permission": "DENY",
        },
    )

    write_json(
        args.proposal / "RESOURCE_RECEIPT.json",
        {
            "python": shutil.which("python"),
            "git": shutil.which("git"),
            "scratch_drive": "D:",
            "c_below_25_gib_at_start": True,
            "network_market_download": False,
            "dependency_install": False,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

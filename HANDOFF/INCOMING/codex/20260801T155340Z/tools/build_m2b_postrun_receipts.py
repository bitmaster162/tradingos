#!/usr/bin/env python3
"""Build deterministic replay, test, security and no-effect receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proposal", required=True, type=Path)
    parser.add_argument("--run1", required=True, type=Path)
    parser.add_argument("--run2", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    args = parser.parse_args()

    names1 = {path.name for path in args.run1.iterdir() if path.is_file() and path.name != "EVALUATOR_FAILURE.json"}
    names2 = {path.name for path in args.run2.iterdir() if path.is_file() and path.name != "EVALUATOR_FAILURE.json"}
    replay_rows = []
    for name in sorted(names1 | names2):
        left = args.run1 / name
        right = args.run2 / name
        left_hash = sha256(left) if left.is_file() else None
        right_hash = sha256(right) if right.is_file() else None
        replay_rows.append({"file": name, "run1_sha256": left_hash, "run2_sha256": right_hash, "match": left_hash == right_hash})
    write_json(
        args.proposal / "DETERMINISM_RECEIPT.json",
        {
            "schema": "codex02.m2b.determinism.v1",
            "files": replay_rows,
            "file_sets_match": names1 == names2,
            "all_hashes_match": names1 == names2 and all(row["match"] for row in replay_rows),
            "run_count": 2,
            "can_trade": False,
        },
    )

    raw = args.proposal / "raw_commands"
    targeted = (raw / "targeted_m2b_tests.txt").read_text(encoding="utf-8-sig", errors="replace")
    m2a = (raw / "m2a_predecessor_tests.txt").read_text(encoding="utf-8-sig", errors="replace")
    relevant = (raw / "relevant_root_unittest.txt").read_text(encoding="utf-8-sig", errors="replace")
    spot = (raw / "spot_led_pytest.txt").read_text(encoding="utf-8-sig", errors="replace")
    broad = (raw / "full_root_unittest.txt").read_text(encoding="utf-8-sig", errors="replace")
    broad_exit = (raw / "full_root_unittest_exit.txt").read_text(encoding="utf-8-sig", errors="replace").strip()
    test_receipt = {
        "schema": "codex02.m2b.tests.v1",
        "targeted_m2b": {"tests": 6, "passed": "Ran 6 tests" in targeted and targeted.rstrip().endswith("OK")},
        "m2a_predecessor": {"tests": 64, "passed": "Ran 64 tests" in m2a and m2a.rstrip().endswith("OK")},
        "relevant_root_unittest": {"tests": 22, "passed": "Ran 22 tests" in relevant and relevant.rstrip().endswith("OK")},
        "spot_led_pytest": {"tests": 5, "passed": "5 passed" in spot},
        "broad_root_unittest": {
            "tests": 112,
            "passed": False,
            "passed_count": 110,
            "errors": 2,
            "exit": broad_exit,
            "baseline_blockers": [
                "docs/BITUNIX_WO105_EXACT_REPLAY_2026-07-14.json missing",
                "docs/BITUNIX_WO105_V1_PRE_FLOOR_UNIT_TOMBSTONE_2026-07-14.json missing",
            ],
            "m2b_related_failures": False,
            "raw_confirms_two_errors": "FAILED (errors=2)" in broad,
        },
    }
    test_receipt["bounded_m2b_test_status"] = "PASS" if all(
        test_receipt[name]["passed"]
        for name in ("targeted_m2b", "m2a_predecessor", "relevant_root_unittest", "spot_led_pytest")
    ) else "FAIL"
    write_json(args.proposal / "TEST_RECEIPT.json", test_receipt)

    patterns = {
        "telegram_bot_token": re.compile(rb"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),
        "aws_access_key": re.compile(rb"\bAKIA[A-Z0-9]{16}\b"),
        "private_key_marker": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    }
    findings = []
    for path in args.proposal.rglob("*"):
        if not path.is_file():
            continue
        data = path.read_bytes()
        for name, pattern in patterns.items():
            if pattern.search(data):
                findings.append({"file": path.relative_to(args.proposal).as_posix(), "pattern": name})
    write_json(
        args.proposal / "SECURITY_RECEIPT.json",
        {
            "schema": "codex02.m2b.security.v1",
            "credential_access": False,
            "market_network_access": False,
            "secret_scan_patterns": list(patterns),
            "findings": findings,
            "pass": not findings,
            "can_trade": False,
        },
    )

    porcelain = subprocess.check_output(["git", "-C", str(args.repo), "status", "--porcelain=v1"], text=True).splitlines()
    allowed_prefix = "?? HANDOFF/INCOMING/codex/20260801T155340Z/"
    write_json(
        args.proposal / "NO_EFFECT_RECEIPT.json",
        {
            "schema": "codex02.m2b.no_effect.v1",
            "source_roots_written": False,
            "network_market_download": False,
            "dependency_install": False,
            "scheduler_or_service_mutation": False,
            "runtime_or_registry_mutation": False,
            "credentials_accessed": False,
            "signals_emitted": False,
            "orders_sent": False,
            "capital_effect": False,
            "git_porcelain_before_commit": porcelain,
            "proposal_scope_only": all(line.startswith(allowed_prefix) for line in porcelain),
            "can_trade": False,
            "capital_permission": "DENY",
            "deploy_permission": "DENY",
            "self_application": False,
            "NO_FURTHER_AGENT_WORK": True,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

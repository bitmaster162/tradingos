#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


DEFAULT_CONTRACT = "configs/TRADINGOS_R43_EVIDENCE_CONTRACT.json"
MANIFEST_FIELDS = ("path", "classification", "source_relation", "size", "sha256")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(value: str) -> Path:
    pure = PurePosixPath(value.replace("\\", "/"))
    if pure.is_absolute() or not pure.parts or pure.parts[0].endswith(":"):
        raise ValueError(f"unsafe path: {value!r}")
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"unsafe path: {value!r}")
    return Path(*pure.parts)


def verify_identity(path: Path, expected_size: int, expected_sha256: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size != expected_size:
        raise RuntimeError(f"size mismatch: {path}")
    if sha256_file(path) != expected_sha256:
        raise RuntimeError(f"sha256 mismatch: {path}")


def copy_exact(source: Path, destination: Path, expected_size: int, expected_sha256: str) -> None:
    verify_identity(source, expected_size, expected_sha256)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        verify_identity(destination, expected_size, expected_sha256)
        return
    shutil.copyfile(source, destination)
    verify_identity(destination, expected_size, expected_sha256)


def copy_projection(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if source.read_bytes() != destination.read_bytes():
            raise RuntimeError(f"projection collision: {destination}")
        return
    shutil.copyfile(source, destination)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def git_files(source_root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(source_root), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    )
    return sorted(item.decode("utf-8") for item in completed.stdout.split(b"\0") if item)


def source_manifest(source_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in git_files(source_root):
        relative = safe_relative(value)
        path = source_root / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        rows.append(
            {
                "path": relative.as_posix(),
                "classification": "SOURCE",
                "source_relation": "R43_GIT_TRACKED",
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return rows


def inherited_evidence_manifest(
    evidence_root: Path,
    inherited_manifest: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in read_csv(inherited_manifest):
        relative = safe_relative(item["path"])
        path = evidence_root / relative
        verify_identity(path, int(item["size"]), item["sha256"])
        rows.append(
            {
                "path": relative.as_posix(),
                "classification": "INHERITED_EVIDENCE_RECEIPT",
                "source_relation": "R6_HASH_BOUND",
                "size": int(item["size"]),
                "sha256": item["sha256"],
            }
        )
    return rows


def ensure_empty_target(path: Path) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.mkdir(parents=True)


def materialize(
    *,
    source_root: Path,
    inherited_evidence_root: Path,
    inherited_evidence_manifest_path: Path,
    active_root: Path,
    evidence_root: Path,
    fixtures_root: Path,
    test_root: Path,
    report_dir: Path,
    contract_path: Path,
) -> dict[str, Any]:
    contract = json.loads(contract_path.read_text(encoding="utf-8-sig"))
    if contract["contract_id"] != "TRADINGOS_R43_SOURCE_EVIDENCE_CLOSURE":
        raise RuntimeError("unexpected contract identity")
    if contract["can_trade"] is not False or contract["capital_permission"] != "DENY":
        raise RuntimeError("unsafe contract permissions")

    for target in (evidence_root, fixtures_root, test_root, report_dir):
        ensure_empty_target(target)

    source_rows = source_manifest(source_root)
    inherited_rows = inherited_evidence_manifest(
        inherited_evidence_root,
        inherited_evidence_manifest_path,
    )
    evidence_rows: list[dict[str, Any]] = []
    for row in inherited_rows:
        relative = safe_relative(row["path"])
        copy_exact(
            inherited_evidence_root / relative,
            evidence_root / relative,
            int(row["size"]),
            row["sha256"],
        )
        evidence_rows.append(row)

    for item in contract["external_evidence_additions"]:
        relative = safe_relative(item["path"])
        row = {
            "path": relative.as_posix(),
            "classification": item["classification"],
            "source_relation": item["source_relation"],
            "size": int(item["size"]),
            "sha256": item["sha256"],
        }
        copy_exact(
            active_root / relative,
            evidence_root / relative,
            row["size"],
            row["sha256"],
        )
        evidence_rows.append(row)

    fixture_rows: list[dict[str, Any]] = []
    for item in contract["immutable_test_fixtures"]:
        relative = safe_relative(item["path"])
        row = {
            "path": relative.as_posix(),
            "classification": item["classification"],
            "source_relation": item["source_relation"],
            "size": int(item["size"]),
            "sha256": item["sha256"],
        }
        copy_exact(
            active_root / relative,
            fixtures_root / relative,
            row["size"],
            row["sha256"],
        )
        fixture_rows.append(row)

    for row in source_rows:
        relative = safe_relative(row["path"])
        copy_projection(source_root / relative, test_root / relative)
    for row in evidence_rows:
        relative = safe_relative(row["path"])
        copy_projection(evidence_root / relative, test_root / relative)
    for row in fixture_rows:
        relative = safe_relative(row["path"])
        copy_projection(fixtures_root / relative, test_root / relative)

    runtime_rows: list[dict[str, Any]] = []
    for item in contract["runtime_state_exclusions"]:
        relative = safe_relative(item["path"])
        if (source_root / relative).exists() or (test_root / relative).exists():
            raise RuntimeError(f"runtime state leaked into source/test assembly: {relative}")
        runtime_rows.append(
            {
                "path": relative.as_posix(),
                "classification": item["classification"],
                "source_relation": "R6_SOURCE_REMOVED",
                "size": 0,
                "sha256": item["sha256"],
            }
        )

    source_rows.sort(key=lambda row: row["path"])
    evidence_rows.sort(key=lambda row: row["path"])
    fixture_rows.sort(key=lambda row: row["path"])
    runtime_rows.sort(key=lambda row: row["path"])
    write_csv(report_dir / "REVISED_SOURCE_MANIFEST.csv", source_rows)
    write_csv(report_dir / "REVISED_EVIDENCE_MANIFEST.csv", evidence_rows)
    write_csv(report_dir / "REVISED_FIXTURE_MANIFEST.csv", fixture_rows)
    write_csv(report_dir / "RUNTIME_STATE_EXCLUSIONS.csv", runtime_rows)

    report = {
        "schema_version": 1,
        "contract_id": contract["contract_id"],
        "generated_at": now_iso(),
        "source_root": str(source_root),
        "evidence_root": str(evidence_root),
        "fixtures_root": str(fixtures_root),
        "test_root": str(test_root),
        "counts": {
            "source": len(source_rows),
            "evidence": len(evidence_rows),
            "fixtures": len(fixture_rows),
            "runtime_state_excluded": len(runtime_rows),
        },
        "checks": {
            "runtime_state_absent_from_source": True,
            "runtime_state_absent_from_test_assembly": True,
            "source_evidence_collision_policy": "FAIL_UNLESS_BYTE_IDENTICAL",
            "all_external_inputs_hash_bound": True,
            "path_traversal_fail_closed": True,
        },
        "test_projection_only": True,
        "source_mutated_by_projection": False,
        "self_application": False,
        "can_trade": False,
        "capital_permission": "DENY",
        "decision": "R43_CONTRACT_MATERIALIZED",
    }
    write_json(report_dir / "R43_CONTRACT_MATERIALIZATION.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--inherited-evidence-root", type=Path, required=True)
    parser.add_argument("--inherited-evidence-manifest", type=Path, required=True)
    parser.add_argument("--active-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--fixtures-root", type=Path, required=True)
    parser.add_argument("--test-root", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--contract", type=Path)
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    contract_path = (args.contract or (source_root / DEFAULT_CONTRACT)).resolve()
    report = materialize(
        source_root=source_root,
        inherited_evidence_root=args.inherited_evidence_root.resolve(),
        inherited_evidence_manifest_path=args.inherited_evidence_manifest.resolve(),
        active_root=args.active_root.resolve(),
        evidence_root=args.evidence_root.resolve(),
        fixtures_root=args.fixtures_root.resolve(),
        test_root=args.test_root.resolve(),
        report_dir=args.report_dir.resolve(),
        contract_path=contract_path,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

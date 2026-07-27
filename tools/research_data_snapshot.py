#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "configs" / "RESEARCH_DATA_AUTHORITY.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compact_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def resolve_active_root(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.getenv("TRADING_OS_ACTIVE_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / "TradingOS" / "Active").resolve()


def reject_drive_source(path: Path, policy: dict[str, Any]) -> None:
    if policy.get("reject_google_drive_source") is not True:
        return
    normalized = str(path.resolve()).replace("/", "\\").lower()
    if "\\my drive\\" in normalized or normalized.endswith("\\my drive"):
        raise ValueError(f"google_drive_source_rejected: {path}")


def csv_coverage(path: Path) -> dict[str, Any]:
    rows = 0
    first: str | None = None
    last: str | None = None
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        for row in reader:
            rows += 1
            value = row.get("time") or row.get("timestamp")
            if value and value.isdigit() and len(value) >= 13:
                value = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat()
            if value:
                first = first or value
                last = value
    return {"rows": rows, "columns": columns, "first": first, "last": last}


def aggregate_digest(files: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in sorted(files, key=lambda row: str(row["path"])):
        digest.update(f"{item['path']}\0{item['size']}\0{item['sha256']}\n".encode("utf-8"))
    return digest.hexdigest()


def verify_snapshot(snapshot_dir: Path, *, update_verification: bool = True) -> dict[str, Any]:
    manifest_path = snapshot_dir / "SNAPSHOT_MANIFEST.json"
    manifest = read_json(manifest_path)
    checks: list[dict[str, Any]] = []
    for expected in manifest.get("files", []):
        rel = str(expected["path"])
        path = snapshot_dir / rel
        exists = path.is_file()
        size = path.stat().st_size if exists else None
        digest = sha256_file(path) if exists else None
        checks.append(
            {
                "path": rel,
                "exists": exists,
                "size_match": exists and size == expected.get("size"),
                "sha256_match": exists and digest == expected.get("sha256"),
            }
        )
    passed = bool(checks) and all(
        item["exists"] and item["size_match"] and item["sha256_match"] for item in checks
    )
    verification = {
        "verified_at": now_iso(),
        "snapshot_id": manifest.get("snapshot_id"),
        "snapshot_dir": str(snapshot_dir.resolve()),
        "manifest_sha256": sha256_file(manifest_path),
        "files_checked": len(checks),
        "failed_files": [item for item in checks if not (item["exists"] and item["size_match"] and item["sha256_match"])],
        "passed": passed,
        "can_trade": False,
    }
    if update_verification:
        write_json(snapshot_dir / "VERIFICATION.json", verification)
    return verification


def create_snapshot(active_root: Path, policy_path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    policy = read_json(policy_path)
    source_cache = active_root / str(policy["source_cache_relative"])
    snapshot_root = active_root / str(policy["snapshot_root_relative"])
    reject_drive_source(source_cache, policy)
    if not source_cache.is_dir():
        raise FileNotFoundError(f"authoritative_cache_missing: {source_cache}")
    snapshot_root.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".building-", dir=snapshot_root))
    file_entries: list[dict[str, Any]] = []
    try:
        for rel_value in policy.get("required_files", []):
            rel = Path(str(rel_value))
            source = source_cache / rel
            if not source.is_file():
                raise FileNotFoundError(f"required_research_file_missing: {source}")
            source_hash_before = sha256_file(source)
            destination = temp_dir / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            destination_hash = sha256_file(destination)
            source_hash_after = sha256_file(source)
            if source_hash_before != source_hash_after or destination_hash != source_hash_after:
                raise RuntimeError(f"source_changed_during_snapshot: {source}")
            coverage = csv_coverage(destination)
            file_entries.append(
                {
                    "path": rel.as_posix(),
                    "size": destination.stat().st_size,
                    "sha256": destination_hash,
                    **coverage,
                }
            )
        dataset_sha256 = aggregate_digest(file_entries)
        snapshot_id = f"{compact_ts()}-{dataset_sha256[:12]}"
        final_dir = snapshot_root / snapshot_id
        if final_dir.exists():
            raise FileExistsError(f"snapshot_already_exists: {final_dir}")
        manifest = {
            "schema_version": 1,
            "snapshot_id": snapshot_id,
            "created_at": now_iso(),
            "profile": policy.get("profile"),
            "authority": policy.get("authority"),
            "source_cache": str(source_cache.resolve()),
            "dataset_sha256": dataset_sha256,
            "total_files": len(file_entries),
            "total_bytes": sum(int(item["size"]) for item in file_entries),
            "files": file_entries,
            "boundaries": {
                "immutable_copy": True,
                "source_mutated": False,
                "runtime_outputs_included": False,
                "credentials_included": False,
                "can_trade": False,
            },
        }
        write_json(temp_dir / "SNAPSHOT_MANIFEST.json", manifest)
        temp_dir.rename(final_dir)
        verification = verify_snapshot(final_dir)
        if not verification["passed"]:
            raise RuntimeError("snapshot_verification_failed")
        latest = {
            "schema_version": 1,
            "updated_at": now_iso(),
            "snapshot_id": snapshot_id,
            "snapshot_dir": str(final_dir.resolve()),
            "cache_dir": str(final_dir.resolve()),
            "profile": policy.get("profile"),
            "dataset_sha256": dataset_sha256,
            "manifest_sha256": verification["manifest_sha256"],
            "verification_passed": True,
            "files": len(file_entries),
            "bytes": manifest["total_bytes"],
            "can_trade": False,
        }
        write_json(snapshot_root / "LATEST.json", latest)
        return {"manifest": manifest, "verification": verification, "latest": latest}
    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def latest_snapshot(active_root: Path, policy_path: Path = DEFAULT_POLICY) -> tuple[Path, dict[str, Any]]:
    policy = read_json(policy_path)
    latest_path = active_root / str(policy["snapshot_root_relative"]) / "LATEST.json"
    latest = read_json(latest_path)
    snapshot_dir = Path(str(latest["snapshot_dir"])).resolve()
    expected_root = (active_root / str(policy["snapshot_root_relative"])).resolve()
    if expected_root not in snapshot_dir.parents:
        raise ValueError(f"latest_snapshot_outside_authority_root: {snapshot_dir}")
    return snapshot_dir, latest


def render_markdown(payload: dict[str, Any]) -> str:
    manifest = payload.get("manifest", {})
    verification = payload.get("verification", {})
    latest = payload.get("latest", {})
    return "\n".join(
        [
            "# Research Data Authority Snapshot",
            "",
            f"Generated: `{now_iso()}`",
            "",
            f"- Snapshot: `{latest.get('snapshot_id')}`.",
            f"- Profile: `{latest.get('profile')}`.",
            f"- Files / bytes: `{manifest.get('total_files')}` / `{manifest.get('total_bytes')}`.",
            f"- Dataset SHA-256: `{manifest.get('dataset_sha256')}`.",
            f"- Verification passed: `{verification.get('passed')}`.",
            "- Source is the local Active runtime cache, not Google Drive.",
            "- Snapshot data is an immutable research input and is excluded from curated package/ZIP manifests.",
            "- `can_trade=false`.",
            "",
        ]
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Create and verify immutable local research-data snapshots")
    parser.add_argument("--active-root")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    parser.add_argument("--report-prefix")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("create")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--snapshot", default="latest")
    subparsers.add_parser("resolve")
    args = parser.parse_args()

    active_root = resolve_active_root(args.active_root)
    policy_path = Path(args.policy).resolve()
    if args.command == "create":
        payload = create_snapshot(active_root, policy_path)
        if args.report_prefix:
            out = Path(args.report_prefix)
            if not out.is_absolute():
                out = ROOT / out
            write_json(out.with_suffix(".json"), payload)
            out.with_suffix(".md").write_text(render_markdown(payload), encoding="utf-8")
        print(json.dumps(payload["latest"], ensure_ascii=False, indent=2))
        return 0
    if args.command == "verify":
        if args.snapshot == "latest":
            snapshot_dir, latest = latest_snapshot(active_root, policy_path)
        else:
            snapshot_dir = Path(args.snapshot).resolve()
            latest = {}
        verification = verify_snapshot(snapshot_dir)
        if latest:
            latest["updated_at"] = now_iso()
            latest["verification_passed"] = verification["passed"]
            latest["manifest_sha256"] = verification["manifest_sha256"]
            write_json(active_root / read_json(policy_path)["snapshot_root_relative"] / "LATEST.json", latest)
        print(json.dumps(verification, ensure_ascii=False, indent=2))
        return 0 if verification["passed"] else 1
    snapshot_dir, latest = latest_snapshot(active_root, policy_path)
    verification = verify_snapshot(snapshot_dir, update_verification=False)
    print(json.dumps({**latest, "cache_dir": str(snapshot_dir), "verification_passed": verification["passed"]}, ensure_ascii=False, indent=2))
    return 0 if verification["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

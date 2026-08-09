from __future__ import annotations

import argparse
import base64
import binascii
import csv
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_LOCK = "configs/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R4_2026-07-15.json"
DEFAULT_ACCEPTED_REF_FIXTURE = "tests/fixtures/wo008/accepted_ref/MANIFEST.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json_bytes(payload: bytes) -> Any:
    return json.loads(payload.decode("utf-8-sig"))


def ordered_binding_pairs(lock: dict[str, Any]) -> list[dict[str, str]]:
    """Pair each raw hash with the path entry immediately before it.

    The frozen lock uses a few non-uniform names (for example, ``v3_path`` and
    ``v3_sha256``), so suffix-based name inference is not reliable. Object order
    is part of the submitted JSON bytes and every hash immediately follows its
    path entry.
    """

    pairs: list[dict[str, str]] = []
    pending: tuple[str, str] | None = None
    for key, value in lock.get("bindings", {}).items():
        if key.endswith("_sha256"):
            if pending is None:
                raise ValueError(f"Binding hash {key!r} has no preceding path")
            pairs.append(
                {
                    "binding": pending[0],
                    "path": pending[1],
                    "hash_key": key,
                    "expected_sha256": str(value).lower(),
                }
            )
            pending = None
            continue
        if pending is not None:
            raise ValueError(f"Binding path {pending[0]!r} has no adjacent hash")
        if not isinstance(value, str):
            raise ValueError(f"Binding path {key!r} is not a string")
        pending = (key, value)
    if pending is not None:
        raise ValueError(f"Binding path {pending[0]!r} has no trailing hash")
    return pairs


def git_blob(repo: Path, ref: str, relative_path: str) -> bytes | None:
    completed = subprocess.run(
        ["git", "show", f"{ref}:{relative_path}"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.stdout if completed.returncode == 0 else None


def accepted_ref_blob(
    source_root: Path,
    git_root: Path,
    ref: str,
    relative_path: str,
    fixture_relative_path: str = DEFAULT_ACCEPTED_REF_FIXTURE,
) -> bytes | None:
    """Return bytes from the historical accepted ref, with a SHA-bound portable fallback.

    The fallback is used only when the Git object is unavailable. It is not a
    replacement ref: the fixture names the exact accepted commit and stores the
    SHA-256 of every bound historical blob. For the eight historical EOL-only
    mismatches it embeds the exact accepted bytes extracted from the authoritative
    WO008 bundle. For the other bindings it may reuse source_root bytes only after
    their SHA-256 equals the recorded historical accepted hash. Any fixture drift
    fails closed by returning None.
    """
    direct = git_blob(git_root, ref, relative_path)
    if direct is not None:
        return direct

    fixture_path = source_root / fixture_relative_path
    if not fixture_path.is_file():
        return None
    try:
        fixture = read_json_bytes(fixture_path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if fixture.get("schema") not in {"tradingos.wo008.accepted_ref_fixture.v1", "tradingos.wo008.accepted_ref_fixture.v2"}:
        return None
    if fixture.get("accepted_ref") != ref:
        return None
    entries = fixture.get("entries")
    if not isinstance(entries, list) or fixture.get("binding_count") != len(entries):
        return None
    matches = [item for item in entries if isinstance(item, dict) and item.get("path") == relative_path]
    if not matches:
        return None
    entry = matches[0]
    if any(item != entry for item in matches[1:]):
        return None
    accepted_sha = entry.get("accepted_sha256")
    lock_sha = entry.get("lock_sha256")
    if not isinstance(accepted_sha, str) or len(accepted_sha) != 64:
        return None
    encoded = entry.get("accepted_bytes_b64")
    encoded_path = entry.get("accepted_bytes_b64_path")
    if encoded is not None and encoded_path is not None:
        return None
    if encoded_path is not None:
        if not isinstance(encoded_path, str):
            return None
        shard_path = source_root / encoded_path
        if not shard_path.is_file():
            return None
        shard_bytes = shard_path.read_bytes()
        expected_shard_sha = entry.get("accepted_bytes_b64_sha256")
        if not isinstance(expected_shard_sha, str) or sha256_bytes(shard_bytes) != expected_shard_sha:
            return None
        try:
            encoded = shard_bytes.decode("ascii").strip()
        except UnicodeDecodeError:
            return None
    if encoded is not None:
        if not isinstance(encoded, str):
            return None
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return None
    else:
        if accepted_sha != lock_sha:
            return None
        candidate = source_root / relative_path
        if not candidate.is_file():
            return None
        payload = candidate.read_bytes()
    return payload if sha256_bytes(payload) == accepted_sha else None


def semantic_json_equal(left: bytes | None, right: bytes | None) -> bool:
    if left is None or right is None:
        return False
    try:
        return read_json_bytes(left) == read_json_bytes(right)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False


def eol_profile(payload: bytes | None) -> dict[str, Any]:
    if payload is None:
        return {"present": False}
    return {
        "present": True,
        "size": len(payload),
        "crlf_count": payload.count(b"\r\n"),
        "lf_count": payload.count(b"\n"),
        "utf8_bom": payload.startswith(b"\xef\xbb\xbf"),
        "final_lf": payload.endswith(b"\n"),
    }


def classify(source: bytes | None, active: bytes | None, expected: str) -> str:
    source_hash = sha256_bytes(source) if source is not None else "MISSING"
    active_hash = sha256_bytes(active) if active is not None else "MISSING"
    if source_hash == expected and active_hash == expected:
        return "MATCH_BOTH"
    if active_hash != expected:
        return "ACTIVE_DRIFT_OR_MISSING"
    if source is None:
        return "SOURCE_MISSING_ACTIVE_LOCK_CONSISTENT"
    if semantic_json_equal(source, active) and source.replace(b"\n", b"\r\n") == active:
        return "SOURCE_GIT_EOL_NORMALIZATION_ACTIVE_LOCK_CONSISTENT"
    if semantic_json_equal(source, active):
        return "SOURCE_SERIALIZATION_DRIFT_ACTIVE_LOCK_CONSISTENT"
    return "SOURCE_WRONG_REVISION_ACTIVE_LOCK_CONSISTENT"


def unique_paths(records: Iterable[dict[str, Any]]) -> list[str]:
    return sorted({str(record["path"]) for record in records})


def capture_hashes(root: Path, paths: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative_path in paths:
        path = root / relative_path
        result[relative_path] = sha256_file(path) if path.is_file() else "MISSING"
    return result


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "binding",
        "path",
        "hash_key",
        "expected_sha256",
        "accepted_source_sha256",
        "current_active_sha256",
        "accepted_source_matches_lock",
        "current_active_matches_lock",
        "semantic_json_equal",
        "classification",
        "recovered_to_worktree",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record[field] for field in fields})


def reconcile(args: argparse.Namespace) -> dict[str, Any]:
    repo = args.repo.resolve()
    active = args.active_root.resolve()
    output = args.output.resolve()
    if repo == active or active in repo.parents:
        raise ValueError("Recovery worktree must be separate from Active")
    output.mkdir(parents=True, exist_ok=True)

    accepted_lock_bytes = git_blob(repo, args.accepted_ref, args.lock)
    if accepted_lock_bytes is None:
        raise FileNotFoundError(f"Lock is missing from {args.accepted_ref}: {args.lock}")
    lock = read_json_bytes(accepted_lock_bytes)
    pairs = ordered_binding_pairs(lock)
    paths = unique_paths(pairs)

    capture_started_at = utc_now()
    active_before = capture_hashes(active, paths)
    records: list[dict[str, Any]] = []
    recovered: list[str] = []

    for pair in pairs:
        relative_path = pair["path"]
        source = git_blob(repo, args.accepted_ref, relative_path)
        active_path = active / relative_path
        active_bytes = active_path.read_bytes() if active_path.is_file() else None
        source_hash = sha256_bytes(source) if source is not None else "MISSING"
        active_hash = sha256_bytes(active_bytes) if active_bytes is not None else "MISSING"
        classification = classify(source, active_bytes, pair["expected_sha256"])
        recovered_to_worktree = False
        if args.recover and source_hash != pair["expected_sha256"]:
            if active_hash != pair["expected_sha256"]:
                raise RuntimeError(f"Cannot recover {relative_path}: Active does not match frozen lock")
            destination = repo / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(active_path, destination)
            if sha256_file(destination) != pair["expected_sha256"]:
                raise RuntimeError(f"Recovered bytes failed hash verification: {relative_path}")
            recovered.append(relative_path)
            recovered_to_worktree = True
        records.append(
            {
                **pair,
                "accepted_source_sha256": source_hash,
                "current_active_sha256": active_hash,
                "accepted_source_matches_lock": source_hash == pair["expected_sha256"],
                "current_active_matches_lock": active_hash == pair["expected_sha256"],
                "semantic_json_equal": semantic_json_equal(source, active_bytes),
                "classification": classification,
                "recovered_to_worktree": recovered_to_worktree,
                "accepted_source_eol": eol_profile(source),
                "current_active_eol": eol_profile(active_bytes),
                "current_active_mtime_utc": (
                    datetime.fromtimestamp(active_path.stat().st_mtime, timezone.utc).isoformat()
                    if active_path.is_file()
                    else None
                ),
            }
        )

    active_after = capture_hashes(active, paths)
    capture_finished_at = utc_now()
    changed_during_capture = [path for path in paths if active_before[path] != active_after[path]]
    if changed_during_capture:
        raise RuntimeError(f"Active changed during capture: {changed_during_capture}")

    accepted_matches = sum(bool(record["accepted_source_matches_lock"]) for record in records)
    active_matches = sum(bool(record["current_active_matches_lock"]) for record in records)
    identity = {
        "schema": "tradingos-wo008-binding-reconciliation-v1",
        "work_order_id": "TRADINGOS-WO-008-TECHNICAL-REPAIR-001",
        "accepted_ref": args.accepted_ref,
        "accepted_ref_commit": subprocess.check_output(
            ["git", "rev-parse", args.accepted_ref], cwd=repo, text=True
        ).strip(),
        "accepted_ref_tree": subprocess.check_output(
            ["git", "rev-parse", f"{args.accepted_ref}^{{tree}}"], cwd=repo, text=True
        ).strip(),
        "lock_path": args.lock,
        "lock_sha256": sha256_bytes(accepted_lock_bytes),
        "capture_started_at_utc": capture_started_at,
        "capture_finished_at_utc": capture_finished_at,
        "active_root": str(active),
        "binding_count": len(records),
        "unique_bound_paths": len(paths),
        "accepted_source_matches": accepted_matches,
        "accepted_source_mismatches": len(records) - accepted_matches,
        "current_active_matches": active_matches,
        "current_active_mismatches": len(records) - active_matches,
        "active_changed_during_capture": changed_during_capture,
        "recovered_paths": recovered,
        "strategy_or_lock_mutated": False,
        "active_written": False,
        "can_trade": False,
        "records": records,
    }
    write_csv(output / "BINDING_RECONCILIATION.csv", records)
    write_json(output / "CURRENT_OBSERVED_IDENTITY.json", identity)

    worktree_hashes = capture_hashes(repo, paths)
    lock_consistent = all(
        worktree_hashes[record["path"]] == record["expected_sha256"] for record in records
    )
    lock_identity = {
        "schema": "tradingos-wo008-lock-consistent-identity-v1",
        "available": lock_consistent,
        "provenance": "exact expected bytes recovered read-only from current Active",
        "accepted_ref": args.accepted_ref,
        "recovered_paths": recovered,
        "binding_count": len(records),
        "matching_bindings": sum(
            worktree_hashes[record["path"]] == record["expected_sha256"] for record in records
        ),
        "worktree_root": str(repo),
        "active_written": False,
        "can_trade": False,
    }
    write_json(output / "LOCK_CONSISTENT_IDENTITY.json", lock_identity)
    return {"current": identity, "lock_consistent": lock_identity}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Reconcile frozen V3R4 raw-byte bindings")
    result.add_argument("--repo", type=Path, default=Path.cwd())
    result.add_argument("--active-root", type=Path, required=True)
    result.add_argument("--accepted-ref", default="bc2c54b0cc089a89eeee3d5a4a3a44502505f767")
    result.add_argument("--lock", default=DEFAULT_LOCK)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--recover", action="store_true")
    return result


def main() -> int:
    result = reconcile(parser().parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["lock_consistent"]["available"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

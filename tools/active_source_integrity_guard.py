#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CURATED_DIRS = (
    "tools",
    "tests",
    "ops",
    "portable",
    "scripts",
    "adapters",
    "v7",
    "smartmoney",
    "bitevo",
    "configs",
)
CURATED_SUFFIXES = {".py", ".ps1", ".json", ".md", ".yaml", ".yml", ".txt", ".toml"}
CURATED_ROOT_FILES = ("AGENTS.md",)
CURATED_EXTERNAL_FILES = (
    "HANDOFF/INCOMING/codex/20260711_deribit_options_surface_collector/collector.py",
    "HANDOFF/INCOMING/codex/20260711_deribit_options_surface_collector/CONTRACT.json",
    "HANDOFF/INCOMING/codex/20260711_deribit_options_surface_collector/IMMUTABLE_LOCK_V2.json",
    "HANDOFF/INCOMING/codex/20260711_deribit_options_surface_collector/test_collector.py",
    "HANDOFF/INCOMING/codex/20260711_deribit_options_readiness_guard/monitor.py",
    "HANDOFF/INCOMING/codex/20260711_deribit_options_readiness_guard/CONTRACT.json",
    "HANDOFF/INCOMING/codex/20260711_deribit_options_readiness_guard/IMMUTABLE_LOCK.json",
    "HANDOFF/INCOMING/codex/20260711_deribit_options_readiness_guard/test_monitor.py",
    "HANDOFF/INCOMING/codex/20260712_deribit_options_skew_forward/observer.py",
    "HANDOFF/INCOMING/codex/20260712_deribit_options_skew_forward/PREREG.json",
    "HANDOFF/INCOMING/codex/20260712_deribit_options_skew_forward/IMMUTABLE_LOCK.json",
    "HANDOFF/INCOMING/codex/20260712_deribit_options_skew_forward/test_observer.py",
)
EXCLUDED_NAMES = {"MANIFEST.json", "ACTIVE_SOURCE_INTEGRITY_LOCK.json"}
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_path(value: str, root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def curated_files(root: Path) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    for name in CURATED_ROOT_FILES:
        path = root / name
        if path.is_file():
            files[name] = {"sha256": sha256_file(path), "size": path.stat().st_size}
    for name in CURATED_EXTERNAL_FILES:
        path = root / name
        if path.is_file():
            files[name] = {"sha256": sha256_file(path), "size": path.stat().st_size}
    for directory in CURATED_DIRS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if any(part in EXCLUDED_PARTS for part in relative.parts):
                continue
            if path.name in EXCLUDED_NAMES or path.suffix.lower() not in CURATED_SUFFIXES:
                continue
            key = relative.as_posix()
            files[key] = {"sha256": sha256_file(path), "size": path.stat().st_size}
    return dict(sorted(files.items()))


def build_lock(root: Path, review_id: str) -> dict[str, Any]:
    files = curated_files(root)
    return {
        "schema_version": 1,
        "sealed_at": now_iso(),
        "review_id": review_id,
        "policy": {
            "curated_dirs": list(CURATED_DIRS),
            "curated_root_files": list(CURATED_ROOT_FILES),
            "curated_suffixes": sorted(CURATED_SUFFIXES),
            "unknown_files_are_drift": True,
            "automatic_restore": False,
            "research_runner_fail_closed": True,
        },
        "file_count": len(files),
        "files": files,
        "runtime_boundary": {
            "integrity_lock_only": True,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def check_lock(root: Path, lock: dict[str, Any], lock_path: Path) -> dict[str, Any]:
    expected = lock.get("files") if isinstance(lock.get("files"), dict) else {}
    current = curated_files(root)
    missing = sorted(set(expected) - set(current))
    untracked = sorted(set(current) - set(expected))
    changed = sorted(
        key
        for key in set(expected) & set(current)
        if not isinstance(expected.get(key), dict)
        or expected[key].get("sha256") != current[key].get("sha256")
        or expected[key].get("size") != current[key].get("size")
    )
    lock_present = bool(expected) and lock_path.is_file()
    passed = lock_present and not missing and not untracked and not changed
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": "active_source_integrity_clean" if passed else "active_source_integrity_drift_blocked",
        "lock_path": str(lock_path),
        "lock_present": lock_present,
        "lock_review_id": lock.get("review_id"),
        "lock_sealed_at": lock.get("sealed_at"),
        "expected_files": len(expected),
        "current_files": len(current),
        "missing": missing,
        "changed": changed,
        "untracked": untracked,
        "drift_count": len(missing) + len(changed) + len(untracked),
        "checks": {
            "lock_present": lock_present,
            "no_missing_files": not missing,
            "no_changed_files": not changed,
            "no_untracked_files": not untracked,
        },
        "next_action": "continue_locked_runtime" if passed else "quarantine_and_review_drift_before_resealing",
        "runtime_boundary": {
            "guard_only": True,
            "research_runner_unblocked_by_integrity": passed,
            "automatic_restore": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Active Source Integrity Guard",
            "",
            f"- Generated: `{report.get('generated_at')}`.",
            f"- Decision: `{report.get('decision')}`.",
            f"- Review ID: `{report.get('lock_review_id')}`.",
            f"- Expected/current files: `{report.get('expected_files')}` / `{report.get('current_files')}`.",
            f"- Drift count: `{report.get('drift_count')}`.",
            f"- Missing: `{', '.join(report.get('missing') or []) or 'none'}`.",
            f"- Changed: `{', '.join(report.get('changed') or []) or 'none'}`.",
            f"- Untracked: `{', '.join(report.get('untracked') or []) or 'none'}`.",
            f"- Next action: `{report.get('next_action')}`.",
            "- Drift blocks the post-seal research runner but never auto-restores files.",
            "- `can_trade=false`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed integrity guard for reviewed Trading OS source surfaces")
    parser.add_argument("action", choices=["check", "seal"], nargs="?", default="check")
    parser.add_argument("--active-root", default=str(ROOT))
    parser.add_argument("--lock", default="configs/ACTIVE_SOURCE_INTEGRITY_LOCK.json")
    parser.add_argument("--out-prefix", default="docs/ACTIVE_SOURCE_INTEGRITY_GUARD")
    parser.add_argument("--review-id", default="")
    parser.add_argument("--acknowledge-reviewed-changes", action="store_true")
    args = parser.parse_args()

    root = Path(args.active_root).resolve()
    lock_path = resolve_path(args.lock, root)
    out_prefix = resolve_path(args.out_prefix, root)
    if args.action == "seal":
        if not args.acknowledge_reviewed_changes or not args.review_id.strip():
            parser.error("seal requires --acknowledge-reviewed-changes and a non-empty --review-id")
        lock = build_lock(root, args.review_id.strip())
        write_json(lock_path, lock)

    report = check_lock(root, read_json(lock_path), lock_path)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "files": report["current_files"],
                "drift_count": report["drift_count"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["decision"] == "active_source_integrity_clean" else 2


if __name__ == "__main__":
    raise SystemExit(main())

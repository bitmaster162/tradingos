#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def display(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def bounded_prefix_files(prefix_root: Path, limit: int, max_file_bytes: int) -> list[Path]:
    selected: list[Path] = []
    if not prefix_root.exists() or limit <= 0:
        return selected
    for directory, dirnames, filenames in os.walk(prefix_root):
        dirnames[:] = sorted(name for name in dirnames if name not in {"__pycache__", ".pytest_cache"})
        for name in sorted(filenames):
            if name in {"telegram.env", ".env"}:
                continue
            path = Path(directory) / name
            try:
                if path.stat().st_size > max_file_bytes:
                    continue
            except OSError:
                continue
            selected.append(path)
            if len(selected) >= limit:
                return selected
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded checksum restore drill for Trading OS runtime-data backup.")
    parser.add_argument("--backup-root", default=str(Path.home() / "My Drive" / "04_PRODUCT_SHELLS" / "Trade" / "_runtime_backups" / "TradingOS_ACTIVE"))
    parser.add_argument("--restore-root", default="_dl/runtime_restore_drill/restored")
    parser.add_argument("--max-files", type=int, default=60)
    parser.add_argument("--max-file-bytes", type=int, default=5_000_000)
    parser.add_argument("--out-prefix", default="docs/RUNTIME_BACKUP_RESTORE_DRILL_2026-06-22")
    args = parser.parse_args()

    backup_root = Path(args.backup_root).resolve()
    restore_root = Path(args.restore_root)
    if not restore_root.is_absolute():
        restore_root = ROOT / restore_root
    candidates: list[Path] = []
    prefix_counts: dict[str, int] = {}
    if backup_root.exists():
        prefixes = ("logs", "docs", "data", "_dl")
        for index, prefix in enumerate(prefixes):
            remaining_budget = max(0, args.max_files - len(candidates))
            if not remaining_budget:
                break
            prefixes_left = len(prefixes) - index
            quota = max(1, remaining_budget // prefixes_left)
            selected = bounded_prefix_files(backup_root / prefix, quota, args.max_file_bytes)
            candidates.extend(selected)
            prefix_counts[prefix] = len(selected)

    restored: list[dict[str, Any]] = []
    for source in candidates:
        rel = source.relative_to(backup_root)
        target = restore_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        source_hash = digest(source)
        target_hash = digest(target)
        restored.append({"path": rel.as_posix(), "size": source.stat().st_size, "source_sha256": source_hash, "restored_sha256": target_hash, "match": source_hash == target_hash})

    required_prefixes = {"logs", "docs", "data"}
    observed_prefixes = {item["path"].split("/", 1)[0] for item in restored}
    all_match = bool(restored) and all(item["match"] for item in restored)
    prefixes_ok = required_prefixes.issubset(observed_prefixes)
    decision = "runtime_backup_restore_drill_passed" if all_match and prefixes_ok else "runtime_backup_restore_drill_failed"
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {"classification": "backup_restore_drill_only", "can_trade": False, "sends_orders": False, "restores_into_active_runtime": False},
        "backup_root": str(backup_root),
        "restore_root": display(restore_root),
        "sampled_files": len(restored),
        "sampled_prefix_counts": prefix_counts,
        "observed_prefixes": sorted(observed_prefixes),
        "required_prefixes": sorted(required_prefixes),
        "all_hashes_match": all_match,
        "prefixes_ok": prefixes_ok,
        "restored": restored,
        "decision": decision,
        "can_trade": False,
    }
    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(
        "\n".join(
            [
                "# Runtime Backup Restore Drill",
                "",
                f"- Decision: `{decision}`",
                f"- Sampled files: `{len(restored)}`",
                f"- Prefixes: `{sorted(observed_prefixes)}`",
                f"- All hashes match: `{all_match}`",
                "- Restore target is isolated; active runtime is not modified.",
                "- Can trade: `false`.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps({"decision": decision, "files": len(restored), "prefixes": sorted(observed_prefixes), "all_hashes_match": all_match, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if decision.endswith("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_PREFIXES = [
    "_dl/",
    "logs/",
    "data/cache/",
    "data/cross_venue_microstructure/",
    "data/runtime/",
    "ops/btcusdt_binance_futures_bot/data/public_live_capture/",
    "ops/btcusdt_binance_futures_bot/data/public_live_smoke/",
]
EXCLUDED_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
EXCLUDED_FILES = {
    "MANIFEST.json",
    ".env",
    "configs/.env",
    "configs/telegram.env",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def should_exclude(path: Path, root: Path) -> tuple[bool, str | None]:
    rel = path.relative_to(root).as_posix()
    if rel in EXCLUDED_FILES or path.name in EXCLUDED_FILES:
        return True, "excluded_secret_or_generated_file"
    if path.suffix in EXCLUDED_SUFFIXES:
        return True, "excluded_bytecode"
    if any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts):
        return True, "excluded_dir"
    if any(rel.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return True, "excluded_runtime_prefix"
    return False, None


def build_manifest(root: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    excluded_secret_paths: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        excluded, reason = should_exclude(path, root)
        if excluded:
            if reason == "excluded_secret_or_generated_file" and rel != "MANIFEST.json":
                excluded_secret_paths.append(rel)
            continue
        data = path.read_bytes()
        files.append({"path": rel, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return {
        "built": now_iso(),
        "policy": "curated portable source manifest; excludes runtime caches, generated outputs, logs, bytecode, live-capture streams and local secret env files",
        "excluded_prefixes": EXCLUDED_PREFIXES,
        "excluded_secret_files": sorted(item for item in EXCLUDED_FILES if item != "MANIFEST.json"),
        "excluded_secret_paths_present": sorted(excluded_secret_paths),
        "total_files": len(files),
        "total_bytes": sum(item["size"] for item in files),
        "missing_count": 0,
        "files": files,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Build curated portable MANIFEST.json without local secrets")
    parser.add_argument("--out", default="MANIFEST.json")
    args = parser.parse_args()

    manifest = build_manifest(ROOT)
    out_path = ROOT / args.out
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "out": str(out_path),
                "total_files": manifest["total_files"],
                "total_bytes": manifest["total_bytes"],
                "excluded_secret_paths_present": manifest["excluded_secret_paths_present"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

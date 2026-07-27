#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_DATA_PATHS = [
    "data/cache/binance_spot_perp_extended/futures/BTCUSDT/4h_klines.csv",
    "data/cache/binance_spot_perp_extended/futures/BTCUSDT/4h_oi_aligned.csv",
    "data/cache/binance_spot_perp_extended/futures/BTCUSDT/1h_klines.csv",
    "data/cache/binance_spot_perp_extended/futures/BTCUSDT/1h_oi_aligned.csv",
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds")


def resolve(value: str, base: Path | None = None) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base or ROOT) / path


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def csv_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False, "rows": 0, "first_time": None, "last_time": None}
    rows = 0
    first_time = None
    last_time = None
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows += 1
            current = row.get("time") or row.get("timestamp")
            if rows == 1:
                first_time = current
            last_time = current
    return {"exists": True, "rows": rows, "first_time": first_time, "last_time": last_time}


def file_state(root: Path, rel_path: str) -> dict[str, Any]:
    path = root / rel_path
    summary = csv_summary(path)
    summary.update(
        {
            "path": rel_path,
            "bytes": path.stat().st_size if path.is_file() else None,
            "sha256": sha256(path),
        }
    )
    return summary


def copy_with_backup(source_root: Path, target_root: Path, rel_path: str, backup_root: Path, *, dry_run: bool) -> dict[str, Any]:
    source = source_root / rel_path
    target = target_root / rel_path
    before = file_state(target_root, rel_path)
    incoming = file_state(source_root, rel_path)
    if not source.is_file():
        return {
            "path": rel_path,
            "status": "source_missing",
            "source": incoming,
            "target_before": before,
            "target_after": before,
            "backup": None,
        }

    backup_path = None
    if target.is_file():
        backup_path = backup_root / rel_path
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        if backup_path is not None:
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup_path)
        shutil.copy2(source, target)
    after = incoming if dry_run else file_state(target_root, rel_path)
    status = "would_copy" if dry_run else "copied"
    if before.get("sha256") == incoming.get("sha256") and before.get("sha256") is not None:
        status = "already_synced"
    return {
        "path": rel_path,
        "status": status,
        "source": incoming,
        "target_before": before,
        "target_after": after,
        "backup": str(backup_path) if backup_path is not None else None,
    }


def build_report(
    *,
    source_root: Path,
    target_root: Path,
    data_paths: list[str],
    backup_root: Path,
    dry_run: bool,
) -> dict[str, Any]:
    actions = [
        copy_with_backup(source_root, target_root, rel_path, backup_root, dry_run=dry_run)
        for rel_path in data_paths
    ]
    copied = sum(1 for item in actions if item["status"] in {"copied", "would_copy"})
    missing = sum(1 for item in actions if item["status"] == "source_missing")
    decision = "data_reconciliation_dry_run"
    if missing:
        decision = "data_reconciliation_blocked_source_missing"
    elif not dry_run:
        decision = "data_reconciliation_completed"
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "source_root": str(source_root),
        "target_root": str(target_root),
        "backup_root": str(backup_root),
        "dry_run": dry_run,
        "decision": decision,
        "files_requested": len(data_paths),
        "files_copied_or_would_copy": copied,
        "source_missing_count": missing,
        "actions": actions,
        "next_action": "rerun derivatives-event regime test and runtime drift audit on reconciled data",
        "runtime_boundary": {"data_sync_only": True, "paper_allowed": False, "live_allowed": False, "orders_allowed": False, "can_trade": False},
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Derivatives Event Data Reconciler",
        "",
        f"- Generated: `{report['generated_at']}`.",
        f"- Decision: `{report['decision']}`.",
        f"- Source: `{report['source_root']}`.",
        f"- Target: `{report['target_root']}`.",
        f"- Backup root: `{report['backup_root']}`.",
        f"- Dry run: `{report['dry_run']}`.",
        "- Data sync only; `can_trade=false`.",
        "",
        "| path | status | source rows | before rows | after rows | source last | after last |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for item in report["actions"]:
        lines.append(
            f"| `{item['path']}` | `{item['status']}` | `{item['source'].get('rows')}` | `{item['target_before'].get('rows')}` | `{item['target_after'].get('rows')}` | `{item['source'].get('last_time')}` | `{item['target_after'].get('last_time')}` |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy canonical derivatives-event data cache with backups before reproducibility reruns")
    parser.add_argument("--source-root", default=r"C:\Users\coins\TradingOS\Active")
    parser.add_argument("--target-root", default=str(ROOT))
    parser.add_argument("--paths", default=",".join(DEFAULT_DATA_PATHS))
    parser.add_argument("--backup-root", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out-prefix", default="docs/DERIVATIVES_EVENT_DATA_RECONCILER_2026-06-26")
    args = parser.parse_args()

    source_root = resolve(args.source_root).resolve()
    target_root = resolve(args.target_root).resolve()
    backup_root = resolve(args.backup_root, target_root).resolve() if args.backup_root else target_root / "_dl" / f"data_reconciliation_backup_{now_utc().strftime('%Y%m%dT%H%M%SZ')}"
    data_paths = [item.strip().replace("\\", "/") for item in args.paths.split(",") if item.strip()]
    report = build_report(
        source_root=source_root,
        target_root=target_root,
        data_paths=data_paths,
        backup_root=backup_root,
        dry_run=args.dry_run,
    )
    out_prefix = resolve(args.out_prefix, target_root)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "files_requested": report["files_requested"],
                "files_copied_or_would_copy": report["files_copied_or_would_copy"],
                "source_missing_count": report["source_missing_count"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["source_missing_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

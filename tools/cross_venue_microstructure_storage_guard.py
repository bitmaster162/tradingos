#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


OK = "cross_venue_microstructure_storage_ok"
WARN = "cross_venue_microstructure_storage_warn"
DEGRADED = "cross_venue_microstructure_storage_degraded"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str, root: Path = ROOT) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def bytes_or_zero(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def gate(name: str, passed: bool, actual: Any, required: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "actual": actual, "required": required, "severity": "hard"}


def warn_gate(name: str, passed: bool, actual: Any, threshold: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "actual": actual, "threshold": threshold, "severity": "warn"}


def evaluate_storage_guard(*, source_dir: Path, report: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    source_dir = source_dir.resolve()
    usage = shutil.disk_usage(source_dir if source_dir.exists() else source_dir.parent)
    free_pct = (usage.free / usage.total * 100.0) if usage.total else 0.0
    files = {
        "sqlite": source_dir / "microstructure.sqlite3",
        "sqlite_wal": source_dir / "microstructure.sqlite3-wal",
        "sqlite_shm": source_dir / "microstructure.sqlite3-shm",
        "minute_features": source_dir / "minute_features_v2.csv",
        "collection_state": source_dir / "COLLECTION_STATE.json",
        "collection_manifest": source_dir / "COLLECTION_MANIFEST.json",
    }
    file_sizes = {name: bytes_or_zero(path) for name, path in files.items()}
    authoritative_bytes = sum(file_sizes.values())
    coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
    span_hours = float(coverage.get("span_hours") or 0.0)
    target_hours = float(policy.get("target_hours") or 168.0)
    bytes_per_hour = authoritative_bytes / span_hours if span_hours > 0 else None
    estimated_target_bytes = int(bytes_per_hour * target_hours) if bytes_per_hour is not None else authoritative_bytes

    min_free_bytes = int(policy.get("min_free_bytes_hard") or 0)
    min_free_pct = float(policy.get("min_free_pct_hard") or 0.0)
    max_authoritative_bytes = int(policy.get("max_authoritative_bytes_hard") or 0)
    max_estimated_target_bytes = int(policy.get("max_estimated_target_bytes_hard") or 0)
    warn_free_bytes = int(policy.get("warn_free_bytes") or 0)
    warn_free_pct = float(policy.get("warn_free_pct") or 0.0)
    warn_estimated_target_bytes = int(policy.get("warn_estimated_target_bytes") or 0)

    hard_gates = [
        gate("policy_locked", policy.get("status") == "locked", policy.get("status"), "locked"),
        gate("source_dir_exists", source_dir.is_dir(), str(source_dir), "existing directory"),
        gate("sqlite_exists", files["sqlite"].is_file(), str(files["sqlite"]), "existing SQLite database"),
        gate("free_bytes_above_hard_floor", usage.free >= min_free_bytes, usage.free, f">={min_free_bytes}"),
        gate("free_pct_above_hard_floor", free_pct >= min_free_pct, round(free_pct, 6), f">={min_free_pct}"),
        gate("authoritative_bytes_below_hard_ceiling", authoritative_bytes <= max_authoritative_bytes, authoritative_bytes, f"<={max_authoritative_bytes}"),
        gate("estimated_target_bytes_below_hard_ceiling", estimated_target_bytes <= max_estimated_target_bytes, estimated_target_bytes, f"<={max_estimated_target_bytes}"),
    ]
    warn_gates = [
        warn_gate("free_bytes_above_warning_floor", usage.free >= warn_free_bytes, usage.free, f">={warn_free_bytes}"),
        warn_gate("free_pct_above_warning_floor", free_pct >= warn_free_pct, round(free_pct, 6), f">={warn_free_pct}"),
        warn_gate("estimated_target_bytes_below_warning_ceiling", estimated_target_bytes <= warn_estimated_target_bytes, estimated_target_bytes, f"<={warn_estimated_target_bytes}"),
    ]
    failed_hard = [row["name"] for row in hard_gates if not row["passed"]]
    failed_warn = [row["name"] for row in warn_gates if not row["passed"]]
    classification = DEGRADED if failed_hard else WARN if failed_warn else OK
    return {
        "classification": classification,
        "hard_gates": hard_gates,
        "warn_gates": warn_gates,
        "failed_hard_gates": failed_hard,
        "failed_warn_gates": failed_warn,
        "observed": {
            "source_dir": str(source_dir),
            "disk_total_bytes": usage.total,
            "disk_used_bytes": usage.used,
            "disk_free_bytes": usage.free,
            "disk_free_pct": round(free_pct, 6),
            "file_sizes": file_sizes,
            "authoritative_bytes": authoritative_bytes,
            "span_hours": round(span_hours, 6),
            "target_hours": target_hours,
            "bytes_per_hour": round(bytes_per_hour, 6) if bytes_per_hour is not None else None,
            "estimated_target_bytes": estimated_target_bytes,
        },
        "next_action": "continue_collection" if not failed_hard else "free_disk_or_reduce_storage_growth_before_research_seal",
        "runtime_boundary": {"storage_monitor_only": True, "signals_allowed": False, "orders_allowed": False, "can_trade": False},
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    observed = report["observed"]
    return "\n".join(
        [
            "# Cross-Venue Microstructure Storage Guard",
            "",
            f"Generated: `{report['generated_at']}`",
            "",
            f"- Classification: `{report['classification']}`.",
            f"- Failed hard gates: `{', '.join(report['failed_hard_gates']) or 'none'}`.",
            f"- Failed warn gates: `{', '.join(report['failed_warn_gates']) or 'none'}`.",
            f"- Disk free: `{observed['disk_free_bytes']}` bytes / `{observed['disk_free_pct']}%`.",
            f"- Authoritative bytes: `{observed['authoritative_bytes']}`.",
            f"- Estimated target bytes: `{observed['estimated_target_bytes']}` for `{observed['target_hours']}h`.",
            "- Storage monitoring only; no signals and no orders.",
            "- `can_trade=false`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed storage guard for cross-venue microstructure collection")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--policy", default="configs/CROSS_VENUE_MICROSTRUCTURE_STORAGE_POLICY.json")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_STORAGE_GUARD_2026-06-25")
    args = parser.parse_args()
    active_root = Path(args.root).resolve()
    policy = read_json(resolve_path(args.policy, active_root))
    source_dir = active_root / str(policy.get("source_cache_relative") or "data/cross_venue_microstructure")
    report = read_json(active_root / str(policy.get("readiness_report") or "docs/CROSS_VENUE_MICROSTRUCTURE_DATA_QUALITY_2026-06-24.json"))
    result = evaluate_storage_guard(source_dir=source_dir, report=report, policy=policy)
    result["generated_at"] = now_iso()
    result["policy_id"] = policy.get("policy_id")
    prefix = resolve_path(args.out_prefix, active_root)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    prefix.with_suffix(".md").write_text(render_markdown(result), encoding="utf-8")
    print(json.dumps({"classification": result["classification"], "failed_hard_gates": result["failed_hard_gates"], "failed_warn_gates": result["failed_warn_gates"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if result["classification"] != DEGRADED else 2


if __name__ == "__main__":
    raise SystemExit(main())

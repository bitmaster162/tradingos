#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: str | Path) -> dict[str, Any]:
    p = resolve_path(path)
    if not p.exists():
        return {"_missing": portable(p)}
    try:
        value = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc), "_path": portable(p)}
    return value if isinstance(value, dict) else {"_read_error": "not_object", "_path": portable(p)}


def file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a sealed-snapshot pack or waiting report for cross-venue microstructure")
    parser.add_argument("--snapshot-gate", default="docs/CROSS_VENUE_MICROSTRUCTURE_SNAPSHOT_GATE_2026-06-25.json")
    parser.add_argument("--readiness", default="docs/CROSS_VENUE_MICROSTRUCTURE_READINESS_PROGRESS_2026-06-25.json")
    parser.add_argument("--health", default="docs/CROSS_VENUE_MICROSTRUCTURE_HEALTH_2026-06-24.json")
    parser.add_argument("--post-seal-guard", default="docs/CROSS_VENUE_MICROSTRUCTURE_POST_SEAL_AUTO_RUN_GUARD_2026-06-29.json")
    parser.add_argument("--data-db", default="data/cross_venue_microstructure/microstructure.sqlite3")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_SEALED_SNAPSHOT_PACK_2026-06-30")
    args = parser.parse_args()
    snapshot = read_json(args.snapshot_gate)
    readiness = read_json(args.readiness)
    health = read_json(args.health)
    post_seal = read_json(args.post_seal_guard)
    failed = snapshot.get("summary", {}).get("failed") if isinstance(snapshot.get("summary"), dict) else None
    snapshot_id = snapshot.get("snapshot_id")
    dataset_sha = snapshot.get("dataset_sha256")
    sealed = bool(snapshot_id and dataset_sha and not failed)
    decision = "microstructure_sealed_snapshot_pack_ready" if sealed else "microstructure_sealed_snapshot_pack_waiting"
    db_path = resolve_path(args.data_db)
    report = {
        "generated_at": now_iso(),
        "tool": "tools/microstructure_sealed_snapshot_pack.py",
        "decision": decision,
        "can_trade": False,
        "sealed": sealed,
        "snapshot": {
            "snapshot_id": snapshot_id,
            "dataset_sha256": dataset_sha,
            "decision": snapshot.get("decision"),
            "failed": failed,
            "summary": snapshot.get("summary"),
        },
        "readiness": {
            "decision": readiness.get("decision"),
            "remaining_hours": readiness.get("remaining_hours"),
            "span_hours": readiness.get("span_hours"),
            "next_action": readiness.get("next_action"),
        },
        "health": {
            "classification": health.get("classification"),
            "failed_hard_gates": health.get("failed_hard_gates"),
            "can_trade": health.get("can_trade"),
        },
        "post_seal_guard": {
            "decision": post_seal.get("decision"),
            "next_action": post_seal.get("next_action"),
            "can_trade": post_seal.get("can_trade"),
        },
        "data_db": {
            "path": portable(db_path),
            "exists": db_path.exists(),
            "size": db_path.stat().st_size if db_path.exists() else None,
            "sha256": file_sha256(db_path) if sealed else None,
            "hash_policy": "hash_only_when_sealed_to_avoid_churn_on_live_db",
        },
        "next_action": "run locked post-seal research chain" if sealed else "keep collector and watchdog running until snapshot gate is sealed",
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(
        "\n".join(
            [
                "# Cross-Venue Microstructure Sealed Snapshot Pack",
                "",
                f"- Generated: `{report['generated_at']}`",
                f"- Decision: `{decision}`",
                f"- Can trade: `false`",
                f"- Sealed: `{str(sealed).lower()}`",
                f"- Snapshot ID: `{snapshot_id}`",
                f"- Dataset SHA256: `{dataset_sha}`",
                f"- Remaining hours: `{report['readiness']['remaining_hours']}`",
                "",
                "## Next Action",
                "",
                f"- {report['next_action']}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps({"decision": decision, "sealed": sealed, "out": portable(out.with_suffix(".json")), "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

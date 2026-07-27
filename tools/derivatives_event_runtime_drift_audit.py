#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DATA_PATHS = [
    "data/cache/binance_spot_perp_extended/futures/BTCUSDT/4h_klines.csv",
    "data/cache/binance_spot_perp_extended/futures/BTCUSDT/4h_oi_aligned.csv",
    "data/cache/binance_spot_perp_extended/futures/BTCUSDT/1h_klines.csv",
    "data/cache/binance_spot_perp_extended/futures/BTCUSDT/1h_oi_aligned.csv",
]

REPORT_PATHS = [
    "docs/DERIVATIVES_EVENT_EDGE_MINER_REGIME_2026-06-26.json",
    "docs/TRADINGOS_CORE_READINESS_EDGE_REPORT_2026-06-26.json",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


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


def file_summary(root: Path, rel_path: str) -> dict[str, Any]:
    path = root / rel_path
    exists = path.is_file()
    summary = csv_summary(path) if rel_path.endswith(".csv") else {"exists": exists}
    summary.update(
        {
            "path": rel_path,
            "bytes": path.stat().st_size if exists else None,
            "sha256": sha256(path),
        }
    )
    return summary


def report_summary(root: Path, rel_path: str) -> dict[str, Any]:
    payload = read_json(root / rel_path)
    selected = payload.get("selected") if isinstance(payload.get("selected"), dict) else {}
    scoreboard = payload.get("scoreboard") if isinstance(payload.get("scoreboard"), dict) else {}
    return {
        "path": rel_path,
        "exists": bool(payload),
        "generated_at": payload.get("generated_at"),
        "decision": payload.get("decision"),
        "summary": payload.get("summary") if isinstance(payload.get("summary"), dict) else None,
        "selected_strategy_id": selected.get("strategy_id"),
        "summary_tested": (payload.get("summary") or {}).get("tested") if isinstance(payload.get("summary"), dict) else None,
        "summary_train_qualified": (payload.get("summary") or {}).get("train_qualified") if isinstance(payload.get("summary"), dict) else None,
        "summary_validation_qualified": (payload.get("summary") or {}).get("validation_qualified") if isinstance(payload.get("summary"), dict) else None,
        "summary_oos_decision": (payload.get("summary") or {}).get("oos_decision") if isinstance(payload.get("summary"), dict) else None,
        "derivatives_event_train_qualified": scoreboard.get("derivatives_event_train_qualified"),
        "derivatives_event_validation_qualified": scoreboard.get("derivatives_event_validation_qualified"),
        "can_trade": payload.get("can_trade"),
        "sha256": sha256(root / rel_path),
    }


def compare_dict(left: dict[str, Any], right: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    differences = {}
    for key in keys:
        if left.get(key) != right.get(key):
            differences[key] = {"source": left.get(key), "runtime": right.get(key)}
    return differences


def build_report(source_root: Path, runtime_root: Path) -> dict[str, Any]:
    data_comparisons = []
    for rel_path in DATA_PATHS:
        source = file_summary(source_root, rel_path)
        runtime = file_summary(runtime_root, rel_path)
        data_comparisons.append(
            {
                "path": rel_path,
                "source": source,
                "runtime": runtime,
                "same_hash": source.get("sha256") == runtime.get("sha256") and source.get("sha256") is not None,
                "differences": compare_dict(source, runtime, ["exists", "rows", "first_time", "last_time", "bytes", "sha256"]),
            }
        )

    report_comparisons = []
    for rel_path in REPORT_PATHS:
        source = report_summary(source_root, rel_path)
        runtime = report_summary(runtime_root, rel_path)
        report_comparisons.append(
            {
                "path": rel_path,
                "source": source,
                "runtime": runtime,
                "same_hash": source.get("sha256") == runtime.get("sha256") and source.get("sha256") is not None,
                "differences": compare_dict(
                    source,
                    runtime,
                    [
                        "exists",
                        "decision",
                        "selected_strategy_id",
                        "summary_tested",
                        "summary_train_qualified",
                        "summary_validation_qualified",
                        "summary_oos_decision",
                        "derivatives_event_train_qualified",
                        "derivatives_event_validation_qualified",
                        "can_trade",
                    ],
                ),
            }
        )

    data_drift = [item for item in data_comparisons if not item["same_hash"]]
    report_drift = [item for item in report_comparisons if item["differences"]]
    decision = "source_runtime_in_sync"
    next_action = "runtime result can be reviewed against source with no file drift"
    if data_drift:
        decision = "source_runtime_data_drift_detected_do_not_promote"
        next_action = "reconcile source/runtime data caches before accepting derivatives-event candidate"
    elif report_drift:
        decision = "source_runtime_report_drift_detected_review_before_promotion"
        next_action = "rerun reports from the same root and compare generated artifacts before accepting candidate"

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "source_root": str(source_root),
        "runtime_root": str(runtime_root),
        "decision": decision,
        "data_files_compared": len(data_comparisons),
        "data_drift_count": len(data_drift),
        "report_files_compared": len(report_comparisons),
        "report_drift_count": len(report_drift),
        "data_comparisons": data_comparisons,
        "report_comparisons": report_comparisons,
        "next_action": next_action,
        "runtime_boundary": {"audit_only": True, "paper_allowed": False, "live_allowed": False, "orders_allowed": False, "can_trade": False},
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Derivatives Event Runtime Drift Audit",
        "",
        f"- Generated: `{report['generated_at']}`.",
        f"- Decision: `{report['decision']}`.",
        f"- Data drift count: `{report['data_drift_count']}/{report['data_files_compared']}`.",
        f"- Report drift count: `{report['report_drift_count']}/{report['report_files_compared']}`.",
        f"- Next action: `{report['next_action']}`.",
        "- Audit only; `can_trade=false`.",
        "",
        "## Data Files",
        "",
        "| path | same_hash | source rows | runtime rows | source last | runtime last |",
        "|---|---:|---:|---:|---|---|",
    ]
    for item in report["data_comparisons"]:
        lines.append(
            f"| `{item['path']}` | `{item['same_hash']}` | `{item['source'].get('rows')}` | `{item['runtime'].get('rows')}` | `{item['source'].get('last_time')}` | `{item['runtime'].get('last_time')}` |"
        )
    lines.extend(["", "## Reports", "", "| path | same_hash | source decision | runtime decision | source selected | runtime selected |", "|---|---:|---|---|---|---|"])
    for item in report["report_comparisons"]:
        lines.append(
            f"| `{item['path']}` | `{item['same_hash']}` | `{item['source'].get('decision')}` | `{item['runtime'].get('decision')}` | `{item['source'].get('selected_strategy_id')}` | `{item['runtime'].get('selected_strategy_id')}` |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare source package and Active runtime derivatives-event data/report drift")
    parser.add_argument("--source-root", default=str(ROOT))
    parser.add_argument("--runtime-root", default=r"C:\Users\coins\TradingOS\Active")
    parser.add_argument("--out-prefix", default="docs/DERIVATIVES_EVENT_RUNTIME_DRIFT_AUDIT_2026-06-26")
    args = parser.parse_args()

    source_root = resolve(args.source_root).resolve()
    runtime_root = resolve(args.runtime_root).resolve()
    report = build_report(source_root, runtime_root)
    out_prefix = resolve(args.out_prefix, source_root)
    write_json_path = out_prefix.with_suffix(".json")
    write_json_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "data_drift_count": report["data_drift_count"],
                "report_drift_count": report["report_drift_count"],
                "next_action": report["next_action"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

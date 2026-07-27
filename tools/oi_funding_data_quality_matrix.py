#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"_read_error": str(path)}
    return payload if isinstance(payload, dict) else {"_read_error": str(path)}


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def as_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def latest_quality_reports(docs_dir: Path) -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    for path in docs_dir.glob("OI_FUNDING_DATA_QUALITY*.json"):
        if "MATRIX" in path.name.upper():
            continue
        payload = read_json(path)
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        interval = str(summary.get("interval") or payload.get("inputs", {}).get("interval") or "unknown")
        current = reports.get(interval)
        if current is None or path.stat().st_mtime > current["mtime"]:
            reports[interval] = {"path": path, "mtime": path.stat().st_mtime, "payload": payload}
    return reports


def classify_interval(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    replay = payload.get("replay_trade_coverage") if isinstance(payload.get("replay_trade_coverage"), dict) else {}
    classification = summary.get("classification")
    full_context_pct = as_float(replay.get("full_context_coverage_pct"))
    aligned_oi_pct = as_float(summary.get("aligned_oi_coverage_pct"))
    aligned_funding_pct = as_float(summary.get("aligned_funding_coverage_pct"))
    ready = (
        classification == "oi_guard_data_ready"
        and full_context_pct is not None
        and full_context_pct >= 95.0
        and aligned_oi_pct is not None
        and aligned_oi_pct >= 95.0
        and aligned_funding_pct is not None
        and aligned_funding_pct >= 95.0
    )
    blockers: list[str] = []
    if classification != "oi_guard_data_ready":
        blockers.append(f"classification:{classification}")
    if full_context_pct is None or full_context_pct < 95.0:
        blockers.append(f"full_context_pct:{full_context_pct}")
    if aligned_oi_pct is None or aligned_oi_pct < 95.0:
        blockers.append(f"aligned_oi_pct:{aligned_oi_pct}")
    if aligned_funding_pct is None or aligned_funding_pct < 95.0:
        blockers.append(f"aligned_funding_pct:{aligned_funding_pct}")
    return {
        "ready": ready,
        "classification": classification,
        "full_context_coverage_pct": full_context_pct,
        "aligned_oi_coverage_pct": aligned_oi_pct,
        "aligned_funding_coverage_pct": aligned_funding_pct,
        "blockers": blockers,
    }


def build_report(docs_dir: Path) -> dict[str, Any]:
    reports = latest_quality_reports(docs_dir)
    intervals: dict[str, Any] = {}
    for interval, item in sorted(reports.items()):
        payload = item["payload"]
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        replay = payload.get("replay_trade_coverage") if isinstance(payload.get("replay_trade_coverage"), dict) else {}
        interval_status = classify_interval(payload)
        intervals[interval] = {
            **interval_status,
            "path": portable(item["path"]),
            "generated_at": payload.get("generated_at"),
            "kline_rows": summary.get("kline_rows"),
            "merged_oi_rows": summary.get("merged_oi_rows"),
            "merged_funding_rows": summary.get("merged_funding_rows"),
            "replay_trades": replay.get("trades"),
        }

    ready_intervals = [key for key, item in intervals.items() if item.get("ready")]
    degraded_intervals = [key for key, item in intervals.items() if not item.get("ready")]
    decision = "oi_funding_quality_ready_for_research"
    next_action = "continue observer/backtest research; data quality is not the active blocker"
    if not intervals:
        decision = "oi_funding_quality_missing"
        next_action = "run oi_funding_data_quality_collector before using OI/funding as any research filter"
    elif degraded_intervals:
        decision = "oi_funding_quality_partial_do_not_promote"
        next_action = "fix degraded intervals or scope research to ready intervals only"

    return {
        "generated_at": now_iso(),
        "boundary": {
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "network_required": False,
        },
        "summary": {
            "reports": len(intervals),
            "ready_intervals": len(ready_intervals),
            "degraded_intervals": len(degraded_intervals),
            "ready_interval_ids": ready_intervals,
            "degraded_interval_ids": degraded_intervals,
        },
        "intervals": intervals,
        "decision": decision,
        "next_action": next_action,
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# OI/Funding Data Quality Matrix",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Local report aggregation only.",
        "- No network, no private credentials, no orders.",
        "",
        "## Summary",
        "",
        f"- Decision: `{report.get('decision')}`.",
        f"- Reports: `{report.get('summary', {}).get('reports')}`.",
        f"- Ready intervals: `{report.get('summary', {}).get('ready_interval_ids')}`.",
        f"- Degraded intervals: `{report.get('summary', {}).get('degraded_interval_ids')}`.",
        "",
        "## Intervals",
        "",
        "| interval | ready | classification | full context % | OI % | funding % | path |",
        "|---|---:|---|---:|---:|---:|---|",
    ]
    for interval, item in report.get("intervals", {}).items():
        lines.append(
            "| "
            f"{interval} | {item.get('ready')} | {item.get('classification')} | "
            f"{item.get('full_context_coverage_pct')} | {item.get('aligned_oi_coverage_pct')} | "
            f"{item.get('aligned_funding_coverage_pct')} | `{item.get('path')}` |"
        )
    lines.extend(["", "## Next Action", "", f"- {report.get('next_action')}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate OI/funding data-quality reports by interval")
    parser.add_argument("--docs-dir", default="docs")
    parser.add_argument("--out-prefix", default="docs/OI_FUNDING_DATA_QUALITY_MATRIX_2026-06-29")
    args = parser.parse_args()

    docs_dir = resolve_path(args.docs_dir)
    out_prefix = resolve_path(args.out_prefix)
    report = build_report(docs_dir)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "md": str(md_path), "summary": report["summary"], "decision": report["decision"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

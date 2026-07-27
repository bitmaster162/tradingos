#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VERSION = "1.9.0"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            payload = json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            raise ValueError(f"Expected JSON object in {path}")
        return payload
    raise ValueError(f"Could not decode JSON: {path}")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    if not events:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tf_seconds(tf: str) -> int:
    suffix = tf[-1:].lower()
    try:
        value = int(tf[:-1])
    except ValueError:
        return 0
    if suffix == "m":
        return value * 60
    if suffix == "h":
        return value * 60 * 60
    if suffix == "d":
        return value * 60 * 60 * 24
    return 0


def latest_close_by_tf(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    timeframes = report.get("timeframes")
    if not isinstance(timeframes, dict):
        return out
    for tf, result in timeframes.items():
        if not isinstance(result, dict):
            continue
        last = result.get("last") if isinstance(result.get("last"), dict) else {}
        out[str(tf)] = {
            "pair": result.get("pair"),
            "close": last.get("close"),
            "generated_at": result.get("generated_at") or report.get("generated_at"),
            "decision": result.get("decision"),
            "regime": result.get("regime"),
        }
    return out


def extract_active_alerts(report: dict[str, Any]) -> list[dict[str, Any]]:
    alerts = report.get("market_state_alerts")
    if not isinstance(alerts, dict):
        return []
    active = alerts.get("active")
    if isinstance(active, list):
        return [item for item in active if isinstance(item, dict)]
    extracted: list[dict[str, Any]] = []
    for value in alerts.values():
        if isinstance(value, dict) and value.get("active"):
            extracted.append(value)
    return extracted


def make_event_uid(source_sha: str, alert: dict[str, Any], tf: str, pair: str, close: Any) -> str:
    raw = "|".join(
        [
            source_sha,
            str(alert.get("id")),
            str(tf),
            str(pair),
            str(close),
            str(alert.get("side_context")),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def directional_return_pct(side_context: str, entry_close: float, latest_close: float) -> float:
    if entry_close == 0:
        return 0.0
    if side_context.upper() == "SHORT":
        return (entry_close - latest_close) / entry_close * 100
    return (latest_close - entry_close) / entry_close * 100


def update_tracker(
    *,
    tracker_path: Path,
    report: dict[str, Any],
    source_report: Path,
    source_sha: str,
    active_alerts: list[dict[str, Any]],
    forward_observations: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    close_map = latest_close_by_tf(report)
    existing = read_jsonl(tracker_path)
    existing_by_uid = {str(item.get("event_uid")): item for item in existing if item.get("event_uid")}
    new_entries: list[dict[str, Any]] = []
    now = now_iso()

    for alert in active_alerts:
        tf = str(alert.get("tf") or "")
        close_info = close_map.get(tf, {})
        close = close_info.get("close")
        pair = str(close_info.get("pair") or report.get("pair") or "unknown")
        if close is None:
            continue
        uid = make_event_uid(source_sha, alert, tf, pair, close)
        if uid in existing_by_uid:
            continue
        new_entries.append(
            {
                "event_uid": uid,
                "status": "pending",
                "created_at": now,
                "source_report": str(source_report),
                "source_sha256": source_sha,
                "alert_id": alert.get("id"),
                "tf": tf,
                "tf_seconds": tf_seconds(tf),
                "pair": pair,
                "side_context": alert.get("side_context"),
                "entry_close": close,
                "latest_close": close,
                "forward_observations": 0,
                "target_forward_observations": forward_observations,
                "directional_return_pct": 0.0,
                "can_trade": False,
                "entry_permission": alert.get("entry_permission", "blocked_alert_only"),
                "research_mode": "alert_only_observation",
            }
        )

    updated: list[dict[str, Any]] = []
    resolved_now = 0
    for item in existing:
        if item.get("status") != "pending":
            updated.append(item)
            continue
        tf = str(item.get("tf") or "")
        close_info = close_map.get(tf, {})
        latest_close = close_info.get("close")
        if latest_close is None:
            updated.append(item)
            continue
        if str(item.get("source_sha256")) == source_sha:
            updated.append(item)
            continue
        try:
            entry_close = float(item.get("entry_close"))
            latest = float(latest_close)
        except (TypeError, ValueError):
            updated.append(item)
            continue
        observations = int(item.get("forward_observations") or 0) + 1
        target = int(item.get("target_forward_observations") or forward_observations)
        item = {
            **item,
            "latest_close": latest,
            "latest_report": str(source_report),
            "latest_source_sha256": source_sha,
            "last_observed_at": now,
            "forward_observations": observations,
            "directional_return_pct": round(
                directional_return_pct(str(item.get("side_context") or ""), entry_close, latest),
                6,
            ),
        }
        if observations >= target:
            item["status"] = "resolved"
            item["resolved_at"] = now
            resolved_now += 1
        updated.append(item)

    all_rows = updated + new_entries
    tracker_path.parent.mkdir(parents=True, exist_ok=True)
    with tracker_path.open("w", encoding="utf-8") as handle:
        for item in all_rows:
            handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")

    pending = [item for item in all_rows if item.get("status") == "pending"]
    resolved = [item for item in all_rows if item.get("status") == "resolved"]
    return new_entries, {
        "path": str(tracker_path),
        "total": len(all_rows),
        "pending": len(pending),
        "resolved": len(resolved),
        "newly_opened": len(new_entries),
        "newly_resolved": resolved_now,
        "pending_sample": pending[-5:],
        "resolved_sample": resolved[-5:],
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# MAX Core Lite v1.9 Alert Observability",
        "",
        f"- Generated: `{summary['generated_at']}`",
        f"- Source report: `{summary['source_report']}`",
        f"- Composite generated: `{summary.get('composite_generated_at')}`",
        f"- Active market-state alerts: `{summary['snapshot']['active_count']}`",
        f"- Log events appended: `{summary['log']['appended_events']}`",
        f"- Tracker pending / resolved: `{summary['tracker']['pending']}` / `{summary['tracker']['resolved']}`",
        f"- Trade permission: `{summary['policy']['trade_permission']}`",
        "",
        "## Active Alerts",
        "",
    ]
    if summary["snapshot"]["active_alerts"]:
        for alert in summary["snapshot"]["active_alerts"]:
            lines.append(
                f"- `{alert.get('id')}` `{alert.get('tf')}` side_context=`{alert.get('side_context')}` "
                f"can_trade=`{alert.get('can_trade')}` entry_permission=`{alert.get('entry_permission')}`"
            )
    else:
        lines.append("- No active market-state alerts in the source report.")
    lines.extend(
        [
            "",
            "## Runtime Boundary",
            "",
            "This module is observability only. It logs alert states and forward outcomes; it does not create orders, "
            "does not unlock entries, and does not change risk limits.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="MAX Core Lite v1.9 alert observability")
    parser.add_argument("--composite", default="_dl/control_panel/MAX_CORE_LITE_COMPOSITE.json")
    parser.add_argument("--log", default="logs/market_state_alerts/market_state_alerts.jsonl")
    parser.add_argument("--tracker", default="logs/market_state_alerts/forward_tracker.jsonl")
    parser.add_argument("--out-prefix", default="_dl/control_panel/MAX_CORE_LITE_V19_ALERT_OBSERVABILITY")
    parser.add_argument("--forward-observations", type=int, default=1)
    args = parser.parse_args()

    composite_path = Path(args.composite)
    if not composite_path.exists():
        raise SystemExit(f"Composite report not found: {composite_path}")

    report = read_json(composite_path)
    source_sha = sha256_file(composite_path)
    active_alerts = extract_active_alerts(report)
    generated_at = now_iso()

    snapshot_event = {
        "event_type": "market_state_alert_snapshot",
        "observed_at": generated_at,
        "source_report": str(composite_path),
        "source_sha256": source_sha,
        "composite_generated_at": report.get("generated_at"),
        "engine": report.get("engine"),
        "engine_version": report.get("engine_version"),
        "active_count": len(active_alerts),
        "entry_permission": (report.get("market_state_alerts") or {}).get("entry_permission")
        if isinstance(report.get("market_state_alerts"), dict)
        else None,
        "trade_permission": False,
    }
    events = [snapshot_event]
    close_map = latest_close_by_tf(report)
    for alert in active_alerts:
        tf = str(alert.get("tf") or "")
        close_info = close_map.get(tf, {})
        events.append(
            {
                "event_type": "market_state_alert_active",
                "observed_at": generated_at,
                "source_report": str(composite_path),
                "source_sha256": source_sha,
                "alert": alert,
                "pair": close_info.get("pair"),
                "close": close_info.get("close"),
                "decision": close_info.get("decision"),
                "regime": close_info.get("regime"),
                "trade_permission": False,
            }
        )

    log_path = Path(args.log)
    append_jsonl(log_path, events)
    new_entries, tracker_summary = update_tracker(
        tracker_path=Path(args.tracker),
        report=report,
        source_report=composite_path,
        source_sha=source_sha,
        active_alerts=active_alerts,
        forward_observations=max(1, args.forward_observations),
    )

    summary = {
        "engine": "MAX_CORE_LITE_OBSERVABILITY",
        "version": VERSION,
        "generated_at": generated_at,
        "source_report": str(composite_path),
        "source_sha256": source_sha,
        "composite_generated_at": report.get("generated_at"),
        "snapshot": {
            "active_count": len(active_alerts),
            "active_alerts": active_alerts,
            "timeframes": sorted(close_map),
        },
        "log": {
            "path": str(log_path),
            "appended_events": len(events),
            "total_events": len(read_jsonl(log_path)),
        },
        "tracker": tracker_summary,
        "new_tracker_entries": new_entries,
        "policy": {
            "trade_permission": False,
            "entry_permission": "observability_only",
            "live_orders": False,
            "risk_multiplier": 0.0,
        },
    }

    out_prefix = Path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), summary)
    out_prefix.with_suffix(".md").parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".md").write_text(render_markdown(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

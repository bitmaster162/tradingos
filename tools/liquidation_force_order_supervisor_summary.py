#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc), "_path": portable(path)}
    return payload if isinstance(payload, dict) else {"_read_error": "not_object", "_path": portable(path)}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_minutes(value: Any, now: datetime) -> float | None:
    parsed = parse_ts(value)
    if parsed is None:
        return None
    return round((now - parsed).total_seconds() / 60.0, 3)


def process_alive(value: Any) -> bool:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            exit_code = ctypes.c_ulong()
            query_ok = bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)))
            kernel32.CloseHandle(handle)
            return query_ok and exit_code.value == 259
        return kernel32.GetLastError() == 5
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def safe_stat(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": portable(path), "size": 0, "last_write": None}
    stat = path.stat()
    return {
        "exists": True,
        "path": portable(path),
        "size": stat.st_size,
        "last_write": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def count_lines(path: Path, max_bytes: int = 2_000_000) -> dict[str, Any]:
    if not path.exists():
        return {"lines": 0, "json_lines": 0, "decisions": {}, "tail": []}
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(max(0, size - max_bytes))
            handle.readline()
        raw = handle.read()
    if raw.startswith(b"\xff\xfe") or raw[:200].count(b"\x00") > 20:
        text = raw.decode("utf-16le", errors="replace")
        if text.startswith("\ufeff"):
            text = text[1:]
        encoding = "utf-16le"
    else:
        text = raw.decode("utf-8", errors="replace")
        encoding = "utf-8"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    decisions: Counter[str] = Counter()
    json_lines = 0
    tail: list[str] = []
    for line in lines:
        if len(tail) >= 10:
            tail.pop(0)
        tail.append(line[-500:])
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            json_lines += 1
            if payload.get("decision"):
                decisions[str(payload["decision"])] += 1
    return {
        "encoding": encoding,
        "lines": len(lines),
        "json_lines": json_lines,
        "decisions": dict(decisions),
        "tail": tail,
    }


def read_event_files(data_dir: Path) -> dict[str, Any]:
    files = sorted(data_dir.rglob("*.jsonl")) if data_dir.exists() else []
    events = 0
    by_symbol: Counter[str] = Counter()
    latest_write = None
    for path in files:
        symbol = path.parent.name.upper()
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                for line in handle:
                    if line.strip():
                        events += 1
                        by_symbol[symbol] += 1
        except OSError:
            continue
        stat = path.stat()
        if latest_write is None or stat.st_mtime > latest_write:
            latest_write = stat.st_mtime
    return {
        "files": len(files),
        "events": events,
        "by_symbol": dict(by_symbol),
        "latest_write": datetime.fromtimestamp(latest_write, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if latest_write else None,
        "paths": [portable(path) for path in files[:20]],
    }


def append_history(path: Path, snapshot: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n")


def preferred_event_count(values: dict[str, Any], fallback: int = 0) -> int:
    for key in ("preregistered_sample_events", "research_universe_events", "events_total"):
        if key in values and values.get(key) is not None:
            return int(values.get(key) or 0)
    return int(fallback)


def read_history(path: Path, lookback_hours: float) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    cutoff = now_utc() - timedelta(hours=lookback_hours)
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                ts = parse_ts(row.get("ts"))
                if ts is not None and ts >= cutoff:
                    rows.append(row)
    except OSError:
        return []
    return rows


def classify(current: dict[str, Any], args: argparse.Namespace) -> tuple[str, list[str], str]:
    reasons: list[str] = []
    status = current["status"]
    heartbeat = current["heartbeat"]
    latest = current["latest_report"]
    data_quality = current["data_quality"]
    event_storage = current["event_storage"]
    pid_alive = bool(status.get("pid_alive"))
    status_age = status.get("age_minutes")
    heartbeat_age = heartbeat.get("age_minutes")
    hard_failures = data_quality.get("hard_failures") or []
    if not pid_alive:
        reasons.append("collector_pid_not_alive")
    if status_age is None or status_age > args.max_status_age_minutes:
        reasons.append("collector_status_stale")
    if heartbeat_age is None or heartbeat_age > args.max_heartbeat_age_minutes:
        reasons.append("collector_heartbeat_stale")
    if heartbeat.get("can_trade") is not False:
        reasons.append("heartbeat_can_trade_not_false")
    if hard_failures:
        reasons.append("data_quality_hard_failures")
    if latest.get("parse_errors_count", 0):
        reasons.append("latest_collector_parse_errors")
    if reasons:
        return "force_order_supervisor_blocked_runtime_or_quality", reasons, "fix collector/runtime/data-quality before relying on first-event guard"
    events = preferred_event_count(data_quality, int(event_storage.get("events") or 0))
    if events >= args.min_events_for_research:
        return "force_order_supervisor_sample_ready_for_research_pipeline", reasons, "run preregistered forceOrder event-study review; do not promote without forward validation"
    if events > 0:
        return "force_order_supervisor_collecting_real_events", reasons, "first-event path is active; keep collecting until minimum sample is reached"
    if int(event_storage.get("events") or 0) > 0:
        return (
            "force_order_supervisor_waiting_preregistered_sample",
            reasons,
            "collector has pre-lock/all-market events; wait for fixed-universe events at or after the preregistered start",
        )
    healthy_wait_decisions = {
        "force_order_forward_collector_connected_no_events_observed",
        "force_order_forward_collector_transport_live_no_liquidations_observed",
    }
    healthy_wait_heartbeats = {"connected_waiting_events", "transport_liveness_ok", "cycle_finished"}
    if latest.get("decision") in healthy_wait_decisions or heartbeat.get("status") in healthy_wait_heartbeats:
        return "force_order_supervisor_healthy_waiting_events", reasons, "keep collector running; current bottleneck is sparse real forceOrder events"
    return "force_order_supervisor_observing_no_events", reasons, "keep collecting and inspect websocket coverage if no messages persist over multiple hours"


def summarize_history(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "snapshots": 0,
            "decision_counts": {},
            "events_first": None,
            "events_last": None,
            "events_delta": None,
            "heartbeat_stale_snapshots": 0,
            "unhealthy_snapshots": 0,
        }
    events_values = [preferred_event_count(row) for row in rows]
    return {
        "snapshots": len(rows),
        "decision_counts": dict(Counter(str(row.get("decision")) for row in rows)),
        "events_first": events_values[0],
        "events_last": events_values[-1],
        "events_delta": events_values[-1] - events_values[0],
        "heartbeat_stale_snapshots": sum(1 for row in rows if row.get("heartbeat_stale")),
        "unhealthy_snapshots": sum(1 for row in rows if row.get("decision") == "force_order_supervisor_blocked_runtime_or_quality"),
    }


def render_markdown(report: dict[str, Any]) -> str:
    current = report["current"]
    lines = [
        "# Liquidation ForceOrder Supervisor Summary",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `false`",
        f"- Lookback hours: `{report['inputs']['lookback_hours']}`",
        "",
        "## Current State",
        "",
        f"- Collector status: `{current['status'].get('status')}`",
        f"- Collector PID alive: `{current['status'].get('pid_alive')}`",
        f"- Status age minutes: `{current['status'].get('age_minutes')}`",
        f"- Status stream mode: `{current['status'].get('stream_mode')}`",
        f"- Heartbeat status: `{current['heartbeat'].get('status')}`",
        f"- Heartbeat age minutes: `{current['heartbeat'].get('age_minutes')}`",
        f"- Heartbeat stream mode: `{current['heartbeat'].get('stream_mode')}`",
        f"- Latest collector decision: `{current['latest_report'].get('decision')}`",
        f"- Events stored: `{current['event_storage'].get('events')}`",
        f"- Preregistered-sample events: `{current['data_quality'].get('preregistered_sample_events')}`",
        f"- Research-universe events (including pre-lock): `{current['data_quality'].get('research_universe_events')}`",
        f"- Event files: `{current['event_storage'].get('files')}`",
        f"- Data-quality decision: `{current['data_quality'].get('decision')}`",
        f"- Runtime reasons: `{', '.join(report['runtime_reasons']) if report['runtime_reasons'] else 'none'}`",
        "",
        "## History",
        "",
        f"- Snapshots: `{report['history_summary'].get('snapshots')}`",
        f"- Decision counts: `{report['history_summary'].get('decision_counts')}`",
        f"- Events delta: `{report['history_summary'].get('events_delta')}`",
        f"- Heartbeat stale snapshots: `{report['history_summary'].get('heartbeat_stale_snapshots')}`",
        f"- Unhealthy snapshots: `{report['history_summary'].get('unhealthy_snapshots')}`",
        "",
        "## Logs",
        "",
        f"- Collector stdout lines sampled: `{current['logs']['collector_stdout'].get('lines')}`",
        f"- Collector stdout decisions: `{current['logs']['collector_stdout'].get('decisions')}`",
        f"- Watchdog stdout lines sampled: `{current['logs']['watchdog_stdout'].get('lines')}`",
        f"- Stderr size: `{current['logs']['collector_stderr_stat'].get('size')}`",
        "",
        "## Next Action",
        "",
        f"- {report['next_action']}",
        "",
        "## Boundary",
        "",
        "- Local supervisor summary only.",
        "- No private credentials, no alerts, no paper entries, no orders.",
        "- Does not infer edge from empty feed; it only proves collector health and event accumulation.",
        "",
    ]
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    now = now_utc()
    logs_dir = resolve_path(args.logs_dir)
    docs_dir = resolve_path(args.docs_dir)
    data_dir = resolve_path(args.data_dir)
    status_path = resolve_path(args.collector_status)
    heartbeat_path = resolve_path(args.collector_heartbeat)
    latest_path = docs_dir / "LIQUIDATION_FORCE_ORDER_FORWARD_COLLECTOR_LATEST.json"
    dq_path = resolve_path(args.data_quality)
    watchdog_path = resolve_path(args.watchdog_report)
    status = read_json(status_path)
    heartbeat = read_json(heartbeat_path)
    latest = read_json(latest_path)
    dq = read_json(dq_path)
    watchdog = read_json(watchdog_path)
    event_storage = read_event_files(data_dir)
    collector_stdout = logs_dir / "liquidation_force_order_stdout.log"
    collector_stderr = logs_dir / "liquidation_force_order_stderr.log"
    watchdog_stdout = logs_dir / "liquidation_force_order_watchdog_stdout.log"
    watchdog_stderr = logs_dir / "liquidation_force_order_watchdog_stderr.log"
    latest_stats = latest.get("stats") if isinstance(latest.get("stats"), dict) else {}
    dq_hard = [
        item.get("name")
        for item in dq.get("hard_failures", [])
        if isinstance(item, dict) and item.get("name")
    ]
    dq_events = dq.get("events") if isinstance(dq.get("events"), dict) else {}
    dq_universe = dq_events.get("research_universe") if isinstance(dq_events.get("research_universe"), dict) else dq_events
    dq_research = (
        dq_events.get("preregistered_sample")
        if isinstance(dq_events.get("preregistered_sample"), dict)
        else dq_universe
    )
    current = {
        "status": {
            "path": portable(status_path),
            "status": status.get("status"),
            "pid": status.get("pid"),
            "pid_alive": process_alive(status.get("pid")),
            "age_minutes": age_minutes(status.get("ts"), now),
            "symbols": status.get("symbols"),
            "stream_mode": status.get("stream_mode"),
        },
        "heartbeat": {
            "path": portable(heartbeat_path),
            "status": heartbeat.get("status"),
            "age_minutes": age_minutes(heartbeat.get("ts"), now),
            "can_trade": heartbeat.get("can_trade"),
            "stream_mode": heartbeat.get("stream_mode"),
            "streams": heartbeat.get("streams"),
            "events_written": heartbeat.get("events_written"),
            "messages_seen": heartbeat.get("messages_seen"),
            "last_message_at": heartbeat.get("last_message_at"),
            "last_event_at": heartbeat.get("last_event_at"),
            "parse_errors_count": heartbeat.get("parse_errors_count"),
        },
        "latest_report": {
            "path": portable(latest_path),
            "decision": latest.get("decision"),
            "mode": latest_stats.get("mode"),
            "events_written": latest_stats.get("events_written"),
            "messages_seen": latest_stats.get("messages_seen"),
            "parse_errors_count": len(latest_stats.get("parse_errors") or []),
            "started_at": latest_stats.get("started_at"),
            "ended_at": latest_stats.get("ended_at"),
        },
        "data_quality": {
            "path": portable(dq_path),
            "decision": dq.get("decision"),
            "hard_failures": dq_hard,
            "soft_failures": [
                item.get("name")
                for item in dq.get("soft_failures", [])
                if isinstance(item, dict) and item.get("name")
            ],
            "preregistered_sample_events": int(dq_research.get("events") or 0),
            "research_universe_events": int(dq_universe.get("events") or 0),
            "research_symbols": dq_research.get("symbols"),
            "all_market_events": int(dq_events.get("events") or 0),
        },
        "watchdog": {
            "path": portable(watchdog_path),
            "decision": watchdog.get("decision"),
            "first_event_guard": (watchdog.get("first_event_guard") or {}).get("report", {}).get("decision") if isinstance(watchdog.get("first_event_guard"), dict) else None,
        },
        "event_storage": event_storage,
        "logs": {
            "collector_stdout_stat": safe_stat(collector_stdout),
            "collector_stderr_stat": safe_stat(collector_stderr),
            "watchdog_stdout_stat": safe_stat(watchdog_stdout),
            "watchdog_stderr_stat": safe_stat(watchdog_stderr),
            "collector_stdout": count_lines(collector_stdout),
            "watchdog_stdout": count_lines(watchdog_stdout),
        },
    }
    decision, reasons, next_action = classify(current, args)
    snapshot = {
        "ts": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "decision": decision,
        "events_total": event_storage.get("events", 0),
        "preregistered_sample_events": current["data_quality"].get("preregistered_sample_events", 0),
        "research_universe_events": current["data_quality"].get("research_universe_events", 0),
        "status": current["status"].get("status"),
        "pid_alive": current["status"].get("pid_alive"),
        "status_age_minutes": current["status"].get("age_minutes"),
        "heartbeat_status": current["heartbeat"].get("status"),
        "heartbeat_age_minutes": current["heartbeat"].get("age_minutes"),
        "heartbeat_stale": current["heartbeat"].get("age_minutes") is None or current["heartbeat"].get("age_minutes") > args.max_heartbeat_age_minutes,
        "latest_decision": current["latest_report"].get("decision"),
        "data_quality_decision": current["data_quality"].get("decision"),
        "hard_failures": reasons,
    }
    history_path = resolve_path(args.history_path)
    if not args.no_append_history:
        append_history(history_path, snapshot)
    history_rows = read_history(history_path, args.lookback_hours)
    report = {
        "generated_at": snapshot["ts"],
        "tool": "tools/liquidation_force_order_supervisor_summary.py",
        "decision": decision,
        "can_trade": False,
        "boundary": {
            "supervisor_only": True,
            "sends_orders": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "inputs": {
            "lookback_hours": args.lookback_hours,
            "min_events_for_research": args.min_events_for_research,
            "max_status_age_minutes": args.max_status_age_minutes,
            "max_heartbeat_age_minutes": args.max_heartbeat_age_minutes,
            "history_path": portable(history_path),
        },
        "current": current,
        "runtime_reasons": reasons,
        "history_summary": summarize_history(history_rows),
        "next_action": next_action,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Supervisor summary for Binance forceOrder liquidation collector")
    parser.add_argument("--logs-dir", default="logs/liquidation_force_order")
    parser.add_argument("--docs-dir", default="docs")
    parser.add_argument("--data-dir", default="data/live/liquidations/binance_force_order")
    parser.add_argument("--collector-status", default="logs/liquidation_force_order/liquidation_force_order_loop_status.json")
    parser.add_argument("--collector-heartbeat", default="logs/liquidation_force_order/liquidation_force_order_collector_heartbeat.json")
    parser.add_argument("--data-quality", default="docs/LIQUIDATION_FORCE_ORDER_DATA_QUALITY_2026-06-30.json")
    parser.add_argument("--watchdog-report", default="docs/LIQUIDATION_FORCE_ORDER_COLLECTOR_WATCHDOG_2026-06-30.json")
    parser.add_argument("--history-path", default="logs/liquidation_force_order/supervisor_summary_history.jsonl")
    parser.add_argument("--lookback-hours", type=float, default=12.0)
    parser.add_argument("--min-events-for-research", type=int, default=500)
    parser.add_argument("--max-status-age-minutes", type=float, default=30.0)
    parser.add_argument("--max-heartbeat-age-minutes", type=float, default=30.0)
    parser.add_argument("--no-append-history", action="store_true")
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_FORCE_ORDER_SUPERVISOR_SUMMARY_2026-07-01")
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "events": report["current"]["event_storage"]["events"],
                "preregistered_sample_events": report["current"]["data_quality"]["preregistered_sample_events"],
                "history_snapshots": report["history_summary"]["snapshots"],
                "runtime_reasons": report["runtime_reasons"],
                "out": portable(out_prefix.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

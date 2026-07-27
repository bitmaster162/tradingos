#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = "bybit_v5_allLiquidation_websocket"
REQUIRED_FIELDS = {
    "event_time_ms",
    "event_time",
    "liquidation_time_ms",
    "liquidation_time",
    "symbol",
    "side",
    "price",
    "quantity",
    "notional_usd",
    "venue",
    "source",
    "is_real_liquidation_feed",
}
VALID_SIDES = {"BUY", "SELL"}


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


def ms_to_dt(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


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


def gate(name: str, passed: bool, actual: Any, required: Any, severity: str = "hard") -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "actual": actual, "required": required, "severity": severity}


def read_jsonl_rows(data_dir: Path, max_bad_lines: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Path]]:
    rows: list[dict[str, Any]] = []
    bad: list[dict[str, Any]] = []
    files = sorted(data_dir.rglob("*.jsonl")) if data_dir.exists() else []
    for path in files:
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                for line_no, line in enumerate(handle, start=1):
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        row = json.loads(text)
                    except json.JSONDecodeError as exc:
                        if len(bad) < max_bad_lines:
                            bad.append({"path": portable(path), "line": line_no, "error": str(exc)})
                        continue
                    if isinstance(row, dict):
                        row["_path"] = portable(path)
                        row["_line"] = line_no
                        rows.append(row)
                    elif len(bad) < max_bad_lines:
                        bad.append({"path": portable(path), "line": line_no, "error": "row_not_object"})
        except OSError as exc:
            if len(bad) < max_bad_lines:
                bad.append({"path": portable(path), "line": None, "error": str(exc)})
    return rows, bad, files


def validate_row(row: dict[str, Any], now: datetime) -> list[str]:
    errors: list[str] = []
    missing = sorted(field for field in REQUIRED_FIELDS if field not in row or row[field] in ("", None))
    if missing:
        errors.append(f"missing:{','.join(missing)}")
    if row.get("venue") != "bybit":
        errors.append(f"invalid_venue:{row.get('venue')}")
    if row.get("source") != SOURCE:
        errors.append(f"invalid_source:{row.get('source')}")
    if row.get("is_real_liquidation_feed") is not True:
        errors.append("not_real_liquidation_feed")
    if str(row.get("side") or "").upper() not in VALID_SIDES:
        errors.append(f"invalid_side:{row.get('side')}")
    event_dt = ms_to_dt(row.get("liquidation_time_ms"))
    if event_dt is None:
        errors.append("invalid_liquidation_time_ms")
    elif event_dt > now and (event_dt - now).total_seconds() > 300:
        errors.append("future_liquidation_time")
    elif event_dt.year < 2020:
        errors.append("liquidation_time_too_old")
    for field in ("price", "quantity", "notional_usd"):
        try:
            value = float(row.get(field))
        except (TypeError, ValueError):
            errors.append(f"invalid_number:{field}")
            continue
        if value <= 0:
            errors.append(f"non_positive:{field}")
    try:
        price = float(row.get("price"))
        quantity = float(row.get("quantity"))
        notional = float(row.get("notional_usd"))
        if abs(price * quantity - notional) > max(1e-6, price * quantity * 0.001):
            errors.append("notional_mismatch")
    except (TypeError, ValueError):
        pass
    return errors


def summarize_events(rows: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    if not rows:
        return {
            "events": 0,
            "by_symbol": {},
            "by_side": {},
            "first_event_time": None,
            "last_event_time": None,
            "last_event_age_minutes": None,
            "notional_total_usd": 0.0,
        }
    times = [ms_to_dt(row.get("liquidation_time_ms")) for row in rows]
    times = [item for item in times if item is not None]
    notionals: list[float] = []
    for row in rows:
        try:
            notionals.append(float(row.get("notional_usd")))
        except (TypeError, ValueError):
            continue
    last_event = max(times) if times else None
    return {
        "events": len(rows),
        "by_symbol": dict(Counter(str(row.get("symbol") or "UNKNOWN").upper() for row in rows)),
        "by_side": dict(Counter(str(row.get("side") or "UNKNOWN").upper() for row in rows)),
        "first_event_time": min(times).isoformat(timespec="seconds").replace("+00:00", "Z") if times else None,
        "last_event_time": last_event.isoformat(timespec="seconds").replace("+00:00", "Z") if last_event else None,
        "last_event_age_minutes": round((now - last_event).total_seconds() / 60.0, 3) if last_event else None,
        "notional_total_usd": round(sum(notionals), 6),
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    now = now_utc()
    data_dir = resolve_path(args.data_dir)
    heartbeat_path = resolve_path(args.collector_heartbeat)
    latest_path = resolve_path(args.latest_collector_report)
    heartbeat = read_json(heartbeat_path)
    latest = read_json(latest_path)
    latest_stats = latest.get("stats") if isinstance(latest.get("stats"), dict) else {}
    rows, bad_lines, files = read_jsonl_rows(data_dir, args.max_bad_lines)
    validation_errors: list[dict[str, Any]] = []
    for row in rows:
        errors = validate_row(row, now)
        if errors and len(validation_errors) < args.max_bad_lines:
            validation_errors.append({"path": row.get("_path"), "line": row.get("_line"), "symbol": row.get("symbol"), "errors": errors})
    event_summary = summarize_events(rows, now)
    heartbeat_age = age_minutes(heartbeat.get("ts"), now)
    latest_parse_errors = latest_stats.get("parse_errors") if isinstance(latest_stats.get("parse_errors"), list) else []
    gates = [
        gate("collector_heartbeat_exists", bool(heartbeat) and not heartbeat.get("_read_error"), portable(heartbeat_path), "readable JSON"),
        gate("collector_heartbeat_recent", heartbeat_age is not None and heartbeat_age <= args.max_heartbeat_age_minutes, heartbeat_age, f"<= {args.max_heartbeat_age_minutes} minutes"),
        gate("collector_heartbeat_can_trade_false", heartbeat.get("can_trade") is False, heartbeat.get("can_trade"), False),
        gate("latest_collector_report_exists", bool(latest) and not latest.get("_read_error"), portable(latest_path), "readable JSON", severity="soft"),
        gate("latest_collector_no_parse_errors", len(latest_parse_errors) == 0, len(latest_parse_errors), 0, severity="soft"),
        gate("jsonl_parse_errors_zero", len(bad_lines) == 0, len(bad_lines), 0),
        gate("schema_errors_zero", len(validation_errors) == 0, len(validation_errors), 0),
        gate("minimum_events_for_research", int(event_summary["events"]) >= args.min_events_for_research, event_summary["events"], f">= {args.min_events_for_research}", severity="soft"),
    ]
    hard_failures = [item for item in gates if item["severity"] == "hard" and not item["passed"]]
    soft_failures = [item for item in gates if item["severity"] == "soft" and not item["passed"]]
    if hard_failures:
        decision = "bybit_liquidation_data_quality_hard_fail"
        next_action = "fix Bybit data-quality hard failures before using rows"
    elif int(event_summary["events"]) == 0:
        decision = "bybit_liquidation_collector_alive_no_events_yet"
        next_action = "keep Bybit collector running; no research consumer until real rows accumulate"
    elif int(event_summary["events"]) < args.min_events_for_research:
        decision = "bybit_liquidation_collecting_insufficient_sample"
        next_action = "continue collecting until minimum Bybit liquidation event sample is reached"
    else:
        decision = "bybit_liquidation_data_ready_for_preregistered_research"
        next_action = "merge with Binance forceOrder context only through a preregistered hypothesis"
    return {
        "generated_at": now_iso(),
        "tool": "tools/bybit_all_liquidation_data_quality.py",
        "decision": decision,
        "can_trade": False,
        "boundary": {"data_quality_only": True, "sends_orders": False, "uses_private_credentials": False, "can_trade": False},
        "inputs": {"data_dir": portable(data_dir), "collector_heartbeat": portable(heartbeat_path), "latest_collector_report": portable(latest_path), "min_events_for_research": args.min_events_for_research},
        "collector": {
            "heartbeat_status": heartbeat.get("status"),
            "heartbeat_age_minutes": heartbeat_age,
            "heartbeat_events_written": heartbeat.get("events_written"),
            "heartbeat_messages_seen": heartbeat.get("messages_seen"),
            "latest_report_decision": latest.get("decision"),
            "latest_messages_seen": latest_stats.get("messages_seen"),
            "latest_events_written": latest_stats.get("events_written"),
        },
        "storage": {"files": len(files), "paths": [portable(path) for path in files[:20]], "bad_lines_sample": bad_lines},
        "events": event_summary,
        "validation": {"schema_error_rows_sample": validation_errors},
        "gates": gates,
        "hard_failures": hard_failures,
        "soft_failures": soft_failures,
        "next_action": next_action,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Bybit All Liquidation Data Quality",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `false`",
        "",
        "## Collector",
        "",
        f"- Heartbeat status: `{report['collector']['heartbeat_status']}`",
        f"- Heartbeat age minutes: `{report['collector']['heartbeat_age_minutes']}`",
        f"- Latest collector decision: `{report['collector']['latest_report_decision']}`",
        "",
        "## Events",
        "",
        f"- Files: `{report['storage']['files']}`",
        f"- Events: `{report['events']['events']}`",
        f"- By symbol: `{report['events']['by_symbol']}`",
        f"- By side: `{report['events']['by_side']}`",
        f"- Notional total USD: `{report['events']['notional_total_usd']}`",
        "",
        "## Gates",
        "",
        "| Gate | Passed | Severity | Actual | Required |",
        "|---|---:|---|---|---|",
    ]
    for item in report["gates"]:
        lines.append(f"| `{item['name']}` | `{str(item['passed']).lower()}` | `{item['severity']}` | `{item['actual']}` | `{item['required']}` |")
    lines.extend(["", "## Next Action", "", f"- {report['next_action']}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Data-quality guard for Bybit V5 allLiquidation feed")
    parser.add_argument("--data-dir", default="data/live/liquidations/bybit_all_liquidation")
    parser.add_argument("--collector-heartbeat", default="logs/liquidation_bybit/bybit_all_liquidation_collector_heartbeat.json")
    parser.add_argument("--latest-collector-report", default="docs/BYBIT_ALL_LIQUIDATION_FORWARD_COLLECTOR_LATEST.json")
    parser.add_argument("--min-events-for-research", type=int, default=500)
    parser.add_argument("--max-heartbeat-age-minutes", type=float, default=30.0)
    parser.add_argument("--max-bad-lines", type=int, default=25)
    parser.add_argument("--out-prefix", default="docs/BYBIT_ALL_LIQUIDATION_DATA_QUALITY_2026-07-01")
    args = parser.parse_args()
    report = build_report(args)
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "events": report["events"]["events"], "hard_failures": [item["name"] for item in report["hard_failures"]], "soft_failures": [item["name"] for item in report["soft_failures"]], "out": portable(out.with_suffix(".json")), "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

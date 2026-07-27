#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FIELDS = {
    "event_time_ms",
    "event_time",
    "trade_time_ms",
    "trade_time",
    "symbol",
    "side",
    "price",
    "quantity",
    "notional_usd",
    "source",
    "is_real_liquidation_feed",
}
VALID_SIDES = {"BUY", "SELL"}
VALID_SOURCE = "binance_usdm_forceOrder_websocket"
DEFAULT_RESEARCH_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BCHUSDT")
DEFAULT_PREREG_LOCK = "configs/BINANCE_FORCE_ORDER_EVENT_STUDY_PREREG_2026-07-12.json"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds").replace("+00:00", "Z")


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc), "_path": str(path)}
    return value if isinstance(value, dict) else {"_read_error": "not_object", "_path": str(path)}


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


def parse_symbols(value: str) -> list[str]:
    symbols = [item.strip().upper() for item in value.split(",") if item.strip()]
    return list(dict.fromkeys(symbols))


def process_alive(value: Any) -> bool:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
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
    return {
        "name": name,
        "passed": bool(passed),
        "actual": actual,
        "required": required,
        "severity": severity,
    }


def read_jsonl_rows(data_dir: Path, max_bad_lines: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Path]]:
    rows: list[dict[str, Any]] = []
    bad_lines: list[dict[str, Any]] = []
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
                        if len(bad_lines) < max_bad_lines:
                            bad_lines.append({"path": portable_path(path), "line": line_no, "error": str(exc)})
                        continue
                    if isinstance(row, dict):
                        row["_path"] = portable_path(path)
                        row["_line"] = line_no
                        rows.append(row)
                    elif len(bad_lines) < max_bad_lines:
                        bad_lines.append({"path": portable_path(path), "line": line_no, "error": "row_not_object"})
        except OSError as exc:
            if len(bad_lines) < max_bad_lines:
                bad_lines.append({"path": portable_path(path), "line": None, "error": str(exc)})
    return rows, bad_lines, files


def validate_row(row: dict[str, Any], now: datetime) -> list[str]:
    errors: list[str] = []
    missing = sorted(field for field in REQUIRED_FIELDS if field not in row or row[field] in ("", None))
    if missing:
        errors.append(f"missing:{','.join(missing)}")
    if row.get("is_real_liquidation_feed") is not True:
        errors.append("not_real_liquidation_feed")
    if row.get("source") != VALID_SOURCE:
        errors.append(f"invalid_source:{row.get('source')}")
    if str(row.get("side") or "").upper() not in VALID_SIDES:
        errors.append(f"invalid_side:{row.get('side')}")
    event_dt = ms_to_dt(row.get("event_time_ms"))
    if event_dt is None:
        errors.append("invalid_event_time_ms")
    elif event_dt > now.replace(microsecond=0):
        # Allow five minutes of clock skew.
        if (event_dt - now).total_seconds() > 300:
            errors.append("future_event_time")
    elif event_dt.year < 2020:
        errors.append("event_time_too_old")
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
        if price > 0 and quantity > 0:
            recomputed = price * quantity
            tolerance = max(1e-6, recomputed * 0.001)
            if abs(recomputed - notional) > tolerance:
                errors.append("notional_mismatch")
    except (TypeError, ValueError):
        pass
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    if raw.get("E") == 1760000000000:
        errors.append("synthetic_sample_timestamp")
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
            "notional_median_usd": None,
        }
    event_times = [ms_to_dt(row.get("event_time_ms")) for row in rows]
    event_times = [item for item in event_times if item is not None]
    notionals = []
    for row in rows:
        try:
            notionals.append(float(row.get("notional_usd")))
        except (TypeError, ValueError):
            continue
    last_event = max(event_times) if event_times else None
    return {
        "events": len(rows),
        "by_symbol": dict(Counter(str(row.get("symbol") or "UNKNOWN").upper() for row in rows)),
        "by_side": dict(Counter(str(row.get("side") or "UNKNOWN").upper() for row in rows)),
        "first_event_time": min(event_times).isoformat(timespec="seconds").replace("+00:00", "Z") if event_times else None,
        "last_event_time": last_event.isoformat(timespec="seconds").replace("+00:00", "Z") if last_event else None,
        "last_event_age_minutes": round((now - last_event).total_seconds() / 60.0, 3) if last_event else None,
        "notional_total_usd": round(sum(notionals), 6),
        "notional_median_usd": round(statistics.median(notionals), 6) if notionals else None,
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    scan_started_at = now_utc()
    data_dir = resolve_path(args.data_dir)
    status_path = resolve_path(args.collector_status)
    heartbeat_path = resolve_path(args.collector_heartbeat)
    latest_report_path = resolve_path(args.latest_collector_report)
    contract_path = resolve_path(args.contract)
    prereg_lock_path = resolve_path(getattr(args, "prereg_lock", DEFAULT_PREREG_LOCK))
    status = read_json(status_path)
    heartbeat = read_json(heartbeat_path)
    latest = read_json(latest_report_path)
    contract = read_json(contract_path)
    prereg_lock = read_json(prereg_lock_path)
    rows, bad_lines, files = read_jsonl_rows(data_dir, args.max_bad_lines)
    evaluation_now = now_utc()

    validation_errors: list[dict[str, Any]] = []
    synthetic_rows = 0
    for row in rows:
        errors = validate_row(row, evaluation_now)
        if any(error == "synthetic_sample_timestamp" for error in errors):
            synthetic_rows += 1
        if errors and len(validation_errors) < args.max_bad_lines:
            validation_errors.append(
                {
                    "path": row.get("_path"),
                    "line": row.get("_line"),
                    "symbol": row.get("symbol"),
                    "errors": errors,
                }
            )

    event_summary = summarize_events(rows, evaluation_now)
    fixed_study = prereg_lock.get("fixed_study") if isinstance(prereg_lock.get("fixed_study"), dict) else {}
    locked_symbols = fixed_study.get("symbols") if isinstance(fixed_study.get("symbols"), list) else []
    research_symbols = [str(item).upper() for item in locked_symbols if str(item).strip()] or parse_symbols(args.research_symbols)
    event_start_at = parse_ts(fixed_study.get("event_start_at"))
    required_research_events = int(fixed_study.get("minimum_events") or 0)
    prereg_lock_valid = bool(
        prereg_lock.get("status") == "accepted_preregistered_research_only"
        and prereg_lock.get("can_trade") is False
        and fixed_study.get("interval") == "1h"
        and fixed_study.get("signal_time") == "event_bar_close"
        and fixed_study.get("entry_time") == "next_bar_open"
        and fixed_study.get("return_measurement") == "next_bar_open_to_horizon_close"
        and event_start_at is not None
        and research_symbols
        and required_research_events > 0
    )
    research_symbol_set = set(research_symbols)
    research_rows = [row for row in rows if str(row.get("symbol") or "").upper() in research_symbol_set]
    research_summary = summarize_events(research_rows, evaluation_now)
    research_summary["symbols"] = research_symbols
    research_summary["by_symbol"] = {
        symbol: int(research_summary.get("by_symbol", {}).get(symbol, 0))
        for symbol in research_symbols
    }
    event_summary["research_universe"] = research_summary
    preregistered_rows = [
        row
        for row in research_rows
        if event_start_at is not None
        and (event_dt := ms_to_dt(row.get("event_time_ms"))) is not None
        and event_dt >= event_start_at
    ]
    preregistered_summary = summarize_events(preregistered_rows, evaluation_now)
    preregistered_summary["symbols"] = research_symbols
    preregistered_summary["event_start_at"] = (
        event_start_at.isoformat(timespec="seconds").replace("+00:00", "Z") if event_start_at else None
    )
    preregistered_summary["by_symbol"] = {
        symbol: int(preregistered_summary.get("by_symbol", {}).get(symbol, 0))
        for symbol in research_symbols
    }
    event_summary["preregistered_sample"] = preregistered_summary
    research_events = int(preregistered_summary["events"])
    status_age = age_minutes(status.get("ts"), evaluation_now)
    heartbeat_age = age_minutes(heartbeat.get("ts"), evaluation_now)
    collector_pid_alive = process_alive(status.get("pid"))
    latest_stats = latest.get("stats") if isinstance(latest.get("stats"), dict) else {}
    latest_parse_errors = latest_stats.get("parse_errors") if isinstance(latest_stats.get("parse_errors"), list) else []
    latest_liveness_messages = int(latest_stats.get("liveness_messages_seen") or 0)
    heartbeat_liveness_messages = int(heartbeat.get("liveness_messages_seen") or 0)
    transport_liveness_ok = bool(
        int(latest_stats.get("events_written") or 0) > 0
        or latest_liveness_messages > 0
        or heartbeat_liveness_messages > 0
    )
    status_ok_values = {
        "running",
        "running_collector_cycle",
        "ran_collector_cycle",
        "sleeping_initial",
        "skipped_existing_liquidation_force_order_loop",
    }

    gates = [
        gate("contract_exists", bool(contract) and not contract.get("_read_error"), portable_path(contract_path), "readable JSON"),
        gate("contract_can_trade_false", contract.get("can_trade") is False, contract.get("can_trade"), False),
        gate(
            "preregistered_research_lock_valid",
            prereg_lock_valid,
            {"lock_id": prereg_lock.get("lock_id"), "event_start_at": fixed_study.get("event_start_at")},
            "accepted lock with fixed 1h study and can_trade=false",
            severity="soft",
        ),
        gate("collector_status_exists", bool(status) and not status.get("_read_error"), portable_path(status_path), "readable JSON"),
        gate("collector_status_recent", status_age is not None and status_age <= args.max_status_age_minutes, status_age, f"<= {args.max_status_age_minutes} minutes"),
        gate("collector_status_ok", status.get("status") in status_ok_values, status.get("status"), sorted(status_ok_values)),
        gate("collector_pid_alive", collector_pid_alive, status.get("pid"), "running process"),
        gate("collector_heartbeat_exists", bool(heartbeat) and not heartbeat.get("_read_error"), portable_path(heartbeat_path), "readable JSON", severity="soft"),
        gate("collector_heartbeat_recent", heartbeat_age is not None and heartbeat_age <= args.max_heartbeat_age_minutes, heartbeat_age, f"<= {args.max_heartbeat_age_minutes} minutes", severity="soft"),
        gate("collector_heartbeat_can_trade_false", heartbeat.get("can_trade") is False, heartbeat.get("can_trade"), False, severity="soft"),
        gate("latest_collector_report_exists", bool(latest) and not latest.get("_read_error"), portable_path(latest_report_path), "readable JSON", severity="soft"),
        gate("latest_collector_no_parse_errors", len(latest_parse_errors) == 0, len(latest_parse_errors), 0, severity="soft"),
        gate(
            "collector_transport_liveness",
            transport_liveness_ok,
            {
                "latest_liveness_messages": latest_liveness_messages,
                "heartbeat_liveness_messages": heartbeat_liveness_messages,
                "latest_events_written": latest_stats.get("events_written"),
            },
            "at least one canary frame or real liquidation event",
        ),
        gate("jsonl_parse_errors_zero", len(bad_lines) == 0, len(bad_lines), 0),
        gate("schema_errors_zero", len(validation_errors) == 0, len(validation_errors), 0),
        gate("synthetic_rows_zero", synthetic_rows == 0, synthetic_rows, 0),
        gate(
            "minimum_preregistered_sample_events",
            research_events >= required_research_events,
            research_events,
            f">= {required_research_events}",
            severity="soft",
        ),
    ]
    hard_failures = [item for item in gates if item["severity"] == "hard" and not item["passed"]]
    soft_failures = [item for item in gates if item["severity"] == "soft" and not item["passed"]]

    if hard_failures:
        decision = "liquidation_force_order_data_quality_hard_fail"
        ready_for_preregistered_research = False
        next_action = "fix hard data-quality gates before using forceOrder rows anywhere"
    elif int(event_summary["events"]) == 0:
        decision = "liquidation_force_order_collector_alive_no_events_yet"
        ready_for_preregistered_research = False
        next_action = "keep collector running; no strategy consumer until real event rows accumulate"
    elif not prereg_lock_valid:
        decision = "liquidation_force_order_waiting_preregistration_lock"
        ready_for_preregistered_research = False
        next_action = "repair or accept the fixed preregistration lock before evaluating any event sample"
    elif research_events < required_research_events:
        decision = "liquidation_force_order_collecting_insufficient_sample"
        ready_for_preregistered_research = False
        next_action = "continue collecting until the fixed BTC/ETH/SOL/BCH research universe reaches its minimum event sample"
    else:
        decision = "liquidation_force_order_data_ready_for_preregistered_research"
        ready_for_preregistered_research = True
        next_action = "preregister a liquidation-feed hypothesis before any backtest or strategy consumer"

    return {
        "generated_at": iso_utc(evaluation_now),
        "tool": "tools/liquidation_force_order_data_quality.py",
        "decision": decision,
        "can_trade": False,
        "ready_for_preregistered_research": ready_for_preregistered_research,
        "strategy_consumer_allowed": False,
        "boundary": {
            "data_quality_only": True,
            "sends_orders": False,
            "uses_private_credentials": False,
            "network_required": False,
        },
        "scan": {
            "started_at": iso_utc(scan_started_at),
            "evaluation_at": iso_utc(evaluation_now),
            "duration_seconds": round((evaluation_now - scan_started_at).total_seconds(), 3),
            "evaluation_clock_captured_after_storage_read": True,
        },
        "inputs": {
            "data_dir": portable_path(data_dir),
            "collector_status": portable_path(status_path),
            "latest_collector_report": portable_path(latest_report_path),
            "contract": portable_path(contract_path),
            "prereg_lock": portable_path(prereg_lock_path),
            "prereg_lock_id": prereg_lock.get("lock_id"),
            "research_symbols": research_symbols,
            "min_events_for_research": required_research_events,
            "cli_min_events_ignored_in_favor_of_lock": args.min_events_for_research,
        },
        "collector": {
            "status": status.get("status"),
            "pid": status.get("pid"),
            "pid_alive": collector_pid_alive,
            "status_age_minutes": status_age,
            "latest_report_decision": latest.get("decision"),
            "latest_report_mode": latest_stats.get("mode"),
            "latest_messages_seen": latest_stats.get("messages_seen"),
            "latest_liveness_stream": latest_stats.get("liveness_stream"),
            "latest_liveness_messages_seen": latest_liveness_messages,
            "latest_events_written": latest_stats.get("events_written"),
            "heartbeat_status": heartbeat.get("status"),
            "heartbeat_age_minutes": heartbeat_age,
            "heartbeat_events_written": heartbeat.get("events_written"),
            "heartbeat_messages_seen": heartbeat.get("messages_seen"),
            "heartbeat_liveness_messages_seen": heartbeat_liveness_messages,
            "heartbeat_last_liveness_at": heartbeat.get("last_liveness_at"),
            "heartbeat_last_event_at": heartbeat.get("last_event_at"),
        },
        "storage": {
            "files": len(files),
            "paths": [portable_path(path) for path in files[:20]],
            "bad_lines_sample": bad_lines,
        },
        "events": event_summary,
        "validation": {
            "schema_error_rows_sample": validation_errors,
            "synthetic_rows": synthetic_rows,
        },
        "gates": gates,
        "hard_failures": hard_failures,
        "soft_failures": soft_failures,
        "next_action": next_action,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Liquidation ForceOrder Data Quality",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `{str(report['can_trade']).lower()}`",
        f"- Ready for preregistered research: `{str(report['ready_for_preregistered_research']).lower()}`",
        f"- Strategy consumer allowed: `{str(report['strategy_consumer_allowed']).lower()}`",
        "",
        "## Collector",
        "",
        f"- Status: `{report['collector']['status']}`",
        f"- PID: `{report['collector']['pid']}`",
        f"- PID alive: `{str(report['collector']['pid_alive']).lower()}`",
        f"- Status age minutes: `{report['collector']['status_age_minutes']}`",
        f"- Latest collector decision: `{report['collector']['latest_report_decision']}`",
        f"- Heartbeat status: `{report['collector']['heartbeat_status']}`",
        f"- Heartbeat age minutes: `{report['collector']['heartbeat_age_minutes']}`",
        f"- Heartbeat events written: `{report['collector']['heartbeat_events_written']}`",
        f"- Heartbeat messages seen: `{report['collector']['heartbeat_messages_seen']}`",
        f"- Heartbeat liveness messages seen: `{report['collector']['heartbeat_liveness_messages_seen']}`",
        f"- Heartbeat last liveness at: `{report['collector']['heartbeat_last_liveness_at']}`",
        f"- Heartbeat last event at: `{report['collector']['heartbeat_last_event_at']}`",
        "",
        "## Events",
        "",
        f"- Files: `{report['storage']['files']}`",
        f"- Events: `{report['events']['events']}`",
        f"- By symbol: `{report['events']['by_symbol']}`",
        f"- Research universe: `{report['events']['research_universe']['symbols']}`",
        f"- Research-universe events: `{report['events']['research_universe']['events']}`",
        f"- Research-universe by symbol: `{report['events']['research_universe']['by_symbol']}`",
        f"- Preregistered sample starts: `{report['events']['preregistered_sample']['event_start_at']}`",
        f"- Preregistered-sample events: `{report['events']['preregistered_sample']['events']}`",
        f"- Preregistered-sample by symbol: `{report['events']['preregistered_sample']['by_symbol']}`",
        f"- First event: `{report['events']['first_event_time']}`",
        f"- Last event: `{report['events']['last_event_time']}`",
        f"- Last event age minutes: `{report['events']['last_event_age_minutes']}`",
        f"- Notional total USD: `{report['events']['notional_total_usd']}`",
        "",
        "## Gates",
        "",
        "| Gate | Passed | Severity | Actual | Required |",
        "|---|---:|---|---|---|",
    ]
    for item in report["gates"]:
        lines.append(
            f"| `{item['name']}` | `{str(item['passed']).lower()}` | `{item['severity']}` | "
            f"`{item['actual']}` | `{item['required']}` |"
        )
    lines.extend(
        [
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}",
            "",
            "## Boundary",
            "",
            "- This is a data-quality report only.",
            "- It sends no orders and uses no private credentials.",
            "- Empty live feed is `not_ready`, not a synthetic substitute.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Data-quality guard for Binance USD-M forceOrder liquidation feed")
    parser.add_argument("--data-dir", default="data/live/liquidations/binance_force_order")
    parser.add_argument("--collector-status", default="logs/liquidation_force_order/liquidation_force_order_loop_status.json")
    parser.add_argument("--collector-heartbeat", default="logs/liquidation_force_order/liquidation_force_order_collector_heartbeat.json")
    parser.add_argument("--latest-collector-report", default="docs/LIQUIDATION_FORCE_ORDER_FORWARD_COLLECTOR_LATEST.json")
    parser.add_argument("--contract", default="configs/LIQUIDATION_REAL_FEED_CONTRACT.json")
    parser.add_argument("--prereg-lock", default=DEFAULT_PREREG_LOCK)
    parser.add_argument("--research-symbols", default=",".join(DEFAULT_RESEARCH_SYMBOLS))
    parser.add_argument("--min-events-for-research", type=int, default=500)
    parser.add_argument("--max-status-age-minutes", type=float, default=30.0)
    parser.add_argument("--max-heartbeat-age-minutes", type=float, default=30.0)
    parser.add_argument("--max-bad-lines", type=int, default=25)
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_FORCE_ORDER_DATA_QUALITY_2026-06-30")
    args = parser.parse_args()

    report = build_report(args)
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "events": report["events"]["events"],
                "research_universe_events": report["events"]["research_universe"]["events"],
                "preregistered_sample_events": report["events"]["preregistered_sample"]["events"],
                "hard_failures": [item["name"] for item in report["hard_failures"]],
                "soft_failures": [item["name"] for item in report["soft_failures"]],
                "out": portable_path(out.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

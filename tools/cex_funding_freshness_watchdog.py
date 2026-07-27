#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "configs" / "CEX_FUNDING_FRESHNESS_WATCHDOG_LOCK_2026-07-13.json"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "CEX_FUNDING_FRESHNESS_WATCHDOG_2026-07-13"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc)}
    return value if isinstance(value, dict) else {"_read_error": "not_object"}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_seconds(value: Any, observed_at: datetime) -> float | None:
    parsed = parse_ts(value)
    if parsed is None:
        return None
    return round((observed_at - parsed).total_seconds(), 3)


def process_alive(pid_value: Any) -> bool:
    try:
        pid = int(pid_value)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) == 0:
                return False
            return exit_code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_jsonl_tail(path: Path, limit: int) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "rows": [], "trailing_bad_lines": 0, "size_bytes": 0}
    try:
        lines = [line for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    except OSError as exc:
        return {"exists": True, "rows": [], "trailing_bad_lines": 0, "size_bytes": path.stat().st_size, "read_error": str(exc)}
    rows: list[dict[str, Any]] = []
    trailing_bad_lines = 0
    for line in reversed(lines):
        if len(rows) >= limit:
            break
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            trailing_bad_lines += 1
            continue
        if isinstance(value, dict):
            rows.append(value)
        else:
            trailing_bad_lines += 1
    rows.reverse()
    return {
        "exists": True,
        "rows": rows,
        "trailing_bad_lines": trailing_bad_lines,
        "size_bytes": path.stat().st_size,
    }


def source_health(name: str, rows: list[dict[str, Any]], observed_at: datetime, contract: dict[str, Any]) -> dict[str, Any]:
    maximum_age = float(contract["maximum_source_age_seconds"])
    maximum_gap = float(contract["maximum_recent_gap_minutes"])
    buckets = [int(row["minute_bucket_ms"]) for row in rows if row.get("minute_bucket_ms") is not None]
    latest = rows[-1] if rows else {}
    latest_bucket_ms = int(latest["minute_bucket_ms"]) if latest.get("minute_bucket_ms") is not None else None
    latest_observed_at = latest.get("observed_at")
    latest_event_age = age_seconds(latest_observed_at, observed_at)
    latest_bucket_age = (
        round((observed_at.timestamp() * 1000 - latest_bucket_ms) / 1000.0, 3)
        if latest_bucket_ms is not None
        else None
    )
    gaps = [(right - left) / 60_000.0 for left, right in zip(buckets, buckets[1:]) if right >= left]
    maximum_recent_gap = max(gaps) if gaps else 0.0
    reasons: list[str] = []
    if not rows:
        reasons.append(f"{name}_journal_empty_or_unreadable")
    if latest_bucket_ms is None:
        reasons.append(f"{name}_minute_bucket_missing")
    if latest_event_age is None or latest_event_age < -60 or latest_event_age > maximum_age:
        reasons.append(f"{name}_source_stale")
    if latest_bucket_age is None or latest_bucket_age < -60 or latest_bucket_age > maximum_age:
        reasons.append(f"{name}_source_stale")
    if maximum_recent_gap > maximum_gap:
        reasons.append(f"{name}_recent_gap_exceeded")
    return {
        "healthy": not reasons,
        "rows_inspected": len(rows),
        "latest_observed_at": latest_observed_at,
        "latest_minute_bucket": latest.get("minute_bucket"),
        "latest_minute_bucket_ms": latest_bucket_ms,
        "source_age_seconds": latest_event_age,
        "bucket_age_seconds": latest_bucket_age,
        "maximum_recent_gap_minutes": round(maximum_recent_gap, 3),
        "reasons": sorted(set(reasons)),
    }


def build_report(
    contract: dict[str, Any],
    loop_lock: dict[str, Any],
    loop_status: dict[str, Any],
    aggregate_rows: list[dict[str, Any]],
    direct_rows: list[dict[str, Any]],
    stderr_size: int,
    previous_state: dict[str, Any],
    observed_at: datetime,
    pid_checker: Callable[[Any], bool] = process_alive,
) -> dict[str, Any]:
    health = contract["health_contract"]
    status_age = age_seconds(loop_status.get("ts"), observed_at)
    lock_pid = loop_lock.get("pid")
    status_pid = loop_status.get("pid")
    pid_is_alive = pid_checker(lock_pid)
    pid_matches = str(lock_pid) == str(status_pid) and lock_pid not in (None, "")
    allowed_status = loop_status.get("status") in set(health["allowed_loop_statuses"])
    status_fresh = status_age is not None and -60 <= status_age <= float(health["maximum_status_age_seconds"])

    exit_fields = ("exit_code", "primary_exit_code", "direct_replication_exit_code", "source_alignment_exit_code")
    exit_codes = {field: loop_status.get(field) for field in exit_fields}
    zero_exits = all(value == 0 for value in exit_codes.values())

    aggregate = source_health("aggregate", aggregate_rows, observed_at, health)
    direct = source_health("direct", direct_rows, observed_at, health)
    aggregate_bucket = aggregate.get("latest_minute_bucket_ms")
    direct_bucket = direct.get("latest_minute_bucket_ms")
    source_skew_minutes = (
        abs(int(aggregate_bucket) - int(direct_bucket)) / 60_000.0
        if aggregate_bucket is not None and direct_bucket is not None
        else None
    )
    source_skew_ok = source_skew_minutes is not None and source_skew_minutes <= float(health["maximum_source_skew_minutes"])

    previous_stderr_size = int(previous_state.get("stderr_size_bytes", stderr_size))
    stderr_growth_bytes = max(0, stderr_size - previous_stderr_size)
    stderr_growth_ok = not bool(health["fail_on_stderr_growth"]) or stderr_growth_bytes == 0

    checks = {
        "loop_lock_present": bool(loop_lock) and not loop_lock.get("_read_error"),
        "loop_status_present": bool(loop_status) and not loop_status.get("_read_error"),
        "pid_alive": pid_is_alive,
        "pid_match": pid_matches if health["require_pid_match"] else True,
        "allowed_loop_status": allowed_status,
        "status_fresh": status_fresh,
        "zero_exit_codes": zero_exits if health["require_zero_exit_codes"] else True,
        "aggregate_source_fresh": aggregate["healthy"],
        "direct_source_fresh": direct["healthy"],
        "source_skew_within_limit": source_skew_ok,
        "stderr_not_growing": stderr_growth_ok,
    }
    blockers = [name for name, passed in checks.items() if not passed]
    decision = "cex_funding_freshness_healthy" if not blockers else "cex_funding_freshness_blocked"
    return {
        "schema_version": 1,
        "generated_at": iso_utc(observed_at),
        "tool": "tools/cex_funding_freshness_watchdog.py",
        "lock_id": contract.get("lock_id"),
        "decision": decision,
        "healthy": not blockers,
        "checks": checks,
        "blockers": blockers,
        "loop": {
            "status": loop_status.get("status"),
            "status_at": loop_status.get("ts"),
            "status_age_seconds": status_age,
            "lock_pid": lock_pid,
            "status_pid": status_pid,
            "pid_alive": pid_is_alive,
            "pid_match": pid_matches,
            "exit_codes": exit_codes,
            "cadence_policy": loop_status.get("cadence_policy"),
        },
        "sources": {
            "aggregate": aggregate,
            "direct": direct,
            "latest_bucket_skew_minutes": None if source_skew_minutes is None else round(source_skew_minutes, 3),
        },
        "stderr": {
            "size_bytes": stderr_size,
            "previous_size_bytes": previous_stderr_size,
            "growth_bytes": stderr_growth_bytes,
        },
        "runtime_boundary": contract.get("runtime_boundary", {}),
        "automatic_restart_attempted": False,
        "edge_evaluated": False,
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    aggregate = report["sources"]["aggregate"]
    direct = report["sources"]["direct"]
    return "\n".join(
        [
            "# CEX Funding Freshness Watchdog",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            f"- Healthy: `{str(report['healthy']).lower()}`",
            f"- Blockers: `{', '.join(report['blockers']) if report['blockers'] else 'none'}`",
            f"- Can trade: `{str(report['can_trade']).lower()}`",
            "",
            "## Loop",
            "",
            f"- Status: `{report['loop']['status']}`",
            f"- PID: `{report['loop']['lock_pid']}`; alive: `{report['loop']['pid_alive']}`; match: `{report['loop']['pid_match']}`",
            f"- Status age seconds: `{report['loop']['status_age_seconds']}`",
            f"- Exit codes: `{report['loop']['exit_codes']}`",
            "",
            "## Sources",
            "",
            f"- Aggregate event/bucket age seconds: `{aggregate['source_age_seconds']}` / `{aggregate['bucket_age_seconds']}`; recent max gap minutes: `{aggregate['maximum_recent_gap_minutes']}`",
            f"- Direct event/bucket age seconds: `{direct['source_age_seconds']}` / `{direct['bucket_age_seconds']}`; recent max gap minutes: `{direct['maximum_recent_gap_minutes']}`",
            f"- Latest bucket skew minutes: `{report['sources']['latest_bucket_skew_minutes']}`",
            f"- Stderr growth bytes: `{report['stderr']['growth_bytes']}`",
            "",
            "## Boundary",
            "",
            "- Operational health only; no restart is attempted.",
            "- Missing rows are never fabricated or backfilled.",
            "- Healthy collection does not establish source equivalence or a trading edge.",
            "- Credentials, signals, paper entries and orders remain disabled.",
        ]
    ) + "\n"


def run(contract_path: Path, out_prefix: Path) -> dict[str, Any]:
    contract = read_json(contract_path)
    inputs = contract["inputs"]
    health = contract["health_contract"]
    loop_lock = read_json(resolve_path(inputs["loop_lock"]))
    loop_status = read_json(resolve_path(inputs["loop_status"]))
    aggregate_tail = read_jsonl_tail(resolve_path(inputs["aggregate_journal"]), int(health["recent_window_rows"]))
    direct_tail = read_jsonl_tail(resolve_path(inputs["direct_journal"]), int(health["recent_window_rows"]))
    stderr_path = resolve_path(inputs["collector_stderr"])
    stderr_size = stderr_path.stat().st_size if stderr_path.exists() else 0
    state_path = resolve_path(inputs["watchdog_state"])
    previous_state = read_json(state_path)
    report = build_report(
        contract,
        loop_lock,
        loop_status,
        aggregate_tail["rows"],
        direct_tail["rows"],
        stderr_size,
        previous_state,
        now_utc(),
    )
    report["sources"]["aggregate"]["journal_exists"] = aggregate_tail["exists"]
    report["sources"]["aggregate"]["trailing_bad_lines"] = aggregate_tail["trailing_bad_lines"]
    report["sources"]["direct"]["journal_exists"] = direct_tail["exists"]
    report["sources"]["direct"]["trailing_bad_lines"] = direct_tail["trailing_bad_lines"]
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    write_json(
        state_path,
        {
            "updated_at": report["generated_at"],
            "stderr_size_bytes": stderr_size,
            "aggregate_minute_bucket_ms": report["sources"]["aggregate"].get("latest_minute_bucket_ms"),
            "direct_minute_bucket_ms": report["sources"]["direct"].get("latest_minute_bucket_ms"),
            "last_decision": report["decision"],
            "can_trade": False,
        },
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fail-closed freshness watchdog for CEX funding collection")
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run(resolve_path(args.contract), resolve_path(args.out_prefix))
    print(json.dumps({"decision": report["decision"], "blockers": report["blockers"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

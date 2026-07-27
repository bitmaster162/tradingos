#!/usr/bin/env python3
from __future__ import annotations

import ctypes
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds").replace("+00:00", "Z")


def today_tag() -> str:
    return datetime.now().date().isoformat()


def resolve_path(value: str | Path, root: Path = ROOT) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def portable(path: Path, root: Path = ROOT) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc), "_path": str(path)}
    return payload if isinstance(payload, dict) else {"_read_error": "json_root_not_object", "_path": str(path)}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def report_ts(payload: dict[str, Any], fallback_path: Path) -> datetime:
    parsed = parse_ts(payload.get("generated_at") or payload.get("as_of") or payload.get("ts"))
    if parsed is not None:
        return parsed
    try:
        return datetime.fromtimestamp(fallback_path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return datetime.min.replace(tzinfo=timezone.utc)


def latest_json(
    patterns: str | Iterable[str],
    root: Path = ROOT,
    exclude: Iterable[Path] = (),
    exclude_name_tokens: Iterable[str] = (),
) -> Path | None:
    pattern_list = [patterns] if isinstance(patterns, str) else list(patterns)
    excluded = {path.resolve() for path in exclude}
    rejected_tokens = tuple(token.upper() for token in exclude_name_tokens)
    candidates: list[tuple[datetime, Path]] = []
    for pattern in pattern_list:
        for path in root.glob(pattern):
            if (
                not path.is_file()
                or path.resolve() in excluded
                or any(token in path.name.upper() for token in rejected_tokens)
            ):
                continue
            payload = read_json(path)
            candidates.append((report_ts(payload, path), path))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1].stat().st_mtime_ns, item[1].name))
    return candidates[-1][1]


def process_alive(pid: Any) -> bool:
    try:
        numeric_pid = int(pid)
    except (TypeError, ValueError):
        return False
    if numeric_pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(numeric_pid, 0)
        except OSError:
            return False
        return True
    process_query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, numeric_pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def boundary_false(payload: dict[str, Any]) -> bool:
    if payload.get("can_trade") is not False:
        return False
    for key in ("signals_allowed", "alerts_allowed", "paper_entries_allowed", "orders_allowed"):
        if payload.get(key) is True:
            return False
    boundary = payload.get("runtime_boundary")
    if isinstance(boundary, dict):
        for key in ("signals_allowed", "alerts_allowed", "paper_entries_allowed", "orders_allowed"):
            if boundary.get(key) is True:
                return False
    return True


def observe_loop(root: Path, loop_id: str, relative_path: str, role: str) -> dict[str, Any]:
    path = root / relative_path
    payload = read_json(path)
    ts = parse_ts(payload.get("ts"))
    age_seconds = round((now_utc() - ts).total_seconds(), 3) if ts is not None else None
    sleep_seconds = int(payload.get("sleep_seconds") or 0)
    freshness_limit_seconds = max(1800, sleep_seconds * 3 + 300)
    status = str(payload.get("status") or "missing")
    pid_alive = process_alive(payload.get("pid"))
    fresh = age_seconds is not None and 0 <= age_seconds <= freshness_limit_seconds
    stopped = status.lower().startswith("stopped")
    active = path.is_file() and not payload.get("_read_error") and pid_alive and fresh and not stopped
    safety_violation = any(
        payload.get(key) is True for key in ("signals_allowed", "alerts_allowed", "paper_entries_allowed", "orders_allowed", "can_trade")
    )
    return {
        "loop_id": loop_id,
        "role": role,
        "path": portable(path, root),
        "exists": path.is_file(),
        "status": status,
        "pid": payload.get("pid"),
        "pid_alive": pid_alive,
        "timestamp": payload.get("ts"),
        "age_seconds": age_seconds,
        "freshness_limit_seconds": freshness_limit_seconds,
        "fresh": fresh,
        "active": active,
        "safety_violation": safety_violation,
    }


LOOP_SPECS = (
    (
        "forward_scheduler",
        "logs/forward_paper_feed/forward_scheduler_loop_status.json",
        "core_observer",
    ),
    (
        "runtime_watchdog",
        "logs/forward_paper_feed/forward_runtime_watchdog_loop_status.json",
        "core_observer",
    ),
    (
        "crowd_fade_observer",
        "logs/forward_paper_feed/crowd_fade_observer_loop_status.json",
        "core_observer",
    ),
    (
        "cross_stack_replication",
        "logs/cross_stack_replication/cross_stack_replication_transition_loop_status.json",
        "external_observer",
    ),
    (
        "microstructure_book",
        "logs/cross_venue_microstructure/microstructure_book_loop_status.json",
        "research_collector",
    ),
    (
        "real_edge_observer",
        "logs/real_edge_observer/real_edge_observer_pulse_loop_status.json",
        "research_observer",
    ),
)


def observe_runtime_loops(root: Path = ROOT) -> list[dict[str, Any]]:
    return [observe_loop(root, loop_id, relative_path, role) for loop_id, relative_path, role in LOOP_SPECS]

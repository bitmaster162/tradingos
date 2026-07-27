from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


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


def canonical_nonoverlap_events(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return the deterministic one-position sample without mutating its raw journal."""
    ordered = sorted(rows, key=lambda row: (str(row.get("signal_ts") or ""), str(row.get("signal_key") or "")))
    accepted: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    last_exit: datetime | None = None
    for row in ordered:
        key = str(row.get("signal_key") or "")
        signal_at = parse_ts(row.get("signal_ts"))
        exit_at = parse_ts(row.get("exit_ts"))
        reason: str | None = None
        if not key or signal_at is None or exit_at is None or exit_at < signal_at:
            reason = "invalid_event_identity_or_timestamp"
        elif key in seen_keys:
            reason = "duplicate_signal_key"
        elif last_exit is not None and signal_at <= last_exit:
            reason = "overlaps_prior_open_trade"
        if reason is not None:
            excluded.append({**row, "sample_exclusion_reason": reason})
            continue
        accepted.append(row)
        seen_keys.add(key)
        last_exit = exit_at
    return accepted, excluded


def last_exit_index(bars: list[Any], last_exit_ts: Any) -> int:
    """Map persisted exit time back to a bar index so restarts cannot overlap trades."""
    last_exit_at = parse_ts(last_exit_ts)
    if last_exit_at is None:
        return -1
    result = -1
    for index, bar in enumerate(bars):
        bar_at = parse_ts(getattr(bar, "ts", None))
        if bar_at is not None and bar_at <= last_exit_at:
            result = index
    return result

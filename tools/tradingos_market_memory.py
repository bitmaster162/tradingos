#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import html
import json
import os
import sys
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tradingos_market_memory_state import (
    diff_states,
    extract_state,
    observed_at,
    parse_time,
    sha,
    source_identity,
    time_text,
    validate_persisted_state,
)

VERSION = "1.1.0"
RECORD_SCHEMA = "tradingos.market_memory.record.v1"
REPLAY_SCHEMA = "tradingos.market_replay.v1"
GENESIS = "GENESIS"
WINDOWS = (("1h", timedelta(hours=1)), ("4h", timedelta(hours=4)), ("24h", timedelta(hours=24)))
SAFETY = {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"}


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _validate_record_safety(value: Any, line_no: int) -> None:
    if not isinstance(value, dict) or value != SAFETY:
        raise ValueError(f"ledger line {line_no}: invalid safety")


def _validate_source_identity(identity: Any, line_no: int) -> None:
    if not isinstance(identity, dict):
        raise ValueError(f"ledger line {line_no}: source_identity must be an object")
    expected_fields = {"brief_id", "symbol", "timeframe", "as_of", "cockpit_fingerprint", "alert_fingerprint"}
    if set(identity) != expected_fields:
        raise ValueError(f"ledger line {line_no}: source_identity fields mismatch")
    for field in ("brief_id", "symbol", "timeframe", "as_of"):
        if not isinstance(identity.get(field), str) or not identity[field].strip():
            raise ValueError(f"ledger line {line_no}: invalid source_identity.{field}")
        if identity[field] != identity[field].strip():
            raise ValueError(f"ledger line {line_no}: non-normalized source_identity.{field}")
    parse_time(identity["as_of"])
    for field in ("cockpit_fingerprint",):
        value = identity.get(field)
        if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError(f"ledger line {line_no}: invalid source_identity.{field}")
    alert_fp = identity.get("alert_fingerprint")
    if alert_fp is not None and (
        not isinstance(alert_fp, str)
        or len(alert_fp) != 64
        or any(ch not in "0123456789abcdef" for ch in alert_fp)
    ):
        raise ValueError(f"ledger line {line_no}: invalid source_identity.alert_fingerprint")


def verify_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = path.read_bytes()
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        raise ValueError("ledger must end with a newline")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("ledger must be UTF-8") from exc

    records: list[dict[str, Any]] = []
    previous_hash = GENESIS
    previous_time = None
    stream_identity: tuple[str, str] | None = None
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            raise ValueError(f"ledger line {line_no}: blank record")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"ledger line {line_no}: invalid JSON") from exc
        if not isinstance(value, dict) or value.get("schema") != RECORD_SCHEMA or value.get("version") != VERSION:
            raise ValueError(f"ledger line {line_no}: invalid record")
        if value.get("sequence") != len(records) + 1:
            raise ValueError(f"ledger line {line_no}: non-contiguous sequence")
        if value.get("prev_record_hash") != previous_hash:
            raise ValueError(f"ledger line {line_no}: prev_record_hash mismatch")

        claimed = value.get("record_hash")
        body = dict(value)
        body.pop("record_hash", None)
        if not isinstance(claimed, str) or sha(body) != claimed:
            raise ValueError(f"ledger line {line_no}: record_hash mismatch")

        observed_text = value.get("observed_at")
        if not isinstance(observed_text, str):
            raise ValueError(f"ledger line {line_no}: invalid observed_at")
        observed = parse_time(observed_text)
        if previous_time is not None and observed <= previous_time:
            raise ValueError(f"ledger line {line_no}: observed_at is not strictly increasing")

        state = value.get("state")
        validate_persisted_state(state, f"ledger line {line_no} state")
        if value.get("state_fingerprint") != sha(state):
            raise ValueError(f"ledger line {line_no}: state_fingerprint mismatch")

        identity = value.get("source_identity")
        _validate_source_identity(identity, line_no)
        if identity["as_of"] != observed_text:
            raise ValueError(f"ledger line {line_no}: source identity timestamp mismatch")
        if value.get("source_identity_fingerprint") != sha(identity):
            raise ValueError(f"ledger line {line_no}: source_identity_fingerprint mismatch")
        cockpit_state = state["cockpit"]
        if identity["symbol"] != cockpit_state["symbol"] or identity["timeframe"] != cockpit_state["timeframe"]:
            raise ValueError(f"ledger line {line_no}: source identity/state stream mismatch")
        has_alert_state = "alert" in state
        has_alert_fingerprint = identity["alert_fingerprint"] is not None
        if has_alert_state != has_alert_fingerprint:
            raise ValueError(f"ledger line {line_no}: alert provenance/state presence mismatch")
        current_stream = (identity["symbol"], identity["timeframe"])
        if stream_identity is None:
            stream_identity = current_stream
        elif current_stream != stream_identity:
            raise ValueError(f"ledger line {line_no}: ledger stream identity mismatch")

        change = value.get("change_from_previous")
        if not isinstance(change, dict):
            raise ValueError(f"ledger line {line_no}: invalid change_from_previous")
        expected_change = (
            {"material_change": False, "change_count": 0, "changes": [], "summary": "BASELINE_ESTABLISHED"}
            if not records
            else diff_states(records[-1]["state"], state)
        )
        if change != expected_change:
            raise ValueError(f"ledger line {line_no}: change_from_previous mismatch")
        _validate_record_safety(value.get("safety"), line_no)

        records.append(value)
        previous_hash = claimed
        previous_time = observed
    return records


@contextmanager
def _writer_lock(ledger: Path) -> Iterator[None]:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ledger.with_name(ledger.name + ".lock")
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _append_line_durable(path: Path, line: bytes) -> None:
    existed = path.exists()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    start_size = os.lseek(fd, 0, os.SEEK_END)
    try:
        offset = 0
        while offset < len(line):
            written = os.write(fd, line[offset:])
            if written <= 0:
                raise OSError(f"short ledger write at {offset}/{len(line)} bytes")
            offset += written
        os.fsync(fd)
    except Exception:
        try:
            os.ftruncate(fd, start_size)
            os.fsync(fd)
        except OSError:
            pass
        raise
    finally:
        os.close(fd)
    if not existed:
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def append_observation(
    ledger: Path,
    cockpit: dict[str, Any],
    alert: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    when = observed_at(cockpit, alert)
    when_dt = parse_time(when)
    state = extract_state(cockpit, alert)
    state_fingerprint = sha(state)
    identity, identity_fingerprint = source_identity(cockpit, alert)

    with _writer_lock(ledger):
        records = verify_ledger(ledger)
        if records:
            last = records[-1]
            last_identity = last["source_identity"]
            if last_identity["symbol"] != identity["symbol"] or last_identity["timeframe"] != identity["timeframe"]:
                raise ValueError("ledger stream identity mismatch: symbol/timeframe")
            last_dt = parse_time(last["observed_at"])
            if when_dt < last_dt:
                raise ValueError("non-monotonic observation; historical backfill is disabled")
            if when_dt == last_dt:
                if (
                    last.get("state_fingerprint") == state_fingerprint
                    and last.get("source_identity_fingerprint") == identity_fingerprint
                ):
                    return "DUPLICATE_SUPPRESSED", last, records
                raise ValueError("same observed_at with conflicting observation")
            change = diff_states(last["state"], state)
            prev_hash = last["record_hash"]
        else:
            change = {
                "material_change": False,
                "change_count": 0,
                "changes": [],
                "summary": "BASELINE_ESTABLISHED",
            }
            prev_hash = GENESIS

        body = {
            "schema": RECORD_SCHEMA,
            "version": VERSION,
            "sequence": len(records) + 1,
            "observed_at": when,
            "prev_record_hash": prev_hash,
            "state_fingerprint": state_fingerprint,
            "source_identity_fingerprint": identity_fingerprint,
            "source_identity": identity,
            "change_from_previous": change,
            "state": state,
            "safety": dict(SAFETY),
        }
        record = dict(body)
        record["record_hash"] = sha(body)
        line = (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        _append_line_durable(ledger, line)
        records.append(record)
        return "APPENDED", record, records


def build_replay(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        raise ValueError("cannot replay an empty ledger")
    current = records[-1]
    now = parse_time(current["observed_at"])
    windows: dict[str, Any] = {}
    for label, delta in WINDOWS:
        cutoff = now - delta
        candidates = [r for r in records[:-1] if parse_time(r["observed_at"]) <= cutoff]
        if not candidates:
            windows[label] = {
                "status": "INSUFFICIENT_HISTORY",
                "requested_cutoff": time_text(cutoff),
                "baseline_observed_at": None,
                "actual_span_hours": None,
                "delta": None,
            }
            continue
        base = candidates[-1]
        span = (now - parse_time(base["observed_at"])).total_seconds() / 3600.0
        windows[label] = {
            "status": "COMPARABLE",
            "requested_cutoff": time_text(cutoff),
            "baseline_sequence": base["sequence"],
            "baseline_observed_at": base["observed_at"],
            "actual_span_hours": round(span, 4),
            "delta": diff_states(base["state"], current["state"]),
        }
    return {
        "schema": REPLAY_SCHEMA,
        "version": VERSION,
        "current_sequence": current["sequence"],
        "current_observed_at": current["observed_at"],
        "current_record_hash": current["record_hash"],
        "ledger_records": len(records),
        "latest_change": current["change_from_previous"],
        "windows": windows,
        "contract": {
            "append_only": True,
            "tamper_evident_hash_chain": True,
            "historical_backfill_disabled": True,
            "exclusive_writer_lock": True,
            "fsync_before_success": True,
            "window_baseline": "nearest real observation at or before requested cutoff",
            "insufficient_history_is_not_fabricated": True,
        },
        "safety": dict(SAFETY),
    }


def render_html(replay: dict[str, Any]) -> str:
    cards: list[str] = []
    for label in ("1h", "4h", "24h"):
        row = replay["windows"][label]
        if row["status"] == "COMPARABLE":
            body = (
                f'<b>{html.escape(row["delta"]["summary"])}</b>'
                f'<span>{row["delta"]["change_count"]} changes · span {row["actual_span_hours"]}h</span>'
            )
        else:
            body = '<b>INSUFFICIENT_HISTORY</b><span>No historical state is fabricated.</span>'
        cards.append(f"<article><small>{label} REPLAY</small>{body}</article>")
    latest = replay["latest_change"]
    rows = "".join(
        f'<li><b>{html.escape(str(x.get("scope")))}</b> · {html.escape(str(x.get("field")))}</li>'
        for x in latest.get("changes", [])[:12]
    ) or "<li>No material change from previous observation.</li>"
    css = "*{box-sizing:border-box}body{margin:0;background:#071019;color:#f4f8fb;font:14px system-ui}main{max-width:1080px;margin:auto;padding:28px}.k{font-size:11px;letter-spacing:.16em;color:#6fdbff;font-weight:800}h1{font-size:48px;letter-spacing:-2px;margin:5px 0}.sub,small,span{color:#8fa5b7}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:20px 0}article,.panel{background:#0d1823;border:1px solid #263746;border-radius:16px;padding:18px}article b,article span{display:block;margin-top:8px}ul{line-height:1.8;padding-left:20px}@media(max-width:700px){.grid{grid-template-columns:1fr}h1{font-size:38px}}"
    return (
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>TradingOS Market Replay</title><style>{css}</style></head><body><main>"
        '<div class="k">TRADINGOS · MARKET MEMORY</div><h1>Change Replay</h1>'
        f'<div class="sub">Observation #{replay["current_sequence"]} · {html.escape(replay["current_observed_at"])} · hash-chain verified</div>'
        f'<div class="grid">{"".join(cards)}</div><section class="panel"><small>LATEST TRANSITION</small>'
        f'<h2>{html.escape(latest["summary"])}</h2><ul>{rows}</ul></section>'
        '<p class="sub">Append-only · locked writer · fsync · no historical backfill · signals=false · orders=false · can_trade=false · capital_permission=DENY</p>'
        "</main></body></html>"
    )


def generate(
    ledger: Path,
    out_dir: Path,
    cockpit_path: Path,
    alert_path: Path | None = None,
) -> tuple[str, dict[str, Path], dict[str, Any]]:
    cockpit = _read(cockpit_path)
    alert = _read(alert_path) if alert_path else None
    status, record, records = append_observation(ledger, cockpit, alert)
    replay = build_replay(records)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "record": out_dir / "latest_record.json",
        "replay": out_dir / "market_replay.json",
        "html": out_dir / "market_replay.html",
    }
    paths["record"].write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    paths["replay"].write_text(json.dumps(replay, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    paths["html"].write_text(render_html(replay), encoding="utf-8", newline="\n")
    return status, paths, replay


def main() -> int:
    parser = argparse.ArgumentParser(description="Append canonical product state to tamper-evident TradingOS market memory")
    parser.add_argument("--cockpit", type=Path, required=True)
    parser.add_argument("--alert", type=Path)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        status, paths, replay = generate(
            args.ledger.resolve(),
            args.out_dir.resolve(),
            args.cockpit.resolve(),
            args.alert.resolve() if args.alert else None,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "result": "PASS",
                "append_status": status,
                "sequence": replay["current_sequence"],
                "windows": {k: v["status"] for k, v in replay["windows"].items()},
                "outputs": {k: str(v) for k, v in paths.items()},
                "can_trade": False,
                "capital_permission": "DENY",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

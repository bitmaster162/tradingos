#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = "tools/bitunix_public_kline_timing_probe.py"
PUBLIC_WS_URL = "wss://fapi.bitunix.com/public/"
OFFICIAL_DOC_URL = "https://www.bitunix.com/api-docs/futures/websocket/public/kline%20channel.html"
CHANNEL_INTERVALS_MS = {"market_kline_1min": 60_000, "market_kline_5min": 300_000}
PRICE_FIELDS = ("o", "h", "l", "c", "b", "q")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_kline_frame(
    message: Any,
    *,
    recv_ns: int,
    expected_symbol: str,
    expected_channel: str,
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(message, dict):
        return None, "frame_not_object"
    if message.get("ch") != expected_channel:
        return None, None
    if message.get("symbol") != expected_symbol:
        return None, "symbol_mismatch"
    interval_ms = CHANNEL_INTERVALS_MS.get(expected_channel)
    if interval_ms is None:
        return None, "unsupported_channel"
    server_ts_ms = integer(message.get("ts"))
    data = message.get("data")
    if server_ts_ms is None or not isinstance(data, dict):
        return None, "kline_shape_invalid"
    values: dict[str, str] = {}
    for field in PRICE_FIELDS:
        value = data.get(field)
        try:
            Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None, f"kline_field_invalid:{field}"
        values[field] = str(value)
    recv_ms = recv_ns // 1_000_000
    bucket_start_ms = server_ts_ms - (server_ts_ms % interval_ms)
    return (
        {
            "schema": "bitunix-public-kline-receipt-v1",
            "recv_ns": recv_ns,
            "recv_ms": recv_ms,
            "server_ts_ms": server_ts_ms,
            "recv_minus_server_ms": recv_ms - server_ts_ms,
            "bucket_start_ms": bucket_start_ms,
            "interval_ms": interval_ms,
            "symbol": expected_symbol,
            "channel": expected_channel,
            "payload": values,
        },
        None,
    )


def analyze_records(records: list[dict[str, Any]], *, latency_cutoff_ms: int) -> dict[str, Any]:
    buckets: dict[int, list[dict[str, Any]]] = {}
    for row in records:
        bucket = integer(row.get("bucket_start_ms"))
        if bucket is not None:
            buckets.setdefault(bucket, []).append(row)
    ordered = sorted(buckets)
    transitions: list[dict[str, Any]] = []
    for previous_bucket, current_bucket in zip(ordered, ordered[1:]):
        interval_ms = integer(buckets[current_bucket][0].get("interval_ms")) or 0
        if current_bucket - previous_bucket != interval_ms:
            continue
        previous_rows = sorted(buckets[previous_bucket], key=lambda row: int(row["recv_ns"]))
        current_rows = sorted(buckets[current_bucket], key=lambda row: int(row["recv_ns"]))
        first_current = current_rows[0]
        last_previous = previous_rows[-1]
        confirmation_latency_ms = int(first_current["recv_ms"]) - current_bucket
        transitions.append(
            {
                "closed_bucket_start_ms": previous_bucket,
                "boundary_ms": current_bucket,
                "last_previous_snapshot_recv_ms": int(last_previous["recv_ms"]),
                "first_new_bucket_recv_ms": int(first_current["recv_ms"]),
                "close_confirmation_latency_ms": confirmation_latency_ms,
                "within_cutoff": 0 <= confirmation_latency_ms <= latency_cutoff_ms,
                "final_value_verified_against_later_rest": False,
            }
        )
    passing = sum(1 for item in transitions if item["within_cutoff"])
    return {
        "records": len(records),
        "buckets": len(ordered),
        "transitions": transitions,
        "transition_count": len(transitions),
        "transitions_within_cutoff": passing,
        "all_observed_transitions_within_cutoff": bool(transitions) and passing == len(transitions),
        "latency_cutoff_ms": latency_cutoff_ms,
        "proof_limit": "Rollover timing only; final OHLCV equality still requires later independent verification.",
    }


async def capture(
    *,
    symbol: str,
    channel: str,
    seconds: float,
    raw_path: Path,
) -> tuple[list[dict[str, Any]], list[str], int]:
    try:
        import websockets  # type: ignore
    except ImportError as exc:
        raise RuntimeError("missing_dependency:websockets") from exc

    records: list[dict[str, Any]] = []
    failures: list[str] = []
    ignored_frames = 0
    deadline = time.monotonic() + seconds
    subscription = {"op": "subscribe", "args": [{"symbol": symbol, "ch": channel}]}
    with raw_path.open("x", encoding="utf-8", newline="\n") as raw:
        try:
            async with websockets.connect(PUBLIC_WS_URL, ping_interval=None, max_size=2**20) as ws:
                await ws.send(json.dumps(subscription, separators=(",", ":")))
                last_ping = time.monotonic()
                while time.monotonic() < deadline:
                    if time.monotonic() - last_ping >= 20:
                        await ws.send(json.dumps({"op": "ping", "ping": int(time.time())}))
                        last_ping = time.monotonic()
                    timeout = min(20.0, max(0.1, deadline - time.monotonic()))
                    try:
                        frame = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        continue
                    recv_ns = time.time_ns()
                    if isinstance(frame, bytes):
                        frame = frame.decode("utf-8", "replace")
                    try:
                        message = json.loads(frame)
                    except json.JSONDecodeError:
                        failures.append("json_decode_failure")
                        continue
                    parsed, error = parse_kline_frame(
                        message,
                        recv_ns=recv_ns,
                        expected_symbol=symbol,
                        expected_channel=channel,
                    )
                    if error:
                        failures.append(error)
                    if parsed is None:
                        ignored_frames += 1
                        continue
                    raw.write(json.dumps(parsed, ensure_ascii=False, separators=(",", ":")) + "\n")
                    raw.flush()
                    records.append(parsed)
        except Exception as exc:
            failures.append(f"network_or_local:{type(exc).__name__}:{exc}")
        raw.flush()
        os.fsync(raw.fileno())
    return records, sorted(set(failures)), ignored_frames


def main() -> int:
    parser = argparse.ArgumentParser(description="Public-only Bitunix kline rollover timing probe")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--channel", choices=sorted(CHANNEL_INTERVALS_MS), default="market_kline_5min")
    parser.add_argument("--seconds", type=float, default=370.0)
    parser.add_argument("--latency-cutoff-ms", type=int, default=5000)
    parser.add_argument("--out-root", default="data/forward/bitunix_kline_timing")
    args = parser.parse_args()
    if args.seconds <= 0 or args.seconds > 900:
        parser.error("--seconds must be in (0, 900]")
    if args.latency_cutoff_ms < 0:
        parser.error("--latency-cutoff-ms must be non-negative")

    out_root = Path(args.out_root)
    if not out_root.is_absolute():
        out_root = ROOT / out_root
    run_dir = out_root / datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%S_%fZ")
    run_dir.mkdir(parents=True, exist_ok=False)
    raw_path = run_dir / "KLINE_RECEIPTS.jsonl"
    started_at = now_iso()
    records, failures, ignored_frames = asyncio.run(
        capture(symbol=args.symbol, channel=args.channel, seconds=args.seconds, raw_path=raw_path)
    )
    analysis = analyze_records(records, latency_cutoff_ms=args.latency_cutoff_ms)
    if not records:
        failures.append("no_kline_records")
    decision = "bitunix_kline_timing_probe_observed"
    if failures:
        decision = "bitunix_kline_timing_probe_capture_failed"
    elif not analysis["transition_count"]:
        decision = "bitunix_kline_timing_probe_waiting_for_boundary"
    elif not analysis["all_observed_transitions_within_cutoff"]:
        decision = "bitunix_kline_timing_probe_cutoff_failed"
    report = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "started_at": started_at,
        "tool": TOOL_PATH,
        "decision": decision,
        "official_doc": OFFICIAL_DOC_URL,
        "run_dir": str(run_dir),
        "symbol": args.symbol,
        "channel": args.channel,
        "requested_seconds": args.seconds,
        "ignored_frames": ignored_frames,
        "failures": sorted(set(failures)),
        "analysis": analysis,
        "evidence": {
            "raw_receipts": str(raw_path),
            "raw_receipts_sha256": sha256_file(raw_path),
            "append_only": True,
        },
        "runtime_boundary": {
            "public_data_only": True,
            "credentials_allowed": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    atomic_json(run_dir / "KLINE_TIMING_REPORT.json", report)
    atomic_json(out_root / "LATEST_KLINE_TIMING_REPORT.json", report)
    print(
        json.dumps(
            {
                "decision": decision,
                "records": analysis["records"],
                "transitions": analysis["transition_count"],
                "failures": report["failures"],
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())

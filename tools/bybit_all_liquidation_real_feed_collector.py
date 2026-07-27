#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import websockets


ROOT = Path(__file__).resolve().parents[1]
BYBIT_LINEAR_PUBLIC_WS = "wss://stream.bybit.com/v5/public/linear"
BYBIT_PUBLIC_TIME_URL = "https://api.bybit.com/v5/market/time"
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
    "received_at_ns",
    "received_at",
    "received_monotonic_ns",
    "corrected_received_at_ns",
    "corrected_received_at",
    "collector_session_id",
    "packet_sequence",
    "collector_host",
    "collector_pid",
    "collector_clock_source",
    "clock_calibration_id",
    "clock_calibrated_at_ns",
    "clock_calibration_age_ns",
    "clock_offset_ns",
    "clock_rtt_ns",
    "clock_uncertainty_ns",
    "clock_calibration_samples",
    "clock_calibration_source",
    "ingest_schema_version",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_server_time_ns(payload: dict[str, Any]) -> int:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    value = result.get("timeNano")
    if value not in (None, ""):
        return int(value)
    fallback_ms = payload.get("time")
    if fallback_ms not in (None, ""):
        return int(fallback_ms) * 1_000_000
    raise ValueError("Bybit public time response has no server timestamp")


def fetch_bybit_clock_sample(
    *,
    url: str = BYBIT_PUBLIC_TIME_URL,
    timeout_s: float = 5.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, int]:
    local_start_ns = time.time_ns()
    monotonic_start_ns = time.perf_counter_ns()
    request = urllib.request.Request(url, headers={"User-Agent": "TradingOS-public-clock-calibration/1"})
    with opener(request, timeout=timeout_s) as response:
        payload = json.loads(response.read().decode("utf-8"))
    monotonic_end_ns = time.perf_counter_ns()
    local_end_ns = time.time_ns()
    if int(payload.get("retCode") or 0) != 0:
        raise ValueError(f"Bybit public time returned retCode={payload.get('retCode')}")
    return {
        "local_start_ns": local_start_ns,
        "local_end_ns": local_end_ns,
        "monotonic_elapsed_ns": monotonic_end_ns - monotonic_start_ns,
        "server_time_ns": parse_server_time_ns(payload),
    }


def select_clock_calibration(
    samples: list[dict[str, int]],
    *,
    calibration_id: str | None = None,
    source: str = BYBIT_PUBLIC_TIME_URL,
) -> dict[str, Any]:
    if not samples:
        raise ValueError("at least one clock sample is required")
    selected = min(samples, key=lambda item: int(item["monotonic_elapsed_ns"]))
    local_midpoint_ns = (int(selected["local_start_ns"]) + int(selected["local_end_ns"])) // 2
    rtt_ns = int(selected["monotonic_elapsed_ns"])
    return {
        "clock_calibration_id": calibration_id or uuid.uuid4().hex,
        "clock_calibrated_at_ns": int(selected["local_end_ns"]),
        "clock_offset_ns": int(selected["server_time_ns"]) - local_midpoint_ns,
        "clock_rtt_ns": rtt_ns,
        "clock_uncertainty_ns": max(1, rtt_ns // 2),
        "clock_calibration_samples": len(samples),
        "clock_calibration_source": source,
        "clock_server_time_ns": int(selected["server_time_ns"]),
        "clock_local_midpoint_ns": local_midpoint_ns,
    }


def calibrate_bybit_clock(*, samples: int = 3, timeout_s: float = 5.0) -> dict[str, Any]:
    if samples < 1:
        raise ValueError("clock calibration samples must be positive")
    observations = [fetch_bybit_clock_sample(timeout_s=timeout_s) for _ in range(samples)]
    return select_clock_calibration(observations)


def synthetic_clock_calibration(reference_ns: int | None = None) -> dict[str, Any]:
    timestamp_ns = int(reference_ns if reference_ns is not None else time.time_ns())
    return {
        "clock_calibration_id": "synthetic-dry-run",
        "clock_calibrated_at_ns": timestamp_ns,
        "clock_offset_ns": 0,
        "clock_rtt_ns": 0,
        "clock_uncertainty_ns": 0,
        "clock_calibration_samples": 1,
        "clock_calibration_source": "synthetic_dry_run_only",
        "clock_server_time_ns": timestamp_ns,
        "clock_local_midpoint_ns": timestamp_ns,
    }


def capture_reception(
    received_at_ns: int | None = None,
    *,
    received_monotonic_ns: int | None = None,
    calibration: dict[str, Any] | None = None,
    collector_session_id: str = "test-session",
    packet_sequence: int = 0,
) -> dict[str, Any]:
    timestamp_ns = int(received_at_ns if received_at_ns is not None else time.time_ns())
    monotonic_ns = int(received_monotonic_ns if received_monotonic_ns is not None else time.perf_counter_ns())
    clock = calibration or synthetic_clock_calibration(timestamp_ns)
    corrected_ns = timestamp_ns + int(clock["clock_offset_ns"])
    received_at = datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=timezone.utc)
    corrected_at = datetime.fromtimestamp(corrected_ns / 1_000_000_000, tz=timezone.utc)
    return {
        "received_at_ns": timestamp_ns,
        "received_at": received_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "received_monotonic_ns": monotonic_ns,
        "corrected_received_at_ns": corrected_ns,
        "corrected_received_at": corrected_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "collector_session_id": collector_session_id,
        "packet_sequence": int(packet_sequence),
        "collector_host": platform.node() or "unknown",
        "collector_pid": os.getpid(),
        "collector_clock_source": "time.time_ns+time.perf_counter_ns+bybit_server_midpoint",
        "clock_calibration_id": str(clock["clock_calibration_id"]),
        "clock_calibrated_at_ns": int(clock["clock_calibrated_at_ns"]),
        "clock_calibration_age_ns": timestamp_ns - int(clock["clock_calibrated_at_ns"]),
        "clock_offset_ns": int(clock["clock_offset_ns"]),
        "clock_rtt_ns": int(clock["clock_rtt_ns"]),
        "clock_uncertainty_ns": int(clock["clock_uncertainty_ns"]),
        "clock_calibration_samples": int(clock["clock_calibration_samples"]),
        "clock_calibration_source": str(clock["clock_calibration_source"]),
        "ingest_schema_version": 3,
    }


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def parse_symbols(value: str) -> list[str]:
    symbols = [item.strip().upper() for item in value.split(",") if item.strip()]
    if not symbols or "ALL" in symbols or "*" in symbols:
        raise ValueError("Bybit allLiquidation requires explicit symbols; ALL is not supported by the topic contract")
    return sorted(set(symbols))


def parse_bybit_message(raw: dict[str, Any], reception: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    topic = str(raw.get("topic") or "")
    if not topic.startswith("allLiquidation."):
        return []
    event_time_ms = int(raw.get("ts") or 0)
    rows: list[dict[str, Any]] = []
    data = raw.get("data") or []
    if isinstance(data, dict):
        items = [data]
    elif isinstance(data, list):
        items = [item for item in data if isinstance(item, dict)]
    else:
        items = []
    packet_reception = reception or capture_reception()
    for item in items:
        symbol = str(item.get("s") or "").upper()
        if not symbol:
            continue
        liquidation_time_ms = int(item.get("T") or event_time_ms)
        price = parse_float(item.get("p"))
        quantity = parse_float(item.get("v"))
        row = {
            "event_time_ms": event_time_ms or liquidation_time_ms,
            "event_time": ms_to_iso(event_time_ms or liquidation_time_ms),
            "liquidation_time_ms": liquidation_time_ms,
            "liquidation_time": ms_to_iso(liquidation_time_ms),
            "symbol": symbol,
            "side": str(item.get("S") or "").upper(),
            "price": round(price, 12),
            "quantity": round(quantity, 12),
            "notional_usd": round(price * quantity, 8),
            "venue": "bybit",
            "source": SOURCE,
            "is_real_liquidation_feed": True,
            "raw": raw,
        }
        row.update(packet_reception)
        missing = sorted(field for field in REQUIRED_FIELDS if field not in row or row[field] in ("", None))
        if missing:
            raise ValueError(f"Bybit liquidation row missing required fields: {missing}")
        rows.append(row)
    return rows


def daily_path(out_dir: Path, row: dict[str, Any]) -> Path:
    day = datetime.fromtimestamp(int(row["liquidation_time_ms"]) / 1000.0, tz=timezone.utc).strftime("%Y%m%d")
    return out_dir / row["symbol"].upper() / f"{day}.jsonl"


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_heartbeat(path: Path, stats: dict[str, Any], status: str, extra: dict[str, Any] | None = None) -> None:
    payload = {
        "ts": now_iso(),
        "status": status,
        "tool": "tools/bybit_all_liquidation_real_feed_collector.py",
        "can_trade": False,
        "data_collector_only": True,
        "venue": "bybit",
        "symbols": stats.get("symbols", []),
        "topics": stats.get("topics", []),
        "url": stats.get("url"),
        "out_dir": stats.get("out_dir"),
        "events_written": stats.get("events_written", 0),
        "messages_seen": stats.get("messages_seen", 0),
        "last_message_at": stats.get("last_message_at"),
        "last_event_at": stats.get("last_event_at"),
        "parse_errors_count": len(stats.get("parse_errors") or []),
        "clock_calibration_ready": stats.get("clock_calibration_ready", False),
        "collector_session_id": stats.get("collector_session_id"),
    }
    if extra:
        payload["extra"] = extra
    write_json(path, payload)


def sample_message() -> dict[str, Any]:
    return {
        "topic": "allLiquidation.BTCUSDT",
        "type": "snapshot",
        "ts": 1739502303204,
        "data": [
            {
                "T": 1739502302929,
                "s": "BTCUSDT",
                "S": "Sell",
                "v": "0.25",
                "p": "65000.5",
            }
        ],
    }


async def collect(args: argparse.Namespace) -> dict[str, Any]:
    symbols = parse_symbols(args.symbols)
    topics = [f"allLiquidation.{symbol}" for symbol in symbols]
    out_dir = resolve_path(args.out_dir)
    heartbeat_path = resolve_path(args.heartbeat_path)
    stats: dict[str, Any] = {
        "started_at": now_iso(),
        "ended_at": None,
        "url": BYBIT_LINEAR_PUBLIC_WS,
        "symbols": symbols,
        "topics": topics,
        "out_dir": portable_path(out_dir),
        "events_written": 0,
        "messages_seen": 0,
        "last_message_at": None,
        "last_event_at": None,
        "parse_errors": [],
        "clock_calibration_errors": [],
        "clock_calibration_ready": False,
        "clock_calibrations": [],
        "collector_session_id": uuid.uuid4().hex,
        "mode": "dry_run" if args.dry_run else "live_websocket",
    }
    write_heartbeat(heartbeat_path, stats, "starting")
    try:
        calibration = (
            synthetic_clock_calibration()
            if args.dry_run
            else await asyncio.to_thread(
                calibrate_bybit_clock,
                samples=args.clock_samples,
                timeout_s=args.clock_timeout_s,
            )
        )
    except Exception as exc:
        stats["clock_calibration_errors"].append(f"{type(exc).__name__}:{exc}")
        stats["ended_at"] = now_iso()
        write_heartbeat(heartbeat_path, stats, "blocked_clock_calibration")
        return stats
    stats["clock_calibration_ready"] = True
    stats["clock_calibrations"].append(calibration)
    if args.dry_run:
        rows = parse_bybit_message(
            sample_message(),
            capture_reception(
                calibration=calibration,
                collector_session_id=stats["collector_session_id"],
                packet_sequence=1,
            ),
        )
        assert rows
        stats["messages_seen"] = 1
        stats["last_message_at"] = now_iso()
        stats["sample_rows"] = rows
        stats["dry_run_parser_ok"] = True
        stats["ended_at"] = now_iso()
        write_heartbeat(heartbeat_path, stats, "dry_run_parser_ok")
        return stats

    deadline = asyncio.get_running_loop().time() + args.max_seconds
    packet_sequence = 0
    async with websockets.connect(BYBIT_LINEAR_PUBLIC_WS, ping_interval=20, ping_timeout=20, close_timeout=5) as websocket:
        subscribe = {"op": "subscribe", "args": topics}
        await websocket.send(json.dumps(subscribe, separators=(",", ":")))
        write_heartbeat(heartbeat_path, stats, "connected_subscribed", extra={"topics": topics})
        while stats["events_written"] < args.max_events and asyncio.get_running_loop().time() < deadline:
            timeout = max(0.1, min(5.0, deadline - asyncio.get_running_loop().time()))
            try:
                message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                write_heartbeat(heartbeat_path, stats, "connected_waiting_events")
                continue
            packet_sequence += 1
            if time.time_ns() - int(calibration["clock_calibrated_at_ns"]) > int(args.clock_refresh_seconds * 1_000_000_000):
                try:
                    calibration = await asyncio.to_thread(
                        calibrate_bybit_clock,
                        samples=args.clock_samples,
                        timeout_s=args.clock_timeout_s,
                    )
                    stats["clock_calibrations"].append(calibration)
                except Exception as exc:
                    stats["clock_calibration_ready"] = False
                    stats["clock_calibration_errors"].append(f"{type(exc).__name__}:{exc}")
                    write_heartbeat(heartbeat_path, stats, "blocked_clock_recalibration")
                    break
            reception = capture_reception(
                calibration=calibration,
                collector_session_id=stats["collector_session_id"],
                packet_sequence=packet_sequence,
            )
            stats["messages_seen"] += 1
            stats["last_message_at"] = now_iso()
            try:
                payload = json.loads(message)
                rows = parse_bybit_message(payload, reception)
            except Exception as exc:
                stats["parse_errors"].append(repr(exc))
                write_heartbeat(heartbeat_path, stats, "parse_error")
                continue
            if not rows:
                write_heartbeat(heartbeat_path, stats, "non_liquidation_message_seen")
                continue
            for row in rows:
                append_jsonl(daily_path(out_dir, row), row)
                stats["events_written"] += 1
                stats["last_event_at"] = row.get("liquidation_time")
                if stats["events_written"] >= args.max_events:
                    break
            write_heartbeat(heartbeat_path, stats, "event_written", extra={"rows": len(rows), "last_event_at": stats.get("last_event_at")})
    stats["ended_at"] = now_iso()
    write_heartbeat(heartbeat_path, stats, "cycle_finished")
    return stats


def render_markdown(report: dict[str, Any]) -> str:
    stats = report["stats"]
    return "\n".join(
        [
            "# Bybit All Liquidation Real-Feed Collector Run",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            f"- Can trade: `false`",
            f"- Mode: `{stats['mode']}`",
            f"- Symbols: `{', '.join(stats['symbols'])}`",
            f"- Topics: `{', '.join(stats['topics'])}`",
            f"- Events written: `{stats['events_written']}`",
            f"- Messages seen: `{stats['messages_seen']}`",
            f"- Output dir: `{stats['out_dir']}`",
            "",
            "## Notes",
            "",
            "- Public Bybit V5 `allLiquidation.{symbol}` websocket collector.",
            "- Data collector only: no private credentials, no alerts, no paper entries, no orders.",
            "- Bybit `S=Buy` means a long position was liquidated; `S=Sell` means a short position was liquidated.",
        ]
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Forward collector for Bybit V5 public allLiquidation events")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,LINKUSDT,LTCUSDT")
    parser.add_argument("--out-dir", default="data/live/liquidations/bybit_all_liquidation")
    parser.add_argument("--max-events", type=int, default=20)
    parser.add_argument("--max-seconds", type=float, default=60.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--clock-samples", type=int, default=3)
    parser.add_argument("--clock-timeout-s", type=float, default=5.0)
    parser.add_argument("--clock-refresh-seconds", type=float, default=240.0)
    parser.add_argument("--heartbeat-path", default="logs/liquidation_bybit/bybit_all_liquidation_collector_heartbeat.json")
    parser.add_argument("--out-prefix", default="docs/BYBIT_ALL_LIQUIDATION_FORWARD_COLLECTOR_2026-07-01")
    args = parser.parse_args()

    stats = asyncio.run(collect(args))
    decision = (
        "bybit_all_liquidation_collector_blocked_clock_calibration"
        if not stats["clock_calibration_ready"]
        else
        "bybit_all_liquidation_collector_wrote_events"
        if stats["events_written"] > 0
        else "bybit_all_liquidation_collector_dry_run_parser_ok"
        if stats["mode"] == "dry_run"
        else "bybit_all_liquidation_collector_connected_no_events_observed"
    )
    report = {
        "generated_at": now_iso(),
        "tool": "tools/bybit_all_liquidation_real_feed_collector.py",
        "decision": decision,
        "can_trade": False,
        "docs": {
            "bybit_all_liquidation": "https://bybit-exchange.github.io/docs/v5/websocket/public/all-liquidation",
        },
        "stats": stats,
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": decision, "out": portable_path(out.with_suffix(".json")), "can_trade": False}, ensure_ascii=False))
    return 2 if not stats["clock_calibration_ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

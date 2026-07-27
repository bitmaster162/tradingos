#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import websockets


ROOT = Path(__file__).resolve().parents[1]
BINANCE_USDM_COMBINED_STREAM = "wss://fstream.binance.com/market/stream?streams={streams}"
ALL_MARKET_FORCE_ORDER_STREAM = "!forceOrder@arr"
DEFAULT_LIVENESS_STREAM = "btcusdt@markPrice@1s"
REQUIRED_FIELDS = {
    "event_time_ms",
    "event_time",
    "trade_time_ms",
    "symbol",
    "side",
    "price",
    "quantity",
    "notional_usd",
    "source",
    "is_real_liquidation_feed",
    "received_at_ns",
    "received_at",
    "collector_host",
    "collector_pid",
    "ingest_schema_version",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def capture_reception(received_at_ns: int | None = None) -> dict[str, Any]:
    timestamp_ns = int(received_at_ns if received_at_ns is not None else time.time_ns())
    received_at = datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=timezone.utc)
    return {
        "received_at_ns": timestamp_ns,
        "received_at": received_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "collector_host": platform.node() or "unknown",
        "collector_pid": os.getpid(),
        "collector_clock_source": "time.time_ns",
        "ingest_schema_version": 2,
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


def parse_symbol_filter(symbols: str) -> tuple[set[str], bool]:
    values = {item.strip().upper() for item in symbols.split(",") if item.strip()}
    return values, not values or "ALL" in values or "*" in values


def force_order_payloads(raw: Any) -> list[dict[str, Any]]:
    payload = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def parse_force_order_payload(
    payload: dict[str, Any],
    symbol_filter: set[str],
    accept_all: bool,
    reception: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if payload.get("e") != "forceOrder":
        return None
    order = payload.get("o") or {}
    symbol = str(order.get("s") or "").upper()
    if not symbol:
        return None
    if not accept_all and symbol not in symbol_filter:
        return None
    event_time_ms = int(payload.get("E") or order.get("T") or 0)
    trade_time_ms = int(order.get("T") or event_time_ms)
    price = parse_float(order.get("ap")) or parse_float(order.get("p"))
    quantity = parse_float(order.get("z")) or parse_float(order.get("l")) or parse_float(order.get("q"))
    row = {
        "event_time_ms": event_time_ms,
        "event_time": ms_to_iso(event_time_ms),
        "trade_time_ms": trade_time_ms,
        "trade_time": ms_to_iso(trade_time_ms),
        "symbol": symbol,
        "side": str(order.get("S") or "").upper(),
        "order_type": order.get("o"),
        "time_in_force": order.get("f"),
        "status": order.get("X"),
        "price": round(price, 12),
        "quantity": round(quantity, 12),
        "notional_usd": round(price * quantity, 8),
        "source": "binance_usdm_forceOrder_websocket",
        "is_real_liquidation_feed": True,
        "raw": payload,
    }
    row.update(reception or capture_reception())
    missing = sorted(field for field in REQUIRED_FIELDS if field not in row or row[field] in ("", None))
    if missing:
        raise ValueError(f"forceOrder row missing required fields: {missing}")
    return row


def parse_force_order_rows(
    raw: Any,
    symbol_filter: set[str],
    accept_all: bool,
    reception: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    packet_reception = reception or capture_reception()
    for payload in force_order_payloads(raw):
        row = parse_force_order_payload(payload, symbol_filter, accept_all, packet_reception)
        if row is not None:
            rows.append(row)
    return rows


def parse_force_order_message(raw: dict[str, Any]) -> dict[str, Any] | None:
    rows = parse_force_order_rows(raw, {"BTCUSDT"}, True)
    return rows[0] if rows else None


def build_streams(symbols: str, stream_mode: str, liveness_stream: str = "") -> tuple[list[str], list[str]]:
    symbol_filter, accept_all = parse_symbol_filter(symbols)
    streams: list[str] = []
    symbol_streams: list[str] = []
    mode = stream_mode.lower().strip()
    if mode in {"symbols", "both"}:
        if accept_all:
            raise ValueError("--stream-mode symbols/both requires explicit --symbols, not ALL")
        symbol_streams = [symbol.lower() for symbol in sorted(symbol_filter)]
        streams.extend(f"{symbol}@forceOrder" for symbol in symbol_streams)
    if mode in {"all_market", "both"}:
        streams.append(ALL_MARKET_FORCE_ORDER_STREAM)
    liveness = liveness_stream.strip()
    if liveness and liveness not in streams:
        streams.append(liveness)
    if not streams:
        raise ValueError(f"unsupported --stream-mode: {stream_mode}")
    return streams, [symbol.upper() for symbol in sorted(symbol_filter)] if not accept_all else ["ALL"]


def daily_path(out_dir: Path, row: dict[str, Any]) -> Path:
    day = datetime.fromtimestamp(int(row["event_time_ms"]) / 1000.0, tz=timezone.utc).strftime("%Y%m%d")
    symbol = row["symbol"].upper()
    return out_dir / symbol / f"{day}.jsonl"


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
        "tool": "tools/binance_force_order_real_feed_collector.py",
        "can_trade": False,
        "data_collector_only": True,
        "symbols": stats.get("symbols", []),
        "mode": stats.get("mode"),
        "url": stats.get("url"),
        "stream_mode": stats.get("stream_mode"),
        "streams": stats.get("streams", []),
        "out_dir": stats.get("out_dir"),
        "events_written": stats.get("events_written", 0),
        "messages_seen": stats.get("messages_seen", 0),
        "force_order_messages_seen": stats.get("force_order_messages_seen", 0),
        "liveness_stream": stats.get("liveness_stream"),
        "liveness_messages_seen": stats.get("liveness_messages_seen", 0),
        "last_liveness_at": stats.get("last_liveness_at"),
        "last_message_at": stats.get("last_message_at"),
        "last_event_at": stats.get("last_event_at"),
        "parse_errors_count": len(stats.get("parse_errors") or []),
    }
    if extra:
        payload["extra"] = extra
    write_json(path, payload)


def sample_message() -> dict[str, Any]:
    return {
        "stream": "btcusdt@forceOrder",
        "data": {
            "e": "forceOrder",
            "E": 1760000000000,
            "o": {
                "s": "BTCUSDT",
                "S": "SELL",
                "o": "LIMIT",
                "f": "IOC",
                "q": "0.010",
                "p": "100000.00",
                "ap": "99950.00",
                "X": "FILLED",
                "l": "0.010",
                "z": "0.010",
                "T": 1760000000000,
            },
        },
    }


def sample_all_market_message() -> dict[str, Any]:
    return {
        "stream": ALL_MARKET_FORCE_ORDER_STREAM,
        "data": [
            sample_message()["data"],
            {
                "e": "forceOrder",
                "E": 1760000001000,
                "o": {
                    "s": "ETHUSDT",
                    "S": "BUY",
                    "o": "LIMIT",
                    "f": "IOC",
                    "q": "0.100",
                    "p": "5000.00",
                    "ap": "5005.00",
                    "X": "FILLED",
                    "l": "0.100",
                    "z": "0.100",
                    "T": 1760000001000,
                },
            },
        ],
    }


async def collect(args: argparse.Namespace) -> dict[str, Any]:
    symbol_filter, accept_all = parse_symbol_filter(args.symbols)
    streams, stats_symbols = build_streams(args.symbols, args.stream_mode, args.liveness_stream)
    stream_path = "/".join(streams)
    url = BINANCE_USDM_COMBINED_STREAM.format(streams=stream_path)
    out_dir = resolve_path(args.out_dir)
    stats: dict[str, Any] = {
        "started_at": now_iso(),
        "ended_at": None,
        "url": url,
        "stream_mode": args.stream_mode,
        "streams": streams,
        "symbols": stats_symbols,
        "accept_all_symbols": accept_all,
        "out_dir": portable_path(out_dir),
        "events_written": 0,
        "messages_seen": 0,
        "force_order_messages_seen": 0,
        "liveness_stream": args.liveness_stream or None,
        "liveness_messages_seen": 0,
        "last_liveness_at": None,
        "last_message_at": None,
        "last_event_at": None,
        "parse_errors": [],
        "mode": "dry_run" if args.dry_run else "live_websocket",
    }
    heartbeat_path = resolve_path(args.heartbeat_path)
    write_heartbeat(heartbeat_path, stats, "starting")
    if args.dry_run:
        raw_sample = sample_all_market_message() if args.stream_mode in {"all_market", "both"} else sample_message()
        rows = parse_force_order_rows(raw_sample, symbol_filter, accept_all)
        assert rows
        stats["events_written"] = 0
        stats["messages_seen"] = 1
        stats["last_message_at"] = now_iso()
        stats["sample_rows"] = rows
        stats["dry_run_parser_ok"] = True
        stats["ended_at"] = now_iso()
        write_heartbeat(heartbeat_path, stats, "dry_run_parser_ok")
        return stats

    deadline = asyncio.get_running_loop().time() + args.max_seconds
    async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=5) as websocket:
        write_heartbeat(heartbeat_path, stats, "connected")
        while stats["events_written"] < args.max_events and asyncio.get_running_loop().time() < deadline:
            timeout = max(0.1, min(5.0, deadline - asyncio.get_running_loop().time()))
            try:
                message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                write_heartbeat(heartbeat_path, stats, "connected_waiting_events")
                continue
            reception = capture_reception()
            stats["messages_seen"] += 1
            stats["last_message_at"] = now_iso()
            try:
                decoded = json.loads(message)
                stream_name = str(decoded.get("stream") or "") if isinstance(decoded, dict) else ""
                if args.liveness_stream and stream_name == args.liveness_stream:
                    stats["liveness_messages_seen"] += 1
                    stats["last_liveness_at"] = stats["last_message_at"]
                    if stats["liveness_messages_seen"] == 1 or stats["liveness_messages_seen"] % 30 == 0:
                        write_heartbeat(heartbeat_path, stats, "transport_liveness_ok")
                rows = parse_force_order_rows(decoded, symbol_filter, accept_all, reception)
            except Exception as exc:
                stats["parse_errors"].append(repr(exc))
                write_heartbeat(heartbeat_path, stats, "parse_error")
                continue
            if not rows:
                if not args.liveness_stream or stream_name != args.liveness_stream:
                    write_heartbeat(heartbeat_path, stats, "non_force_order_message_seen")
                continue
            stats["force_order_messages_seen"] += 1
            for row in rows:
                append_jsonl(daily_path(out_dir, row), row)
                stats["events_written"] += 1
                stats["last_event_at"] = row.get("event_time")
                if stats["events_written"] >= args.max_events:
                    break
            write_heartbeat(heartbeat_path, stats, "event_written", extra={"rows": len(rows), "last_event_at": stats.get("last_event_at")})
    stats["ended_at"] = now_iso()
    write_heartbeat(heartbeat_path, stats, "cycle_finished")
    return stats


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Binance ForceOrder Real-Feed Collector Run",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            f"- Can trade: `{str(report['can_trade']).lower()}`",
            f"- Mode: `{report['stats']['mode']}`",
            f"- Stream mode: `{report['stats'].get('stream_mode')}`",
            f"- Symbols: `{', '.join(report['stats']['symbols'])}`",
            f"- Streams: `{', '.join(report['stats'].get('streams', []))}`",
            f"- Events written: `{report['stats']['events_written']}`",
            f"- Messages seen: `{report['stats']['messages_seen']}`",
            f"- Force-order messages seen: `{report['stats'].get('force_order_messages_seen')}`",
            f"- Liveness stream: `{report['stats'].get('liveness_stream')}`",
            f"- Liveness messages seen: `{report['stats'].get('liveness_messages_seen')}`",
            f"- Output dir: `{report['stats']['out_dir']}`",
            "",
            "## Notes",
            "",
            "- This collector writes true event-level Binance USD-M `forceOrder` rows.",
            "- It is a data collector only; no strategy consumer is enabled.",
            "- If a live run sees zero events, that is not a failure by itself because liquidation events are sparse.",
        ]
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Forward collector for Binance USD-M forceOrder liquidation events")
    parser.add_argument("--symbols", default="ALL")
    parser.add_argument("--stream-mode", choices=["symbols", "all_market", "both"], default="all_market")
    parser.add_argument("--out-dir", default="data/live/liquidations/binance_force_order")
    parser.add_argument("--max-events", type=int, default=10)
    parser.add_argument("--max-seconds", type=float, default=60.0)
    parser.add_argument("--liveness-stream", default=DEFAULT_LIVENESS_STREAM)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--heartbeat-path", default="logs/liquidation_force_order/liquidation_force_order_collector_heartbeat.json")
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_FORCE_ORDER_FORWARD_COLLECTOR_2026-06-30")
    args = parser.parse_args()

    stats = asyncio.run(collect(args))
    if stats["events_written"] > 0:
        decision = "force_order_forward_collector_wrote_events"
    elif stats["mode"] == "dry_run":
        decision = "force_order_forward_collector_dry_run_parser_ok"
    elif stats["liveness_messages_seen"] > 0:
        decision = "force_order_forward_collector_transport_live_no_liquidations_observed"
    else:
        decision = "force_order_forward_collector_silent_stream_unhealthy"
    report = {
        "generated_at": now_iso(),
        "tool": "tools/binance_force_order_real_feed_collector.py",
        "decision": decision,
        "can_trade": False,
        "contract": "configs/LIQUIDATION_REAL_FEED_CONTRACT.json",
        "stats": stats,
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": decision, "out": portable_path(out.with_suffix(".json"))}, ensure_ascii=False))
    return 2 if decision == "force_order_forward_collector_silent_stream_unhealthy" else 0


if __name__ == "__main__":
    raise SystemExit(main())

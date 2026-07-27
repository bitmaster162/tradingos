#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import websockets

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bybit_all_liquidation_real_feed_collector as legacy


TOOL_PATH = "tools/bybit_all_liquidation_real_feed_collector_v2.py"
# Keep the canonical venue source stable; schema/version fields carry ingest evolution.
SOURCE = legacy.SOURCE
INGEST_SCHEMA_VERSION = 4
PACKET_FIELDS = {"packet_item_index", "packet_item_count"}
REQUIRED_FIELDS = legacy.REQUIRED_FIELDS | PACKET_FIELDS
LEGACY_PARSE_BYBIT_MESSAGE = legacy.parse_bybit_message


def parse_bybit_message(raw: dict[str, Any], reception: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Preserve every exchange item and give it a receipt-unique packet ordinal."""
    rows = LEGACY_PARSE_BYBIT_MESSAGE(raw, reception)
    item_count = len(rows)
    for index, row in enumerate(rows):
        row["packet_item_index"] = index
        row["packet_item_count"] = item_count
        row["ingest_schema_version"] = INGEST_SCHEMA_VERSION
        row["source"] = SOURCE
        missing = sorted(field for field in REQUIRED_FIELDS if row.get(field) in (None, ""))
        if missing:
            raise ValueError(f"Bybit schema-v4 liquidation row missing required fields: {missing}")
    return rows


def render_markdown(report: dict[str, Any]) -> str:
    stats = report["stats"]
    return "\n".join(
        [
            "# Bybit All Liquidation Real-Feed Collector V2",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            "- Ingest schema: `4`",
            "- Packet item ordinals: `enabled`",
            f"- Events written: `{stats['events_written']}`",
            f"- Messages seen: `{stats['messages_seen']}`",
            "- Public data only: `true`",
            "- Can trade: `false`",
            "",
            "Identical market tuples in one Bybit packet are preserved as separate observations. "
            "Physical write identity is `collector_session_id + packet_sequence + packet_item_index`.",
            "",
        ]
    )


async def collect(args: argparse.Namespace) -> dict[str, Any]:
    symbols = legacy.parse_symbols(args.symbols)
    topics = [f"allLiquidation.{symbol}" for symbol in symbols]
    out_dir = legacy.resolve_path(args.out_dir)
    heartbeat_path = legacy.resolve_path(args.heartbeat_path)
    stats: dict[str, Any] = {
        "started_at": legacy.now_iso(),
        "ended_at": None,
        "url": legacy.BYBIT_LINEAR_PUBLIC_WS,
        "symbols": symbols,
        "topics": topics,
        "out_dir": legacy.portable_path(out_dir),
        "events_written": 0,
        "packets_written": 0,
        "messages_seen": 0,
        "last_message_at": None,
        "last_event_at": None,
        "parse_errors": [],
        "clock_calibration_errors": [],
        "clock_calibration_ready": False,
        "clock_calibrations": [],
        "collector_session_id": uuid.uuid4().hex,
        "mode": "dry_run" if args.dry_run else "live_websocket",
        "packet_atomic_write": True,
    }
    legacy.write_heartbeat(heartbeat_path, stats, "starting_schema_v4")
    try:
        calibration = (
            legacy.synthetic_clock_calibration()
            if args.dry_run
            else await asyncio.to_thread(
                legacy.calibrate_bybit_clock,
                samples=args.clock_samples,
                timeout_s=args.clock_timeout_s,
            )
        )
    except Exception as exc:
        stats["clock_calibration_errors"].append(f"{type(exc).__name__}:{exc}")
        stats["ended_at"] = legacy.now_iso()
        legacy.write_heartbeat(heartbeat_path, stats, "blocked_clock_calibration_schema_v4")
        return stats
    stats["clock_calibration_ready"] = True
    stats["clock_calibrations"].append(calibration)
    if args.dry_run:
        rows = parse_bybit_message(
            legacy.sample_message(),
            legacy.capture_reception(
                calibration=calibration,
                collector_session_id=stats["collector_session_id"],
                packet_sequence=1,
            ),
        )
        assert rows
        stats["messages_seen"] = 1
        stats["packets_written"] = 1
        stats["last_message_at"] = legacy.now_iso()
        stats["sample_rows"] = rows
        stats["dry_run_parser_ok"] = True
        stats["ended_at"] = legacy.now_iso()
        legacy.write_heartbeat(heartbeat_path, stats, "dry_run_parser_ok_schema_v4")
        return stats

    deadline = asyncio.get_running_loop().time() + args.max_seconds
    packet_sequence = 0
    async with websockets.connect(
        legacy.BYBIT_LINEAR_PUBLIC_WS,
        ping_interval=20,
        ping_timeout=20,
        close_timeout=5,
    ) as websocket:
        subscribe = {"op": "subscribe", "args": topics}
        await websocket.send(json.dumps(subscribe, separators=(",", ":")))
        legacy.write_heartbeat(heartbeat_path, stats, "connected_subscribed_schema_v4", extra={"topics": topics})
        while stats["events_written"] < args.max_events and asyncio.get_running_loop().time() < deadline:
            timeout = max(0.1, min(5.0, deadline - asyncio.get_running_loop().time()))
            try:
                message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                legacy.write_heartbeat(heartbeat_path, stats, "connected_waiting_events_schema_v4")
                continue
            packet_sequence += 1
            if time.time_ns() - int(calibration["clock_calibrated_at_ns"]) > int(
                args.clock_refresh_seconds * 1_000_000_000
            ):
                try:
                    calibration = await asyncio.to_thread(
                        legacy.calibrate_bybit_clock,
                        samples=args.clock_samples,
                        timeout_s=args.clock_timeout_s,
                    )
                    stats["clock_calibrations"].append(calibration)
                except Exception as exc:
                    stats["clock_calibration_ready"] = False
                    stats["clock_calibration_errors"].append(f"{type(exc).__name__}:{exc}")
                    legacy.write_heartbeat(heartbeat_path, stats, "blocked_clock_recalibration_schema_v4")
                    break
            reception = legacy.capture_reception(
                calibration=calibration,
                collector_session_id=stats["collector_session_id"],
                packet_sequence=packet_sequence,
            )
            stats["messages_seen"] += 1
            stats["last_message_at"] = legacy.now_iso()
            try:
                rows = parse_bybit_message(json.loads(message), reception)
            except Exception as exc:
                stats["parse_errors"].append(repr(exc))
                legacy.write_heartbeat(heartbeat_path, stats, "parse_error_schema_v4")
                continue
            if not rows:
                legacy.write_heartbeat(heartbeat_path, stats, "non_liquidation_message_seen_schema_v4")
                continue
            # A packet is the smallest durable unit. The event cap is checked only between packets.
            for row in rows:
                legacy.append_jsonl(legacy.daily_path(out_dir, row), row)
                stats["events_written"] += 1
                stats["last_event_at"] = row.get("liquidation_time")
            stats["packets_written"] += 1
            legacy.write_heartbeat(
                heartbeat_path,
                stats,
                "packet_written_schema_v4",
                extra={"rows": len(rows), "last_event_at": stats.get("last_event_at")},
            )
    stats["ended_at"] = legacy.now_iso()
    stats["event_cap_overshoot"] = max(0, int(stats["events_written"]) - int(args.max_events))
    legacy.write_heartbeat(heartbeat_path, stats, "cycle_finished_schema_v4")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Schema-v4 Bybit allLiquidation collector with packet item ordinals")
    parser.add_argument(
        "--symbols",
        default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,LINKUSDT,LTCUSDT",
    )
    parser.add_argument("--out-dir", default="data/live/liquidations/bybit_all_liquidation")
    parser.add_argument("--max-events", type=int, default=20)
    parser.add_argument("--max-seconds", type=float, default=60.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--clock-samples", type=int, default=3)
    parser.add_argument("--clock-timeout-s", type=float, default=5.0)
    parser.add_argument("--clock-refresh-seconds", type=float, default=240.0)
    parser.add_argument(
        "--heartbeat-path",
        default="logs/liquidation_bybit/bybit_all_liquidation_collector_heartbeat.json",
    )
    parser.add_argument("--out-prefix", default="docs/BYBIT_ALL_LIQUIDATION_FORWARD_COLLECTOR_V2_LATEST")
    args = parser.parse_args()

    stats = asyncio.run(collect(args))
    decision = (
        "bybit_all_liquidation_collector_v2_blocked_clock_calibration"
        if not stats["clock_calibration_ready"]
        else "bybit_all_liquidation_collector_v2_wrote_schema_v4_events"
        if stats["events_written"] > 0
        else "bybit_all_liquidation_collector_v2_dry_run_parser_ok"
        if stats["mode"] == "dry_run"
        else "bybit_all_liquidation_collector_v2_connected_no_events_observed"
    )
    report = {
        "generated_at": legacy.now_iso(),
        "tool": TOOL_PATH,
        "decision": decision,
        "ingest_schema_version": INGEST_SCHEMA_VERSION,
        "packet_item_identity": "collector_session_id+packet_sequence+packet_item_index",
        "can_trade": False,
        "stats": stats,
    }
    out = legacy.resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": decision, "out": legacy.portable_path(out.with_suffix('.json')), "can_trade": False}))
    return 2 if not stats["clock_calibration_ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

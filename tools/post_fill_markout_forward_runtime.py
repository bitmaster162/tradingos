#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, is_dataclass
import json
import os
from pathlib import Path
import signal
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BOT_SRC = ROOT / "ops" / "btcusdt_binance_futures_bot" / "src"
if str(BOT_SRC) not in sys.path:
    sys.path.insert(0, str(BOT_SRC))

from btcusdt_bot.authoritative.archive import USER_TRADES_DATASET  # noqa: E402
from btcusdt_bot.authoritative.backfill import (  # noqa: E402
    AuthoritativeHistoryBackfillConfig,
    AuthoritativeHistoryBackfiller,
)
from btcusdt_bot.collector.book_ticker import BookTickerCollector  # noqa: E402
from btcusdt_bot.config import BotConfig  # noqa: E402
from btcusdt_bot.connectors.rest_client import BinanceRESTClient  # noqa: E402
from btcusdt_bot.connectors.signing import now_ms  # noqa: E402
from btcusdt_bot.monitoring.post_fill_forward import (  # noqa: E402
    PostFillForwardLock,
    build_post_fill_forward_report,
    load_post_fill_forward_lock,
)
from btcusdt_bot.storage.jsonl import JSONLWriter  # noqa: E402
from btcusdt_bot.utils.serde import to_jsonable  # noqa: E402


DEFAULT_PREREG = ROOT / "configs" / "POST_FILL_MARKOUT_FORWARD_PREREG_2026-07-14.json"
DEFAULT_WORKER_STATUS = ROOT / "logs" / "post_fill_markout_forward" / "worker_status.json"
DEFAULT_PULSE_HISTORY = ROOT / "logs" / "post_fill_markout_forward" / "pulse_history.jsonl"


def _json_value(value: object) -> object:
    return to_jsonable(asdict(value) if is_dataclass(value) else value)


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temp.write_text(
        json.dumps(_json_value(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def _append_jsonl(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_json_value(payload), ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def contiguous_user_trade_coverage_cursor(manifest_path: Path, *, floor_ms: int) -> int:
    manifest = _read_json(manifest_path)
    datasets = manifest.get("datasets")
    user_trades = datasets.get(USER_TRADES_DATASET) if isinstance(datasets, dict) else None
    intervals: list[tuple[int, int]] = []
    if isinstance(user_trades, dict):
        for bucket in user_trades.values():
            if not isinstance(bucket, dict):
                continue
            for item in bucket.get("intervals") or []:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    continue
                try:
                    intervals.append((int(item[0]), int(item[1])))
                except (TypeError, ValueError):
                    continue
    cursor = floor_ms
    for start_ms, end_ms in sorted(intervals):
        if end_ms < cursor:
            continue
        if start_ms > cursor:
            break
        cursor = max(cursor, end_ms + 1)
    return cursor


def _build_bot_config(lock: PostFillForwardLock) -> BotConfig:
    requested_env = os.getenv("BOT_ENV", "demo").strip().lower()
    if requested_env != "demo":
        raise ValueError("post_fill_forward_runtime_requires_demo_env")
    os.environ["BOT_ENV"] = "demo"
    os.environ["DATA_DIR"] = str(lock.market_root)
    config = BotConfig.from_env()
    if config.is_live:
        raise ValueError("post_fill_forward_runtime_live_env_forbidden")
    if config.symbol != lock.symbol:
        raise ValueError("post_fill_forward_runtime_symbol_mismatch")
    return config


def _credentials_state(config: BotConfig) -> str:
    if config.api_key and config.api_secret:
        return "complete"
    if config.api_key or config.api_secret:
        return "incomplete"
    return "missing"


def _backfill_user_trades(
    config: BotConfig,
    lock: PostFillForwardLock,
    *,
    closed_end_ms: int,
    overlap_ms: int,
) -> dict[str, object]:
    credential_state = _credentials_state(config)
    if credential_state != "complete":
        return {
            "decision": f"authoritative_backfill_blocked_credentials_{credential_state}",
            "request_sent": False,
            "endpoint_scope": ["/fapi/v1/userTrades"],
            "income_requested": False,
            "orders_allowed": False,
            "can_trade": False,
        }
    if closed_end_ms < lock.forward_start_ms:
        return {
            "decision": "authoritative_backfill_waiting_closed_window",
            "request_sent": False,
            "endpoint_scope": ["/fapi/v1/userTrades"],
            "income_requested": False,
            "orders_allowed": False,
            "can_trade": False,
        }

    manifest_path = lock.archive_root / "authoritative" / "manifests" / f"{lock.symbol.lower()}_history_manifest.json"
    cursor_ms = contiguous_user_trade_coverage_cursor(manifest_path, floor_ms=lock.forward_start_ms)
    if cursor_ms > closed_end_ms:
        return {
            "decision": "authoritative_user_trades_up_to_date",
            "request_sent": False,
            "coverage_cursor_ms": cursor_ms,
            "closed_end_ms": closed_end_ms,
            "endpoint_scope": ["/fapi/v1/userTrades"],
            "income_requested": False,
            "orders_allowed": False,
            "can_trade": False,
        }

    request_start_ms = max(lock.forward_start_ms, cursor_ms - max(0, overlap_ms))
    client = BinanceRESTClient(
        base_url=config.rest_base_url,
        api_key=config.api_key,
        api_secret=config.api_secret,
        recv_window_ms=config.recv_window_ms,
        timeout_s=config.timeout_s,
    )
    with JSONLWriter(lock.archive_root) as writer:
        result = AuthoritativeHistoryBackfiller(
            config,
            client=client,
            writer=writer,
            backfill_config=AuthoritativeHistoryBackfillConfig(
                archive_root=lock.archive_root,
                start_ms=request_start_ms,
                end_ms=closed_end_ms,
                include_income_history=False,
            ),
        ).run_once()
    return {
        "decision": "authoritative_user_trades_backfill_completed",
        "request_sent": True,
        "requested_start_ms": request_start_ms,
        "requested_end_ms": closed_end_ms,
        "user_trade_row_count": result.user_trade_row_count,
        "user_trade_requests": result.user_trade_requests,
        "income_requested": result.income_history_requested,
        "income_requests": result.income_requests,
        "manifest_path": str(result.manifest_path),
        "endpoint_scope": ["/fapi/v1/userTrades"],
        "orders_allowed": False,
        "can_trade": False,
    }


def run_pulse(
    *,
    prereg_path: Path,
    project_root: Path,
    config: BotConfig,
    generated_at_ms: int | None = None,
    overlap_ms: int = 60_000,
    collector_status: dict[str, object] | None = None,
) -> dict[str, Any]:
    pulse_at_ms = generated_at_ms if generated_at_ms is not None else now_ms()
    lock = load_post_fill_forward_lock(prereg_path, project_root=project_root)
    closed_end_ms = pulse_at_ms - max(lock.horizons_seconds) * 1000 - lock.max_post_horizon_delay_ms
    try:
        backfill = _backfill_user_trades(
            config,
            lock,
            closed_end_ms=closed_end_ms,
            overlap_ms=overlap_ms,
        )
    except Exception as exc:  # noqa: BLE001
        backfill = {
            "decision": "authoritative_user_trades_backfill_failed",
            "error_type": type(exc).__name__,
            "request_sent": True,
            "income_requested": False,
            "endpoint_scope": ["/fapi/v1/userTrades"],
            "orders_allowed": False,
            "can_trade": False,
        }

    observer = build_post_fill_forward_report(
        prereg_path,
        project_root=project_root,
        generated_at_ms=pulse_at_ms,
        credentials_present=config.has_api_credentials,
    )
    output_root = project_root / "data"
    with JSONLWriter(output_root) as writer:
        latest_path = writer.write_json("live/reports/latest_post_fill_forward_observer.json", observer)
        history_path = writer.append_record(
            "reports",
            f"{lock.symbol.lower()}_post_fill_forward_observer",
            {"report": observer},
            event_time_ms=pulse_at_ms,
        )

    pulse = {
        "schema_version": 1,
        "generated_at_ms": pulse_at_ms,
        "decision": "post_fill_forward_runtime_pulse_completed",
        "lock_id": lock.lock_id,
        "credentials_state": _credentials_state(config),
        "collector": collector_status or {},
        "backfill": backfill,
        "observer": {
            "decision": observer.get("decision"),
            "blockers": observer.get("blockers"),
            "book_capture": observer.get("book_capture"),
            "authoritative_manifest_present": observer.get("authoritative_manifest_present"),
            "latest_path": str(latest_path),
            "history_path": str(history_path),
        },
        "runtime_boundary": {
            "demo_only": True,
            "public_capture_allowed": True,
            "signed_endpoint_allowlist": ["/fapi/v1/userTrades"],
            "income_endpoint_allowed": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    return pulse


def _collector_status(collector: BookTickerCollector, task: asyncio.Task[object]) -> dict[str, object]:
    return {
        "task_done": task.done(),
        "messages_received": collector.status.messages_received,
        "reconnects": collector.status.reconnects,
        "last_event_time_ms": collector.status.last_event_time_ms,
        "last_error": collector.status.last_error,
        "last_written_path": collector.status.last_written_path,
        "public_data_only": True,
        "orders_allowed": False,
        "can_trade": False,
    }


async def run_runtime(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    prereg_path = Path(args.prereg_path)
    if not prereg_path.is_absolute():
        prereg_path = project_root / prereg_path
    worker_status_path = Path(args.worker_status)
    if not worker_status_path.is_absolute():
        worker_status_path = project_root / worker_status_path
    pulse_history_path = Path(args.pulse_history)
    if not pulse_history_path.is_absolute():
        pulse_history_path = project_root / pulse_history_path

    lock = load_post_fill_forward_lock(prereg_path, project_root=project_root)
    config = _build_bot_config(lock)
    stop_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop_requested.set)
        except (NotImplementedError, RuntimeError):
            pass

    collector_writer: JSONLWriter | None = None
    collector: BookTickerCollector | None = None
    collector_task: asyncio.Task[object] | None = None

    def start_collector() -> tuple[BookTickerCollector, asyncio.Task[object]]:
        nonlocal collector_writer
        if collector_writer is None:
            collector_writer = JSONLWriter(lock.market_root)
        instance = BookTickerCollector(config, writer=collector_writer)
        task = asyncio.create_task(
            instance.run(stop_after_messages=args.collector_max_messages),
            name="post-fill-book-ticker-collector",
        )
        return instance, task

    if not args.skip_collector:
        collector, collector_task = start_collector()
        if args.collector_startup_grace_seconds > 0:
            try:
                await asyncio.wait_for(
                    stop_requested.wait(),
                    timeout=args.collector_startup_grace_seconds,
                )
            except TimeoutError:
                pass

    pulses = 0
    collector_restarts = 0
    next_pulse_at = time.monotonic()
    try:
        while not stop_requested.is_set():
            if collector_task is not None and collector_task.done():
                failure_type = None
                try:
                    collector_task.result()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    failure_type = type(exc).__name__
                if args.max_pulses is not None and pulses >= args.max_pulses:
                    break
                collector_restarts += 1
                _atomic_write_json(
                    worker_status_path,
                    {
                        "status": "collector_restarting",
                        "collector_restarts": collector_restarts,
                        "last_failure_type": failure_type,
                        "orders_allowed": False,
                        "can_trade": False,
                    },
                )
                await asyncio.sleep(args.collector_restart_seconds)
                collector, collector_task = start_collector()

            if time.monotonic() >= next_pulse_at:
                collector_snapshot = (
                    _collector_status(collector, collector_task)
                    if collector is not None and collector_task is not None
                    else {"skipped": True, "can_trade": False}
                )
                pulse = await asyncio.to_thread(
                    run_pulse,
                    prereg_path=prereg_path,
                    project_root=project_root,
                    config=config,
                    overlap_ms=args.backfill_overlap_ms,
                    collector_status=collector_snapshot,
                )
                pulses += 1
                status = {
                    "schema_version": 1,
                    "status": "sleeping" if args.max_pulses is None or pulses < args.max_pulses else "bounded_complete",
                    "pid": os.getpid(),
                    "pulses": pulses,
                    "collector_restarts": collector_restarts,
                    "last_pulse": pulse,
                    "next_pulse_after_seconds": args.pulse_seconds,
                    "orders_allowed": False,
                    "can_trade": False,
                }
                _atomic_write_json(worker_status_path, status)
                _append_jsonl(pulse_history_path, pulse)
                if args.max_pulses is not None and pulses >= args.max_pulses:
                    break
                next_pulse_at = time.monotonic() + args.pulse_seconds

            try:
                await asyncio.wait_for(stop_requested.wait(), timeout=1.0)
            except TimeoutError:
                pass
    finally:
        if collector_task is not None and not collector_task.done():
            collector_task.cancel()
            try:
                await collector_task
            except asyncio.CancelledError:
                pass
        if collector_writer is not None:
            collector_writer.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed Binance demo post-fill forward runtime")
    parser.add_argument("--project-root", default=str(ROOT))
    parser.add_argument("--prereg-path", default=str(DEFAULT_PREREG))
    parser.add_argument("--worker-status", default=str(DEFAULT_WORKER_STATUS))
    parser.add_argument("--pulse-history", default=str(DEFAULT_PULSE_HISTORY))
    parser.add_argument("--pulse-seconds", type=int, default=300)
    parser.add_argument("--collector-startup-grace-seconds", type=float, default=2.0)
    parser.add_argument("--collector-restart-seconds", type=float, default=5.0)
    parser.add_argument("--collector-max-messages", type=int, default=None)
    parser.add_argument("--backfill-overlap-ms", type=int, default=60_000)
    parser.add_argument("--max-pulses", type=int, default=None)
    parser.add_argument("--skip-collector", action="store_true")
    args = parser.parse_args()
    if args.pulse_seconds < 10 and args.max_pulses != 1:
        parser.error("pulse-seconds must be at least 10 for an unbounded runtime")
    if args.collector_restart_seconds < 1:
        parser.error("collector-restart-seconds must be at least 1")
    if args.max_pulses is not None and args.max_pulses < 1:
        parser.error("max-pulses must be positive")
    return asyncio.run(run_runtime(args))


if __name__ == "__main__":
    raise SystemExit(main())

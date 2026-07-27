from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass

import websockets
from websockets.exceptions import ConnectionClosed

from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.connectors.ws_urls import build_combined_stream_url
from btcusdt_bot.connectors.rest_client import BinanceRESTClient
from btcusdt_bot.simulator.depth_book import DepthBookSyncError, LocalDepthBook
from btcusdt_bot.state.store import StateStore
from btcusdt_bot.storage.jsonl import JSONLWriter
from btcusdt_bot.ws.messages import decode_ws_message


@dataclass(slots=True)
class DepthBookCollectorStatus:
    url: str
    stream: str
    snapshot_limit: int
    depth_levels: int
    messages_received: int = 0
    local_snapshots_written: int = 0
    snapshot_loads: int = 0
    resyncs: int = 0
    reconnects: int = 0
    last_update_id: int = 0
    last_event_time_ms: int = 0
    last_error: str = ""
    last_written_path: str = ""


class DepthBookCollector:
    def __init__(
        self,
        config: BotConfig,
        *,
        client: BinanceRESTClient,
        writer: JSONLWriter,
        store: StateStore | None = None,
        depth_levels: int = 20,
        snapshot_limit: int = 1000,
        logger: logging.Logger | None = None,
        stream: str | None = None,
        snapshot_fetch_method: str = "depth",
        snapshot_event_type: str = "localDepthSnapshot",
        snapshot_filename_prefix: str = "localDepth",
        store_patch_method: str = "patch_depth_snapshot",
        logger_name: str = "btcusdt_bot.depth_book_collector",
    ) -> None:
        self.config = config
        self.client = client
        self.writer = writer
        self.store = store
        self.depth_levels = max(1, depth_levels)
        self.snapshot_limit = max(5, snapshot_limit)
        self.snapshot_fetch_method = snapshot_fetch_method
        self.snapshot_event_type = snapshot_event_type
        self.snapshot_filename_prefix = snapshot_filename_prefix
        self.store_patch_method = store_patch_method
        self.logger = logger or logging.getLogger(logger_name)
        self.stream = stream or f"{config.symbol.lower()}@depth@100ms"
        self.url = build_combined_stream_url(self.config.ws_public_base_url, [self.stream])
        self.status = DepthBookCollectorStatus(
            url=self.url,
            stream=self.stream,
            snapshot_limit=self.snapshot_limit,
            depth_levels=self.depth_levels,
        )
        self.book = LocalDepthBook(symbol=config.symbol, levels=self.depth_levels)

    def manifest(self) -> dict[str, object]:
        if self.snapshot_fetch_method == "rpi_depth":
            notes = [
                "Use RPI diff depth stream plus RPI REST snapshot for an RPI-aware local order book.",
                "Bootstrap by buffering diff events, then apply snapshot and only process events where U <= lastUpdateId <= u.",
                "Crossed RPI price levels can be hidden even though RPI orders are included in the stream and REST snapshot.",
            ]
        else:
            notes = [
                "Use diff depth stream plus REST snapshot for a local order book.",
                "Bootstrap by buffering diff events, then apply snapshot and only process events where U <= lastUpdateId <= u.",
                "On any pu/u sequence gap, discard state and re-bootstrap.",
            ]
        return {
            "url": self.url,
            "streams": [self.stream],
            "routing": "public",
            "snapshot_limit": self.snapshot_limit,
            "depth_levels": self.depth_levels,
            "notes": notes,
        }

    async def run(self, *, stop_after_messages: int | None = None) -> DepthBookCollectorStatus:
        backoff_s = max(0.2, self.config.reconnect_initial_backoff_ms / 1000)
        max_backoff_s = max(backoff_s, self.config.reconnect_max_backoff_ms / 1000)

        while True:
            try:
                async with websockets.connect(
                    self.url,
                    ping_interval=None,
                    max_size=None,
                    open_timeout=self.config.timeout_s,
                ) as websocket:
                    self.logger.info("depth collector connected", extra={"url": self.url})
                    backoff_s = max(0.2, self.config.reconnect_initial_backoff_ms / 1000)
                    buffer: deque[dict[str, object]] = deque(maxlen=5000)
                    snapshot_task = asyncio.create_task(self._fetch_snapshot())
                    self.book.reset()
                    async for raw_message in websocket:
                        ws_message = decode_ws_message(raw_message)
                        payload = ws_message.payload
                        if str(payload.get("e", "")) != "depthUpdate":
                            continue
                        self._write_raw(ws_message.stream or self.stream, payload)
                        self.status.messages_received += 1
                        self.status.last_event_time_ms = int(payload.get("E", payload.get("T", now_ms())))
                        buffer.append(payload)

                        if not self.book.initialized:
                            if snapshot_task.done():
                                snapshot = snapshot_task.result()
                                try:
                                    applied = self.book.bootstrap_from_buffer(snapshot, list(buffer))
                                except DepthBookSyncError as exc:
                                    self.status.last_error = str(exc)
                                    self.status.resyncs += 1
                                    self.book.reset()
                                    snapshot_task = asyncio.create_task(self._fetch_snapshot())
                                else:
                                    self.status.snapshot_loads += 1
                                    self.status.last_update_id = self.book.last_update_id
                                    self.logger.info("depth bootstrap applied", extra={"applied": applied, "last_update_id": self.book.last_update_id})
                                    self._write_local_snapshot(event_time_ms=self.book.last_event_time_ms)
                            if stop_after_messages is not None and self.status.messages_received >= stop_after_messages:
                                await websocket.close()
                                return self.status
                            continue

                        try:
                            self.book.apply_diff_event(payload)
                        except DepthBookSyncError as exc:
                            self.status.last_error = str(exc)
                            self.status.resyncs += 1
                            self.book.reset()
                            buffer.clear()
                            buffer.append(payload)
                            snapshot_task = asyncio.create_task(self._fetch_snapshot())
                        else:
                            self.status.last_update_id = self.book.last_update_id
                            self._write_local_snapshot(event_time_ms=self.book.last_event_time_ms)

                        if stop_after_messages is not None and self.status.messages_received >= stop_after_messages:
                            await websocket.close()
                            return self.status
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as exc:
                self.status.last_error = f"connection_closed code={exc.code} reason={exc.reason}"
                self.logger.warning("depth collector connection closed: %s", self.status.last_error)
            except Exception as exc:  # noqa: BLE001
                self.status.last_error = str(exc)
                self.logger.exception("depth collector error")

            self.status.reconnects += 1
            await asyncio.sleep(backoff_s)
            backoff_s = min(max_backoff_s, backoff_s * 2)

    def _write_raw(self, stream: str, payload: dict[str, object]) -> None:
        event_time_ms = int(payload.get("E", payload.get("T", now_ms())))
        path = self.writer.append_record(
            "public",
            self.stream,
            {
                "received_at_ms": now_ms(),
                "stream": stream,
                "event_type": str(payload.get("e", "")),
                "payload": payload,
            },
            event_time_ms=event_time_ms,
        )
        self.status.last_written_path = str(path)

    def _write_local_snapshot(self, *, event_time_ms: int) -> None:
        view = self.book.snapshot(levels=self.depth_levels)
        payload = view.to_payload(symbol=self.config.symbol, event_type=self.snapshot_event_type)
        if self.store is not None:
            patch_method = getattr(self.store, self.store_patch_method, None)
            if callable(patch_method):
                patch_method(payload)
        path = self.writer.append_record(
            "public",
            f"{self.config.symbol.lower()}_{self.snapshot_filename_prefix}{self.depth_levels}",
            {
                "received_at_ms": now_ms(),
                "stream": self.stream,
                "event_type": self.snapshot_event_type,
                "payload": payload,
            },
            event_time_ms=event_time_ms,
        )
        self.status.local_snapshots_written += 1
        self.status.last_written_path = str(path)

    async def _fetch_snapshot(self) -> dict[str, object]:
        fetcher = getattr(self.client, self.snapshot_fetch_method)
        result = await asyncio.to_thread(fetcher, self.config.symbol, self.snapshot_limit)
        return result.data


class RPIDepthBookCollector(DepthBookCollector):
    def __init__(
        self,
        config: BotConfig,
        *,
        client: BinanceRESTClient,
        writer: JSONLWriter,
        store: StateStore | None = None,
        depth_levels: int = 20,
        snapshot_limit: int = 1000,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(
            config,
            client=client,
            writer=writer,
            store=store,
            depth_levels=depth_levels,
            snapshot_limit=snapshot_limit,
            logger=logger,
            stream=f"{config.symbol.lower()}@rpiDepth@500ms",
            snapshot_fetch_method="rpi_depth",
            snapshot_event_type="localRpiDepthSnapshot",
            snapshot_filename_prefix="localRpiDepth",
            store_patch_method="patch_rpi_depth_snapshot",
            logger_name="btcusdt_bot.rpi_depth_book_collector",
        )

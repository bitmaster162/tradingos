from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import websockets
from websockets.exceptions import ConnectionClosed

from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.connectors.ws_urls import build_combined_stream_url
from btcusdt_bot.state.store import StateStore
from btcusdt_bot.storage.jsonl import JSONLWriter
from btcusdt_bot.ws.messages import WSMessage, decode_ws_message


@dataclass(slots=True)
class BookTickerCollectorStatus:
    url: str
    stream: str
    messages_received: int = 0
    reconnects: int = 0
    last_event_time_ms: int = 0
    last_error: str = ""
    last_written_path: str = ""


class BookTickerCollector:
    def __init__(
        self,
        config: BotConfig,
        *,
        writer: JSONLWriter,
        store: StateStore | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.writer = writer
        self.store = store
        self.logger = logger or logging.getLogger("btcusdt_bot.book_ticker_collector")
        self.stream = f"{config.symbol.lower()}@bookTicker"
        self.url = build_combined_stream_url(self.config.ws_public_base_url, [self.stream])
        self.status = BookTickerCollectorStatus(url=self.url, stream=self.stream)

    def manifest(self) -> dict[str, object]:
        return {
            "url": self.url,
            "streams": [self.stream],
            "routing": "public",
            "notes": [
                "Use individual symbol bookTicker on /public for real-time top-of-book.",
                "All-bookTicker is slower and less suitable for single-symbol execution gating.",
            ],
        }

    def handle_message(self, ws_message: WSMessage) -> None:
        payload = ws_message.payload
        event_time_ms = int(payload.get("E", payload.get("T", now_ms())))
        record = {
            "received_at_ms": now_ms(),
            "stream": ws_message.stream or self.stream,
            "event_type": str(payload.get("e", "")),
            "payload": payload,
        }
        if self.store is not None:
            self.store.patch_book_ticker(payload)
        path = self.writer.append_record(
            "public",
            self.stream,
            record,
            event_time_ms=event_time_ms,
        )
        self.status.messages_received += 1
        self.status.last_event_time_ms = event_time_ms
        self.status.last_written_path = str(path)

    async def run(self, *, stop_after_messages: int | None = None) -> BookTickerCollectorStatus:
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
                    self.logger.info("book ticker collector connected", extra={"url": self.url})
                    backoff_s = max(0.2, self.config.reconnect_initial_backoff_ms / 1000)
                    async for raw_message in websocket:
                        ws_message = decode_ws_message(raw_message)
                        self.handle_message(ws_message)
                        if stop_after_messages is not None and self.status.messages_received >= stop_after_messages:
                            await websocket.close()
                            return self.status
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as exc:
                self.status.last_error = f"connection_closed code={exc.code} reason={exc.reason}"
                self.logger.warning("book ticker connection closed: %s", self.status.last_error)
            except Exception as exc:  # noqa: BLE001
                self.status.last_error = str(exc)
                self.logger.exception("book ticker collector error")

            self.status.reconnects += 1
            await asyncio.sleep(backoff_s)
            backoff_s = min(max_backoff_s, backoff_s * 2)

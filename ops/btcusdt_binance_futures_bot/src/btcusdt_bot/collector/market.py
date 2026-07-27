from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.connectors.ws_urls import build_combined_stream_url
from btcusdt_bot.storage.jsonl import JSONLWriter
from btcusdt_bot.ws.messages import WSMessage, decode_ws_message


@dataclass(slots=True)
class MarketCollectorStatus:
    url: str
    streams: list[str] = field(default_factory=list)
    messages_received: int = 0
    reconnects: int = 0
    last_event_time_ms: int = 0
    last_stream: str = ""
    last_event_type: str = ""
    last_error: str = ""
    last_written_path: str = ""


class MarketCollector:
    def __init__(
        self,
        config: BotConfig,
        *,
        writer: JSONLWriter,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.writer = writer
        self.logger = logger or logging.getLogger("btcusdt_bot.market_collector")
        self.streams = self._build_streams()
        self.url = build_combined_stream_url(self.config.ws_market_base_url, self.streams)
        self.status = MarketCollectorStatus(url=self.url, streams=list(self.streams))

    def _build_streams(self) -> list[str]:
        symbol = self.config.symbol.lower()
        streams = [
            f"{symbol}@aggTrade",
            f"{symbol}@markPrice@1s",
        ]
        for interval in self.config.kline_intervals:
            streams.append(f"{symbol}@kline_{interval}")
        if self.config.enable_contract_info_stream:
            streams.append("!contractInfo")
        if self.config.enable_force_order_stream:
            streams.append("!forceOrder@arr")
        return streams

    def manifest(self) -> dict[str, object]:
        return {
            "url": self.url,
            "streams": self.streams,
            "routing": "market",
            "notes": [
                "Use combined stream mode for regular futures market data.",
                "Expected payloads: aggTrade, markPriceUpdate, kline, contractInfo.",
            ],
        }

    def handle_message(self, ws_message: WSMessage) -> None:
        payload = ws_message.payload
        event_time_ms = int(payload.get("E", now_ms()))
        stream_name = ws_message.stream or str(payload.get("e", "unknown"))
        event_type = str(payload.get("e", ""))
        record = {
            "received_at_ms": now_ms(),
            "stream": stream_name,
            "event_type": event_type,
            "payload": payload,
        }
        path = self.writer.append_record(
            "market",
            stream_name,
            record,
            event_time_ms=event_time_ms,
        )
        self.status.messages_received += 1
        self.status.last_event_time_ms = event_time_ms
        self.status.last_stream = stream_name
        self.status.last_event_type = event_type
        self.status.last_written_path = str(path)

    async def run(self, *, stop_after_messages: int | None = None) -> MarketCollectorStatus:
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
                    self.logger.info("market collector connected", extra={"url": self.url})
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
                self.logger.warning("market collector connection closed: %s", self.status.last_error)
            except Exception as exc:  # noqa: BLE001
                self.status.last_error = str(exc)
                self.logger.exception("market collector error")

            self.status.reconnects += 1
            await asyncio.sleep(backoff_s)
            backoff_s = min(max_backoff_s, backoff_s * 2)

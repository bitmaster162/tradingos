from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import websockets
from websockets.exceptions import ConnectionClosed

from btcusdt_bot.collector.futures_book_ticker_gate import (
    FuturesBookTickerAdmission,
    FuturesBookTickerGate,
    FuturesBookTickerGateReject,
)
from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.connectors.ws_urls import build_combined_stream_url
from btcusdt_bot.state.store import StateStore
from btcusdt_bot.storage.jsonl import JSONLWriter


@dataclass(slots=True)
class BookTickerCollectorStatus:
    url: str
    stream: str
    messages_received: int = 0
    messages_rejected: int = 0
    reconnects: int = 0
    last_event_time_ms: int = 0
    last_received_at_ms: int = 0
    last_quote_age_ms: int = 0
    last_raw_sha256: str = ""
    freshness_source: str = "E"
    last_rejection_reason: str = ""
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
        self.gate = FuturesBookTickerGate(
            expected_symbol=config.symbol,
            expected_stream=self.stream,
            max_age_ms=config.stale_data_limit_ms,
        )
        self.status = BookTickerCollectorStatus(url=self.url, stream=self.stream)

    def manifest(self) -> dict[str, object]:
        return {
            "url": self.url,
            "streams": [self.stream],
            "routing": "public",
            "quote_gate": {
                "kind": "binance_usdm_futures_book_ticker",
                "freshness_source": "E",
                "max_age_ms": self.gate.max_age_ms,
                "max_raw_frame_bytes": self.gate.max_raw_frame_bytes,
                "fallback_timestamp_allowed": False,
            },
            "notes": [
                "Use individual symbol bookTicker on /public for real-time top-of-book.",
                "All-bookTicker is slower and less suitable for single-symbol execution gating.",
                "Frames are admitted fail-closed before store or JSONL writes.",
                "Freshness is computed only from Binance event time E; T and local receipt time are not substitutes.",
            ],
        }

    def _commit_admission(self, admission: FuturesBookTickerAdmission) -> None:
        payload = admission.state_payload()
        record = {
            "received_at_ms": admission.received_at_ms,
            "stream": admission.stream or self.stream,
            "event_type": "bookTicker",
            "freshness_source": admission.freshness_source,
            "quote_age_ms": admission.age_ms,
            "raw_sha256": admission.raw_sha256,
            "raw_size_bytes": admission.raw_size_bytes,
            "payload": payload,
        }
        if self.store is not None:
            self.store.patch_book_ticker(payload)
        path = self.writer.append_record(
            "public",
            self.stream,
            record,
            event_time_ms=admission.event_time_ms,
        )
        self.status.messages_received += 1
        self.status.last_event_time_ms = admission.event_time_ms
        self.status.last_received_at_ms = admission.received_at_ms
        self.status.last_quote_age_ms = admission.age_ms
        self.status.last_raw_sha256 = admission.raw_sha256
        self.status.last_rejection_reason = ""
        self.status.last_written_path = str(path)

    def handle_raw_message(self, raw_message: str | bytes, *, received_at_ms: int | None = None) -> bool:
        try:
            admission = self.gate.admit(raw_message, received_at_ms=received_at_ms)
        except FuturesBookTickerGateReject as exc:
            self.status.messages_rejected += 1
            self.status.last_rejection_reason = str(exc)
            self.logger.warning("book ticker frame rejected: %s", exc)
            return False
        self._commit_admission(admission)
        return True

    async def run(self, *, stop_after_messages: int | None = None) -> BookTickerCollectorStatus:
        backoff_s = max(0.2, self.config.reconnect_initial_backoff_ms / 1000)
        max_backoff_s = max(backoff_s, self.config.reconnect_max_backoff_ms / 1000)

        while True:
            try:
                async with websockets.connect(
                    self.url,
                    ping_interval=None,
                    max_size=self.gate.max_raw_frame_bytes,
                    open_timeout=self.config.timeout_s,
                ) as websocket:
                    self.logger.info("book ticker collector connected", extra={"url": self.url})
                    backoff_s = max(0.2, self.config.reconnect_initial_backoff_ms / 1000)
                    async for raw_message in websocket:
                        received_at_ms = now_ms()
                        admitted = self.handle_raw_message(raw_message, received_at_ms=received_at_ms)
                        if admitted and stop_after_messages is not None and self.status.messages_received >= stop_after_messages:
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

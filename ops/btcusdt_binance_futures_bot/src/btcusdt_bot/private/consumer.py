from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import websockets
from websockets.exceptions import ConnectionClosed

from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.rest_client import BinanceRESTClient
from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.connectors.ws_urls import build_private_url
from btcusdt_bot.state.store import StateStore
from btcusdt_bot.storage.jsonl import JSONLWriter
from btcusdt_bot.ws.messages import WSMessage, decode_ws_message


@dataclass(slots=True)
class PrivateConsumerStatus:
    messages_received: int = 0
    reconnects: int = 0
    listen_key_rotations: int = 0
    last_event_time_ms: int = 0
    last_event_type: str = ""
    last_error: str = ""
    last_state_path: str = ""
    listen_key_tail: str = ""


class PrivateEventDispatcher:
    def __init__(self, store: StateStore):
        self.store = store

    def dispatch(self, payload: dict[str, object]) -> str:
        event_type = str(payload.get("e", ""))
        if event_type == "ORDER_TRADE_UPDATE":
            self.store.apply_order_trade_update(payload)
        elif event_type == "ACCOUNT_UPDATE":
            self.store.apply_account_update(payload)
        elif event_type == "ALGO_UPDATE":
            self.store.apply_algo_update(payload)
        elif event_type == "ACCOUNT_CONFIG_UPDATE":
            self.store.apply_account_config_update(payload)
        elif event_type == "listenKeyExpired":
            self.store.mark_listen_key_expired(payload)
        return event_type


class PrivateStreamConsumer:
    def __init__(
        self,
        config: BotConfig,
        *,
        client: BinanceRESTClient,
        store: StateStore,
        writer: JSONLWriter,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.store = store
        self.writer = writer
        self.logger = logger or logging.getLogger("btcusdt_bot.private_consumer")
        self.dispatcher = PrivateEventDispatcher(store)
        self.status = PrivateConsumerStatus()

    def _mask_listen_key(self, listen_key: str) -> str:
        return listen_key[-8:] if len(listen_key) >= 8 else listen_key

    async def _start_listen_key(self) -> str:
        response = await asyncio.to_thread(self.client.start_user_stream)
        listen_key = str(response.data["listenKey"])
        self.status.listen_key_tail = self._mask_listen_key(listen_key)
        return listen_key

    async def _keepalive_loop(self, listen_key: str, stop_event: asyncio.Event) -> None:
        period_s = max(60.0, self.config.user_stream_keepalive_ms / 1000)
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=period_s)
                return
            except TimeoutError:
                pass

            try:
                await asyncio.to_thread(self.client.keepalive_user_stream, listen_key)
            except Exception as exc:  # noqa: BLE001
                self.status.last_error = f"listen_key_keepalive_failed: {exc}"
                self.logger.warning("private stream keepalive failed: %s", exc)

    async def _countdown_heartbeat_loop(self, stop_event: asyncio.Event) -> None:
        period_s = max(5.0, self.config.heartbeat_interval_ms / 1000)
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=period_s)
                return
            except TimeoutError:
                pass

            try:
                await asyncio.to_thread(
                    self.client.countdown_cancel_all,
                    self.config.symbol,
                    self.config.countdown_cancel_ms,
                )
            except Exception as exc:  # noqa: BLE001
                self.status.last_error = f"countdown_cancel_heartbeat_failed: {exc}"
                self.logger.warning("countdown heartbeat failed: %s", exc)

    def _persist_private_event(self, ws_message: WSMessage) -> None:
        payload = ws_message.payload
        event_type = str(payload.get("e", "unknown"))
        event_time_ms = int(payload.get("E", now_ms()))
        record = {
            "received_at_ms": now_ms(),
            "stream": ws_message.stream,
            "event_type": event_type,
            "payload": payload,
        }
        self.writer.append_record(
            "private",
            event_type,
            record,
            event_time_ms=event_time_ms,
        )

    def _flush_state_snapshot(self) -> None:
        path = self.writer.write_json("private/state/latest.json", self.store.snapshot())
        self.status.last_state_path = str(path)

    async def run(self, *, stop_after_messages: int | None = None) -> PrivateConsumerStatus:
        if not self.config.has_api_credentials:
            raise ValueError("API credentials are required for private stream consumption.")

        backoff_s = max(0.2, self.config.reconnect_initial_backoff_ms / 1000)
        max_backoff_s = max(backoff_s, self.config.reconnect_max_backoff_ms / 1000)

        while True:
            listen_key = await self._start_listen_key()
            url = build_private_url(
                self.config.ws_private_base_url,
                listen_key,
                list(self.config.private_events),
            )
            stop_event = asyncio.Event()
            tasks = [asyncio.create_task(self._keepalive_loop(listen_key, stop_event))]
            if self.config.enable_countdown_heartbeat:
                tasks.append(asyncio.create_task(self._countdown_heartbeat_loop(stop_event)))

            should_rotate = False
            try:
                async with websockets.connect(
                    url,
                    ping_interval=None,
                    max_size=None,
                    open_timeout=self.config.timeout_s,
                ) as websocket:
                    self.logger.info("private stream connected", extra={"url": url})
                    backoff_s = max(0.2, self.config.reconnect_initial_backoff_ms / 1000)
                    async for raw_message in websocket:
                        ws_message = decode_ws_message(raw_message)
                        self._persist_private_event(ws_message)
                        event_type = self.dispatcher.dispatch(ws_message.payload)

                        self.status.messages_received += 1
                        self.status.last_event_type = event_type
                        self.status.last_event_time_ms = int(ws_message.payload.get("E", now_ms()))

                        if self.status.messages_received % self.config.state_flush_every_events == 0:
                            self._flush_state_snapshot()

                        if event_type == "listenKeyExpired":
                            should_rotate = True
                            await websocket.close()
                            break
                        if stop_after_messages is not None and self.status.messages_received >= stop_after_messages:
                            self._flush_state_snapshot()
                            await websocket.close()
                            return self.status
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as exc:
                self.status.last_error = f"connection_closed code={exc.code} reason={exc.reason}"
                self.logger.warning("private stream connection closed: %s", self.status.last_error)
            except Exception as exc:  # noqa: BLE001
                self.status.last_error = str(exc)
                self.logger.exception("private stream error")
            finally:
                stop_event.set()
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

            self.status.reconnects += 1
            if should_rotate:
                self.status.listen_key_rotations += 1
            await asyncio.sleep(backoff_s)
            backoff_s = min(max_backoff_s, backoff_s * 2)

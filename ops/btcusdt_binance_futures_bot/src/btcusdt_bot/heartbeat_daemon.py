from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from btcusdt_bot.config import BotConfig
from btcusdt_bot.execution.gateway import ExecutionGateway
from btcusdt_bot.storage.jsonl import JSONLWriter


@dataclass(slots=True)
class CountdownHeartbeatStatus:
    iterations: int = 0
    sent: int = 0
    execution_unknown: int = 0
    errors: int = 0
    last_response_path: str = ""
    last_error: str = ""


class CountdownHeartbeatDaemon:
    def __init__(
        self,
        config: BotConfig,
        *,
        gateway: ExecutionGateway,
        writer: JSONLWriter,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.gateway = gateway
        self.writer = writer
        self.logger = logger or logging.getLogger("btcusdt_bot.heartbeat")
        self.status = CountdownHeartbeatStatus()

    async def run(
        self,
        *,
        interval_seconds: float,
        max_iterations: int | None = None,
        send: bool = False,
    ) -> CountdownHeartbeatStatus:
        while True:
            result = await asyncio.to_thread(self.gateway.refresh_countdown, dry_run=not send)
            self.status.iterations += 1
            if result.sent:
                self.status.sent += 1
            if result.execution_unknown:
                self.status.execution_unknown += 1
            if result.error is not None and not result.execution_unknown:
                self.status.errors += 1
                self.status.last_error = result.error.get("message", "")
                self.logger.warning("countdown heartbeat error: %s", self.status.last_error)

            path = self.writer.append_record(
                "heartbeat",
                f"{self.config.symbol.lower()}_countdown",
                {
                    "send": send,
                    "countdown_time_ms": self.config.countdown_cancel_ms,
                    "result": result,
                },
            )
            self.status.last_response_path = str(path)
            self.writer.write_json("heartbeat/latest.json", self.status)

            if max_iterations is not None and self.status.iterations >= max_iterations:
                return self.status
            await asyncio.sleep(max(0.1, interval_seconds))

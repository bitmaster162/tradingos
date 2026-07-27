from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.rest_client import BinanceRESTClient
from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.state.store import StateStore
from btcusdt_bot.storage.jsonl import JSONLWriter


@dataclass(slots=True)
class CrowdingCollectorStatus:
    iterations: int = 0
    snapshots_written: int = 0
    last_snapshot_path: str = ""
    last_snapshot_time_ms: int = 0
    last_error: str = ""


class CrowdingCollector:
    def __init__(
        self,
        config: BotConfig,
        *,
        client: BinanceRESTClient,
        writer: JSONLWriter,
        store: StateStore | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.writer = writer
        self.store = store
        self.logger = logger or logging.getLogger("btcusdt_bot.crowding_collector")
        self.status = CrowdingCollectorStatus()

    def fetch_snapshot(self, *, period: str = "5m") -> dict[str, Any]:
        snapshot_time_ms = now_ms()
        open_interest = self.client.open_interest(self.config.symbol).data
        open_interest_hist = self.client.open_interest_hist(self.config.symbol, period=period, limit=1).data
        global_ratio = self.client.global_long_short_account_ratio(self.config.symbol, period=period, limit=1).data
        top_account_ratio = self.client.top_long_short_account_ratio(self.config.symbol, period=period, limit=1).data
        top_position_ratio = self.client.top_long_short_position_ratio(self.config.symbol, period=period, limit=1).data
        taker_ratio = self.client.taker_buy_sell_ratio(self.config.symbol, period=period, limit=1).data

        snapshot = {
            "symbol": self.config.symbol,
            "period": period,
            "snapshot_time_ms": snapshot_time_ms,
            "open_interest": open_interest,
            "open_interest_hist": _latest_row(open_interest_hist),
            "global_long_short_account_ratio": _latest_row(global_ratio),
            "top_long_short_account_ratio": _latest_row(top_account_ratio),
            "top_long_short_position_ratio": _latest_row(top_position_ratio),
            "taker_buy_sell_ratio": _latest_row(taker_ratio),
        }
        return snapshot

    async def run(
        self,
        *,
        period: str = "5m",
        interval_seconds: float = 30.0,
        max_iterations: int | None = None,
    ) -> CrowdingCollectorStatus:
        while True:
            try:
                snapshot = await asyncio.to_thread(self.fetch_snapshot, period=period)
                event_time_ms = int(snapshot.get("snapshot_time_ms", now_ms()))
                if self.store is not None:
                    self.store.patch_crowding_snapshot(snapshot)
                path = self.writer.append_record(
                    "crowding",
                    f"{self.config.symbol.lower()}_{period}",
                    snapshot,
                    event_time_ms=event_time_ms,
                )
                self.writer.write_json("crowding/latest.json", snapshot)
                self.status.iterations += 1
                self.status.snapshots_written += 1
                self.status.last_snapshot_path = str(path)
                self.status.last_snapshot_time_ms = event_time_ms
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.status.last_error = str(exc)
                self.logger.exception("crowding collector iteration failed")
                self.status.iterations += 1

            if max_iterations is not None and self.status.iterations >= max_iterations:
                return self.status
            await asyncio.sleep(max(0.1, interval_seconds))


def _latest_row(payload: Any) -> Any:
    if isinstance(payload, list):
        return payload[-1] if payload else {}
    return payload

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from btcusdt_bot.authoritative.archive import (
    INCOME_HISTORY_DATASET,
    USER_TRADES_DATASET,
    AuthoritativeArchive,
)
from btcusdt_bot.authoritative.fetchers import AuthoritativeHistoryFetcher
from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.storage.jsonl import JSONLWriter


@dataclass(slots=True)
class AuthoritativeHistoryBackfillConfig:
    archive_root: Path
    start_ms: int
    end_ms: int
    user_trade_limit: int = 1000
    income_limit: int = 1000
    income_window_ms: int = 7 * 24 * 60 * 60 * 1000
    include_income_history: bool = True


@dataclass(slots=True)
class AuthoritativeHistoryBackfillResult:
    symbol: str
    started_at_ms: int
    completed_at_ms: int
    requested_start_ms: int
    requested_end_ms: int
    archive_root: Path
    manifest_path: Path
    user_trade_row_count: int
    income_row_count: int
    user_trade_bucket_counts: dict[str, int]
    income_bucket_counts: dict[str, int]
    user_trade_requests: int
    income_requests: int
    user_trade_windows: int
    income_windows: int
    income_history_requested: bool


class AuthoritativeHistoryBackfiller:
    def __init__(
        self,
        config: BotConfig,
        *,
        client: object,
        writer: JSONLWriter,
        backfill_config: AuthoritativeHistoryBackfillConfig,
    ) -> None:
        self.config = config
        self.client = client
        self.writer = writer
        self.backfill_config = backfill_config
        self.archive = AuthoritativeArchive(backfill_config.archive_root, symbol=config.symbol)
        self.fetcher = AuthoritativeHistoryFetcher(
            client,
            symbol=config.symbol,
            user_trade_limit=backfill_config.user_trade_limit,
            income_limit=backfill_config.income_limit,
            income_window_ms=backfill_config.income_window_ms,
        )

    def run_once(self) -> AuthoritativeHistoryBackfillResult:
        started_at_ms = now_ms()
        user_trades = self.fetcher.fetch_user_trades_partitioned(
            self.backfill_config.start_ms,
            self.backfill_config.end_ms,
        )
        income_rows = (
            self.fetcher.fetch_income_history_partitioned(
                self.backfill_config.start_ms,
                self.backfill_config.end_ms,
            )
            if self.backfill_config.include_income_history
            else []
        )
        updated_at_ms = now_ms()
        user_trade_bucket_counts = self.archive.upsert_rows(
            USER_TRADES_DATASET,
            user_trades,
            coverage_intervals=[(self.backfill_config.start_ms, self.backfill_config.end_ms)],
            updated_at_ms=updated_at_ms,
        )
        income_bucket_counts = (
            self.archive.upsert_rows(
                INCOME_HISTORY_DATASET,
                income_rows,
                coverage_intervals=[(self.backfill_config.start_ms, self.backfill_config.end_ms)],
                updated_at_ms=updated_at_ms,
            )
            if self.backfill_config.include_income_history
            else {}
        )
        result = AuthoritativeHistoryBackfillResult(
            symbol=self.config.symbol,
            started_at_ms=started_at_ms,
            completed_at_ms=updated_at_ms,
            requested_start_ms=self.backfill_config.start_ms,
            requested_end_ms=self.backfill_config.end_ms,
            archive_root=self.backfill_config.archive_root,
            manifest_path=self.archive.manifest_path(),
            user_trade_row_count=len(user_trades),
            income_row_count=len(income_rows),
            user_trade_bucket_counts=user_trade_bucket_counts,
            income_bucket_counts=income_bucket_counts,
            user_trade_requests=self.fetcher.stats.user_trade_requests,
            income_requests=self.fetcher.stats.income_requests,
            user_trade_windows=self.fetcher.stats.user_trade_windows,
            income_windows=self.fetcher.stats.income_windows,
            income_history_requested=self.backfill_config.include_income_history,
        )
        self.writer.write_json("authoritative/latest_backfill_status.json", result)
        self.writer.append_record(
            "reports",
            f"{self.config.symbol.lower()}_authoritative_backfill",
            {"result": result},
            event_time_ms=updated_at_ms,
        )
        return result

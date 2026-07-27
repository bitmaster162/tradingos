from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from btcusdt_bot.authoritative.archive import (
    INCOME_HISTORY_DATASET,
    USER_TRADES_DATASET,
    ArchiveLoadResult,
    AuthoritativeArchive,
)
from btcusdt_bot.authoritative.fetchers import AuthoritativeHistoryFetcher
from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.signing import now_ms
from btcusdt_bot.monitoring.trade_reconciliation import (
    TradeReconciliationDecision,
    TradeReconciliationStatus,
    TradeReconciliationThresholds,
    evaluate_trade_reconciliation,
    load_json,
)
from btcusdt_bot.storage.jsonl import JSONLWriter

_MAX_USER_TRADES_LIMIT = 1000
_MAX_INCOME_LIMIT = 1000
_DEFAULT_INCOME_WINDOW_MS = 7 * 24 * 60 * 60 * 1000
_ZERO = Decimal("0")


@dataclass(slots=True)
class TradeReconciliationDaemonConfig:
    runtime_state_path: Path
    lookback_ms: int
    thresholds: TradeReconciliationThresholds
    session_state_path: Path | None = None
    prefer_session_window: bool = True
    user_trade_limit: int = _MAX_USER_TRADES_LIMIT
    income_limit: int = _MAX_INCOME_LIMIT
    income_window_ms: int = _DEFAULT_INCOME_WINDOW_MS
    authoritative_archive_root: Path | None = None
    prefer_authoritative_archive: bool = True
    hydrate_archive_gaps: bool = True


class TradeReconciliationDaemon:
    def __init__(
        self,
        config: BotConfig,
        *,
        client: object,
        writer: JSONLWriter,
        daemon_config: TradeReconciliationDaemonConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.writer = writer
        self.daemon_config = daemon_config
        self.logger = logger or logging.getLogger("btcusdt_bot.trade_reconciliation")
        self.status = TradeReconciliationStatus()
        self.archive = (
            AuthoritativeArchive(daemon_config.authoritative_archive_root, symbol=config.symbol)
            if daemon_config.authoritative_archive_root is not None
            else None
        )

    def _new_fetcher(self) -> AuthoritativeHistoryFetcher:
        return AuthoritativeHistoryFetcher(
            self.client,
            symbol=self.config.symbol,
            user_trade_limit=self.daemon_config.user_trade_limit,
            income_limit=self.daemon_config.income_limit,
            income_window_ms=self.daemon_config.income_window_ms,
        )

    def _load_session_started_at_ms(self) -> int:
        path = self.daemon_config.session_state_path
        if path is None or not path.exists():
            return 0
        try:
            payload = load_json(path)
        except Exception:  # noqa: BLE001
            return 0
        return int(payload.get("session_started_at_ms", 0) or 0)

    def _resolve_window(self, compared_at_ms: int) -> tuple[int, int, str, int]:
        start_time_ms = max(0, compared_at_ms - self.daemon_config.lookback_ms)
        session_started_at_ms = 0
        window_mode = "lookback"
        if self.daemon_config.prefer_session_window:
            session_started_at_ms = self._load_session_started_at_ms()
            if 0 < session_started_at_ms < compared_at_ms:
                start_time_ms = session_started_at_ms
                window_mode = "session"
        return start_time_ms, compared_at_ms, window_mode, session_started_at_ms

    def _fetch_user_trades_partitioned(self, start_time_ms: int, end_time_ms: int) -> list[dict[str, object]]:
        fetcher = self._new_fetcher()
        return fetcher.fetch_user_trades_partitioned(start_time_ms, end_time_ms)

    def _fetch_income_history_paginated(self, start_time_ms: int, end_time_ms: int) -> list[dict[str, object]]:
        fetcher = self._new_fetcher()
        return fetcher.fetch_income_history_partitioned(start_time_ms, end_time_ms)

    def _load_rows_with_archive(
        self,
        *,
        dataset: str,
        start_time_ms: int,
        end_time_ms: int,
        fetcher: AuthoritativeHistoryFetcher,
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
        if self.archive is None or not self.daemon_config.prefer_authoritative_archive:
            rows = self._fetch_live_rows(dataset=dataset, start_time_ms=start_time_ms, end_time_ms=end_time_ms, fetcher=fetcher)
            return rows, {
                "dataset": dataset,
                "source_mode": "live_only",
                "archive_coverage_ratio": _ZERO,
                "archive_gap_count": 1 if rows else 0,
                "archive_row_count": 0,
                "live_row_count": len(rows),
                "requested_ms": max(0, end_time_ms - start_time_ms + 1),
            }

        archive_result = self.archive.load_rows_for_range(dataset, start_ms=start_time_ms, end_ms=end_time_ms)
        rows = list(archive_result.rows)
        live_row_count = 0
        for gap_start_ms, gap_end_ms in archive_result.gaps:
            live_gap_rows = self._fetch_live_rows(
                dataset=dataset,
                start_time_ms=gap_start_ms,
                end_time_ms=gap_end_ms,
                fetcher=fetcher,
            )
            rows.extend(live_gap_rows)
            live_row_count += len(live_gap_rows)
            if self.daemon_config.hydrate_archive_gaps and live_gap_rows:
                self.archive.upsert_rows(
                    dataset,
                    live_gap_rows,
                    coverage_intervals=[(gap_start_ms, gap_end_ms)],
                    updated_at_ms=now_ms(),
                )

        rows = self._dedupe_rows(rows, dataset=dataset)
        if live_row_count > 0 and archive_result.rows:
            source_mode = "archive_blended"
        elif live_row_count > 0:
            source_mode = "live_gap_fill"
        else:
            source_mode = archive_result.source_mode
        return rows, {
            "dataset": dataset,
            "source_mode": source_mode,
            "archive_coverage_ratio": archive_result.coverage_ratio,
            "archive_gap_count": len(archive_result.gaps),
            "archive_row_count": len(archive_result.rows),
            "live_row_count": live_row_count,
            "requested_ms": archive_result.requested_ms,
        }

    def _fetch_live_rows(
        self,
        *,
        dataset: str,
        start_time_ms: int,
        end_time_ms: int,
        fetcher: AuthoritativeHistoryFetcher,
    ) -> list[dict[str, object]]:
        if dataset == USER_TRADES_DATASET:
            return fetcher.fetch_user_trades_partitioned(start_time_ms, end_time_ms)
        if dataset == INCOME_HISTORY_DATASET:
            return fetcher.fetch_income_history_partitioned(start_time_ms, end_time_ms)
        raise ValueError(f"unsupported_dataset:{dataset}")

    @staticmethod
    def _dedupe_rows(rows: list[dict[str, object]], *, dataset: str) -> list[dict[str, object]]:
        if dataset == USER_TRADES_DATASET:
            key_fn = AuthoritativeHistoryFetcher.user_trade_key
            sort_key = lambda row: (
                int(row.get("time", 0) or 0),
                int(row.get("id", row.get("tradeId", 0)) or 0),
            )
        else:
            key_fn = AuthoritativeHistoryFetcher.income_key
            sort_key = lambda row: (
                int(row.get("time", 0) or 0),
                str(row.get("tranId", "")),
            )
        merged: dict[tuple[object, ...], dict[str, object]] = {}
        for row in rows:
            if isinstance(row, dict):
                merged[key_fn(row)] = row
        return sorted(merged.values(), key=sort_key)

    def _evaluate(
        self,
    ) -> tuple[
        TradeReconciliationDecision,
        dict[str, object],
        list[dict[str, object]],
        list[dict[str, object]],
        dict[str, object],
    ]:
        runtime_state = load_json(self.daemon_config.runtime_state_path)
        compared_at_ms = now_ms()
        start_time_ms, end_time_ms, window_mode, session_started_at_ms = self._resolve_window(compared_at_ms)
        fetcher = self._new_fetcher()
        user_trades, user_source = self._load_rows_with_archive(
            dataset=USER_TRADES_DATASET,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            fetcher=fetcher,
        )
        income_rows, income_source = self._load_rows_with_archive(
            dataset=INCOME_HISTORY_DATASET,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            fetcher=fetcher,
        )
        decision = evaluate_trade_reconciliation(
            runtime_state=runtime_state,
            symbol=self.config.symbol,
            exchange_user_trades=user_trades,
            exchange_income_rows=income_rows,
            lookback_start_ms=start_time_ms,
            lookback_end_ms=end_time_ms,
            thresholds=self.daemon_config.thresholds,
            compared_at_ms=compared_at_ms,
            window_mode=window_mode,
            session_started_at_ms=session_started_at_ms,
        )
        source_meta = {
            "source_mode": self._combine_source_modes(
                str(user_source.get("source_mode", "")),
                str(income_source.get("source_mode", "")),
            ),
            "user_trades": user_source,
            "income_history": income_source,
            "user_trade_requests": fetcher.stats.user_trade_requests,
            "income_requests": fetcher.stats.income_requests,
            "user_trade_windows": fetcher.stats.user_trade_windows,
            "income_windows": fetcher.stats.income_windows,
        }
        return decision, runtime_state, user_trades, income_rows, source_meta

    @staticmethod
    def _combine_source_modes(user_mode: str, income_mode: str) -> str:
        modes = {user_mode, income_mode}
        if modes == {"archive_complete"}:
            return "archive_only"
        if "archive_blended" in modes or ("archive_complete" in modes and "live_gap_fill" in modes):
            return "archive_blended"
        if "live_gap_fill" in modes and ("archive_partial" in modes or "archive_missing" in modes or "archive_complete" in modes):
            return "archive_blended"
        if "live_gap_fill" in modes:
            return "live_gap_fill"
        if "archive_missing" in modes and len(modes) == 1:
            return "archive_missing"
        if "archive_partial" in modes:
            return "archive_partial"
        if "archive_complete" in modes:
            return "archive_only"
        return "live_only"

    async def run(
        self,
        *,
        interval_seconds: float,
        max_iterations: int | None = None,
    ) -> TradeReconciliationStatus:
        while True:
            self.status.iterations += 1
            try:
                decision, runtime_state, user_trades, income_rows, source_meta = await asyncio.to_thread(self._evaluate)
                latest_path = self.writer.write_json("live/guards/latest_trade_reconciliation.json", decision)
                report_path = self.writer.append_record(
                    "reports",
                    f"{self.config.symbol.lower()}_trade_reconciliation",
                    {
                        "decision": decision,
                        "runtime_state": runtime_state,
                        "user_trades": user_trades,
                        "income_history": income_rows,
                        "source": source_meta,
                    },
                    event_time_ms=decision.compared_at_ms,
                )
                self.status.decisions_written += 1
                self.status.last_action = decision.action
                self.status.last_path = str(report_path)
                self.status.last_window_mode = decision.window_mode
                self.status.last_source_mode = str(source_meta.get("source_mode", "") or "")
                self.status.session_started_at_ms = decision.session_started_at_ms
                self.status.exchange_trade_count = decision.exchange_trade_count
                self.status.local_trade_fill_count = decision.local_trade_fill_count
                self.status.missing_local_order_ratio = decision.missing_local_order_ratio
                self.status.realized_pnl_diff_usdt = decision.realized_pnl_diff_usdt
                self.status.commission_abs_diff_usdt = decision.commission_abs_diff_usdt
                self.status.quote_qty_abs_diff_usdt = decision.quote_qty_abs_diff_usdt
                self.status.income_trade_link_gap_ratio = decision.income_trade_link_gap_ratio
                user_source = source_meta.get("user_trades", {}) if isinstance(source_meta.get("user_trades"), dict) else {}
                income_source = source_meta.get("income_history", {}) if isinstance(source_meta.get("income_history"), dict) else {}
                self.status.user_trade_archive_coverage_ratio = Decimal(str(user_source.get("archive_coverage_ratio", "0") or "0"))
                self.status.income_archive_coverage_ratio = Decimal(str(income_source.get("archive_coverage_ratio", "0") or "0"))
                self.status.archive_gap_count = int(user_source.get("archive_gap_count", 0) or 0) + int(
                    income_source.get("archive_gap_count", 0) or 0
                )
                self.status.archived_user_trade_count = int(user_source.get("archive_row_count", 0) or 0)
                self.status.live_user_trade_count = int(user_source.get("live_row_count", 0) or 0)
                self.status.archived_income_count = int(income_source.get("archive_row_count", 0) or 0)
                self.status.live_income_count = int(income_source.get("live_row_count", 0) or 0)
                if decision.action == "reduce_size":
                    self.status.reduce_size_decisions += 1
                elif decision.action == "observe_only":
                    self.status.observe_only_decisions += 1
                self.writer.write_json("live/guards/latest_trade_reconciliation_status.json", self.status)
                self.logger.info(
                    "trade reconciliation decision=%s window_mode=%s source_mode=%s size_multiplier=%s latest=%s",
                    decision.action,
                    decision.window_mode,
                    self.status.last_source_mode,
                    decision.size_multiplier,
                    latest_path,
                )
            except Exception as exc:  # noqa: BLE001
                self.status.last_error = str(exc)
                self.writer.write_json("live/guards/latest_trade_reconciliation_status.json", self.status)
                self.logger.warning("trade reconciliation evaluation failed: %s", self.status.last_error)

            if max_iterations is not None and self.status.iterations >= max_iterations:
                return self.status
            await asyncio.sleep(max(0.1, interval_seconds))

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from btcusdt_bot.config import BotConfig
from btcusdt_bot.monitoring.session_truth_trend import (
    SessionTruthTrendDecision,
    SessionTruthTrendStatus,
    SessionTruthTrendThresholds,
    evaluate_session_truth_trend,
)
from btcusdt_bot.reporting.session_truth_report import SessionTruthReport
from btcusdt_bot.storage.jsonl import JSONLWriter


@dataclass(slots=True)
class SessionTruthTrendDaemonConfig:
    report_path: Path
    thresholds: SessionTruthTrendThresholds


class SessionTruthTrendDaemon:
    def __init__(
        self,
        config: BotConfig,
        *,
        writer: JSONLWriter,
        daemon_config: SessionTruthTrendDaemonConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.writer = writer
        self.daemon_config = daemon_config
        self.logger = logger or logging.getLogger("btcusdt_bot.session_truth_trend")
        self.status = SessionTruthTrendStatus()

    def _load_report(self) -> SessionTruthReport:
        payload = json.loads(self.daemon_config.report_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("report"), dict):
            payload = payload.get("report")
        if not isinstance(payload, dict):
            raise ValueError("session_truth_report_invalid")
        return SessionTruthReport.from_payload(payload)

    def _evaluate(self) -> tuple[SessionTruthTrendDecision, SessionTruthReport]:
        report = self._load_report()
        decision = evaluate_session_truth_trend(
            report=report,
            thresholds=self.daemon_config.thresholds,
            compared_at_ms=report.compared_at_ms,
        )
        return decision, report

    async def run(
        self,
        *,
        interval_seconds: float,
        max_iterations: int | None = None,
    ) -> SessionTruthTrendStatus:
        while True:
            self.status.iterations += 1
            try:
                decision, report = await asyncio.to_thread(self._evaluate)
                latest_path = self.writer.write_json("live/guards/latest_session_truth_trend.json", decision)
                latest_report_path = self.writer.write_json(
                    "live/reports/latest_session_truth_trend.json",
                    {"decision": decision, "report": report},
                )
                report_path = self.writer.append_record(
                    "reports",
                    f"{self.config.symbol.lower()}_session_truth_trend",
                    {"decision": decision, "report": report},
                    event_time_ms=decision.compared_at_ms,
                )
                self.status.decisions_written += 1
                self.status.last_action = decision.action
                self.status.last_path = str(report_path)
                self.status.active_bucket_count = decision.active_bucket_count
                self.status.negative_bucket_ratio = decision.negative_bucket_ratio
                self.status.recent_bucket_net_realized_bps = decision.recent_bucket_net_realized_bps
                self.status.cumulative_drawdown_usdt = decision.cumulative_drawdown_usdt
                if decision.action == "reduce_size":
                    self.status.reduce_size_decisions += 1
                elif decision.action == "observe_only":
                    self.status.observe_only_decisions += 1
                self.writer.write_json("live/guards/latest_session_truth_trend_status.json", self.status)
                self.logger.info(
                    "session truth trend decision=%s latest=%s live_report=%s",
                    decision.action,
                    latest_path,
                    latest_report_path,
                )
            except Exception as exc:  # noqa: BLE001
                self.status.last_error = str(exc)
                self.writer.write_json("live/guards/latest_session_truth_trend_status.json", self.status)
                self.logger.warning("session truth trend evaluation failed: %s", self.status.last_error)

            if max_iterations is not None and self.status.iterations >= max_iterations:
                return self.status
            await asyncio.sleep(max(0.1, interval_seconds))

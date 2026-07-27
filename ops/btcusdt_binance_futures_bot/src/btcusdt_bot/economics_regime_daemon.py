from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from btcusdt_bot.config import BotConfig
from btcusdt_bot.monitoring.economics_regime import (
    EconomicsRegimeDecision,
    EconomicsRegimeStatus,
    EconomicsRegimeThresholds,
    evaluate_economics_regime,
)
from btcusdt_bot.reporting.economics_dashboard import EconomicsDashboard, build_economics_dashboard
from btcusdt_bot.storage.jsonl import JSONLWriter


@dataclass(slots=True)
class EconomicsRegimeDaemonConfig:
    lookback_days: int
    thresholds: EconomicsRegimeThresholds
    end_date: str | None = None


class EconomicsRegimeDaemon:
    def __init__(
        self,
        config: BotConfig,
        *,
        writer: JSONLWriter,
        daemon_config: EconomicsRegimeDaemonConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.writer = writer
        self.daemon_config = daemon_config
        self.logger = logger or logging.getLogger("btcusdt_bot.economics_regime")
        self.status = EconomicsRegimeStatus()

    def _resolve_end_date(self) -> str:
        if self.daemon_config.end_date:
            return self.daemon_config.end_date
        return datetime.now(tz=UTC).strftime("%Y-%m-%d")

    def _evaluate(self) -> tuple[EconomicsRegimeDecision, EconomicsDashboard]:
        dashboard = build_economics_dashboard(
            data_dir=self.config.data_dir,
            symbol=self.config.symbol,
            end_date=self._resolve_end_date(),
            lookback_days=self.daemon_config.lookback_days,
        )
        compared_at_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        decision = evaluate_economics_regime(
            dashboard=dashboard,
            thresholds=self.daemon_config.thresholds,
            compared_at_ms=compared_at_ms,
        )
        return decision, dashboard

    async def run(
        self,
        *,
        interval_seconds: float,
        max_iterations: int | None = None,
    ) -> EconomicsRegimeStatus:
        while True:
            self.status.iterations += 1
            try:
                decision, dashboard = await asyncio.to_thread(self._evaluate)
                latest_path = self.writer.write_json("live/guards/latest_economics_regime.json", decision)
                latest_report_path = self.writer.write_json(
                    "live/reports/latest_economics_dashboard.json",
                    {"decision": decision, "dashboard": dashboard},
                )
                dashboard_path = self.writer.append_record(
                    "reports",
                    f"{self.config.symbol.lower()}_economics_dashboard",
                    {"dashboard": dashboard},
                    event_time_ms=decision.compared_at_ms,
                )
                regime_path = self.writer.append_record(
                    "reports",
                    f"{self.config.symbol.lower()}_economics_regime",
                    {"decision": decision, "dashboard": dashboard},
                    event_time_ms=decision.compared_at_ms,
                )
                self.status.decisions_written += 1
                self.status.last_action = decision.action
                self.status.last_path = str(regime_path)
                self.status.active_day_count = decision.active_day_count
                self.status.negative_day_ratio = decision.negative_day_ratio
                self.status.recent_day_net_realized_bps = decision.recent_day_net_realized_bps
                self.status.cumulative_drawdown_usdt = decision.cumulative_drawdown_usdt
                if decision.action == "reduce_size":
                    self.status.reduce_size_decisions += 1
                elif decision.action == "observe_only":
                    self.status.observe_only_decisions += 1
                self.writer.write_json("live/guards/latest_economics_regime_status.json", self.status)
                self.logger.info(
                    "economics regime decision=%s latest=%s report=%s dashboard=%s",
                    decision.action,
                    latest_path,
                    regime_path,
                    dashboard_path,
                )
            except Exception as exc:  # noqa: BLE001
                self.status.last_error = str(exc)
                self.writer.write_json("live/guards/latest_economics_regime_status.json", self.status)
                self.logger.warning("economics regime evaluation failed: %s", self.status.last_error)

            if max_iterations is not None and self.status.iterations >= max_iterations:
                return self.status
            await asyncio.sleep(max(0.1, interval_seconds))
